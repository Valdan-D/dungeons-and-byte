"""Servizio di rilevamento di box e schede dei mostri (D&B). POST /zone {image_b64, conf?} -> riquadri in pixel dell'immagine."""
import base64
import io
import os

from flask import Flask, jsonify, request
from PIL import Image
from ultralytics import YOLO

MODELLO = os.environ.get("DNB_LAYOUT_MODEL", "/opt/dnb-layout/layout_best.pt")
CLASSI = {0: "box", 1: "scheda"}
app = Flask(__name__)
modello = YOLO(MODELLO)


@app.get("/health")
def health():
    return jsonify({"ok": True, "modello": os.path.basename(MODELLO)})


@app.post("/zone")
def zone():
    b = request.get_json(force=True)
    im = Image.open(io.BytesIO(base64.b64decode(b["image_b64"]))).convert("RGB")
    r = modello.predict(im, imgsz=1280, conf=float(b.get("conf", 0.25)), device="cpu", verbose=False)[0]
    W, H = im.size
    zs = [{"label": CLASSI[int(c)], "confidence": round(float(p), 3), "bbox": [round(float(v), 1) for v in xy]}
          for c, p, xy in zip(r.boxes.cls.tolist(), r.boxes.conf.tolist(), r.boxes.xyxy.tolist())]
    return jsonify({"width": W, "height": H, "zones": zs})


if __name__ == "__main__":
    app.run(host=os.environ.get("DNB_HOST", "0.0.0.0"), port=int(os.environ.get("DNB_PORT", "8097")))
