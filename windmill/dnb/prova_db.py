import wmill
import psycopg2

def main():
    """Prova di collegamento al database D&B con il ruolo caricatore (non scrive niente)."""
    db = wmill.get_resource("f/dnb/db_dnb_loader")
    with psycopg2.connect(host=db["host"], port=db["port"], user=db["user"], password=db["password"], dbname=db["dbname"], sslmode=db.get("sslmode", "prefer")) as c:
        with c.cursor() as cur:
            cur.execute("SELECT current_user, (SELECT count(*) FROM libri), (SELECT count(*) FROM entita), version()")
            u, nl, ne, v = cur.fetchone()
    return {"utente": u, "libri": nl, "entita": ne, "versione": v.split(",")[0]}
