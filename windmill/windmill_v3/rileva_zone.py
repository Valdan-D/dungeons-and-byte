# requirements:
# pymupdf==1.28.0
"""Rileva box e schede dei mostri su ogni pagina e li scrive nel json OCR (chiave "dnb_zone", coordinate in pixel come i blocchi PaddleOCR).
Schede: dal testo OCR (anche senza cornice; i pezzi su piu' colonne/pagine portano lo stesso "mostro").
Box (e schede che il testo non vede): rilevatore YOLO, servizio sulla LXC 121 (/opt/dnb-layout). Senza applica=True non scrive nulla."""
import pymupdf
import base64
import glob
import json
import os
import re
import shutil
import urllib.request

BASE = "/shared/projects/"
SERVIZIO = "http://192.168.178.121:8097/zone"
LATO = 1280
# un "box" con Classe Armatura e Punti Ferita nel testo e' una scheda mostro (foto di libri: schede ombreggiate come i box)
CA = re.compile(r"Classe Armatura|\bCA\b")
PF = re.compile(r"Punti Ferita|\bPF\b")
# riga del tipo: breve, taglia fra le prime parole, poi una virgola (l'allineamento l'OCR a volte lo storpia)
TAGLIA = re.compile(r"^[^\n,.]{0,30}\b(minuscol[oa]|piccol[oa]|medi[oa]|grande|enorme|mastodontic[oa])\b[^\n.]{0,40},[^\n.]{1,40}$", re.I)
NOME_TIPO = re.compile(r"^([A-ZÀ-Ý][A-ZÀ-Ý' -]{2,40}?)\s+(\S.*)$")  # "ARPIA Mostruosità Media, ..." su una riga
STAT = re.compile(r"Class\w? Armatura|Punti Ferita", re.I)
STAT_IN_TESTA = re.compile(r"^\s*Class\w? Armatura\b", re.I)
FORDES = re.compile(r"\bFOR\b.{0,30}\bDES\b.{0,30}\bCOS\b", re.S)
INDICE = re.compile(r",\s*\d+([-–]\d+)?\s*$")  # voce dell'indice analitico: "Combattere alla cieca (talento), 209"
FOGLIO = re.compile(r"\bpagina \d+ di \d+\b", re.I)  # piede delle pagine da fotocopiare (fogli del personaggio): niente box
SOTTO = re.compile(r"^\s*(azioni|reazioni|azioni leggendarie|azioni bonus)\s*$", re.I)
TITOLI = ("paragraph_title", "doc_title", "header")
SALTA = ("number", "footer", "footer_image", "header_image", "image", "figure_title", "seal")




def testo_dentro(d, bb):
    out = []
    for b in d.get("parsing_res_list", []):
        x0, y0, x1, y1 = b["block_bbox"]
        if bb[0] <= (x0 + x1) / 2 <= bb[2] and bb[1] <= (y0 + y1) / 2 <= bb[3]:
            out.append(b.get("block_content") or "")
    return " ".join(out)


def righe(t):
    t = re.sub(r"<[^>]+>", "\n", (t or "").replace("\\n", "\n"))
    return "\n".join(r.strip() for r in t.split("\n") if r.strip())


def blocchi(f, n):
    J = json.load(open(f, encoding="utf-8"))
    meta = J["width"] / 2
    out = []
    for b in J["parsing_res_list"]:
        if b["block_label"] in SALTA:
            continue
        x0, y0, x1, y1 = b["block_bbox"]
        col = 0 if (x0 + x1) / 2 < meta else 1
        out.append({"pag": n, "col": col, "y": y0, "label": b["block_label"], "t": righe(b["block_content"]),
                    "rect": [x0, y0, x1, y1]})
    return sorted(out, key=lambda b: (b["col"], b["y"]))


def nome_tipo(t):
    """(nome, True) se il blocco inizia con nome del mostro + riga del tipo, su due righe o su una."""
    r = t.split("\n")
    if not r[0] or len(r[0]) > 45 or TAGLIA.match(r[0]) or SOTTO.match(r[0]):
        m = NOME_TIPO.match(r[0]) if r[0] else None
        return (m.group(1), True) if m and TAGLIA.match(m.group(2)) else (None, False)
    if len(r) > 1 and TAGLIA.match(r[1]):
        return r[0], True
    m = NOME_TIPO.match(r[0])
    return (m.group(1), True) if m and TAGLIA.match(m.group(2)) else (r[0], False)


def nome_buono(nome):
    """niente voci di glossario/indice ("capra (scheda delle", "passo velato (incantesimo),": in minuscolo) ne' sigle come "BC" """
    return bool(nome) and not (re.search(r"[(),]", nome) and nome != nome.upper()) and len(re.sub(r"[^A-Za-zÀ-ÿ]", "", nome)) >= 3


def inizio(bs, i):
    b = bs[i]; nome, ok = nome_tipo(b["t"])
    if ok:
        return nome if nome_buono(nome) else None
    if not nome_buono(nome) or INDICE.search(b["t"]) or FORDES.match(nome) or STAT.search(nome) or re.search(r"\d", nome):
        return None
    if b["label"] == "table" and STAT.search(b["t"][:250]):  # scheda letta come una tabella sola, nome in testa
        return nome if nome == nome.upper() else None  # in minuscolo e' l'intestazione di una tabella ("Classe", "Armature")
    for c in bs[i + 1:i + 3]:  # titolo seguito subito dalla riga del tipo o dalle statistiche (foto: tipo illeggibile)
        if c["pag"] != b["pag"] or c["col"] != b["col"]:
            break
        if TAGLIA.match(c["t"].split("\n")[0]) or STAT_IN_TESTA.match(c["t"]) or FORDES.match(c["t"]):
            return nome if b["label"] in TITOLI or len(b["t"]) <= 45 else None
        if len(c["t"]) > 120:
            break
    return None


def mostri(file_json):
    """Schede dei mostri dal testo OCR, lette colonna per colonna e pagina per pagina: [{nome, pezzi: [(pag, rect pixel)]}]."""
    bs = [b for n in sorted(file_json) for b in blocchi(file_json[n], n)]
    M, cur = [], None
    for i, b in enumerate(bs):
        nome = inizio(bs, i)
        if not nome and b["label"] == "table" and FORDES.search(b["t"]) and (cur is None or cur["fordes"]):
            nome = "(senza nome)"  # seconda tabella delle caratteristiche: il titolo l'OCR l'ha perso
        if nome:
            if cur and not cur["stat"]:  # era il titolo della descrizione, non una scheda
                M.remove(cur)
            cur = {"nome": nome, "blocchi": [b], "fordes": bool(FORDES.search(b["t"])),
                   "stat": bool(FORDES.search(b["t"]) or STAT.search(b["t"]))}; M.append(cur)
        elif cur and b["label"] in TITOLI and not SOTTO.match(b["t"]):
            if not cur["stat"]:
                M.remove(cur)
            cur = None
        elif cur:
            cur["blocchi"].append(b); cur["fordes"] |= bool(FORDES.search(b["t"]))
            cur["stat"] |= bool(FORDES.search(b["t"]) or STAT.search(b["t"]))
    if cur and not cur["stat"]:
        M.remove(cur)
    for m in M:  # pezzi: un rettangolo per pagina e colonna
        P = {}
        for b in m["blocchi"]:
            r = P.setdefault((b["pag"], b["col"]), list(b["rect"]))
            r[:] = [min(r[0], b["rect"][0]), min(r[1], b["rect"][1]), max(r[2], b["rect"][2]), max(r[3], b["rect"][3])]
        m["pezzi"] = [(k[0], [round(v) for v in r]) for k, r in sorted(P.items())]
        del m["blocchi"], m["fordes"], m["stat"]
    return M


def coperto(q, r):
    """frazione di q dentro r"""
    i = max(0, min(q[2], r[2]) - max(q[0], r[0])) * max(0, min(q[3], r[3]) - max(q[1], r[1]))
    return i / max(1e-9, (q[2] - q[0]) * (q[3] - q[1]))


def iou(a, b):
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0])); iy = max(0, min(a[3], b[3]) - max(a[1], b[1])); i = ix * iy
    return i / ((a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - i + 1e-9)


def box_vettoriali(pg, d):
    """Riquadri colorati disegnati nel PDF (PDF digitali): rettangolo pieno di colore diverso dalla pagina, con del testo
    (non per lo piu' tabella) dentro. Coordinate in pixel del json OCR."""
    W, H = pg.rect.width, pg.rect.height
    s = d["width"] / W
    D = [x for x in pg.get_drawings() if x.get("fill") is not None and x.get("fill_opacity", 1) > 0.9]
    grandi = [x for x in D if x["rect"].width > 0.9 * W and x["rect"].height > 0.9 * H]
    fondo = grandi[0]["fill"] if grandi else (1, 1, 1)
    out = []
    for x in D:
        r = x["rect"]
        if r.width < 80 or r.height < 30 or r.width > 0.8 * W or r.height > 0.85 * H:
            continue
        if max(abs(a - b) for a, b in zip(x["fill"], fondo)) < 0.04:
            continue
        q = [r.x0 * s, r.y0 * s, r.x1 * s, r.y1 * s]
        if any(iou(q, o) > 0.9 for o in out):
            continue
        n = t = 0
        for b in d["parsing_res_list"]:
            if b["block_label"] in SALTA:
                continue
            x0, y0, x1, y1 = b["block_bbox"]
            if q[0] <= (x0 + x1) / 2 <= q[2] and q[1] <= (y0 + y1) / 2 <= q[3]:
                k = len(b.get("block_content") or "")
                n += k
                t += k if b["block_label"] == "table" else 0
        if n >= 60 and t < 0.5 * n:  # con testo; non righe colorate di una tabella
            out.append([round(v, 1) for v in q])
    return out


def chiedi(png_jpg, conf):
    req = urllib.request.Request(SERVIZIO, data=json.dumps({"image_b64": base64.b64encode(png_jpg).decode(), "conf": conf}).encode(),
                                 headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=120).read())


def main(progetto: str, applica: bool = False, conf: float = 0.25, da: int = 1, a: int = 100000):
    doc = pymupdf.open(BASE + "pool/" + progetto + ".pdf")
    file_json = {int(re.search(r"pag_(\d+)_res", f).group(1)): f for f in glob.glob(BASE + progetto + "/json/pag_*_res.json")}
    orig = BASE + progetto + "/json_orig"
    riepilogo = {"pagine": 0, "box": 0, "schede": 0, "mostri": [], "pagine_con_zone": [], "pagine_senza_rilevatore": []}
    file_json = {n: f for n, f in file_json.items() if da <= n <= a and n <= len(doc)}
    testo = {}
    for k, m in enumerate(mostri(file_json)):
        riepilogo["mostri"].append({"nome": m["nome"], "pagine": sorted({p for p, r in m["pezzi"]})})
        for j, (p, r) in enumerate(m["pezzi"]):
            testo.setdefault(p, []).append({"label": "scheda", "confidence": None, "bbox": r, "fonte": "testo",
                                            "mostro": k, "nome": m["nome"], "pezzo": j + 1, "di": len(m["pezzi"])})
    for n in sorted(file_json):
        pg = doc[n - 1]
        z = LATO / max(pg.rect.width, pg.rect.height)
        img = pg.get_pixmap(matrix=pymupdf.Matrix(z, z), alpha=False).tobytes("jpg", jpg_quality=82)
        d = json.load(open(file_json[n], encoding="utf-8"))
        try:
            r = chiedi(img, conf)
        except Exception:  # rilevatore spento o in errore: restano le schede del testo, il flusso non si blocca
            riepilogo["pagine_senza_rilevatore"].append(n)
            r = {"width": d["width"], "zones": []}
        s = d["width"] / r["width"]  # pixel del rilevatore -> pixel del json OCR
        zone = [{"label": q["label"], "confidence": q["confidence"], "bbox": [round(v * s, 1) for v in q["bbox"]], "fonte": "yolo"} for q in r["zones"]]
        T = testo.get(n, [])
        foglio = any(FOGLIO.search(b.get("block_content") or "") for b in d["parsing_res_list"])
        if foglio:
            zone = []  # foglio del personaggio: campi e liste non sono box
        zone = [q for q in zone if not any(coperto(q["bbox"], t["bbox"]) >= 0.5 for t in T)]  # gia' presa come scheda dal testo
        for q in zone:
            t = testo_dentro(d, q["bbox"])
            if q["label"] == "box" and CA.search(t) and PF.search(t):
                q["label"], q["fonte"] = "scheda", "yolo+regola_testo"
        if not foglio:
            for v in box_vettoriali(pg, d):
                if any(coperto(v, q["bbox"]) >= 0.5 for q in zone if q["label"] == "scheda") or any(coperto(v, t["bbox"]) >= 0.5 for t in T):
                    continue
                zone = [q for q in zone if q["label"] != "box" or not (iou(q["bbox"], v) >= 0.5 or coperto(q["bbox"], v) >= 0.8)]  # il disegno e' piu' preciso del rilevatore
                zone.append({"label": "box", "confidence": None, "bbox": v, "fonte": "vettoriale"})
        zone += T
        schede = [q["bbox"] for q in zone if q["label"] == "scheda"]
        zone = [q for q in zone if q["label"] != "box" or not any(coperto(q["bbox"], r) >= 0.8 for r in schede)]  # stessa zona vista due volte
        riepilogo["pagine"] += 1
        riepilogo["box"] += sum(1 for q in zone if q["label"] == "box")
        riepilogo["schede"] += sum(1 for q in zone if q["label"] == "scheda")
        if zone:
            riepilogo["pagine_con_zone"].append(n)
        if applica:
            os.makedirs(orig, exist_ok=True)
            if not os.path.exists(orig + "/" + os.path.basename(file_json[n])):
                shutil.copy(file_json[n], orig)  # copia dell'originale, una volta sola
            d["dnb_zone"] = zone
            json.dump(d, open(file_json[n], "w", encoding="utf-8"), ensure_ascii=False)
    return riepilogo
