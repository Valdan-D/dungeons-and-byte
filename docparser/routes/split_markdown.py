from flask import Blueprint, request, jsonify
import logging

split_markdown_bp = Blueprint('split_markdown', __name__)

MAX_CHARS = 1500

def find_split_point(text, max_chars):
    """Trova il punto di taglio migliore nel testo."""
    if len(text) <= max_chars:
        return len(text)
    slice_text = text[:max_chars]
    # 1. Cerca intestazione markdown
    heading_idx = slice_text.rfind('\n#')
    if heading_idx > max_chars * 0.5:
        return heading_idx
    # 2. Cerca doppio a capo (fine paragrafo)
    para_idx = slice_text.rfind('\n\n')
    if para_idx > max_chars * 0.3:
        return para_idx
    # 3. Cerca punto fermo
    dot_idx = slice_text.rfind('. ')
    if dot_idx > max_chars * 0.3:
        return dot_idx + 1
    # 4. Cerca virgola
    comma_idx = slice_text.rfind(', ')
    if comma_idx > max_chars * 0.3:
        return comma_idx + 1
    # 5. Fallback: taglia netto
    return max_chars

def split_into_chunks(text, max_chars=MAX_CHARS):
    """Divide il testo in chunk rispettando i punti naturali."""
    chunks = []
    remaining = text
    while remaining:
        split_at = find_split_point(remaining, max_chars)
        chunks.append(remaining[:split_at].strip())
        remaining = remaining[split_at:].strip()
    return chunks

@split_markdown_bp.route('/split_markdown', methods=['POST'])
def split_markdown():
    """
    Divide il markdown in chunk rispettando i punti naturali.
    Riceve: { "markdown": "...", "page": 1, "projectName": "nome", "pageType": "standard", "system": "D&D 5e" }
    Restituisce: { status, page, projectName, system, pageType, chunks, total_chunks }
    """
    logging.info("--- Split markdown ---")
    try:
        data = request.get_json()
        markdown = data.get('markdown', '')
        page = data.get('page', 1)
        project_name = data.get('projectName', '')
        page_type = data.get('pageType', 'standard')
        system = data.get('system', 'D&D BX/BECMI').upper()

        if not markdown:
            return jsonify({'error': 'markdown mancante'}), 400

        chunks = split_into_chunks(markdown)
        logging.info(f"Pagina {page}: {len(chunks)} chunk generati")

        return jsonify({
            'status': 'success',
            'page': page,
            'projectName': project_name,
            'system': system,
            'pageType': page_type,
            'chunks': chunks,
            'total_chunks': len(chunks)
        })

    except Exception as e:
        logging.error(f"Errore split_markdown: {str(e)}")
        return jsonify({'error': str(e)}), 500
