// V3.0 - applica-fix-manuali: applica <progetto>/manual_fixes.json (se presente) al testo di ogni pagina. Portato da V_2.0 senza modifiche di logica.
import fs from 'fs';
export async function main(items: any[]) {
  let projectName = items[0]?.projectName || '';

  const fixesPath = `/shared/projects/${projectName}/manual_fixes.json`;

  let fixes = [];
  if (fs.existsSync(fixesPath)) {
    try {
      fixes = JSON.parse(fs.readFileSync(fixesPath, 'utf8'));
    } catch (e) {
      throw new Error(`manual_fixes.json non e' JSON valido per il progetto '${projectName}': ${e.message}`);
    }
  }

  if (fixes.length === 0) {
    return items;
  }

  function escapeRegExp(s) {
    return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  }

  function applyFix(text, fix) {
    const pattern = fix.regex
      ? new RegExp(fix.find, fix.flags || 'g')
      : new RegExp(escapeRegExp(fix.find), 'g');
    return text.replace(pattern, fix.replace);
  }

  for (const item of items) {
    const pageNum = item.page;
    let text = item.finalContent || '';
    for (const fix of fixes) {
      if (fix.page != null && fix.page !== pageNum) continue;
      text = applyFix(text, fix);
    }
    item.finalContent = text;
  }

  return items;
}
