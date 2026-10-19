import json, os, time

ET = ["bestiario", "incantesimo", "classe", "specie", "background", "oggetto", "regola", "luogo"]

def main(progetto: str, soglia: float = 0.8, salto_righe_massimo: int = 2):
    """Etichettatura, passo 3: applica la soglia ai punteggi, unisce le unita' contigue con la stessa tipologia in INTERVALLI DI RIGHE
    (per file di capitoli) e scrive <progetto>/etichette/etichette.json. Non usa la GPU: si puo' rifare con soglie diverse."""
    cart = f"/shared/projects/{progetto}/etichette"
    dati = json.load(open(f"{cart}/punteggi.json"))
    unita = dati["unita"]; tot_tok = sum(u["tok"] for u in unita) or 1
    for u in unita:
        u["tipi"] = [t for t in ET if u["punti"][t] >= soglia]
    intervalli = {t: [] for t in ET}
    for t in ET:
        cur = None
        for u in unita:
            if t not in u["tipi"]:
                if cur: intervalli[t].append(cur); cur = None
                continue
            vicino = cur and cur["file"] == u["file"] and u["righe"][0] - cur["righe"][1] <= salto_righe_massimo + 1
            if vicino:
                cur["righe"][1] = max(cur["righe"][1], u["righe"][1]); cur["unita"].append(u["id"]); cur["tok"] += u["tok"]
                cur["_punti"].append(u["punti"][t])
            else:
                if cur: intervalli[t].append(cur)
                cur = {"file": u["file"], "righe": list(u["righe"]), "unita": [u["id"]], "tok": u["tok"], "_punti": [u["punti"][t]]}
        if cur: intervalli[t].append(cur)
    for t in ET:
        for iv in intervalli[t]:
            p = iv.pop("_punti"); iv["punteggio_medio"] = round(sum(p) / len(p), 3)
    senza = [u["id"] for u in unita if not u["tipi"]]
    out = {"progetto": progetto, "soglia": soglia, "creato": time.strftime("%Y-%m-%d %H:%M:%S"), "unita_totali": len(unita), "token_totali": tot_tok,
           "senza_etichetta": len(senza), "intervalli": intervalli,
           "unita": [{"id": u["id"], "file": u["file"], "righe": u["righe"], "tok": u["tok"], "tipi": u["tipi"]} for u in unita]}
    json.dump(out, open(f"{cart}/etichette.json", "w"), ensure_ascii=False)
    righe = ["| Tipologia | Unita' | Intervalli | Token | % del testo |", "|---|---:|---:|---:|---:|"]
    for t in ET:
        n = sum(1 for u in unita if t in u["tipi"]); tk = sum(u["tok"] for u in unita if t in u["tipi"])
        righe.append(f"| {t} | {n} | {len(intervalli[t])} | {tk} | {100 * tk / tot_tok:.0f}% |")
    tk_s = sum(u["tok"] for u in unita if not u["tipi"])
    righe.append(f"| *nessuna* | {len(senza)} | - | {tk_s} | {100 * tk_s / tot_tok:.0f}% |")
    md = f"**{progetto}** - soglia {soglia}: {len(unita)} unita', {tot_tok} token.\n\nScritto in `{cart}/etichette.json`.\n\n" + "\n".join(righe)
    tab = [{"tipologia": t, "file": iv["file"], "righe": f"{iv['righe'][0]}-{iv['righe'][1]}", "unita": len(iv["unita"]), "token": iv["tok"], "punteggio": iv["punteggio_medio"]}
           for t in ET for iv in intervalli[t]]
    return {"render_all": [{"markdown": md}, {"table-row-object": tab[:300]}]}
