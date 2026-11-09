# Rilevatore di box e schede dei mostri

Riconosce nelle pagine dei manuali i **box** (riquadri di testo a parte) e le **schede dei mostri**, e li scrive nel json OCR
di ogni pagina (chiave `dnb_zone`, coordinate in pixel come i blocchi PaddleOCR).

## Come funziona
Il passo `rileva_zone` (`windmill/windmill_v3/rileva_zone.py`, ultimo passo del flusso V3.0, non bloccante) usa tre fonti:

1. **Schede dal testo OCR** (anche senza cornice). Un mostro inizia con un titolo seguito dalla riga del tipo
   (taglia + virgola) o dalle statistiche (Classe Armatura, FOR/DES/COS) e finisce al mostro successivo. I blocchi si leggono
   colonna per colonna e pagina per pagina: i pezzi dello stesso mostro su colonne o pagine diverse restano collegati
   (`mostro`, `nome`, `pezzo`, `di`). Filtri: niente voci di glossario o indice analitico, niente intestazioni di tabella.
2. **Rettangoli disegnati nel PDF** (PDF digitali): rettangolo pieno di colore diverso dal fondo, con testo dentro e non per
   lo piu' tabella. E' la fonte piu' precisa dove esiste.
3. **Rilevatore YOLO** (`layout_gateway.py`, servizio `dnb-layout` sulla macchina GPU, porta 8097, `POST /zone`): vede i box
   senza disegno vettoriale (scansioni, foto). Se non risponde il passo continua con le prime due fonti.

Le pagine con "pagina N di M" nel piede (fogli da fotocopiare, come i fogli del personaggio) non hanno box.
Prima di scrivere, il passo copia gli originali in `json_orig`.

`windmill/revisione/prepara_revisione.py` legge `dnb_zone` come ulteriore generatore di candidati box
(voce `rilevatore-layout`) e scarta i candidati dentro una scheda; la decisione finale resta alla revisione umana.

## Pesi del modello
I pesi (`*.pt`) non stanno nel repository. Sono addestrati su pagine di manuali: non si pubblicano qui.
E' un modello YOLO a due classi (0 box, 1 scheda), immagini a 1280 px.
