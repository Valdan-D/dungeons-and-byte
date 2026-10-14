// V3.0 - crea-cartelle: POST /setup del gateway PaddleOCR (stesso endpoint di V_2.0).
export async function main(projectName: string) {
  const r = await fetch('http://192.168.178.121:8091/setup', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ projectName }) });
  const corpo = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(`/setup ha risposto ${r.status}: ${JSON.stringify(corpo)}`);
  return corpo;
}
