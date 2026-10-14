# requirements:
# pymupdf==1.28.0
import os
import fitz  # PyMuPDF

def main(progetto: str):
    """Ricapitolando, passo 1: legge l'outline/bookmark nativo del PDF (pool/<progetto>.pdf). Se non c'e' il PDF restituisce {}."""
    pdf = f"/shared/projects/pool/{progetto}.pdf"
    if not os.path.exists(pdf):
        return {}
    try:
        doc = fitz.open(pdf)
    except Exception as e:
        return {"errore": str(e)}
    toc = doc.get_toc()
    return {"pagine": doc.page_count, "outline": [{"livello": l, "titolo": t, "pagina": p} for l, t, p in toc]}
