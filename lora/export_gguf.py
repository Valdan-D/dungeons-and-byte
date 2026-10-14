"""Esporta un adapter LoRA in GGUF Q8_0 e copia su Drive SOLO i file finali.
Uso: python export_gguf.py <tipologia>
Esporta sul disco locale della VM (il Drive montato non regge file grandi e satura la RAM) e poi copia una volta sola."""
import os, sys, glob, gc, shutil
os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")

R = "/content/drive/MyDrive/colab"
tip = sys.argv[1]
adapter_dir = f"{R}/adapters/{tip}"
assert os.path.isdir(adapter_dir), f"Adapter non trovato: {adapter_dir}"
dest = f"{R}/adapters/{tip}_gguf_gguf"
prev = f"{R}/adapters/{tip}_v2_gguf_gguf"

from unsloth import FastLanguageModel
model, tokenizer = FastLanguageModel.from_pretrained(model_name=adapter_dir, max_seq_length=4096, load_in_4bit=True)

tmp = "/content/gguf_tmp"
shutil.rmtree(tmp, ignore_errors=True)
os.makedirs(tmp, exist_ok=True)
model.save_pretrained_gguf(f"{tmp}/{tip}", tokenizer, quantization_method="q8_0")

prodotte = [d for d in glob.glob(f"{tmp}/*") if os.path.isdir(d) and glob.glob(d + "/*.gguf")]
assert prodotte, "Nessun file .gguf prodotto"
if os.path.isdir(dest) and not os.path.exists(prev):      # GGUF di un adapter precedente: lo conservo
    os.rename(dest, prev)
    print("GGUF precedente conservato in", prev)
os.makedirs(dest, exist_ok=True)
for fn in glob.glob(prodotte[0] + "/*"):
    if os.path.isfile(fn) and not fn.endswith(".safetensors"):
        shutil.copy2(fn, dest)
for f in glob.glob(dest + "/*.gguf"):
    print("GGUF pronto:", f, round(os.path.getsize(f) / 1e9, 2), "GB")
shutil.rmtree(tmp, ignore_errors=True)
del model
gc.collect()
