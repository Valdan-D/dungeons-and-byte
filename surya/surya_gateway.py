import os
os.environ["SURYA_INFERENCE_BACKEND"] = "llamacpp"
os.environ["LLAMA_CPP_BINARY"] = "/opt/llama-vulkan/llama-b8759/llama-server"
os.environ["GGML_VK_VISIBLE_DEVICES"] = "0"
os.environ["SURYA_INFERENCE_PARALLEL"] = "1"  # una sola richiesta alla volta (gateway sequenziale, mai batch)
os.environ["SURYA_INFERENCE_CTX_SIZE"] = "16384"  # minimo di libreria, invece di 8x12288=98304 (causava OOM/swap thrashing sull_host, 15GB RAM totali)

import base64
import io
import logging
from flask import Flask, request, jsonify
from PIL import Image
from surya.layout import LayoutPredictor

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

_predictor = None


def get_predictor():
    global _predictor
    if _predictor is None:
        logging.info("Caricamento LayoutPredictor Surya...")
        _predictor = LayoutPredictor()
    return _predictor


@app.route("/layout", methods=["POST"])
def layout():
    data = request.get_json()
    image_b64 = data.get("image_b64")
    if not image_b64:
        return jsonify({"error": "manca image_b64"}), 400

    image = Image.open(io.BytesIO(base64.b64decode(image_b64)))
    predictor = get_predictor()
    predictions = predictor([image])
    pred = predictions[0]

    zones = []
    for b in pred.bboxes:
        zones.append({
            "label": b.label,
            "bbox": list(b.bbox),
            "confidence": getattr(b, "confidence", None),
        })

    return jsonify({"zones": zones, "width": image.width, "height": image.height})


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8100)
