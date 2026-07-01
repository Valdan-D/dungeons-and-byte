from flask import Blueprint, request, jsonify
import logging
import os
import json
import subprocess

dots_mocr_bp = Blueprint('dots_mocr', __name__)

PYTHON_BIN = os.environ.get('DOTS_MOCR_PYTHON', '/opt/docparser/bin/python')
INFERENCE_SCRIPT = os.environ.get('DOTS_MOCR_SCRIPT', '/opt/dots.ocr/run_inference.py')
TIMEOUT = int(os.environ.get('DOTS_MOCR_TIMEOUT', '1800'))  # 30 minuti default


def _build_markdown(elements, page_num):
    SKIP = {'Page-footer', 'Page-header'}
    lines = []
    fig_counter = 1

    for el in elements:
        if not isinstance(el, dict):
            continue
        category = el.get('category', '')
        text = (el.get('text') or '').strip()

        if category in SKIP:
            continue

        if category == 'Picture':
            placeholder = f"![Page{page_num}_Fig{fig_counter}](images/page{page_num}_fig{fig_counter}.png)"
            lines.append(placeholder)
            fig_counter += 1

        elif category == 'Section-header':
            clean = text.lstrip('#').strip()
            lines.append(f"## {clean}")

        elif category == 'Title':
            clean = text.lstrip('#').strip()
            lines.append(f"# {clean}")

        elif text:
            lines.append(text)

    return '\n\n'.join(lines)


@dots_mocr_bp.route('/dots_mocr', methods=['POST'])
def dots_mocr():
    logging.info("--- dots.mocr ---")
    try:
        data = request.get_json()
        image_path = data.get('imagePath')
        page_num = data.get('page', 1)

        if not image_path:
            return jsonify({'error': 'imagePath mancante'}), 400
        if not os.path.exists(image_path):
            return jsonify({'error': f'File non trovato: {image_path}'}), 404
        if not os.path.exists(INFERENCE_SCRIPT):
            return jsonify({'error': f'Script non trovato: {INFERENCE_SCRIPT}'}), 500

        logging.info(f"Avvio subprocess per pagina {page_num}: {image_path}")

        result = subprocess.run(
            [PYTHON_BIN, INFERENCE_SCRIPT, image_path],
            capture_output=True,
            text=True,
            timeout=TIMEOUT
        )

        if result.returncode != 0:
            logging.error(f"Subprocess fallito: {result.stderr[:500]}")
            return jsonify({'error': result.stderr[:500]}), 500

        try:
            output = json.loads(result.stdout)
        except json.JSONDecodeError:
            logging.error(f"Output non JSON: {result.stdout[:200]}")
            return jsonify({'error': 'Output non valido dal subprocess'}), 500

        if 'error' in output:
            return jsonify({'error': output['error']}), 500

        elements = output.get('elements', [])
        raw = output.get('raw', '')
        markdown = _build_markdown(elements, page_num)

        logging.info(f"OK: {len(elements)} elementi, pagina {page_num}")

        return jsonify({
            'status': 'success',
            'page': page_num,
            'elements': elements,
            'markdown': markdown,
            'raw': raw
        })

    except subprocess.TimeoutExpired:
        logging.error(f"Timeout dopo {TIMEOUT}s")
        return jsonify({'error': f'Timeout dopo {TIMEOUT} secondi'}), 504

    except Exception as e:
        logging.error(f"Errore dots.mocr: {str(e)}")
        return jsonify({'error': str(e)}), 500
