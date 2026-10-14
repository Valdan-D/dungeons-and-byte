#!/bin/bash
# Coda: aspetta che regola finisca, poi passa gli altri LoRA sull'SRD uno alla volta (riprende da dove si e' fermato)
cd /opt/dnb-lora-models
R=risultati_srd
echo "$(date) coda avviata: attendo regola" >> $R/coda.log
until grep -q FINE_LIBRO_LORA $R/regola_unite.log 2>/dev/null; do sleep 30; done
for t in incantesimo classe specie background luogo; do
  if grep -q FINE_LIBRO_LORA $R/${t}_unite.log 2>/dev/null; then echo "$(date) $t gia' fatto" >> $R/coda.log; continue; fi
  echo "$(date) avvio $t" >> $R/coda.log
  python3 libro_lora_v2.py $t /opt/dnb-lora-models/srd_finestre_unite.json /opt/dnb-lora-models/$R/${t}_unite.jsonl >> $R/${t}_unite.log 2>&1
  echo "$(date) fine $t" >> $R/coda.log
done
echo "$(date) CODA_COMPLETATA" >> $R/coda.log
