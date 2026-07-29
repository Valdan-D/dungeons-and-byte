import subprocess
import time
import logging
import urllib.request
from flask import Flask, jsonify

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

SURYA_SERVICE = "surya-gateway"
SURYA_HEALTH_URL = "http://localhost:8100/health"


def is_active(service):
    r = subprocess.run(["systemctl", "is-active", service], capture_output=True, text=True)
    return r.stdout.strip() == "active"


def wait_surya_ready(timeout=60):
    """Il processo systemd puo' risultare 'active' prima che il modello
    llama.cpp sia caricato e pronto a rispondere (~20-25s misurati) - senza
    questa attesa, la prima pagina del ramo nativo rischierebbe di trovare
    il gateway non ancora pronto e cadere nel fallback standard invece di
    usare Surya."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(SURYA_HEALTH_URL, timeout=3) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            pass
        time.sleep(1)
    return False


@app.route("/status", methods=["GET"])
def status():
    return jsonify({"surya_active": is_active(SURYA_SERVICE)})


@app.route("/ensure/surya", methods=["POST"])
def ensure_surya():
    """Chiamato dal ramo nativo (v1.3) prima di usare Surya per il
    riconoscimento zone: se il gateway non e' attivo lo avvia. Non serve
    fermare Ollama esplicitamente - Ollama scarica da solo i modelli dopo
    un periodo di inattivita' (keep_alive), quindi libera la GPU in modo
    naturale se non viene interpellato."""
    if is_active(SURYA_SERVICE) and wait_surya_ready(timeout=5):
        logging.info("surya-gateway gia' attivo e pronto")
        return jsonify({"action": "already_active", "service": SURYA_SERVICE, "ready": True})
    logging.info("Avvio surya-gateway...")
    subprocess.run(["systemctl", "start", SURYA_SERVICE], check=True)
    ready = wait_surya_ready(timeout=60)
    return jsonify({"action": "started", "service": SURYA_SERVICE, "ready": ready})


@app.route("/restart/surya", methods=["POST"])
def restart_surya():
    """Riavvio INCONDIZIONATO (non solo se non attivo), da chiamare
    periodicamente durante l'elaborazione di libri lunghi dal ramo nativo.
    Necessario perche' llama-server (build b8759) accumula memoria ad ogni
    richiesta /layout elaborata - non un'allocazione fissa ma un leak
    progressivo (verificato: da 828MB dopo un riavvio pulito a 7.5GB dopo
    ~89 pagine, stessa causa dell'incidente precedente ma non risolta
    abbassando solo ctx-size/parallel, che ha solo alzato la soglia di
    partenza). Riavviare periodicamente (ogni ~15 pagine, gestito dal
    workflow n8n) mantiene la memoria sotto controllo per l'intera durata
    di un libro lungo, invece di aspettare che ricada di nuovo in swap
    thrashing."""
    was_active = is_active(SURYA_SERVICE)
    if was_active:
        logging.info("Riavvio periodico surya-gateway (contenimento memoria)...")
        subprocess.run(["systemctl", "stop", SURYA_SERVICE], check=True)
    subprocess.run(["systemctl", "start", SURYA_SERVICE], check=True)
    ready = wait_surya_ready(timeout=60)
    return jsonify({"action": "restarted", "service": SURYA_SERVICE, "ready": ready})


@app.route("/ensure/ollama", methods=["POST"])
def ensure_ollama():
    """Chiamato dal ramo scansione (OCR) prima di usare Ollama per la
    pulizia del testo: se surya-gateway e' attivo, lo ferma per liberare la
    VRAM che altrimenti resterebbe riservata in permanenza (llama-server
    di Surya non la rilascia mai da solo, essendo un servizio sempre
    attivo) - senza questo, Ollama tende a girare in gran parte su CPU per
    mancanza di VRAM libera (verificato: 80% CPU / 20% GPU con
    surya-gateway attivo, "La Prova dei Signori Della Guerra",
    2026-07-29)."""
    if not is_active(SURYA_SERVICE):
        logging.info("surya-gateway gia' fermo")
        return jsonify({"action": "already_stopped", "service": SURYA_SERVICE})
    logging.info("Fermo surya-gateway per liberare VRAM a Ollama...")
    subprocess.run(["systemctl", "stop", SURYA_SERVICE], check=True)
    return jsonify({"action": "stopped", "service": SURYA_SERVICE})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8101)
