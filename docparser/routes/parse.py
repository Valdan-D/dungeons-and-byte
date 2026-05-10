from flask import Blueprint, request, jsonify
import os
import logging
# Importiamo la funzione e l'utilità per convertire in dizionario
from unstructured.partition.pdf import partition_pdf

parse_bp = Blueprint('parse', __name__)

@parse_bp.route('/parse_from_path', methods=['POST'])
def parse_from_path():
    logging.info("--- Parsing Locale con Unstructured (HI_RES) ---")
    try:
        data = request.get_json()
        pdf_path = data.get('pdfPath')
        page_num = data.get('page', 1) # Recuperiamo il numero pagina se inviato

        if not pdf_path or not os.path.exists(pdf_path):
            logging.error(f"File non trovato: {pdf_path}")
            return jsonify({'error': f'File non trovato: {pdf_path}'}), 404

        # Esecuzione del parsing HI_RES
        # Questa operazione è CPU intensive e richiede i modelli di computer vision
        elements = partition_pdf(
            filename=pdf_path,
            strategy="hi_res",                # Necessario per identificare Figure e Tabelle
            infer_table_structure=True,        # Estrae la struttura delle tabelle
            extract_images_in_pdf=False,       # Non estraiamo i file qui, lo faremo in n8n con le coordinate
            languages=["ita"],
            hi_res_model_name="yolox"          # Modello molto accurato per il layout
        )

        # 1. Prepariamo la lista degli elementi con metadati e coordinate
        elements_dicts = []
        for el in elements:
            el_dict = el.to_dict()
            
            # Pulizia: Unstructured a volte restituisce coordinate complesse.
            # Ci assicuriamo che n8n riceva i punti o il bounding box.
            if 'metadata' in el_dict and 'coordinates' in el_dict['metadata']:
                # Manteniamo le coordinate per il ritaglio nel "Ramo A"
                el_dict['boundingBox'] = el_dict['metadata']['coordinates']
            
            elements_dicts.append(el_dict)

        # 2. Creiamo il contenuto Markdown unificato (quello che va alla Pulizia)
        # Usiamo il metodo degli elementi stessi per mantenere la formattazione
        markdown_content = "\n\n".join([str(el) for el in elements])

        logging.info(f"Parsing completato con successo per la pagina {page_num}")

        return jsonify({
            'status': 'success',
            'page': page_num,
            'markdown': markdown_content,
            'elements': elements_dicts,       # Lista completa per il nodo "verifica-immagini"
            'method': 'local_venv_unstructured_hi_res',
            'metadata': {
                'strategy': 'hi_res',
                'element_count': len(elements_dicts)
            }
        })

    except Exception as e:
        logging.error(f"Errore critico nel parsing locale: {str(e)}")
        return jsonify({
            'status': 'error',
            'error': str(e)
        }), 500
