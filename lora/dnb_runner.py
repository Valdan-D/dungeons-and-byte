#!/usr/bin/env python3
"""Esecutore dei lavori LoRA su LXC 121 (solo libreria standard). Comandi FISSI, nessuna shell, accessibile solo dall'IP di Windmill.
GET  /salute  -> ok (senza segreto)
GET  /stato   -> stato di GPU, OCR, lavori (da file .stato e log), coda
POST /lancia  {tipologia, finestre}  -> avvia libro_lora_v2.py se la GPU e' libera
POST /stop    -> ferma i lavori LoRA (e la coda automatica)"""
import json, os, re, subprocess, time, glob
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE = "/opt/dnb-lora-models"; RIS = f"{BASE}/risultati_srd"
TOKEN = os.environ["RUNNER_TOKEN"]; IP_AMMESSI = set(os.environ.get("RUNNER_ALLOWED_IPS", "127.0.0.1").split(","))
TIPI = ["bestiario_new", "bestiario_old", "oggetto", "regola", "incantesimo", "classe", "specie", "background", "luogo"]
FINESTRE = {"srd_unite": (f"{BASE}/srd_finestre_unite.json", "unite"), "srd_mostri_titoli": (f"{BASE}/srd_mostri_titoli.json", "titoli")}

def run(args):
    return subprocess.run(args, capture_output=True, text=True, timeout=30).stdout.strip()

def processi():
    out = run(["pgrep", "-af", "libro_lora_v2.py|coda_srd.sh"])
    return [l for l in out.splitlines() if "pgrep" not in l]

def stato():
    lavori = []
    for f in sorted(glob.glob(f"{RIS}/*.stato")):
        try: d = json.load(open(f))
        except Exception: continue
        log = f.replace(".jsonl.stato", ".log")
        finito = os.path.exists(log) and "FINE_LIBRO_LORA" in open(log, errors="ignore").read()[-2000:]
        d["file"] = os.path.basename(f).replace(".jsonl.stato", "")
        d["finito"] = finito
        d["ultimo_aggiornamento_min_fa"] = round((time.time() - os.path.getmtime(f)) / 60, 1)
        lavori.append(d)
    coda = ""
    if os.path.exists(f"{RIS}/coda.log"): coda = "\n".join(open(f"{RIS}/coda.log").read().strip().splitlines()[-6:])
    gpu = run(["nvidia-smi", "--query-gpu=memory.used,memory.total", "--format=csv,noheader"])
    ocr = {s: run(["systemctl", "is-active", s]) for s in ("paddleocr-vlm", "paddleocr-gateway")}
    return {"ora": time.strftime("%Y-%m-%d %H:%M:%S"), "gpu_memoria": gpu, "ocr": ocr, "processi_attivi": processi(), "lavori": lavori, "coda": coda}

def lancia(body):
    tip, fin = body.get("tipologia"), body.get("finestre")
    if tip not in TIPI: return 400, {"errore": f"tipologia non valida: {tip}", "ammesse": TIPI}
    if fin not in FINESTRE: return 400, {"errore": f"finestre non valide: {fin}", "ammesse": list(FINESTRE)}
    attivi = processi()
    if attivi: return 409, {"errore": "GPU occupata da un altro lavoro", "processi": attivi}
    ocr = [s for s in ("paddleocr-vlm", "paddleocr-gateway") if run(["systemctl", "is-active", s]) == "active"]
    if ocr: return 409, {"errore": "L'OCR e' attivo: fermalo prima (non lo fermo da solo)", "servizi": ocr}
    path, suff = FINESTRE[fin]; out = f"{RIS}/{tip}_{suff}.jsonl"; log = f"{RIS}/{tip}_{suff}.log"
    with open(log, "a") as lf:
        subprocess.Popen(["python3", f"{BASE}/libro_lora_v2.py", tip, path, out], stdout=lf, stderr=lf, stdin=subprocess.DEVNULL, start_new_session=True, cwd=BASE)
    return 200, {"avviato": True, "tipologia": tip, "finestre": fin, "risultati": out, "nota": "riprende da dove si era fermato se il file esiste"}

def stop():
    for p in ("coda_srd.sh", "libro_lora_v2.py"): subprocess.run(["pkill", "-f", p])
    time.sleep(2); subprocess.run(["pkill", "-f", "llama-server.*--port 8099"])
    return 200, {"fermato": True, "processi_rimasti": processi()}

class H(BaseHTTPRequestHandler):
    def _r(self, code, obj):
        b = json.dumps(obj, ensure_ascii=False).encode(); self.send_response(code); self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
    def _ok(self):
        if self.client_address[0] not in IP_AMMESSI: self._r(403, {"errore": "origine non ammessa"}); return False
        if self.headers.get("X-Runner-Token") != TOKEN: self._r(401, {"errore": "segreto errato"}); return False
        return True
    def do_GET(self):
        if self.path == "/salute": return self._r(200, {"ok": True})
        if not self._ok(): return
        if self.path == "/stato":
            try: return self._r(200, stato())
            except Exception as e: return self._r(500, {"errore": str(e)})
        self._r(404, {"errore": "non trovato"})
    def do_POST(self):
        if not self._ok(): return
        n = int(self.headers.get("Content-Length") or 0)
        try: body = json.loads(self.rfile.read(n) or b"{}")
        except Exception: return self._r(400, {"errore": "JSON non valido"})
        try:
            if self.path == "/lancia": return self._r(*lancia(body))
            if self.path == "/stop": return self._r(*stop())
        except Exception as e: return self._r(500, {"errore": str(e)})
        self._r(404, {"errore": "non trovato"})
    def log_message(self, *a): pass

if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8095), H).serve_forever()
