# requirements:
# pymupdf
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


def solo_numeri(t):
    t = t.lower()
    # solo numeri o una notazione di dadi ("2d6", "d20+3"); NON i codici di area come "D3: Tana dei lupi"
    return bool(re.fullmatch(r"[\d\s.,:;/+\-–]*", t)) or bool(re.fullmatch(r"\d*d\d+\s*([+\-–]\s*\d+)?", t.strip()))


def leggi_pagine(libro):
    pag = {}
    for f in glob.glob(BASE + libro + "/json/pag_*_res.json"):
        pag[int(re.search(r"pag_(\d+)_res", f).group(1))] = json.load(open(f))
    return pag


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


def trova_box(libro, pag, catalogo):
    """Candidati box: area con testo e sfondo/cornice diversi dalla pagina. Piu' generatori (disegni vettoriali del catalogo,
    sfondo del blocco diverso dalla carta, pixel rosa/lilla e verdi, immagini con cornice ornamentale), stessi filtri.
    La decisione finale (e' un box / non lo e') spetta alla revisione umana."""
    vett = [v for v in catalogo["voci"] if v["indizio"] == "disegno_vettoriale"]
    doc = pymupdf.open(BASE + "pool/" + libro + ".pdf")
    box = []
    colori_schede = []
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
            fl = [b for b in cb if math.dist(b["c"], ref) > PAR_SFONDO["distanza_min"] and sum(b["c"]) / 3 >= PAR_SFONDO["luminosita_min"]]
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
            "sezioni_da_intestazione": {"inizi": n_inizi, "ripetizioni": n_rip, "sintetiche": len(sint)}}
