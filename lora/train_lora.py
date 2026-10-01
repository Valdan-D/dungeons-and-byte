"""
Training LoRA per il progetto Dungeons & Byte, pensato per girare su Google Colab
(GPU T4 gratuita) con dati/checkpoint/output salvati su Google Drive per sopravvivere
a disconnessioni di sessione.

Uso tipico da notebook:
    import sys
    sys.path.append("/content/drive/MyDrive/colab/scripts")
    from train_lora import run_training
    run_training(tipologia="incantesimo")

Oppure da riga di comando (se il runtime Colab ha già il Drive montato):
    python train_lora.py --tipologia incantesimo

Ripresa dopo disconnessione: rilanciare lo stesso comando con la stessa tipologia,
lo script trova da solo l'ultimo checkpoint in checkpoints/<tipologia>/ e riparte da li'.
"""

import argparse
import glob
import os


DRIVE_ROOT = "/content/drive/MyDrive/colab"

# Qwen3-4B e' la scelta sicura (larghissimo margine di VRAM anche sulla T4 16GB).
# Qwen3-8B e' possibile su T4 (16GB, il doppio della T1000 locale dove aveva fallito
# per OOM) ma lascia meno margine: provarlo solo se il tempo lo permette, monitorando
# torch.cuda.max_memory_allocated() nei log.
DEFAULT_MODEL = "unsloth/Qwen3-4B-unsloth-bnb-4bit"

MAX_SEQ_LENGTH = 4096
LORA_R = 16
LORA_ALPHA = 16
TARGET_MODULES = ["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"]


def _latest_checkpoint(checkpoint_dir: str):
    checkpoints = glob.glob(os.path.join(checkpoint_dir, "checkpoint-*"))
    if not checkpoints:
        return None
    return max(checkpoints, key=lambda p: int(p.rsplit("-", 1)[-1]))


def run_training(
    tipologia: str,
    model_name: str = DEFAULT_MODEL,
    num_train_epochs: int = 3,
    per_device_train_batch_size: int = 2,
    gradient_accumulation_steps: int = 4,
    learning_rate: float = 2e-4,
    save_steps: int = 25,
    drive_root: str = DRIVE_ROOT,
):
    from unsloth import FastLanguageModel
    import torch
    from datasets import load_dataset
    from trl import SFTTrainer, SFTConfig

    dataset_path = os.path.join(drive_root, "dataset", f"{tipologia}_train.jsonl")
    checkpoint_dir = os.path.join(drive_root, "checkpoints", tipologia)
    adapter_dir = os.path.join(drive_root, "adapters", tipologia)
    log_path = os.path.join(drive_root, "logs", f"{tipologia}.log")

    os.makedirs(checkpoint_dir, exist_ok=True)
    os.makedirs(adapter_dir, exist_ok=True)
    os.makedirs(os.path.dirname(log_path), exist_ok=True)

    if not os.path.isfile(dataset_path):
        raise FileNotFoundError(
            f"Dataset non trovato: {dataset_path}\n"
            f"Verificare che la cartella '{drive_root}' sia stata caricata su Google Drive "
            f"e che il Drive sia montato in Colab (drive.mount('/content/drive'))."
        )

    print(f"[{tipologia}] Carico modello base: {model_name}", flush=True)
    model, tokenizer = FastLanguageModel.from_pretrained(
        model_name=model_name,
        max_seq_length=MAX_SEQ_LENGTH,
        load_in_4bit=True,
        dtype=None,
        device_map={"": 0},
    )

    model = FastLanguageModel.get_peft_model(
        model,
        r=LORA_R,
        target_modules=TARGET_MODULES,
        lora_alpha=LORA_ALPHA,
        lora_dropout=0,
        bias="none",
        use_gradient_checkpointing="unsloth",
        random_state=42,
    )

    print(f"[{tipologia}] VRAM dopo LoRA: {torch.cuda.memory_allocated()/1e9:.2f} GB", flush=True)

    dataset = load_dataset("json", data_files=dataset_path, split="train")

    def formatting(example):
        text = tokenizer.apply_chat_template(
            example["messages"], tokenize=False, add_generation_prompt=False, enable_thinking=False
        )
        return {"text": text}

    dataset = dataset.map(formatting)
    print(f"[{tipologia}] Dataset pronto: {len(dataset)} esempi", flush=True)

    resume_from = _latest_checkpoint(checkpoint_dir)
    if resume_from:
        print(f"[{tipologia}] Trovato checkpoint precedente, riprendo da: {resume_from}", flush=True)

    trainer = SFTTrainer(
        model=model,
        tokenizer=tokenizer,
        train_dataset=dataset,
        args=SFTConfig(
            dataset_text_field="text",
            max_seq_length=MAX_SEQ_LENGTH,
            per_device_train_batch_size=per_device_train_batch_size,
            gradient_accumulation_steps=gradient_accumulation_steps,
            warmup_steps=10,
            num_train_epochs=num_train_epochs,
            learning_rate=learning_rate,
            logging_steps=5,
            optim="adamw_8bit",
            weight_decay=0.01,
            lr_scheduler_type="linear",
            seed=42,
            output_dir=checkpoint_dir,
            save_strategy="steps",
            save_steps=save_steps,
            save_total_limit=3,
            report_to="none",
        ),
    )

    result = trainer.train(resume_from_checkpoint=resume_from)

    print(f"[{tipologia}] Training completato. Loss finale: {result.training_loss}", flush=True)
    print(f"[{tipologia}] VRAM di picco: {torch.cuda.max_memory_allocated()/1e9:.2f} GB", flush=True)

    model.save_pretrained(adapter_dir)
    tokenizer.save_pretrained(adapter_dir)
    print(f"[{tipologia}] Adapter LoRA salvato in: {adapter_dir}", flush=True)

    with open(log_path, "a", encoding="utf-8") as f:
        f.write(f"loss_finale={result.training_loss} adapter_dir={adapter_dir}\n")

    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--tipologia", required=True, choices=[
        "bestiario_png", "incantesimo", "specie", "classe",
        "background", "luogo", "oggetto", "regola",
    ])
    parser.add_argument("--model-name", default=DEFAULT_MODEL)
    parser.add_argument("--epochs", type=int, default=3)
    parser.add_argument("--batch-size", type=int, default=2)
    parser.add_argument("--grad-accum", type=int, default=4)
    parser.add_argument("--lr", type=float, default=2e-4)
    parser.add_argument("--save-steps", type=int, default=25)
    args = parser.parse_args()

    run_training(
        tipologia=args.tipologia,
        model_name=args.model_name,
        num_train_epochs=args.epochs,
        per_device_train_batch_size=args.batch_size,
        gradient_accumulation_steps=args.grad_accum,
        learning_rate=args.lr,
        save_steps=args.save_steps,
    )
