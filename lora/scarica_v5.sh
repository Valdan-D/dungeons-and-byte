#!/bin/bash
# Scarica i 3 GGUF v5 da Drive (link condiviso dall'utente) in /opt/dnb-lora-models/v5/<tipologia>/
cd /opt/dnb-lora-models
dl() { tip=$1; id=$2; mkdir -p v5/$tip
  echo "[$(date +%H:%M:%S)] $tip"
  /usr/local/bin/gdown -q --continue "https://drive.google.com/uc?id=$id" -O v5/$tip/Qwen3-4B.Q8_0.gguf || echo "FALLITO $tip"
  ls -la v5/$tip; }
dl bestiario_new 1eLd-T2fz066ld2JNzaB7ij_ndeGr_Js-
dl oggetto 1Y2Ct9MSUxR1MzLRwE9UJMnSyVzC3Mt5I
dl regola 1-1_xWBA_tdgvALg5U86N76TFntSt54PF
echo FATTO_DOWNLOAD
