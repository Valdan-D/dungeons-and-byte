#!/usr/bin/env python3
"""
Estrae un manifest strutturato per pagina da un PDF nativo (D&D): blocchi
classificati per tipo (heading, paragrafo, box/trafiletto, didascalia
immagine, header/footer, numero pagina, tabella, immagine) con relative
coordinate.

Consolida in un unico script le tecniche validate separatamente:
- classificazione riga per riga con raggruppamento (non a livello di blocco
  PyMuPDF, che a volte incolla titolo+paragrafo)
- header/footer per ripetizione multi-pagina (non per sola posizione)
- titoli riconosciuti anche per colore fuori norma, non solo per dimensione
- box/trafiletti da rettangoli vettoriali pieni (get_drawings)
- tabelle con sfondo a righe alternate (get_drawings, non find_tables)
- tabelle a dado casuale senza alcun segnale grafico (solo allineamento
  testo + intestazione "dN" riconosciuta)

Uso: manifest_final.py <file.pdf> <pagina_inizio> [<pagina_fine>]
"""
import fitz
import json
import re
import sys
import io
import logging

try:
    import pytesseract
    from PIL import Image
    _OCR_AVAILABLE = True
except Exception:
    _OCR_AVAILABLE = False
from statistics import median

_FUNC_END = re.compile(
    r'\b(delle|della|del|degli|dei|di|de|le|la|lo|gli|il|e|o|un|una|che|'
    r'con|per|su|da|in|a|al|alla|alle|ai|agli|nel|nella|nelle|nei|negli|'
    r'questo|questa|questi|queste|come|dove|quando|tra|fra|'
    r'si|mi|ti|ci|vi|ne|lo|li|le)\s*$',
    re.I
)
DICE_RE = re.compile(r'^\d*d\d+$', re.I)
ROW_START_RE = re.compile(r'^\d+([–\-]\d+)?$')


# ---------------------------------------------------------------------------
# geometria di base
# ---------------------------------------------------------------------------

def contains(outer, inner, tol=3):
    return (inner[0] >= outer[0] - tol and inner[1] >= outer[1] - tol and
            inner[2] <= outer[2] + tol and inner[3] <= outer[3] + tol)


def x_overlap(a, b):
    return not (a[2] < b[0] or b[2] < a[0])


def union_bbox(a, b):
    return (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))


def bbox_center(bbox):
    return ((bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2)


# ---------------------------------------------------------------------------
# header/footer per ripetizione multi-pagina
# ---------------------------------------------------------------------------

def normalize_margin_text(text):
    t = text.strip().lower()
    t = re.sub(r'\d+', '#', t)
    t = re.sub(r'\s+', ' ', t)
    return t


def position_bucket(bbox, W):
    cx = (bbox[0] + bbox[2]) / 2
    if cx < W / 3:
        return "left"
    if cx > W * 2 / 3:
        return "right"
    return "center"


# ---------------------------------------------------------------------------
# heading per dimensione o colore
# ---------------------------------------------------------------------------

def color_distance(c1, c2):
    r1, g1, b1 = (c1 >> 16) & 255, (c1 >> 8) & 255, c1 & 255
    r2, g2, b2 = (c2 >> 16) & 255, (c2 >> 8) & 255, c2 & 255
    return ((r1 - r2) ** 2 + (g1 - g2) ** 2 + (b1 - b2) ** 2) ** 0.5


def page_body_color(text_dict):
    """Colore piu' frequente (pesato per caratteri) sulla pagina - il colore
    del corpo testo, di riferimento per rilevare i titoli fuori norma."""
    colors = {}
    for b in text_dict["blocks"]:
        if b.get("type") != 0:
            continue
        for line in b.get("lines", []):
            for span in line.get("spans", []):
                t = span["text"].strip()
                if t and len(t) > 2:
                    c = span.get("color", 0)
                    colors[c] = colors.get(c, 0) + len(t)
    if not colors:
        return 0
    return max(colors.items(), key=lambda kv: kv[1])[0]


def page_font_thresholds(text_dict):
    all_sizes = []
    for b in text_dict["blocks"]:
        if b.get("type") != 0:
            continue
        for line in b.get("lines", []):
            for span in line.get("spans", []):
                t = span["text"].strip()
                if t and len(t) > 2:
                    all_sizes.append(round(span["size"], 1))
    body_size = median(all_sizes) if all_sizes else 10.0
    return body_size, body_size * 1.7, body_size * 1.3


def classify_line_font(text, max_size, bold, h2_thresh, h3_thresh, color_dist=0, color_thresh=20):
    """Un titolo puo' essere segnalato dalla dimensione O da un colore fuori
    norma rispetto al corpo testo (titoli di sezione a volte hanno una
    dimensione simile al testo normale ma un colore distintivo).

    Elementi decorativi isolati - capolettera (drop cap: lettera enorme che
    apre un paragrafo) o piccoli ornamenti di fine sezione in font simbolico
    (dimensione minuscola ma colore fuori norma, es. dingbat mappato su un
    carattere ASCII qualsiasi come "z") - possono soddisfare il criterio
    dimensione O colore esattamente come un heading vero. Il segnale che li
    distingue e' che sono sempre 1-2 lettere isolate (mai cifre: "9 m" o
    "36 m", frequenti nelle schede statistiche dei mostri, non vanno confusi
    con un ornamento pur avendo pochi caratteri) - nessun heading vero in
    questi manuali e' cosi' corto, i titoli sono sempre parole/frasi. Vanno
    esclusi a prescindere dal motivo (dimensione o colore) per cui altrimenti
    verrebbero promossi a heading, altrimenti diventano "capitoli" fantasma
    vuoti o quasi in split-capitoli. Nota: non copre gli ornamenti puramente
    di punteggiatura (es. "«", "'" isolati) - quelli restano come heading_h3
    residuo, un problema piu' piccolo perche' non frammenta i capitoli (solo
    heading_h2 lo fa)."""
    ok_len = len(text) < 120 and not _FUNC_END.search(text)
    is_decoration = len(text) <= 2 and text.isalpha()
    if max_size >= h2_thresh and ok_len and not is_decoration:
        return "heading_h2"
    if (max_size >= h3_thresh or color_dist >= color_thresh) and ok_len and not is_decoration:
        return "heading_h3"
    if bold and len(text) < 100:
        return "bold"
    return "paragraph"


def extract_line_groups_simple(text_dict):
    """Una entry per riga fisica di PyMuPDF (testo+bbox), senza classificazione.
    Usata dai rilevatori di tabella, che ragionano su geometria grezza."""
    groups = []
    for b in text_dict["blocks"]:
        if b.get("type") != 0:
            continue
        for line in b.get("lines", []):
            parts = [s["text"].strip() for s in line.get("spans", []) if s["text"].strip()]
            if not parts:
                continue
            groups.append({"text": " ".join(parts).strip(), "bbox": tuple(line["bbox"])})
    return groups


PARA_MERGE_MAX_GAP = 5.0
PARA_MERGE_MIN_GAP = -2.0


def ocr_fix_replacement_chars(page, groups, lang="ita", x_padding=2.0, y_padding=0.3, zoom=6.0):
    """
    Fallback automatico per righe con U+FFFD (carattere di sostituzione):
    alcuni font subset embedded nei PDF (tipico di export InDesign, uno per
    occorrenza) hanno un ToUnicode CMap con una voce mancante per uno o piu'
    glifi specifici - non e' un limite di lettura, il dato Unicode manca nel
    PDF stesso, quindi va recuperato leggendo visivamente il glifo. Ritaglia
    la singola riga incriminata (non l'intera pagina, per restare veloce) e
    la rilegge con Tesseract OCR (psm 6: gestisce sia una riga sia un
    "gruppo" che PyMuPDF ha gia' unito su 2 righe, es. testo di un box).
    Il padding verticale resta minimo apposta: in questo libro l'interlinea
    e' molto stretta (~2-3pt) e un padding troppo generoso include anche un
    frammento della riga sopra/sotto, confondendo l'OCR (verificato
    empiricamente - con padding=2 l'OCR leggeva rumore dalla riga precedente
    insieme a quella giusta).

    Due controlli di sicurezza, anch'essi emersi empiricamente:
    - se la riga originale non ha ALCUN carattere buono attorno al/ai '�'
      (tutta la riga e' irrecuperabile), quasi sempre e' un ornamento
      decorativo (bordo di box, dingbat di fine paragrafo) e NON una parola
      persa - l'OCR su questi ritagli produce spesso testo plausibile ma
      falso (es. "uu wi pra") invece di un errore visibile: si scarta la
      riga piuttosto che rischiare di inventare contenuto.
    - se ci sono caratteri buoni, si accetta l'OCR solo se il frammento piu'
      lungo di testo gia' decodificato correttamente da PyMuPDF si ritrova
      nel risultato OCR - altrimenti il ritaglio potrebbe aver letto la
      zona sbagliata e si lascia la riga invariata (con l'eventuale '�'
      residuo) invece di sostituirla con una lettura inattendibile.
    """
    if not _OCR_AVAILABLE:
        return groups
    out = []
    for g in groups:
        if "�" not in g["text"]:
            out.append(g)
            continue

        known_good = max(re.split(r"�+", g["text"]), key=len).strip()
        if len(known_good) < 3:
            logging.info(f"Riga decorativa scartata pag {page.number + 1}: {g['text']!r}")
            continue

        try:
            x0, y0, x1, y1 = g["bbox"]
            clip = fitz.Rect(x0 - x_padding, y0 + y_padding, x1 + x_padding, y1 - y_padding)
            pix = page.get_pixmap(clip=clip, matrix=fitz.Matrix(zoom, zoom))
            img = Image.open(io.BytesIO(pix.tobytes("png")))
            ocr_text = pytesseract.image_to_string(img, lang=lang, config="--psm 6").strip()
            ocr_text = " ".join(ocr_text.split())

            # spazi rimossi del tutto (non solo collassati) nel confronto:
            # l'estrazione originale a volte inserisce spazi spuri proprio
            # a ridosso del glifo non decodificato (es. "OLTRETOM BA" invece
            # di "OLTRETOMBA") che altrimenti farebbero fallire il match
            # anche quando l'OCR ha letto correttamente
            known_good_norm = re.sub(r"\s+", "", known_good.lower())[:25]
            ocr_norm = re.sub(r"\s+", "", ocr_text.lower())
            if ocr_text and "�" not in ocr_text and known_good_norm in ocr_norm:
                logging.info(f"OCR fallback riga pag {page.number + 1}: {g['text']!r} -> {ocr_text!r}")
                g["text"] = ocr_text
            else:
                logging.warning(f"OCR fallback non plausibile pag {page.number + 1}: {g['text']!r} -> tentativo {ocr_text!r}")
        except Exception as e:
            logging.warning(f"OCR fallback errore pag {page.number + 1}: {e}")
        out.append(g)
    return out


def extract_line_groups(text_dict, h2_thresh, h3_thresh, body_color=None, page=None):
    """
    Cammina riga per riga dentro ogni blocco PyMuPDF, classifica ogni riga
    singolarmente, poi fonde righe fisiche CONSECUTIVE dello stesso tipo
    quando il gap verticale tra loro e' compatibile con una normale
    interlinea (continuazione dello stesso paragrafo/titolo) e non con un
    nuovo paragrafo. La fusione NON e' limitata al singolo blocco PyMuPDF:
    per alcuni PDF (a seconda del tool di impaginazione) ogni riga fisica
    arriva gia' come blocco separato anche quando fa parte dello stesso
    paragrafo, quindi il confine di blocco da solo non e' un segnale
    affidabile di "nuovo paragrafo" - lo e' invece il gap verticale
    (misurato empiricamente: ~1.5-3.5pt in continuazione, 8pt o piu' a un
    nuovo paragrafo/titolo).
    """
    flat = []
    for b in text_dict["blocks"]:
        if b.get("type") != 0:
            continue
        for line in b.get("lines", []):
            parts = []
            max_size = 0
            bold = False
            color_chars = {}
            for span in line.get("spans", []):
                t = span["text"].strip()
                if not t:
                    continue
                parts.append(t)
                # Glifi non decodificati (U+FFFD, legature/font subset senza
                # ToUnicode) a volte riportano una dimensione bogus, spesso
                # piu' grande del testo reale circostante: se lasciata entrare
                # nel calcolo di max_size trasforma un paragrafo normale in un
                # falso heading. Il testo del glifo viene comunque incluso in
                # parts (e corretto poi da ocr_fix_replacement_chars), solo la
                # sua dimensione va esclusa dalla classificazione.
                if t.strip("�") and span["size"] > max_size:
                    max_size = span["size"]
                if any(k in span.get("font", "") for k in ("Bold", "bold", "Heavy")):
                    bold = True
                c = span.get("color", 0)
                color_chars[c] = color_chars.get(c, 0) + len(t)
            text = " ".join(parts).strip()
            if not text:
                continue
            line_color = max(color_chars.items(), key=lambda kv: kv[1])[0] if color_chars else 0
            color_dist = color_distance(line_color, body_color) if body_color is not None else 0
            ftype = classify_line_font(text, max_size, bold, h2_thresh, h3_thresh, color_dist)
            bbox = tuple(line["bbox"])
            flat.append({"font_type": ftype, "text": text, "bbox": bbox, "max_size": max_size})

    if page is not None:
        # l'OCR di fallback va fatto QUI, riga fisica per riga fisica
        # (bbox stretto), prima della fusione in paragrafi: dopo la fusione
        # il bbox puo' coprire piu' righe/l'intero paragrafo, e l'OCR a
        # riga-singola (--psm 7) fallisce su un ritaglio multi-riga
        flat = ocr_fix_replacement_chars(page, flat)

    groups = []
    for g in flat:
        if groups and groups[-1]["font_type"] == g["font_type"]:
            prev = groups[-1]
            gap = g["bbox"][1] - prev["bbox"][3]
            if PARA_MERGE_MIN_GAP <= gap <= PARA_MERGE_MAX_GAP:
                prev["text"] += " " + g["text"]
                prev["bbox"] = union_bbox(prev["bbox"], g["bbox"])
                prev["max_size"] = max(prev["max_size"], g["max_size"])
                continue
        groups.append(dict(g))
    return groups


def build_header_footer_index(doc, page_numbers):
    """
    Passata multi-pagina: raccoglie i candidati in fascia margine (alto/basso)
    su un intervallo di pagine e conta quante volte lo stesso testo normalizzato
    (numeri sostituiti da '#') ricorre nella stessa posizione (sinistra/centro/
    destra). Serve a distinguere un vero header/footer ricorrente da un titolo
    di sezione che capita solo ad essere in cima alla pagina.
    """
    index = {}
    per_page = {}
    for pno in page_numbers:
        page = doc[pno - 1]
        W, H = page.rect.width, page.rect.height
        margin_top = H * 0.08
        margin_bottom = H * 0.92
        text_dict = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE | fitz.TEXT_DEHYPHENATE)
        _, h2t, h3t = page_font_thresholds(text_dict)
        bcolor = page_body_color(text_dict)
        groups = extract_line_groups(text_dict, h2t, h3t, body_color=bcolor)
        cands = [g for g in groups if g["bbox"][1] < margin_top or g["bbox"][3] > margin_bottom]
        per_page[pno] = cands
        for g in cands:
            key = (position_bucket(g["bbox"], W), normalize_margin_text(g["text"]))
            index.setdefault(key, set()).add(pno)
    return index, per_page


# ---------------------------------------------------------------------------
# tabelle - variante 1: sfondo a righe alternate (get_drawings)
# ---------------------------------------------------------------------------

def detect_shaded_table_candidates(drawings):
    """Path di riempimento con >=4 sotto-rettangoli: candidati tabella."""
    candidates = []
    for d in drawings:
        items = d.get("items", [])
        rects = [it[1] for it in items if it[0] == "re"]
        if len(rects) < 4 or d.get("fill") is None:
            continue
        candidates.append(rects)
    return candidates


def build_column_template(rects):
    """Raggruppa i rettangoli per banda Y (riga), usa la riga con piu' celle
    come modello di colonne. Ritorna (col_edges, row_bands)."""
    rows = {}
    for r in rects:
        key = (round(r.y0, 1), round(r.y1, 1))
        rows.setdefault(key, []).append(r)

    best_row = max(rows.values(), key=len)
    best_row_sorted = sorted(best_row, key=lambda r: r.x0)
    col_edges = [best_row_sorted[0].x0] + [r.x1 for r in best_row_sorted]

    row_bands = sorted(rows.keys())
    return col_edges, row_bands


def assign_column(bbox, col_edges, tol=4):
    """Colonna solo se il testo sta INTERAMENTE dentro i bordi di una colonna
    (non solo il centro) - un titolo/heading che scavalca piu' colonne non
    e' una cella valida e va trattato come confine di fine tabella."""
    x0, x1 = bbox[0], bbox[2]
    for i in range(len(col_edges) - 1):
        if x0 >= col_edges[i] - tol and x1 <= col_edges[i + 1] + tol:
            return i
    return None


def build_shaded_table(idx, table_metas, all_groups):
    col_edges, row_bands = table_metas[idx]
    left, right = col_edges[0], col_edges[-1]
    top = row_bands[0][0]

    # tetto: non oltrepassare l'inizio di un'ALTRA tabella che condivide lo
    # stesso range di colonne (x sovrapposta) - evita di "invadere" la
    # tabella successiva quando sono vicine tra loro
    ceiling = float("inf")
    for j, (ce, rb) in enumerate(table_metas):
        if j == idx:
            continue
        other_top = rb[0][0]
        x_ov = not (ce[-1] < left or ce[0] > right)
        if other_top > top and x_ov:
            ceiling = min(ceiling, other_top)

    cands = sorted(
        [g for g in all_groups if g["bbox"][0] >= left - 5 and g["bbox"][2] <= right + 5
         and g["bbox"][1] >= top - 12 and g["bbox"][1] < ceiling - 2],
        key=lambda g: g["bbox"][1]
    )
    included = []
    prev_bottom = top - 12
    max_gap = 30
    for g in cands:
        gap = g["bbox"][1] - prev_bottom
        if included and gap > max_gap:
            break
        col = assign_column(g["bbox"], col_edges)
        if col is None:
            break  # testo che scavalca piu' colonne (titolo/heading) -> fine tabella
        g["_col"] = col
        included.append(g)
        prev_bottom = max(prev_bottom, g["bbox"][3])

    # passo 1: raggruppa in righe FISICHE (stessa Y = stessa riga visiva)
    included.sort(key=lambda g: (round(g["bbox"][1], 0), g["bbox"][0]))
    physical_rows = []
    cur_row = []
    cur_y = None
    for g in included:
        y0 = g["bbox"][1]
        if cur_y is None or abs(y0 - cur_y) < 4:
            cur_row.append(g)
            cur_y = y0 if cur_y is None else cur_y
        else:
            physical_rows.append(cur_row)
            cur_row = [g]
            cur_y = y0
    if cur_row:
        physical_rows.append(cur_row)

    physical_cells = []
    for row in physical_rows:
        cells = [""] * (len(col_edges) - 1)
        for g in row:
            ci = g["_col"]
            cells[ci] = (cells[ci] + " " + g["text"]).strip() if cells[ci] else g["text"]
        physical_cells.append(cells)

    # passo 2: fondi le righe fisiche in righe LOGICHE - una riga fisica
    # senza nulla in colonna 0 e' la continuazione (a capo) della riga sopra
    grid = []
    for cells in physical_cells:
        starts_new_row = bool(cells[0].strip())
        if starts_new_row or not grid:
            grid.append(cells)
        else:
            prev = grid[-1]
            for ci, val in enumerate(cells):
                if val:
                    prev[ci] = (prev[ci] + " " + val).strip() if prev[ci] else val

    bbox = (left, top, right, prev_bottom)
    return {"bbox": [round(v, 1) for v in bbox], "grid": grid, "source": "shading"}


# ---------------------------------------------------------------------------
# tabelle - variante 2: tabella a dado senza alcun segnale grafico
# ---------------------------------------------------------------------------

def find_dice_headers(groups):
    """Cerca intestazioni tipo 'd4'/'d100' seguite, sulla stessa riga
    visiva, da un'etichetta di colonna (es. 'Effetto', 'Creatura'). Se piu'
    intestazioni 'dN' cadono sulla stessa riga fisica (tabelle a piu' coppie
    di colonne affiancate, es. '1d8 Creatura | 1d8 Creatura'), tiene solo la
    piu' a sinistra - il resto della riga viene comunque incluso da
    build_dice_table tramite col_right."""
    raw_headers = []
    for g in groups:
        if not DICE_RE.match(g["text"]):
            continue
        y0 = g["bbox"][1]
        partner = None
        for h in groups:
            if h is g:
                continue
            if abs(h["bbox"][1] - y0) < 3 and h["bbox"][0] > g["bbox"][2]:
                partner = h
                break
        if partner is not None:
            raw_headers.append((g, partner))

    raw_headers.sort(key=lambda hp: (round(hp[0]["bbox"][1], 0), hp[0]["bbox"][0]))
    headers = []
    last_y = None
    for dice_h, label_h in raw_headers:
        y0 = dice_h["bbox"][1]
        if last_y is not None and abs(y0 - last_y) < 3:
            continue
        headers.append((dice_h, label_h))
        last_y = y0
    return headers


def build_dice_table(dice_h, label_h, groups, right_width=210, x_tol=6, max_gap=25):
    col_left = dice_h["bbox"][0]
    col_mid = label_h["bbox"][0]
    col_right = col_mid + right_width
    top = dice_h["bbox"][1]

    cands = sorted(
        [g for g in groups if g["bbox"][1] > top + 2 and g["bbox"][0] >= col_left - x_tol
         and g["bbox"][2] <= col_right + x_tol],
        key=lambda g: g["bbox"][1]
    )

    # passo 1: raggruppa in righe FISICHE (piccole differenze di baseline tra
    # span diversi sulla stessa riga visiva)
    physical_rows = []
    cur_phys = []
    cur_y = None
    for g in cands:
        y0 = g["bbox"][1]
        if cur_y is None or abs(y0 - cur_y) < 4:
            cur_phys.append(g)
            cur_y = y0 if cur_y is None else cur_y
        else:
            physical_rows.append(cur_phys)
            cur_phys = [g]
            cur_y = y0
    if cur_phys:
        physical_rows.append(cur_phys)

    # passo 2: classifica per colonna, poi decide inizio riga vs continuazione
    rows = []
    cur = None
    prev_bottom = dice_h["bbox"][3]
    stop = False
    for phys in physical_rows:
        if stop:
            break
        row_y = min(g["bbox"][1] for g in phys)
        gap = row_y - prev_bottom
        if (rows or cur) and gap > max_gap:
            break

        col0_text, col1_text = "", ""
        for g in sorted(phys, key=lambda g: g["bbox"][0]):
            x0, x1 = g["bbox"][0], g["bbox"][2]
            in_col0 = col_left - x_tol <= x0 and x1 <= col_mid - x_tol + 12
            in_col1 = col_mid - x_tol <= x0 and x1 <= col_right + x_tol
            if in_col0:
                col0_text = (col0_text + " " + g["text"]).strip() if col0_text else g["text"]
            elif in_col1:
                col1_text = (col1_text + " " + g["text"]).strip() if col1_text else g["text"]
            else:
                stop = True
                break
        if stop and not col0_text and not col1_text:
            break

        if col0_text and ROW_START_RE.match(col0_text):
            if cur:
                rows.append(cur)
            cur = [col0_text, col1_text]
        else:
            if cur is None:
                # colonna 0 mancante (es. numero riga reso come icona
                # grafica, non testo) - apri comunque la riga
                cur = ["", col1_text]
            else:
                cur[1] = (cur[1] + " " + col1_text).strip() if cur[1] else col1_text
        prev_bottom = max(g["bbox"][3] for g in phys)
    if cur:
        rows.append(cur)

    bbox = (col_left, top, col_right, prev_bottom)
    return {"bbox": [round(v, 1) for v in bbox], "grid": rows, "source": "text"}


# ---------------------------------------------------------------------------
# tabelle - variante 3: griglia senza alcun segnale grafico (ne' sfondo ne'
# intestazione dN) - es. tabelle di statistiche a colonne allineate
# ---------------------------------------------------------------------------

CELL_MAX_LEN = 28
GRID_X_TOL = 15


def is_cell_like(text):
    """Frammento breve senza terminazione di frase - tipico di una cella di
    tabella senza griglia grafica (etichetta, nome, valore corto), a
    differenza di una riga di paragrafo che scorre a capo."""
    t = text.strip()
    if not t or len(t) > CELL_MAX_LEN:
        return False
    return t[-1] not in '.!?,;:'


def group_physical_rows(groups, y_tol=4):
    """Raggruppa gruppi di testo in righe fisiche (stessa banda Y), ordinate
    per Y poi per X."""
    order = sorted(groups, key=lambda g: g["bbox"][1])
    rows = []
    for g in order:
        if rows and abs(g["bbox"][1] - rows[-1][0]) < y_tol:
            rows[-1][1].append(g)
        else:
            rows.append([g["bbox"][1], [g]])
    return [(y, sorted(items, key=lambda g: g["bbox"][0])) for y, items in rows]


def _find_grid_table_row_runs_in_subset(raw_lines, min_cols, y_tol, max_row_gap):
    physical_rows = group_physical_rows(raw_lines, y_tol=y_tol)

    candidates = []
    for y, items in physical_rows:
        if len(items) < min_cols:
            continue
        if any(x_overlap(items[i]["bbox"], items[i + 1]["bbox"]) for i in range(len(items) - 1)):
            continue
        if not all(is_cell_like(g["text"]) for g in items):
            continue
        candidates.append((y, items))

    runs = []
    cur = []
    prev_y = None
    for y, items in candidates:
        if cur and (y - prev_y) > max_row_gap:
            if len(cur) >= 2:
                runs.append(cur)
            cur = []
        cur.append((y, items))
        prev_y = y
    if len(cur) >= 2:
        runs.append(cur)
    return runs


def find_grid_table_row_runs(raw_lines, W, min_cols=3, y_tol=4, max_row_gap=20):
    """Individua sequenze di righe fisiche consecutive che si comportano
    come una tabella senza alcun segnale grafico (niente sfondo, niente
    intestazione 'dN'): >= min_cols frammenti brevi non sovrapposti in X per
    riga. Il normale testo scorrevole su piu' colonne di pagina non genera
    mai 3+ frammenti fianco a fianco sulla stessa banda Y (ogni colonna di
    pagina produce una sola riga per banda Y), quindi il segnale distingue
    in modo affidabile una griglia di celle da un layout a colonne.

    L'analisi va condotta separatamente per meta' sinistra/destra pagina:
    un layout a due colonne puo' avere una frase della colonna opposta alla
    stessa altezza Y di una riga di tabella - raggruppare per sola Y senza
    distinguere la meta' pagina la farebbe scartare (o mescolerebbe due
    tabelle indipendenti, una per meta')."""
    left = [g for g in raw_lines if (g["bbox"][0] + g["bbox"][2]) / 2 < W / 2]
    right = [g for g in raw_lines if (g["bbox"][0] + g["bbox"][2]) / 2 >= W / 2]
    return (_find_grid_table_row_runs_in_subset(left, min_cols, y_tol, max_row_gap) +
            _find_grid_table_row_runs_in_subset(right, min_cols, y_tol, max_row_gap))


def build_grid_table(run, x_tol=GRID_X_TOL):
    """Costruisce la tabella da una sequenza di righe candidate: le colonne
    sono derivate dagli inizi X ricorrenti (non da rettangoli disegnati),
    usando come modello la riga con piu' celle."""
    template_items = max((items for _, items in run), key=len)
    col_starts = sorted(g["bbox"][0] for g in template_items)

    def col_of(x0):
        best, best_d = None, x_tol
        for i, cs in enumerate(col_starts):
            d = abs(x0 - cs)
            if d < best_d:
                best, best_d = i, d
        return best

    grid = []
    all_bboxes = []
    for _, items in run:
        row = [""] * len(col_starts)
        for g in items:
            ci = col_of(g["bbox"][0])
            if ci is None:
                continue
            row[ci] = (row[ci] + " " + g["text"]).strip() if row[ci] else g["text"]
            all_bboxes.append(g["bbox"])
        grid.append(row)

    left = min(b[0] for b in all_bboxes)
    top = min(b[1] for b in all_bboxes)
    right = max(b[2] for b in all_bboxes)
    bottom = max(b[3] for b in all_bboxes)
    return {"bbox": [round(v, 1) for v in (left, top, right, bottom)], "grid": grid, "source": "grid_text"}


def bbox_overlap_ratio(a, b):
    """Frazione dell'area di a coperta dalla sovrapposizione con b."""
    ix0, iy0 = max(a[0], b[0]), max(a[1], b[1])
    ix1, iy1 = min(a[2], b[2]), min(a[3], b[3])
    if ix1 <= ix0 or iy1 <= iy0:
        return 0.0
    inter = (ix1 - ix0) * (iy1 - iy0)
    area_a = (a[2] - a[0]) * (a[3] - a[1])
    return inter / area_a if area_a > 0 else 0.0


def detect_tables(drawings, raw_lines, W):
    tables = []

    shaded_candidates = detect_shaded_table_candidates(drawings)
    table_metas = [build_column_template(rects) for rects in shaded_candidates]
    for i in range(len(shaded_candidates)):
        tables.append(build_shaded_table(i, table_metas, raw_lines))

    # tabelle a dado testuali: si costruisce comunque la tabella candidata e
    # si scarta solo se il suo bbox RISULTANTE si sovrappone molto con una
    # gia' rilevata via sfondo. Non basta guardare la sola intestazione: per
    # le tabelle a sfondo l'intestazione sta sopra la prima riga ombreggiata,
    # quindi puo' cadere fuori dal bbox della tabella gia' trovata.
    for dice_h, label_h in find_dice_headers(raw_lines):
        candidate = build_dice_table(dice_h, label_h, raw_lines)
        if any(bbox_overlap_ratio(candidate["bbox"], t["bbox"]) > 0.5 for t in tables):
            continue
        tables.append(candidate)

    # tabelle a griglia testuale (variante 3): stesso criterio di scarto per
    # sovrapposizione, cosi' non duplica una tabella gia' rilevata via
    # sfondo o pattern dado
    for run in find_grid_table_row_runs(raw_lines, W):
        candidate = build_grid_table(run)
        if any(bbox_overlap_ratio(candidate["bbox"], t["bbox"]) > 0.5 for t in tables):
            continue
        tables.append(candidate)

    return tables


# ---------------------------------------------------------------------------
# classificazione pagina
# ---------------------------------------------------------------------------

def classify_page(doc, page, hf_index=None, hf_threshold=2):
    W, H = page.rect.width, page.rect.height
    page_rect = (0.0, 0.0, W, H)
    margin_top = H * 0.08
    margin_bottom = H * 0.92

    text_dict = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE | fitz.TEXT_DEHYPHENATE)
    raw_lines = extract_line_groups_simple(text_dict)

    try:
        drawings = page.get_drawings()
    except Exception:
        drawings = []

    tables = detect_tables(drawings, raw_lines, W)

    def clip_to_page(r):
        return (max(r[0], page_rect[0]), max(r[1], page_rect[1]),
                min(r[2], page_rect[2]), min(r[3], page_rect[3]))

    # disegni vettoriali (per box/trafiletti) - clippati contro i bordi pagina
    box_rects = []
    for d in drawings:
        rect = d.get("rect")
        if rect is None:
            continue
        rect = clip_to_page(tuple(rect))
        x0, y0, x1, y1 = rect
        if x1 <= x0 or y1 <= y0:
            continue
        area = (x1 - x0) * (y1 - y0)
        has_fill = d.get("fill") is not None
        has_stroke = d.get("color") is not None and (d.get("width") or 0) > 0
        if area < 2500:
            continue
        if has_fill or has_stroke:
            box_rects.append((x0, y0, x1, y1))

    # immagini con coordinate, clippate contro i bordi pagina (alcuni PDF
    # dichiarano rettangoli immagine che escono dalla pagina, es. sfondi con
    # bleed) - il filtro dimensione minima va applicato DOPO il clip
    image_rects = []
    for img_idx, img_ref in enumerate(page.get_images(full=True)):
        xref = img_ref[0]
        for r in page.get_image_rects(xref):
            rc = clip_to_page(tuple(r))
            x0, y0, x1, y1 = rc
            if x1 <= x0 or y1 <= y0:
                continue
            if (x1 - x0) < 80 or (y1 - y0) < 80:
                continue
            image_rects.append({"xref": xref, "idx": img_idx, "bbox": rc})

    body_size, h2_thresh, h3_thresh = page_font_thresholds(text_dict)
    body_color = page_body_color(text_dict)

    line_groups = extract_line_groups(text_dict, h2_thresh, h3_thresh, body_color=body_color, page=page)

    # scarta i gruppi di testo gia' consumati da una tabella rilevata, per
    # evitare che lo stesso contenuto compaia due volte nel manifest
    def in_any_table(bbox):
        cx, cy = bbox_center(bbox)
        for t in tables:
            tb = t["bbox"]
            if tb[0] - 3 <= cx <= tb[2] + 3 and tb[1] - 3 <= cy <= tb[3] + 3:
                return True
        return False

    line_groups = [g for g in line_groups if not in_any_table(g["bbox"])]

    blocks_out = []
    bid = 0
    for grp in line_groups:
        text = grp["text"]
        bbox = grp["bbox"]

        bid += 1
        block_id = f"b{bid}"
        btype = None
        anchor = None

        # 1. margine alto/basso -> header/footer o numero pagina. Controllata
        #    PRIMA del box: un banner di capitolo ricorrente che ha anche uno
        #    sfondo decorativo (es. dentro un rettangolo pieno) e' per RUOLO
        #    un header ricorrente, non un trafiletto di contenuto - la
        #    ripetizione multi-pagina ha priorita' sulla geometria del box.
        #    Un numero isolato in fascia margine e' gia' un segnale forte di
        #    per se' (non serve la ripetizione, che puo' fallire ai bordi
        #    dell'intervallo processato, es. numerazione alternata
        #    sinistra/destra). Il testo generico invece richiede la
        #    ripetizione per non scambiare un titolo di sezione per un header.
        if bbox[1] < margin_top or bbox[3] > margin_bottom:
            if re.fullmatch(r"\d{1,4}", text):
                btype = "page_number"
            elif hf_index is not None:
                key = (position_bucket(bbox, W), normalize_margin_text(text))
                if len(hf_index.get(key, ())) >= hf_threshold:
                    btype = "header_footer"

        # 2. dentro un box vettoriale
        if btype is None:
            for br in box_rects:
                if contains(br, bbox):
                    btype = "box"
                    break

        # 3. didascalia immagine: subito sotto un'immagine, font piu' piccolo del corpo, x sovrapposta
        if btype is None:
            for ir in image_rects:
                irb = ir["bbox"]
                if (0 <= (bbox[1] - irb[3]) < 40 and x_overlap(bbox, irb)
                        and grp["max_size"] < body_size * 0.95):
                    btype = "image_caption"
                    anchor = f"img{ir['idx'] + 1}"
                    break

        # 4. fallback: tipo gia' deciso a livello di riga/gruppo (dimensione o colore)
        if btype is None:
            btype = grp["font_type"]

        entry = {"id": block_id, "type": btype, "bbox": [round(v, 1) for v in bbox], "text": text[:300]}
        if anchor:
            entry["anchor"] = anchor
        blocks_out.append(entry)

    for i, ir in enumerate(image_rects):
        blocks_out.append({"id": f"img{i+1}", "type": "image",
                            "bbox": [round(v, 1) for v in ir["bbox"]], "xref": ir["xref"]})

    for i, t in enumerate(tables):
        blocks_out.append({"id": f"tbl{i+1}", "type": "table", "bbox": t["bbox"],
                            "grid": t["grid"], "source": t["source"]})

    return {
        "page": page.number + 1,
        "width": round(W, 1),
        "height": round(H, 1),
        "rotation": page.rotation,
        "blocks": blocks_out,
    }


def main():
    pdf_path = sys.argv[1]
    page_start = int(sys.argv[2])
    page_end = int(sys.argv[3]) if len(sys.argv) > 3 else page_start
    doc = fitz.open(pdf_path)
    page_numbers = list(range(page_start, page_end + 1))

    hf_index, _ = build_header_footer_index(doc, page_numbers)

    manifests = []
    for pno in page_numbers:
        page = doc[pno - 1]
        manifests.append(classify_page(doc, page, hf_index=hf_index))

    if len(manifests) == 1:
        print(json.dumps(manifests[0], ensure_ascii=False, indent=2))
    else:
        print(json.dumps(manifests, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
