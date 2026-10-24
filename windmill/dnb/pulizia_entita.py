#requirements:
#psycopg2-binary
#wmill
import unicodedata
import wmill

CLASSI = {"barbaro", "bardo", "chierico", "druido", "guerriero", "ladro", "mago", "monaco", "paladino", "ranger", "stregone", "warlock"}

def main(solo_controllo: bool = True, escludi_libri: list[str] = []):
    """Pulizia automatica del database (solo righe con revisione 'automatica', nulla viene cancellato):
    1) nei libri-avventura (titolo con 'avventura') specie/classe/background -> 'scartata' (le avventure non hanno specie, classi o background nuovi: sono PNG, luoghi e mostri scambiati);
    2) nei manuali di regole (>= 8 delle 12 classi base) le 'classe' sono marcate dati.tipo_classe = 'classe' (le 12 base) o 'sottoclasse' (le altre).
    Ripetibile: va rilanciata dopo ogni nuovo caricamento di un libro (il caricamento riscrive le voci automatiche)."""
    import psycopg2
    from psycopg2.extras import Json
    solo_controllo = True if solo_controllo is None else solo_controllo
    escludi_libri = escludi_libri or []
    db = wmill.get_resource("f/dnb/db_dnb_loader")
    tab = []; tot_scarto = 0; tot_sott = 0; tot_cl = 0
    def n(s): return "".join(c for c in unicodedata.normalize("NFKD", s or "") if not unicodedata.combining(c)).strip().lower()
    with psycopg2.connect(host=db["host"], port=db["port"], user=db["user"], password=db["password"], dbname=db["dbname"], sslmode=db.get("sslmode", "prefer")) as c:
        with c.cursor() as cur:
            cur.execute("SELECT id, titolo FROM libri ORDER BY titolo")
            for lid, titolo in cur.fetchall():
                if any(titolo.lower().startswith(x.lower()) for x in escludi_libri):
                    tab.append({"libro": titolo[:40], "regola": "escluso", "voci": 0}); continue
                if "avventura" in titolo.lower():
                    cur.execute("SELECT tipologia, count(*) FROM entita WHERE libro_id=%s AND tipologia IN ('specie','classe','background') AND revisione='automatica' GROUP BY 1 ORDER BY 1", (lid,))
                    for tip, k in cur.fetchall(): tab.append({"libro": titolo[:40], "regola": f"1 avventura: scarta {tip}", "voci": k}); tot_scarto += k
                    if not solo_controllo:
                        cur.execute("UPDATE entita SET revisione='scartata' WHERE libro_id=%s AND tipologia IN ('specie','classe','background') AND revisione='automatica'", (lid,))
                    continue
                cur.execute("SELECT id, nome FROM entita WHERE libro_id=%s AND tipologia='classe' AND revisione='automatica'", (lid,))
                cl = cur.fetchall()
                base = [i for i, nome in cl if n(nome) in CLASSI]
                if len({n(nome) for _, nome in cl} & CLASSI) < 8: continue
                sott = [i for i, nome in cl if n(nome) not in CLASSI]
                tab.append({"libro": titolo[:40], "regola": "2 manuale: classi base", "voci": len(base)}); tab.append({"libro": titolo[:40], "regola": "2 manuale: sottoclassi", "voci": len(sott)})
                tot_cl += len(base); tot_sott += len(sott)
                if not solo_controllo:
                    for ids, val in ((base, "classe"), (sott, "sottoclasse")):
                        if ids: cur.execute("UPDATE entita SET dati = dati || jsonb_build_object('tipo_classe', %s::text) WHERE id = ANY(%s) AND revisione='automatica'", (val, ids))
    esito = "solo controllo (nulla scritto)" if solo_controllo else "applicato"
    md = f"**Pulizia del database** - {esito}\n\n- Voci di avventure da scartare: **{tot_scarto}**\n- Classi base marcate: **{tot_cl}**, sottoclassi marcate: **{tot_sott}**"
    return {"render_all": [{"markdown": md}, {"table-row-object": tab}]}
