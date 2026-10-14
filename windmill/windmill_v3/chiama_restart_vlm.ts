// V3.0 - chiama-restart-vlm: POST /restart_vlm (timeout 90 s come in V_2.0).
export async function main() {
  const r = await fetch('http://<GPU_HOST>:8091/restart_vlm', { method: 'POST', signal: AbortSignal.timeout(90000) });
  const corpo = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(`/restart_vlm ha risposto ${r.status}: ${JSON.stringify(corpo)}`);
  return corpo;
}
