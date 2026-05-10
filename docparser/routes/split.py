from flask import Blueprint, request, jsonify
import logging
import os
import fitz  # PyMuPDF

split_bp = Blueprint('split', __name__)

@split_bp.route('/split', methods=['POST'])
def split_pdf():
    """
    Splitta un PDF in pagine singole salvate su disco.
    Riceve: { "pdfPath": "/path/file.pdf", "projectName": "nome" }
    Restituisce: array di { page, path } per ogni pagina creata.
    """
    logging.info("--- Split PDF ---")
    try:
        data = request.get_json()
        pdf_path = data.get('pdfPath')
        project_name = data.get('projectName')

        if not pdf_path or not project_name:
            return jsonify({'error': 'pdfPath e projectName sono obbligatori'}), 400
        if not os.path.exists(pdf_path):
            return jsonify({'error': f'File non trovato: {pdf_path}'}), 404

        pages_dir = f"/shared/projects/{project_name}/pages"
        os.makedirs(pages_dir, exist_ok=True)

        doc = fitz.open(pdf_path)
        pages = []

        for i in range(len(doc)):
            page_path = f"{pages_dir}/page_{i+1}.pdf"
            # Crea un nuovo documento con una sola pagina e lo salva
            single = fitz.open()
            single.insert_pdf(doc, from_page=i, to_page=i)
            single.save(page_path)
            single.close()
            pages.append({
                'page': i + 1,
                'path': page_path
            })
            logging.info(f"Pagina {i+1} salvata: {page_path}")

        doc.close()
        return jsonify({
            'status': 'success',
            'projectName': project_name,
            'total_pages': len(pages),
            'pages': pages
        })
    except Exception as e:
        logging.error(f"Errore split: {str(e)}")
        return jsonify({'error': str(e)}), 500
