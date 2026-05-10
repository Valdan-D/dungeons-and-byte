from flask import Flask
from routes.parse import parse_bp
from routes.split import split_bp
from routes.images import images_bp
from routes.ocr import ocr_bp
from routes.setup import setup_bp
from routes.recognize import recognize_bp
from routes.crop import crop_bp
from routes.split_markdown import split_markdown_bp
from routes.correct_markdown import correct_markdown_bp
import logging
import sys

# Configurazione logging: mostra timestamp, livello e messaggio su stdout
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)

app = Flask(__name__)

# Registra tutti i blueprint (moduli di route)
app.register_blueprint(parse_bp)
app.register_blueprint(split_bp)
app.register_blueprint(images_bp)
app.register_blueprint(ocr_bp)
app.register_blueprint(setup_bp)
app.register_blueprint(recognize_bp)
app.register_blueprint(crop_bp, url_prefix='/crop')
app.register_blueprint(correct_markdown_bp)
app.register_blueprint(split_markdown_bp)

if __name__ == '__main__':
    logging.info("Avvio DocParser Flask Server sulla porta 5000...")
    app.run(host='0.0.0.0', port=5000, threaded=True)
