"""Etichetta -> coda LoRA su LXC121 -> riepilogo -> carica nel DB, per un libro 5e. Uso: pipeline_libro.py PROGETTO"""
import os, json, pathlib, subprocess, sys, time
sys.path.insert(0, os.path.expanduser("~/.config/dnb"))
import windmill_mcp as w
prog = sys.argv[1]
url = pathlib.Path(os.path.expanduser("~/.config/dnb/windmill_mcp.url")).read_text().strip()
def tool(name, args, secondi=590):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}})
    out = subprocess.run(["curl", "-s", "-m", str(secondi), "-X", "POST", url, "-H", "Content-Type: application/json", "-H", "Accept: application/json, text/event-stream", "-d", body], capture_output=True, text=True).stdout
    l = [x for x in out.replace("\r", "").split("\n") if x.startswith("data: ")]
    r = json.loads(l[-1][6:]) if l else {"errore": out[:200]}
    c = r.get("result", r)
    t = "".join(x.get("text", "") for x in c.get("content", [])) if isinstance(c, dict) and "content" in c else json.dumps(c)
    try: return json.loads(t)
    except Exception: return {"grezzo": t[:500]}
def ssh(cmd, t=60):
    return subprocess.run(["ssh", "root@" + os.environ["PVE_HOST"], f"pct exec 121 -- bash -c {json.dumps(cmd)}"], capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=t).stdout
# attende che la GPU sia libera
while ssh("pgrep -f '[l]ibro_lora_v2.py|[c]lassifica_libro.py' | head -1").strip(): time.sleep(20)
d = tool("f-f_etichettatura_etichetta__libro", {"progetto": {"value": prog, "label": "x"}, "soglia": 0.8, "rifai": False})
if "render_all" not in d: print("ETICHETTATURA FALLITA", d); sys.exit(1)
print(d["render_all"][0]["markdown"][:1200], flush=True)
s = tool("s-f_estrazione_scegli__lora", {"progetto": prog, "tipologie": ["bestiario","incantesimo","classe","specie","background","oggetto","regola","luogo"], "bestiario": "auto"})
tips = [x["tipologia"] for x in s["lora"]]; print("LoRA da lanciare:", tips, "saltate:", s.get("saltate"), flush=True)
cmd = f"cd /opt/dnb-lora-models && setsid nohup ./coda_libro.sh {json.dumps(prog)} {' '.join(tips)} > /dev/null 2>&1 < /dev/null &"
try: ssh(cmd, 20)
except subprocess.TimeoutExpired: pass
time.sleep(5)
for i in range(1500):
    if "CODA_COMPLETATA" in ssh(f"tail -1 {json.dumps('/shared/projects/' + prog + '/estrazione/coda_libro.log')} 2>/dev/null"): break
    time.sleep(20)
else: print("coda non finita nel tempo"); sys.exit(2)
print("coda finita", flush=True)
r = tool("s-f_estrazione_riepilogo__estrazione", {"progetto": prog}); print(r["render_all"][0]["markdown"] if "render_all" in r else r)
t = tool("s-f_estrazione_carica__nel__db", {"progetto": prog, "carica_nel_db": True, "solo_controllo": False, "escludi_tipologie": []})
if "render_all" in t:
    print(t["render_all"][0]["markdown"]);
    for x in t["render_all"][1]["table-row-object"][:40]: print(x)
else: print(t)
