import glob
import logging
import os
import shutil
import subprocess
import time
import urllib.request

import io

import fitz
from flask import Flask, jsonify, request
from paddleocr import PaddleOCRVL
from PIL import Image

logging.basicConfig(level=logging.INFO)

app = Flask(__name__)

pipeline = PaddleOCRVL(
    pipeline_version="v1.6",
    vl_rec_backend="llama-cpp-server",
    vl_rec_server_url="http://127.0.0.1:8090/v1",
    device="cpu",
)

WORK_DIR = "/tmp/paddle_work"

# Filtro dimensione minima per le immagini incorporate (scarta icone/bullet decorativi)
MIN_IMG_SIZE = 80
# Se un'immagine incorporata copre almeno questa percentuale dell'area pagina,
# la trattiamo come "a piena pagina"
FULL_PAGE_AREA_RATIO = 0.5
# Un'immagine a piena pagina viene mostrata SOLO se il testo riconosciuto dal
# VLM per quella pagina e' sotto questa soglia di caratteri. Sotto soglia =
# quasi certamente un'illustrazione a piena pagina che il layout detector non
# segmenta (vedi pag.9: solo la didascalia, 140 caratteri, viene riconosciuta
# come testo - il resto della pagina e' l'illustrazione). Sopra soglia = la
# pagina ha comunque un contenuto testuale reale (es. l'Indice a pag.6), quindi
# l'immagine a piena pagina rilevata e' con ogni probabilita' uno sfondo
# decorativo del template grafico, non contenuto - non la mostriamo.
TESTO_CORTO_SOGLIA = 300


@app.route("/health")
def health():
    return jsonify({"status": "ok"})


@app.route("/setup", methods=["POST"])
def setup_project():
    # Stessa logica di docparser (routes/setup.py) - riprodotta qui per non
    # dipendere piu' dal container 109, che serve altri workflow n8n non
    # legati a questa pipeline.
    data = request.get_json()
    project_name = data.get("projectName")
    if not project_name:
        return jsonify({"error": "projectName mancante"}), 400

    base = f"/shared/projects/{project_name}"
    paths = [f"{base}/pages", f"{base}/images", f"{base}/json", f"{base}/markdown"]
    for path in paths:
        os.makedirs(path, exist_ok=True)

    return jsonify({"status": "success", "projectName": project_name, "paths": paths})


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


def _estrai_immagini_native(doc, page, page_num, img_dir):
    """Estrae le immagini incorporate REALI della pagina (oggetti PDF
    originali, mai passati per uno screenshot/ritaglio - a differenza del
    crop via layout del VLM, qui non c'e' rischio di tagli imprecisi).
    Scarta solo quelle troppo piccole (icone/bullet).
    Ritorna (piena_pagina: list[dict], posizionate: list[dict]), ognuna con
    {filename, y_ratio} - y_ratio = posizione verticale relativa (0=inizio
    pagina, 1=fine pagina), usata poi per piazzare il riferimento nel punto
    giusto del markdown."""
    page_rect = page.rect
    page_area = page_rect.width * page_rect.height

    piena_pagina = []
    posizionate = []
    counter = 1

    for img in page.get_images(full=True):
        xref = img[0]
        try:
            base = doc.extract_image(xref)
        except Exception:
            continue
        w, h = base.get("width", 0), base.get("height", 0)
        if w < MIN_IMG_SIZE or h < MIN_IMG_SIZE:
            continue

        rects = page.get_image_rects(xref)
        rect = rects[0] if rects else None
        area_ratio = ((rect.width * rect.height) / page_area) if rect else 1.0
        y_ratio = ((rect.y0 + rect.y1) / 2 / page_rect.height) if rect else 0.0

        ext = base["ext"]
        img_bytes = base["image"]
        if ext not in ("jpg", "jpeg", "png", "gif", "webp"):
            # Formati non web-safe (es. JPEG2000/.jpx, comune nei PDF nativi
            # moderni) non sono visualizzabili dai browser: riconvertiamo in PNG.
            try:
                buf = io.BytesIO()
                Image.open(io.BytesIO(img_bytes)).convert("RGB").save(buf, format="PNG")
                img_bytes = buf.getvalue()
                ext = "png"
            except Exception:
                logging.warning("Pagina %s: conversione PNG fallita per immagine nativa, mantengo formato originale .%s", page_num, ext)
        new_name = f"img_pag_{page_num}_native{counter}.{ext}"
        with open(f"{img_dir}/{new_name}", "wb") as f:
            f.write(img_bytes)
        counter += 1

        entry = {"filename": new_name, "y_ratio": y_ratio}
        if area_ratio >= FULL_PAGE_AREA_RATIO or rect is None:
            piena_pagina.append(entry)
        else:
            posizionate.append(entry)

    return piena_pagina, posizionate


def _inserisci_immagini_posizionate(md_content, immagini):
    """Inserisce ogni immagine posizionata nel punto del markdown
    proporzionale alla sua posizione verticale nella pagina (approssimazione
    per paragrafi: non abbiamo un aggancio preciso blocco-per-blocco senza
    riscrivere l'assemblaggio del markdown attorno all'output strutturato
    della pipeline invece che al file .md gia' pronto)."""
    if not immagini:
        return md_content
    paragrafi = md_content.split("\n\n")
    n = len(paragrafi) if paragrafi else 1
    # Inserisce dal fondo verso l'inizio per non invalidare gli indici già calcolati
    for img in sorted(immagini, key=lambda i: i["y_ratio"], reverse=True):
        idx = min(int(round(img["y_ratio"] * n)), len(paragrafi))
        tag = f'<div style="text-align: center;"><img src="./images/{img["filename"]}" alt="Image" width="60%" /></div>'
        paragrafi.insert(idx, tag)
    return "\n\n".join(paragrafi)


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
    json_dir = f"{project_dir}/json"
    page_work_dir = f"{WORK_DIR}/{project_name}/{page_num}"
    os.makedirs(md_dir, exist_ok=True)
    os.makedirs(img_dir, exist_ok=True)
    os.makedirs(json_dir, exist_ok=True)
    os.makedirs(page_work_dir, exist_ok=True)

    try:
        doc = fitz.open(pdf_path)
        page = doc[page_num - 1]
        pix = page.get_pixmap(matrix=fitz.Matrix(2.0, 2.0))
        img_path = f"{page_work_dir}/pag_{page_num}.png"
        pix.save(img_path)

        # Alcune pagine (rare, causa non chiara) fanno fallire la generazione
        # deterministica (temp=0) con un errore di formato lato modello (un
        # vicolo cieco nella grammatica imposta all'output). Verificato:
        # riproducibile al 100% a temp=0 sulla stessa pagina, ma sparisce con
        # una temperatura piu' alta - non sempre alla prima (0.3 non basta
        # sempre, vedi un modulo classico pag.120, serve arrivare fino a
        # 0.9), quindi piu' tentativi a scaglioni invece di uno solo. Se
        # anche l'ultimo fallisce, errore vero: l'esecuzione deve fermarsi
        # (non proseguire silenziosamente saltando la pagina).
        save_path = f"{page_work_dir}/out"
        last_error = None
        aside_texts = []
        for temp in (None, 0.3, 0.6, 0.9):
            try:
                if temp is None:
                    results = pipeline.predict(img_path)
                else:
                    logging.warning("Pagina %s: tentativo precedente fallito, ritento a temperature=%s", page_num, temp)
                    results = pipeline.predict(img_path, temperature=temp)
                results = list(results)
                for res in results:
                    res.save_to_markdown(save_path=save_path)
                    try:
                        res.save_to_json(save_path=json_dir)
                    except Exception:
                        logging.warning("Pagina %s: salvataggio JSON fallito (non bloccante)", page_num)
                    try:
                        res_data = res.json.get("res", {})
                        for blk in res_data.get("parsing_res_list", []):
                            if blk.get("block_label") == "aside_text":
                                testo = (blk.get("block_content") or "").strip()
                                if testo:
                                    aside_texts.append(testo)
                    except Exception:
                        logging.warning("Pagina %s: estrazione aside_text fallita (non bloccante)", page_num)
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

        # --- Estrazione diretta delle immagini incorporate (PDF nativi) ---
        # A differenza del ritaglio via layout del VLM (basato su uno
        # screenshot della pagina + riquadro stimato dal modello, quindi
        # soggetto a tagli imprecisi e a mancati rilevamenti su illustrazioni
        # dipinte a piena pagina - vedi pag.9, il modello di layout non
        # classifica affatto l'immagine, nemmeno a soglia di confidenza
        # 0.01), qui prendiamo l'oggetto immagine originale incorporato nel
        # PDF cosi' com'e'.
        #
        # Va fatto SOLO se il testo riconosciuto e' corto (vedi
        # TESTO_CORTO_SOGLIA): su una pagina con testo vero e sostanzioso
        # (es. pag.6, l'intero Indice) qualsiasi immagine incorporata
        # trovata, grande o piccola, e' quasi certamente parte dello stesso
        # sfondo/bordo decorativo del template grafico, non contenuto - non
        # va ne' mostrata ne' salvata su disco in quel caso (altrimenti si
        # accumulano centinaia di file inutili, uno sfondo quasi per pagina).
        if len(md_content.strip()) < TESTO_CORTO_SOGLIA:
            piena_pagina, posizionate = _estrai_immagini_native(doc, page, page_num, img_dir)
            if piena_pagina:
                tags = "\n".join(
                    f'<div style="text-align: center;"><img src="./images/{img["filename"]}" alt="Image" width="100%" /></div>'
                    for img in piena_pagina
                )
                md_content = f"{tags}\n\n{md_content}" if md_content.strip() else tags
            if posizionate:
                md_content = _inserisci_immagini_posizionate(md_content, posizionate)

        # Blocchi "aside_text" (testo a margine, quasi sempre crediti
        # illustratore accanto/dopo le tavole) - la libreria ha un gestore
        # per questa etichetta ma nella pratica il contenuto non arriva mai
        # nel markdown finale (verificato: 200/201 casi persi su tutto il
        # libro, 2026-08-11). Li recuperiamo qui direttamente dal JSON
        # strutturato invece di fidarci dell'output della libreria.
        if aside_texts:
            note = "\n\n".join(f"*(testo a margine: {t})*" for t in aside_texts)
            md_content = f"{md_content}\n\n{note}" if md_content.strip() else note

        if not md_content.strip():
            # Pagina senza alcun blocco riconosciuto con contenuto (ne' testo
            # ne' un ritaglio immagine, ne' immagini incorporate native
            # trovate sopra): tipicamente uno scan puro senza oggetti
            # immagine incorporati e senza testo riconosciuto. Il file .md
            # prodotto dalla pipeline in questo caso esiste ma e' vuoto (0
            # byte), non assente - per questo va controllato il CONTENUTO,
            # non solo se il file esiste. Fallback: usiamo l'intera pagina
            # gia' renderizzata come immagine della pagina, per non perderla.
            logging.warning("Pagina %s: nessun contenuto riconosciuto, uso l'intera pagina come immagine", page_num)
            new_name = f"img_pag_{page_num}_img1.png"
            shutil.copy(img_path, f"{img_dir}/{new_name}")
            md_content = f'<div style="text-align: center;"><img src="./images/{new_name}" alt="Image" width="100%" /></div>\n'

        file_path = f"{md_dir}/page_{page_num}.md"
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(md_content)

        shutil.rmtree(page_work_dir, ignore_errors=True)
        doc.close()

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
