"""Test adapter LoRA attuali su finestre multi-entità (val_v2). Gira su LXC 121.

Per tipologia: N finestre con >=2 entità + M negative. Misura recall/precisione sui nomi,
JSON valido, e quante volte il modello si ferma a 1 entità quando ce ne sono di più.
Nomi estratti anche da output troncato (regex), così un JSON tagliato non azzera il conteggio.
"""
import json, random, re, subprocess, sys, time, unicodedata
from pathlib import Path
import requests

sys.path.insert(0, "/opt/dnb-lora-models")
from validate_lora import avvia_llama_server, PORT  # stesso server/porta della validazione

MODELS = Path("/opt/dnb-lora-models/adapters")
VAL = Path("/opt/dnb-lora-models/val_v2")
OUT = Path("/opt/dnb-lora-models/test_finestre.json")
N_MULTI, N_NEG = int(sys.argv[1]), int(sys.argv[2])
TIPI = sys.argv[3].split(",")
ADAPTER = {"bestiario_new": "bestiario_png"}          # il vecchio adapter bestiario
SYS_ADAPTER = {"bestiario_new": json.load(open("/opt/dnb-lora-models/val_v2/sys_bestiario_png.json"))}
random.seed(42)

def norm(s):
    s = "".join(c for c in unicodedata.normalize("NFD", s.lower()) if unicodedata.category(c) != "Mn")
    return " ".join(re.findall(r"[a-z0-9]+", re.sub(r"\(.*?\)", "", s)))

def chiama(sysp, testo):
    body = {"model": "local", "messages": [{"role": "system", "content": sysp}, {"role": "user", "content": testo}],
            "temperature": 0.1, "max_tokens": 2500, "chat_template_kwargs": {"enable_thinking": False}}
    r = requests.post(f"http://127.0.0.1:{PORT}/v1/chat/completions", json=body, timeout=600)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]

def nomi_output(raw):
    try:
        v = json.loads(re.sub(r"^```(json)?|```$", "", raw.strip(), flags=re.M).strip())
        if isinstance(v, list):
            return [norm(e.get("nome", "")) for e in v if isinstance(e, dict)], True
    except json.JSONDecodeError:
        pass
    return [norm(x) for x in re.findall(r'"nome"\s*:\s*"([^"]*)"', raw)], False

def match(a, b):
    return a == b or (len(a) >= 4 and len(b) >= 4 and (a in b or b in a))

risultati = json.load(open(OUT)) if OUT.exists() else {}
for tip in TIPI:
    if tip in risultati:
        continue
    righe = [json.loads(l)["messages"] for l in open(VAL / f"{tip}_train.jsonl")]
    meta = [json.loads(l) for l in open(VAL / f"{tip}_train.meta.jsonl")]
    multi = [r for r, m in zip(righe, meta) if m["fonte"] == "reale" and len(json.loads(r[2]["content"])) >= 2]
    neg = [r for r, m in zip(righe, meta) if m["fonte"] == "negativo"]
    campione = random.sample(multi, min(N_MULTI, len(multi))) + random.sample(neg, min(N_NEG, len(neg)))
    if not campione:
        continue
    gguf = MODELS / f"{ADAPTER.get(tip, tip)}_gguf_gguf" / "qwen3-4b.Q8_0.gguf"
    print(f"[{tip}] {len(campione)} finestre, modello {gguf.parent.name}", flush=True)
    proc = avvia_llama_server(gguf)
    casi = []
    try:
        for r in campione:
            attese = [norm(e["nome"]) for e in json.loads(r[2]["content"])]
            t0 = time.time()
            try:
                raw = chiama(SYS_ADAPTER.get(tip, r[0]["content"]), r[1]["content"])
            except Exception as ex:
                raw = f"ERRORE {ex}"
            trovati, valido = nomi_output(raw)
            tp = sum(1 for a in attese if any(match(a, t) for t in trovati))
            extra = [t for t in trovati if not any(match(a, t) for a in attese)]
            casi.append({"attese": attese, "trovati": trovati, "tp": tp, "extra": extra, "json_valido": valido,
                         "secondi": round(time.time() - t0, 1)})
            print(f"  attese {len(attese)} trovate {tp} extra {len(extra)} json {valido} ({casi[-1]['secondi']}s)", flush=True)
    finally:
        proc.terminate(); proc.wait()
    m = [c for c in casi if c["attese"]]; n = [c for c in casi if not c["attese"]]
    risultati[tip] = {
        "finestre_multi": len(m),
        "recall": round(sum(c["tp"] for c in m) / max(1, sum(len(c["attese"]) for c in m)), 3),
        "fermo_a_1": sum(1 for c in m if len(c["trovati"]) <= 1),
        "tutte_trovate": sum(1 for c in m if c["tp"] == len(c["attese"])),
        "json_non_valido": sum(1 for c in casi if not c["json_valido"]),
        "negativi": len(n), "negativi_ok": sum(1 for c in n if not c["trovati"]),
        "casi": casi,
    }
    json.dump(risultati, open(OUT, "w"), ensure_ascii=False, indent=1)
    print(tip, {k: v for k, v in risultati[tip].items() if k != "casi"}, flush=True)
