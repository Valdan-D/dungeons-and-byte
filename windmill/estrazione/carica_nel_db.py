import glob, json, os, re
import wmill
import psycopg2
from psycopg2.extras import Json

OBBLIGATORI = {
    "incantesimo": ["livello", "scuola", "tempo_di_lancio", "gittata", "componenti", "durata", "descrizione"],
    "oggetto": ["categoria"],                    # la descrizione manca legittimamente per armi, armature ed equipaggiamento
    "classe": ["descrizione"],                   # dado_vita solo per le classi vere (le sottoclassi hanno variante_di)
    "specie": ["taglia", "velocita"],
    "background": ["competenze"],
}
SOLO_DESCRIZIONE = ["regola", "luogo", "bestiario_new", "bestiario_old"]

def N(s): return re.sub(r"\W+", " ", str(s).lower()).strip()

def metadati(progetto):
    """Anno, edizione e licenza dal nome del libro (stessa regola usata per il bestiario)."""
    m = re.findall(r"(?<!\d)((?:19|20)\d{2})(?!\d)", progetto)
    anno = int(m[-1]) if m else None
    cc = "IT_SRD_CC" in progetto
    if cc or "5.5" in progetto or (anno and anno >= 2024): ed = "2024"
    elif anno and anno >= 2014 or re.search(r"(?:^|[ -])5(?:[ -]|$)", progetto): ed = "2014"
    else: ed = "classico"
    slug = re.sub(r"[^a-z0-9]+", "-", progetto.lower()).strip("-")[:60]
    return {"slug": slug, "titolo": progetto, "anno": anno, "edizione": ed, "licenza": "CC-BY-4.0" if cc else "proprietario", "pubblico": cc}

def avvisi_di(tip, e):
    av = []
    nome = str(e.get("nome", "")).strip()
    if len(nome) < 3 or len(nome) > 80: av.append("nome di lunghezza sospetta")
    if re.fullmatch(r"[\d\W_]+", nome): av.append("nome senza lettere")
    for k in OBBLIGATORI.get(tip, []) + (["descrizione"] if tip in SOLO_DESCRIZIONE else []):
        v = e.get(k)
        if v is None or str(v).strip() == "": av.append(f"campo mancante: {k}")
    if tip == "classe" and not str(e.get("variante_di") or "").strip() and not str(e.get("dado_vita") or "").strip():
        av.append("campo mancante: dado_vita")
    if tip == "incantesimo":
        try:
            if not 0 <= int(e.get("livello")) <= 9: av.append("livello fuori da 0-9")
        except Exception: av.append("livello non numerico")
    return av

def main(progetto: str, carica_nel_db: bool = True, solo_controllo: bool = False, escludi_tipologie: list[str] = []):
    """Estrazione, passo 5: controlla le entita' di <progetto>/estrazione/entita.json e le carica nel database D&B (ruolo caricatore).
    Idempotente: le voci automatiche non piu' presenti vengono tolte, quelle 'verificate' non si toccano. 'solo_controllo' = prova senza scrivere."""
    if not carica_nel_db: return {"saltato": True, "motivo": "caricamento non richiesto"}
    d = f"/shared/projects/{progetto}/estrazione"
    E = json.load(open(f"{d}/entita.json"))["entita"]
    meta = metadati(progetto); rep = []; avvisi_tab = []; pronti = {}
    for tip, lista in E.items():
        if tip in escludi_tipologie: rep.append((tip, len(lista), 0, 0, "esclusa")); continue
        try: fin = {w["id"]: w for w in json.load(open(f"{d}/finestre_{tip}.json"))}
        except Exception: fin = {}
        ok = scartate = con_av = 0; righe_t = []
        for e in lista:
            e = dict(e); nome = str(e.get("nome", "")).strip(); f = e.pop("_finestra", None); fl = e.pop("_file", None)
            av = avvisi_di(tip, e)
            if not nome or "nome senza lettere" in av: scartate += 1; continue
            if av: con_av += 1; e["_avvisi"] = av; avvisi_tab += [{"tipologia": tip, "nome": nome, "avviso": a} for a in av]
            w = fin.get(f); r = (w["righe"][0], w["righe"][1]) if w else None
            if w and (w.get("file_fine", w["file"]) != w["file"] or r[0] > r[1]): r = None    # finestra a cavallo di piu' file: niente intervallo di righe
            righe_t.append((N(nome), nome, e, (w or {}).get("file", fl), r)); ok += 1
        pronti[tip] = righe_t; rep.append((tip, len(lista), ok, scartate, f"{con_av} con avvisi"))
    esito = "solo controllo (nulla scritto)"
    if not solo_controllo:
        db = wmill.get_resource("f/dnb/db_dnb_loader")
        with psycopg2.connect(host=db["host"], port=db["port"], user=db["user"], password=db["password"], dbname=db["dbname"], sslmode=db.get("sslmode", "prefer")) as c:
            with c.cursor() as cur:
                cur.execute("""INSERT INTO libri(slug,titolo,anno,edizione,licenza,pubblico) VALUES (%s,%s,%s,%s,%s,%s)
                               ON CONFLICT (slug) DO UPDATE SET titolo=EXCLUDED.titolo, anno=EXCLUDED.anno, edizione=EXCLUDED.edizione RETURNING id""",
                            (meta["slug"], meta["titolo"], meta["anno"], meta["edizione"], meta["licenza"], meta["pubblico"]))
                lid = cur.fetchone()[0]; scritte = tolte = 0
                for tip, righe_t in pronti.items():
                    nomi = [x[0] for x in righe_t]
                    for nn, nome, e, f, r in righe_t:
                        cur.execute("""INSERT INTO entita(libro_id,tipologia,nome,nome_norm,dati,file,righe,adapter) VALUES (%s,%s,%s,%s,%s,%s,CASE WHEN %s::int IS NULL THEN NULL ELSE int4range(%s::int,%s::int,'[]') END,%s)
                                       ON CONFLICT (libro_id,tipologia,nome_norm) DO UPDATE SET nome=EXCLUDED.nome, dati=EXCLUDED.dati, file=EXCLUDED.file, righe=EXCLUDED.righe, adapter=EXCLUDED.adapter, creato_il=now()
                                       WHERE entita.revisione = 'automatica'""",
                                    (lid, tip, nome, nn, Json(e), f, r[0] if r else None, r[0] if r else None, r[1] if r else None, f"lora_v5/{tip}"))
                        scritte += 1
                    cur.execute("DELETE FROM entita WHERE libro_id=%s AND tipologia=%s AND revisione='automatica' AND NOT (nome_norm = ANY(%s))", (lid, tip, nomi))
                    tolte += cur.rowcount
        esito = f"scritte/aggiornate {scritte}, tolte perche' non piu' estratte {tolte}"
    righe = ["| Tipologia | Estratte | Caricabili | Scartate | Note |", "|---|---:|---:|---:|---|"] + [f"| {t} | {a} | {b} | {c} | {n} |" for t, a, b, c, n in rep]
    md = (f"**{progetto}** - libro `{meta['slug']}` (edizione {meta['edizione']}, licenza {meta['licenza']}, pubblico: {'si' if meta['pubblico'] else 'no'})\n\n"
          f"Esito: {esito}\n\n" + "\n".join(righe))
    return {"render_all": [{"markdown": md}, {"table-row-object": avvisi_tab[:300]}]}
