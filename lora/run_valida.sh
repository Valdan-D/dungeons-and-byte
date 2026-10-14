#!/bin/bash
# Validazione GGUF v5: ferma l'OCR di produzione e lo riavvia SEMPRE all'uscita (anche in caso di errore o Ctrl-C).
trap 'systemctl start paddleocr-vlm paddleocr-gateway; echo "OCR riavviato: $(systemctl is-active paddleocr-vlm paddleocr-gateway | tr "\n" " ")"; echo FINE_VALIDAZIONE' EXIT
systemctl stop paddleocr-gateway paddleocr-vlm
sleep 3
cd /opt/dnb-lora-models && python3 valida_finestre.py "$@"
