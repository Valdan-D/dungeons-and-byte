import json, os, time, urllib.request, urllib.error, urllib.parse
RUNNER = "http://<GPU_HOST>:8095"

def chiama(percorso, corpo=None):
    dati = json.dumps(corpo).encode() if corpo is not None else None
    req = urllib.request.Request(RUNNER + percorso, data=dati, method="POST" if corpo is not None else "GET",
                                 headers={"X-Runner-Token": os.environ["RUNNER_TOKEN"], "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r: return r.status, json.load(r)
    except urllib.error.HTTPError as e: return e.code, json.loads(e.read() or b"{}")

def main(progetto: str, tipologia: str, n_finestre: int, ore_massime: int = 12):
    """Estrazione, passo 3 (per ogni LoRA): lancia il LoRA sulle finestre e aspetta che finisca (controlla ogni 30 s).
    Fallisce subito se la GPU e' occupata o l'OCR e' attivo. Se il flusso viene rilanciato, riprende da dove si era fermato."""
    if not n_finestre: return {"tipologia": tipologia, "saltata": True, "motivo": "nessuna finestra"}
    codice, d = chiama("/estrai", {"progetto": progetto, "tipologia": tipologia})
    if codice != 200: raise Exception(f"{tipologia} NON avviato ({codice}): {d.get('errore')} {d.get('processi') or d.get('servizi') or ''}")
    q = "/estrazione?progetto=" + urllib.parse.quote(progetto); fine = time.time() + ore_massime * 3600; t0 = time.time(); st = {}
    time.sleep(20)
    while time.time() < fine:
        c, s = chiama(q)
        if c == 200:
            mio = [x for x in s.get("lavori", []) if x.get("tipologia") == tipologia]
            st = mio[0] if mio else {}
            attivi = [p for p in s.get("processi_attivi", []) if tipologia in p]
            if st.get("finito") and not attivi:
                return {"tipologia": tipologia, "finestre": st.get("totale"), "minuti": round((time.time() - t0) / 60, 1)}
            if not attivi and time.time() - t0 > 120 and not st.get("finito"):
                raise Exception(f"{tipologia}: il processo e' terminato senza finire. Ultimo stato: {st}")
        time.sleep(30)
    raise Exception(f"{tipologia}: tempo massimo superato ({ore_massime} h). Ultimo stato: {st}")
