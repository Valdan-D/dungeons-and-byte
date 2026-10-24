#requirements:
#psycopg2-binary
#wmill
import glob, json, os, re, unicodedata
import wmill

P = "/shared/projects"
# riga "tipo + taglia" che apre la scheda di una creatura (titolo ##### o ######)
TT = re.compile(r"^#{5,6}\s+(Aberrazione|Bestia|Celestiale|Costrutto|Drago|Elementale|Folletto|Fata|Gigante|Immondo|Mostruosit|Non\s*morto|Melma|Pianta|Vegetale|Umanoide)", re.I)

def norm(s):
    s = unicodedata.normalize("NFKD", s or "")
    return re.sub(r"[^a-z0-9]", "", "".join(c for c in s if not unicodedata.combining(c)).lower())

def blocchi_creatura(testo):
    """Per ogni incantesimo (titolo ####) le schede creatura che contiene: [{incantesimo_titolo, nome, tipo, testo, riga}]."""
    L = testo.split("\n"); out = []; spell = None; i = 0
    posizioni = []                     # (indice riga titolo-incantesimo, titolo)
    for k, l in enumerate(L):
        if re.match(r"#### ", l): posizioni.append((k, l[5:].strip()))
    def titolo_sopra(k):
        t = None
        for pk, pt in posizioni:
            if pk <= k: t = (pk, pt)
            else: break
        return t
    inizi = []                         # (indice inizio blocco, indice riga tipo, titolo spell)
    for k, l in enumerate(L):
        if not TT.match(l): continue
        sp = titolo_sopra(k)
        if not sp: continue
        s = k
        j = k - 1
        while j > sp[0] and not L[j].strip(): j -= 1
        cand = L[j].strip() if j > sp[0] else ""
        if cand and len(cand) <= 60 and not cand.startswith(("#", "<", "*(", "|")) and not cand.endswith("."):
            s = j
        inizi.append((s, k, sp))
    for n, (s, k, sp) in enumerate(inizi):
        fine = len(L)
        nxt_sp = [pk for pk, _ in posizioni if pk > k]
        if nxt_sp: fine = nxt_sp[0]
        if n + 1 < len(inizi) and inizi[n + 1][0] < fine and inizi[n + 1][2][0] == sp[0]: fine = inizi[n + 1][0]
        corpo = [x for x in L[s:fine] if not x.strip().startswith("*(testo a margine")]
        tipo = L[k].strip("# ").strip()
        nome = L[s].strip() if s != k else tipo
        out.append({"incantesimo_titolo": sp[1], "nome": nome, "tipo": tipo, "testo": "\n".join(corpo).strip(), "riga": k + 1})
    return out

def main(progetto: str, solo_controllo: bool = True):
    """Collega le schede delle creature evocate (titoli ##### sotto un incantesimo ####) all'incantesimo che le evoca: campo `creature_evocate` in dati. Scarta le voci bestiario sbagliate nate dai capitoli degli incantesimi. Ripetibile: va rilanciata dopo ogni nuovo caricamento del libro."""
    import psycopg2
    from psycopg2.extras import Json
    solo_controllo = True if solo_controllo is None else solo_controllo
    if "/" in progetto or progetto.startswith("."): raise Exception("nome progetto non valido")
    db = wmill.get_resource("f/dnb/db_dnb_loader")
    righe_tab = []; non_trovati = []; da_scrivere = []
    with psycopg2.connect(host=db["host"], port=db["port"], user=db["user"], password=db["password"], dbname=db["dbname"], sslmode=db.get("sslmode", "prefer")) as c:
        with c.cursor() as cur:
            cur.execute("SELECT id FROM libri WHERE titolo=%s", (progetto,)); r = cur.fetchone()
            if not r: raise Exception("libro non presente nel database: caricalo prima")
            lid = r[0]
            cur.execute("SELECT id, nome, revisione FROM entita WHERE libro_id=%s AND tipologia='incantesimo'", (lid,))
            spells = [(i, n, norm(n), rev) for i, n, rev in cur.fetchall()]
            per_file = {}
            for f in sorted(glob.glob(f"{P}/{progetto}/capitoli_ricapitolati/*.md")):
                bl = blocchi_creatura(open(f, errors="ignore").read())
                if bl: per_file[os.path.basename(f)] = bl
            raccolte = {}      # id incantesimo -> lista creature
            for fn, bl in per_file.items():
                for b in bl:
                    nt = norm(b["incantesimo_titolo"])
                    cand = [s for s in spells if s[2] and nt.startswith(s[2])]
                    if not cand: non_trovati.append({"file": fn, "incantesimo_nel_testo": b["incantesimo_titolo"][:50], "creatura": b["nome"][:40]}); continue
                    lungo = max(len(s[2]) for s in cand)
                    for s in [s for s in cand if len(s[2]) == lungo]:
                        raccolte.setdefault(s[0], {"nome": s[1], "rev": s[3], "lista": []})["lista"].append(
                            {"nome": b["nome"], "tipo": b["tipo"], "testo": b["testo"], "file": fn, "riga": b["riga"]})
            for sid, v in raccolte.items():
                righe_tab.append({"incantesimo": v["nome"], "creature": " | ".join(x["nome"][:28] for x in v["lista"]), "caratteri": sum(len(x["testo"]) for x in v["lista"]), "revisione": v["rev"]})
                da_scrivere.append((sid, v))
            # voci bestiario nate da un blocco creatura GIA' agganciato a un incantesimo (la finestra di righe contiene la riga del blocco)
            agganciati = {(x["file"], x["riga"]) for _, v in da_scrivere for x in v["lista"]}
            cur.execute("SELECT id, nome, file, lower(righe), upper(righe) FROM entita WHERE libro_id=%s AND tipologia IN ('bestiario_new','bestiario_old') AND file ILIKE '%%incantesim%%' AND revisione='automatica' AND righe IS NOT NULL", (lid,))
            sbagliati = [(i, n) for i, n, f, lo, hi in cur.fetchall() if any(ff == f and lo <= rr < hi for ff, rr in agganciati)]
            esito = "solo controllo (nulla scritto)"
            if not solo_controllo:
                for sid, v in da_scrivere:
                    cur.execute("UPDATE entita SET dati = dati || jsonb_build_object('creature_evocate', %s::jsonb) WHERE id=%s AND revisione='automatica'", (Json(v["lista"]), sid))
                for i, _ in sbagliati:
                    cur.execute("UPDATE entita SET revisione='scartata' WHERE id=%s AND revisione='automatica'", (i,))
                esito = f"scritte {len(da_scrivere)} schede incantesimo con creature evocate; scartate {len(sbagliati)} voci bestiario da capitoli di incantesimi"
    md = (f"**{progetto}**\n\nEsito: {esito}\n\n- Incantesimi con creature evocate: **{len(da_scrivere)}** (blocchi creatura: {sum(len(v['lista']) for _, v in da_scrivere)})\n"
          f"- Blocchi senza incantesimo nel database: {len(non_trovati)}\n- Voci bestiario da scartare (nate dai capitoli degli incantesimi): {len(sbagliati)} → {', '.join(n for _, n in sbagliati)[:300]}")
    return {"render_all": [{"markdown": md}, {"table-row-object": righe_tab}, {"table-row-object": non_trovati}]}
