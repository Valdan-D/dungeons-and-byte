#!/bin/bash
# uso: prova_estrazione.sh PROGETTO tip [tip ...]   (usa le finestre gia' costruite in <progetto>/estrazione/)
P="/shared/projects/$1"; shift
for t in "$@"; do
  python3 /opt/dnb-lora-models/libro_lora_v2.py "$t" "$P/estrazione/finestre_$t.json" "$P/estrazione/$t.jsonl" > "$P/estrazione/$t.log" 2>&1
done
echo FINE_PROVA > "$P/estrazione/PROVA_FINE"
