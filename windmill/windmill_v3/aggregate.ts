// V3.0 - Aggregate: raccoglie il testo finale di tutte le pagine.
export async function main(items: any[]) {
  const projectName = items[0]?.projectName || '';
  const finalContent = items.map((i: any) => i.finalContent);
  return { finalContent, projectName };
}
