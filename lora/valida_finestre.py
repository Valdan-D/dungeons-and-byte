#!/usr/bin/env python3
"""Validazione degli adapter v5 (GGUF) su finestre MAI VISTE in training. Gira su LXC 121 con llama-server.

Uso:  python3 valida_finestre.py bestiario_new,oggetto,regola
GGUF attesi in   /opt/dnb-lora-models/v5/<tipologia>/*.gguf
Finestre in      /opt/dnb-lora-models/val_v3/<tipologia>_val.jsonl   (system/user/assistant, risposta = verita')
Stesso criterio del test di Colab: nome atteso "trovato" se uguale o contenuto in uno estratto (o viceversa).
Decodifica deterministica (temperatura 0), thinking disattivato, max 2500 token di risposta.
"""
import json, re, subprocess, sys, time, unicodedata
from collections import Counter
from pathlib import Path

import requests

BASE = Path("/opt/dnb-lora-models")
LLAMA = "/opt/llamacpp-paddleocr/vulkan/llama-b10242/llama-server"
PORT = 8099
OUT = BASE / "valida_v5.json"


def norm(s):
    s = "".join(c for c in unicodedata.normalize("NFD", s.lower()) if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", re.sub(r"\(.*?\)", "", s)).strip()


def uguali(a, t):
    return a == t or (len(a) >= 4 and len(t) >= 4 and (a in t or t in a))


def nomi_output(raw):
    """(nomi, json_valido). Se il JSON e' troncato recupera comunque i nomi con una regex."""
    txt = re.sub(r"^```(json)?|```$", "", raw.strip(), flags=re.M).strip()
    try:
        v = json.loads(txt)
        if isinstance(v, list):
            return [norm(e.get("nome", "")) for e in v if isinstance(e, dict)], True
    except json.JSONDecodeError:
        pass
    return [norm(x) for x in re.findall(r'"nome"\s*:\s*"([^"]*)"', raw)], False


def avvia(gguf):
    p = subprocess.Popen([LLAMA, "-m", str(gguf), "--port", str(PORT), "--host", "127.0.0.1", "-ngl", "99",
                          "--ctx-size", "8192"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(90):
        try:
            if requests.get(f"http://127.0.0.1:{PORT}/health", timeout=2).status_code == 200:
                return p
        except requests.RequestException:
            pass
        time.sleep(2)
    p.terminate()
    raise RuntimeError(f"llama-server non risponde per {gguf}")


def chiama(sysp, testo):
    body = {"model": "local", "messages": [{"role": "system", "content": sysp}, {"role": "user", "content": testo}],
            "temperature": 0, "max_tokens": 2500, "chat_template_kwargs": {"enable_thinking": False}}
    r = requests.post(f"http://127.0.0.1:{PORT}/v1/chat/completions", json=body, timeout=900)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


def main():
    tipologie = sys.argv[1].split(",")
    risultati = json.load(open(OUT)) if OUT.exists() else {}
    for tip in tipologie:
        ggufs = sorted((BASE / "v5" / tip).glob("*.gguf"))
        valf = BASE / "val_v3" / f"{tip}_val.jsonl"
        if not ggufs or not valf.exists():
            print(f"[{tip}] SALTATA: manca {'il GGUF' if not ggufs else 'il file di validazione'}", flush=True)
            continue
        righe = [json.loads(l) for l in open(valf)]
        print(f"\n=== {tip}: {len(righe)} finestre | {ggufs[0].name}", flush=True)
        proc = avvia(ggufs[0])
        casi = []
        try:
            for i, r in enumerate(righe, 1):
                ms = r["messages"]
                attesi = [norm(e["nome"]) for e in json.loads(ms[2]["content"])]
                t0 = time.time()
                try:
                    raw = chiama(ms[0]["content"], ms[1]["content"])
                except Exception as ex:
                    raw = f"ERRORE {ex}"
                trovati, valido = nomi_output(raw)
                ok = sum(1 for a in attesi if any(uguali(a, t) for t in trovati))
                extra = [t for t in trovati if not any(uguali(a, t) for a in attesi)]
                ciclo = any(c > 3 for c in Counter(trovati).values())
                casi.append({"attesi": attesi, "trovati": trovati, "ok": ok, "extra": extra, "json_valido": valido, "ciclo": ciclo,
                             "secondi": round(time.time() - t0, 1)})
                print(f"finestra {i}: attesi {len(attesi)} trovati {ok} | extra {len(extra)} | JSON valido: {valido}"
                      f"{' | CICLO' if ciclo else ''} ({casi[-1]['secondi']}s)", flush=True)
                if not valido or ok < len(attesi):
                    print("   attesi :", attesi[:8]); print("   trovati:", trovati[:8])
                    print("   output :", raw[:240].replace("\n", " "), flush=True)
        finally:
            proc.terminate(); proc.wait()
        pos = [c for c in casi if c["attesi"]]; neg = [c for c in casi if not c["attesi"]]
        riep = {"finestre": len(casi), "entita_attese": sum(len(c["attesi"]) for c in pos), "entita_trovate": sum(c["ok"] for c in pos),
                "extra_totali": sum(len(c["extra"]) for c in casi), "json_non_valido": sum(1 for c in casi if not c["json_valido"]),
                "cicli": sum(1 for c in casi if c["ciclo"]), "vuote": len(neg), "vuote_ok": sum(1 for c in neg if not c["trovati"] and c["json_valido"]),
                "casi": casi}
        risultati[tip] = riep
        json.dump(risultati, open(OUT, "w"), ensure_ascii=False, indent=1)
        print(f"\n>>> {tip}: entita' {riep['entita_trovate']}/{riep['entita_attese']} | finestre vuote risolte {riep['vuote_ok']}/{riep['vuote']} "
              f"| JSON non validi {riep['json_non_valido']}/{riep['finestre']} | cicli {riep['cicli']} | extra {riep['extra_totali']}", flush=True)


if __name__ == "__main__":
    main()
