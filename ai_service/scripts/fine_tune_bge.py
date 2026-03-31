#!/usr/bin/env python3
"""Step 4: Fine-tune BAAI/bge-m3 on Saudi legal triplets.

Loads train.jsonl and val.jsonl from Step 3, trains with
MultipleNegativesRankingLoss, evaluates with InformationRetrievalEvaluator,
and saves the fine-tuned model.

Optimized for RTX 4070 Ti Super (16GB VRAM).

Usage:
    python -m ai_service.scripts.fine_tune_bge

    # Custom hyperparameters
    python -m ai_service.scripts.fine_tune_bge \
        --batch-size 8 --epochs 3 --lr 2e-5 --max-seq-length 512
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

from loguru import logger

from ai_service.scripts._shared.paths import (
    EVAL_REPORT,
    FINETUNED_MODEL_DIR,
    TRAIN_JSONL,
    VAL_JSONL,
    ensure_dirs,
)

# ---------------------------------------------------------------------------
# Evaluation helpers
# ---------------------------------------------------------------------------


def _build_ir_eval_data(
    triplets: list[dict],
) -> tuple[dict[str, str], dict[str, str], dict[str, dict[str, int]]]:
    """Convert triplets into IR evaluation format.

    Returns:
        queries: {qid: query_text}
        corpus:  {cid: doc_text}
        relevant_docs: {qid: {cid: 1}}
    """
    queries: dict[str, str] = {}
    corpus: dict[str, str] = {}
    relevant_docs: dict[str, dict[str, int]] = {}
    corpus_id_counter = 0

    # Group by query to avoid duplicates
    query_groups: dict[str, dict] = {}
    for t in triplets:
        qkey = t["query"][:200]  # Use first 200 chars as key
        if qkey not in query_groups:
            query_groups[qkey] = {
                "query": t["query"],
                "positives": set(),
                "negatives": set(),
            }
        query_groups[qkey]["positives"].add(t["positive"])
        query_groups[qkey]["negatives"].add(t["negative"])

    for i, (qkey, group) in enumerate(query_groups.items()):
        qid = f"q{i}"
        queries[qid] = group["query"]
        relevant_docs[qid] = {}

        for pos in group["positives"]:
            cid = f"c{corpus_id_counter}"
            corpus[cid] = pos
            relevant_docs[qid][cid] = 1
            corpus_id_counter += 1

        for neg in group["negatives"]:
            cid = f"c{corpus_id_counter}"
            corpus[cid] = neg
            corpus_id_counter += 1

    return queries, corpus, relevant_docs


def _evaluate_model(
    model,
    val_triplets: list[dict],
    batch_size: int = 16,
) -> dict[str, float]:
    """Evaluate a SentenceTransformer model on validation triplets.

    Computes Recall@5, Recall@10, MRR, and nDCG@10.
    """
    from sentence_transformers.evaluation import (  # type: ignore
        InformationRetrievalEvaluator,
    )

    queries, corpus, relevant_docs = _build_ir_eval_data(val_triplets)

    if not queries or not corpus:
        logger.warning("Not enough data for evaluation")
        return {}

    evaluator = InformationRetrievalEvaluator(
        queries=queries,
        corpus=corpus,
        relevant_docs=relevant_docs,
        name="saudi-legal-val",
        batch_size=batch_size,
        show_progress_bar=True,
    )

    # Run evaluation
    results = evaluator(model)

    # Extract key metrics
    metrics = {}
    for key, value in results.items():
        if isinstance(value, (int, float)):
            # Clean up metric names
            clean_key = key.replace("saudi-legal-val_", "")
            metrics[clean_key] = round(value, 4)

    return metrics


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fine-tune BAAI/bge-m3 on Saudi legal training data."
    )
    parser.add_argument(
        "--train",
        type=str,
        default=str(TRAIN_JSONL),
        help=f"Training JSONL path (default: {TRAIN_JSONL})",
    )
    parser.add_argument(
        "--val",
        type=str,
        default=str(VAL_JSONL),
        help=f"Validation JSONL path (default: {VAL_JSONL})",
    )
    parser.add_argument(
        "--output-model",
        type=str,
        default=str(FINETUNED_MODEL_DIR),
        help=f"Output model directory (default: {FINETUNED_MODEL_DIR})",
    )
    parser.add_argument(
        "--base-model",
        type=str,
        default="BAAI/bge-m3",
        help="Base model name. Default: BAAI/bge-m3",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=8,
        help="Training batch size. Default: 8",
    )
    parser.add_argument(
        "--grad-accum",
        type=int,
        default=4,
        help="Gradient accumulation steps. Default: 4 (effective batch=32)",
    )
    parser.add_argument(
        "--epochs",
        type=int,
        default=3,
        help="Number of training epochs. Default: 3",
    )
    parser.add_argument(
        "--lr",
        type=float,
        default=2e-5,
        help="Learning rate. Default: 2e-5",
    )
    parser.add_argument(
        "--max-seq-length",
        type=int,
        default=512,
        help="Max sequence length. Default: 512 (try 384 if OOM)",
    )
    parser.add_argument(
        "--warmup-ratio",
        type=float,
        default=0.1,
        help="Warmup ratio. Default: 0.1",
    )
    parser.add_argument(
        "--weight-decay",
        type=float,
        default=0.01,
        help="Weight decay. Default: 0.01",
    )
    parser.add_argument(
        "--fp16",
        action="store_true",
        default=True,
        help="Use mixed precision (fp16). Default: True",
    )
    parser.add_argument(
        "--no-fp16",
        action="store_true",
        default=False,
        help="Disable mixed precision.",
    )
    args = parser.parse_args()

    use_fp16 = args.fp16 and not args.no_fp16

    ensure_dirs()
    train_path = Path(args.train)
    val_path = Path(args.val)
    output_model_path = Path(args.output_model)

    if not train_path.exists():
        logger.error(f"Training file not found: {train_path}")
        sys.exit(1)
    if not val_path.exists():
        logger.error(f"Validation file not found: {val_path}")
        sys.exit(1)

    # ----- Load data -----
    logger.info("Loading training data...")

    def _load_triplets(path: Path) -> list[dict]:
        triplets = []
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    triplets.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return triplets

    train_triplets = _load_triplets(train_path)
    val_triplets = _load_triplets(val_path)

    logger.info(f"  Train: {len(train_triplets)} triplets")
    logger.info(f"  Val:   {len(val_triplets)} triplets")

    if len(train_triplets) < 10:
        logger.error("Too few training triplets. Need at least 10.")
        sys.exit(1)

    # ----- Import sentence-transformers -----
    try:
        from sentence_transformers import (  # type: ignore
            InputExample,
            SentenceTransformer,
            losses,
        )
        from sentence_transformers.evaluation import (  # type: ignore
            InformationRetrievalEvaluator,
        )
        from torch.utils.data import DataLoader
    except ImportError as e:
        logger.error(
            f"Missing dependency: {e}. "
            f"Install with: pip install sentence-transformers torch"
        )
        sys.exit(1)

    # ----- Load model -----
    logger.info(f"Loading base model: {args.base_model}")

    try:
        import torch

        device = "cuda" if torch.cuda.is_available() else "cpu"
        if device == "cuda":
            gpu_name = torch.cuda.get_device_name(0)
            gpu_mem = torch.cuda.get_device_properties(0).total_mem / 1e9
            logger.info(f"  GPU: {gpu_name} ({gpu_mem:.1f} GB)")
        else:
            logger.warning("  No CUDA GPU detected, training on CPU (will be slow)")

        model = SentenceTransformer(args.base_model, device=device)
        model.max_seq_length = args.max_seq_length
        logger.info(f"  Model loaded, max_seq_length={args.max_seq_length}")
    except Exception as e:
        logger.error(f"Failed to load model: {e}")
        sys.exit(1)

    # ----- Baseline evaluation -----
    logger.info("Running baseline evaluation on val set...")
    baseline_metrics = _evaluate_model(model, val_triplets, batch_size=args.batch_size * 2)
    logger.info(f"  Baseline metrics: {baseline_metrics}")

    # ----- Prepare training data -----
    logger.info("Preparing training examples...")
    train_examples = [
        InputExample(texts=[t["query"], t["positive"], t["negative"]])
        for t in train_triplets
    ]

    train_dataloader = DataLoader(
        train_examples,
        shuffle=True,
        batch_size=args.batch_size,
    )

    train_loss = losses.MultipleNegativesRankingLoss(model)

    # ----- Prepare evaluator -----
    queries, corpus, relevant_docs = _build_ir_eval_data(val_triplets)
    evaluator = InformationRetrievalEvaluator(
        queries=queries,
        corpus=corpus,
        relevant_docs=relevant_docs,
        name="saudi-legal-val",
        batch_size=args.batch_size * 2,
        show_progress_bar=True,
    )

    # ----- Training -----
    steps_per_epoch = math.ceil(len(train_examples) / args.batch_size)
    total_steps = steps_per_epoch * args.epochs
    warmup_steps = int(total_steps * args.warmup_ratio)

    logger.info(f"Starting fine-tuning:")
    logger.info(f"  Epochs:           {args.epochs}")
    logger.info(f"  Batch size:       {args.batch_size}")
    logger.info(f"  Grad accum:       {args.grad_accum}")
    logger.info(f"  Effective batch:  {args.batch_size * args.grad_accum}")
    logger.info(f"  Learning rate:    {args.lr}")
    logger.info(f"  Warmup steps:     {warmup_steps}")
    logger.info(f"  Total steps:      {total_steps}")
    logger.info(f"  FP16:             {use_fp16}")
    logger.info(f"  Max seq length:   {args.max_seq_length}")
    logger.info(f"  Output:           {output_model_path}")

    try:
        # sentence-transformers 3.x supports gradient_accumulation_steps
        fit_kwargs: dict = dict(
            train_objectives=[(train_dataloader, train_loss)],
            evaluator=evaluator,
            epochs=args.epochs,
            warmup_steps=warmup_steps,
            output_path=str(output_model_path),
            evaluation_steps=max(100, len(train_dataloader) // 2),
            save_best_model=True,
            use_amp=use_fp16,
            optimizer_params={"lr": args.lr},
            weight_decay=args.weight_decay,
            show_progress_bar=True,
        )

        # Add gradient accumulation if supported (sentence-transformers >= 2.3)
        import inspect
        if "accumulation_steps" in inspect.signature(model.fit).parameters:
            fit_kwargs["accumulation_steps"] = args.grad_accum
        elif args.grad_accum > 1:
            logger.warning(
                f"Your sentence-transformers version does not support "
                f"accumulation_steps. Effective batch = {args.batch_size} "
                f"(not {args.batch_size * args.grad_accum})."
            )

        model.fit(**fit_kwargs)
        logger.info("Training completed successfully!")

    except RuntimeError as e:
        if "out of memory" in str(e).lower() or "CUDA" in str(e):
            logger.error(
                f"CUDA out of memory! Try reducing:\n"
                f"  --batch-size {max(1, args.batch_size // 2)}\n"
                f"  --max-seq-length {max(128, args.max_seq_length - 128)}\n"
                f"Original error: {e}"
            )
            sys.exit(1)
        raise

    # ----- Post-training evaluation -----
    logger.info("Running post-training evaluation...")

    # Load the best saved model
    finetuned_model = SentenceTransformer(str(output_model_path), device=device)
    finetuned_metrics = _evaluate_model(
        finetuned_model, val_triplets, batch_size=args.batch_size * 2
    )
    logger.info(f"  Fine-tuned metrics: {finetuned_metrics}")

    # ----- Comparison report -----
    report = {
        "base_model": args.base_model,
        "fine_tuned_model": str(output_model_path),
        "training_config": {
            "epochs": args.epochs,
            "batch_size": args.batch_size,
            "grad_accum": args.grad_accum,
            "lr": args.lr,
            "max_seq_length": args.max_seq_length,
            "fp16": use_fp16,
            "warmup_ratio": args.warmup_ratio,
            "weight_decay": args.weight_decay,
            "train_triplets": len(train_triplets),
            "val_triplets": len(val_triplets),
        },
        "baseline_metrics": baseline_metrics,
        "finetuned_metrics": finetuned_metrics,
        "improvement": {},
    }

    # Calculate improvements
    for key in finetuned_metrics:
        if key in baseline_metrics:
            diff = finetuned_metrics[key] - baseline_metrics[key]
            report["improvement"][key] = round(diff, 4)

    report_path = EVAL_REPORT
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    logger.info(f"\nEvaluation Report:")
    logger.info(f"{'Metric':<35} {'Baseline':>10} {'Fine-tuned':>10} {'Delta':>10}")
    logger.info(f"{'-'*65}")
    for key in sorted(finetuned_metrics.keys()):
        base_val = baseline_metrics.get(key, 0.0)
        ft_val = finetuned_metrics[key]
        delta = ft_val - base_val
        sign = "+" if delta >= 0 else ""
        logger.info(f"{key:<35} {base_val:>10.4f} {ft_val:>10.4f} {sign}{delta:>9.4f}")

    logger.info(f"\nReport saved to: {report_path}")
    logger.info(f"Model saved to:  {output_model_path}")
    logger.info(f"\nTo deploy, set in your .env:")
    logger.info(f"  EMBEDDING_MODEL_NAME={output_model_path}")
    logger.info(f"  EMBEDDING_DEVICE=cuda")


if __name__ == "__main__":
    main()
