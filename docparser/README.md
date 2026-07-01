# DocParser

Flask microservice that handles PDF parsing, OCR, image extraction, and markdown processing.
It acts as the backend for the n8n orchestration workflow.

## Endpoints

### PDF processing

**`POST /recognize`**
Detects whether a PDF is native (text-based) or scanned (image-based).
Samples the first 5 pages and checks for extractable text.
```json
Request:  { "pdfPath": "/shared/projects/pool/manual.pdf" }
Response: { "status": "success", "is_scanned": false, "checked_pages": 5, "total_pages": 120 }
```

**`POST /setup`**
Creates the project folder structure under `/shared/projects/<projectName>/`.
```json
Request:  { "projectName": "My Manual - dnd - 2024" }
Response: { "status": "success", "paths": ["…/pages", "…/images", "…/json", "…/markdown"] }
```

**`POST /split`**
Splits a PDF into single-page PDF files saved to `<project>/pages/`.
```json
Request:  { "pdfPath": "/shared/projects/pool/manual.pdf", "projectName": "My Manual" }
Response: { "status": "success", "total_pages": 120, "pages": [{ "page": 1, "path": "…/page_1.pdf" }] }
```

**`POST /parse_from_path`**
Runs Unstructured.io hi-res parsing (YOLOX layout model) on a single-page PDF.
Returns markdown text and element list with bounding boxes.
```json
Request:  { "pdfPath": "…/pages/page_1.pdf", "page": 1 }
Response: { "status": "success", "markdown": "…", "elements": […], "method": "local_venv_unstructured_hi_res" }
```

**`POST /ocr_from_path`**
Tesseract OCR fallback for scanned pages. Renders at ~216 DPI before OCR.
```json
Request:  { "pdfPath": "…/pages/page_1.pdf", "page": 1, "lang": "ita" }
Response: { "status": "success", "text": "…", "is_scanned": true }
```

### Image extraction

**`POST /extract_images_from_path`**
Extracts embedded images from a native PDF page and saves them as PNG.
```json
Request:  { "pdfPath": "…/pages/page_1.pdf", "projectName": "My Manual", "page": 1 }
Response: { "status": "success", "total_images": 2, "images": [{ "index": 1, "path": "…", "ext": "png" }] }
```

**`POST /render_page`**
Renders a full scanned page as a PNG at 200 DPI. Used when `is_scanned=true`.
```json
Request:  { "pdfPath": "…/pages/page_1.pdf", "projectName": "My Manual", "page": 1 }
Response: { "status": "success", "path": "…/images/page_1_scan.png" }
```

**`POST /crop/extract`**
Crops a region from a PDF page using bounding box coordinates from Unstructured.io.
Handles the coordinate scaling between Unstructured's layout space and the rendered image.
```json
Request:  { "pdfPath": "…", "page": 1, "left": 100, "top": 200, "width": 300, "height": 150,
            "layout_width": 2885, "layout_height": 3754 }
Response: PNG binary stream
```

### Markdown processing

**`POST /split_markdown`**
Splits a markdown text into chunks ≤1500 chars, respecting natural break points
(headings → paragraphs → sentences). Used for RAG ingestion.
```json
Request:  { "markdown": "…", "page": 1, "projectName": "My Manual", "pageType": "standard", "system": "D&D 5e" }
Response: { "status": "success", "chunks": ["…", "…"], "total_chunks": 3 }
```

**`POST /correct_markdown`** *(legacy)*
Splits markdown into chunks and sends each to LiteLLM for correction.
Requires `LITELLM_URL` and `LITELLM_API_KEY` environment variables.
Superseded by the `pulizia-llm-ollama` node in the n8n workflow.
```json
Request:  { "markdown": "…", "page": 1, "projectName": "My Manual", "pageType": "standard", "system": "D&D 5e" }
Response: { "status": "success", "markdown": "…corrected…", "total_chunks": 2 }
```

**`POST /dots_mocr`**
Runs dots.mocr OCR on an image file via subprocess. Returns structured elements and markdown.
Requires `DOTS_MOCR_PYTHON` and `DOTS_MOCR_SCRIPT` environment variables.
```json
Request:  { "imagePath": "/shared/projects/…/images/page_1_scan.png", "page": 1 }
Response: { "status": "success", "elements": […], "markdown": "…", "raw": "…" }
```

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `DOCPARSER_BASE_PATH` | `/shared/projects` | Base path for project output folders |
| `LITELLM_URL` | `http://localhost:4000/v1/chat/completions` | LiteLLM endpoint (correct_markdown) |
| `LITELLM_API_KEY` | *(required for correct_markdown)* | LiteLLM API key |
| `DOTS_MOCR_PYTHON` | `/opt/docparser/bin/python` | Python binary for dots.mocr subprocess |
| `DOTS_MOCR_SCRIPT` | `/opt/dots.ocr/run_inference.py` | dots.mocr inference script path |
| `DOTS_MOCR_TIMEOUT` | `1800` | Timeout in seconds for dots.mocr subprocess |

## Dependencies

```
flask
PyMuPDF (fitz)
Pillow
pytesseract
unstructured[pdf]
requests
```

Unstructured.io requires the YOLOX model for hi-res layout detection.
Tesseract must be installed on the system with the `ita` language pack.

## Running

```bash
cd /opt/docparser
python app.py
# Listens on 0.0.0.0:5000
```
