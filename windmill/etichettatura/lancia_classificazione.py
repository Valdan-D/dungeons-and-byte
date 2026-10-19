import json, os, urllib.request, urllib.error, urllib.parse
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

def main(progetto: str, rifai: bool = False):
    """Etichettatura, passo 1: avvia il classificatore a blocchi sul libro (GPU). Se i punteggi esistono gia' e 'rifai' e' falso, salta.
    Fallisce (con il motivo) se la GPU e' occupata o l'OCR e' attivo."""
    q = urllib.parse.quote(progetto)
    codice, st = chiama(f"/classifica?progetto={q}")
    if codice != 200:
        raise Exception(f"Progetto non valido ({codice}): {st.get('errore')}")
    if st.get("punteggi_presenti") and not rifai and not st.get("in_corso"):
        return {"saltata": True, "motivo": "punteggi gia' presenti (spunta 'rifai' per ricalcolarli)", "stato": st}
    if st.get("in_corso"):
        return {"saltata": False, "motivo": "classificazione gia' in corso: la attendo", "stato": st}
    codice, d = chiama("/classifica", {"progetto": progetto})
    if codice != 200:
        raise Exception(f"Classificazione NON avviata ({codice}): {d.get('errore')} {d.get('processi') or d.get('servizi') or ''}")
    return {"saltata": False, "motivo": "avviata", "stato": d}
