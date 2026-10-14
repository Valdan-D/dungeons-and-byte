// Ricapitolando FASE 1 - rileva i capitoli e scrive la BOZZA da correggere a mano.
// Portato da n8n (nodo rileva-capitoli-bozza, stato CORRENTE del 24/09/2026). Scrive: <cartella_output>/<progetto>.md e <progetto>_da_verificare.txt
// (di default /shared/projects/estrazione/bozze_capitoli: SOVRASCRIVE la bozza corrente del progetto).
// 'outline' e' il risultato del passo precedente (f/ricapitolando/outline_pdf).
import fs from 'fs';

export async function main(progetto: string, outline: any = null, cartella_output: string = '') {
const projectName = progetto;
const projectDir = `/shared/projects/${projectName}`;
const mdPath = `${projectDir}/${projectName}.md`;
const pdfPath = `/shared/projects/pool/${projectName}.pdf`;
const pagesDir = `${projectDir}/markdown`;

if (!fs.existsSync(mdPath)) {
  throw new Error(`File completo non trovato: ${mdPath}`);
}

const DIACRITICI = new RegExp(String.fromCharCode(0x5b) + String.fromCharCode(0x0300) + '-' + String.fromCharCode(0x036f) + String.fromCharCode(0x5d), 'g');

// Vedi commento su similitudine() - limite di posizione per il tier "sottostringa".
const POSIZIONE_MAX_SOTTOSTRINGA = 60;

function normalizza(s) {
  return s
    .toLowerCase()
    .normalize('NFD').replace(DIACRITICI, '')
    .replace(/[^a-z0-9 ]+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

// Come normalizza(), ma tiene anche una mappa norm->raw (mappa[j] = indice in
// s del carattere raw che ha originato norm[j]). Serve SOLO al fix "riga
// fusa" del 2026-09-23 (vedi piu' sotto), per ritrovare nel testo originale
// il punto esatto in cui finisce un titolo trovato come sottostringa nel
// testo normalizzato. normalizza() resta invariata e non usa questa
// funzione, per non rischiare regressioni sul matching esistente.
function normalizzaConMappa(s) {
  let norm = '';
  const mappa = [];
  let ultimoEraSpazio = true;
  for (let i = 0; i < s.length; i++) {
    const decomposto = s[i].normalize('NFD').replace(DIACRITICI, '');
    for (const ch of decomposto) {
      const lower = ch.toLowerCase();
      if (/[a-z0-9]/.test(lower)) {
        norm += lower;
        mappa.push(i);
        ultimoEraSpazio = false;
      } else if (!ultimoEraSpazio) {
        norm += ' ';
        mappa.push(i);
        ultimoEraSpazio = true;
      }
    }
  }
  while (norm.endsWith(' ')) { norm = norm.slice(0, -1); mappa.pop(); }
  return { norm, mappa };
}

function distanzaLevenshtein(a, b) {
  const m = a.length, n = b.length;
  const dp = new Array(n + 1);
  for (let j = 0; j <= n; j++) dp[j] = j;
  for (let i = 1; i <= m; i++) {
    let prev = dp[0];
    dp[0] = i;
    for (let j = 1; j <= n; j++) {
      const temp = dp[j];
      dp[j] = a[i - 1] === b[j - 1] ? prev : 1 + Math.min(prev, dp[j], dp[j - 1]);
      prev = temp;
    }
  }
  return dp[n];
}

// Solo corrispondenze "forti": frase esatta, prefisso o sottostringa contigua.
// Niente fallback a parole sparse: per titoli brevi (1-2 parole) bastava una
// parola qualunque presente ovunque nella riga per superare la soglia,
// agganciando interi paragrafi di prosa a caso (bug reale trovato e corretto
// il 2026-08-09: "L'immaginazione e' un ingrediente..." promosso a capitolo).
function similitudine(a, b) {
  if (!a || !b) return 0;
  if (b === a) return 1.0;
  if (b.startsWith(a)) return 0.95;
  // Il tier "sottostringa" accetta il match SOLO se il titolo compare vicino
  // all'inizio della riga (entro POSIZIONE_MAX_SOTTOSTRINGA caratteri
  // normalizzati) - trovato 2026-09-23: titoli generici brevi che ricorrono
  // piu' volte nel libro (es. "Conclusione") possono comparire come
  // riferimento incrociato dentro un paragrafo enorme e scorrelato ("...vedi
  // Conclusione"), a centinaia di caratteri dall'inizio - senza questo
  // limite venivano promossi a heading dell'intero paragrafo. I casi reali
  // di riga fusa titolo+tagline (Collegio del Valore, Dominio della Vita,
  // ecc.) hanno sempre il titolo entro poche decine di caratteri
  // dall'inizio, quindi restano coperti da questo limite.
  const posSottostringa = b.indexOf(a);
  if (posSottostringa !== -1 && posSottostringa <= POSIZIONE_MAX_SOTTOSTRINGA) return 0.85;
  // Tolleranza a spazi mancanti nell'OCR (es. 'PUNTEGGIDI' invece di
  // 'PUNTEGGI DI' - trovato 2026-08-10, causa sistematica di ~12 voci non
  // trovate, tutte varianti di 'Livello 4: Aumento dei Punteggi di
  // Caratteristica' ripetuto una volta per classe). Confronto sicuro perche'
  // richiede ancora uguaglianza/prefisso sull'INTERA stringa senza spazi,
  // non una sottostringa qualsiasi - stesso livello di rigore delle regole
  // sopra, solo tollerante a spazi persi.
  const aNoSpazi = a.replace(/ /g, '');
  const bNoSpazi = b.replace(/ /g, '');
  if (aNoSpazi && (bNoSpazi === aNoSpazi || bNoSpazi.startsWith(aNoSpazi))) return 0.90;
  // Tolleranza a piccole differenze ortografiche (es. singolare/plurale,
  // refusi OCR di 1-2 caratteri - trovato 2026-09-23: "Descrizione degli
  // Incantesimi" nell'outline vs "Descrizioni degli Incantesimi" nel testo,
  // causa di un intero capitolo di ~374 incantesimi inghiottito nel file
  // precedente per mancato riconoscimento del boundary). Resta un confronto
  // sull'INTERA stringa (non una sottostringa qualsiasi) e richiede
  // lunghezze comparabili: stesso livello di rigore delle regole sopra,
  // solo tollerante a un piccolo numero di caratteri diversi.
  if (a.length >= 12 && Math.abs(a.length - b.length) <= 3) {
    const soglia = Math.max(1, Math.floor(a.length * 0.08));
    if (distanzaLevenshtein(a, b) <= soglia) return 0.88;
  }
  return 0;
}

function targetsDaOutline(titolo) {
  const targets = [normalizza(titolo)];
  if (titolo.includes(' - ')) {
    const idx = titolo.indexOf(' - ');
    targets.push(normalizza(titolo.slice(0, idx).trim()));
    targets.push(normalizza(titolo.slice(idx + 3).trim()));
  }
  return targets;
}

const bozzaDir = cartella_output || '/shared/projects/estrazione/bozze_capitoli';
fs.mkdirSync(bozzaDir, { recursive: true });
const bozzaPath = `${bozzaDir}/${projectName}.md`;
const reportPath = `${bozzaDir}/${projectName}_da_verificare.txt`;

// ---------- Scarto pagina PDF / pagina stampata ----------
// Ogni pagina digitalizzata da V_2.0 salva un JSON con un blocco "number"
// (il numero stampato nel footer). Serve per tradurre tra l'indice file
// (page_N.md, la stessa numerazione usata dall'outline del PDF) e il numero
// che l'utente vede leggendo il libro/PDF - comodo quando si discute di una
// pagina specifica. Non sempre leggibile (frontespizi, tavole a piena
// pagina, rumore OCR tipo "12+") - si prende lo scarto piu' frequente tra le
// pagine dove e' leggibile, 0 se nessuna lo e' (manuali scansionati senza
// numerazione riconosciuta, o cartella json assente).
const jsonDir = `${projectDir}/json`;
let pageOffset = 0;
if (fs.existsSync(pagesDir) && fs.existsSync(jsonDir)) {
  const offsetCounts = new Map();
  for (const f of fs.readdirSync(pagesDir)) {
    const m = f.match(/^page_(\d+)\.md$/);
    if (!m) continue;
    const p = parseInt(m[1], 10);
    const jsonFp = `${jsonDir}/pag_${p}_res.json`;
    if (!fs.existsSync(jsonFp)) continue;
    try {
      const jd = JSON.parse(fs.readFileSync(jsonFp, 'utf8'));
      const blocks = (jd.res || jd).parsing_res_list || [];
      const numBlock = blocks.find(b => b.block_label === 'number');
      if (!numBlock) continue;
      const match = (numBlock.block_content || '').trim().match(/^\d+/);
      if (!match) continue;
      const off = p - parseInt(match[0], 10);
      offsetCounts.set(off, (offsetCounts.get(off) || 0) + 1);
    } catch (e) {
      // JSON mancante/corrotto per questa pagina - non blocca il resto
    }
  }
  let maxCount = 0;
  for (const [off, count] of offsetCounts) {
    if (count > maxCount) { maxCount = count; pageOffset = off; }
  }
}

// ---------- Prova a leggere l'outline nativo del PDF ----------
let outlineGrezzo = [];
let pdfOutlineOk = false;
if (fs.existsSync(pdfPath)) {
  {
    const parsed = outline;
    if (parsed && parsed.outline && parsed.outline.length > 0) {
      outlineGrezzo = parsed.outline;
      pdfOutlineOk = true;
    }
  }
}

// Trovato 2026-09-24 su una guida 5e di ambientazione:
// un outline PDF puo' esistere (non vuoto, quindi pdfOutlineOk=true sopra)
// ma essere un placeholder INUTILE - una voce per pagina, titoli letterali
// "pag.001", "pag.002", ... (161/161 voci in quel caso). Con un outline del
// genere ogni voce cerca "pag.NNN" come riga di testo nel markdown (che non
// esiste), quindi cerca() fallisce sempre, zero confermati, la bozza esce
// senza NESSUNA intestazione - FASE 2 poi fallisce del tutto ("nessuna
// intestazione trovata"). Se la stragrande maggioranza delle voci segue
// questo pattern, l'outline non e' utilizzabile: si forza il fallback
// euristico invece di usarlo alla cieca.
let outlineScartatoPlaceholder = false;
if (pdfOutlineOk) {
  const placeholderCount = outlineGrezzo.filter(o => /^pag\.?\s*\d+$/i.test(o.titolo.trim())).length;
  if (outlineGrezzo.length > 0 && placeholderCount / outlineGrezzo.length >= 0.8) {
    pdfOutlineOk = false;
    outlineScartatoPlaceholder = true;
  }
}

if (pdfOutlineOk && fs.existsSync(pagesDir)) {
  // ================= MODALITA' OUTLINE NATIVO =================
  const outline = outlineGrezzo.filter(o => !/^(tabella|box)\s*:/i.test(o.titolo.trim()));
  const boxEntries = outlineGrezzo.filter(o => /^box\s*:/i.test(o.titolo.trim()));

  const pageFiles = fs.readdirSync(pagesDir).filter(f => /^page_\d+\.md$/.test(f));
  const maxPage = pageFiles.reduce((m, f) => Math.max(m, parseInt(f.match(/\d+/)[0], 10)), 0);

  const allLines = [];
  for (let p = 1; p <= maxPage; p++) {
    const fp = `${pagesDir}/page_${p}.md`;
    if (!fs.existsSync(fp)) continue;
    let content = fs.readFileSync(fp, 'utf8');
    content = content.replace(/^# .+? - Pagina \d+\s*\n/, '');
    for (const l of content.split('\n')) {
      allLines.push({ testo: l, pagina: p });
    }
  }

  function cerca(voce, cursore) {
    const targets = targetsDaOutline(voce.titolo);
    let miglior = { idx: -1, score: 0, distanzaPagina: 99, target: null };
    for (let margine = 1; margine <= 3 && miglior.idx === -1; margine++) {
      for (let i = cursore; i < allLines.length; i++) {
        const riga = allLines[i];
        if (Math.abs(riga.pagina - voce.pagina) > margine) continue;
        const testoNorm = normalizza(riga.testo);
        for (const target of targets) {
          const s = similitudine(target, testoNorm);
          if (s >= 0.85 && s > miglior.score) {
            miglior = { idx: i, score: s, distanzaPagina: Math.abs(riga.pagina - voce.pagina), target };
          }
        }
      }
    }
    return miglior;
  }

  const confermati = [];
  const nonTrovati = [];
  let cursore = 0;
  for (const voce of outline) {
    const m = cerca(voce, cursore);
    if (m.idx !== -1) {
      confermati.push({ indiceRiga: m.idx, livello: voce.livello, titolo: voce.titolo, pagina: voce.pagina, distanzaPagina: m.distanzaPagina, score: m.score, target: m.target });
      cursore = m.idx;
    } else {
      nonTrovati.push(voce);
    }
  }

  // I box vengono cercati a parte (senza avanzare il cursore principale: interrompono
  // il flusso normale e possono comparire fuori sequenza rispetto ai paragrafi veri)
  const boxTrovati = [];
  for (const voce of boxEntries) {
    const titoloBox = voce.titolo.includes(':') ? voce.titolo.split(':').slice(1).join(':').trim() : voce.titolo;
    const m = cerca({ ...voce, titolo: titoloBox }, 0);
    if (m.idx !== -1) {
      boxTrovati.push({ indiceRiga: m.idx, titolo: titoloBox });
    }
  }

  const confermatiPerRiga = new Map(confermati.map(c => [c.indiceRiga, c]));
  const boxPerRiga = new Map(boxTrovati.map(b => [b.indiceRiga, b]));

  // Confine di ogni box = il prossimo confine noto (intestazione o box successivo).
  // Serve per trasformare TUTTO lo span del box in blockquote (">"), non solo la
  // prima riga: altrimenti il corpo del box resta indistinguibile dal testo
  // normale (segnalato dall'utente: "cosi' viene messo nel box solo la prima riga?").
  const confini = Array.from(new Set([...confermatiPerRiga.keys(), ...boxPerRiga.keys()])).sort((a, b) => a - b);

  // Ultimo indice riga di ogni pagina (allLines e' costruito in ordine di
  // pagina crescente, quindi l'ultimo indice visto per una pagina e' sempre
  // quello corretto).
  const ultimaRigaPagina = new Map();
  allLines.forEach((riga, i) => ultimaRigaPagina.set(riga.pagina, i));

  // Un box non attraversa mai il confine di pagina. Verificato sui dati reali
  // (JSON per-pagina, block_bbox) che non esiste alcun segnale affidabile di
  // continuita' fra una pagina e la successiva: il layout a due colonne
  // riparte sempre da zero (sinistra poi destra) su ogni pagina, quindi una
  // pagina "dopo" con due colonne e' indistinguibile da un box che prosegue.
  // Bug reale trovato il 2026-08-12: il box "Ordine di marcia" (pag.23,
  // colonna destra) inghiottiva "ESEMPIO DI ESPLORAZIONE" (pag.24, colonna
  // sinistra, sezione completamente diversa) perche' l'intestazione di mezzo
  // non era stata confermata e fineBox() andava avanti a cercare senza
  // limiti. Meglio chiudere un box un po' presto (perde l'ultima riga, se
  // per davvero prosegue nella pagina dopo) che troppo tardi (inghiotte
  // capitoli interi) - non c'e' modo di distinguere i due casi dal JSON.
  function fineBox(inizio) {
    let fineConfine = allLines.length;
    for (const c of confini) {
      if (c > inizio) { fineConfine = c; break; }
    }
    const paginaInizio = allLines[inizio].pagina;
    const fineDiPagina = (ultimaRigaPagina.get(paginaInizio) ?? (allLines.length - 1)) + 1;
    return Math.min(fineConfine, fineDiPagina);
  }
  const boxSpans = Array.from(boxPerRiga.keys()).map(idx => [idx, fineBox(idx)]);
  function dentroBox(i) {
    return boxSpans.some(([inizio, fine]) => inizio <= i && i < fine);
  }

  // Righe fuse OCR (titolo + testo successivo sulla stessa riga fisica) che il
  // fix sotto e' riuscito/non riuscito a separare automaticamente - riportate
  // nel report per controllo a campione (2026-09-23).
  const righeFuseSeparate = [];
  const righeFuseSospette = [];

  // Righe di output con pagina associata (per marcare le vere giunzioni tra pagine dopo)
  const outLines = [];
  allLines.forEach((riga, i) => {
    const testoBase = riga.testo.replace(/^#{1,6}\s+/, '');
    if (boxPerRiga.has(i)) {
      outLines.push({ testo: `> **[BOX: ${boxPerRiga.get(i).titolo}]**`, pagina: riga.pagina });
      const prefisso = testoBase.trim() ? '> ' : '>';
      outLines.push({ testo: `${prefisso}${testoBase}`, pagina: riga.pagina });
    } else if (confermatiPerRiga.has(i)) {
      const c = confermatiPerRiga.get(i);
      // Fix "riga fusa" (2026-09-23): un match arrivato dal livello
      // "sottostringa" (0.85, non esatto/prefisso/senza-spazi) su una riga
      // MOLTO piu' lunga del titolo cercato e' quasi sempre l'OCR che ha
      // fuso titolo+frase successiva sulla stessa riga fisica (nessun
      // a-capo nel blocco di layout riconosciuto) - es. "Collegio del
      // Valore" + tagline del Bardo, o casi piu' gravi a livello 2 sul
      // un modulo 5e moderno dove l'intera riga apriva un file nuovo nel punto
      // sbagliato. Proviamo a spezzare invece di marcare tutta la riga
      // come heading; se non troviamo un punto di taglio pulito, teniamo
      // il comportamento di prima (sicurezza) e segnaliamo nel report.
      let gestita = false;
      if (c.score === 0.85 && c.target && testoBase.length > c.target.length * 1.6) {
        const { norm: rigaNorm, mappa: rigaMappa } = normalizzaConMappa(testoBase);
        const startNorm = rigaNorm.indexOf(c.target);
        if (startNorm !== -1) {
          const endNormEsclusivo = startNorm + c.target.length;
          const fineRaw = endNormEsclusivo < rigaMappa.length ? rigaMappa[endNormEsclusivo] : testoBase.length;
          const partTitolo = testoBase.slice(0, fineRaw).trim();
          const partResto = testoBase.slice(fineRaw).trim();
          // Limite sulla parte-titolo (2026-09-23, trovato sul un modulo 5e moderno):
          // titoli generici brevi (es. "Conclusione", ripetuto piu' volte nel
          // libro) possono comparire come citazione incrociata dentro un
          // paragrafo enorme e completamente estraneo (es. "...vedi
          // Conclusione"), non come vero fuso titolo+tagline in testa a riga.
          // In quel caso fineRaw cade a centinaia di caratteri dall'inizio
          // riga: NON e' il pattern che questo fix intende risolvere, quindi
          // va trattato come sospetto, non separato automaticamente - i casi
          // reali di riga fusa (Collegio del Valore, Dominio della Vita, ecc.)
          // hanno sempre partTitolo entro poche decine di caratteri.
          if (partTitolo && partTitolo.length <= 100 && partResto.length >= 15) {
            outLines.push({ testo: `${'#'.repeat(Math.min(c.livello, 6))} ${partTitolo}`, pagina: riga.pagina });
            outLines.push({ testo: partResto, pagina: riga.pagina });
            righeFuseSeparate.push(`p.${riga.pagina} "${c.titolo}" -> heading="${partTitolo}" | resto spostato a paragrafo`);
            gestita = true;
          }
        }
        if (!gestita) {
          righeFuseSospette.push(`p.${riga.pagina} "${c.titolo}" -> riga fusa sospetta ma non separabile automaticamente, verificare a mano: "${testoBase.slice(0, 120)}"`);
        }
      }
      if (!gestita) {
        outLines.push({ testo: `${'#'.repeat(Math.min(c.livello, 6))} ${testoBase}`, pagina: riga.pagina });
      }
    } else if (dentroBox(i)) {
      const prefisso = testoBase.trim() ? '> ' : '>';
      outLines.push({ testo: `${prefisso}${testoBase}`, pagina: riga.pagina });
    } else {
      outLines.push({ testo: testoBase, pagina: riga.pagina });
    }
  });

  // Assembla inserendo un marcatore SOLO alle vere giunzioni tra pagine, poi ripulisce
  // parole spezzate a fine pagina e frasi interrotte, come gia' faceva split-capitoli
  // sul flow principale (qui mancava, causa reale dei tanti "a capo" da correggere a mano)
  let assembled = outLines.length ? outLines[0].testo : '';
  for (let i = 1; i < outLines.length; i++) {
    assembled += (outLines[i].pagina !== outLines[i - 1].pagina) ? '\n\n<!-- pb -->\n\n' : '\n';
    assembled += outLines[i].testo;
  }
  assembled = assembled.replace(/(\w)-\n\n<!-- pb -->\n\n(\w)/g, '$1$2');
  assembled = assembled.replace(/([^.!?:;»")\]\n])\n\n<!-- pb -->\n\n([a-zà-ù])/g, '$1 $2');
  assembled = assembled.replace(/\n*<!-- pb -->\n*/g, '\n\n');
  assembled = assembled.replace(/\n{3,}/g, '\n\n');

  const distanzaMedia = confermati.length ? (confermati.reduce((s, c) => s + c.distanzaPagina, 0) / confermati.length).toFixed(2) : 'n/d';

  const legend = [
    '<!--',
    `  Bozza capitoli - ${projectName} (generata da OUTLINE NATIVO del PDF)`,
    `  Voci outline totali: ${outline.length} (esclude Tabella:/Box:) - confermate nel testo: ${confermati.length} - non trovate: ${nonTrovati.length}`,
    `  Box laterali (Box: ...): ${boxEntries.length} nell'outline, ${boxTrovati.length} individuati nel testo - segnati con "> [BOX] Titolo" prima del punto in cui iniziano`,
    `  Scarto pagina PDF/stampata: ${pageOffset} (pagina stampata = pagina PDF - ${pageOffset}; 0 se non determinabile)`,
    `  Vedi anche: ${projectName}_da_verificare.txt per le voci non trovate o da ricontrollare`,
    '  Correggi il NUMERO DI CANCELLETTI direttamente sulle intestazioni se qualcosa non torna.',
    '  #=capitolo ##=paragrafo (apre un file) ###/####/...=sottoparagrafo (resta nel file del paragrafo)',
    '-->',
    ''
  ].join('\n');

  fs.writeFileSync(bozzaPath, legend + assembled, 'utf8');

  const reportLines = [
    `Report controllo outline - ${projectName}`,
    `Voci outline totali (escluse Tabella:/Box:): ${outline.length}`,
    `Confermate nel testo: ${confermati.length}`,
    `Non trovate (da posizionare a mano se rilevanti): ${nonTrovati.length}`,
    `Distanza media pagina attesa/trovata: ${distanzaMedia}`,
    `Box laterali: ${boxTrovati.length}/${boxEntries.length} individuati`,
    `Scarto pagina PDF/stampata: ${pageOffset}`,
    '',
    '--- NON TROVATE ---',
    ...nonTrovati.map(v => `[L${v.livello}] p.${v.pagina} ${v.titolo}`),
    '',
    '--- CONFERMATE MA SU PAGINA DIVERSA DA QUELLA ATTESA (ricontrolla) ---',
    ...confermati.filter(c => c.distanzaPagina > 0).map(c => `[L${c.livello}] atteso p.${c.pagina}, trovato a distanza ${c.distanzaPagina} pagine - ${c.titolo}`),
    '',
    '--- RIGHE FUSE SEPARATE AUTOMATICAMENTE (verifica a campione) ---',
    ...righeFuseSeparate,
    '',
    '--- RIGHE FUSE SOSPETTE, NON SEPARATE (verificare a mano) ---',
    ...righeFuseSospette
  ];
  fs.writeFileSync(reportPath, reportLines.join('\n'), 'utf8');

  return ({
    projectName,
    modalita: 'outline_nativo',
    voci_outline: outline.length,
    confermate: confermati.length,
    non_trovate: nonTrovati.length,
    distanza_media_pagina: distanzaMedia,
    box_trovati: boxTrovati.length,
    box_totali: boxEntries.length,
    pageOffset,
    bozzaPath,
    reportPath
  });
}

// ================= MODALITA' EURISTICA (fallback, nessun outline nel PDF) =================
const text = fs.readFileSync(mdPath, 'utf8');
const lines = text.split('\n');

const SOTTOPARAGRAFO_BLACKLIST = [
  'TRATTI', 'AZIONI', 'AZIONI BONUS', 'REAZIONI',
  'ATTACCO LEGGENDARIO', 'AZIONI LEGGENDARIE', 'TALENTI DI LEGGENDA',
  'TANA', 'EFFETTI DI TANA', 'AZIONI REGIONALI', 'VARIANTE', 'COMPAGNONE',
  'COME PERSONAGGIO MULTICLASSE', 'TALENTO ORIGINI'
];
const SOTTOPARAGRAFO_PATTERNS = [
  /^LIVELLO\s+(\d+|L)\b/i,
  /^COME PERSONAGGIO DI \d+[ºo]?\s*LIVELLO/i,
  /^COSTO:\s*\d+\s*PUNT[OI]\s*STREGONERIA/i,
  /^DIVENTARE UN[ao']?\s+/i,
  /^PRIVILEGI\s+(DI CLASSE\s+)?DEL[l'oa]?\s+/i,
  /^SOTTOCLASSI\s+DEL[l'oa]?\s+/i,
  /^INCANTESIMI\s+DEL[l'oa]?\s+/i,
  /^DADI\s+DI\s+ENERGIA\s+DEL[l'oa]?\s+/i
];
const ZONA_APRI = /^SOTTOCLASSI\s+DEL[l'oa]?\s+/i;
const ZONA_CHIUDI = /^DIVENTARE UN[ao']?\s+/i;

// ---- Fix 2026-09-23: righe fuse titolo+corpo-testo, versione euristica ----
// Stesso bug gia' risolto nella modalita' outline nativo (titolo di
// stanza/mostro fuso sulla stessa riga fisica con la frase successiva),
// trovato anche qui (es. "un modulo 5e"): niente
// outline con cui confrontarsi, quindi il taglio si basa su pattern
// specifici e sicuri invece che su un titolo noto da cercare - se non
// matchano NESSUNO dei due, la riga resta intera come prima (nessun
// tentativo generico "a occhio", stesso principio di rigore del resto del
// codice).
const RIGA_FUSA_INIZIO_RESTO = [
  /\bDescrivi\b/,
  /\b(?:Aberrazione|Bestia|Celestiale|Costrutto|Drago|Elementale|Fata|Gigante|Immondo|Mostruosit[aà]|Mestruosit[oa]|Non\s+Morto|Melma|Pianta|Vegetale|Umanoide)\s+(?:Minuscol[oa]|Piccol[oa]|Medi[oa]|Grande|Enorme|Mastodontic[oa])\b/,
  // Trovato 2026-09-23 sul un regolamento 5e: nome incantesimo fuso con
  // la riga scuola+livello (es. "DARDO INCANTATO Invocazione di 1 livello").
  /\b(?:Abiurazione|Ammaliamento|Divinazione|Evocazione|Illusione|Invocazione|Necromanzia|Trasmutazione)\s+di\s+/,
];
function trovaTaglioEuristico(title) {
  for (const pat of RIGA_FUSA_INIZIO_RESTO) {
    const m = pat.exec(title);
    if (m && m.index > 0) {
      const titolo = title.slice(0, m.index).trim();
      const resto = title.slice(m.index).trim();
      if (titolo && resto.length >= 10) return { titolo, resto };
    }
  }
  return null;
}

// ---- Fix 2026-09-23: titoli-frammento di corpo-testo (retrocessi, non aperti come file) ----
// I titoli genuini in questa pipeline sono quasi sempre TUTTO MAIUSCOLO,
// oppure iniziano con un codice ambiente breve tipo "C9." / "R15.". Un
// titolo che non e' ne' l'uno ne' l'altro, ed e' lungo (>=5 parole), e'
// quasi certamente un frammento di prosa promosso per errore a titolo dal
// layout detector a monte (pipeline V_2.0/paddleocr, non da questo nodo).
//
// ATTENZIONE (2026-09-23, trovato subito dopo il primo tentativo): NON
// retrocedere automaticamente in base a questo segnale - provato su un modulo classico (reprint) (1992) e la regola ha inghiottito titoli
// di stanza legittimi come "Stanza 5: sala da pranzo" o "Stanza 12: salone
// delle danze" (5 parole, non maiuscolo, nessun prefisso tipo "C9." perche'
// questo libro usa "Stanza N:" invece) - le convenzioni di titolo cambiano
// da libro a libro, un segnale generico basato solo su maiuscolo/lunghezza
// non e' affidabile abbastanza per una correzione automatica. Declassato a
// SOLO segnalazione (vedi piu' sotto, unito a titoliSospettiEur) - stesso
// principio delle righe fuse non separabili: meglio segnalare per verifica
// a mano che rischiare di corrompere un titolo vero.
function sembraFrammentoDiTesto(title) {
  if (title === title.toUpperCase()) return false;
  if (/^[A-Z]\d+\.\s/.test(title)) return false;
  return true;
}

let inZonaSottoclassi = false;
let nCapitolo = 0, nParagrafo = 0, nSotto = 0, nSottoSotto = 0;
const righeFuseSeparateEur = [];
const titoliSospettiEur = [];

const outLinesEur = lines.flatMap((line) => {
  const m = line.match(/^(#{1,6})\s+(.+)$/);
  if (!m) return [line];
  const hashes = m[1];
  let title = m[2].trim();
  let restoSeparato = null;

  if (hashes.length >= 2) {
    const taglio = trovaTaglioEuristico(title);
    if (taglio) {
      righeFuseSeparateEur.push(`"${title.slice(0, 80)}" -> titolo="${taglio.titolo}" | resto spostato a paragrafo`);
      title = taglio.titolo;
      restoSeparato = taglio.resto;
    }
  }

  const upper = title.toUpperCase();
  let livello;
  if (hashes.length === 1) {
    livello = 1;
  } else {
    const isSottoparagrafo = SOTTOPARAGRAFO_BLACKLIST.includes(upper) || SOTTOPARAGRAFO_PATTERNS.some(p => p.test(title));
    if (isSottoparagrafo) {
      livello = 3;
    } else if (inZonaSottoclassi) {
      livello = 4;
    } else {
      livello = 2;
      // Titolo tenuto come paragrafo (apre file) ma sospetto (ne' tutto
      // maiuscolo ne' con prefisso tipo "C9.") - MAI retrocesso in automatico
      // (vedi commento su sembraFrammentoDiTesto), solo segnalato per
      // controllo a campione: puo' essere un frammento di prosa promosso per
      // errore, o semplicemente lo stile di titolo di questo libro (es.
      // "Stanza N: ...") - non c'e' modo di saperlo senza guardare il libro.
      if (sembraFrammentoDiTesto(title)) {
        titoliSospettiEur.push(title.slice(0, 100));
      }
    }
  }
  if (ZONA_APRI.test(title)) inZonaSottoclassi = true;
  if (ZONA_CHIUDI.test(title)) inZonaSottoclassi = false;
  if (livello === 1) nCapitolo++;
  else if (livello === 2) nParagrafo++;
  else if (livello === 3) nSotto++;
  else nSottoSotto++;

  const headingLine = `${'#'.repeat(livello)} ${title}`;
  return restoSeparato ? [headingLine, restoSeparato] : [headingLine];
});

const legendEur = [
  '<!--',
  `  Bozza capitoli - ${projectName} (generata con euristica: nessun outline nativo nel PDF)`,
  `  Scarto pagina PDF/stampata: ${pageOffset} (pagina stampata = pagina PDF - ${pageOffset}; 0 se non determinabile)`,
  `  Vedi anche: ${projectName}_da_verificare.txt per titoli fusi separati/retrocessi/sospetti`,
  '  Correggi il NUMERO DI CANCELLETTI direttamente sulle intestazioni:',
  '  #=capitolo ##=paragrafo (apre un file) ###=sottoparagrafo ####=sotto-sottoparagrafo (restano nel file del paragrafo)',
  '-->',
  ''
].join('\n');

fs.writeFileSync(bozzaPath, legendEur + outLinesEur.join('\n'), 'utf8');

const reportLinesEur = [
  `Report euristica - ${projectName}`,
  `Capitoli: ${nCapitolo}  Paragrafi: ${nParagrafo}  Sottoparagrafi: ${nSotto}  Sotto-sottoparagrafi: ${nSottoSotto}`,
  '',
  '--- RIGHE FUSE SEPARATE AUTOMATICAMENTE (verifica a campione) ---',
  ...righeFuseSeparateEur,
  '',
  '--- TITOLI SOSPETTI TENUTI COME PARAGRAFO (ne\' maiuscolo ne\' codice ambiente - possono essere frammenti di prosa O semplicemente lo stile titoli di questo libro, verificare a mano) ---',
  ...titoliSospettiEur
];
fs.writeFileSync(reportPath, reportLinesEur.join('\n'), 'utf8');

return ({
  projectName,
  modalita: 'euristica',
  outline_scartato_placeholder: outlineScartatoPlaceholder,
  capitolo: nCapitolo,
  paragrafo: nParagrafo,
  sottoparagrafo: nSotto,
  sottosottoparagrafo: nSottoSotto,
  righe_fuse_separate: righeFuseSeparateEur.length,
  titoli_sospetti: titoliSospettiEur.length,
  pageOffset,
  bozzaPath,
  reportPath
});
}
