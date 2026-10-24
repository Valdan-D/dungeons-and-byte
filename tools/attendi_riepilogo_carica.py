import os, json, subprocess, sys, time
sys.path.insert(0, os.path.expanduser("~/.config/dnb"))
import windmill_mcp as w
prog = sys.argv[1]
for i in range(680):
    r = subprocess.run(["ssh", "root@" + os.environ["PVE_HOST"], f"pct exec 121 -- bash -c 'tail -1 \"/shared/projects/{prog}/estrazione/coda_libro.log\" 2>/dev/null'"], capture_output=True, text=True, stdin=subprocess.DEVNULL)
    if "CODA_COMPLETATA" in r.stdout: break
    time.sleep(10)
else:
    print("coda non finita entro il tempo"); sys.exit()
print("coda finita:", r.stdout.strip(), flush=True)
r2 = json.loads(w.testo(w.call("s-f_estrazione_riepilogo__estrazione", {"progetto": prog}))); print(r2["render_all"][0]["markdown"], flush=True)
t = w.testo(w.call("s-f_estrazione_carica__nel__db", {"progetto": prog, "carica_nel_db": True, "solo_controllo": False, "escludi_tipologie": []}))
try:
    d = json.loads(t); print(d["render_all"][0]["markdown"]); print(d["render_all"][1]["table-row-object"][:40])
except Exception: print(t[:1500])
