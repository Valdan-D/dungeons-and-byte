import json
import os

import wmill


def main(progetto: str, cartella_output: str = ""):
    """Passo con pausa: scrive nello stato del libro l'indirizzo con cui il servizio di revisione fa
    ripartire il flusso (quando premi Concludi); poi il flusso si ferma fino a quel momento."""
    u = wmill.get_resume_urls()
    f = (cartella_output or "/shared/projects").rstrip("/") + "/" + progetto + "/revisione/stato.json"
    st = json.load(open(f)) if os.path.exists(f) else {}
    st["stato"] = "in_revisione"
    st["resume_url"] = u.get("resume")
    json.dump(st, open(f, "w"))
    return {"progetto": progetto, "in_attesa_di_revisione": True}
