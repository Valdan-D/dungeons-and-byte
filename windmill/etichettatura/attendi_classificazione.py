import json, os, time, urllib.request, urllib.error, urllib.parse
RUNNER = "http://<GPU_HOST>:8095"

def stato(progetto):
    req = urllib.request.Request(RUNNER + "/classifica?progetto=" + urllib.parse.quote(progetto), headers={"X-Runner-Token": os.environ["RUNNER_TOKEN"]})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)

def main(progetto: str, salta: bool = False, minuti_massimi: int = 60):
    """Etichettatura, passo 2: aspetta la fine della classificazione (controlla il runner ogni 5 s). Se il passo 1 ha saltato, esce subito."""
    if salta:
        return {"attesa": "non necessaria"}
    fine = time.time() + minuti_massimi * 60; avviata = False; st = {}
    while time.time() < fine:
        st = stato(progetto)
        if st.get("in_corso"): avviata = True
        if st.get("fase") == "errore" or st.get("errore_fatale"):
            raise Exception(f"Classificazione fallita: {st.get('errore')}")
        if st.get("finito") and not st.get("in_corso"):
            return {"attesa": "finita", "unita": st.get("totale"), "secondi": st.get("secondi")}
        time.sleep(5)
    raise Exception(f"Tempo massimo superato ({minuti_massimi} min). Ultimo stato: {st}")
