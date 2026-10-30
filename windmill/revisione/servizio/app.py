"""Servizio di revisione titoli e box (D&B).

Dati per libro in <BASE>/<libro>/revisione/:
  proposte.json    proposte automatiche (titoli in ordine di lettura, con in_box)
  stato.json       {"stato": "in_revisione" | "chiuso", "resume_url": opzionale}
  decisioni.jsonl  log delle scelte (una riga per scelta, con data)
  finale.json      risultato a revisione conclusa
Il PDF resta in <BASE>/pool/<libro>.pdf; le pagine si disegnano al momento.
"""
import hmac
import io
import json
import os
import time
import urllib.parse
import urllib.request

import pymupdf
from flask import Flask, abort, jsonify, request, send_file

BASE = os.environ.get("DNB_BASE", "/shared/projects")
CLASSI = {"titolo", "tabella", "non_titolo", "box_titolo", "box_senza_titolo", "immagine", "titolo_unito_testo", "elenco", "non_so"}
UNISCI = {None, "dopo", "prima"}
app = Flask(__name__)
FILE_CHIAVE = os.environ.get("DNB_KEYFILE", "/etc/dnb_revisione.key")


ACCETTA_DA = {a.strip() for a in os.environ.get("DNB_ALLOW", "").split(",") if a.strip()}


@app.before_request
def controlla_chiave():
    """DNB_ALLOW: indirizzi da cui si accettano richieste (il proxy davanti al servizio; il
    loopback e' sempre ammesso). Se esiste anche il file chiave serve l'intestazione X-Revisione-Key.
    Senza nessuno dei due il servizio resta aperto (solo prove)."""
    if ACCETTA_DA and request.remote_addr not in ACCETTA_DA | {"127.0.0.1", "::1"}:
        abort(403)
    try:
        chiave = open(FILE_CHIAVE, encoding="utf-8").read().strip()
    except OSError:
        return None
    if not chiave or not hmac.compare_digest(request.headers.get("X-Revisione-Key", ""), chiave):
        abort(403)


def cartella(libro):
    if not libro or "/" in libro or ".." in libro or libro.startswith("."):
        abort(404)
    p = os.path.join(BASE, libro, "revisione")
    if not os.path.isdir(p):
        abort(404)
    return p


def leggi(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def stato(libro):
    return leggi(os.path.join(cartella(libro), "stato.json"), {}).get("stato")


def decisioni(libro):
    """Decisioni effettive: l'ultima per ogni titolo; 'annulla' toglie l'ultima messa."""
    eff, pila = {}, []
    try:
        with open(os.path.join(cartella(libro), "decisioni.jsonl"), encoding="utf-8") as f:
            for riga in f:
                if not riga.strip():
                    continue
                d = json.loads(riga)
                if d.get("annulla"):
                    if pila:
                        i, prima = pila.pop()
                        if prima is None:
                            eff.pop(i, None)
                        else:
                            eff[i] = prima
                else:
                    pila.append((d["id"], eff.get(d["id"])))
                    eff[d["id"]] = d
    except OSError:
        pass
    return eff


def dati(libro):
    prop = leggi(os.path.join(cartella(libro), "proposte.json"), [])
    dec = decisioni(libro)
    out = []
    for i, t in enumerate(prop):
        d = dec.get(i)
        classe = d["classe"] if d else (t["proposta"] if t["proposta"] in CLASSI else "titolo")
        if not d and t.get("in_box"):
            classe = "box_titolo"
        livello = d.get("livello") if d else t.get("livello_proposto")
        out.append({"id": i, "pag": t["pag"], "testo": t["testo"], "label": t["label"], "motivo": t.get("motivo", ""),
                    "proposta": t["proposta"], "in_box": bool(t.get("in_box")), "classe": classe,
                    "livello": livello if classe == "titolo" else None, "deciso": d is not None,
                    "unisci": d.get("unisci") if d else None, "prima": t.get("prima"), "dopo": t.get("dopo"),
                    "rect": t.get("rect")})
    return out


@app.get("/api/libri")
def api_libri():
    libri = []
    for nome in sorted(os.listdir(BASE)):
        p = os.path.join(BASE, nome, "revisione", "stato.json")
        if os.path.isfile(p) and leggi(p, {}).get("stato") == "in_revisione":
            d = dati(nome)
            libri.append({"libro": nome, "titoli": len(d), "decisi": sum(1 for x in d if x["deciso"])})
    return jsonify(libri)


@app.get("/api/<libro>/dati")
def api_dati(libro):
    if stato(libro) not in ("in_revisione", "chiuso"):
        abort(404)
    return jsonify({"libro": libro, "stato": stato(libro), "titoli": dati(libro)})


@app.post("/api/<libro>/decisione")
def api_decisione(libro):
    if stato(libro) != "in_revisione":
        abort(409, "libro non in revisione")
    b = request.get_json(force=True)
    n = len(leggi(os.path.join(cartella(libro), "proposte.json"), []))
    if b.get("annulla"):
        riga = {"annulla": True, "ts": time.strftime("%Y-%m-%dT%H:%M:%S")}
    else:
        if not isinstance(b.get("id"), int) or not 0 <= b["id"] < n or b.get("classe") not in CLASSI:
            abort(400)
        liv = b.get("livello")
        if liv is not None and (not isinstance(liv, int) or not 1 <= liv <= 6):
            abort(400)
        unisci = b.get("unisci")
        if unisci not in UNISCI or (unisci and b["classe"] != "titolo"):
            abort(400)
        riga = {"id": b["id"], "classe": b["classe"], "livello": liv, "unisci": unisci, "ts": time.strftime("%Y-%m-%dT%H:%M:%S")}
    with open(os.path.join(cartella(libro), "decisioni.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps(riga, ensure_ascii=False) + "\n")
    return jsonify({"ok": True})


@app.post("/api/<libro>/concludi")
def api_concludi(libro):
    c = cartella(libro)
    st = leggi(os.path.join(c, "stato.json"), {})
    if st.get("stato") != "in_revisione":
        abort(409, "libro non in revisione")
    d = dati(libro)
    json.dump({"libro": libro, "chiuso": time.strftime("%Y-%m-%dT%H:%M:%S"), "titoli": d},
              open(os.path.join(c, "finale.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    st["stato"] = "chiuso"
    json.dump(st, open(os.path.join(c, "stato.json"), "w"), ensure_ascii=False)
    ripresa = None
    if st.get("resume_url"):
        try:
            u = urllib.parse.urlparse(st["resume_url"])
            locale = os.environ.get("DNB_WINDMILL", "").rstrip("/")  # Windmill nello stesso container: niente giro da Cloudflare
            url = locale + u.path + ("?" + u.query if u.query else "") if locale else st["resume_url"]
            req = urllib.request.Request(url, data=b"{}", headers={"Content-Type": "application/json", "Host": u.netloc}, method="POST")
            ripresa = urllib.request.urlopen(req, timeout=20).status
        except Exception as e:  # il libro resta chiuso; la ripresa si puo' rifare a mano
            ripresa = "errore: %s" % e
    return jsonify({"ok": True, "titoli": len(d), "ripresa": ripresa})


@app.get("/img/<libro>/<int:pag>.jpg")
def img(libro, pag):
    cartella(libro)
    pdf = os.path.join(BASE, "pool", libro + ".pdf")
    if not os.path.isfile(pdf):
        abort(404)
    doc = pymupdf.open(pdf)
    if not 1 <= pag <= len(doc):
        abort(404)
    z = float(request.args.get("z", 1.5))
    pagina = doc[pag - 1]
    try:  # riquadro del candidato box (punti PDF): lo si disegna in rosso sulla pagina, solo in memoria
        x0, y0, x1, y1 = [float(v) for v in request.args.get("box", "").split(",")]
        pagina.draw_rect(pymupdf.Rect(x0 - 6, y0 - 6, x1 + 6, y1 + 6), color=(0.9, 0, 0), width=2)  # qualche punto fuori dal bordo: la cornice vera resta visibile
    except ValueError:
        pass
    pix = pagina.get_pixmap(matrix=pymupdf.Matrix(z, z), colorspace=pymupdf.csRGB, alpha=False)
    r = send_file(io.BytesIO(pix.tobytes("jpg", jpg_quality=78)), mimetype="image/jpeg")
    r.headers["Cache-Control"] = "private, max-age=3600"
    return r


@app.get("/")
def home():
    return send_file(os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.html"))


if __name__ == "__main__":
    app.run(host=os.environ.get("DNB_HOST", "127.0.0.1"), port=int(os.environ.get("DNB_PORT", "8095")))
