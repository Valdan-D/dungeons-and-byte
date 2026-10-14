// V3.0 - salva-markdown-totale: scrive <progetto>/<progetto>.md (o in cartella_output/<progetto>/ per le prove).
import fs from 'fs';
export async function main(aggregato: any, cartella_output: string = '') {
  const projectName = aggregato.projectName;
  const finalContents = aggregato.finalContent;
  const joined = Array.isArray(finalContents)
    ? finalContents
        .map(c => String(c).replace(/^# [^\n]+ - Pagina \d+\s*\n\n?/, ''))
        .join('\n\n---\n\n')
    : String(finalContents || '').replace(/^# [^\n]+ - Pagina \d+\s*\n\n?/, '');

  const base = cartella_output ? `${cartella_output}/${projectName}` : `/shared/projects/${projectName}`;
  fs.mkdirSync(base, { recursive: true });
  const filePath = `${base}/${projectName}.md`;
  fs.writeFileSync(filePath, joined, 'utf8');

  return { saved: filePath, projectName, bytes: Buffer.byteLength(joined, 'utf8') };
}
