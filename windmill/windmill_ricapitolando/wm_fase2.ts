// Ricapitolando FASE 2 - genera i file capitoli_ricapitolati/*.md dalla bozza CORRETTA a mano.
// Portato da n8n (nodo genera-capitoli-da-bozza). ATTENZIONE: cancella i file gia' presenti in capitoli_ricapitolati del progetto e li riscrive.
// Legge la bozza da /shared/projects/estrazione/bozze_capitoli/<progetto>.md.  cartella_output (solo per prove): scrive in <cartella_output>/<progetto>/capitoli_ricapitolati.
// soglia_kb: un file piu' grande di cosi' (default 100 KB) si divide anche al livello di intestazione successivo (### poi #### ...), ricorsivamente;
// i file piu' piccoli restano interi (stesso risultato di prima). 0 = nessuna divisione.
import fs from 'fs';

export async function main(progetto: string, cartella_output: string = '', soglia_kb: number = 100) {
const projectName = progetto;
const bozzaPath = `/shared/projects/estrazione/bozze_capitoli/${projectName}.md`;
if (!fs.existsSync(bozzaPath)) {
  throw new Error(`Bozza non trovata, esegui prima la FASE 1: ${bozzaPath}`);
}

const sourceLines = fs.readFileSync(bozzaPath, 'utf8').split('\n');

// Individua i punti che aprono un nuovo file: livello 1 (#) o livello 2 (##)
const nuovi = [];
sourceLines.forEach((line, idx) => {
  const m = line.match(/^(#{1,4})\s+(.+)$/);
  if (m && m[1].length <= 2) {
    nuovi.push({ riga: idx, livello: m[1].length, titolo: m[2].trim() });
  }
});

if (nuovi.length === 0) {
  throw new Error('Nessuna intestazione di livello 1 o 2 (#/##) trovata nella bozza corretta');
}

const capDir = cartella_output ? `${cartella_output}/${projectName}/capitoli_ricapitolati` : `/shared/projects/${projectName}/capitoli_ricapitolati`;
if (fs.existsSync(capDir)) {
  for (const f of fs.readdirSync(capDir)) fs.unlinkSync(`${capDir}/${f}`);
}
fs.mkdirSync(capDir, { recursive: true });

const savedFiles = [];

// Contenuto prima della prima intestazione di livello 1/2 (premessa, o il blocco di commento della legenda)
if (nuovi[0].riga > 0) {
  const pre = sourceLines.slice(0, nuovi[0].riga).join('\n').trim();
  if (pre) {
    const filename = `${capDir}/0000-premessa.md`;
    fs.writeFileSync(filename, pre, 'utf8');
    savedFiles.push({ index: 0, title: 'premessa', filename });
  }
}

const SOGLIA = soglia_kb * 1024;
const slugDi = (title: string) => title
  .toLowerCase()
  .replace(/[àáâä]/g, 'a').replace(/[èéêë]/g, 'e')
  .replace(/[ìíîï]/g, 'i').replace(/[òóôö]/g, 'o').replace(/[ùúûü]/g, 'u')
  .replace(/[^a-z0-9.]+/g, '-').replace(/^-|-$/g, '').slice(0, 60);

// Divide un file troppo grande al livello di intestazione successivo; ripete sui pezzi ancora troppo grandi (fino a ######).
// Il testo prima della prima sotto-intestazione resta col titolo del padre; ogni pezzo successivo comincia con la propria intestazione.
function dividi(righe: string[], livello: number, titolo: string): { titolo: string; testo: string }[] {
  const testo = righe.join('\n').trim();
  if (soglia_kb <= 0 || Buffer.byteLength(testo, 'utf8') <= SOGLIA || livello >= 6) return [{ titolo, testo }];
  const prossimo = livello + 1;
  const punti: { i: number; titolo: string }[] = [];
  righe.forEach((l, i) => {
    const m = l.match(/^(#{1,6})\s+(.+)$/);
    if (m && m[1].length === prossimo) punti.push({ i, titolo: m[2].trim() });
  });
  if (punti.length === 0) return dividi(righe, prossimo, titolo);
  const pezzi: { titolo: string; testo: string }[] = [];
  if (punti[0].i > 0) pezzi.push(...dividi(righe.slice(0, punti[0].i), prossimo, titolo));
  for (let k = 0; k < punti.length; k++) {
    const fine = (k + 1 < punti.length) ? punti[k + 1].i : righe.length;
    pezzi.push(...dividi(righe.slice(punti[k].i, fine), prossimo, punti[k].titolo));
  }
  return pezzi.filter(x => x.testo.length > 0);
}

let numero = 0;
let divisi = 0;
for (let i = 0; i < nuovi.length; i++) {
  const startLine = nuovi[i].riga;
  const endLine = (i + 1 < nuovi.length) ? nuovi[i + 1].riga : sourceLines.length;
  const pezzi = dividi(sourceLines.slice(startLine, endLine), nuovi[i].livello, nuovi[i].titolo);
  if (pezzi.length > 1) divisi++;
  for (const pezzo of pezzi) {
    numero++;
    const filename = `${capDir}/${String(numero).padStart(4, '0')}-${slugDi(pezzo.titolo)}.md`;
    fs.writeFileSync(filename, pezzo.testo, 'utf8');
    savedFiles.push({ index: numero, title: pezzo.titolo, filename, livello: nuovi[i].livello });
  }
}

return { salvati: savedFiles.length, capDir, projectName, file_divisi: divisi, soglia_kb };
}
