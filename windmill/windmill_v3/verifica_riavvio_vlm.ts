// V3.0 - verifica-riavvio-periodico-vlm: riavvio periodico del motore VLM ogni 15 pagine (leak di memoria noto).
// Questo passo si limita a DECIDERE; la chiamata di riavvio vera e propria e' nel passo successivo.
export async function main(page: number) {
  return { page, deveRiavviareVlm: (page % 15 === 0) };
}
