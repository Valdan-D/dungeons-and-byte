from flask import Blueprint, request, jsonify, send_file
import os
import fitz
from PIL import Image
import io
import logging

crop_bp = Blueprint('crop', __name__)

@crop_bp.route('/extract', methods=['POST'])
def extract_crop():
    try:
        data = request.get_json()
        pdf_path = data.get('pdfPath')
        page_num = int(data.get('page', 1)) - 1
        
        # Coordinate da n8n
        left = float(data.get('left', 0))
        top = float(data.get('top', 0))
        width = float(data.get('width', 0))
        height = float(data.get('height', 0))
        
        # DIMENSIONI DEL LAYOUT DI UNSTRUCTURED (fondamentali!)
        # Se non passate, usiamo i valori che hai postato tu come default
        l_width = float(data.get('layout_width', 2885))
        l_height = float(data.get('layout_height', 3754))

        doc = fitz.open(pdf_path)
        page = doc.load_page(page_num)
        
        # Renderizziamo la pagina a una risoluzione fissa alta (es. 300 DPI / Zoom 3)
        zoom = 3.0
        mat = fitz.Matrix(zoom, zoom)
        pix = page.get_pixmap(matrix=mat)
        img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)

        # CALCOLO SCALA REALE
        # Rapporto tra i pixel dell'immagine renderizzata e il layout di Unstructured
        scale_x = pix.width / l_width
        scale_y = pix.height / l_height

        # Applichiamo la scala alle coordinate di n8n
        crop_box = (
            left * scale_x,
            top * scale_y,
            (left + width) * scale_x,
            (top + height) * scale_y
        )

        logging.info(f"Crop Box finale: {crop_box} su immagine {pix.width}x{pix.height}")

        cropped_img = img.crop(crop_box)
        
        img_byte_arr = io.BytesIO()
        cropped_img.save(img_byte_arr, format='PNG')
        img_byte_arr.seek(0)
        
        return send_file(img_byte_arr, mimetype='image/png')

    except Exception as e:
        logging.error(f"Errore: {str(e)}")
        return jsonify({'error': str(e)}), 500
