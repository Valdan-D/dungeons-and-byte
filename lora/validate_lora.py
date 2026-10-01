"""
Validazione sistematica degli adapter LoRA D&B su libri di validazione mai visti in
training (bestiario_png sostituito da bestiario_new + bestiario_old dal 29/09/2026;
bestiario_old non ha ground truth di validazione se i libri di validazione sono tutti 5e).

I libri di validazione si dichiarano in `validation_books.json` (non incluso nel repo:
dipende da quali manuali si possiedono): mappa "cartella progetto" -> "suffisso file
ground truth". Esempio con il solo SRD, rilasciato con licenza CC:
  {"IT_SRD_CC_v5.2.1 - Manuale - dnd - 5.5": "IT_SRD_CC_v5.2.1"}

Per ogni tipologia:
  1. Avvia llama-server puntato sul .gguf corrispondente
  2. Per ogni entità nel ground truth di validazione:
       - trova il file capitolo sorgente corrispondente (via slug del nome)
       - chiama il modello con lo stesso system prompt usato in training
       - cerca l'entità corrispondente nell'array JSON restituito
       - confronta i campi con il ground truth
  3. Ferma llama-server, aggrega i risultati, passa alla tipologia successiva

Uso:
  python3 validate_lora.py                       # tutte le tipologie, tutti i libri
  python3 validate_lora.py --tipologia incantesimo --limit 10   # test rapido

NOTA: NON esegue nulla da solo finché non lo si lancia esplicitamente.
Questo file è per revisione prima di un run reale.
"""

import argparse
import difflib
import json
import re
import subprocess
import time
import unicodedata
from pathlib import Path

import requests

PROJECTS_ROOT = Path("/shared/projects")
VALIDAZIONE_DIR = PROJECTS_ROOT / "validazione"
MODELS_ROOT = Path("/opt/dnb-lora-models/adapters")
LLAMA_SERVER_BIN = "/opt/llamacpp-paddleocr/vulkan/llama-b10242/llama-server"
PORT = 8099  # porta dedicata alla validazione, diversa da quella di produzione (8090)

# mappa esplicita cartella libro -> suffisso usato nei nomi dei file di validazione
# (verificato con `ls /shared/projects/validazione/`, non derivabile in modo affidabile dal nome cartella)
LIBRI_VALIDAZIONE = json.loads((Path(__file__).parent / "validation_books.json").read_text(encoding="utf-8"))

# stessi system prompt usati nel dataset di training (da dataset_consolidato/*_train.jsonl)
SYSTEM_PROMPTS = {
    "bestiario_new": "Sei un motore di estrazione dati per manuali D&D. Analizza il testo fornito ed estrai tutte le entità di tipo 'bestiario_new' presenti, restituendo un array JSON. Ogni entità deve seguire questo schema di riferimento: [\"gdr\", \"versione\", \"tipologia\", \"nome\", \"caratteristiche\", \"descrizione\", \"stazza_peso (fantasma)\", \"velocita (fantasma)\", \"resistenza_vulnerabilita_immunita (fantasma)\", \"allineamento (fantasma)\"]. Se non ci sono entità di questo tipo nel testo, restituisci un array vuoto [].",
    "bestiario_old": "Sei un motore di estrazione dati per manuali D&D. Analizza il testo fornito ed estrai tutte le entità di tipo 'bestiario_old' presenti (mostri e creature di edizioni classiche/BECMI/AD&D, non personaggi giocanti/PNG umani), restituendo un array JSON. Ogni entità deve seguire questo schema di riferimento: [\"gdr\", \"versione\", \"tipologia\", \"nome\", \"descrizione\"]. Il campo 'descrizione' contiene tutto il testo di gioco così come appare nella fonte (statistiche incluse, se presenti), riportato per intero: i formati delle statistiche variano da edizione a edizione e non vanno scomposti in sotto-campi. Se non ci sono entità di questo tipo nel testo, restituisci un array vuoto [].",
    "incantesimo": "Sei un motore di estrazione dati per manuali D&D. Analizza il testo fornito ed estrai tutte le entità di tipo 'incantesimo' presenti, restituendo un array JSON. Ogni entità deve seguire questo schema di riferimento: [\"gdr\", \"versione\", \"tipologia\", \"nome\", \"livello\", \"scuola\", \"tempo_di_lancio\", \"gittata\", \"area\", \"componenti\", \"durata\", \"classi\", \"tag (fantasma: concentrazione/rituale/altro)\", \"descrizione\"]. Se non ci sono entità di questo tipo nel testo, restituisci un array vuoto [].",
    "oggetto": "Sei un motore di estrazione dati per manuali D&D. Analizza il testo fornito ed estrai tutte le entità di tipo 'oggetto' presenti, restituendo un array JSON. Ogni entità deve seguire questo schema di riferimento: [\"gdr\", \"versione\", \"tipologia (arma / oggetto_magico / oggetto_comune)\", \"nome\", \"categoria\", \"costo\", \"peso\", \"danno (fantasma, armi)\", \"proprieta (fantasma, armi)\", \"rarita (fantasma, oggetti magici)\", \"sintonizzazione (fantasma, oggetti magici)\", \"descrizione (copia pari pari)\"]. Se non ci sono entità di questo tipo nel testo, restituisci un array vuoto [].",
    "specie": "Sei un motore di estrazione dati per manuali D&D. Analizza il testo fornito ed estrai tutte le entità di tipo 'specie' presenti, restituendo un array JSON. Ogni entità deve seguire questo schema di riferimento: [\"gdr\", \"versione\", \"tipologia\", \"nome\", \"variante_di (per sottospecie)\", \"caratteristiche\", \"bonus_caratteristiche\", \"taglia\", \"velocita\", \"tratti_razziali_tag (con eventuali sinonimi)\", \"linguaggi\", \"descrizione\"]. Se non ci sono entità di questo tipo nel testo, restituisci un array vuoto [].",
    "classe": "Sei un motore di estrazione dati per manuali D&D. Analizza il testo fornito ed estrai tutte le entità di tipo 'classe' presenti, restituendo un array JSON. Ogni entità deve seguire questo schema di riferimento: [\"gdr\", \"versione\", \"tipologia\", \"nome\", \"variante_di (per sottoclassi)\", \"dado_vita\", \"caratteristica_primaria\", \"competenze\", \"progressione (copia pari pari per livello)\", \"tag\", \"descrizione\"]. Se non ci sono entità di questo tipo nel testo, restituisci un array vuoto [].",
    "background": "Sei un motore di estrazione dati per manuali D&D. Analizza il testo fornito ed estrai tutte le entità di tipo 'background' presenti, restituendo un array JSON. Ogni entità deve seguire questo schema di riferimento: [\"gdr\", \"versione\", \"tipologia\", \"nome\", \"competenze\", \"equipaggiamento_iniziale (copia pari pari, incluse opzioni A/B)\", \"talento (fantasma, 5.5e)\", \"tratto_caratteristico\", \"tag\", \"descrizione\"]. Se non ci sono entità di questo tipo nel testo, restituisci un array vuoto [].",
    "luogo": "Sei un motore di estrazione dati per manuali D&D. Analizza il testo fornito ed estrai tutte le entità di tipo 'luogo' presenti, restituendo un array JSON. Ogni entità deve seguire questo schema di riferimento: [\"gdr\", \"versione\", \"tipologia\", \"nome\", \"descrizione (copia pari pari)\"]. Se non ci sono entità di questo tipo nel testo, restituisci un array vuoto [].",
    "regola": "Sei un motore di estrazione dati per manuali D&D. Analizza il testo fornito ed estrai tutte le entità di tipo 'regola' presenti, restituendo un array JSON. Ogni entità deve seguire questo schema di riferimento: [\"gdr\", \"versione\", \"tipologia\", \"nome\", \"descrizione (copia pari pari)\"]. Se non ci sono entità di questo tipo nel testo, restituisci un array vuoto [].",
}


def slugify(name: str) -> str:
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    name = re.sub(r"[^a-zA-Z0-9]+", "", name).lower()
    return name


def trova_file_sorgente(libro_dir: Path, nome_entita: str) -> Path | None:
    """Cerca il file capitolo corrispondente a un'entità per nome, dentro capitoli_ricapitolati/."""
    capitoli_dir = libro_dir / "capitoli_ricapitolati"
    if not capitoli_dir.is_dir():
        return None
    target_slug = slugify(nome_entita)
    # 1. match esatto sullo slug del nome file (dopo il prefisso numerico "NNNN-")
    for f in capitoli_dir.glob("*.md"):
        file_slug = slugify(re.sub(r"^\d+-", "", f.stem))
        if file_slug == target_slug:
            return f
    # 2. fallback: cerca il nome come intestazione "## Nome" dentro i file (case-insensitive)
    for f in capitoli_dir.glob("*.md"):
        try:
            text = f.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if re.search(rf"^#+\s*{re.escape(nome_entita)}\s*$", text, re.IGNORECASE | re.MULTILINE):
            return f
    return None


def avvia_llama_server(gguf_path: Path) -> subprocess.Popen:
    proc = subprocess.Popen(
        [
            LLAMA_SERVER_BIN,
            "-m", str(gguf_path),
            "--port", str(PORT),
            "--host", "127.0.0.1",
            "--temp", "0.1",
            "-ngl", "99",
            "--ctx-size", "8192",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    # attende che risponda
    for _ in range(60):
        try:
            r = requests.get(f"http://127.0.0.1:{PORT}/health", timeout=2)
            if r.status_code == 200:
                return proc
        except requests.RequestException:
            pass
        time.sleep(2)
    proc.terminate()
    raise RuntimeError(f"llama-server non risponde dopo 120s per {gguf_path}")


def chiama_modello(system_prompt: str, testo: str) -> str:
    body = {
        "model": "local",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": testo},
        ],
        "temperature": 0.1,
        "max_tokens": 1500,  # senza limite il modello puo' non emettere mai lo stop token e girare per minuti
        "chat_template_kwargs": {"enable_thinking": False},  # senza, Qwen3 puo' restare bloccato su <think> ed esaurire il budget senza mai rispondere
    }
    r = requests.post(f"http://127.0.0.1:{PORT}/v1/chat/completions", json=body, timeout=120)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


def estrai_json_array(raw: str):
    """Rimuove eventuali fence ```json ... ``` e fa il parse, ritorna None se non valido."""
    cleaned = re.sub(r"^```(json)?|```$", "", raw.strip(), flags=re.MULTILINE).strip()
    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, list):
            return parsed
        return None
    except json.JSONDecodeError:
        return None


def trova_entita_per_nome(entita_list: list, nome_target: str) -> dict | None:
    target_slug = slugify(nome_target)
    for e in entita_list:
        if not isinstance(e, dict):
            continue
        nome = e.get("nome", "")
        if slugify(nome) == target_slug:
            return e
    # fallback: match parziale
    for e in entita_list:
        if not isinstance(e, dict):
            continue
        nome = e.get("nome", "")
        if target_slug and (target_slug in slugify(nome) or slugify(nome) in target_slug):
            return e
    return None


def similarita_testo(a: str, b: str) -> float:
    a_norm = re.sub(r"\s+", " ", (a or "").strip().lower())
    b_norm = re.sub(r"\s+", " ", (b or "").strip().lower())
    if not a_norm and not b_norm:
        return 1.0
    return difflib.SequenceMatcher(None, a_norm, b_norm).ratio()


def valida_tipologia(tipologia: str, limit: int | None = None) -> dict:
    gguf_path = MODELS_ROOT / f"{tipologia}_gguf_gguf" / "qwen3-4b.Q8_0.gguf"
    if not gguf_path.is_file():
        raise FileNotFoundError(f"Modello non trovato: {gguf_path}")
    if tipologia not in SYSTEM_PROMPTS:
        raise ValueError(f"System prompt mancante per '{tipologia}' — vedi TODO nello script")

    system_prompt = SYSTEM_PROMPTS[tipologia]
    risultati = []

    print(f"[{tipologia}] avvio llama-server su {gguf_path}...", flush=True)
    proc = avvia_llama_server(gguf_path)
    try:
        for nome_libro_cartella, nome_libro_file in LIBRI_VALIDAZIONE.items():
            libro_dir = PROJECTS_ROOT / nome_libro_cartella
            val_file = VALIDAZIONE_DIR / f"{tipologia}_validazione_{nome_libro_file}.json"
            if not val_file.is_file():
                print(f"  [{tipologia}] nessun file di validazione per {nome_libro_cartella} ({val_file.name}), salto", flush=True)
                continue

            ground_truth = json.loads(val_file.read_text(encoding="utf-8"))
            entita_gt = ground_truth.get("entita", ground_truth if isinstance(ground_truth, list) else [])

            for entita in entita_gt[:limit] if limit else entita_gt:
                nome = entita.get("nome", "")
                src_file = trova_file_sorgente(libro_dir, nome)
                esito = {"libro": nome_libro_cartella, "nome": nome}

                if src_file is None:
                    esito["stato"] = "file_sorgente_non_trovato"
                    risultati.append(esito)
                    print(f"  [{tipologia}] {nome}: {esito['stato']}", flush=True)
                    continue

                testo_sorgente = src_file.read_text(encoding="utf-8", errors="ignore")
                if len(testo_sorgente) > 20000:
                    esito["stato"] = "sorgente_troppo_lungo_saltato"
                    risultati.append(esito)
                    print(f"  [{tipologia}] {nome}: {esito['stato']}", flush=True)
                    continue

                try:
                    raw_output = chiama_modello(system_prompt, testo_sorgente)
                except Exception as e:
                    esito["stato"] = f"errore_chiamata: {e}"
                    risultati.append(esito)
                    print(f"  [{tipologia}] {nome}: {esito['stato']}", flush=True)
                    continue

                parsed = estrai_json_array(raw_output)
                if parsed is None:
                    esito["stato"] = "json_non_valido"
                    esito["raw"] = raw_output[:500]
                    risultati.append(esito)
                    print(f"  [{tipologia}] {nome}: {esito['stato']}", flush=True)
                    continue

                trovata = trova_entita_per_nome(parsed, nome)
                if trovata is None:
                    esito["stato"] = "entita_non_trovata_nell_output"
                    esito["output_completo"] = parsed
                    risultati.append(esito)
                    print(f"  [{tipologia}] {nome}: {esito['stato']}", flush=True)
                    continue

                esito["stato"] = "ok"
                esito["campi_fissi_corretti"] = (
                    trovata.get("gdr") == "dnd" and trovata.get("tipologia") == entita.get("tipologia", tipologia)
                )
                esito["descrizione_similarita"] = similarita_testo(
                    entita.get("descrizione", ""), trovata.get("descrizione", "")
                )
                esito["estratto"] = trovata
                risultati.append(esito)

                print(f"  [{tipologia}] {nome}: {esito['stato']}"
                      + (f" (sim. descrizione: {esito.get('descrizione_similarita', 0):.2f})" if esito["stato"] == "ok" else ""),
                      flush=True)
    finally:
        proc.terminate()
        proc.wait(timeout=15)

    # aggregazione
    totale = len(risultati)
    ok = sum(1 for r in risultati if r["stato"] == "ok")
    sim_media = (
        sum(r.get("descrizione_similarita", 0) for r in risultati if r["stato"] == "ok") / ok
        if ok else 0
    )
    return {
        "tipologia": tipologia,
        "totale_entita_testate": totale,
        "estratte_correttamente": ok,
        "percentuale_successo": round(100 * ok / totale, 1) if totale else 0,
        "similarita_descrizione_media": round(sim_media, 3),
        "dettaglio": risultati,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--tipologia", default=None, help="Se omesso, valida tutte le tipologie con prompt disponibile")
    parser.add_argument("--limit", type=int, default=None, help="Limita il numero di entità per libro (per test rapidi)")
    parser.add_argument("--output", default="/tmp/validazione_risultati.json")
    args = parser.parse_args()

    tipologie = [args.tipologia] if args.tipologia else list(SYSTEM_PROMPTS.keys())

    report = {}
    for t in tipologie:
        report[t] = valida_tipologia(t, limit=args.limit)
        # salvataggio incrementale: se il processo viene interrotto, il lavoro fatto finora non si perde
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        print(f"[{t}] salvato in {args.output} ({len(report)}/{len(tipologie)} tipologie fatte finora)", flush=True)

    print("\n=== RIEPILOGO ===")
    for t, r in report.items():
        print(f"{t}: {r['estratte_correttamente']}/{r['totale_entita_testate']} ({r['percentuale_successo']}%), similarità descrizione media {r['similarita_descrizione_media']}")
