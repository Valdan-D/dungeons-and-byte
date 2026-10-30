# requirements:
# pymupdf
"""Revisione titoli - controllo di completezza: tutto cio' che serve c'e' per ogni pagina?"""
import glob
import json
import os
import re

import pymupdf

BASE = "/shared/projects/"


def main(progetto: str, cartella_output: str = "", riprepara: bool = False):
    pdf = BASE + "pool/" + progetto + ".pdf"
    if not os.path.isfile(pdf):
        raise Exception("PDF mancante in pool: " + pdf)
    n = len(pymupdf.open(pdf))
    js = {int(re.search(r"pag_(\d+)_", f).group(1)) for f in glob.glob(BASE + progetto + "/json/pag_*_res.json")}
    md = {int(re.search(r"page_(\d+)\.md", f).group(1)) for f in glob.glob(BASE + progetto + "/markdown/page_*.md")}
    tutte = set(range(1, n + 1))
    problemi = []
    if tutte - js:
        problemi.append("JSON OCR mancanti per %d pagine su %d (es. %s)" % (len(tutte - js), n, sorted(tutte - js)[:8]))
    if tutte - md:
        problemi.append("markdown mancanti per %d pagine su %d (es. %s)" % (len(tutte - md), n, sorted(tutte - md)[:8]))
    if not os.path.isfile(BASE + "tools/catalogo_box.json"):
        problemi.append("catalogo dei box mancante (tools/catalogo_box.json)")
    rev = (cartella_output or "/shared/projects").rstrip("/") + "/" + progetto + "/revisione"
    if os.path.isfile(rev + "/decisioni.jsonl") and os.path.getsize(rev + "/decisioni.jsonl") > 0 and not riprepara:
        problemi.append("esistono gia' decisioni in %s: per ripartire da zero rilancia con riprepara=true (la storia viene tenuta)" % rev)
    if problemi:
        raise Exception("Libro non pronto per la revisione: " + " | ".join(problemi))
    return {"progetto": progetto, "pagine": n, "json": len(js), "markdown": len(md)}
