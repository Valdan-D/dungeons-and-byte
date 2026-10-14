# requirements:
# pymupdf==1.28.0
import fitz  # PyMuPDF

def main(projectName: str, pdfPath: str):
    """V3.0 - conta-pagine: numero di pagine del PDF (PyMuPDF; coincide con pdfinfo su tutti i PDF del pool). Restituisce l'elenco 1..N."""
    n = fitz.open(pdfPath).page_count
    return {"projectName": projectName, "pdfPath": pdfPath, "pagine": list(range(1, n + 1))}
