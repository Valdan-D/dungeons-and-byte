import json, os, urllib.request, urllib.error
RUNNER = "http://192.168.178.121:8095"

def chiama(percorso, corpo=None):
    dati = json.dumps(corpo).encode() if corpo is not None else None
    req = urllib.request.Request(RUNNER + percorso, data=dati, method="POST" if corpo is not None else "GET",
                                 headers={"X-Runner-Token": os.environ["RUNNER_TOKEN"], "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")

def main():
    """Ferma i lavori LoRA in corso e la coda automatica sul server 121 (non tocca l'OCR)."""
    codice, d = chiama("/stop", {})
    return {"markdown": f"**Fermato**: {d.get('fermato')}  |  processi rimasti: {d.get('processi_rimasti')}"}
