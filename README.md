# 🐉 Dungeons & Byte

A self-hosted pipeline to digitize and index tabletop RPG manuals.
Transforms PDF documents into structured Markdown, ready for RAG ingestion or full-text search.

## What it does

- Detects whether a PDF is native or scanned
- Splits PDFs into single pages for parallel processing
- Extracts text using Unstructured.io (hi-res, YOLOX layout model)
- Falls back to Tesseract OCR for scanned pages
- Extracts and crops images with coordinate mapping
- Cleans OCR output with regex pre-processing + LLM post-processing (qwen2.5:3b via Ollama)
- Detects and logs OCR anomalies (table garbage, wrong language) to a QA file
- Applies manual corrections from `qa/solutions.json` before final assembly
- Creates a structured project folder for each manual

## v2.0 — Single-pipeline architecture (PaddleOCR-VL)

The v1.x architecture (below) grew a hard fork between native and scanned PDFs
(different engines, different bugs, different fixes) plus ~20 accumulated
one-off heuristics in `manifest_engine.py`/`parse.py` for edge cases found
one book at a time (decorative garbage headings, monster stat blocks read as
titles, TOC dot-leaders, page numbers read as headings, running headers,
font-glyph ambiguity, h2 calibration per book, ...). Every fix reduced one
symptom without touching the underlying issue: font-size/geometry heuristics
were being asked to make judgment calls that require actual semantic
understanding of the page.

v2.0 replaces the whole native/scan split and the heuristic pile with a
**single self-hosted vision-language model** ([PaddleOCR-VL-1.6](https://huggingface.co/PaddlePaddle/PaddleOCR-VL-1.6),
~0.9B params) that does layout detection + OCR + table/structure recognition
in one pass, always working from the rendered page **image** — so native and
scanned PDFs go through the identical code path. Kept intentionally simple
(KISS): one model, one gateway, one n8n loop, no per-book special cases.

```mermaid
graph TD
    A[PDF Manuale] --> B[n8n - Dungeons_and_Byte-V_2.0]
    B --> C[crea-cartelle - paddleocr-gateway /setup]
    C --> D[conta-pagine - pdfinfo]
    D --> E[Loop Over Items]
    E -->|pagina gia' fatta| E
    E -->|nuova| F[verifica-riavvio-periodico-vlm - ogni 15 pagine]
    F --> G[paddleocr-gateway :8091 /parse_page]
    G --> H[render pagina a immagine - PyMuPDF]
    H --> I[PaddleOCR-VL via llama-server :8090]
    I --> J[markdown + immagini salvate sul progetto]
    J --> E
    E -->|loop completo| K[filtro-e-assemblaggio-md-v2]
    K --> L[applica-fix-manuali - riusato]
    L --> M[Markdown + capitoli strutturati]
```

**Deliberately kept local, not cloud.** A cloud LLM (tested with
`groq/llama-3.3-70b-versatile`) produced excellent results, but the free-tier
throughput (~30-40 pages/day) and the ongoing per-token cost were rejected in
favor of staying fully self-hosted. That choice had a real engineering cost:

- **Memory leak in `llama-server`**: not just an oversized initial allocation
  (fixed with `--parallel 1 --ctx-size 16384`, same fix already used for
  Surya) but genuine growth *during* use (~700MB clean → 5-7GB after a few
  hundred pages), enough to push the whole Proxmox host into swap thrashing.
  Mitigated with an unconditional restart every 15 pages
  (`verifica-riavvio-periodico-vlm`, fire-and-forget), same pattern already
  proven for Surya's own leak.
- **Deterministic decoding dead-end**: a rare, page-specific error
  (`"model produced output that does not match the expected peg-native
  format"`) where the model's constrained-grammar output gets stuck at
  `temperature=0`, more likely on pages with long continuous text blocks
  (dense multi-column pages). Mitigated with tiered retries at increasing
  temperature (`0 → 0.3 → 0.6 → 0.9`) — improves the odds but is
  probabilistic, not guaranteed; if all tiers fail the request errors out and
  the n8n execution stops (by design — a failed page should be visible, not
  silently skipped).
- **GPU contention**: the T1000 8GB is shared with other containers
  (Ollama, and formerly dots.ocr on the same box). The old
  `dots-ocr.service`/`ocr-gateway.service` grabbed the GPU ahead of the VLM
  at container boot twice before being removed entirely — the container
  (renamed `paddleocr-vl`, ex `dots-ocr`) is now dedicated to this pipeline
  only.

**Fully self-contained**: v2.0 no longer depends on the v1.x `DocParser`
service at all — the one endpoint it still used (`/setup`, project folder
creation) was reimplemented directly in the gateway (`paddleocr-vl/`), so
the whole pipeline now runs off a single container plus n8n for
orchestration.

**Known limitations, not yet solved:**
- Pages with a complex map+box layout (several room-description boxes
  scattered around a dungeon map) can still mix text from different rooms —
  the exact case that originally motivated adding Surya to v1.3. Better than
  the old fixed 2-column geometric split, but not fully solved.
- During a high-temperature retry, a monster/NPC stat block can occasionally
  be misclassified as a chapter heading instead of staying as body text
  (found once across 15 books) — same failure class the v1.x
  `_is_monster_statblock_title` heuristic targeted, not yet re-added here
  since it's rare enough to fix manually via `manual_fixes.json` if it
  recurs.

**Validated on 15 manuals** — a mix of native and scanned PDFs, spanning
original publication years from the early 1980s to 2024, ranging from
short single-session adventures to 400+ page core rulebooks.

Code: [`paddleocr-vl/`](./paddleocr-vl) (gateway + systemd units). Workflow:
[`n8n/Dungeons_and_Byte-V_2.0.workflow.json`](./n8n/Dungeons_and_Byte-V_2.0.workflow.json).

---

## v1.x Architecture (legacy)

```mermaid
graph TD
    A[PDF Manuale] --> B[n8n - Orchestrazione]
    B --> C[DocParser - Flask Microservice]
    C --> D[recognize - Scansionato o nativo]
    D --> E[setup - Crea struttura progetto]
    E --> F[split - Divide in pagine singole]
    F --> G[parse - Unstructured hi-res + YOLOX]
    F --> H[ocr - Tesseract fallback]
    G --> I[extract_images - Estrae immagini]
    G --> J[pulizia-markdown - Regex pre-cleaning]
    H --> J
    J --> K[pulizia-llm-ollama - qwen2.5:3b cleanup]
    K --> L[verifica-pagina - QA anomaly detection]
    L --> M[salva-pagine-markdown]
    M --> N[controllo-errori - Apply solutions or STOP]
    N --> O[filtro-e-assemblaggio-md]
    O --> P[Markdown + capitoli strutturati]
```

## Stack

Python · Flask · PyMuPDF · Unstructured.io · Tesseract · n8n · Ollama (qwen2.5:3b)

## Project folder structure

```
/shared/projects/
├── pool/                        # PDF sources (input queue)
├── qa/
│   ├── solutions.json           # Global corrections applied cross-project
│   └── errors/
│       └── <project-name>.json  # Per-run anomaly log
└── <Project Title>/
    ├── images/                  # Extracted images
    ├── json/                    # Intermediate JSON from docparser
    ├── markdown/                # Per-page cleaned markdown (page_N.md)
    ├── pages/                   # Single-page PDFs
    └── <Project Title>.md       # Final assembled markdown
```

## QA / Correction workflow

After each run, `qa/errors/<project>.json` contains detected anomalies:

```json
[
  {
    "page": 50,
    "type": "table_garbage",
    "context": "Blocca persone | Ammaliamento",
    "column_index": 2,
    "found": "O00rP00ì0)ìz£0 010"
  }
]
```

To fix: add entries to `qa/solutions.json` and re-run the workflow.
The `controllo-errori` node applies corrections before assembly:

```json
[
  { "wrong": "O00rP00ì0)ìz£0 010", "correct": "C" }
]
```

Solutions accumulate across runs — once added, they apply to all future projects automatically.

## DocParser endpoints

| Endpoint | Method | Description |
|---|---|---|
| `/parse_from_path` | POST | Unstructured hi-res PDF parsing (YOLOX) |
| `/ocr_from_path` | POST | Tesseract OCR fallback |
| `/split` | POST | Split PDF into single pages |
| `/setup` | POST | Create project folder structure |
| `/recognize` | POST | Detect if PDF is native or scanned |
| `/extract_images_from_path` | POST | Extract embedded images |
| `/render_page` | POST | Render scanned page as PNG |
| `/crop` | POST | Crop image regions by bounding box |
| `/split_markdown` | POST | Split markdown into chunks for RAG |
| `/correct_markdown` | POST | LLM-based markdown correction (legacy, requires LiteLLM) |
| `/dots_mocr` | POST | dots.mocr OCR via subprocess |

## n8n workflow

The current (v2.0) workflow is exported at
`n8n/Dungeons_and_Byte-V_2.0.workflow.json`. Legacy exports (v1.x, native/scan
split) are kept in the same folder for reference. Import directly into n8n
via Settings → Import Workflow.

**After import, update these placeholders in the node HTTP request URLs:**

| Placeholder | Description |
|---|---|
| `DOCPARSER_HOST` | IP or hostname of the DocParser container (port 5000) — v1.x only |
| `OLLAMA_HOST` | IP or hostname of the Ollama container (port 11434) — v1.x only |
| `PADDLEOCR_GATEWAY_HOST` | IP or hostname of the PaddleOCR-VL gateway container (port 8091) — v2.0, all nodes |

## Environment variables (DocParser)

| Variable | Default | Description |
|---|---|---|
| `DOCPARSER_BASE_PATH` | `/shared/projects` | Base path for project folders |
| `LITELLM_URL` | `http://localhost:4000/v1/chat/completions` | LiteLLM endpoint (correct_markdown) |
| `LITELLM_API_KEY` | *(required)* | API key for LiteLLM |
| `DOTS_MOCR_PYTHON` | `/opt/docparser/bin/python` | Python binary for dots.mocr |
| `DOTS_MOCR_SCRIPT` | `/opt/dots.ocr/run_inference.py` | Inference script path |

## LoRA entity extraction (experimental)

On top of the Markdown output, [`lora/`](./lora) contains the tooling to train and validate small
per-entity-type LoRA adapters that extract structured entities (spells, items, monsters, ...) from
the cleaned chapters. Training data comes from commercial manuals and is **not** included.

## Status

🚧 Work in progress — part of a larger homelab AI project.

## Notes

Deployed on LXC containers (Proxmox). GPU: NVIDIA T1000 8GB.

**Content policy:** this repository contains code only. Commercial rulebooks, their text, images and any dataset derived from them are never committed (see `.gitignore`); the only manual referenced is the CC-licensed SRD.

## Windmill (orchestrazione), LoRA v5 e classificatore (ottobre 2026)

Solo codice: nessun manuale, dataset o risultato derivato (vedi "Content policy").

- `windmill/` – script e flussi esportati da Windmill (workspace `dnb`):
  - `windmill_v3/` + `dungeons_and_byte_v3_0.flow.json`: la pipeline OCR (V3.0), stessi passi del flusso n8n; il PDF si sceglie da un menu che legge la cartella `pool/`.
  - `windmill_ricapitolando/` + `fase1.flow.json`, `fase2.flow.json`: "Ricapitolando" in due flussi separati (rilevamento capitoli e generazione dei file). L'elenco dei progetti nei flussi esportati è un segnaposto.
  - `pilota/`: script che lanciano/fermano/controllano i lavori LoRA tramite il runner (`RUNNER_TOKEN` arriva da variabile d'ambiente, mai nel codice).
- `lora/` – addestramento v5 (`train_lora_v5.py`, `export_gguf.py`), prompt delle 9 tipologie (`prompts9.json`), estrazione su un libro intero (`libro_lora_v2.py`, `coda_srd.sh`), validazione a finestre (`valida_finestre.py`) e `dnb_runner.py` (servizio con comandi fissi, accetta solo l'IP di Windmill e un segreto da `/etc/dnb-runner.env`).
- `classificatore/` – prova isolata (GPU) del classificatore multi-label GLiNER2.5-Decide: tempi e qualità zero-shot. Non ancora integrato nella pipeline.

**Configuration note:** host addresses are not hard-coded. In `windmill/` the placeholder `<GPU_HOST>` stands for the container that runs the OCR gateway (:8091) and the LoRA runner (:8095); set it to your own address in Windmill. `RUNNER_ALLOWED_IPS` (comma-separated) lists the addresses the runner accepts; `LITELLM_URL` and `SURYA_GATEWAY_URL` configure the docparser endpoints.

## Block classifier, labelling and extraction flows (October 2026)

Still code only. Windmill flows are exported as JSON; host addresses are placeholders (see the configuration note above).

- `classificatore/` – the block classifier pipeline that decides which LoRA sees which part of a book:
  - `classifica_libro.py`: splits a book (`capitoli_ricapitolati/`) into units of at most ~400 tokens that never cross two chapter files, and scores every unit for the 8 typologies with GLiNER2.5-multi-Decide plus a LoRA adapter (GPU). Writes `etichette/punteggi.json`.
  - `costruisci_finestre.py`: builds the extraction windows of one LoRA from the labelled intervals (margin of one unit, title-only files attached to their body, one card per window for monsters, windows cut on headings). CPU only.
  - `valuta_adapter.py`: evaluates an adapter on the test set with a threshold sweep.
- `windmill/etichettatura/` – flow `etichetta_libro`: start the classifier through the runner, wait, apply the threshold and merge contiguous units into line intervals (`etichette/etichette.json`). Re-running with another threshold does not use the GPU.
- `windmill/estrazione/` – flow `estrai_libro`: picks the LoRAs (bestiario new/old from the year in the manual name: 2014 or later = new; if there is no year, edition 5 or 5.5 = new), builds the windows, runs the LoRAs one at a time and collects the entities in `estrazione/entita.json`.
- `lora/dnb_runner.py` now also exposes `/classifica`, `/finestre`, `/estrai` and `/estrazione`; they refuse to start while the GPU is busy or the OCR service is running. `lora/estrazione_libro.sh` runs several LoRAs in a row on already built windows.

Measured on the CC-licensed SRD (windows from the classifier vs. fixed windows over the whole book): the seven non-monster LoRAs went from about 55 hours to about 6 hours, and the extra, wrong extractions dropped from hundreds to a handful.
