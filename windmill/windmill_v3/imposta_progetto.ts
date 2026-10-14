// V3.0 - imposta-progetto: il nome del progetto e' il nome del PDF nel pool (senza .pdf).
import fs from 'fs';
export async function main(progetto: any) {
  const projectName: string = typeof progetto === 'string' ? progetto : progetto?.value;
  if (!projectName) throw new Error('Nessun progetto selezionato');
  const pdfPath = `/shared/projects/pool/${projectName}.pdf`;
  if (!fs.existsSync(pdfPath)) throw new Error(`PDF non trovato nel pool: ${pdfPath}`);
  return { projectName, pdfPath };
}
