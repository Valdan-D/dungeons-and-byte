# n8n Workflow

Orchestration workflow for the Dungeons & Byte digitization pipeline.

## Import

1. Open n8n → **Settings → Import Workflow**
2. Select `Dungeons_and_Byte.workflow.json`
3. Replace the two host placeholders in the HTTP Request nodes (see Configuration below)
4. Trigger manually by clicking **Test Workflow**

## Configuration

After import, find-and-replace these placeholders in the workflow node parameters:

| Placeholder | Replace with | Service |
|---|---|---|
| `DOCPARSER_HOST` | IP or hostname of DocParser container | port 5000 |
| `OLLAMA_HOST` | IP or hostname of Ollama container | port 11434 |

The shared projects folder must be mounted inside the n8n container at `/shared/projects/`.

## Node map

```
trigger manuale
  └─ Read/Write Files from Disk1      # read PDF filename from pool/
  └─ Code in JavaScript1              # build projectName and pdfPath
  └─ crea-cartelle                    # POST /setup → create folder structure
  └─ inizializza-qa                   # reset qa/errors/<project>.json for this run
  └─ Splittta                         # POST /split → split PDF into pages
  └─ Split Out                        # fan-out: one item per page
  └─ Loop Over Items                  # process pages one at a time
       │
       ├─ [each page]
       │   └─ promt_per_render        # build render request
       │   └─ render_page             # POST /render_page → page as PNG
       │   └─ verifica-immagini       # check if page has images
       │       ├─ splitta-immagini    # fan-out image elements
       │       │   └─ estrae-immagini # POST /crop/extract → save cropped images
       │       │   └─ salva-immagini  # write image files to disk
       │       └─ pulizia-markdown    # regex pre-cleaning (hyphen joins, OCR garbage)
       │           └─ pulizia-llm-ollama  # qwen2.5:3b cleanup + language fallback
       │               └─ verifica-pagina # QA: detect table garbage + wrong language
       │                   └─ pagine-da-binario-a-markdown  # convert to file
       │                       └─ salva-pagine-markdown     # write markdown/page_N.md
       │                           └─ Merge ──────────────────────────┐
       │                                                               │
       └─ [loop feedback] ◄────────────────────────────────────────────┘
       │
       └─ [done]
           └─ controllo-errori        # check qa/errors → apply solutions or STOP
           └─ divisione immagini-markdown  # filter: keep only .md items
           └─ filtro-e-assemblaggio-md    # assemble pages into final document
               ├─ Aggregate
               │   └─ salva-markdown-totale  # write <project>.md
               └─ split-capitoli            # split into chapter files
```

## QA system

Three nodes manage the quality assurance loop:

### `inizializza-qa`
Runs once at workflow start. Resets `qa/errors/<projectName>.json` to an empty array,
so each run starts with a clean error log.

### `verifica-pagina`
Runs for every page after LLM cleanup. Detects:
- **`table_garbage`**: HTML table cells where the ratio of letters to total characters
  is below 40% and the cell contains symbols/numbers. Typical cause: PDF icons (C, R, M
  for concentration/ritual/material) that OCR renders as random character sequences.
- **`wrong_language`**: Page content contains unambiguous Portuguese or Spanish words,
  indicating the LLM translated the text despite instructions not to.

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
| No errors in log | Pass through, assembly continues normally |
| Errors found + `qa/solutions.json` has matching entries | Apply corrections to `markdown/page_N.md` files and in-memory items, then continue |
| Errors found + no matching solution | **STOP** with a descriptive error listing every unsolved anomaly |

To resolve a stop: add entries to `qa/solutions.json` and re-run the workflow.
```json
[
  { "wrong": "O00rP00ì0)ìz£0 010", "correct": "C" },
  { "wrong": "OrPO", "correct": "R" }
]
```
Solutions are global and accumulate across projects — entries added for one manual
are automatically applied to all future runs.

## Requirements

- n8n with filesystem access to `/shared/projects/`
- DocParser running at `DOCPARSER_HOST:5000`
- Ollama running at `OLLAMA_HOST:11434` with `qwen2.5:3b` pulled
- No execution timeout set on the workflow (405-page manuals take ~90 minutes)
