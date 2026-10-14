// Ricapitolando FASE 2 - genera i file capitoli_ricapitolati/*.md dalla bozza CORRETTA a mano.
// Portato da n8n (nodo genera-capitoli-da-bozza). ATTENZIONE: cancella i file gia' presenti in capitoli_ricapitolati del progetto e li riscrive.
// Legge la bozza da /shared/projects/estrazione/bozze_capitoli/<progetto>.md.  cartella_output (solo per prove): scrive in <cartella_output>/<progetto>/capitoli_ricapitolati.
import fs from 'fs';

export async function main(progetto: string, cartella_output: string = '') {
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

for (let i = 0; i < nuovi.length; i++) {
  const startLine = nuovi[i].riga;
  const endLine = (i + 1 < nuovi.length) ? nuovi[i + 1].riga : sourceLines.length;
  const content = sourceLines.slice(startLine, endLine).join('\n').trim();
  const title = nuovi[i].titolo;
  const slug = title
    .toLowerCase()
    .replace(/[àáâä]/g, 'a').replace(/[èéêë]/g, 'e')
    .replace(/[ìíîï]/g, 'i').replace(/[òóôö]/g, 'o').replace(/[ùúûü]/g, 'u')
    .replace(/[^a-z0-9.]+/g, '-').replace(/^-|-$/g, '').slice(0, 60);
  const filename = `${capDir}/${String(i + 1).padStart(4, '0')}-${slug}.md`;
  fs.writeFileSync(filename, content, 'utf8');
  savedFiles.push({ index: i + 1, title, filename, livello: nuovi[i].livello });
}

return { salvati: savedFiles.length, capDir, projectName };
}
