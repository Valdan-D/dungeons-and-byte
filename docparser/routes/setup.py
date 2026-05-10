from flask import Blueprint, request, jsonify
import logging
import os

setup_bp = Blueprint('setup', __name__)

@setup_bp.route('/setup', methods=['POST'])
def setup_project():
    """
    Crea la struttura di cartelle per un nuovo progetto.
    Riceve: { "projectName": "nome_progetto" }
    Crea: /shared/projects/nome_progetto/pages/, /images/ e /json/
    """
    logging.info("--- Setup progetto ---")
    try:
        data = request.get_json()
        project_name = data.get('projectName')

        if not project_name:
            return jsonify({'error': 'projectName mancante'}), 400

        base = f"/shared/projects/{project_name}"
        paths = [
            f"{base}/pages",
            f"{base}/images",
            f"{base}/json",
            f"{base}/markdown"
        ]

        for path in paths:
            os.makedirs(path, exist_ok=True)
            logging.info(f"Cartella creata: {path}")

        return jsonify({
            'status': 'success',
            'projectName': project_name,
            'paths': paths
        })

    except Exception as e:
        logging.error(f"Errore setup: {str(e)}")
        return jsonify({'error': str(e)}), 500
