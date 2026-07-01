from flask import Blueprint, request, jsonify
import logging
import requests

correct_markdown_bp = Blueprint('correct_markdown', __name__)

LITELLM_URL = "http://192.168.178.79:4000/v1/chat/completions"
LITELLM_KEY = "Bearer sk-w7soaCDhxpWjReQyBS9M8Q"
MAX_CHARS = 3000

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


def correct_chunk(chunk, page, project_name, page_type, system, chunk_index, total_chunks):
    """Chiama LiteLLM per correggere un singolo chunk."""
    prompt = f"""Agisci come un esperto di digitalizzazione di manuali GDR (Sistema: {system}).
Analizza la Pagina {page} del progetto "{project_name}" (chunk {chunk_index + 1} di {total_chunks}).

REGOLE DI ESTRAZIONE:
1. TABELLE: Converti tabelle in markdown strutturato esattamente come la pagina analizzata.
2. MEDIA: Se trovi tag "[SEGNAPOSTO_IMMAGINE: nome_file.png]", mantienili esattamente dove sono.
3. COPERTINE: Se la pagina è indicata come "{'Copertina/Immagine Piena' if page_type == 'full_image' else 'Standard'}", descrivi brevemente il soggetto dell'immagine.
4. TESTO: non riassumere il testo e non commentarlo, ma correggi possibili errori di scansione.
5. IMPORTANTE: Restituisci SOLO il testo markdown pulito, senza commenti, senza spiegazioni, senza prefissi.

RISPONDI ESCLUSIVAMENTE IN FORMATO MARKDOWN PURO

TESTO DA ANALIZZARE:
{chunk}"""

    payload = {
        "model": "ollama/llama3",
        "temperature": 0.1,
        "max_tokens": 4000,
        "messages": [
            {
                "role": "system",
                "content": "Sei un estrattore di dati tecnico. Restituisci SOLO il testo markdown pulito senza alcun commento o spiegazione. Non riassumere mai il testo originale."
            },
            {
                "role": "user",
                "content": prompt
            }
        ]
    }

    response = requests.post(
        LITELLM_URL,
        json=payload,
        headers={
            "Authorization": LITELLM_KEY,
            "Content-Type": "application/json"
        },
        timeout=600
    )
    response.raise_for_status()
    data = response.json()
    content = data["choices"][0]["message"]["content"]

    # Pulizia backtick residui
    clean = content.replace("```json", "").replace("```markdown", "").replace("```", "").strip()
    return clean


@correct_markdown_bp.route('/correct_markdown', methods=['POST'])
def correct_markdown():
    """
    Divide il markdown in chunk, corregge ogni chunk via LiteLLM e riassembla.
    Riceve: { "markdown": "...", "page": 1, "projectName": "nome", "pageType": "standard", "system": "D&D 5e" }
    Restituisce: { status, page, projectName, markdown }
    """
    logging.info("--- Correzione markdown ---")
    try:
        data = request.get_json()
        markdown = data.get('markdown', '')
        page = data.get('page', 1)
        project_name = data.get('projectName', '')
        page_type = data.get('pageType', 'standard')
        system = data.get('system', 'D&D BX/BECMI').upper()

        if not markdown:
            return jsonify({'error': 'markdown mancante'}), 400

        # Divide in chunk
        chunks = split_into_chunks(markdown)
        logging.info(f"Pagina {page}: {len(chunks)} chunk da correggere")

        # Corregge ogni chunk
        corrected_chunks = []
        for idx, chunk in enumerate(chunks):
            logging.info(f"Correzione chunk {idx + 1}/{len(chunks)}...")
            corrected = correct_chunk(chunk, page, project_name, page_type, system, idx, len(chunks))
            corrected_chunks.append(corrected)

        # Riassembla
        final_markdown = '\n\n'.join(corrected_chunks)

        logging.info(f"Pagina {page} corretta: {len(corrected_chunks)} chunk riassemblati")

        return jsonify({
            'status': 'success',
            'page': page,
            'projectName': project_name,
            'markdown': final_markdown,
            'total_chunks': len(chunks)
        })

    except requests.exceptions.Timeout:
        logging.error("Timeout chiamata LiteLLM")
        return jsonify({'error': 'Timeout LiteLLM'}), 504

    except Exception as e:
        logging.error(f"Errore correct_markdown: {str(e)}")
        return jsonify({'error': str(e)}), 500
