"""Revisione titoli - passo 'scrivi bozza': dalle scelte (finale.json) alla bozza dei capitoli.

Scrive <out>/estrazione/bozze_capitoli/<progetto>.md nello stesso formato di Ricapitolando FASE 1
(# capitolo, ## paragrafo, ### ... sottoparagrafo; box come blockquote '> **[BOX: nome]**'),
cosi' la FASE 2 resta invariata. Se la bozza esiste gia' ne tiene una copia datata."""
import glob
import json
import os
import re
import shutil
import time

BASE = "/shared/projects/"
TIT = ("paragraph_title", "doc_title", "figure_title")


def norm(s):
    s = re.sub(r"<[^>]+>", " ", s)
    s = re.sub(r"[*_`#>|\\$^{}]", " ", s)
    return re.sub(r"\s+", " ", s).strip().lower()


def prima_riga(b):
    return (b.get("block_content") or "").strip().split("\n")[0]


def mappa_pagina(md_righe, blocchi):
    """idx blocco -> (riga inizio, riga fine) nel markdown, in ordine di lettura."""
    ord_ = sorted(range(len(blocchi)), key=lambda i: (blocchi[i].get("block_order") if blocchi[i].get("block_order") is not None else 10 ** 6 + i))
    inizio, cur = {}, 0
    for i in ord_:
        t = norm(prima_riga(blocchi[i]))
        if not t or blocchi[i]["block_label"] in ("image", "header_image", "footer_image"):
            continue
        for k in range(cur, len(md_righe)):
            ln = norm(md_righe[k])
            if ln and (ln == t or (len(t) >= 6 and (ln.startswith(t[:40]) or t in ln))):
                inizio[i] = k
                cur = k + 1
                break
    ordinati = sorted(inizio.items(), key=lambda x: x[1])
    fine = {}
    for n, (i, k) in enumerate(ordinati):
        fine[i] = (ordinati[n + 1][1] - 1) if n + 1 < len(ordinati) else len(md_righe) - 1
    return ord_, {i: (inizio[i], fine[i]) for i in inizio}


def main(progetto: str, cartella_output: str = ""):
    base_out = (cartella_output or "/shared/projects").rstrip("/")
    rev = base_out + "/" + progetto + "/revisione"
    fin = json.load(open(rev + "/finale.json", encoding="utf-8"))["titoli"]
    prop = json.load(open(rev + "/proposte.json", encoding="utf-8"))
    box = json.load(open(rev + "/box.json", encoding="utf-8")) if os.path.exists(rev + "/box.json") else []
    catalogo = json.load(open(BASE + "tools/catalogo_box.json"))
    voci_ok = {v["id"] for v in catalogo["voci"] if v.get("stato") == "confermato"}
    # nuovo metodo: i box sono voci della revisione (label "box") decise dall'umano; altrimenti si usa box.json col catalogo
    nuovo_metodo = any(p.get("label") == "box" for p in prop)
    if nuovo_metodo:
        box = [{"pag": p["pag"], "rect": p["rect"], "blocchi": p["blocchi"], "voci": ["revisione"]}
               for f, p in zip(fin, prop) if p.get("label") == "box" and f["classe"] == "box_senza_titolo"]
    if len(fin) != len(prop):
        raise Exception("finale.json (%d) e proposte.json (%d) non coincidono" % (len(fin), len(prop)))

    pagine_md = {}
    for f in glob.glob(BASE + progetto + "/markdown/page_*.md"):
        pagine_md[int(re.search(r"page_(\d+)\.md", f).group(1))] = open(f, encoding="utf-8").read()
    da_verificare, n_box, n_head = [], 0, 0
    out_lines = []  # (testo, pagina)
    ultimo_livello = 1

    for n in sorted(pagine_md):
        testo = re.sub(r"^# .+? - Pagina \d+\s*\n", "", pagine_md[n])
        righe = testo.split("\n")
        try:
            blocchi = json.load(open(BASE + progetto + "/json/pag_%d_res.json" % n))["parsing_res_list"]
        except OSError:
            blocchi = []
        ord_, mappa = mappa_pagina(righe, blocchi)
        base = [re.sub(r"^#{1,6}\s+", "", r) for r in righe]
        rimosse, titoli_riga, resto = set(), {}, {}

        def vicina(k, passo):
            j = k + passo
            while 0 <= j < len(base):
                if base[j].strip() and j not in rimosse:
                    return j
                j += passo
            return None

        # titoli di questa pagina, in ordine di lettura
        mie = [(i, f, p) for i, (f, p) in enumerate(zip(fin, prop)) if p["pag"] == n and p.get("label") != "box"]
        mie.sort(key=lambda x: (x[2]["pos"], x[0]))
        cima = []  # intestazioni "sintetiche" (cambio di intestazione corrente): in cima alla pagina
        for _, f, p in mie:
            cl, lv, un = f["classe"], f.get("livello"), f.get("unisci")
            if p.get("sintetico"):
                if cl == "titolo":
                    ultimo_livello = lv or ultimo_livello
                    cima.append("#" * min(lv or 1, 6) + " " + f["testo"].split("\n")[0].strip())
                continue
            idx = ord_[p["pos"]] if 0 <= p["pos"] < len(ord_) else None
            L = mappa.get(idx, (None,))[0] if idx is not None else None
            if cl == "titolo":
                ultimo_livello = lv or ultimo_livello
                if L is None:
                    da_verificare.append("p.%d titolo non trovato nel markdown: %r" % (n, p["testo"][:60]))
                    continue
                if L in rimosse:
                    continue
                if un == "dopo":
                    q = vicina(L, 1)
                    if q is None:
                        da_verificare.append("p.%d titolo spezzato senza blocco dopo: %r" % (n, p["testo"][:60]))
                    else:
                        base[L] = base[L].strip() + " " + base[q].strip()
                        rimosse.add(q)
                elif un == "prima":
                    q = vicina(L, -1)
                    if q is None:
                        da_verificare.append("p.%d titolo spezzato senza blocco prima: %r" % (n, p["testo"][:60]))
                    else:
                        base[q] = base[q].strip() + " " + base[L].strip()
                        rimosse.add(L)
                        L = q
                titoli_riga[L] = lv or 2
            elif cl == "titolo_unito_testo":
                if L is None:
                    da_verificare.append("p.%d titolo unito al testo non trovato: %r" % (n, p["testo"][:60]))
                    continue
                titolo = p["testo"].split("\n")[0].strip()
                riga = base[L].strip()
                rest = riga[len(titolo):].strip() if riga.lower().startswith(titolo.lower()) else ""
                lvl = min(ultimo_livello + 1, 6)
                base[L] = titolo
                titoli_riga[L] = lvl
                if rest:
                    resto[L] = rest
                da_verificare.append("p.%d %r: titolo unito al testo, livello assunto %d (uno sotto il titolo precedente)" % (n, titolo[:40], lvl))

        # box di questa pagina
        in_box = {}
        for b in [x for x in box if x["pag"] == n]:
            nome = None
            titoli_box = [f["testo"].split("\n")[0].strip() for i, (f, p) in enumerate(zip(fin, prop))
                          if p["pag"] == n and p.get("label") != "box" and f["classe"] == "box_titolo" and not p.get("sintetico") and ord_[p["pos"]] in b["blocchi"]]
            if titoli_box:
                nome = titoli_box[0]
            elif b.get("tipo") == "esempio_di_gioco":
                nome = "Esempio di gioco"
            elif b.get("tipo") == "nota_a_margine":
                nome = "Nota a margine"
            elif not nuovo_metodo and not (set(b["voci"]) & voci_ok):
                continue  # riquadro trovato solo da una regola ancora da confermare, senza titolo di box deciso
            righe_box = [mappa[i] for i in b["blocchi"] if i in mappa]
            if not righe_box:
                continue
            a, z = min(r[0] for r in righe_box), max(r[1] for r in righe_box)
            for k in range(a, z + 1):
                in_box[k] = (nome, k == a)
            n_box += 1

        for h in cima:
            out_lines.append((h, n))
            n_head += 1
        for k, r in enumerate(base):
            if k in rimosse:
                continue
            r = r if k not in titoli_riga else "#" * min(titoli_riga[k], 6) + " " + r.strip()
            if k in titoli_riga:
                n_head += 1
            if k in in_box:
                nome, primo = in_box[k]
                if primo:
                    out_lines.append(("> **[BOX%s]**" % (": " + nome if nome else ""), n))
                out_lines.append((("> " + r) if r.strip() else ">", n))
            else:
                out_lines.append((r, n))
            if k in resto:
                out_lines.append((resto[k], n))

    assembled = out_lines[0][0] if out_lines else ""
    for i in range(1, len(out_lines)):
        assembled += ("\n\n<!-- pb -->\n\n" if out_lines[i][1] != out_lines[i - 1][1] else "\n") + out_lines[i][0]
    assembled = re.sub(r"(\w)-\n\n<!-- pb -->\n\n(\w)", r"\1\2", assembled)
    assembled = re.sub(r"([^.!?:;»\")\]\n])\n\n<!-- pb -->\n\n([a-zà-ù])", r"\1 \2", assembled)
    assembled = re.sub(r"\n*<!-- pb -->\n*", "\n\n", assembled)
    assembled = re.sub(r"\n{3,}", "\n\n", assembled)

    legenda = "\n".join([
        "<!--",
        "  Bozza capitoli - %s (generata dalla REVISIONE TITOLI)" % progetto,
        "  Intestazioni scritte: %d - box: %d - voci da verificare: %d (vedi %s_da_verificare.txt)" % (n_head, n_box, len(da_verificare), progetto),
        "  Correggi il NUMERO DI CANCELLETTI direttamente sulle intestazioni se qualcosa non torna.",
        "  #=capitolo ##=paragrafo (apre un file) ###/####/...=sottoparagrafo (resta nel file del paragrafo)",
        "-->", ""])
    d = base_out + "/estrazione/bozze_capitoli" if cartella_output else "/shared/projects/estrazione/bozze_capitoli"
    os.makedirs(d, exist_ok=True)
    bozza = d + "/" + progetto + ".md"
    copia = None
    if os.path.exists(bozza):
        copia = bozza + ".bak-" + time.strftime("%Y%m%d-%H%M%S")
        shutil.copy2(bozza, copia)
    open(bozza, "w", encoding="utf-8").write(legenda + assembled)
    open(d + "/" + progetto + "_da_verificare.txt", "w", encoding="utf-8").write("Report revisione titoli - %s\n" % progetto + "\n".join(da_verificare) + "\n")
    return {"bozza": bozza, "copia_precedente": copia, "intestazioni": n_head, "box": n_box, "da_verificare": len(da_verificare)}
