#!/usr/bin/env python3
"""
Estrae testo e immagini da PDF nativo con PyMuPDF.
Rileva heading tramite dimensione font (no LLM, no OCR).
Uso: pdf_to_markdown.py <file.pdf> [images_dir]
Output: JSON array [{page: N, markdown: "...", images: ["filename", ...]}]
"""
import fitz
import json
import sys
import re
import os
from statistics import median

_FUNC_END = re.compile(
    r'\b(delle|della|del|degli|dei|di|de|le|la|lo|gli|il|e|o|un|una|che|'
    r'con|per|su|da|in|a|al|alla|alle|ai|agli|nel|nella|nelle|nei|negli|'
    r'questo|questa|questi|queste|come|dove|quando|tra|fra|'
    r'si|mi|ti|ci|vi|ne|lo|li|le)\s*$',
    re.I
)

def clean_text(t):
    t = t.replace('\xad', '')   # soft hyphen
    t = t.replace('\xa0', ' ')  # non-breaking space
    t = re.sub(r'[ \t]+', ' ', t)
    return t.strip()

def is_bold(font_name):
    return any(k in font_name for k in ('Bold', 'bold', 'Heavy', 'heavy'))

def extract_page_text(page):
    blocks = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE | fitz.TEXT_DEHYPHENATE)["blocks"]

    all_sizes = []
    for b in blocks:
        if b.get("type") != 0:
            continue
        for line in b.get("lines", []):
            for span in line.get("spans", []):
                t = span["text"].strip()
                if t and len(t) > 2:
                    all_sizes.append(round(span["size"], 1))

    if not all_sizes:
        return ""

    body_size = median(all_sizes)
    h2_thresh = body_size * 1.7
    h3_thresh = body_size * 1.3

    md_parts = []

    for b in blocks:
        if b.get("type") != 0:
            continue

        block_lines = []
        for line in b.get("lines", []):
            line_parts = []
            max_size = 0
            bold_flag = False
            for span in line.get("spans", []):
                t = clean_text(span["text"])
                if not t:
                    continue
                sz = span["size"]
                if sz > max_size:
                    max_size = sz
                if is_bold(span.get("font", "")):
                    bold_flag = True
                line_parts.append(t)

            line_text = ' '.join(line_parts).strip()
            if not line_text:
                continue

            if max_size >= h2_thresh and len(line_text) < 120:
                if _FUNC_END.search(line_text):
                    block_lines.append(line_text)
                else:
                    block_lines.append(f"## {line_text}")
            elif max_size >= h3_thresh and len(line_text) < 120:
                if _FUNC_END.search(line_text):
                    block_lines.append(line_text)
                else:
                    block_lines.append(f"### {line_text}")
            elif bold_flag and len(line_text) < 100 and max_size >= body_size * 0.95:
                block_lines.append(f"**{line_text}**")
            else:
                block_lines.append(line_text)

        if block_lines:
            md_parts.append('\n'.join(block_lines))

    markdown = '\n\n'.join(md_parts)
    markdown = re.sub(r'\n{3,}', '\n\n', markdown).strip()
    return markdown

def extract_page_images(doc, page, page_num, images_dir):
    """Estrae immagini dalla pagina, salva in images_dir. Ritorna lista nomi file."""
    if not images_dir:
        return []

    saved = []
    img_list = page.get_images(full=True)

    for img_idx, img_ref in enumerate(img_list):
        xref = img_ref[0]
        try:
            base_image = doc.extract_image(xref)
        except Exception:
            continue

        width  = base_image.get("width", 0)
        height = base_image.get("height", 0)
        # Salta immagini troppo piccole (icone decorative, bullet points)
        if width < 80 or height < 80:
            continue

        ext      = base_image.get("ext", "png")
        img_data = base_image["image"]
        fname    = f"img_pag_{page_num}_{img_idx + 1}.{ext}"
        fpath    = os.path.join(images_dir, fname)

        with open(fpath, "wb") as f:
            f.write(img_data)
        saved.append(fname)

    return saved

def main():
    if len(sys.argv) < 2:
        print("Uso: pdf_to_markdown.py <file.pdf> [images_dir]", file=sys.stderr)
        sys.exit(1)

    pdf_path   = sys.argv[1]
    images_dir = sys.argv[2] if len(sys.argv) > 2 else None

    if images_dir:
        os.makedirs(images_dir, exist_ok=True)

    doc = fitz.open(pdf_path)
    pages = []

    for i in range(len(doc)):
        page    = doc[i]
        page_num = i + 1
        markdown = extract_page_text(page)
        images   = extract_page_images(doc, page, page_num, images_dir)
        pages.append({"page": page_num, "markdown": markdown, "images": images})

    print(json.dumps(pages, ensure_ascii=False))

if __name__ == "__main__":
    main()
