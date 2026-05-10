from flask import Blueprint, request, jsonify
import logging
import os
import fitz  # PyMuPDF

recognize_bp = Blueprint('recognize', __name__)

@recognize_bp.route('/recognize', methods=['POST'])
def recognize_pdf():
    """
    Rileva se un PDF è scansionato o nativo controllando le prime pagine.
    Riceve: { "pdfPath": "/path/file.pdf" }
    Restituisce: { status, is_scanned, checked_pages, pdfPath }
    is_scanned: true se nessuna delle pagine campionate contiene testo
    """
    logging.info("--- Riconoscimento tipo PDF ---")
    try:
        data = request.get_json()
        pdf_path = data.get('pdfPath')

        if not pdf_path:
            return jsonify({'error': 'pdfPath mancante'}), 400

        if not os.path.exists(pdf_path):
            return jsonify({'error': f'File non trovato: {pdf_path}'}), 404

        doc = fitz.open(pdf_path)
        total_pages = len(doc)

        # Controlla le prime 5 pagine (o meno se il PDF è corto)
        pages_to_check = min(5, total_pages)
        text_found = False

        for i in range(pages_to_check):
            page = doc[i]
            blocks = page.get_text("blocks")
            text = "\n".join([b[4] for b in blocks]).strip()
            if len(text) > 20:  # soglia minima per evitare falsi positivi
                text_found = True
                break

        doc.close()

        return jsonify({
            'status': 'success',
            'pdfPath': pdf_path,
            'is_scanned': not text_found,
            'checked_pages': pages_to_check,
            'total_pages': total_pages
        })

    except Exception as e:
        logging.error(f"Errore recognize: {str(e)}")
        return jsonify({'error': str(e)}), 500
