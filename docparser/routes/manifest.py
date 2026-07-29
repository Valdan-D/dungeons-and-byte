from flask import Blueprint, request, jsonify
import json
import logging
import os
import re
import fitz  # PyMuPDF

from manifest_engine import (
    classify_page, build_header_footer_index, page_font_thresholds,
    extract_line_groups, page_body_color,
)

manifest_bp = Blueprint('manifest', __name__)


# Alcuni font subset embedded in questi PDF (uno per occorrenza, tipico di
# export InDesign) hanno un glifo condiviso/ambiguo tra "1" e "l"/"I" (e "0"
# e "o"/"O") - probabile deduplica di glifi visivamente identici fatta dal
# tool di generazione. PyMuPDF decodifica quel glifo con QUALSIASI Unicode
# il ToUnicode CMap del font dichiari, che puo' essere quello sbagliato: non
# e' un bug di lettura, l'informazione e' geneticamente ambigua nel PDF.
# Qui si corregge solo il caso piu' frequente e piu' dannoso (cambia valori
# di gioco): la notazione dei dadi (es. "dl2" -> "d12", "lOdlO" -> "10d10"),
# validando che il risultato normalizzato sia un dado D&D valido - non tocca
# la corruzione a livello di parole normali (es. "m1ss10ne"), che richiede
# un dizionario/spellcheck e non e' risolvibile con una regex sicura.
_DICE_AMBIG_RE = re.compile(r'\b([0-9lIoO]{0,2})([dD])([0-9lIoO]{1,3})\b')
_VALID_DIE_SIZES = {2, 3, 4, 6, 8, 10, 12, 20, 100}


def _normalize_dice_token(tok):
    return tok.replace('l', '1').replace('I', '1').replace('O', '0').replace('o', '0')


_KNOWN_CORRUPTION_RE = re.compile(r'## _ A_Z_IO.*?(?=\n\nRaggio di luce)', re.DOTALL)


def fix_known_corruptions(text):
    """Correzioni puntuali una-tantum per corruzioni uniche gia' diagnosticate
    a mano, non riconducibili a un pattern generale (a differenza di
    fix_jj_ligature/fix_dice_notation/fix_area_code_notation). Qui: pag.86 di
    "Le Chiavi del Caveau Aureo", l'heading "AZIONI BONUS" nella scheda
    DIFENSORE MECCANICO viene decodificato con trattini bassi e '�' al posto
    di quasi ogni lettera (font-subset del tutto privo di ToUnicode per quei
    glifi, diverso dal caso l/1 - qui non c'e' un singolo carattere ambiguo
    da correggere, l'informazione manca del tutto). Si sostituisce l'intero
    frammento (identificato tra due ancore stabili: l'inizio riconoscibile
    "_ A_Z_IO" e il paragrafo "Raggio di luce" che segue immediatamente
    l'heading nel testo originale) con il testo corretto."""
    return _KNOWN_CORRUPTION_RE.sub("## AZIONI BONUS", text)


def fix_jj_ligature(text):
    """Un'altra ambiguita' di font isolata in questo libro: nel testo in
    grassetto (font diverso dal corpo, es. "AJJarme." invece di "Allarme.",
    "deJJe" invece di "delle") il doppio "ll" viene decodificato come "JJ".
    A differenza di l/1 o Z/1, qui non serve validazione incrociata: "JJ"
    (doppia J maiuscola) non e' mai una sequenza valida in una parola
    italiana, quindi la sostituzione con "ll" e' sicura senza eccezioni -
    verificato su tutte le occorrenze del libro (AJJarme/AJJarmi/deJJe)."""
    return text.replace("JJ", "ll")


def fix_dice_notation(text):
    def repl(m):
        count_raw, d_char, size_raw = m.group(1), m.group(2), m.group(3)
        if count_raw.isdigit() and size_raw.isdigit():
            return m.group(0)
        count_norm = _normalize_dice_token(count_raw)
        size_norm = _normalize_dice_token(size_raw)
        if not size_norm.isdigit():
            return m.group(0)
        if count_norm and not count_norm.isdigit():
            return m.group(0)
        size_val = int(size_norm)
        if size_val not in _VALID_DIE_SIZES:
            return m.group(0)
        if count_norm:
            count_val = int(count_norm)
            if not (1 <= count_val <= 20):
                return m.group(0)
        return f'{count_norm}{d_char}{size_norm}'
    return _DICE_AMBIG_RE.sub(repl, text)


# Gli stessi glifi ambigui (l/1, I/1, O/0, o/0) corrompono anche i codici di
# area/mappa usati da queste avventure per riferirsi a stanze (es. "P12",
# "M13" -> "Pl2", "Ml3"). A differenza dei dadi, qui non c'e' un insieme
# finito di valori validi da controllare - la validazione e' invece
# incrociata: un codice ambiguo si corregge SOLO se la sua forma normalizzata
# esiste GIA' altrove nel libro scritta senza ambiguita' (stesso codice
# citato più volte, non sempre corrotto nello stesso punto). Questo richiede
# di vedere tutte le pagine insieme, non una alla volta come fix_dice_notation
# - viene applicato in manifest_from_path dopo il rendering di tutte le
# pagine. Parole italiane corte che capitano nello stesso pattern (es. "Il",
# "Al", "Lo") restano intatte perche' la loro forma normalizzata ("I1", "A1",
# "L0") non coincide quasi mai con un vero codice area del libro.
_AREA_CODE_CLEAN_RE = re.compile(r'\b([A-Z])([0-9]{1,3})\b')
_AREA_CODE_AMBIG_RE = re.compile(r'\b([A-Z])([0-9lIOo]{1,3})\b')


def fix_area_code_notation(pages_markdown):
    """pages_markdown: lista di stringhe markdown, una per pagina (ordine
    qualsiasi). Ritorna una nuova lista con i codici area ambigui corretti,
    validati incrociando l'intero libro."""
    clean_codes = set()
    for md in pages_markdown:
        for m in _AREA_CODE_CLEAN_RE.finditer(md):
            clean_codes.add(m.group(1) + m.group(2))

    if not clean_codes:
        return pages_markdown

    def repl(m):
        letter, suffix = m.group(1), m.group(2)
        if suffix.isdigit():
            return m.group(0)
        norm = _normalize_dice_token(suffix)
        if not norm.isdigit():
            return m.group(0)
        candidate = letter + norm
        if candidate in clean_codes:
            return candidate
        return m.group(0)

    return [_AREA_CODE_AMBIG_RE.sub(repl, md) for md in pages_markdown]


def order_blocks_for_markdown(manifest):
    """I blocchi di testo sono gia' in ordine di lettura naturale (colonna
    sinistra poi destra) cosi' come li restituisce PyMuPDF, ma tabelle e
    immagini vengono aggiunte in coda dal classificatore e vanno reinserite
    nella posizione corretta rispetto al testo circostante. Si riordina
    tutto per (colonna, Y) - stesso criterio colonna-poi-riga del testo.

    Un titolo di pagina (heading_h2) e' spesso centrato orizzontalmente
    sull'intera pagina (non confinato in una colonna) - il suo centro puo'
    cadere per pochi punti oltre l'esatto punto medio W/2 usato per decidere
    la colonna (verificato: pag. 209 di "Le Chiavi del Caveau Aureo",
    titolo "INDICE" a 1.5pt oltre il centro), finendo bucketizzato nella
    colonna sbagliata - tutto il contenuto della colonna sinistra lo precede
    allora nel markdown finale, anche se e' l'elemento piu' in alto della
    pagina. Si tratta come titolo di pagina (ordinato per Y, prima di
    entrambe le colonne) solo quando e' vicino al centro ENTRO una
    tolleranza stretta (non un singolo punto esatto, stesso principio del
    fix di fusione a due colonne) E si trova in cima alla pagina (nessun
    altro blocco di testo/tabella sopra di lui) - un heading_h3 di
    sottosezione a meta' pagina, anche se vicino al centro per coincidenza,
    resta legato alla sua colonna."""
    W = manifest["width"]
    blocks = manifest["blocks"]
    CENTER_TOLERANCE = 15.0
    min_y = min((b["bbox"][1] for b in blocks if b["type"] not in ("image", "table")), default=0.0)

    def key(b):
        bbox = b["bbox"]
        cx = (bbox[0] + bbox[2]) / 2
        if (b["type"] == "heading_h2" and abs(cx - W / 2) <= CENTER_TOLERANCE
                and bbox[1] <= min_y + 5):
            return (-1, bbox[1])
        col = 0 if cx < W / 2 else 1
        return (col, bbox[1])

    return sorted(blocks, key=key)


def merge_dropcap_paragraphs(blocks):
    """Un capolettera (blocco 'paragraph' isolato di 1-2 lettere, gia'
    derubricato da heading in classify_line_font perche' troppo corto per
    essere un titolo vero) va ricongiunto come prefisso al paragrafo a cui
    appartiene. L'ordine (prima o dopo, nella lista ordinata per Y) non e'
    affidabile: un capolettera parte alla stessa altezza Y del testo che
    introduce, per definizione - un margine di 1pt decide quale dei due
    ordina prima, e puo' capitare da entrambi i lati (verificato: a volte
    precede il paragrafo, a volte lo segue). Si sceglie il paragrafo
    adiacente piu' vicino in Y (precedente o successivo) e gli si antepone
    la lettera (maiuscola, i capolettera decorano sempre l'inizio di una
    frase).

    Un piccolo ornamento decorativo isolato (es. un dingbat di fine sezione
    in font simbolico) soddisfa lo stesso criterio testuale ma NON va unito
    a nulla: si distingue da un vero capolettera per la dimensione, sempre
    molto maggiore del paragrafo che introduce (e' letteralmente cio' che
    rende un capolettera tale - un ornamento e' invece spesso piu' PICCOLO
    del corpo testo). Se nessun paragrafo adiacente e' significativamente
    piu' piccolo del blocco candidato, questo resta isolato com'era."""
    out = []
    n = len(blocks)
    for i, b in enumerate(blocks):
        text = (b.get("text") or "").strip()
        is_candidate = b["type"] == "paragraph" and len(text) <= 2 and text.isalpha()
        if not is_candidate:
            out.append(b)
            continue

        prev_par = out[-1] if out and out[-1]["type"] == "paragraph" else None
        next_par = blocks[i + 1] if i + 1 < n and blocks[i + 1]["type"] == "paragraph" else None
        dropcap_size = b.get("max_size", 0)

        def is_real_dropcap(neighbor):
            if neighbor is None:
                return False
            neighbor_size = neighbor.get("max_size", 0)
            return neighbor_size > 0 and dropcap_size >= neighbor_size * 1.8

        prev_ok = is_real_dropcap(prev_par)
        next_ok = is_real_dropcap(next_par)
        if not prev_ok and not next_ok:
            out.append(b)
            continue

        gap_prev = (b["bbox"][1] - prev_par["bbox"][3]) if prev_ok else float("inf")
        gap_next = (next_par["bbox"][1] - b["bbox"][3]) if next_ok else float("inf")
        letter = text.upper()
        if abs(gap_prev) <= abs(gap_next):
            prev_par["text"] = letter + prev_par["text"]
        else:
            next_par["text"] = letter + next_par["text"]
        # il blocco capolettera stesso non va aggiunto a out (assorbito nel paragrafo)
    return out


def _fetch_surya_zones(page, zoom=2.5, gateway_url="http://192.168.178.115:8100/layout", timeout=120):
    """Chiama il gateway Surya (container 114, layout detection via VLM) per
    ottenere le zone reali della pagina (Text/Table/Picture/SectionHeader/...).
    Ritorna le zone con bbox in coordinate PDF (punti), o None se il gateway
    non risponde (fallback silenzioso al comportamento standard - vedi
    memoria dnb_scan_engine_parse/n8n_api per il contesto del passaggio)."""
    import base64
    import json
    import urllib.request
    try:
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom))
        img_b64 = base64.b64encode(pix.tobytes("png")).decode()
        body = json.dumps({"image_b64": img_b64}).encode()
        req = urllib.request.Request(gateway_url, data=body, method="POST",
                                      headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.load(r)
        zones = []
        for z in data.get("zones", []):
            bbox = [v / zoom for v in z["bbox"]]
            zones.append({"label": z["label"], "bbox": bbox})
        return zones if zones else None
    except Exception as e:
        logging.warning(f"Gateway Surya non raggiungibile, fallback a ordinamento standard: {e}")
        return None


def _zone_row_bands(zones, min_gap=6.0, resolution=1.0):
    y_min = min(z["bbox"][1] for z in zones)
    y_max = max(z["bbox"][3] for z in zones)
    n_bins = int((y_max - y_min) / resolution) + 2
    covered = [False] * n_bins
    for z in zones:
        i0 = max(0, int((z["bbox"][1] - y_min) / resolution))
        i1 = min(n_bins - 1, int((z["bbox"][3] - y_min) / resolution))
        for i in range(i0, i1 + 1):
            covered[i] = True
    bands, start = [], None
    for i in range(n_bins):
        if covered[i] and start is None:
            start = i
        elif not covered[i] and start is not None:
            bands.append([y_min + start * resolution, y_min + i * resolution])
            start = None
    if start is not None:
        bands.append([y_min + start * resolution, y_max])
    merged = []
    for b in bands:
        if merged and b[0] - merged[-1][1] < min_gap:
            merged[-1][1] = b[1]
        else:
            merged.append(b)
    return [tuple(b) for b in merged]


def _zone_gutters(zones, x_min, x_max, min_gutter=8.0, resolution=1.0):
    n_bins = int((x_max - x_min) / resolution) + 2
    covered = [False] * n_bins
    for z in zones:
        i0 = max(0, int((z["bbox"][0] - x_min) / resolution))
        i1 = min(n_bins - 1, int((z["bbox"][2] - x_min) / resolution))
        for i in range(i0, i1 + 1):
            covered[i] = True
    gutters, i = [], 0
    while i < n_bins:
        if not covered[i]:
            j = i
            while j < n_bins and not covered[j]:
                j += 1
            if (j - i) * resolution >= min_gutter:
                gutters.append(x_min + i * resolution)
            i = j
        else:
            i += 1
    return gutters


def order_zones_recursive(zones):
    """Taglio ricorsivo (bande orizzontali poi colonne) applicato alle zone
    reali rilevate da Surya - sicuro perche' le zone sono i confini VERI
    della pagina (rilevati dal modello), non blocchi di testo nostri con
    buchi artificiali lasciati da tabelle escluse (il problema che rendeva
    instabile lo stesso approccio applicato ai blocchi interni del motore,
    vedi sessione 2026-07-28)."""
    bands = _zone_row_bands(zones)
    ordered = []
    for by0, by1 in bands:
        band_zones = [z for z in zones if by0 - 0.5 <= z["bbox"][1] < by1 + 0.5]
        x_min = min(z["bbox"][0] for z in band_zones)
        x_max = max(z["bbox"][2] for z in band_zones)
        gutters = sorted(_zone_gutters(band_zones, x_min, x_max))

        def col_of(x0, gutters=gutters):
            col = 0
            for g in gutters:
                if x0 > g:
                    col += 1
            return col

        band_zones.sort(key=lambda z: (col_of(z["bbox"][0]), z["bbox"][1]))
        ordered.extend(band_zones)
    return ordered


def order_blocks_with_zones(manifest, zones):
    """Assegna ogni blocco (dal classificatore esistente di manifest_engine)
    alla zona Surya con cui condivide piu' area, poi ordina per (indice
    ordine-zona, Y del blocco dentro la zona). Blocchi senza sovrapposizione
    con nessuna zona (raro) restano in coda nell'ordine originale, non
    vengono mai persi."""
    ordered_zones = order_zones_recursive(zones)
    blocks = manifest["blocks"]

    def overlap_area(a, b):
        x0 = max(a[0], b[0]); y0 = max(a[1], b[1])
        x1 = min(a[2], b[2]); y1 = min(a[3], b[3])
        if x1 <= x0 or y1 <= y0:
            return 0.0
        return (x1 - x0) * (y1 - y0)

    assigned = []
    unassigned = []
    for blk in blocks:
        best_idx, best_area = None, 0.0
        for i, z in enumerate(ordered_zones):
            area = overlap_area(blk["bbox"], z["bbox"])
            if area > best_area:
                best_area, best_idx = area, i
        if best_idx is None:
            unassigned.append(blk)
        else:
            assigned.append((best_idx, blk))

    assigned.sort(key=lambda t: (t[0], t[1]["bbox"][1]))
    return [b for _, b in assigned] + unassigned


def _is_decorative_garbage_heading(text, min_len=8, max_alpha_ratio=0.3):
    """Un heading (font grande, quindi classificato heading_h2/h3 solo per
    dimensione) il cui testo e' quasi tutto simboli/punteggiatura e' quasi
    certamente una firma/fregio decorativo letto con un font senza mappatura
    Unicode corretta (stessa causa radice del bug capolettera gia' noto -
    vedi memoria dnb_manifest_docparser - ma qui su una stringa intera, non
    un singolo carattere, quindi il controllo esistente isalpha() sul
    singolo capolettera non lo intercetta). Verificato: "La Ricerca della
    Spada D'Argento" pag.2, firma illustratore in font corsivo decorativo,
    16.2pt Bold Italic, ~16% di caratteri alfabetici sui ~38 totali - ben
    sotto la soglia di qualunque titolo vero, anche brevissimo.
    Soglia scelta larga (8 caratteri minimo, 30% alfabetici) per non
    rischiare falsi positivi su titoli brevi legittimi."""
    stripped = text.strip()
    if len(stripped) < min_len:
        return False
    alpha_count = sum(1 for c in stripped if c.isalpha())
    return (alpha_count / len(stripped)) < max_alpha_ratio


def _filter_margin_garbage_fragments(blocks):
    """Frammenti di testo cortissimi (<8 caratteri) con bassa densita'
    alfabetica (<30%), posizionati OLTRE il margine stabilito dal
    contenuto vero della STESSA pagina (non una soglia fissa in punti,
    che varierebbe troppo tra un frontespizio con cornice decorativa
    enorme e una pagina normale a 2 colonne con margini stretti) -
    quasi certamente rumore da elementi decorativi (cornici, fregi,
    tratteggi) letti come testo. Il margine di riferimento e' calcolato
    dai blocchi "normali" (>=15 caratteri) della stessa pagina, quindi
    si adatta automaticamente al layout specifico invece di rischiare
    di scartare testo vero su pagine con margini stretti. Verificato su
    "La Ricerca della Spada D'Argento" pag.2 (cornice decorativa)."""
    normal_blocks = [b for b in blocks if len((b.get("text") or "").strip()) >= 15
                     and b["type"] not in ("image", "table")]
    if not normal_blocks:
        return blocks
    left_margin = min(b["bbox"][0] for b in normal_blocks)
    right_margin = max(b["bbox"][2] for b in normal_blocks)

    def is_garbage(b):
        text = (b.get("text") or "").strip()
        if not text or len(text) >= 8:
            return False
        alpha = sum(1 for c in text if c.isalpha())
        if (alpha / len(text)) >= 0.3:
            return False
        bbox = b["bbox"]
        return bbox[2] < left_margin or bbox[0] > right_margin

    return [b for b in blocks if not is_garbage(b)]


def render_markdown(manifest, zones=None):
    parts = []
    if zones:
        ordered = merge_dropcap_paragraphs(order_blocks_with_zones(manifest, zones))
    else:
        ordered = merge_dropcap_paragraphs(order_blocks_for_markdown(manifest))
    ordered = _filter_margin_garbage_fragments(ordered)
    for b in ordered:
        t = b["type"]
        if t in ("header_footer", "page_number", "image"):
            continue
        text = b.get("text", "")
        # gli heading/bold multi-riga (fuse da extract_line_groups quando la
        # stessa classificazione continua su righe fisiche consecutive)
        # vanno collassati su una riga sola: il marcatore markdown si applica
        # solo alla prima riga, altrimenti le righe successive uscirebbero
        # come paragrafo "orfano" senza marcatore
        single_line = text.replace("\n", " ")
        if t == "heading_h2":
            if _is_decorative_garbage_heading(single_line):
                continue
            parts.append(f"## {single_line}")
        elif t == "heading_h3":
            if _is_decorative_garbage_heading(single_line):
                continue
            parts.append(f"### {single_line}")
        elif t == "bold":
            parts.append(f"**{single_line}**")
        elif t == "table":
            grid = b.get("grid") or []
            if not grid:
                continue
            ncols = max(len(r) for r in grid)
            rows_md = []
            for r in grid:
                cells = list(r) + [""] * (ncols - len(r))
                cells = [(c or "").replace("|", "\\|").replace("\n", " ") for c in cells]
                rows_md.append("| " + " | ".join(cells) + " |")
            sep = "| " + " | ".join(["---"] * ncols) + " |"
            parts.append(rows_md[0] + "\n" + sep + "\n" + "\n".join(rows_md[1:]))
        else:  # paragraph, box, image_caption
            if text:
                parts.append(text)
    return fix_dice_notation(fix_jj_ligature(fix_known_corruptions("\n\n".join(parts))))


def save_page_images(doc, page_num, manifest, images_dir):
    """Salva su disco le immagini della pagina, sempre decodificate in JPEG
    standard. Alcuni PDF (es. export InDesign moderni) comprimono le
    immagini internamente in JPEG2000: `extract_image` restituiva quei byte
    grezzi con estensione .jpx, un formato che browser e viewer markdown
    non sanno decodificare (immagini invisibili a valle). Decodificando
    sempre via Pixmap (MuPDF include il decoder JPEG2000/CMYK) si ottiene
    un formato uniforme e sempre visualizzabile, qualunque sia la codifica
    originale nel PDF."""
    saved = []
    for b in manifest["blocks"]:
        if b["type"] != "image":
            continue
        xref = b["xref"]
        try:
            pix = fitz.Pixmap(doc, xref)
            if pix.colorspace is None:
                # maschera/stencil senza informazione di colore, non un'immagine visualizzabile
                continue
            if pix.colorspace.n not in (1, 3):
                pix = fitz.Pixmap(fitz.csRGB, pix)
            if pix.alpha:
                pix = fitz.Pixmap(pix, 0)
            # Le immagini decorative (sfondo/banner) restano salvate su
            # disco per riferimento, ma con prefisso "bg_" anziche' "img_":
            # il nodo n8n che assembla il markdown finale legge la cartella
            # images/ per pattern (regex su "img_pag_") anziche' dal JSON,
            # quindi questo prefisso le esclude automaticamente dal markdown
            # senza dover modificare il workflow.
            prefix = "bg" if b.get("decorative") else "img"
            fname = f"{prefix}_pag_{page_num}_{b['id']}.jpeg"
            fpath = os.path.join(images_dir, fname)
            pix.save(fpath, output="jpeg", jpg_quality=92)
        except Exception as e:
            logging.warning(f"Immagine xref={xref} pagina {page_num} non estratta: {e}")
            continue
        saved.append(fname)
    return saved


def _project_cache_path(project_name):
    return f"/shared/projects/{project_name}/.native_pagecache.json"


def _hf_index_to_json(index):
    return [{"key": list(k), "pages": sorted(v)} for k, v in index.items()]


def _hf_index_from_json(data):
    index = {}
    for entry in data:
        index[tuple(entry["key"])] = set(entry["pages"])
    return index


def _count_real_h2(doc, page_numbers, h2_mult, hf_index):
    """Conteggio di quanti heading_h2 "veri" produce un dato moltiplicatore,
    usato solo per la ricalibrazione - non e' il rendering finale. USA
    classify_page completo (non una riclassificazione semplificata): un
    primo tentativo che saltava tabelle/box/header-footer per velocita'
    sovrastimava sistematicamente il conteggio, contando come "reali" titoli
    di capitolo che nella pipeline vera vengono scartati come box
    decorativo (verificato: "L'Occhio di Traldar" pag.5, "Capitolo 1:
    Pericolo Lungo le Strade" dentro un riquadro colorato, classificato
    "box" non heading_h2) o come header ricorrente (stesso titolo ripetuto
    su ogni pagina del capitolo, pag.12 "Capitolo 2: ..." classificato
    "header_footer") - la ricalibrazione quindi non scattava mai perche' il
    conteggio veloce restava sopra soglia, pur avendo il libro zero
    capitoli reali utilizzabili. Il limite di lunghezza (<=40 caratteri) e'
    piu' stretto del filtro decorativo generale (_is_decorative_garbage_heading,
    che richiede <30% alfabetico): un frammento corrotto puo' avere
    abbastanza parole vere mescolate da superare quella soglia (verificato:
    pag.19 di Spada, un heading gia' noto/accettato come corrotto - "un
    angolo e' visibile un vecchio" con prefisso illeggibile) pur non
    essendo affatto un titolo di sezione breve come quelli veri di questo
    genere di libri."""
    count = 0
    for pno in page_numbers:
        page = doc[pno - 1]
        manifest = classify_page(doc, page, hf_index=hf_index, h2_mult=h2_mult)
        for b in manifest["blocks"]:
            if (b["type"] == "heading_h2" and len((b.get("text") or "").strip()) <= 40
                    and not _is_decorative_garbage_heading(b.get("text") or "")):
                count += 1
    return count


def _calibrate_h2_mult(doc, page_numbers, hf_index):
    """Il default (1.7x corpo testo) e' calibrato su Chiavi/SRD/Skarda, ma
    alcuni libri (verificato: "La Ricerca della Spada D'Argento", reprint
    1992) impaginano i titoli di sezione veri leggermente piu' piccoli
    (~1.55-1.65x) - restano heading_h3 e non generano MAI un capitolo
    (split-capitoli spezza solo su heading_h2), facendo collassare l'intero
    libro in 1-2 capitoli enormi. Abbassare il default globalmente non e'
    sicuro: Chiavi ha decine di sotto-titoli (nomi di stanze) allo stesso
    rapporto dimensione, che diventerebbero capitoli fantasma. Si ricalibra
    quindi SOLO per il progetto specifico, e SOLO quando il sintomo e'
    presente (pochi heading_h2 reali nonostante molte pagine): si abbassa
    progressivamente il moltiplicatore finche' il conteggio non smette di
    crescere (un "plateau" - il segnale che si e' catturato l'intero
    livello di titoli di sezione, prima di scendere nel testo normale).
    Verificato su Spada: conteggio reale 3 (default) -> ... -> 8 (1.5) -> 8
    (1.45, invariato: plateau) -> si ferma a 1.5. Soglia "insufficiente"
    fissata a 5 (non un valore piu' basso): un libro di 21 pagine con solo
    3 heading_h2 reali e' gia' chiaramente collassato in troppo pochi
    capitoli. Se abbassare il moltiplicatore non produce MAI un
    miglioramento reale, si torna al default (1.7): significa che il libro
    ha davvero pochi capitoli e non e' un problema di calibrazione.
    Verificato su SRD/Chiavi/Skarda (248/91/71 heading_h2 reali gia' al
    default): mai innescata, byte-identici a prima di questo fix."""
    n_pages = len(page_numbers)
    default_count = _count_real_h2(doc, page_numbers, 1.7, hf_index)
    if n_pages < 10 or default_count >= 5:
        return 1.7, default_count

    prev_mult, prev_count = 1.7, default_count
    for mult in (1.65, 1.6, 1.55, 1.5, 1.45, 1.4, 1.35):
        count = _count_real_h2(doc, page_numbers, mult, hf_index)
        if count > default_count and count == prev_count:
            return prev_mult, prev_count
        prev_mult, prev_count = mult, count
    if prev_count > default_count:
        return prev_mult, prev_count
    return 1.7, default_count


def _get_or_build_project_cache(doc, project_name, n_pages):
    """Costruisce (una sola volta per progetto, poi cache su disco) i dati
    che richiedono di vedere TUTTE le pagine insieme:
    - hf_index: rilevamento header/footer per ripetizione multi-pagina
    - clean_codes: codici area non ambigui, per la correzione incrociata
      di fix_area_code_notation
    Necessario per abilitare l'elaborazione pagina-per-pagina (meccanismo
    di ripresa, vedi memoria n8n_api) senza perdere questi due controlli
    che per natura hanno bisogno del contesto dell'intero libro. Il costo
    di questo calcolo (un passaggio completo, senza Surya, sull'intero
    libro) viene pagato una sola volta alla prima chiamata di un progetto.
    """
    cache_path = _project_cache_path(project_name)
    if os.path.exists(cache_path):
        with open(cache_path, "r", encoding="utf-8") as f:
            cached = json.load(f)
        if cached.get("n_pages") == n_pages:
            return (_hf_index_from_json(cached["hf_index"]), set(cached["clean_codes"]),
                    cached.get("h2_mult", 1.7))
        logging.info("Cache progetto non corrisponde al numero di pagine attuale, ricalcolo")

    logging.info(f"Costruzione cache progetto '{project_name}' ({n_pages} pagine, una tantum)...")
    page_numbers = list(range(1, n_pages + 1))
    hf_index, _ = build_header_footer_index(doc, page_numbers)
    h2_mult, real_h2_count = _calibrate_h2_mult(doc, page_numbers, hf_index)
    if h2_mult != 1.7:
        logging.info(f"Progetto '{project_name}': ricalibrato h2_mult a {h2_mult} (heading_h2 reali: {real_h2_count})")

    all_markdown = []
    for pno in page_numbers:
        page = doc[pno - 1]
        manifest = classify_page(doc, page, hf_index=hf_index, h2_mult=h2_mult)
        all_markdown.append(render_markdown(manifest))

    clean_codes = set()
    for md in all_markdown:
        for m in _AREA_CODE_CLEAN_RE.finditer(md):
            clean_codes.add(m.group(1) + m.group(2))

    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump({
            "n_pages": n_pages,
            "hf_index": _hf_index_to_json(hf_index),
            "clean_codes": sorted(clean_codes),
            "h2_mult": h2_mult,
        }, f, ensure_ascii=False)

    return hf_index, clean_codes, h2_mult


def _apply_area_code_fix(markdown, clean_codes):
    def repl(m):
        letter, suffix = m.group(1), m.group(2)
        if suffix.isdigit():
            return m.group(0)
        norm = _normalize_dice_token(suffix)
        if not norm.isdigit():
            return m.group(0)
        candidate = letter + norm
        if candidate in clean_codes:
            return candidate
        return m.group(0)
    return _AREA_CODE_AMBIG_RE.sub(repl, markdown)


@manifest_bp.route('/manifest_page_from_path', methods=['POST'])
def manifest_page_from_path():
    """
    Come /manifest_from_path ma per una SINGOLA pagina - permette al ramo
    nativo del workflow n8n di elaborare pagina-per-pagina con lo stesso
    meccanismo di ripresa gia' usato per il ramo scansionato (vedi memoria
    n8n_api): se il processo si interrompe, si riparte solo dalle pagine
    mancanti invece che dall'intero libro.

    Riceve: { pdfPath, projectName, page (1-indexed), useSuryaLayout? }
    Restituisce: { page, markdown, images, projectName, fileName, filePath }
    """
    logging.info("--- Estrazione manifest per singola pagina (PyMuPDF) ---")
    try:
        data = request.get_json()
        pdf_path = data.get('pdfPath')
        project_name = data.get('projectName')
        pno = data.get('page')
        use_surya_layout = bool(data.get('useSuryaLayout', False))

        if not pdf_path or not project_name or not pno:
            return jsonify({'error': 'pdfPath, projectName e page sono obbligatori'}), 400
        if not os.path.exists(pdf_path):
            return jsonify({'error': f'File non trovato: {pdf_path}'}), 404

        pno = int(pno)
        md_dir = f"/shared/projects/{project_name}/markdown"
        images_dir = f"/shared/projects/{project_name}/images"
        os.makedirs(md_dir, exist_ok=True)
        os.makedirs(images_dir, exist_ok=True)

        doc = fitz.open(pdf_path)
        n_pages = len(doc)
        if pno < 1 or pno > n_pages:
            return jsonify({'error': f'Pagina {pno} fuori range (1-{n_pages})'}), 400

        hf_index, clean_codes, h2_mult = _get_or_build_project_cache(doc, project_name, n_pages)

        page = doc[pno - 1]
        manifest = classify_page(doc, page, hf_index=hf_index, h2_mult=h2_mult)
        zones = _fetch_surya_zones(page) if use_surya_layout else None
        markdown = render_markdown(manifest, zones=zones)
        markdown = _apply_area_code_fix(markdown, clean_codes)
        images = save_page_images(doc, pno, manifest, images_dir)

        doc.close()

        return jsonify({
            'page': pno,
            'markdown': markdown,
            'images': images,
            'projectName': project_name,
            'fileName': f'page_{pno}.md',
            'filePath': f'{md_dir}/page_{pno}.md',
            'nPages': n_pages,
        })

    except Exception as e:
        logging.error(f"Errore critico nell'estrazione manifest per pagina: {str(e)}")
        return jsonify({'status': 'error', 'error': str(e)}), 500


@manifest_bp.route('/manifest_from_path', methods=['POST'])
def manifest_from_path():
    """
    Estrae un manifest strutturato (heading/paragrafo/box/tabella/immagine)
    da un PDF nativo con PyMuPDF, genera il markdown di ogni pagina e salva
    le immagini embedded su disco. Sostituisce pdf_to_markdown.py per il
    ramo "Dungeons_and_Byte-V_1.1-beta" del workflow, chiamato via HTTP
    invece che come processo figlio.

    Riceve: { "pdfPath": "/path/file.pdf" (documento intero, non splittato),
              "projectName": "nome",
              "maxPages": N (opzionale, per test su un sottoinsieme) }
    Restituisce: array di { page, markdown, images, projectName, fileName, filePath }
    """
    logging.info("--- Estrazione manifest da path (PyMuPDF) ---")
    try:
        data = request.get_json()
        pdf_path = data.get('pdfPath')
        project_name = data.get('projectName')
        max_pages = data.get('maxPages')
        use_surya_layout = bool(data.get('useSuryaLayout', False))

        if not pdf_path or not project_name:
            return jsonify({'error': 'pdfPath e projectName sono obbligatori'}), 400
        if not os.path.exists(pdf_path):
            return jsonify({'error': f'File non trovato: {pdf_path}'}), 404

        md_dir = f"/shared/projects/{project_name}/markdown"
        images_dir = f"/shared/projects/{project_name}/images"
        os.makedirs(md_dir, exist_ok=True)
        os.makedirs(images_dir, exist_ok=True)

        doc = fitz.open(pdf_path)
        n_pages = len(doc)
        if max_pages:
            n_pages = min(n_pages, int(max_pages))
        page_numbers = list(range(1, n_pages + 1))

        hf_index, _ = build_header_footer_index(doc, page_numbers)

        results = []
        for pno in page_numbers:
            page = doc[pno - 1]
            manifest = classify_page(doc, page, hf_index=hf_index)
            zones = _fetch_surya_zones(page) if use_surya_layout else None
            markdown = render_markdown(manifest, zones=zones)
            images = save_page_images(doc, pno, manifest, images_dir)

            results.append({
                'page': pno,
                'markdown': markdown,
                'images': images,
                'projectName': project_name,
                'fileName': f'page_{pno}.md',
                'filePath': f'{md_dir}/page_{pno}.md',
                'manifest': manifest,
            })
            logging.info(f"Pagina {pno}/{n_pages} elaborata ({len(images)} immagini)")

        doc.close()

        fixed_markdown = fix_area_code_notation([r['markdown'] for r in results])
        for r, fixed in zip(results, fixed_markdown):
            r['markdown'] = fixed

        return jsonify(results)

    except Exception as e:
        logging.error(f"Errore critico nell'estrazione manifest: {str(e)}")
        return jsonify({'status': 'error', 'error': str(e)}), 500
