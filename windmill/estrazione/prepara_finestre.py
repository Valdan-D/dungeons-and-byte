import json, os, urllib.request, urllib.error
RUNNER = "http://<GPU_HOST>:8095"

def chiama(percorso, corpo=None, timeout=600):
    dati = json.dumps(corpo).encode() if corpo is not None else None
    req = urllib.request.Request(RUNNER + percorso, data=dati, method="POST" if corpo is not None else "GET",
                                 headers={"X-Runner-Token": os.environ["RUNNER_TOKEN"], "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r: return r.status, json.load(r)
    except urllib.error.HTTPError as e: return e.code, json.loads(e.read() or b"{}")

def main(progetto: str, tipologia: str, ricostruisci: bool = False):
    """Estrazione, passo 2 (per ogni LoRA): costruisce le finestre dai blocchi etichettati.
    Se le finestre esistono gia' (piu' recenti delle etichette) e 'ricostruisci' e' falso le riusa e il LoRA riprende da dove era arrivato;
    altrimenti le ricostruisce e mette da parte i risultati precedenti di questa tipologia."""
    codice, d = chiama("/finestre", {"progetto": progetto, "tipologia": tipologia, "riusa": not ricostruisci})
    if codice != 200: raise Exception(f"Finestre NON costruite ({codice}): {d.get('errore')} {d.get('dettaglio', '')}")
    return d
