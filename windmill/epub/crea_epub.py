#requirements:
#markdown
#lxml
#pillow
#wmill
import glob, hashlib, io, html, mimetypes, os, re, uuid, zipfile
from datetime import datetime, timezone

import markdown
from lxml import etree, html as lhtml

P = "/shared/projects"
KB_DEFAULT = 80          # dimensione massima (KB di HTML) di un file del libro: i capitoli lunghi vengono raggruppati
CSS = """
body { font-family: serif; line-height: 1.4; margin: 0 .6em; }
h1 { font-size: 1.7em; margin: 1.2em 0 .5em; page-break-before: always; }
h2 { font-size: 1.35em; margin: 1.3em 0 .4em; }
h3 { font-size: 1.1em; margin: 1em 0 .3em; }
p { margin: .5em 0; text-align: left; }
img { max-width: 100%; height: auto; }
div.centro { text-align: center; }
table { border-collapse: collapse; margin: .8em auto; width: 100%; max-width: 100%; font-size: .85em; table-layout: auto; }
td, th { border: 1px solid #888; padding: .2em .35em; vertical-align: top; overflow-wrap: break-word; }
table.t8 { font-size: .75em; } table.t8 td, table.t8 th { padding: .12em .2em; }
table.t11 td, table.t11 th, table.t14 td, table.t14 th { overflow-wrap: anywhere; }
table.t11 { font-size: .62em; } table.t11 td, table.t11 th { padding: .08em .1em; }
table.t14 { font-size: .5em; } table.t14 td, table.t14 th { padding: .05em .06em; }
th { background: #ddd; }
hr { margin: 1.2em 0; }
.titolo-libro { text-align: center; margin-top: 30%; }
.titolo-libro h1 { page-break-before: avoid; font-size: 2em; }
"""

def tipo_media(fn):
    return {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".gif": "image/gif",
            ".webp": "image/webp", ".svg": "image/svg+xml"}.get(os.path.splitext(fn)[1].lower()) or mimetypes.guess_type(fn)[0]

def riduci(percorso, larg_max, qualita, grigio):
    """Ridimensiona (solo se piu' larga di larg_max) e ricomprime in JPEG; trasparenze su fondo bianco."""
    from PIL import Image
    im = Image.open(percorso)
    if im.mode in ("RGBA", "LA", "P"):
        im = im.convert("RGBA"); fondo = Image.new("RGB", im.size, "white"); fondo.paste(im, mask=im.split()[-1]); im = fondo
    im = im.convert("L" if grigio else "RGB")
    if im.width > larg_max: im = im.resize((larg_max, max(1, round(im.height * larg_max / im.width))), Image.LANCZOS)
    b = io.BytesIO(); im.save(b, "JPEG", quality=qualita, optimize=True); return b.getvalue()

def xhtml(titolo, corpo):
    return ('<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE html>\n'
            '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="it" lang="it">\n'
            f'<head><meta charset="utf-8"/><title>{html.escape(titolo)}</title><link rel="stylesheet" type="text/css" href="style.css"/></head>\n'
            f'<body>\n{corpo}\n</body>\n</html>\n')

def titolo_da_nome(p):
    return re.sub(r"\s+-\s+(Manuale|Avventura|Avventura Singola|Ambientazione).*$", "", p).replace("_", " ").strip()

def main(progetto: str, titolo: str = "", autore: str = "", dimensione_kb: int = KB_DEFAULT, cartella_uscita: str = "", escludi_indice_stampato: bool = True,
         ottimizza_immagini: bool = True, larghezza_max_px: int = 1100, qualita_jpeg: int = 70, scala_di_grigi: bool = False):
    """Crea un EPUB3 dai capitoli ricapitolati di un progetto (testo corretto, immagini da images/). Sola lettura sul progetto; scrive <progetto>/epub/<titolo>.epub."""
    if "/" in progetto or progetto.startswith("."): raise Exception("nome progetto non valido")
    dimensione_kb = dimensione_kb or KB_DEFAULT; titolo = titolo or ""; autore = autore or ""; cartella_uscita = cartella_uscita or ""
    escludi_indice_stampato = True if escludi_indice_stampato is None else escludi_indice_stampato
    ottimizza_immagini = True if ottimizza_immagini is None else ottimizza_immagini
    larghezza_max_px = larghezza_max_px or 1100; qualita_jpeg = min(95, max(30, qualita_jpeg or 70)); scala_di_grigi = bool(scala_di_grigi)
    base = f"{P}/{progetto}"; cap = f"{base}/capitoli_ricapitolati"
    files = sorted(glob.glob(f"{cap}/*.md"))
    if not files: raise Exception(f"Nessun capitolo in {cap}: serve il passo 'Ricapitola'.")
    titolo = titolo.strip() or titolo_da_nome(progetto)
    md = markdown.Markdown(extensions=["tables", "sane_lists"])
    sezioni = []                # {id, titolo, livello, html}
    immagini = {}               # nome file -> percorso su disco
    avvisi = []
    for n, f in enumerate(files):
        testo = open(f, errors="ignore").read().strip()
        if not testo: continue
        if escludi_indice_stampato and re.search(r"^\d+-(contents|indice|sommario)", os.path.basename(f), re.I):
            avvisi.append(f"{os.path.basename(f)}: indice stampato escluso (ha i numeri di pagina)"); continue
        md.reset()
        h = md.convert(testo)
        # normalizza in XHTML ben formato; immagini verso images/, mancanti scartate
        try:
            frag = lhtml.fragment_fromstring(h, create_parent="div")
        except Exception as e:
            avvisi.append(f"{os.path.basename(f)}: html non leggibile ({e})"); continue
        for img in frag.iter("img"):
            src = img.get("src", "")
            nome = os.path.basename(src)
            percorso = f"{base}/images/{nome}"
            if nome and os.path.exists(percorso):
                nuovo = (os.path.splitext(nome)[0] + ".jpg") if (ottimizza_immagini and not nome.lower().endswith(".svg")) else nome
                immagini[nuovo] = percorso; img.set("src", f"images/{nuovo}")
                if not img.get("alt"): img.set("alt", "")
            else:
                avvisi.append(f"{os.path.basename(f)}: immagine mancante {nome}")
                img.drop_tree()
        for el in frag.iter():
            for a in ("width", "height") if el.tag == "img" else ():
                if el.get(a, "").endswith("%"): del el.attrib[a]
        for t in frag.iter("table"):     # tabelle larghe: carattere e margini ridotti per entrare nella pagina (negli e-reader non si scorre di lato)
            cols = 0
            for tr in t.iter("tr"):
                cols = max(cols, sum(int(c.get("colspan") or 1) if str(c.get("colspan") or "1").isdigit() else 1 for c in tr if c.tag in ("td", "th")))
            cl = "t14" if cols >= 14 else "t11" if cols >= 11 else "t8" if cols >= 8 else ""
            if cl: t.set("class", cl)
            for a in ("style", "border"): t.attrib.pop(a, None)
        titolo_h = None; livello = 2
        for el in frag.iter("h1", "h2", "h3"):
            titolo_h = " ".join("".join(el.itertext()).split()); livello = int(el.tag[1]); break
        sid = f"s{n:04d}"
        corpo = "".join(etree.tostring(c, encoding="unicode", method="xml") for c in frag) if len(frag) else ""
        corpo = html.escape(frag.text) + corpo if frag.text and frag.text.strip() else corpo
        if not corpo.strip(): continue
        sezioni.append({"id": sid, "titolo": titolo_h or re.sub(r"[-_]+", " ", re.sub(r"^\d+-", "", os.path.splitext(os.path.basename(f))[0])).strip().capitalize(), "livello": livello,
                        "html": f'<section id="{sid}">\n{corpo}\n</section>'})
    # raggruppa in file xhtml: nuovo file a ogni titolo di livello 1 o quando supera il limite
    gruppi = []; cur = None
    for s in sezioni:
        if cur is None or s["livello"] == 1 or cur["kb"] + len(s["html"]) / 1024 > dimensione_kb:
            cur = {"file": f"cap{len(gruppi):04d}.xhtml", "sez": [], "kb": 0}; gruppi.append(cur)
        cur["sez"].append(s); cur["kb"] += len(s["html"]) / 1024
        s["href"] = f'{cur["file"]}#{s["id"]}'
    # indice: parti (livello 1) con sezioni sotto; se non ci sono livelli 1 le sezioni stanno in piano
    nav_voci = []; parte = None
    for s in sezioni:
        if s["livello"] == 1: parte = {"s": s, "figli": []}; nav_voci.append(parte)
        elif parte: parte["figli"].append(s)
        else: nav_voci.append({"s": s, "figli": []})
    def li(v):
        s = v["s"]; r = f'<li><a href="{s["href"]}">{html.escape(s["titolo"])}</a>'
        if v["figli"]: r += "<ol>" + "".join(f'<li><a href="{c["href"]}">{html.escape(c["titolo"])}</a></li>' for c in v["figli"]) + "</ol>"
        return r + "</li>"
    ident = "urn:uuid:" + str(uuid.uuid5(uuid.NAMESPACE_URL, "dnb/" + progetto))
    adesso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    nav = ('<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE html>\n<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="it" lang="it">'
           f'<head><meta charset="utf-8"/><title>Indice</title><link rel="stylesheet" type="text/css" href="style.css"/></head><body>'
           '<nav epub:type="toc" id="toc"><h1>Indice</h1><ol>' + "".join(li(v) for v in nav_voci) + '</ol></nav></body></html>\n')
    frontespizio = xhtml(titolo, f'<div class="titolo-libro"><h1>{html.escape(titolo)}</h1>' + (f"<p>{html.escape(autore)}</p>" if autore else "") + "</div>")
    manifest = [("nav", "nav.xhtml", "application/xhtml+xml", ' properties="nav"'), ("css", "style.css", "text/css", ""),
                ("front", "front.xhtml", "application/xhtml+xml", "")]
    spine = ["front"]
    for i, g in enumerate(gruppi):
        manifest.append((f"c{i}", g["file"], "application/xhtml+xml", "")); spine.append(f"c{i}")
    for k, nome in enumerate(sorted(immagini)):
        manifest.append((f"i{k}", f"images/{nome}", tipo_media(nome) or "application/octet-stream", ""))
    opf = ('<?xml version="1.0" encoding="utf-8"?>\n<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="uid" xml:lang="it">'
           f'<metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:identifier id="uid">{ident}</dc:identifier><dc:title>{html.escape(titolo)}</dc:title>'
           '<dc:language>it</dc:language>' + (f"<dc:creator>{html.escape(autore)}</dc:creator>" if autore else "") +
           f'<meta property="dcterms:modified">{adesso}</meta></metadata><manifest>' +
           "".join(f'<item id="{i}" href="{h}" media-type="{m}"{p}/>' for i, h, m, p in manifest) +
           '</manifest><spine>' + "".join(f'<itemref idref="{i}"/>' for i in spine) + '</spine></package>\n')
    contenitore = ('<?xml version="1.0"?>\n<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
                   '<rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>\n')
    uscita = cartella_uscita.strip() or f"{base}/epub"
    os.makedirs(uscita, exist_ok=True)
    nome_file = re.sub(r"[^\w\- ]+", "", titolo).strip().replace(" ", "_") + ".epub"
    dest = f"{uscita}/{nome_file}"
    peso_prima = peso_dopo = 0
    with zipfile.ZipFile(dest + ".tmp", "w") as z:
        z.writestr(zipfile.ZipInfo("mimetype"), "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        z.writestr("META-INF/container.xml", contenitore, compress_type=zipfile.ZIP_DEFLATED)
        for nome, contenuto in (("content.opf", opf), ("nav.xhtml", nav), ("style.css", CSS), ("front.xhtml", frontespizio)):
            z.writestr("OEBPS/" + nome, contenuto, compress_type=zipfile.ZIP_DEFLATED)
        for g in gruppi:
            z.writestr("OEBPS/" + g["file"], xhtml(g["sez"][0]["titolo"], "\n".join(s["html"] for s in g["sez"])), compress_type=zipfile.ZIP_DEFLATED)
        for nome, percorso in immagini.items():
            if ottimizza_immagini and nome.lower().endswith(".jpg") and not percorso.lower().endswith(".svg"):
                dati = riduci(percorso, larghezza_max_px, qualita_jpeg, scala_di_grigi)
                peso_prima += os.path.getsize(percorso); peso_dopo += len(dati)
                z.writestr("OEBPS/images/" + nome, dati, compress_type=zipfile.ZIP_STORED)
            else:
                z.write(percorso, "OEBPS/images/" + nome, compress_type=zipfile.ZIP_STORED)
    os.replace(dest + ".tmp", dest)
    # verifica: ogni xhtml/opf/nav e' XML ben formato, ogni link dell'indice e il manifest puntano a qualcosa che esiste
    problemi = []
    with zipfile.ZipFile(dest) as z:
        nomi = set(z.namelist())
        if z.namelist()[0] != "mimetype": problemi.append("mimetype non e' il primo file")
        ids = {}
        for nome in nomi:
            if nome.endswith((".xhtml", ".opf")) or nome == "META-INF/container.xml":
                try:
                    r = etree.fromstring(z.read(nome))
                    if nome.endswith(".xhtml"): ids[nome.split("/", 1)[1]] = set(r.xpath("//@id"))
                except Exception as e: problemi.append(f"{nome}: XML non valido ({e})")
        for _, h, _, _ in manifest:
            if "OEBPS/" + h not in nomi: problemi.append(f"manifest: manca {h}")
        for v in nav_voci:
            for s in [v["s"]] + v["figli"]:
                f_, a_ = s["href"].split("#")
                if a_ not in ids.get(f_, set()): problemi.append(f"indice: ancora mancante {s['href']}")
    kb = os.path.getsize(dest) / 1024
    tab = [{"voce": "File", "valore": dest}, {"voce": "Dimensione", "valore": f"{kb:.0f} KB"},
           {"voce": "Sezioni", "valore": len(sezioni)}, {"voce": "File xhtml", "valore": len(gruppi)},
           {"voce": "Voci dell'indice (primo livello)", "valore": len(nav_voci)}, {"voce": "Immagini", "valore": len(immagini)},
           {"voce": "Peso immagini", "valore": (f"{peso_prima/1048576:.1f} MB -> {peso_dopo/1048576:.1f} MB" if peso_prima else "originali")},
           {"voce": "Avvisi", "valore": len(avvisi)}, {"voce": "Problemi di verifica", "valore": len(problemi)}]
    md_out = f"**{titolo}** → `{dest}`\n\n" + ("Verifica interna: **nessun problema**." if not problemi else "Verifica interna: **PROBLEMI** (vedi tabella).")
    return {"render_all": [{"markdown": md_out}, {"table-row-object": tab},
                           {"table-row-object": [{"tipo": "avviso", "dettaglio": a} for a in avvisi[:100]] + [{"tipo": "problema", "dettaglio": p} for p in problemi[:100]]}]}
