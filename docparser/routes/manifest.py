from flask import Blueprint, request, jsonify
import logging
import os
import re
import fitz  # PyMuPDF

from manifest_engine import classify_page, build_header_footer_index

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


def render_markdown(manifest):
    parts = []
    ordered = merge_dropcap_paragraphs(order_blocks_for_markdown(manifest))
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
            parts.append(f"## {single_line}")
        elif t == "heading_h3":
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
            markdown = render_markdown(manifest)
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
