# n8n Workflow

Orchestration workflow for the Dungeons & Byte digitization pipeline.

## Import

1. Open n8n → **Settings → Import Workflow**
2. Select `Dungeons_and_Byte.workflow.json`
3. Replace the host placeholders in the HTTP Request nodes (see Configuration below)
4. Trigger manually by clicking **Test Workflow**

## Configuration

After import, find-and-replace these placeholders in the workflow node parameters:

| Placeholder | Replace with | Service |
|---|---|---|
| `DOCPARSER_HOST` | IP or hostname of DocParser container | port 5000 |
| `OLLAMA_HOST` | IP or hostname of Ollama container | port 11434 |

The shared projects folder must be mounted inside the n8n container at `/shared/projects/`.

### n8n environment requirements

The following must be set in the n8n environment (e.g. `/opt/n8n.env`):

```
NODE_FUNCTION_ALLOW_BUILTIN=fs,child_process,path
```

### System dependencies (inside n8n container)

- `poppler-utils` → provides `/bin/pdftotext` (PDF type detection)
- `pymupdf` → `pip install pymupdf` (native PDF text + image extraction)

The extraction script must be placed at `/shared/projects/tools/pdf_to_markdown.py`
(included in this repository under `n8n/pdf_to_markdown.py`).

## Node map

The workflow automatically branches based on PDF type detection:

```
trigger manuale
  └─ Read/Write Files from Disk1      # read PDF filename from pool/
  └─ Code in JavaScript1              # build projectName and pdfPath
  └─ crea-cartelle                    # POST /setup → create folder structure
  └─ inizializza-qa                   # reset qa/errors/<project>.json for this run
  └─ rileva-tipo-pdf                  # pdftotext first 3 pages → charCount
  └─ tipo-pdf-if (isNative?)
       │
       ├─ [TRUE — native digital PDF, ~seconds]
       │   └─ estrai-pagine-pdf       # run pdf_to_markdown.py → text + images at once
       │   └─ verifica-pagina-nativo  # QA check per page (no LLM involved)
       │   └─ salva-pagina-md-nativo  # write markdown/page_N.md
       │   └─ controllo-errori ──────────────────────────────────────┐
       │                                                              │
       └─ [FALSE — scanned PDF, ~hours]                              │
           └─ Splittta               # POST /split → split PDF into pages
           └─ Split Out              # fan-out: one item per page
           └─ Loop Over Items        # process pages one at a time
                │                                                     │
                ├─ [each page]                                        │
                │   └─ promt_per_render      # build render request   │
                │   └─ render_page           # POST /render_page → PNG│
                │   └─ verifica-immagini     # check for images       │
                │       ├─ splitta-immagini  # fan-out image elements │
                │       │   └─ estrae-immagini  # crop + save images  │
                │       │   └─ salva-immagini   # write image files   │
                │       └─ pulizia-markdown  # regex pre-cleaning     │
                │           └─ pulizia-llm-ollama  # qwen2.5:3b       │
                │               └─ verifica-pagina  # QA per page     │
                │                   └─ pagine-da-binario-a-markdown   │
                │                       └─ salva-pagine-markdown      │
                │                           └─ Merge ─────────────── │
                │                                                      │
                └─ [done] → controllo-errori ─────────────────────────┘
                                │
                                └─ filtro-e-assemblaggio-md
                                    ├─ Aggregate (custom code node)
                                    │   └─ salva-markdown-totale   # write <project>.md
                                    └─ split-capitoli              # split into chapters
```

### Native PDF detection

`rileva-tipo-pdf` runs `/bin/pdftotext` on the first 3 pages and counts non-whitespace
characters. If `charCount > 300` → native digital PDF (skip OCR entirely).

### Native PDF extraction (`pdf_to_markdown.py`)

Uses PyMuPDF (fitz) with font-size-based heading detection — no LLM, no OCR:

- `body_size` = median font size of each page
- `≥ 1.7× body_size` → `##` (chapter heading)
- `≥ 1.3× body_size` → `###` (section heading)
- Bold spans → `**text**`
- Fragmented headings guard: lines ending with Italian prepositions/articles are
  demoted to body text to avoid splitting mid-phrase headings across two lines
- Images: saved as `img_pag_{N}_{idx}.{ext}` in the project's `images/` folder
- Output: JSON array `[{page: N, markdown: "...", images: ["filename", ...]}]`

Performance: ~4–10 seconds for 400-page manuals (vs hours for OCR path).

## QA system

Three nodes manage the quality assurance loop:

### `inizializza-qa`
Runs once at workflow start. Resets `qa/errors/<projectName>.json` to an empty array.

### `verifica-pagina` / `verifica-pagina-nativo`
Runs for every page after processing. Detects:
- **`table_garbage`**: HTML table cells where letters-to-total-chars ratio < 40% and
  the cell contains symbols/numbers. Typical cause: PDF icons (C, R, M for
  concentration/ritual/material) rendered as random character sequences by OCR.
- **`wrong_language`**: Page content contains unambiguous Portuguese or Spanish words,
  indicating the LLM translated the text despite instructions.
- **`suspicious_heading`**: A heading contains a word that matches a known Italian
  common noun ending (e.g. `-azioni`, `-zione`) — may be a false split.

Detected anomalies are appended to `qa/errors/<projectName>.json`:
```json
{
  "page": 50,
  "type": "table_garbage",
  "context": "Blocca persone | Ammaliamento",
  "column_index": 2,
  "found": "O00rP00ì0)ìz£0 010"
}
```

### `controllo-errori`
Runs once after the loop completes. Three outcomes:

| Condition | Outcome |
|---|---|
| No errors | Pass through, assembly continues |
| Errors + matching entry in `solutions.json` | Apply corrections, then continue |
| Errors + no matching solution | **STOP** with full error listing |

To resolve a stop: add entries to `qa/solutions.json` and re-run:
```json
[
  { "wrong": "O00rP00ì0)ìz£0 010", "correct": "C" },
  { "wrong": "Azioni", "correct": "Azioni", "type": "identity" }
]
```
`"type": "identity"` marks false positives (the heading is correct as-is).
Solutions are global and accumulate across projects.

## Requirements

- n8n with filesystem access to `/shared/projects/`
- DocParser running at `DOCPARSER_HOST:5000` (scanned path only)
- Ollama running at `OLLAMA_HOST:11434` with `qwen2.5:3b` pulled (scanned path only)
- `NODE_FUNCTION_ALLOW_BUILTIN=fs,child_process,path` in n8n env
- `poppler-utils` and `pymupdf` installed in the n8n container
- `pdf_to_markdown.py` at `/shared/projects/tools/pdf_to_markdown.py`
