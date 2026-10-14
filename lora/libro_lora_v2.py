#!/usr/bin/env python3
"""Passa le finestre di un libro a UN LoRA e salva ogni risposta (JSONL, riprende da dove si e' fermato).
Uso: libro_lora.py <tipologia> <finestre.json> <uscita.jsonl>"""
import json, subprocess, time, requests, os, sys
tip, FIN, OUT = sys.argv[1:4]
BASE = "/opt/dnb-lora-models"
LLAMA = "/opt/llamacpp-paddleocr/vulkan/llama-b10242/llama-server"; PORT = 8099
fin = json.load(open(FIN)); prompt = json.load(open(f"{BASE}/prompts9.json"))["prompt"][tip]
fatte = sum(1 for _ in open(OUT)) if os.path.exists(OUT) else 0
STATO = OUT + ".stato"
print(f"[{tip}] {len(fin)} finestre, gia' fatte {fatte}", flush=True)
p = subprocess.Popen([LLAMA, "-m", f"{BASE}/v5/{tip}/Qwen3-4B.Q8_0.gguf", "--port", str(PORT), "--host", "127.0.0.1", "-ngl", "99", "--ctx-size", "12288"],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
try:
    for _ in range(120):
        try:
            if requests.get(f"http://127.0.0.1:{PORT}/health", timeout=2).status_code == 200: break
        except requests.RequestException: pass
        time.sleep(2)
    t_inizio = time.time(); base = fatte
    with open(OUT, "a") as f:
        for w in fin[fatte:]:
            t0 = time.time()
            try:
                body = {"model": "l", "messages": [{"role": "system", "content": prompt}, {"role": "user", "content": w["testo"]}],
                        "temperature": 0, "max_tokens": 6000, "chat_template_kwargs": {"enable_thinking": False}}
                raw = requests.post(f"http://127.0.0.1:{PORT}/v1/chat/completions", json=body, timeout=900).json()["choices"][0]["message"]["content"]
            except Exception as ex:
                raw = f"ERRORE {ex}"
            try:
                v = json.loads(raw); ok = isinstance(v, list); nomi = [e.get("nome") for e in v if isinstance(e, dict)] if ok else []
            except Exception:
                ok = False; nomi = []
            f.write(json.dumps({"id": w["id"], "file": w["file"], "secondi": round(time.time() - t0, 1), "json_valido": ok, "nomi": nomi, "raw": raw}, ensure_ascii=False) + "\n"); f.flush()
            fatte += 1
            ritmo = (time.time() - t_inizio) / (fatte - base)
            open(STATO, "w").write(json.dumps({"tipologia": tip, "fatte": fatte, "totale": len(fin), "min_rimanenti_stima": round((len(fin) - fatte) * ritmo / 60)}))
            print(f"[{tip}] {fatte}/{len(fin)} {round(time.time()-t0,1)}s estratti={len(nomi)} json={ok}", flush=True)
finally:
    p.terminate(); p.wait()
print("FINE_LIBRO_LORA", flush=True)
