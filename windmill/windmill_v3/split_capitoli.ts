// V3.0 - split-capitoli: cancella e riscrive <progetto>/capitoli/*.md dividendo il testo ai titoli '## '. Portato da V_2.0 senza modifiche di logica.
import fs from 'fs';
export async function main(items: any[], cartella_output: string = '') {
  const projectName = items[0]?.projectName || '';
  const capitoliDir = cartella_output ? `${cartella_output}/${projectName}/capitoli` : `/shared/projects/${projectName}/capitoli`;

  if (fs.existsSync(capitoliDir)) {
    for (const f of fs.readdirSync(capitoliDir)) {
      fs.unlinkSync(`${capitoliDir}/${f}`);
    }
  }
  fs.mkdirSync(capitoliDir, { recursive: true });

  const texts = items.map(item => {
    let content = item.finalContent || '';
    content = content.replace(/^# .+? - Pagina \d+\s*\n/, '');
    return content.trim();
  });

  let fullText = texts.join('\n\n<!-- pb -->\n\n');

  fullText = fullText.replace(/(\w)-\n\n<!-- pb -->\n\n(\w)/g, '$1$2');
  fullText = fullText.replace(/([^.!?:;»\")\]\n])\n\n<!-- pb -->\n\n([a-z\u00e0-\u00fc])/g, '$1 $2');
  fullText = fullText.replace(/\n*<!-- pb -->\n*/g, '\n\n');
  fullText = fullText.replace(/\n{3,}/g, '\n\n');

  const parts = fullText.split(/(?=^## )/m).filter(p => p.trim());

  if (parts.length === 0) {
    const filename = `${capitoliDir}/01-completo.md`;
    fs.writeFileSync(filename, fullText, 'utf8');
    return { salvati: 1, files: [filename], projectName };
  }

  const savedFiles = [];
  for (let i = 0; i < parts.length; i++) {
    const content = parts[i].trim();
    const firstLine = content.split('\n')[0].trim();
    let title = firstLine.replace(/^#+\s*/, '').trim();
    if (/^!\[.*\]\(.*\)/.test(firstLine) || !title) {
      title = 'COPERTINA';
    }
    const slug = title
      .toLowerCase()
      .replace(/[\u00e0\u00e1\u00e2\u00e4]/g, 'a').replace(/[\u00e8\u00e9\u00ea\u00eb]/g, 'e')
      .replace(/[\u00ec\u00ed\u00ee\u00ef]/g, 'i').replace(/[\u00f2\u00f3\u00f4\u00f6]/g, 'o').replace(/[\u00f9\u00fa\u00fb\u00fc]/g, 'u')
      .replace(/[^a-z0-9.]+/g, '-').replace(/^-|-$/g, '').slice(0, 60);
    const filename = `${capitoliDir}/${String(i + 1).padStart(2, '0')}-${slug}.md`;
    fs.writeFileSync(filename, content, 'utf8');
    savedFiles.push({ index: i + 1, title, filename });
  }

  return { salvati: savedFiles.length, files: savedFiles, projectName };
}
