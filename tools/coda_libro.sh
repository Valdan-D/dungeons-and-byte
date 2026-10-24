#!/bin/bash
# uso: coda_libro.sh PROGETTO tip [tip ...]  — attende la GPU libera, costruisce le finestre mancanti, lancia i LoRA in fila (riprendono da dove erano).
P="/shared/projects/$1"; shift
D="$P/estrazione"; mkdir -p "$D"
while pgrep -f libro_lora_v2.py > /dev/null; do sleep 15; done
for t in "$@"; do
  [ -f "$D/finestre_$t.json" ] || (cd /opt/dnb-classificatore && /opt/classificatore_test/venv/bin/python costruisci_finestre.py "$(basename "$P")" "$t" >> "$D/$t.log" 2>&1)
  echo "$(date) avvio $t" >> "$D/coda_libro.log"
  python3 /opt/dnb-lora-models/libro_lora_v2.py "$t" "$D/finestre_$t.json" "$D/$t.jsonl" >> "$D/$t.log" 2>&1
  echo "$(date) fine $t" >> "$D/coda_libro.log"
done
echo "$(date) CODA_COMPLETATA" >> "$D/coda_libro.log"
