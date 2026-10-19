#!/usr/bin/env python3
"""Classificatore a blocchi (D&B): divide un libro in unita' (paragrafi <= ~400 token, mai a cavallo di due file di
capitoli_ricapitolati/) e assegna a ognuna il punteggio di ogni tipologia. Scrive <progetto>/etichette/punteggi.json.
Uso: classifica_libro.py PROGETTO     (PROGETTO = nome della cartella in /shared/projects)"""
import glob, json, os, re, sys, time
os.environ.setdefault("HF_HOME", "/opt/classificatore_test/hf"); os.environ["HF_HUB_DISABLE_XET"] = "1"
BASE = "/opt/dnb-classificatore"; P = "/shared/projects"
ET = ["bestiario", "incantesimo", "classe", "specie", "background", "oggetto", "regola", "luogo"]
SCH = {"tipologie": {"labels": ET, "multi_label": True, "cls_threshold": 0.0}}   # 0 = confidenza di tutte le etichette
MAX_UNITA, MAX_TABELLA = 400, 500

def main(progetto):
    if "/" in progetto or progetto.startswith("."): sys.exit("nome progetto non valido")
    cart = f"{P}/{progetto}"; src = f"{cart}/capitoli_ricapitolati"
    if not os.path.isdir(src): sys.exit(f"manca {src}")
    out = f"{cart}/etichette"; os.makedirs(out, exist_ok=True)
    stato = lambda **k: json.dump({"progetto": progetto, "aggiornato": time.strftime("%Y-%m-%d %H:%M:%S"), **k}, open(f"{out}/classifica.stato", "w"))
    stato(fase="preparo le unita'", fatte=0, totale=0, finito=False)
    from tokenizers import Tokenizer
    TOK = Tokenizer.from_file(f"{BASE}/tokenizer_qwen3.json")
    ntok = lambda s: len(TOK.encode(s).ids)

    def spezza_tabelle(b):
        m = re.search(r"(<table[^>]*>)(.*?)</table>", b, re.S)
        if not m or ntok(b) <= MAX_TABELLA: return [b]
        righe = re.findall(r"<tr>.*?</tr>", m.group(2), re.S)
        if len(righe) < 3: return [b]
        pre, post = b[:m.start()].strip(), b[m.end():].strip()
        o = [pre] if pre else []
        testa, corpo, cur = righe[0], righe[1:], []
        for r in corpo:
            if cur and ntok(m.group(1) + testa + "".join(cur) + r + "</table>") > MAX_TABELLA:
                o.append(m.group(1) + testa + "".join(cur) + "</table>"); cur = []
            cur.append(r)
        if cur: o.append(m.group(1) + testa + "".join(cur) + "</table>")
        if post: o.extend(spezza_tabelle(post))
        return o

    unita, testi = [], []
    for f in sorted(glob.glob(f"{src}/*.md")):
        testo = open(f, errors="ignore").read(); righe_file = testo.split("\n"); bl = []
        for m in re.finditer(r"(.*?)(\n\s*\n|\Z)", testo, re.S):
            b0 = m.group(1)
            if not b0.strip(): continue
            lead = len(b0) - len(b0.lstrip())
            ini = testo.count("\n", 0, m.start(1) + lead) + 1; fin = testo.count("\n", 0, m.start(1) + len(b0.rstrip())) + 1
            for s in spezza_tabelle(b0.strip()):
                if s.strip(): bl.append({"t": s.strip(), "tok": ntok(s.strip()), "r": (ini, fin)})
        k = 0
        while k < len(bl):
            s = k; tok = 0
            while k < len(bl) and (tok + bl[k]["tok"] <= MAX_UNITA or k == s):
                if k > s and bl[k]["t"].startswith("#") and tok >= 150: break
                tok += bl[k]["tok"] + 1; k += 1
            a, z = bl[s]["r"][0], bl[k - 1]["r"][1]
            unita.append({"id": len(unita), "file": os.path.basename(f), "righe": [a, z], "tok": tok})
            testi.append("\n".join(righe_file[a - 1:z]).strip() or " ")
    tot = len(unita); stato(fase="carico il modello", fatte=0, totale=tot, finito=False)
    from gliner2 import AutoExtractor
    m = AutoExtractor.from_pretrained("fastino/GLiNER2.5-multi-Decide"); m.load_adapter(f"{BASE}/adapter/final"); m = m.to("cuda")
    t0 = time.time(); punti = []
    for i in range(0, tot, 64):
        for r in m.batch_classify_text(testi[i:i + 64], SCH, batch_size=8, include_confidence=True, max_len=512):
            punti.append({x["label"]: round(x["confidence"], 4) for x in r["tipologie"]})
        stato(fase="classifico", fatte=len(punti), totale=tot, finito=False)
    for u, p in zip(unita, punti): u["punti"] = p
    tmp = f"{out}/punteggi.json.tmp"
    json.dump({"progetto": progetto, "creato": time.strftime("%Y-%m-%d %H:%M:%S"), "adapter": "classificatore_blocchi_v1 (epoca 6)",
               "etichette": ET, "secondi": round(time.time() - t0), "unita": unita}, open(tmp, "w"), ensure_ascii=False)
    os.replace(tmp, f"{out}/punteggi.json")
    stato(fase="finito", fatte=tot, totale=tot, finito=True, secondi=round(time.time() - t0))
    print("FINE_CLASSIFICA", tot, "unita", flush=True)

if __name__ == "__main__":
    try: main(sys.argv[1])
    except SystemExit: raise
    except Exception as e:
        import traceback; traceback.print_exc()
        try:
            out = f"{P}/{sys.argv[1]}/etichette"; json.dump({"progetto": sys.argv[1], "fase": "errore", "errore": str(e), "finito": False, "errore_fatale": True}, open(f"{out}/classifica.stato", "w"))
        except Exception: pass
        sys.exit(1)
