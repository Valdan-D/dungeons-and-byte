#!/usr/bin/env python3
"""Esecutore dei lavori LoRA su LXC 121 (solo libreria standard). Comandi FISSI, nessuna shell, accessibile solo dall'IP di Windmill.
GET  /salute  -> ok (senza segreto)
GET  /stato   -> stato di GPU, OCR, lavori (da file .stato e log), coda
POST /lancia  {tipologia, finestre}  -> avvia libro_lora_v2.py se la GPU e' libera
POST /stop    -> ferma i lavori LoRA (e la coda automatica)
POST /classifica {progetto} -> avvia il classificatore a blocchi su un libro (se GPU libera e OCR spento)
GET  /classifica?progetto=NOME -> stato della classificazione di quel libro
POST /finestre {progetto, tipologia} -> costruisce le finestre del LoRA dagli intervalli etichettati (CPU; sposta a parte i risultati vecchi)
POST /estrai {progetto, tipologia} -> avvia il LoRA sulle finestre del libro (GPU libera, OCR spento)
GET  /estrazione?progetto=NOME -> stato delle estrazioni di quel libro"""
import json, os, re, subprocess, time, glob
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE = "/opt/dnb-lora-models"; RIS = f"{BASE}/risultati_srd"
TOKEN = os.environ["RUNNER_TOKEN"]; IP_AMMESSI = set(os.environ.get("RUNNER_ALLOWED_IPS", "127.0.0.1").split(","))
TIPI = ["bestiario_new", "bestiario_old", "oggetto", "regola", "incantesimo", "classe", "specie", "background", "luogo"]
FINESTRE = {"srd_unite": (f"{BASE}/srd_finestre_unite.json", "unite"), "srd_mostri_titoli": (f"{BASE}/srd_mostri_titoli.json", "titoli")}

CLF = "/opt/dnb-classificatore"; CLF_PY = "/opt/classificatore_test/venv/bin/python"; PROGETTI = "/shared/projects"

def progetto_ok(nome):
    """solo nomi semplici di cartelle che hanno capitoli_ricapitolati (niente percorsi, niente '..')"""
    return isinstance(nome, str) and nome and "/" not in nome and not nome.startswith(".") and os.path.isdir(f"{PROGETTI}/{nome}/capitoli_ricapitolati")

def run(args):
    return subprocess.run(args, capture_output=True, text=True, timeout=30).stdout.strip()

def processi():
    out = run(["pgrep", "-af", "libro_lora_v2.py|coda_srd.sh|classifica_libro.py"])
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

def classifica(body):
    nome = body.get("progetto")
    if not progetto_ok(nome): return 400, {"errore": "progetto non valido o senza capitoli_ricapitolati"}
    attivi = processi()
    if attivi: return 409, {"errore": "GPU occupata da un altro lavoro", "processi": attivi}
    ocr = [x for x in ("paddleocr-vlm", "paddleocr-gateway") if run(["systemctl", "is-active", x]) == "active"]
    if ocr: return 409, {"errore": "L'OCR e' attivo: fermalo prima (non lo fermo da solo)", "servizi": ocr}
    out = f"{PROGETTI}/{nome}/etichette"; os.makedirs(out, exist_ok=True)
    with open(f"{out}/classifica.log", "w") as lf:
        subprocess.Popen([CLF_PY, f"{CLF}/classifica_libro.py", nome], stdout=lf, stderr=lf, stdin=subprocess.DEVNULL, start_new_session=True, cwd=CLF)
    return 200, {"avviato": True, "progetto": nome, "risultato": f"{out}/punteggi.json"}

def stato_classifica(nome):
    if not progetto_ok(nome): return 400, {"errore": "progetto non valido"}
    f = f"{PROGETTI}/{nome}/etichette/classifica.stato"
    d = json.load(open(f)) if os.path.exists(f) else {"fase": "mai lanciata", "finito": False}
    d["in_corso"] = any(nome in p for p in processi())
    d["punteggi_presenti"] = os.path.exists(f"{PROGETTI}/{nome}/etichette/punteggi.json")
    return 200, d

def tip_ok(tip): return tip in TIPI

def finestre_libro(body):
    nome, tip = body.get("progetto"), body.get("tipologia")
    if not progetto_ok(nome): return 400, {"errore": "progetto non valido o senza capitoli_ricapitolati"}
    if not tip_ok(tip): return 400, {"errore": f"tipologia non valida: {tip}", "ammesse": TIPI}
    if not os.path.exists(f"{PROGETTI}/{nome}/etichette/etichette.json"): return 400, {"errore": "mancano le etichette: lancia prima il flusso Etichettatura"}
    d = f"{PROGETTI}/{nome}/estrazione"; os.makedirs(d, exist_ok=True); ts = time.strftime("%Y%m%d_%H%M%S")
    fin = f"{d}/finestre_{tip}.json"
    if body.get("riusa") and os.path.exists(fin) and os.path.getmtime(fin) > os.path.getmtime(f"{PROGETTI}/{nome}/etichette/etichette.json"):
        n = json.load(open(fin))   # finestre gia' costruite DOPO le ultime etichette: si riusano (e il LoRA riprende da dove era arrivato)
        return 200, {"progetto": nome, "tipologia": tip, "finestre": len(n), "token_inviati": sum(x["tok"] for x in n), "riusate": True, "file": fin}
    for ext in ("jsonl", "jsonl.stato", "log"):      # le finestre cambiano: i risultati vecchi non sono piu' riprendibili, li metto da parte
        f = f"{d}/{tip}.{ext}"
        if os.path.exists(f): os.rename(f, f"{f}.vecchio_{ts}")
    r = subprocess.run([CLF_PY, f"{CLF}/costruisci_finestre.py", nome, tip], capture_output=True, text=True, timeout=600, cwd=CLF)
    if r.returncode != 0: return 500, {"errore": "costruzione finestre fallita", "dettaglio": r.stderr[-600:]}
    return 200, json.loads(r.stdout.strip().splitlines()[-1])

def estrai(body):
    nome, tip = body.get("progetto"), body.get("tipologia")
    if not progetto_ok(nome): return 400, {"errore": "progetto non valido o senza capitoli_ricapitolati"}
    if not tip_ok(tip): return 400, {"errore": f"tipologia non valida: {tip}", "ammesse": TIPI}
    fin = f"{PROGETTI}/{nome}/estrazione/finestre_{tip}.json"
    if not os.path.exists(fin): return 400, {"errore": "mancano le finestre: usa prima /finestre"}
    attivi = processi()
    if attivi: return 409, {"errore": "GPU occupata da un altro lavoro", "processi": attivi}
    ocr = [x for x in ("paddleocr-vlm", "paddleocr-gateway") if run(["systemctl", "is-active", x]) == "active"]
    if ocr: return 409, {"errore": "L'OCR e' attivo: fermalo prima (non lo fermo da solo)", "servizi": ocr}
    out = f"{PROGETTI}/{nome}/estrazione/{tip}.jsonl"
    with open(f"{PROGETTI}/{nome}/estrazione/{tip}.log", "a") as lf:
        subprocess.Popen(["python3", f"{BASE}/libro_lora_v2.py", tip, fin, out], stdout=lf, stderr=lf, stdin=subprocess.DEVNULL, start_new_session=True, cwd=BASE)
    return 200, {"avviato": True, "progetto": nome, "tipologia": tip, "risultati": out, "nota": "riprende da dove si era fermato se il file esiste"}

def stato_estrazione(nome):
    if not progetto_ok(nome): return 400, {"errore": "progetto non valido"}
    d = f"{PROGETTI}/{nome}/estrazione"; lav = []
    for f in sorted(glob.glob(f"{d}/*.jsonl.stato")):
        try: x = json.load(open(f))
        except Exception: continue
        tip = os.path.basename(f).replace(".jsonl.stato", ""); log = f"{d}/{tip}.log"
        x["finito"] = os.path.exists(log) and "FINE_LIBRO_LORA" in open(log, errors="ignore").read()[-2000:]
        x["ultimo_aggiornamento_min_fa"] = round((time.time() - os.path.getmtime(f)) / 60, 1); lav.append(x)
    fin = {os.path.basename(f)[len("finestre_"):-5]: len(json.load(open(f))) for f in glob.glob(f"{d}/finestre_*.json")}
    return 200, {"progetto": nome, "lavori": lav, "finestre": fin, "processi_attivi": processi()}

def stop():
    for p in ("coda_srd.sh", "libro_lora_v2.py", "classifica_libro.py"): subprocess.run(["pkill", "-f", p])
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
        if self.path.startswith("/estrazione"):
            from urllib.parse import urlparse, parse_qs
            q = parse_qs(urlparse(self.path).query)
            try: return self._r(*stato_estrazione((q.get("progetto") or [""])[0]))
            except Exception as e: return self._r(500, {"errore": str(e)})
        if self.path.startswith("/classifica"):
            from urllib.parse import urlparse, parse_qs
            q = parse_qs(urlparse(self.path).query)
            try: return self._r(*stato_classifica((q.get("progetto") or [""])[0]))
            except Exception as e: return self._r(500, {"errore": str(e)})
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
            if self.path == "/classifica": return self._r(*classifica(body))
            if self.path == "/finestre": return self._r(*finestre_libro(body))
            if self.path == "/estrai": return self._r(*estrai(body))
        except Exception as e: return self._r(500, {"errore": str(e)})
        self._r(404, {"errore": "non trovato"})
    def log_message(self, *a): pass

if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8095), H).serve_forever()
