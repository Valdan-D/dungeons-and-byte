from flask import Blueprint, request, jsonify
import logging
import os
import fitz  # PyMuPDF
from PIL import Image
import pytesseract
import io

ocr_bp = Blueprint('ocr', __name__)

@ocr_bp.route('/ocr_from_path', methods=['POST'])
def ocr_from_path():
    """
    Esegue OCR su un PDF scansionato già salvato su disco.
    Riceve: { "pdfPath": "/path/page_1.pdf", "page": 1, "lang": "ita" }
    Restituisce: { status, page, text, is_scanned: true }
    Usato nel loop n8n quando parse_from_path restituisce is_scanned: true.
    Il PDF viene renderizzato come immagine ad alta risoluzione (300 DPI)
    prima di essere passato a Tesseract per massimizzare la qualità OCR.
    """
    logging.info("--- OCR da path ---")
    try:
        data = request.get_json()
        pdf_path = data.get('pdfPath')
        page_num = data.get('page', 1)
        lang = data.get('lang', 'ita')  # default italiano

        if not pdf_path:
            return jsonify({'error': 'pdfPath mancante'}), 400
        if not os.path.exists(pdf_path):
            return jsonify({'error': f'File non trovato: {pdf_path}'}), 404

        doc = fitz.open(pdf_path)
        page = doc[0]

        # Renderizza la pagina come immagine ad alta risoluzione (300 DPI)
        # Matrix(3, 3) equivale a 3x72 = 216 DPI, usiamo 4x per ~288 DPI
        mat = fitz.Matrix(3, 3)
        pix = page.get_pixmap(matrix=mat)
        img_bytes = pix.tobytes("png")
        doc.close()

        # Converti in immagine PIL e passa a Tesseract
        img_pil = Image.open(io.BytesIO(img_bytes))
        text = pytesseract.image_to_string(img_pil, lang=lang)

        logging.info(f"OCR completato per pagina {page_num}, {len(text)} caratteri estratti")

        return jsonify({
            'status': 'success',
            'page': page_num,
            'text': text.strip(),
            'is_scanned': True  # sempre true per questo endpoint
        })
    except Exception as e:
        logging.error(f"Errore OCR: {str(e)}")
        return jsonify({'error': str(e)}), 500
