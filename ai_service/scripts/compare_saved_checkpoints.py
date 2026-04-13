#!/usr/bin/env python3
"""Compare the final fine-tuned model against saved trainer checkpoints."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from loguru import logger

from ai_service.scripts._shared.paths import VAL_JSONL
from ai_service.scripts.fine_tune_bge import _evaluate_model


def _load_triplets(path: Path) -> list[dict]:
    triplets = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                triplets.append(json.loads(line))
    return triplets


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Evaluate saved SentenceTransformer checkpoints on val data."
    )
    parser.add_argument(
        "--models",
        nargs="+",
        required=True,
        help="Model/checkpoint directories to evaluate.",
    )
    parser.add_argument(
        "--val",
        type=str,
        default=str(VAL_JSONL),
        help=f"Validation JSONL path (default: {VAL_JSONL})",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=2,
        help="Evaluation batch size. Default: 2",
    )
    parser.add_argument(
        "--device",
        type=str,
        default="cuda",
        help="Device to load the model on. Default: cuda",
    )
    args = parser.parse_args()

    from sentence_transformers import SentenceTransformer  # type: ignore

    val_triplets = _load_triplets(Path(args.val))
    logger.info(f"Loaded {len(val_triplets)} validation triplets")

    results: list[dict] = []
    for model_path in args.models:
        path = Path(model_path)
        logger.info(f"Evaluating: {path}")
        model = SentenceTransformer(str(path), device=args.device)
        metrics = _evaluate_model(model, val_triplets, batch_size=args.batch_size)
        row = {"path": str(path), "metrics": metrics}
        results.append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)

    if results:
        key = "cosine_accuracy@10"
        best = max(results, key=lambda row: row["metrics"].get(key, float("-inf")))
        logger.info(
            f"Best by {key}: {best['path']} = {best['metrics'].get(key)}"
        )


if __name__ == "__main__":
    main()
