#!/usr/bin/env python3
"""Fine-tune Qwen 3.5 9B or 27B with LoRA for Serbian legal domain.

Usage:
    python scripts/train_lora.py --model 9b     # Fine-tune 9B (16-bit LoRA)
    python scripts/train_lora.py --model 27b    # Fine-tune 27B (4-bit + LoRA)
    python scripts/train_lora.py --model 9b --export-gguf   # Train + export GGUF

Requirements:
    pip install unsloth peft trl datasets accelerate bitsandbytes

Hardware:
    - 9B: RTX 3090 (24 GB VRAM) — 16-bit LoRA, ~22 GB VRAM
    - 27B: RTX 3090 (24 GB VRAM) — 4-bit base + LoRA, ~20 GB VRAM
"""
import argparse
import json
import os
import sys
from pathlib import Path

# Suppress warnings and disable memory-hungry fused losses
os.environ["TOKENIZERS_PARALLELISM"] = "false"
os.environ["UNSLOTH_DISABLE_FUSED_CE"] = "1"


def _patch_fused_ce():
    """Monkey-patch Unsloth fused CE loss to avoid OOM on 24GB GPUs with large models."""
    try:
        import unsloth_zoo.fused_losses.cross_entropy_loss as ce_mod
        original_get_chunk = ce_mod._get_chunk_multiplier
        def _patched_get_chunk(vocab_size, target_gb=1.0):
            try:
                return original_get_chunk(vocab_size, target_gb)
            except RuntimeError:
                # Fallback: use minimal chunks
                return max(1, vocab_size // 8192)
        ce_mod._get_chunk_multiplier = _patched_get_chunk
    except Exception:
        pass

_patch_fused_ce()


def main():
    parser = argparse.ArgumentParser(description="Fine-tune Qwen 3.5 for LexArdor")
    parser.add_argument("--model", choices=["9b", "27b"], default="9b", help="Model size to fine-tune")
    parser.add_argument("--epochs", type=int, default=3, help="Number of training epochs")
    parser.add_argument("--batch-size", type=int, default=1, help="Per-device batch size")
    parser.add_argument("--lr", type=float, default=2e-4, help="Learning rate")
    parser.add_argument("--lora-r", type=int, default=None, help="LoRA rank (default: 16 for 9b, 8 for 27b)")
    parser.add_argument("--max-seq-len", type=int, default=1024, help="Max sequence length")
    parser.add_argument("--export-gguf", action="store_true", help="Export to GGUF after training")
    parser.add_argument("--output-dir", type=str, default=None, help="Output directory for adapter")
    parser.add_argument("--dataset", type=str, default="data/training/lexardor_final_training.jsonl", help="Training data path")
    args = parser.parse_args()

    # ── Model selection ──────────────────────────────────────────────────────
    if args.model == "9b":
        model_name = "unsloth/Qwen3.5-9B"
        load_in_16bit = False
        load_in_4bit = True   # 4-bit to fit training in 24GB (16-bit OOMs)
    else:
        model_name = "unsloth/Qwen3.5-27B"
        load_in_16bit = False
        load_in_4bit = True  # 27B needs 4-bit to fit in 24GB

    if args.lora_r is None:
        args.lora_r = 8 if args.model == "27b" else 16

    output_dir = args.output_dir or f"data/training/lexardor-qwen3.5-{args.model}-lora"

    print(f"{'='*60}")
    print(f"  LexArdor Fine-Tuning — Qwen 3.5 {args.model.upper()}")
    print(f"{'='*60}")
    print(f"  Model: {model_name}")
    print(f"  Precision: {'16-bit' if load_in_16bit else '4-bit'}")
    print(f"  LoRA rank: {args.lora_r}")
    print(f"  Epochs: {args.epochs}")
    print(f"  Batch size: {args.batch_size}")
    print(f"  Learning rate: {args.lr}")
    print(f"  Max seq len: {args.max_seq_len}")
    print(f"  Dataset: {args.dataset}")
    print(f"  Output: {output_dir}")
    print(f"{'='*60}")

    # ── Load model ───────────────────────────────────────────────────────────
    print("\n[1/5] Loading model...")
    from unsloth import FastLanguageModel

    model, tokenizer = FastLanguageModel.from_pretrained(
        model_name=model_name,
        max_seq_length=args.max_seq_len,
        load_in_4bit=load_in_4bit,
        load_in_16bit=load_in_16bit,
        full_finetuning=False,  # LoRA only
    )

    # ── Configure LoRA ───────────────────────────────────────────────────────
    print("[2/5] Configuring LoRA adapter...")
    model = FastLanguageModel.get_peft_model(
        model,
        r=args.lora_r,
        lora_alpha=args.lora_r,  # alpha = r is standard
        lora_dropout=0,
        bias="none",
        target_modules="all-linear",
        modules_to_save=["lm_head", "embed_tokens"],
    )

    # Print trainable params
    trainable = sum(p.numel() for p in model.parameters() if p.requires_grad)
    total = sum(p.numel() for p in model.parameters())
    print(f"  Trainable: {trainable:,} / {total:,} ({trainable/total*100:.2f}%)")

    # ── Load dataset ─────────────────────────────────────────────────────────
    print("[3/5] Loading dataset...")
    from datasets import Dataset

    data = []
    with open(args.dataset, "r", encoding="utf-8") as f:
        for line in f:
            item = json.loads(line)
            # Format as chat template
            messages = [
                {"role": "system", "content": "Ti si LexArdor, AI pravni asistent za srpsko pravo. Odgovaraj precizno na osnovu priloženih izvora. Citiraj članove zakona inline."},
                {"role": "user", "content": f"{item['instruction']}\n\n{item['input']}"},
                {"role": "assistant", "content": item["output"]},
            ]
            text = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=False)
            data.append({"text": text})

    dataset = Dataset.from_list(data)
    print(f"  Dataset size: {len(dataset)} examples")

    # ── Train ────────────────────────────────────────────────────────────────
    print("[4/5] Training...")
    from trl import SFTTrainer
    from transformers import TrainingArguments

    trainer = SFTTrainer(
        model=model,
        tokenizer=tokenizer,
        train_dataset=dataset,
        args=TrainingArguments(
            output_dir=output_dir,
            per_device_train_batch_size=args.batch_size,
            gradient_accumulation_steps=8,
            gradient_checkpointing=True,
            num_train_epochs=args.epochs,
            learning_rate=args.lr,
            fp16=False,
            bf16=True,
            logging_steps=10,
            save_strategy="epoch",
            warmup_steps=50,
            weight_decay=0.01,
            optim="adamw_8bit",
            seed=42,
            report_to="none",
        ),
        max_seq_length=args.max_seq_len,
    )

    trainer.train()
    print("  Training complete!")

    # ── Save adapter ─────────────────────────────────────────────────────────
    print("[5/5] Saving...")
    model.save_pretrained(output_dir)
    tokenizer.save_pretrained(output_dir)
    print(f"  LoRA adapter saved to: {output_dir}")

    # ── Export to GGUF ───────────────────────────────────────────────────────
    if args.export_gguf:
        print("\n[BONUS] Exporting to GGUF...")
        gguf_dir = output_dir + "-gguf"
        # Merge LoRA + quantize
        model.save_pretrained_gguf(
            gguf_dir,
            tokenizer,
            quantization_method="q8_0" if args.model == "9b" else "q4_k_m",
        )
        print(f"  GGUF saved to: {gguf_dir}")

        # Copy to models directory
        gguf_files = list(Path(gguf_dir).glob("*.gguf"))
        if gguf_files:
            dest = Path.home() / "models" / "lexardor" / f"LexArdor-Qwen3.5-{args.model.upper()}-Legal.gguf"
            import shutil
            shutil.copy2(gguf_files[0], dest)
            print(f"  Copied to: {dest}")

    print(f"\n{'='*60}")
    print(f"  DONE! Fine-tuned Qwen 3.5 {args.model.upper()} for LexArdor")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
