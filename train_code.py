"""Train a small adapter from code edits that already passed the checks.

Running this file only reports whether training can start. It does not download
a model. Add --run when the report says training can start.

A run needs three things already on this PC:
- at least 8 saved edits in memory/code_edits.jsonl
- the original model folder, with config.json and weight files, at
  models/base or in the CODE_BASE_MODEL path (the Ollama GGUF cannot be trained)
- torch, transformers, peft, and bitsandbytes installed in this Python
"""
import argparse
import os
import sys
from pathlib import Path

import memory

ROOT = Path(__file__).resolve().parent
MIN_EDITS = 8
ADAPTER = ROOT / "memory" / "adapters" / "code"


def base_model_dir():
    chosen = os.environ.get("CODE_BASE_MODEL") or str(ROOT / "models" / "base")
    path = Path(chosen)
    if (path / "config.json").is_file():
        return path
    return None


def load_examples():
    rows = []
    for item in memory._load_jsonl(memory.CODE_EDITS):
        changes = item.get("changes") or []
        if not changes:
            continue
        parts = []
        for change in changes:
            parts.append("File: %s\nBefore:\n%s\nAfter:\n%s" % (
                change.get("path") or "",
                change.get("before") or "",
                change.get("after") or "",
            ))
        rows.append({
            "instruction": item.get("problem") or "Fix the code.",
            "output": "\n\n".join(parts),
        })
    return rows


def report():
    examples = load_examples()
    base = base_model_dir()
    missing = []
    if len(examples) < MIN_EDITS:
        missing.append("at least %s successful edits (there are %s)" % (MIN_EDITS, len(examples)))
    if base is None:
        missing.append("the original model folder at %s" % (ROOT / "models" / "base"))
    packages = []
    for name in ("torch", "transformers", "peft", "bitsandbytes"):
        try:
            __import__(name)
        except ImportError:
            packages.append(name)
    if packages:
        missing.append("these packages: %s" % ", ".join(packages))
    return examples, base, missing


def train(examples, base):
    import torch
    from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
    from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig

    tokenizer = AutoTokenizer.from_pretrained(base, local_files_only=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
    quant = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_quant_type="nf4",
        bnb_4bit_compute_dtype=torch.float16,
    )
    model = AutoModelForCausalLM.from_pretrained(
        base,
        quantization_config=quant,
        device_map="auto",
        local_files_only=True,
    )
    model = prepare_model_for_kbit_training(model)
    model = get_peft_model(model, LoraConfig(
        r=8,
        lora_alpha=16,
        lora_dropout=0.05,
        bias="none",
        task_type="CAUSAL_LM",
        target_modules=["q_proj", "v_proj"],
    ))
    model.train()
    optimizer = torch.optim.AdamW((param for param in model.parameters() if param.requires_grad), lr=1e-4)
    for example in examples:
        text = "Problem:\n%s\n\nEdit:\n%s" % (example["instruction"], example["output"])
        batch = tokenizer(text, return_tensors="pt", truncation=True, max_length=1024)
        batch = {key: value.to(model.device) for key, value in batch.items()}
        loss = model(**batch, labels=batch["input_ids"]).loss
        loss.backward()
        optimizer.step()
        optimizer.zero_grad(set_to_none=True)
    ADAPTER.mkdir(parents=True, exist_ok=True)
    model.save_pretrained(ADAPTER)
    tokenizer.save_pretrained(ADAPTER)
    print("Saved the adapter in %s" % ADAPTER)
    print("Ollama still runs the GGUF. This adapter is the trained piece. Merging it into a new GGUF needs a local convert tool and is not done here.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", action="store_true", help="Train when every requirement is already on this PC")
    args = parser.parse_args()
    examples, base, missing = report()
    print("Saved edits: %s" % len(examples))
    if base:
        print("Base model: %s" % base)
    if missing:
        print("Training can start after you have:")
        for item in missing:
            print("- %s" % item)
        if any(item.startswith("these packages") for item in missing):
            print("Install them with: python -m pip install torch transformers peft bitsandbytes")
        print("The Ollama GGUF stays as it is. This script does not download weights.")
        return 0
    print("Ready. %s examples from %s." % (len(examples), base))
    if not args.run:
        print("Start it with: python train_code.py --run")
        return 0
    train(examples, base)
    return 0


if __name__ == "__main__":
    sys.exit(main())
