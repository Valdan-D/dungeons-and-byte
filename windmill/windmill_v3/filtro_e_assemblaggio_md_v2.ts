// V3.0 - filtro-e-assemblaggio-md-v2: legge le pagine (markdown/page_N.md, nell'ordine) e prepara "# <progetto> - Pagina N" + testo.
// In V_2.0 il testo veniva dalla lettura del file (pagine gia' fatte) o dalla risposta del gateway, che coincide con il file scritto.
import fs from 'fs';
export async function main(projectName: string, pagine: number[]) {
  const results: any[] = [];
  for (const pageNum of pagine) {
    const fp = `/shared/projects/${projectName}/markdown/page_${pageNum}.md`;
    if (!fs.existsSync(fp)) throw new Error(`Pagina mancante: ${fp}`);
    // Tolte le note "*(testo a margine: ...)*" (crediti a margine aggiunti dal gateway): non servono nel markdown.
    const text = fs.readFileSync(fp, 'utf8').replace(/\n*\*\(testo a margine: [^\n]*\)\*[ \t]*/g, '').trim();
    results.push({ fileName: `page_${pageNum}.md`, projectName, page: pageNum, finalContent: `# ${projectName} - Pagina ${pageNum}\n\n${text}` });
  }
  return results;
}
