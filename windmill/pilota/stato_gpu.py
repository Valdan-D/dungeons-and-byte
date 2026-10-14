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
    """Mostra lo stato della GPU, dell'OCR e dei lavori LoRA sul server 121."""
    codice, d = chiama("/stato")
    if codice != 200:
        return {"markdown": f"**Errore {codice}** dall'esecutore: {d}"}
    righe = []
    for l in d["lavori"]:
        fatte, tot = l.get("fatte", 0), l.get("totale", 0)
        stato = "finito" if l["finito"] else ("in corso" if l["ultimo_aggiornamento_min_fa"] < 15 else "fermo?")
        righe.append({"lavoro": l["file"], "avanzamento": f"{fatte}/{tot}", "percentuale": round(100 * fatte / tot) if tot else 0,
                      "min_rimanenti_stima": None if l["finito"] else l.get("min_rimanenti_stima"), "stato": stato,
                      "ultimo_aggiornamento_min_fa": l["ultimo_aggiornamento_min_fa"]})
    righe.sort(key=lambda r: (r["stato"] == "finito", r["lavoro"]))
    ocr = ", ".join(f"{k}: **{v}**" for k, v in d["ocr"].items())
    attivi = d["processi_attivi"]
    testa = (f"## Stato del server GPU ({d['ora']})\n\n"
             f"- Memoria GPU: **{d['gpu_memoria']}**\n- OCR: {ocr}\n"
             f"- Processi LoRA attivi: **{len(attivi)}**" + ("" if not attivi else "\n" + "\n".join(f"  - `{p[:110]}`" for p in attivi)) +
             f"\n\n**Coda:**\n```\n{d['coda'] or '(vuota)'}\n```")
    return {"render_all": [{"markdown": testa}, {"table-row-object": righe}]}
