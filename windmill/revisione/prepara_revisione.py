# requirements:
# pymupdf
# scikit-learn
# numpy
"""Revisione titoli - passo 'prepara': proposte automatiche + box -> <out>/<progetto>/revisione/.

Legge (sempre da /shared/projects/<progetto>): json/pag_N_res.json, il PDF in pool/ e il catalogo dei box.
Scrive in <cartella_output o /shared/projects>/<progetto>/revisione/: proposte.json, box.json, stato.json.
Non usa la GPU e si puo' rilanciare quando il catalogo cresce."""
import collections
import glob
import json
import math
import os
import re
import statistics as st

import pymupdf

BASE = "/shared/projects/"
TIT = ("paragraph_title", "doc_title", "figure_title")
TESTO = ("text", "paragraph_title", "doc_title")
SCUOLE = r"(Abiurazione|Ammaliamento|Divinazione|Evocazione|Illusione|Invocazione|Necromanzia|Trasmutazione)"
RE_SCUOLA = re.compile(r"^\s*" + SCUOLE + r"\s+di\b|^\s*" + SCUOLE + r"\s*\(?\s*trucchetto", re.I)
RE_LIVELLO = re.compile(r"^\W*\$?\s*\d+\s*(\^\s*\{?\s*\\?\w*\s*\}?|[°º])?\s*\$?\s*livello\s*$|^\s*trucchetti\s*\(|^\s*incantesimi\s+(da|del|dello|della)\b", re.I)


def regola(testo):
    righe = [r.strip() for r in testo.split("\n") if r.strip()]
    if len(righe) >= 2 and RE_SCUOLA.match(righe[1]):
        return "titolo_unito_testo"
    if len(righe) <= 2 and RE_LIVELLO.match(righe[0]):
        return "elenco"
    return None


# intestazione di tabella di dadi: "d12 Effetto", "d6 Gesta compiute", "1d100 Monile" (dado valido + testo). NON i codici di stanza
# con i due punti ("D3: Tana dei lupi"), che sono titoli veri. Misurata sulle decisioni umane di 3 libri: 42 voci, 0 titoli veri.
RE_DADI = re.compile(r"^\s*\d*\s*[dD]\s*(4|6|8|10|12|20|100|%)\s+(?![:.])\S")


def solo_numeri(t):
    t = t.lower()
    # solo numeri o una notazione di dadi ("2d6", "d20+3"); NON i codici di area come "D3: Tana dei lupi"
    return bool(re.fullmatch(r"[\d\s.,:;/+\-–]*", t)) or bool(re.fullmatch(r"\d*d\d+\s*([+\-–]\s*\d+)?", t.strip()))


def leggi_pagine(libro):
    pag = {}
    for f in glob.glob(BASE + libro + "/json/pag_*_res.json"):
        pag[int(re.search(r"pag_(\d+)_res", f).group(1))] = json.load(open(f))
    return pag


# copertina e crediti: il marchio da solo ("DUNGEONS & DRAGONS") non e' un titolo; le voci dei crediti sulla stessa pagina di
# "Riconoscimenti" ("In copertina", "Studio D&D", "Localizzazione"...) nemmeno. Il titolo del libro (copertina e frontespizio) e
# "Riconoscimenti" restano titoli. Regola ricavata dalle decisioni umane su quattro libri.
def _chiave_testo(t):
    t = re.sub(r"\$[^$]*\$", " ", t or "")
    return re.sub(r"[^a-z0-9]", "", t.lower().replace("&", ""))


def copertina_e_crediti(out):
    n_marchio = n_crediti = 0
    crediti_pag = None
    for p in out:  # in ordine di lettura
        if p["proposta"] not in ("titolo",):
            continue
        k = _chiave_testo(p["testo"])
        if p["pag"] <= 3 and k in ("dungeonsdragons", "dungeonsanddragons"):
            p["proposta"], p["motivo"], p["livello_proposto"] = "non_titolo", "marchio (non e' un titolo)", None
            n_marchio += 1
            continue
        if p["pag"] <= 8 and k in ("riconoscimenti", "crediti"):
            crediti_pag = p["pag"]
            continue
        if crediti_pag is not None and p["pag"] == crediti_pag:
            p["proposta"], p["motivo"], p["livello_proposto"] = "non_titolo", "voce dei crediti (sotto 'Riconoscimenti')", None
            n_crediti += 1
    return n_marchio, n_crediti


def proposte(pag):
    blocchi = []
    for n in sorted(pag):
        d = pag[n]
        L = d["parsing_res_list"]
        ordine = sorted(range(len(L)), key=lambda i: (L[i].get("block_order") if L[i].get("block_order") is not None else 10 ** 6 + i))
        for k, i in enumerate(ordine):
            b = L[i]
            blocchi.append({"pag": n, "pos": k, "idx": i, "label": b["block_label"], "bbox": b.get("block_bbox"), "H": d["height"],
                            "testo": (b.get("block_content") or "").strip()})
    ripet = collections.Counter(b["testo"].lower() for b in blocchi if b["label"] in TIT)
    per_pag = collections.defaultdict(list)
    for b in blocchi:
        per_pag[b["pag"]].append(b)
    out = []
    for idx, b in enumerate(blocchi):
        if b["label"] not in TIT:
            continue
        t = b["testo"]
        classe, motivo = "titolo", ""
        succ = next((x for x in blocchi[idx + 1: idx + 3] if x["pag"] == b["pag"]), None)
        r = regola(t)
        if r:
            classe, motivo = r, "regola: " + ("nome + scuola e livello" if r == "titolo_unito_testo" else "intestazione di elenco incantesimi")
        elif b["label"] == "figure_title":
            classe, motivo = "tabella", "figure_title"
        elif ripet[t.lower()] >= 20:
            classe, motivo = "non_titolo", "testo ripetuto %d volte" % ripet[t.lower()]
        elif solo_numeri(t):
            classe, motivo = "non_titolo", "solo numeri/dadi"
        elif RE_DADI.match(t):
            classe, motivo = "tabella", "intestazione di tabella di dadi"
        elif succ and succ["label"] == "table":
            classe, motivo = "tabella", "tabella subito dopo"
        h = (b["bbox"][3] - b["bbox"][1]) * 1000 / b["H"] if b["bbox"] else 0
        liv = 1 if (b["label"] == "doc_title" or h >= 35) else 2
        pp = per_pag[b["pag"]]

        def vic(k):
            if 0 <= k < len(pp):
                return {"label": pp[k]["label"], "testo": pp[k]["testo"].replace("\n", " ")[:90]}
            return None
        out.append({"pag": b["pag"], "pos": b["pos"], "idx": b["idx"], "label": b["label"], "testo": t, "bbox": b["bbox"],
                    "altezza_mille": round(h, 1), "proposta": classe, "motivo": motivo,
                    "livello_proposto": liv if classe == "titolo" else None, "prima": vic(b["pos"] - 1), "dopo": vic(b["pos"] + 1)})
    copertina_e_crediti(out)
    return out


def _norm_h(t):
    t = re.sub(r"[^a-zà-ù ]+", " ", (t or "").lower())
    return re.sub(r"\s+", " ", t).strip()


def _simili(a, b):
    import difflib
    return bool(a) and bool(b) and (a == b or difflib.SequenceMatcher(None, a, b).ratio() >= 0.9)


def intestazioni_correnti(pag, prop):
    """Le intestazioni ripetute in cima alle pagine (blocchi 'header') cambiano dove comincia una sezione.
    - un titolo uguale all'intestazione corrente, vicino all'inizio della sezione, ne e' l'inizio (livello 1);
    - lo stesso titolo ripetuto piu' avanti nella sezione e' una ripetizione (non titolo);
    - se l'inizio non ha un titolo, si aggiunge una voce sintetica in cima alla pagina.
    Servono sezioni lunghe almeno 2 pagine (esclude copertine e righe isolate)."""
    runs = []  # [pagina_inizio, testo, norm, pagine]
    for n in sorted(pag):
        hs = [b for b in pag[n]["parsing_res_list"] if b["block_label"] == "header" and re.search(r"[A-Za-zÀ-ù]{4,}", b.get("block_content") or "")]
        if not hs:
            continue
        t = max(hs, key=lambda b: len(b["block_content"]))["block_content"].strip().split("\n")[0]
        k = _norm_h(t)
        recenti = [r[2] for r in runs[-4:]]
        if runs and _simili(k, runs[-1][2]):
            runs[-1][3] += 1
        elif any(_simili(k, r) for r in recenti):
            continue  # intestazioni alternate (pagina sinistra/destra): non e' una nuova sezione
        else:
            runs.append([n, t, k, 1])
    sint, n_inizi, n_rip = [], 0, 0
    for (n0, t, k, lung), succ in zip(runs, runs[1:] + [None]):
        if lung < 2:
            continue
        fine = (succ[0] - 1) if succ else max(pag)
        titoli = [p for p in prop if p["label"] != "header" and n0 - 1 <= p["pag"] <= fine and _simili(_norm_h(p["testo"].split("\n")[0]), k)]
        inizio = titoli[0] if titoli and titoli[0]["pag"] <= n0 else None
        if inizio:
            inizio["proposta"], inizio["motivo"], inizio["livello_proposto"] = "titolo", "inizio sezione (cambio di intestazione)", 1
            n_inizi += 1
        else:
            sint.append({"pag": n0, "pos": -1, "idx": None, "label": "header", "testo": t, "bbox": None, "altezza_mille": 0,
                         "proposta": "titolo", "motivo": "cambio di intestazione corrente", "livello_proposto": 1,
                         "prima": None, "dopo": None, "sintetico": True})
        for p in titoli:
            if p is not inizio and p["proposta"] != "non_titolo":
                p["proposta"], p["motivo"], p["livello_proposto"] = "non_titolo", "ripetizione dell'intestazione corrente", None
                n_rip += 1
    return sint, n_inizi, n_rip


# ---------- box ----------
def match_vett(dr, par):
    if len(dr["items"]) != par["items"]:
        return False
    f, pf = dr.get("fill"), par.get("fill")
    if (f is None) != (pf is None):
        return False
    if f and any(abs(a - b) > 0.03 for a, b in zip(f, pf)):
        return False
    if par.get("line") is None and dr.get("color") is not None:
        return False
    r = dr["rect"]
    if "larghezza_pt" in par:
        if abs(r.width - par["larghezza_pt"]) > 4:
            return False
    elif r.width < par.get("larghezza_min_pt", 0):
        return False
    if r.width > par.get("larghezza_max_pt", 1e9):
        return False
    return r.height >= par.get("altezza_min_pt", 0)


def bg_block(pix, bb, ow):
    f = pix.width / ow
    x0, y0, x1, y1 = [int(v * f) for v in bb]
    x0 = max(x0, 0); y0 = max(y0, 0); x1 = min(x1, pix.width - 1); y1 = min(y1, pix.height - 1)
    s, n, W = pix.samples, pix.n, pix.width
    px = []
    for y in range(y0, y1, 2):
        for x in range(x0, x1, 2):
            i = (y * W + x) * n
            px.append((s[i] + s[i + 1] + s[i + 2], s[i], s[i + 1], s[i + 2]))
    if len(px) < 8:
        return None
    px.sort(reverse=True)
    top = px[: max(4, len(px) * 2 // 5)]
    return [st.median(p[k] for p in top) for k in (1, 2, 3)]


SOLO_TITOLO = ("paragraph_title", "doc_title", "figure_title", "header", "footer", "number", "image", "header_image", "footer_image")
RE_BATTUTA = re.compile(r"^\s*(\d{1,2}\s+)?[A-ZÀ-Ý][\w'à-ÿ]{1,14}(\s*\([^)]{0,40}\))?\s*:\s*\S")


def _battuta(b):
    return b["l"] in TESTO and bool(RE_BATTUTA.match(b["t"]))


def _dialogo(blocchi):
    """Esempio di gioco: la maggior parte dei blocchi di testo inizia con 'Nome:' (battute)."""
    testo = [b for b in blocchi if b["l"] in TESTO]
    return len(testo) >= 1 and sum(1 for b in testo if _battuta(b)) * 2 >= len(testo)


def _estendi_dialogo(dentro, bl, R):
    """Il riquadro trovato dal colore taglia spesso il dialogo: si aggiungono i blocchi adiacenti
    (stessa colonna, distanza <= 14 pt) che sono battute o continuazioni (iniziano in minuscolo)."""
    dentro = list(dentro)
    x0, y0, x1, y1 = R.x0, R.y0, R.x1, R.y1
    cambiato = True
    while cambiato:
        cambiato = False
        for b in bl:
            if b in dentro or b["l"] not in TESTO or b["l"] != "text":
                continue
            r = b["r"]
            ov = min(x1, r[2]) - max(x0, r[0])
            if ov < 0.6 * (r[2] - r[0]):
                continue
            if max(r[1] - y1, y0 - r[3]) > 14:  # distanza verticale dal riquadro (negativa = si sovrappongono)
                continue
            if _battuta(b) or b["t"][:1].islower():
                dentro.append(b)
                x0, y0, x1, y1 = min(x0, r[0] - 6), min(y0, r[1] - 4), max(x1, r[2] + 6), max(y1, r[3] + 4)
                cambiato = True
    return dentro, pymupdf.Rect(x0, y0, x1, y1)


S_PIXEL = 0.5
PAR_PIXEL = {"larghezza_min_pt": 215, "larghezza_max_pt": 245, "altezza_min_pt": 30, "riempimento_min": 0.5, "area_min_pt2": 2500}


def _tinto(r, g, b):
    return g <= 205 and r >= 170 and r >= g + 15 and b >= g + 8 and b <= 235 and r >= b - 30


def trova_pixel(page, par=PAR_PIXEL, maschera=None):
    """Riquadri rosa/lilla (immagini) a larghezza di colonna: componenti connesse di pixel tinti."""
    pm = page.get_pixmap(matrix=pymupdf.Matrix(S_PIXEL, S_PIXEL), colorspace=pymupdf.csRGB, alpha=False)
    w, h, s = pm.width, pm.height, pm.samples
    m = bytearray(w * h)
    for i in range(w * h):
        if (maschera or _tinto)(s[3 * i], s[3 * i + 1], s[3 * i + 2]):
            m[i] = 1
    seen = bytearray(w * h)
    out = []
    for st in range(w * h):
        if not m[st] or seen[st]:
            continue
        stack = [st]; seen[st] = 1; a = 0; x0 = w; y0 = h; x1 = y1 = 0
        while stack:
            i = stack.pop(); y, x = divmod(i, w); a += 1
            x0 = min(x0, x); x1 = max(x1, x); y0 = min(y0, y); y1 = max(y1, y)
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    xx, yy = x + dx, y + dy
                    if 0 <= xx < w and 0 <= yy < h:
                        j = yy * w + xx
                        if m[j] and not seen[j]:
                            seen[j] = 1; stack.append(j)
        bw, bh = (x1 - x0 + 1) / S_PIXEL, (y1 - y0 + 1) / S_PIXEL
        fill = a / S_PIXEL / S_PIXEL / (bw * bh)
        if (a / S_PIXEL / S_PIXEL >= par["area_min_pt2"] and par["larghezza_min_pt"] <= bw <= par["larghezza_max_pt"]
                and bh >= par["altezza_min_pt"] and fill >= par["riempimento_min"]):
            out.append([round(x0 / S_PIXEL), round(y0 / S_PIXEL), round((x1 + 1) / S_PIXEL), round((y1 + 1) / S_PIXEL)])
    return out


PAR_VERDE = {"larghezza_min_pt": 150, "larghezza_max_pt": 400, "altezza_min_pt": 40, "riempimento_min": 0.5, "area_min_pt2": 2500}


def _verde(r, g, b):
    return g >= 205 and g >= r + 6 and g >= b + 14 and r <= 240


def _colore_linea(page, R):
    """Colore medio dei pixel piu' scuri nella fascia di 4 pt lungo il bordo di R (la linea della cornice)."""
    pm = page.get_pixmap(matrix=pymupdf.Matrix(1, 1), clip=R, colorspace=pymupdf.csRGB, alpha=False)
    w, h, sm = pm.width, pm.height, pm.samples
    anello = []
    for y in range(h):
        for x in list(range(0, min(4, w))) + list(range(max(w - 4, 4), w)):
            i = (y * w + x) * 3
            anello.append((sm[i], sm[i + 1], sm[i + 2]))
    for x in range(4, w - 4):
        for y in list(range(0, min(4, h))) + list(range(max(h - 4, 4), h)):
            i = (y * w + x) * 3
            anello.append((sm[i], sm[i + 1], sm[i + 2]))
    if not anello:
        return None
    anello.sort(key=sum)
    scuri = anello[: max(10, len(anello) // 8)]
    return [st.mean(p[k] for p in scuri) for k in range(3)]


def trova_cornici(page, bl):
    """Note a margine con cornice ornamentale: immagine larga una colonna, chiara, con linea marrone (R-B >= 30),
    che contiene testo. Un'immagine puo' contenere piu' cornici impilate: si separano dai vuoti (> 24 pt) tra i blocchi di testo.
    Le schede dei mostri (linea grigia) e le righe di tabella (azzurre) sono escluse dal colore della linea."""
    out = []
    for im in page.get_image_info():
        x0, y0, x1, y1 = im["bbox"]
        w, h = x1 - x0, y1 - y0
        if not (200 <= w <= 270 and h >= 50) or x0 < 0:
            continue
        R = pymupdf.Rect(im["bbox"])
        dentro = [b for b in bl if b["l"] in TESTO and R.contains(pymupdf.Point((b["r"][0] + b["r"][2]) / 2, (b["r"][1] + b["r"][3]) / 2))]
        if not dentro:
            continue
        g = page.get_pixmap(matrix=pymupdf.Matrix(0.5, 0.5), clip=R, colorspace=pymupdf.csGRAY, alpha=False).samples
        if sum(1 for v in g if v > 150) / len(g) < 0.8:
            continue
        c = _colore_linea(page, R)
        if not c or c[0] - c[2] < 30:
            continue
        dentro.sort(key=lambda b: b["r"][1])
        gruppi = [[dentro[0]]]
        for b in dentro[1:]:
            if b["r"][1] - max(x["r"][3] for x in gruppi[-1]) > 24:
                gruppi.append([b])
            else:
                gruppi[-1].append(b)
        for gr in gruppi:
            r = [min(b["r"][0] for b in gr) - 14, min(b["r"][1] for b in gr) - 14, max(b["r"][2] for b in gr) + 14, max(b["r"][3] for b in gr) + 14]
            r = [max(r[0], x0), max(r[1], y0), min(r[2], x1), min(r[3], y1)]
            out.append({"rect": r, "blocchi": gr})
    return out


RE_SCHEDA = re.compile(r"Classe Armatura|Punti Ferita|\bCA\s*\d+\b.{0,40}\bIniziativa|\bPF\s*\d+\b", re.S)
PAR_SFONDO = {"distanza_min": 16, "luminosita_min": 150}


def _dentro(R, bl):
    return [b for b in bl if R.contains(pymupdf.Point((b["r"][0] + b["r"][2]) / 2, (b["r"][1] + b["r"][3]) / 2))]


RE_RIGA_TABELLA = re.compile(r"^\s*(d\d+\b|\d{1,3}\s*([\u2013\u2014-]\s*\d{1,3})?(\s|\.|$))", re.I)


def _ok_candidato(R, dentro, larg_pagina):
    """Filtri comuni a tutti i generatori: un box ha testo, non e' una tabella, non e' solo un titolo,
    non e' la scheda di un mostro, non e' largo quanto la pagina e il testo riempie buona parte dell'area."""
    testo = [b for b in dentro if b["l"] in TESTO]
    if not testo or any(b["l"] == "table" for b in dentro) or all(b["l"] in SOLO_TITOLO for b in dentro):
        return False
    if R.width > 0.8 * larg_pagina or R.height < 28 or R.width < 100:
        return False
    righe_num = sum(1 for b in testo if RE_RIGA_TABELLA.match(b["tx"]))
    if righe_num >= 2 and righe_num * 2 >= len(testo):
        return False  # tabella di tiri di dado / elenco numerato con righe colorate: non e' un box
    if RE_SCHEDA.search(" ".join(b["tx"] for b in testo)):
        return False  # scheda di mostro (stat block): non e' un box
    return sum((pymupdf.Rect(b["r"]) & R).get_area() for b in testo) / max(R.get_area(), 1) >= 0.35


def _soglia_sfondo(doc, pag):
    """Soglia di colore per il generatore 'sfondo diverso dalla carta'. Su una scansione con carta irregolare lo scarto tipico di un blocco
    dalla carta della sua pagina e' alto (un libro rumoroso ~12; libri puliti 0-7): li' la soglia sale a 45, sopra il rumore e sotto i box veri (57-73)."""
    ds = []
    for n in sorted(pag)[::max(1, len(pag) // 60)]:
        if n > len(doc):
            continue
        j = pag[n]
        pix = doc[n - 1].get_pixmap(matrix=pymupdf.Matrix(1, 1), colorspace=pymupdf.csRGB, alpha=False)
        cb = [bg_block(pix, b["block_bbox"], j["width"]) for b in j["parsing_res_list"] if b["block_label"] in TESTO and b.get("block_bbox")]
        cb = [c for c in cb if c]
        if cb:
            ref = [st.median(c[k] for c in cb) for k in range(3)]
            ds += [math.dist(c, ref) for c in cb]
    return PAR_SFONDO["distanza_min"] if not ds or st.median(ds) <= 10 else 45


def trova_box(libro, pag, catalogo):
    """Candidati box: area con testo e sfondo/cornice diversi dalla pagina. Piu' generatori (disegni vettoriali del catalogo,
    sfondo del blocco diverso dalla carta, pixel rosa/lilla e verdi, immagini con cornice ornamentale), stessi filtri.
    La decisione finale (e' un box / non lo e') spetta alla revisione umana."""
    vett = [v for v in catalogo["voci"] if v["indizio"] == "disegno_vettoriale"]
    doc = pymupdf.open(BASE + "pool/" + libro + ".pdf")
    box = []
    colori_schede = []
    soglia_sfondo = _soglia_sfondo(doc, pag)
    for n in sorted(pag):
        if n > len(doc):
            continue
        j = pag[n]
        p = doc[n - 1]
        s = p.rect.width / j["width"]
        bl = [{"i": i, "l": b["block_label"], "r": [v * s for v in b["block_bbox"]], "t": (b.get("block_content") or "").strip().replace("\n", " ")[:60],
               "tx": (b.get("block_content") or "")[:300]}
              for i, b in enumerate(j["parsing_res_list"]) if b.get("block_bbox")]
        cand = []  # (rect, voce), in ordine di precisione: il primo trovato tiene il rettangolo
        for dr in p.get_drawings():
            for v in vett:
                if match_vett(dr, v["parametri"]):
                    cand.append(([dr["rect"].x0, dr["rect"].y0, dr["rect"].x1, dr["rect"].y1], v["id"]))
        for c in trova_cornici(p, bl):
            cand.append((c["rect"], "immagine-cornice-ornamentale"))
        for r in trova_pixel(p):
            cand.append((r, "raster-riquadro-rosa-lilla"))
        for r in trova_pixel(p, PAR_VERDE, _verde):
            cand.append((r, "raster-riquadro-verde"))
        for z in j.get("dnb_zone", []):  # box del rilevatore di layout (passo rileva-zone dell'OCR): una fonte in piu', stessi filtri
            if z["label"] == "box":
                cand.append(([v * s for v in z["bbox"]], "rilevatore-layout"))
        schede = [pymupdf.Rect([v * s for v in z["bbox"]]) for z in j.get("dnb_zone", []) if z["label"] == "scheda"]
        pix = p.get_pixmap(matrix=pymupdf.Matrix(1, 1), colorspace=pymupdf.csRGB, alpha=False)
        cb = []
        for b in bl:
            if b["l"] in TESTO:
                c = bg_block(pix, [v / s for v in b["r"]], j["width"])
                if c:
                    b["c"] = c
                    cb.append(b)
        if cb:
            ref = [st.median(b["c"][k] for b in cb) for k in range(3)]
            fl = [b for b in cb if math.dist(b["c"], ref) > soglia_sfondo and sum(b["c"]) / 3 >= PAR_SFONDO["luminosita_min"]]
            fl.sort(key=lambda b: (b["r"][0] // 150, b["r"][1]))
            cl = []
            for b in fl:
                for g in cl:
                    gx0, gy0, gx1, gy1 = g["r"]
                    ov = min(gx1, b["r"][2]) - max(gx0, b["r"][0])
                    if ov >= 0.5 * min(gx1 - gx0, b["r"][2] - b["r"][0]) and b["r"][1] - gy1 <= 30 and b["r"][1] >= gy0 - 2:
                        g["r"] = [min(gx0, b["r"][0]), min(gy0, b["r"][1]), max(gx1, b["r"][2]), max(gy1, b["r"][3])]
                        break
                else:
                    cl.append({"r": list(b["r"])})
            for g in cl:
                r = g["r"]
                cand.append(([r[0] - 6, r[1] - 6, r[2] + 6, r[3] + 6], "sfondo-diverso-dalla-carta"))
        unici = []
        for r, voce in cand:
            R = pymupdf.Rect(r)
            for u in unici:
                inter = R & u["R"]
                if not inter.is_empty and inter.get_area() >= 0.6 * min(R.get_area(), u["R"].get_area()):
                    u["voci"].add(voce)
                    break
            else:
                unici.append({"R": R, "voci": {voce}})
        for u in unici:
            R = u["R"]
            if any((R & S).get_area() >= 0.5 * R.get_area() for S in schede):
                continue  # dentro una scheda di mostro trovata dal rilevatore: non e' un box
            dentro = _dentro(R, bl)
            if not _ok_candidato(R, dentro, p.rect.width):
                continue
            colori = [b["c"] for b in dentro if "c" in b]
            nuovo = {"pag": n, "rect": [round(x) for x in (R.x0, R.y0, R.x1, R.y1)], "voci": sorted(u["voci"]), "blocchi": [b["i"] for b in dentro],
                     "titoli": [b["t"] for b in dentro if b["l"] in ("paragraph_title", "doc_title")], "tipo": None}
            if colori:
                nuovo["_colore"] = [st.median(c[k] for c in colori) for k in range(3)]
            box.append(nuovo)
            # lo sfondo "generico" e' sospetto se i candidati coprono gran parte del testo della pagina (sfondo della pagina, non di un box)
        gen = [x for x in box if x["pag"] == n and x["voci"] == ["sfondo-diverso-dalla-carta"]]
        area_testo = sum(pymupdf.Rect(b["r"]).get_area() for b in bl if b["l"] in TESTO) or 1
        if gen and sum(pymupdf.Rect(x["rect"]).get_area() for x in gen) / area_testo > 0.6:
            box[:] = [x for x in box if not (x["pag"] == n and x["voci"] == ["sfondo-diverso-dalla-carta"])]
        for b in bl:  # colori di sfondo delle schede dei mostri in questo libro
            if "c" in b and b["l"] in TESTO and RE_SCHEDA.search(b["tx"]):
                colori_schede.append(b["c"])
    # scarta i candidati solo-sfondo con lo stesso colore delle schede (la scheda non e' un box)
    def _scheda(c):
        return any(math.dist(c, k) < 14 for k in colori_schede)
    box[:] = [x for x in box if not (x["voci"] == ["sfondo-diverso-dalla-carta"] and "_colore" in x and _scheda(x["_colore"]))]
    for x in box:
        x.pop("_colore", None)
    return box


# ---------- livello dei titoli dall'indice (segnalibri) del PDF ----------
def _norm_ix(t):
    return re.sub(r"[^a-z0-9à-ù]", "", (t or "").lower())


def _senza_accenti(t):
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFKD", t or "") if not unicodedata.combining(c))


def _nome_ix(t):
    """Nome di un mostro/PNG come lo scrive l'indice: senza 'Stat Block:' davanti e senza il tipo dopo il trattino ("Aboleth - Aberrazione Grande")."""
    t = re.sub(r"^\s*(stat\s*block|box)\s*[:\-\u2013]\s*", "", t or "", flags=re.I)
    return _norm_ix(_senza_accenti(re.split(r"\s+[-\u2013\u2014]\s+", t)[0]))


def _in_zona(pag_json, bbox):
    cx, cy = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2
    for z in pag_json.get("dnb_zone") or []:
        if z.get("label") in ("scheda", "box") and z["bbox"][0] <= cx <= z["bbox"][2] and z["bbox"][1] <= cy <= z["bbox"][3]:
            return True
    return False


def _stile_titolo(doc, b, cache):
    """(corpo arrotondato a 0,5 pt, font, colore, flags) del testo piu' lungo del titolo; None se nel PDF non c'e' testo."""
    n = b["pag"]
    if n not in cache:
        pg = doc[n - 1]
        cache[n] = (pg, pg.rect.width / b["W"], pg.rect.height / b["H"])
    pg, sx, sy = cache[n]
    r = pymupdf.Rect(b["bbox"][0] * sx - 1, b["bbox"][1] * sy - 1, b["bbox"][2] * sx + 1, b["bbox"][3] * sy + 1)
    try:
        sp = [s for bl in pg.get_text("dict", clip=r)["blocks"] if bl.get("type") == 0 for l in bl["lines"] for s in l["spans"] if s["text"].strip()]
    except Exception:
        return None
    if not sp:
        return None
    c = collections.Counter()
    for s in sp:
        c[(round(s["size"] * 2) / 2, s["font"], s["color"], s["flags"])] += len(s["text"])
    return c.most_common(1)[0][0]


def _linea_sotto(doc, T, zoom=3):
    """True se sotto il titolo c'e' una sottile linea orizzontale (immagine) larga come il titolo: banda di righe di pixel scure
    (>=35 grigi sotto la carta su >=90% della larghezza), spessa al massimo 2,5 pt, isolata sopra e sotto."""
    import numpy as np
    res, cache = [], {}
    for b in T:
        n = b["pag"]
        if n not in cache:
            cache.clear()
            pg = doc[n - 1]
            pm = pg.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), colorspace=pymupdf.csGRAY)
            cache[n] = (np.frombuffer(pm.samples, dtype=np.uint8).reshape(pm.h, pm.w).astype(np.int16), pg.rect.width / b["W"], pg.rect.height / b["H"])
        a, sx, sy = cache[n]
        h = a.shape[0]
        x0, x1, y1 = int(b["bbox"][0] * sx * zoom), int(b["bbox"][2] * sx * zoom), int(b["bbox"][3] * sy * zoom)
        if x1 - x0 < 20:
            res.append(0.0)
            continue
        ya, yb = max(0, y1 - zoom), min(h, y1 + 14 * zoom)
        reg = a[ya:yb, x0:x1]
        if reg.shape[0] < 6:
            res.append(0.0)
            continue
        fondo = np.percentile(a[max(0, y1 - 30 * zoom):yb, x0:x1], 90)
        cop = ((fondo - reg) >= 35).mean(axis=1)
        trov, i, n_, mx = False, 0, len(cop), int(2.5 * zoom)
        while i < n_:
            if cop[i] >= 0.9:
                j = i
                while j + 1 < n_ and cop[j + 1] >= 0.9:
                    j += 1
                prima = cop[max(0, i - 4):i].max() if i > 0 else 0.0
                dopo = cop[j + 1:j + 5].max() if j + 1 < n_ else 0.0
                if (j - i + 1) <= mx and prima < 0.75 and dopo < 0.75:
                    trov = True
                    break
                i = j + 1
            else:
                i += 1
        res.append(float(trov))
    return res


def _caratteristiche_livello(T, S, lin):
    import numpy as np
    hs = [(b["bbox"][3] - b["bbox"][1]) / b["H"] / (b["t"].count("\n") + 1) for b in T]
    hm = st.median(hs)
    sizes = [s[0] for s in S if s]
    sm = st.median(sizes)
    fc = collections.Counter(s[1] for s in S if s)
    cc = collections.Counter(s[2] for s in S if s)
    rip = collections.Counter(b["t"].lower() for b in T)
    nan = float("nan")
    X = []
    for k, b in enumerate(T):
        t, s, bb = b["t"], S[k], b["bbox"]
        letters = [c for c in t if c.isalpha()]
        col = s[2] if s else 0
        X.append([s[0] if s else nan, (s[0] / sm) if s else nan, fc[s[1]] / len(sizes) if s else nan, cc[s[2]] / len(sizes) if s else nan,
                  ((col >> 16) & 255) if s else nan, ((col >> 8) & 255) if s else nan, (col & 255) if s else nan,
                  float(bool(s[3] & 16) or ("bold" in s[1].lower())) if s else nan, float(bool(s[3] & 2)) if s else nan,
                  hs[k] / hm, (sum(c.isupper() for c in letters) / len(letters)) if letters else 0.0, len(t), len(t.split()), t.count("\n") + 1,
                  (bb[2] - bb[0]) / b["W"], abs((bb[0] + bb[2]) / 2 / b["W"] - 0.5), bb[0] / b["W"], bb[1] / b["H"], b["pag"] / b["npag"],
                  float(bool(re.match(r"^\s*\d+[A-Za-z]?[.)]?\s", t))), float(t.rstrip().endswith(":")), float(t.rstrip().endswith(".")),
                  rip[t.lower()] / len(T), b["prima_tit"], b["dopo_tit"], b["prima_none"],
                  float(b["label"] == "doc_title"), float(b["label"] == "figure_title"), lin[k]])
    X = np.array(X, dtype=float)
    sz = X[:, 0]
    prev, nxt = np.r_[np.nan, sz[:-1]], np.r_[sz[1:], np.nan]
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.c_[X, prev, nxt, sz / prev, sz / nxt]


def titoli_in_schede(doc, pag, prop):
    """Nei PDF con testo nativo i titoli dentro una scheda (zona 'scheda' di dnb_zone: "Azioni", "Reazioni", il nome in testa alla scheda...)
    non sono titoli di capitolo. Misurato sulle decisioni umane dei libri con testo: 150 su 152 non erano titoli. Nelle scansioni
    le zone di scheda (YOLO) sono inaffidabili (in un libro: 78 su 101 erano titoli veri): li' la regola non si applica."""
    n = len(doc)
    campione = list(range(0, n, max(1, n // 20)))
    if sum(1 for i in campione if len(doc[i].get_text().strip()) > 50) < 0.5 * len(campione):
        return {"usato": False, "motivo": "PDF senza testo nativo (scansione)"}
    cambiati = 0
    for p in prop:
        if p["proposta"] != "titolo" or p.get("idx") is None or not p.get("bbox"):
            continue
        cx, cy = (p["bbox"][0] + p["bbox"][2]) / 2, (p["bbox"][1] + p["bbox"][3]) / 2
        for z in pag[p["pag"]].get("dnb_zone") or []:
            if z.get("label") == "scheda" and z["bbox"][0] <= cx <= z["bbox"][2] and z["bbox"][1] <= cy <= z["bbox"][3]:
                p["proposta"], p["motivo"], p["livello_proposto"] = "non_titolo", "dentro una scheda (zona rilevata)", None
                cambiati += 1
                break
    return {"usato": True, "titoli_in_schede": cambiati}


def livelli_da_indice(doc, pag, prop, minimo_abbinati=40):
    """Se il PDF ha un indice (segnalibri) con piu' livelli, il livello dei titoli ne viene proposto cosi':
    - i titoli che corrispondono a una voce dell'indice prendono il livello dell'indice;
    - gli altri lo prendono da un classificatore addestrato su QUEL libro con le voci abbinate (corpo, font, colore, grassetto,
      maiuscole, posizione, titoli vicini, linea sotto il titolo). I titoli dentro schede e box non servono ne' vengono toccati.
    Cambia solo `livello_proposto` dei titoli gia' proposti come 'titolo'. Senza indice utilizzabile non cambia nulla."""
    import difflib
    import numpy as np
    from sklearn.ensemble import HistGradientBoostingClassifier as HGB
    toc = doc.get_toc()
    if len(toc) < 30 or len({l for l, _, _ in toc}) < 2:
        return {"usato": False, "motivo": "il PDF non ha un indice utilizzabile (%d voci)" % len(toc)}
    npag = len(pag)
    cache_lab = {}
    T = []
    for k, p in enumerate(prop):
        if p.get("idx") is None or p["label"] not in TIT or not p.get("bbox"):
            continue
        n = p["pag"]
        if n not in cache_lab:
            L = pag[n]["parsing_res_list"]
            o = sorted(range(len(L)), key=lambda i: (L[i].get("block_order") if L[i].get("block_order") is not None else 10 ** 6 + i))
            cache_lab[n] = [L[i]["block_label"] for i in o]
        labs, pos = cache_lab[n], p["pos"]
        T.append({"k": k, "pag": n, "npag": npag, "bbox": p["bbox"], "W": pag[n]["width"], "H": pag[n]["height"], "t": p["testo"], "label": p["label"],
                  "zona": bool(p.get("in_box")) or _in_zona(pag[n], p["bbox"]),
                  "prima_tit": float(pos > 0 and labs[pos - 1] in TIT), "dopo_tit": float(pos + 1 < len(labs) and labs[pos + 1] in TIT), "prima_none": float(pos == 0)})
    if len(T) < minimo_abbinati:
        return {"usato": False, "motivo": "troppo pochi titoli (%d)" % len(T)}
    # abbinamento voci dell'indice -> titoli (stessa pagina, testo uguale o simile >= 0,85), solo fuori da schede e box
    per = collections.defaultdict(list)
    for i, b in enumerate(T):
        if not b["zona"]:
            per[b["pag"]].append(i)
    abb = {}
    for lv, titolo, p in toc:
        nt = _norm_ix(titolo)
        if len(nt) < 3:
            continue
        best = (0.0, None)
        for i in per.get(p, []):
            nb = _norm_ix(T[i]["t"])
            r = 1.0 if nb == nt else difflib.SequenceMatcher(None, nt, nb).ratio()
            if r > best[0]:
                best = (r, i)
        if best[0] >= 0.85 and best[1] not in abb:
            abb[best[1]] = lv
    # secondo passaggio: l'indice scrive i mostri "Aboleth - Aberrazione Grande" / "Stat Block: Aboleth"; si confronta il solo nome
    # (stessa pagina, senza accenti), solo per i titoli ancora non abbinati e prima le voci che non sono 'Stat Block'
    for lv, titolo, p in sorted(toc, key=lambda x: x[1].lower().lstrip().startswith("stat")):
        nt2 = _nome_ix(titolo)
        if len(nt2) < 4:
            continue
        for i in per.get(p, []):
            if i not in abb and _nome_ix(T[i]["t"].split("\n")[0]) == nt2:
                abb[i] = lv
                break
    cache = {}
    S = [_stile_titolo(doc, b, cache) for b in T]
    idx = [i for i in abb if S[i]]
    if len(idx) < minimo_abbinati or len({abb[i] for i in idx}) < 2:
        return {"usato": False, "motivo": "voci dell'indice abbinate troppo poche (%d)" % len(idx), "voci_indice": len(toc)}
    lin = _linea_sotto(doc, T)
    X = _caratteristiche_livello(T, S, lin)
    y = np.zeros(len(T), dtype=int)
    for i in idx:
        y[i] = abb[i]
    mk = lambda: HGB(max_iter=200, learning_rate=0.08, random_state=0)
    acc = None
    if len(idx) >= 100:  # qualita' fuori campione: 5 gruppi a rotazione sulle voci abbinate
        arr, ok = np.array(idx), 0
        for g in range(5):
            te = arr[g::5]
            tr = np.setdiff1d(arr, te)
            ok += int((mk().fit(X[tr], y[tr]).predict(X[te]) == y[te]).sum())
        acc = round(ok / len(arr), 3)
    mod = mk().fit(X[idx], y[idx])
    n_idx = n_mod = n_skip = n_diff = 0
    distr = collections.Counter()
    for i, b in enumerate(T):
        p = prop[b["k"]]
        if b["zona"] or p["proposta"] != "titolo":
            n_skip += 1
            continue
        if i in abb:  # abbinato all'indice: vale il suo livello anche se nel PDF non c'e' testo leggibile (scritte grafiche)
            nuovo, fonte = abb[i], "indice"
            n_idx += 1
        elif S[i]:
            nuovo, fonte = int(mod.predict(X[[i]])[0]), "stile"
            n_mod += 1
        else:
            n_skip += 1
            continue
        if p.get("livello_proposto") != nuovo:
            n_diff += 1
            p["livello_regola_vecchia"] = p.get("livello_proposto")
        p["livello_proposto"], p["livello_fonte"] = nuovo, fonte
        distr[nuovo] += 1
    n_testatine = 0  # le testatine di pagina trasformate in titoli (voci sintetiche): se l'indice le conosce, vale il suo livello
    for p in prop:
        if p.get("sintetico") and p["proposta"] == "titolo":
            nt = _norm_ix(p["testo"])
            for lv, titolo, pgn in toc:
                if pgn == p["pag"] and len(nt) >= 3 and difflib.SequenceMatcher(None, nt, _norm_ix(titolo)).ratio() >= 0.85:
                    p["livello_regola_vecchia"] = p.get("livello_proposto")
                    p["livello_proposto"], p["livello_fonte"] = lv, "indice"
                    n_testatine += 1
                    break
    return {"usato": True, "voci_indice": len(toc), "abbinate": len(abb), "addestramento": len(idx), "accordo_fuori_campione": acc,
            "testatine_dall_indice": n_testatine, "titoli_dall_indice": n_idx, "titoli_dallo_stile": n_mod, "non_toccati": n_skip, "livello_cambiato_rispetto_alla_regola_vecchia": n_diff,
            "distribuzione_livelli": dict(sorted(distr.items()))}


def titoli_ripetuti(prop):
    """Un titolo di livello 1-2 che torna entro 6 pagine con lo stesso testo (senza maiuscole e punteggiatura) scende di un livello rispetto
    al primo, se non e' gia' confermato dall'indice del PDF (scritta grafica o testatina): resta un titolo ma non e' piu' un capitolo.
    Segue le decisioni umane (due libri: capitolo L1 e sezione omonima L2; L2 e L3). I titoli confermati dall'indice e le pagine
    1-3 (copertina e frontespizio) non si toccano."""
    T = [p for p in prop if p["proposta"] == "titolo" and p.get("idx") is not None or p.get("sintetico") and p["proposta"] == "titolo"]
    n = 0
    for i, p in enumerate(T):
        if p.get("livello_fonte") == "indice" or (p.get("livello_proposto") or 9) > 2:
            continue
        k = _norm_ix(p["testo"])
        if len(k) < 4:
            continue
        for q in T[:i]:
            if q["pag"] <= 3 or p["pag"] - q["pag"] > 6 or (q.get("livello_proposto") or 9) > 2 or _norm_ix(q["testo"]) != k:
                continue
            if p["livello_proposto"] <= q["livello_proposto"]:
                p["livello_regola_vecchia"] = p.get("livello_regola_vecchia", p["livello_proposto"])
                p["livello_proposto"] = min(5, q["livello_proposto"] + 1)
                p["livello_fonte"] = "ripetizione"
                n += 1
            break
    return n


def etichette_di_immagini(pag, prop, con_indice=False):
    """Un titolo ben definito resta titolo; le etichette no. Si applica ai titoli NON confermati dall'indice del PDF:
    - dentro la stessa immagine ci sono 2 o piu' titoli: sono etichette di quell'immagine (tue decisioni: 7 su 7 'immagine');
    - nei libri con indice, una scritta dentro un'immagine che l'indice non conferma non e' un titolo ben definito (tue decisioni: 11 su 11;
      quelle confermate dall'indice erano titoli 21 su 21);
    - pagina senza testo corrente (< 150 caratteri) con 3 o piu' titoli: etichette di mappa o righe di tabella (tue decisioni: 34 su 34),
      ma solo le etichette piccole: un titolo grande (livello 1 per grandezza) e' il titolo ben definito della pagina e resta.
    Un titolo da solo nella sua immagine, o da solo su una pagina di mappa, resta titolo (tue decisioni: 26 su 37 e 20 su 29)."""
    IMG = ("image", "header_image", "footer_image", "chart", "seal")
    TESTO_ = ("text", "list", "aside_text", "vision_footnote")
    info = {}
    for n, d in pag.items():
        L = d["parsing_res_list"]
        imgs = [b["block_bbox"] for b in L if b["block_label"] in IMG and b.get("block_bbox")]
        info[n] = {"imgs": imgs, "corpo": sum(len(b.get("block_content") or "") for b in L if b["block_label"] in TESTO_),
                   "ntit": sum(1 for b in L if b["block_label"] in TIT)}
    def immagine_di(n, bb):
        area = (bb[2] - bb[0]) * (bb[3] - bb[1]) or 1
        best = (0.0, None)
        for k, q in enumerate(info[n]["imgs"]):
            w, h = min(bb[2], q[2]) - max(bb[0], q[0]), min(bb[3], q[3]) - max(bb[1], q[1])
            if w > 0 and h > 0 and w * h / area > best[0]:
                best = (w * h / area, k)
        return best[1] if best[0] >= 0.6 else None
    per_img = collections.Counter()
    for p in prop:
        if p.get("idx") is not None and p["label"] in TIT and p.get("bbox"):
            k = immagine_di(p["pag"], p["bbox"])
            p["_img"] = (p["pag"], k) if k is not None else None
            if k is not None:
                per_img[p["_img"]] += 1
    n_img = n_mappa = 0
    for p in prop:
        img = p.pop("_img", None)
        if p["proposta"] != "titolo" or p.get("idx") is None or p.get("livello_fonte") == "indice":
            continue
        if img is not None and (per_img[img] >= 2 or con_indice):
            p["proposta"], p["livello_proposto"] = "non_titolo", None
            p["motivo"] = "etichetta dentro un'immagine (piu' titoli nella stessa immagine)" if per_img[img] >= 2 else "scritta dentro un'immagine non confermata dall'indice"
            n_img += 1
        elif info[p["pag"]]["corpo"] < 150 and info[p["pag"]]["ntit"] >= 3 and p.get("livello_proposto") != 1:  # i titoli grandi (livello 1 per grandezza) restano
            p["proposta"], p["motivo"], p["livello_proposto"] = "non_titolo", "etichetta su pagina senza testo (mappa o tabella)", None
            n_mappa += 1
        if p["proposta"] == "non_titolo":
            p.pop("livello_fonte", None)
            p.pop("livello_regola_vecchia", None)
    return {"in_immagini": n_img, "su_pagine_di_mappa": n_mappa}


def nomi_delle_schede(doc, pag, prop):
    """Il nome in cima a una scheda (mostro, PNG) e' l'intestazione della scheda, anche se sta dentro la zona 'scheda' (le altre voci
    della scheda - Azioni, Reazioni... - restano non titoli). Livello: quello dell'indice del PDF per quel nome (pagina +-1, prima le voci
    non 'Stat Block'; nomi uguali o uno l'inizio dell'altro, senza accenti); se l'indice non conferma il nome, niente intestazione
    ("titolo ben definito, altrimenti niente"). Se lo stesso nome e' gia' un titolo vero entro 1 pagina non si duplica.
    Solo zone con nome letto dal testo (fonte 'testo') e primo pezzo della scheda."""
    toc = doc.get_toc()
    ix = collections.defaultdict(list)
    for lv, t, pg in toc:
        k = _nome_ix(t)
        if k:
            ix[k].append((lv, pg, t.lower().lstrip().startswith("stat")))
    gia = collections.defaultdict(list)
    for p in prop:
        if p["proposta"] == "titolo" and p.get("idx") is not None:
            gia[_norm_ix(_senza_accenti((p["testo"] or "").split("\n")[0]))].append(p["pag"])
    per_blocco = {(p["pag"], p["idx"]): p for p in prop if p.get("idx") is not None}
    cand = []
    for n in sorted(pag):
        d = pag[n]
        L = d["parsing_res_list"]
        ordine = sorted(range(len(L)), key=lambda i: (L[i].get("block_order") if L[i].get("block_order") is not None else 10 ** 6 + i))
        for z in d.get("dnb_zone") or []:
            if z.get("label") != "scheda" or z.get("fonte") != "testo" or not z.get("nome") or z.get("pezzo") not in (1, None):
                continue
            k = _norm_ix(_senza_accenti(z["nome"]))
            if len(k) < 3:
                continue
            blocco = None
            for pos, i in enumerate(ordine):
                b = L[i]
                bb = b.get("block_bbox")
                if not bb or b["block_label"] in ("image", "header_image", "footer_image"):
                    continue
                cx, cy = (bb[0] + bb[2]) / 2, (bb[1] + bb[3]) / 2
                if not (z["bbox"][0] <= cx <= z["bbox"][2] and z["bbox"][1] <= cy <= z["bbox"][3]):
                    continue
                prima = _norm_ix(_senza_accenti((b.get("block_content") or "").strip().split("\n")[0]))
                if prima == k or prima.startswith(k):
                    blocco = (i, pos)
                    break
            if blocco is None:
                continue
            c = [x for x in ix.get(k, []) if abs(x[1] - n) <= 1]
            if not c and len(k) >= 8:  # nome della scheda troncato dall'OCR ("Quaggoth servitore delle"): e' l'inizio del nome nell'indice
                c = [x for kk, voci in ix.items() if kk.startswith(k) for x in voci if abs(x[1] - n) <= 1]
            c.sort(key=lambda x: (x[2], abs(x[1] - n), x[0]))
            cand.append({"pag": n, "k": k, "blocco": blocco, "livello": c[0][0] if c else None})
    st_ = collections.Counter()
    for c in cand:
        if any(abs(pg - c["pag"]) <= 1 for pg in gia.get(c["k"], [])):
            st_["gia_titolo_vicino"] += 1
            continue
        liv, fonte = c["livello"], "indice_scheda"
        if liv is None:  # l'indice non conferma il nome: niente intestazione
            st_["non_nell_indice"] += 1
            continue
        i, pos = c["blocco"]
        n = c["pag"]
        d = pag[n]
        b = d["parsing_res_list"][i]
        p = per_blocco.get((n, i))
        if p is None:
            bb = b["block_bbox"]
            p = {"pag": n, "pos": pos, "idx": i, "label": b["block_label"], "testo": (b.get("block_content") or "").strip(), "bbox": bb,
                 "altezza_mille": round((bb[3] - bb[1]) * 1000 / d["height"], 1), "proposta": "titolo", "motivo": "", "livello_proposto": None,
                 "prima": None, "dopo": None, "in_box": None}
            prop.append(p)
            per_blocco[(n, i)] = p
            st_["blocco_nuovo"] += 1
        p["proposta"], p["motivo"], p["livello_proposto"], p["livello_fonte"] = "titolo", "nome della scheda", liv, fonte
        p.pop("livello_regola_vecchia", None)
        st_[fonte] += 1
    return {"schede": len(cand), **dict(st_)}


def main(progetto: str, cartella_output: str = "", resume_url: str = ""):
    pag = leggi_pagine(progetto)
    if not pag:
        raise Exception("Nessun JSON OCR in " + BASE + progetto + "/json")
    catalogo = json.load(open(BASE + "tools/catalogo_box.json"))
    prop = proposte(pag)
    sint, n_inizi, n_rip = intestazioni_correnti(pag, prop)
    prop = sorted(prop + sint, key=lambda p: (p["pag"], p["pos"]))
    box = trova_box(progetto, pag, catalogo)
    # titoli dentro un box
    n_in = 0
    for t in prop:
        t["in_box"] = None
    scala = {}
    doc = pymupdf.open(BASE + "pool/" + progetto + ".pdf")
    for t in prop:
        if not t["bbox"]:
            continue
        sc = scala.setdefault(t["pag"], doc[t["pag"] - 1].rect.width / pag[t["pag"]]["width"])
        cx, cy = (t["bbox"][0] + t["bbox"][2]) / 2 * sc, (t["bbox"][1] + t["bbox"][3]) / 2 * sc
        for b in box:
            R = b["rect"]
            if b["pag"] == t["pag"] and R[0] <= cx <= R[2] and R[1] <= cy <= R[3]:
                t["in_box"] = {"pag": b["pag"], "rect": R, "voci": b["voci"]}
                n_in += 1
                break
    try:  # titoli dentro le schede: non sono titoli (solo PDF con testo nativo)
        schede_info = titoli_in_schede(doc, pag, prop)
    except Exception as e:
        schede_info = {"usato": False, "errore": str(e)[:200]}
    try:  # livello dei titoli dall'indice del PDF (se c'e'); mai un motivo per bloccare la preparazione
        liv_info = livelli_da_indice(doc, pag, prop)
    except Exception as e:
        liv_info = {"usato": False, "errore": str(e)[:200]}
    try:  # ripetizioni di titoli gia' confermati dall'indice
        rip_info = titoli_ripetuti(prop)
    except Exception as e:
        rip_info = {"errore": str(e)[:200]}
    try:  # etichette dentro le immagini o su pagine di mappa (dopo l'indice: un titolo confermato dall'indice resta)
        img_info = etichette_di_immagini(pag, prop, con_indice=bool(liv_info.get("usato")))
    except Exception as e:
        img_info = {"errore": str(e)[:200]}
    try:  # il nome in cima alle schede e' l'intestazione della scheda (dopo le altre regole, che non devono disfarlo)
        nomi_info = nomi_delle_schede(doc, pag, prop)
    except Exception as e:
        nomi_info = {"errore": str(e)[:200]}
    # i box candidati diventano voci della revisione (label "box"), subito prima del loro primo blocco: l'umano decide "e' un box" / "non lo e'"
    for b in box:
        L = pag[b["pag"]]["parsing_res_list"]
        ordine = sorted(range(len(L)), key=lambda i: (L[i].get("block_order") if L[i].get("block_order") is not None else 10 ** 6 + i))
        rango = {i: k for k, i in enumerate(ordine)}
        primo = min(rango[i] for i in b["blocchi"])
        testo = " ".join((L[i].get("block_content") or "").strip().replace("\n", " ") for i in sorted(b["blocchi"], key=lambda i: rango[i])[:2])[:140]
        prop.append({"pag": b["pag"], "pos": primo - 0.5, "idx": None, "label": "box", "testo": testo, "bbox": None, "altezza_mille": 0,
                     "proposta": "box_senza_titolo", "motivo": "candidato: " + ", ".join(b["voci"]), "livello_proposto": None,
                     "prima": None, "dopo": None, "in_box": None, "rect": b["rect"], "blocchi": b["blocchi"]})
    prop = sorted(prop, key=lambda p: (p["pag"], p["pos"]))
    d = (cartella_output or "/shared/projects").rstrip("/") + "/" + progetto + "/revisione"
    os.makedirs(d, exist_ok=True)
    json.dump(prop, open(d + "/proposte.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    json.dump(box, open(d + "/box.json", "w", encoding="utf-8"), ensure_ascii=False)
    for f in ("decisioni.jsonl", "finale.json"):  # una nuova preparazione riparte da zero, ma tiene la storia
        if os.path.exists(d + "/" + f):
            os.rename(d + "/" + f, d + "/" + f + ".prima_di_ripreparare")
    json.dump({"stato": "in_revisione", "resume_url": resume_url or None}, open(d + "/stato.json", "w"))
    return {"cartella": d, "pagine": len(pag), "titoli": len(prop), "per_classe": dict(collections.Counter(t["proposta"] for t in prop)), "candidati_box": len(box),
            "box": len(box), "titoli_in_box": n_in,
            "livelli_da_indice": liv_info,
            "titoli_in_schede": schede_info,
            "etichette_immagini": img_info,
            "nomi_schede": nomi_info,
            "titoli_ripetuti": rip_info,
            "sezioni_da_intestazione": {"inizi": n_inizi, "ripetizioni": n_rip, "sintetiche": len(sint)}}
