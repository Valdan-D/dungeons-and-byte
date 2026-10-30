"""Recupero box mancanti (comando a parte, non tocca titoli ne' decisioni).
Cerca i riquadri rosa/lilla con larghezza piu' tollerante di quella del rilevatore standard, scarta quelli che
coincidono con box gia' presenti in box.json e quelli che non passano i filtri comuni.
applica=False: solo rapporto. applica=True: aggiunge i nuovi a box.json (copia in box.json.prima_recupero)
e riscrive la bozza con scrivi_bozza (metodo legacy: box.json + catalogo)."""
import importlib
import json
import sys

sys.path.insert(0, "/shared/projects/tools/revisione_titoli")
import pymupdf
P = importlib.import_module("prepara_revisione")

PAR = dict(P.PAR_PIXEL, larghezza_min_pt=110, larghezza_max_pt=330, altezza_min_pt=30, riempimento_min=0.45)


def main(progetto: str, applica: bool = False, larghezza_max: int = 250):
    V = "/shared/projects/" + progetto
    rev = V + "/revisione/"
    box = json.load(open(rev + "box.json", encoding="utf-8"))
    pag = P.leggi_pagine(progetto)
    doc = pymupdf.open(P.BASE + "pool/" + progetto + ".pdf")
    nuovi = []
    for n in sorted(pag):
        j = pag[n]
        p = doc[n - 1]
        s = p.rect.width / j["width"]
        bl = [{"i": i, "l": b["block_label"], "r": [v * s for v in b["block_bbox"]], "t": (b.get("block_content") or "").strip().replace("\n", " ")[:60],
               "tx": (b.get("block_content") or "")[:300]}
              for i, b in enumerate(j["parsing_res_list"]) if b.get("block_bbox")]
        gia = [pymupdf.Rect(b["rect"]) for b in box if b["pag"] == n]
        for r in P.trova_pixel(p, PAR):
            R = pymupdf.Rect(r)
            if any(not (R & g).is_empty and (R & g).get_area() >= 0.5 * min(R.get_area(), g.get_area()) for g in gia):
                continue
            dentro = P._dentro(R, bl)
            if not P._ok_candidato(R, dentro, p.rect.width):
                continue
            x = {"pag": n, "rect": [round(v) for v in r], "voci": ["raster-riquadro-rosa-lilla"], "blocchi": [b["i"] for b in dentro],
                 "titoli": [b["t"] for b in dentro if b["l"] in ("paragraph_title", "doc_title")], "tipo": None}
            nuovi.append(x)
            gia.append(R)
    larghi = [x for x in nuovi if x["rect"][2] - x["rect"][0] > larghezza_max]  # piu' larghi di una colonna: dubbi, non si aggiungono
    nuovi = [x for x in nuovi if x not in larghi]
    out = {"esistenti": len(box), "nuovi": len(nuovi), "pagine": sorted({x["pag"] for x in nuovi}),
           "dubbi_non_aggiunti": [(x["pag"], x["rect"]) for x in larghi],
           "esempi": [(x["pag"], x["rect"], x["titoli"][:1]) for x in nuovi[:8]]}
    if applica and nuovi:
        json.dump(box, open(rev + "box.json.prima_recupero", "w", encoding="utf-8"), ensure_ascii=False)
        json.dump(box + nuovi, open(rev + "box.json", "w", encoding="utf-8"), ensure_ascii=False)
        S = importlib.import_module("scrivi_bozza")
        out["bozza"] = S.main(progetto)
    return out
