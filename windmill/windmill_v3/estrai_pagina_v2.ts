// V3.0 - estrai-pagina-v2: POST /parse_page (timeout 600 s come in V_2.0). Se il gateway risponde con un errore il flusso si ferma;
// rilanciandolo riparte dalle pagine mancanti (quelle gia' fatte vengono saltate). Il testo della pagina resta nel file markdown/page_N.md.
export async function main(pdfPath: string, projectName: string, page: number) {
  const r = await fetch('http://<GPU_HOST>:8091/parse_page', { method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ pdfPath, projectName, page }), signal: AbortSignal.timeout(600000) });
  const corpo: any = await r.json().catch(() => ({}));
  if (!r.ok || corpo.status === 'error') throw new Error(`/parse_page pagina ${page} ha risposto ${r.status}: ${JSON.stringify(corpo)}`);
  return { status: corpo.status, page: corpo.page, fileName: corpo.fileName };
}
