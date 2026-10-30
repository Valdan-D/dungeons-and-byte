# Revisione titoli e box

Sostituisce Ricapitolando FASE 1: un flusso Windmill prepara le proposte (titoli, livelli, candidati box), si ferma
per la revisione umana in un piccolo servizio Flask, poi scrive la bozza dei capitoli e lancia la FASE 2.

- `revisione_titoli.flow.json`: il flusso (controlla completezza, prepara, pausa, scrive la bozza, FASE 2).
- `controlla_completezza.py`, `prepara_revisione.py`, `attesa_revisione.py`, `scrivi_bozza.py`: i passi del flusso.
- `recupera_box.py`: comando a parte per recuperare box mancanti (riquadri rosa/lilla bassi o larghi) senza toccare le decisioni sui titoli.
- `catalogo_box.json`: generatori di candidati box. Un solo rilevatore per tutti i libri; la decisione "e un box / non lo e" e umana e viene salvata come dato di addestramento.
- `servizio/`: app Flask (`app.py`, `index.html`) e unita systemd. Indirizzi da configurare con `DNB_HOST` e `DNB_ALLOW`.

Solo codice: nessun testo o immagine dei manuali (vedi la sezione "Content policy" del README principale).
