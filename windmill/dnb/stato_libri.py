#requirements:
#pymupdf==1.28.0
#psycopg2-binary
#wmill
import glob, json, os, re, time, urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo
RM = ZoneInfo('Europe/Rome')
import wmill

P = "/shared/projects"
RUNNER = "http://<GPU_HOST>:8095"

def edizione(nome):
    m = re.findall(r"(?<!\d)((?:19|20)\d{2})(?!\d)", nome)
    if m: return "5e" if int(m[-1]) >= 2014 else "classico"
    return "5e" if (re.search(r"(?:^|[ -])5(?:\.5)?(?:[ -]|$)", nome) or "5.2.1" in nome) else "?"

def quando(path):
    return datetime.fromtimestamp(os.path.getmtime(path), RM).strftime("%d/%m %H:%M") if os.path.exists(path) else ""

def runner():
    try:
        req = urllib.request.Request(RUNNER + "/stato", headers={"X-Runner-Token": os.environ["RUNNER_TOKEN"]})
        with urllib.request.urlopen(req, timeout=15) as r: s = json.load(r)
        ocr = "ACCESO" if any(v == "active" for v in s["ocr"].values()) else "spento"
        gpu = "occupata: " + "; ".join(p.split()[-3][:40] for p in s["processi_attivi"]) if s["processi_attivi"] else "libera"
        return f"OCR **{ocr}** · GPU **{gpu}**"
    except Exception as e:
        return f"stato OCR/GPU non disponibile ({e})"

def main(solo_cartella_attiva: bool = False):
    """Sola lettura. Per ogni libro: pagine OCR, capitoli, etichette, estrazione, caricamento nel database, e il PROSSIMO PASSO."""
    import fitz, psycopg2
    db = wmill.get_resource("f/dnb/db_dnb_loader"); nel_db = {}
    try:
        with psycopg2.connect(host=db["host"], port=db["port"], user=db["user"], password=db["password"], dbname=db["dbname"], sslmode=db.get("sslmode", "prefer"), connect_timeout=5) as c:
            with c.cursor() as cur:
                cur.execute("SELECT l.titolo, count(e.id), l.pubblico FROM libri l LEFT JOIN entita e ON e.libro_id = l.id GROUP BY l.titolo, l.pubblico")
                nel_db = {t: (n, pub) for t, n, pub in cur.fetchall()}
    except Exception as e:
        nel_db = None
    righe = []
    for d in sorted(os.listdir(P)):
        base = f"{P}/{d}"
        if not (os.path.isdir(f"{base}/markdown") or os.path.isdir(f"{base}/capitoli_ricapitolati")): continue
        pdf = f"{P}/pool/{d}.pdf"
        try: tot = fitz.open(pdf).page_count if os.path.exists(pdf) else None
        except Exception: tot = None
        md = len(glob.glob(f"{base}/markdown/page_*.md"))
        cap = len(glob.glob(f"{base}/capitoli_ricapitolati/*.md"))
        ed = edizione(d)
        et = f"{base}/etichette/etichette.json"; en = f"{base}/estrazione/entita.json"
        n_unita = ""
        if os.path.exists(et):
            try: n_unita = str(json.load(open(et))["unita_totali"]) + " unità"
            except Exception: pass
        lora = []
        for st in sorted(glob.glob(f"{base}/estrazione/*.jsonl.stato")):
            try: s = json.load(open(st))
            except Exception: continue
            lora.append(f"{s['tipologia']} {'✓' if s['fatte'] >= s['totale'] else str(s['fatte']) + '/' + str(s['totale'])}")
        ent = ""
        if os.path.exists(en):
            try: ent = f"{sum(len(v) for v in json.load(open(en))['entita'].values())} entità"
            except Exception: pass
        n_db = (nel_db or {}).get(d, (0, False))[0] if nel_db is not None else None
        ocr_ok = md > 0 and (tot is None or md >= tot)
        if not ocr_ok: prossimo = "1 · Digitalizza (OCR)" if md == 0 else f"1 · Completa l'OCR ({md}/{tot} pagine)"
        elif cap == 0: prossimo = "2 · Ricapitola (fase 1, correggi la bozza, fase 2)"
        elif ed == "classico": prossimo = "in attesa: il classificatore non è ancora addestrato sui manuali classici"
        elif not os.path.exists(et): prossimo = "3 · Etichetta"
        elif not ent: prossimo = "4 · Estrai"
        elif nel_db is None: prossimo = "(database non raggiungibile)"
        elif not n_db: prossimo = "Carica nel database (flusso Estrazione con 'carica nel database', o script carica_nel_db)"
        else: prossimo = "completo ✓"
        righe.append({"libro": d[:60], "edizione": ed, "OCR (pagine)": f"{md}/{tot if tot else '?'}", "capitoli": cap,
                      "etichette": (n_unita + " · " + quando(et)) if n_unita else "—", "estrazione": (", ".join(lora) or "—") + ((" · " + ent) if ent else ""),
                      "database": ("—" if not n_db else f"{n_db} voci"), "prossimo passo": prossimo})
    intestazione = f"**Stato dei libri** · {datetime.now(RM).strftime('%d/%m/%Y %H:%M')} · {runner()}"
    return {"render_all": [{"markdown": intestazione}, {"table-row-object": righe}]}
