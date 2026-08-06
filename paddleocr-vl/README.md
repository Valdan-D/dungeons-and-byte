# PaddleOCR-VL gateway (v2.0)

Two systemd services, both running on the same container (GPU passthrough
required):

- **`paddleocr-vlm.service`** — `llama-server` (llama.cpp) serving
  [PaddleOCR-VL-1.6](https://huggingface.co/PaddlePaddle/PaddleOCR-VL-1.6)
  (~0.9B params, GGUF + mmproj) on port `8090`. This is the actual
  vision-language model doing OCR + layout + table recognition.
- **`paddleocr-gateway.service`** — `paddleocr_gateway.py`, a small Flask
  wrapper on port `8091`. Renders a single PDF page to PNG (PyMuPDF), runs it
  through the `PaddleOCRVL` Python pipeline (`vl_rec_backend=llama-cpp-server`,
  talking to the service above), saves the resulting markdown + any extracted
  images directly into the project folder, and returns the result. This is
  the endpoint the n8n workflow calls.

## Why two services

`llama-server` needs a GPU-heavy long-running process; the gateway needs
PyMuPDF + the PaddleX/PaddleOCR Python stack (CPU) and orchestrates the file
I/O. Keeping them separate means the gateway can force an unconditional
restart of just the VLM (see below) without losing its own in-memory model
state (`PaddleOCRVL` layout model, loaded once at gateway startup).

## Endpoints

**`POST /setup`**
Creates the project folder structure under `/shared/projects/<projectName>/`
(`pages/`, `images/`, `json/`, `markdown/`). Reimplements the old DocParser
`/setup` endpoint so v2.0 has no dependency on that service at all.
```json
Request:  { "projectName": "My Manual - dnd - 2024" }
Response: { "status": "success", "projectName": "My Manual - dnd - 2024", "paths": ["…/pages", "…/images", "…/json", "…/markdown"] }
```

**`POST /parse_page`**
Renders one PDF page and runs it through the full PaddleOCR-VL pipeline.
Retries at increasing `temperature` (`0 → 0.3 → 0.6 → 0.9`) if the model hits
a deterministic-decoding dead-end (`"model produced output that does not
match the expected peg-native format"`, more likely on pages with long
continuous text blocks). If every tier still fails, the page is *not* saved
and the request errors — a failed page should stop the run, not be silently
skipped. Images are copied into `<project>/images/img_pag_N_imgM.<ext>` and
referenced from the markdown at their real position on the page (not grouped
separately, unlike the v1.x pipeline). Pages with no text and no detectable
image region (e.g. a full-page illustration the layout model doesn't
segment) fall back to saving the entire rendered page as one image, so
content is never silently dropped.
```json
Request:  { "pdfPath": "/shared/projects/pool/manual.pdf", "projectName": "My Manual", "page": 1 }
Response: { "status": "success", "page": 1, "projectName": "My Manual",
            "fileName": "page_1.md", "filePath": "…/markdown/page_1.md", "markdown": "…" }
```

**`POST /restart_vlm`**
Unconditionally restarts `paddleocr-vlm` and waits (up to 60s) for it to
report healthy again. Called by the n8n workflow every 15 pages
(`verifica-riavvio-periodico-vlm`, fire-and-forget with a short client
timeout) to contain a memory leak in `llama-server` that grows with request
count regardless of the `--ctx-size` fix below.
```json
Request:  {}
Response: { "status": "ok", "ready": true }
```

**`GET /health`** → `{ "status": "ok" }` for both services.

## Setup notes

- `llama-server` must be started with `--parallel 1 --ctx-size 16384` —
  without it, the default multi-slot KV-cache allocation reserves several
  GB for parallel requests that never arrive (the gateway only ever sends
  one request at a time). Same root cause and fix already used for Surya.
- If this container previously ran something else with GPU auto-start
  (e.g. this one started life as a `dots-ocr` OCR experiment container,
  hence the `paddleocr-vl` rename), remove or disable that service entirely
  — it will compete for the GPU and can grab it before the VLM on boot.
- `/shared/projects` must be mounted on this container (`pct set <vmid>
  -mp0 /mnt/shared/projects,mp=/shared/projects`) — it isn't by default on a
  container that wasn't already part of the pipeline.
