"""Prova isolata del classificatore GLiNER2.5-Decide su blocchi dell'SRD (GPU).
Misura: velocita' per blocco, effetto della lunghezza, risposta multi-label zero-shot.
Uso: venv/bin/python prova_classificatore.py <nome_modello>"""
import json, sys, time, os, torch
os.environ["HF_HOME"] = "/opt/classificatore_test/hf"
from gliner2 import GLiNER2

nome = sys.argv[1]
t0 = time.time()
model = GLiNER2.from_pretrained(nome)
try:
    model = model.to("cuda")
except Exception as e:
    print("to(cuda) non riuscito:", e)
print("caricato", nome, round(time.time() - t0, 1), "s; dispositivo:", next(model.parameters()).device)

ETICHETTE = ["bestiario", "incantesimo", "classe", "specie", "background", "oggetto", "regola", "luogo"]
finestre = json.load(open("/opt/dnb-lora-models/srd_finestre_unite.json"))
tok = lambda f: f["tok"]

def schema():
    return {"tipologie": {"labels": ETICHETTE, "multi_label": True, "cls_threshold": 0.4}}

def classifica(testo):
    return model.classify_text(testo, schema())

# 1) esempio qualsiasi per vedere il formato di risposta
print("ESEMPIO:", classifica(finestre[200]["testo"][:1500]))

# 2) lunghezza: prende blocchi di ~500, ~1000, ~1800 token (finestre intere) e misura tempo e VRAM
for soglia in (500, 1000):
    cand = [f for f in finestre if abs(tok(f) - soglia) < soglia * 0.25][:5]
    ts = []
    for f in cand:
        torch.cuda.synchronize(); t = time.time()
        r = classifica(f["testo"])
        torch.cuda.synchronize(); ts.append(time.time() - t)
    print(f"~{soglia} tok: {len(cand)} blocchi, media {sum(ts)/max(1,len(ts)):.3f}s, ultimo risultato: {r}")
print("VRAM max MB:", torch.cuda.max_memory_allocated() // 2**20)

# 3) tutto l'SRD, a pezzi da ~CH token (testo diviso per paragrafi), etichette unite per finestra
CH = int(os.environ.get("CH", "500"))
def pezzi(f):
    par = f["testo"].split("\n\n"); r = max(1, len(f["testo"])) / max(1, f["tok"]); cur = ""; out = []
    for p in par:
        if cur and (len(cur) + len(p)) / r > CH: out.append(cur); cur = ""
        cur += ("\n\n" if cur else "") + p
    if cur: out.append(cur)
    # paragrafi enormi: taglio duro
    fin = []
    for c in out:
        m = int(CH * r * 1.5)
        fin += [c[k:k+m] for k in range(0, len(c), m)] if len(c) > m else [c]
    return fin
out = []
t = time.time()
for f in finestre:
    lab = set(); np_ = 0
    for c in pezzi(f):
        np_ += 1
        r = classifica(c)
        lab |= set(r["tipologie"])
    out.append({"id": f["id"], "file": f["file"], "tok": f["tok"], "pezzi": np_, "etichette": sorted(lab)})
tot = time.time() - t
print(f"SRD intero a pezzi da {CH}: {len(finestre)} finestre in {tot:.0f}s ({tot/len(finestre):.2f}s/finestra); VRAM max MB {torch.cuda.max_memory_allocated() // 2**20}")
json.dump(out, open(f"/opt/classificatore_test/srd_{nome.split('/')[-1]}_{CH}.json", "w"), ensure_ascii=False)
