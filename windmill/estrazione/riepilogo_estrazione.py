import glob, json, os, re, time

def N(s): return re.sub(r"\W+", " ", str(s).lower()).strip()

def main(progetto: str):
    """Estrazione, passo 4: raccoglie le risposte dei LoRA in <progetto>/estrazione/entita.json (senza duplicati per nome) e riassume."""
    d = f"/shared/projects/{progetto}/estrazione"; tutte = {}; righe = ["| Tipologia | Finestre | Minuti | JSON rotti | Entita' uniche |", "|---|---:|---:|---:|---:|"]; tab = []
    for f in sorted(glob.glob(f"{d}/*.jsonl")):
        tip = os.path.basename(f)[:-6]; visti = {}; n = rotti = 0; sec = 0.0
        for l in open(f):
            r = json.loads(l); n += 1; sec += r.get("secondi", 0)
            try: v = json.loads(r["raw"])
            except Exception: v = None
            if not isinstance(v, list): rotti += 1; continue
            for e in v:
                if isinstance(e, dict) and e.get("nome"):
                    k = N(e["nome"])
                    if k not in visti: e = dict(e); e["_finestra"] = r["id"]; e["_file"] = r["file"]; visti[k] = e
        tutte[tip] = list(visti.values())
        righe.append(f"| {tip} | {n} | {sec / 60:.0f} | {rotti} | {len(visti)} |")
        tab += [{"tipologia": tip, "nome": e["nome"], "file": e["_file"]} for e in visti.values()]
    json.dump({"progetto": progetto, "creato": time.strftime("%Y-%m-%d %H:%M:%S"), "entita": tutte}, open(f"{d}/entita.json", "w"), ensure_ascii=False)
    md = f"**{progetto}**\n\nRaccolte in `{d}/entita.json`.\n\n" + "\n".join(righe)
    return {"render_all": [{"markdown": md}, {"table-row-object": tab[:500]}]}
