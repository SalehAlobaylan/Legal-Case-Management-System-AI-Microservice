#!/usr/bin/env python3
"""Step 2b: Sample citation extractions for manual quality review.

Reads citations.jsonl from Step 2 and outputs a human-readable CSV
for manual QA before proceeding to triplet building.

Usage:
    python -m ai_service.scripts.qa_sample
    python -m ai_service.scripts.qa_sample --n 300
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import sys
from pathlib import Path

from loguru import logger

from ai_service.scripts._shared.paths import (
    CITATIONS_JSONL,
    QA_SAMPLE_CSV,
    ensure_dirs,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Sample citation extractions for manual QA review."
    )
    parser.add_argument(
        "--input",
        type=str,
        default=str(CITATIONS_JSONL),
        help=f"Input citations JSONL (default: {CITATIONS_JSONL})",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=str(QA_SAMPLE_CSV),
        help=f"Output CSV path (default: {QA_SAMPLE_CSV})",
    )
    parser.add_argument(
        "--n",
        type=int,
        default=200,
        help="Number of samples. Default: 200",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Random seed for reproducibility. Default: 42",
    )
    args = parser.parse_args()

    ensure_dirs()
    input_path = Path(args.input)
    output_path = Path(args.output)

    if not input_path.exists():
        logger.error(f"Input file not found: {input_path}")
        sys.exit(1)

    # Collect all cases that have at least 1 citation
    candidates: list[dict] = []

    with open(input_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue

            citations = record.get("citations", [])
            if not citations:
                continue

            # Create one row per citation for review
            for cit in citations:
                candidates.append({
                    "case_id": record.get("case_id", ""),
                    "court_name": record.get("court_name", ""),
                    "case_snippet": record.get("case_text", "")[:500],
                    "article_ref": cit.get("article_ref", ""),
                    "article_number": cit.get("article_number", ""),
                    "regulation_name_raw": cit.get("regulation_name_raw", ""),
                    "regulation_db_id": cit.get("regulation_db_id", ""),
                    "regulation_db_title": cit.get("regulation_db_title", ""),
                    "confidence": cit.get("confidence", ""),
                    "match_score": cit.get("match_score", ""),
                })

    logger.info(f"Found {len(candidates)} total citation instances from cases with citations")

    # Sample
    random.seed(args.seed)
    n = min(args.n, len(candidates))
    samples = random.sample(candidates, n)

    # Write CSV
    fieldnames = [
        "case_id",
        "court_name",
        "case_snippet",
        "article_ref",
        "article_number",
        "regulation_name_raw",
        "regulation_db_id",
        "regulation_db_title",
        "confidence",
        "match_score",
    ]

    with open(output_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(samples)

    # Also write JSONL version for programmatic access
    jsonl_path = output_path.with_suffix(".jsonl")
    with open(jsonl_path, "w", encoding="utf-8") as f:
        for row in samples:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    logger.info(f"QA sample written:")
    logger.info(f"  CSV:   {output_path} ({n} rows)")
    logger.info(f"  JSONL: {jsonl_path} ({n} rows)")
    logger.info(f"")
    logger.info(f"Please review the CSV to verify citation quality before")
    logger.info(f"proceeding to Step 3 (build_training_triplets.py).")


if __name__ == "__main__":
    main()
