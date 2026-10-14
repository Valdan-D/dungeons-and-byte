import json, os, urllib.request, urllib.error
RUNNER = "http://<GPU_HOST>:8095"

def chiama(percorso, corpo=None):
    dati = json.dumps(corpo).encode() if corpo is not None else None
    req = urllib.request.Request(RUNNER + percorso, data=dati, method="POST" if corpo is not None else "GET",
                                 headers={"X-Runner-Token": os.environ["RUNNER_TOKEN"], "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")

from typing import Literal

def main(tipologia: Literal["bestiario_new", "bestiario_old", "oggetto", "regola", "incantesimo", "classe", "specie", "background", "luogo"] = "oggetto",
         finestre: Literal["srd_unite", "srd_mostri_titoli"] = "srd_unite"):
    """Avvia un LoRA su un insieme di finestre (riprende da dove si era fermato). Rifiuta se la GPU e' occupata o l'OCR e' attivo."""
    codice, d = chiama("/lancia", {"tipologia": tipologia, "finestre": finestre})
    if codice == 200:
        return {"markdown": f"**Avviato** `{tipologia}` su `{finestre}`.\n\nRisultati: `{d['risultati']}`\n\n{d['nota']}. Guarda l'avanzamento con lo script *stato_gpu*."}
    return {"markdown": f"**Non avviato (codice {codice})**: {d.get('errore')}\n\n```\n{json.dumps(d, ensure_ascii=False, indent=1)}\n```"}
