import glob
import logging
import os
import shutil
import subprocess
import time
import urllib.request

import fitz
from flask import Flask, jsonify, request
from paddleocr import PaddleOCRVL

logging.basicConfig(level=logging.INFO)

app = Flask(__name__)

pipeline = PaddleOCRVL(
    pipeline_version="v1.6",
    vl_rec_backend="llama-cpp-server",
    vl_rec_server_url="http://127.0.0.1:8090/v1",
    device="cpu",
)

WORK_DIR = "/tmp/paddle_work"


@app.route("/health")
def health():
    return jsonify({"status": "ok"})


@app.route("/restart_vlm", methods=["POST"])
def restart_vlm():
    # Riavvio incondizionato del motore VLM (llama-server) per contenere il
    # leak di memoria per-richiesta gia' visto e risolto allo stesso modo per
    # Surya (vedi dnb_surya_layout_integration): il fix sulla dimensione
    # iniziale (--parallel 1 --ctx-size 16384) abbassa il punto di partenza
    # ma non ferma la crescita progressiva nel tempo. Chiamato da n8n ogni N
    # pagine, in stile "fire-and-forget" (timeout client breve): il comando
    # stop/start parte comunque anche se n8n non aspetta la risposta intera.
    logging.warning("Riavvio incondizionato di paddleocr-vlm richiesto")
    subprocess.run(["systemctl", "restart", "paddleocr-vlm"], check=True)

    deadline = time.time() + 60
    ready = False
    while time.time() < deadline:
        try:
            with urllib.request.urlopen("http://127.0.0.1:8090/health", timeout=2) as r:
                if r.status == 200:
                    ready = True
                    break
        except Exception:
            pass
        time.sleep(1)

    return jsonify({"status": "ok" if ready else "timeout", "ready": ready})


@app.route("/parse_page", methods=["POST"])
def parse_page():
    data = request.get_json()
    pdf_path = data["pdfPath"]
    project_name = data["projectName"]
    page_num = int(data["page"])  # 1-indexed

    if not os.path.exists(pdf_path):
        return jsonify({"status": "error", "error": f"File non trovato: {pdf_path}"}), 404

    project_dir = f"/shared/projects/{project_name}"
    md_dir = f"{project_dir}/markdown"
    img_dir = f"{project_dir}/images"
    page_work_dir = f"{WORK_DIR}/{project_name}/{page_num}"
    os.makedirs(md_dir, exist_ok=True)
    os.makedirs(img_dir, exist_ok=True)
    os.makedirs(page_work_dir, exist_ok=True)

    try:
        doc = fitz.open(pdf_path)
        page = doc[page_num - 1]
        pix = page.get_pixmap(matrix=fitz.Matrix(2.0, 2.0))
        img_path = f"{page_work_dir}/render.png"
        pix.save(img_path)

        # Alcune pagine (rare, causa non chiara) fanno fallire la generazione
        # deterministica (temp=0) con un errore di formato lato modello (un
        # vicolo cieco nella grammatica imposta all'output). Verificato:
        # riproducibile al 100% a temp=0 sulla stessa pagina, ma sparisce con
        # una temperatura piu' alta - non sempre alla prima (0.3 non basta
        # sempre, vedi "In Cerca Di Avventura" pag.120, serve arrivare fino a
        # 0.9), quindi piu' tentativi a scaglioni invece di uno solo. Se
        # anche l'ultimo fallisce, errore vero: l'esecuzione deve fermarsi
        # (non proseguire silenziosamente saltando la pagina).
        save_path = f"{page_work_dir}/out"
        last_error = None
        for temp in (None, 0.3, 0.6, 0.9):
            try:
                if temp is None:
                    results = pipeline.predict(img_path)
                else:
                    logging.warning("Pagina %s: tentativo precedente fallito, ritento a temperature=%s", page_num, temp)
                    results = pipeline.predict(img_path, temperature=temp)
                for res in results:
                    res.save_to_markdown(save_path=save_path)
                last_error = None
                break
            except Exception as e:
                last_error = e

        if last_error is not None:
            logging.error("Pagina %s: tutti i tentativi falliti (%s)", page_num, last_error)
            raise last_error

        md_files = glob.glob(f"{save_path}/*.md")
        md_content = open(md_files[0], encoding="utf-8").read() if md_files else ""

        img_src_dir = f"{save_path}/imgs"
        counter = 1
        if os.path.isdir(img_src_dir):
            for fname in sorted(os.listdir(img_src_dir)):
                ext = fname.rsplit(".", 1)[-1]
                new_name = f"img_pag_{page_num}_img{counter}.{ext}"
                shutil.copy(f"{img_src_dir}/{fname}", f"{img_dir}/{new_name}")
                md_content = md_content.replace(f"imgs/{fname}", f"./images/{new_name}")
                counter += 1

        if not md_content.strip():
            # Pagina senza alcun blocco riconosciuto con contenuto (ne' testo
            # ne' un ritaglio immagine): tipicamente una tavola/illustrazione
            # a piena pagina che il rilevatore di layout non segmenta affatto
            # (trovato su "In Cerca Di Avventura" pag.4, 2026-08-04 - un
            # disegno a china a piena pagina). Il file .md prodotto dalla
            # pipeline in questo caso esiste ma e' vuoto (0 byte), non
            # assente - per questo va controllato il CONTENUTO, non solo se
            # il file esiste. Fallback: usiamo l'intera pagina gia'
            # renderizzata come immagine della pagina, per non perderla.
            logging.warning("Pagina %s: nessun contenuto riconosciuto, uso l'intera pagina come immagine", page_num)
            new_name = f"img_pag_{page_num}_img1.png"
            shutil.copy(img_path, f"{img_dir}/{new_name}")
            md_content = f'<div style="text-align: center;"><img src="./images/{new_name}" alt="Image" width="100%" /></div>\n'

        file_path = f"{md_dir}/page_{page_num}.md"
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(md_content)

        shutil.rmtree(page_work_dir, ignore_errors=True)

        return jsonify({
            "status": "success",
            "page": page_num,
            "projectName": project_name,
            "fileName": f"page_{page_num}.md",
            "filePath": file_path,
            "markdown": md_content,
        })

    except Exception as e:
        logging.exception("Errore parsing pagina %s", page_num)
        return jsonify({"status": "error", "error": str(e)}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8091)
