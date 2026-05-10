from flask import Blueprint, request, jsonify
import logging
import os
import base64
import fitz  # PyMuPDF
from PIL import Image
import io

images_bp = Blueprint('images', __name__)


@images_bp.route('/extract_images_from_path', methods=['POST'])
def extract_images_from_path():
    """
    Estrae tutte le immagini embedded da un PDF con testo già su disco e le salva come PNG.
    Riceve: { "pdfPath": "/path/page_1.pdf", "projectName": "nome", "page": 1 }
    Restituisce: { status, page, total_images, images: [{ index, path, ext }] }
    Le immagini vengono convertite in PNG da qualsiasi formato originale (es. JPX).
    Usato solo per PDF NON scansionati (is_scanned=false).
    """
    logging.info("--- Estrazione immagini da path ---")
    try:
        data = request.get_json()
        pdf_path = data.get('pdfPath')
        project_name = data.get('projectName')
        page_num = data.get('page', 1)

        if not pdf_path or not project_name:
            return jsonify({'error': 'pdfPath e projectName sono obbligatori'}), 400
        if not os.path.exists(pdf_path):
            return jsonify({'error': f'File non trovato: {pdf_path}'}), 404

        images_dir = f"/shared/projects/{project_name}/images"
        os.makedirs(images_dir, exist_ok=True)

        doc = fitz.open(pdf_path)
        page = doc[0]
        images = []

        for img_index, img in enumerate(page.get_images(full=True)):
            xref = img[0]  # riferimento interno all'immagine nel PDF
            base_image = doc.extract_image(xref)
            img_bytes = base_image["image"]

            # Converti in PNG con Pillow indipendentemente dal formato originale
            img_pil = Image.open(io.BytesIO(img_bytes))
            png_buffer = io.BytesIO()
            img_pil.save(png_buffer, format='PNG')
            png_bytes = png_buffer.getvalue()

            save_path = f"{images_dir}/page_{page_num}_img_{img_index + 1}.png"
            with open(save_path, "wb") as f:
                f.write(png_bytes)

            images.append({
                'index': img_index + 1,
                'path': save_path,
                'ext': 'png'
            })
            logging.info(f"Immagine {img_index + 1} estratta e convertita in PNG da pagina {page_num}")

        doc.close()
        return jsonify({
            'status': 'success',
            'page': page_num,
            'total_images': len(images),
            'images': images
        })

    except Exception as e:
        logging.error(f"Errore estrazione immagini: {str(e)}")
        return jsonify({'error': str(e)}), 500


@images_bp.route('/render_page', methods=['POST'])
def render_page():
    """
    Renderizza una pagina PDF scansionata come PNG ad alta risoluzione.
    Usato per salvare l'immagine della pagina intera quando is_scanned=true.
    Riceve: { "pdfPath": "/path/page_1.pdf", "projectName": "nome", "page": 1 }
    Restituisce: { status, page, path }
    """
    logging.info("--- Render pagina scansionata ---")
    try:
        data = request.get_json()
        pdf_path = data.get('pdfPath')
        project_name = data.get('projectName')
        page_num = data.get('page', 1)

        if not pdf_path or not project_name:
            return jsonify({'error': 'pdfPath e projectName sono obbligatori'}), 400
        if not os.path.exists(pdf_path):
            return jsonify({'error': f'File non trovato: {pdf_path}'}), 404

        images_dir = f"/shared/projects/{project_name}/images"
        os.makedirs(images_dir, exist_ok=True)

        doc = fitz.open(pdf_path)
        page = doc[0]

        # Renderizza a 200 DPI (Matrix 2.78 ≈ 200 DPI)
        mat = fitz.Matrix(2.78, 2.78)
        pix = page.get_pixmap(matrix=mat)
        doc.close()

        save_path = f"{images_dir}/page_{page_num}_scan.png"
        pix.save(save_path)

        logging.info(f"Pagina {page_num} renderizzata: {save_path}")
        return jsonify({
            'status': 'success',
            'page': page_num,
            'path': save_path
        })

    except Exception as e:
        logging.error(f"Errore render pagina: {str(e)}")
        return jsonify({'error': str(e)}), 500


@images_bp.route('/extract_images', methods=['POST'])
def extract_images():
    """
    Estrae immagini da un PDF ricevuto come binary stream nel body.
    Endpoint legacy - usa extract_images_from_path per i nuovi flussi.
    Riceve: PDF binario nel body + query params ?page=1&projectName=nome
    Restituisce: immagini salvate su disco + base64 nella risposta JSON.
    """
    logging.info("--- Estrazione immagini ---")
    try:
        stream = request.get_data()
        if not stream:
            return jsonify({'error': 'Nessun dato ricevuto'}), 400

        # Parametri passati come query string
        page_num = request.args.get('page', 1, type=int)
        project_name = request.args.get('projectName', 'unknown')

        doc = fitz.open(stream=stream, filetype="pdf")
        page = doc[0]
        images = []

        for img_index, img in enumerate(page.get_images(full=True)):
            xref = img[0]
            base_image = doc.extract_image(xref)
            img_bytes = base_image["image"]

            # Converti in PNG con Pillow
            img_pil = Image.open(io.BytesIO(img_bytes))
            png_buffer = io.BytesIO()
            img_pil.save(png_buffer, format='PNG')
            png_bytes = png_buffer.getvalue()

            save_path = f"/shared/projects/{project_name}/images/page_{page_num}_img_{img_index + 1}.png"
            with open(save_path, "wb") as f:
                f.write(png_bytes)

            images.append({
                'index': img_index + 1,
                'path': save_path,
                'ext': 'png',
                'base64': base64.b64encode(png_bytes).decode('utf-8')
            })
            logging.info(f"Immagine {img_index + 1} estratta e convertita in PNG da pagina {page_num}")

        doc.close()
        return jsonify({
            'status': 'success',
            'page': page_num,
            'total_images': len(images),
            'images': images
        })

    except Exception as e:
        logging.error(f"Errore estrazione immagini: {str(e)}")
        return jsonify({'error': str(e)}), 500

