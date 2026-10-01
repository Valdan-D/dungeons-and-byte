# LoRA entity extraction (experimental)

Fine-tuned LoRA adapters (Qwen3-4B, [Unsloth](https://github.com/unslothai/unsloth) on a free
Colab T4) that read a window of rulebook Markdown and return the game entities it contains as a
JSON array — one adapter per entity type (spells, items, species, classes, backgrounds, places,
rules, monsters).

**No book content is published here.** Training data is derived from commercial manuals and stays
private; only code lives in this repo. Examples and docs refer to the CC-licensed SRD only.

## Files

| File | Purpose |
|---|---|
| `train_lora.py` | Training logic (Unsloth + TRL `SFTTrainer`), resumes from the last checkpoint after a Colab disconnect |
| `train_lora_qwen3.ipynb` | Colab notebook that drives it and exports the adapter to GGUF (Q8_0) for `llama-server` |
| `validate_lora.py` | Runs each adapter on held-out books and compares against a ground truth. Held-out books are declared in `validation_books.json` (not in the repo): `{"<project folder>": "<ground-truth file suffix>"}` |
| `test_finestre.py` | Checks multi-entity windows: how many of the entities in a window the model actually returns, and whether it answers `[]` when there are none |

Training expects the layout `colab/{dataset,notebooks,scripts,adapters,checkpoints,logs}` on Google
Drive (`MyDrive/colab`); datasets are one JSONL file per type in chat format (`system` / `user` /
`assistant`).

## Lessons learned (why windows)

- `MAX_SEQ_LENGTH` is 4096 tokens and the trainer truncates **prompt + answer** together. Examples
  built from whole chapters lose the end of the answer, so the model never saw a complete
  multi-entity output and learned to stop at the first entity.
- Training examples must therefore be **windows** (~2,500 tokens, cut between paragraphs, never
  through an entity) that contain *all* the entities of that window, plus some windows with no
  entities whose answer is `[]`. The same windowing has to be used at inference time.
- Entity text must be copied verbatim from the source, never summarized; in older, loosely
  structured modules an entity can be scattered over several places, so extraction is per window
  and fragments are merged by name afterwards.
- Qwen3 needs `enable_thinking: false` at inference, otherwise it can stall inside `<think>`.
