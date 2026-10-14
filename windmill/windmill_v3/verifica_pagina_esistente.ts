// V3.0 - verifica-pagina-esistente + pagina-gia-fatta?: una pagina e' "gia' fatta" se esiste markdown/page_N.md.
import fs from 'fs';
export async function main(projectName: string, page: number) {
  const filePath = `/shared/projects/${projectName}/markdown/page_${page}.md`;
  const paginaGiaFatta = fs.existsSync(filePath);
  return { projectName, page, paginaGiaFatta, ...(paginaGiaFatta ? { fileName: `page_${page}.md`, filePath } : {}) };
}
