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
        page_num = data.get('page', 1)  # Recuperiamo il numero pagina se inviato

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

        # Check pagina full-image: nessun elemento testuale trovato
        if not elements:
            logging.info(f"Pagina {page_num} e' full-image, nessun elemento testuale trovato")
            return jsonify({
                'status': 'full_image',
                'page': page_num,
                'markdown': f'[IMMAGINE - pagina {page_num}]',
                'elements': [],
                'method': 'local_venv_unstructured_hi_res',
                'metadata': {
                    'strategy': 'hi_res',
                    'element_count': 0
                }
            })

        # 1. Prepariamo la lista degli elementi con metadati e coordinate
        # Gestiamo ogni elemento in modo robusto per evitare crash su None
        elements_dicts = []
        for el in elements:
            try:
                el_dict = el.to_dict()
                # Pulizia: Unstructured a volte restituisce coordinate complesse.
                # Ci assicuriamo che n8n riceva i punti o il bounding box.
                if 'metadata' in el_dict and 'coordinates' in el_dict['metadata']:
                    # Manteniamo le coordinate per il ritaglio nel "Ramo A"
                    el_dict['boundingBox'] = el_dict['metadata']['coordinates']
                elements_dicts.append(el_dict)
            except Exception as el_err:
                logging.warning(f"Elemento saltato per errore: {el_err}")
                continue

        # 2. Creiamo il contenuto Markdown unificato preservando la struttura
        markdown_chunks = []
        
        for el in elements:
            try:
                # Estraiamo la categoria identificata da Unstructured (Title, Table, NarrativeText, ecc.)
                category = getattr(el, "category", "Text")
                text = el.text if hasattr(el, 'text') and el.text else ""
                
                if not text:
                    continue

                # Gestione dei Titoli (li trasformiamo in header Markdown per lo split in n8n)
                if category == "Title":
                    if text.strip().isdigit():
                        # Numero isolato classificato come Title: quasi certamente un
                        # numero di pagina/footer mal riconosciuto da Unstructured/YOLOX,
                        # non un titolo vero (nessun titolo reale in questi manuali e'
                        # fatto di sole cifre, verificato su 4 manuali diversi).
                        # Trattato come Header/Footer: scartato per non spezzare i capitoli.
                        continue
                    # Intestazione ricorrente di pagina (titolo del capitolo ripetuto in
                    # piccolo in cima alle pagine successive alla prima): verificato su 5
                    # avventure diverse che l'apertura vera di un capitolo ha un font
                    # circa 2x piu' alto (80-95px) di qualsiasi ripetizione (44-48px) o
                    # titolo di sezione nel corpo pagina (che comunque non sta mai nella
                    # fascia alta). Soglie relative all'altezza di rendering della
                    # pagina per restare valide anche con risoluzioni diverse tra libri.
                    try:
                        coords = el.metadata.coordinates
                        page_h = coords.system.height if coords and coords.system else None
                        if coords and coords.points and page_h:
                            ys = [pt[1] for pt in coords.points]
                            y_top = min(ys)
                            height = max(ys) - min(ys)
                            if (y_top / page_h) < 0.11 and (height / page_h) < 0.018:
                                continue
                    except Exception:
                        pass
                    markdown_chunks.append(f"## {text}")
                
                # Gestione delle Liste
                elif category == "ListItem":
                    markdown_chunks.append(f"- {text}")
                
                # Gestione delle Tabelle
                elif category == "Table":
                    # Se infer_table_structure=True, Unstructured salva la tabella in formato HTML nei metadati
                    if hasattr(el, "metadata") and hasattr(el.metadata, "text_as_html") and el.metadata.text_as_html:
                        markdown_chunks.append(el.metadata.text_as_html) 
                    else:
                        markdown_chunks.append(text)
                
                # Pulizia: ignoriamo header e footer (numeri di pagina, loghi ricorrenti)
                elif category in ["Header", "Footer"]:
                    continue
                    
                # Testo standard (NarrativeText, UncategorizedText)
                else:
                    markdown_chunks.append(text)
                    
            except Exception as chunk_err:
                logging.warning(f"Errore nella generazione markdown per elemento: {chunk_err}")

        markdown_content = "\n\n".join(markdown_chunks)

        logging.info(f"Parsing completato con successo per la pagina {page_num}")
        return jsonify({
            'status': 'success',
            'page': page_num,
            'markdown': markdown_content,
            'elements': elements_dicts,        # Lista completa per il nodo "verifica-immagini"
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
