#!/usr/bin/env python3
"""Step 3: Build training triplets (query, positive, negative) for BGE fine-tuning.

Reads citations.jsonl from Step 2, resolves cited regulation chunks from the DB,
generates hard negatives using the current BGE-M3 model, and writes train/val
JSONL files ready for fine-tuning.

Usage:
    python -m ai_service.scripts.build_training_triplets

    # Custom settings
    python -m ai_service.scripts.build_training_triplets \
        --hard-neg-k 15 --max-hard-neg 3 --max-random-neg 2
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import sys
from collections import defaultdict
from pathlib import Path
from typing import List, Optional, Sequence

from loguru import logger
from tqdm import tqdm

from ai_service.scripts._shared.arabic_utils import normalize_arabic
from ai_service.scripts._shared.db_client import (
    get_connection,
    load_all_chunk_embeddings,
    load_chunks_by_regulation,
    load_first_chunk_for_regulation,
    load_regulation_chunks_by_article,
)
from ai_service.scripts._shared.paths import (
    CITATIONS_JSONL,
    TRAIN_JSONL,
    TRIPLETS_DIR,
    TRIPLETS_STATS,
    VAL_JSONL,
    ensure_dirs,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Section markers that often precede the case facts
_FACTS_MARKERS = [
    "الوقائع",
    "ملخص القضية",
    "موضوع الدعوى",
    "تتلخص وقائع",
    "تتحصل وقائع",
]


def _extract_facts_section(text: str, max_chars: int = 1500) -> str:
    """Try to extract the facts/summary section from judgment text.

    Searches the original text (not normalized) for facts markers so the
    returned substring uses correct original-text indices. Falls back to
    the first max_chars characters.
    """
    best_start = -1

    for marker in _FACTS_MARKERS:
        # Search in original text first
        idx = text.find(marker)
        if idx == -1:
            # Try normalized marker in normalized text, then map back
            # by searching a small normalized window
            norm_marker = normalize_arabic(marker)
            norm_text = normalize_arabic(text)
            nidx = norm_text.find(norm_marker)
            if nidx != -1:
                # Approximate: search for the marker neighborhood in original
                # Use a window around the approximate position
                search_start = max(0, nidx - 20)
                search_end = min(len(text), nidx + len(marker) + 20)
                local_idx = text.find(marker, search_start, search_end)
                if local_idx != -1:
                    idx = local_idx

        if idx != -1 and (best_start == -1 or idx < best_start):
            best_start = idx

    if best_start >= 0:
        return text[best_start : best_start + max_chars].strip()
    return text[:max_chars].strip()


def _cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    """Compute cosine similarity between two vectors."""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a)) or 1.0
    norm_b = math.sqrt(sum(x * x for x in b)) or 1.0
    return dot / (norm_a * norm_b)


def _case_id_to_split(case_id: str, val_ratio: float = 0.2) -> str:
    """Deterministic hash-based train/val split by case_id."""
    h = hashlib.md5(case_id.encode("utf-8")).hexdigest()
    # Use first 8 hex chars as a fraction
    fraction = int(h[:8], 16) / 0xFFFFFFFF
    return "val" if fraction < val_ratio else "train"


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build training triplets for BGE-M3 fine-tuning."
    )
    parser.add_argument(
        "--citations",
        type=str,
        default=str(CITATIONS_JSONL),
        help=f"Input citations JSONL (default: {CITATIONS_JSONL})",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default=str(TRIPLETS_DIR),
        help=f"Output directory (default: {TRIPLETS_DIR})",
    )
    parser.add_argument(
        "--hard-neg-k",
        type=int,
        default=10,
        help="Top-K chunks to consider for hard negatives. Default: 10",
    )
    parser.add_argument(
        "--max-hard-neg",
        type=int,
        default=3,
        help="Max hard negatives per positive. Default: 3",
    )
    parser.add_argument(
        "--max-random-neg",
        type=int,
        default=2,
        help="Max random negatives per positive. Default: 2",
    )
    parser.add_argument(
        "--val-ratio",
        type=float,
        default=0.2,
        help="Fraction of cases for validation. Default: 0.2",
    )
    parser.add_argument(
        "--max-query-chars",
        type=int,
        default=1500,
        help="Max characters for query text. Default: 1500",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Random seed. Default: 42",
    )
    parser.add_argument(
        "--embedding-model",
        type=str,
        default="BAAI/bge-m3",
        help="SentenceTransformer model used for query embeddings. Default: BAAI/bge-m3",
    )
    parser.add_argument(
        "--device",
        type=str,
        default="auto",
        help="Embedding device for hard negative mining. auto, cuda, or cpu. Default: auto",
    )
    args = parser.parse_args()

    random.seed(args.seed)
    ensure_dirs()

    citations_path = Path(args.citations)
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    train_path = out_dir / "train.jsonl"
    val_path = out_dir / "val.jsonl"
    stats_path = out_dir / "stats.json"

    if not citations_path.exists():
        logger.error(f"Citations file not found: {citations_path}")
        sys.exit(1)

    # ----- Phase A: Load all chunk embeddings for hard negative mining -----
    logger.info("Phase A: Loading chunk embeddings from DB for hard negatives...")
    conn = get_connection()

    all_chunks = load_all_chunk_embeddings(conn)
    if not all_chunks:
        logger.error("No chunk embeddings found in DB. Cannot generate negatives.")
        conn.close()
        sys.exit(1)

    # Index chunks by regulation_id for quick lookup
    chunks_by_reg: dict[int, list[dict]] = defaultdict(list)
    chunks_by_category: dict[str, list[dict]] = defaultdict(list)
    for chunk in all_chunks:
        chunks_by_reg[chunk["regulation_id"]].append(chunk)
        if chunk.get("category"):
            chunks_by_category[chunk["category"]].append(chunk)

    logger.info(
        f"  {len(all_chunks)} chunks across "
        f"{len(chunks_by_reg)} regulations, "
        f"{len(chunks_by_category)} categories"
    )

    # ----- Phase B: Load BGE-M3 for query embedding -----
    logger.info("Phase B: Loading embedding model for query embedding...")
    from sentence_transformers import SentenceTransformer  # type: ignore

    candidate_devices = [args.device] if args.device != "auto" else ["cuda", "cpu"]
    model = None
    for device_name in candidate_devices:
        try:
            model = SentenceTransformer(args.embedding_model, device=device_name)
            logger.info(f"  {args.embedding_model} loaded on {device_name.upper()}")
            break
        except Exception:
            continue

    if model is None:
        logger.error(
            f"Failed to load embedding model {args.embedding_model} "
            f"on devices {candidate_devices}"
        )
        conn.close()
        sys.exit(1)

    # ----- Phase C: Process citations and build triplets -----
    logger.info("Phase C: Building triplets from citations...")

    train_triplets: list[dict] = []
    val_triplets: list[dict] = []
    stats = {
        "total_cases": 0,
        "cases_with_citations": 0,
        "cases_with_resolved_citations": 0,
        "total_positives_found": 0,
        "total_triplets_train": 0,
        "total_triplets_val": 0,
        "skipped_no_positive": 0,
        "skipped_no_text": 0,
        "embedding_model": args.embedding_model,
        "embedding_device": args.device,
    }

    with open(citations_path, "r", encoding="utf-8") as f:
        lines = [l.strip() for l in f if l.strip()]

    logger.info(f"  Processing {len(lines)} cases...")

    for line in tqdm(lines, desc="Building triplets"):
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue

        stats["total_cases"] += 1
        case_id = record.get("case_id", "")
        case_text = record.get("case_text", "")
        citations = record.get("citations", [])

        if not case_text or len(case_text) < 50:
            stats["skipped_no_text"] += 1
            continue

        if not citations:
            continue

        stats["cases_with_citations"] += 1

        # Filter to resolved citations only
        resolved = [c for c in citations if c.get("regulation_db_id") is not None]
        if not resolved:
            continue

        stats["cases_with_resolved_citations"] += 1

        # Extract query text
        query_text = _extract_facts_section(case_text, args.max_query_chars)
        if len(query_text) < 30:
            stats["skipped_no_text"] += 1
            continue

        # Embed query
        query_embedding = model.encode(query_text, convert_to_numpy=True).tolist()

        # Determine train/val split
        split = _case_id_to_split(case_id, args.val_ratio)

        # Collect all positive regulation IDs for this case (for negative filtering)
        positive_reg_ids = {c["regulation_db_id"] for c in resolved}

        for citation in resolved:
            reg_id = citation["regulation_db_id"]
            article_ref = citation.get("article_ref", "")
            article_number = citation.get("article_number")

            # --- Find positive chunk ---
            positive_text: Optional[str] = None

            # Try matching by article_ref
            if article_number:
                matching_chunks = load_regulation_chunks_by_article(
                    reg_id, str(article_number), conn
                )
                if matching_chunks:
                    positive_text = matching_chunks[0]["content"]

            # Fallback: first chunk of that regulation
            if not positive_text:
                first_chunk = load_first_chunk_for_regulation(reg_id, conn)
                if first_chunk:
                    positive_text = first_chunk["content"]

            if not positive_text or len(positive_text) < 20:
                stats["skipped_no_positive"] += 1
                continue

            stats["total_positives_found"] += 1

            # --- Generate hard negatives ---
            hard_negatives: list[str] = []

            # Score all chunks against the query
            scored_chunks: list[tuple[float, dict]] = []
            for chunk in all_chunks:
                if chunk["regulation_id"] in positive_reg_ids:
                    continue  # Skip true positives
                if not chunk.get("embedding"):
                    continue
                score = _cosine_similarity(query_embedding, chunk["embedding"])
                scored_chunks.append((score, chunk))

            # Sort by descending similarity
            scored_chunks.sort(key=lambda x: x[0], reverse=True)

            # Take top-K as hard negatives
            for score, chunk in scored_chunks[: args.hard_neg_k]:
                if len(hard_negatives) >= args.max_hard_neg:
                    break
                neg_text = chunk["content"]
                if neg_text and len(neg_text) >= 20:
                    hard_negatives.append(neg_text)

            # --- Generate random negatives (same category preferred) ---
            random_negatives: list[str] = []

            # Map court_type integer to regulation category
            _COURT_TO_CATEGORY: dict[int | str, str] = {
                1: "commercial_law",
                2: "labor_law",
                3: "criminal_law",
                4: "civil_law",
                5: "procedural_law",
                "1": "commercial_law",
                "2": "labor_law",
                "3": "criminal_law",
                "4": "civil_law",
                "5": "procedural_law",
            }
            case_court_type = record.get("court_type", "")
            matched_category = _COURT_TO_CATEGORY.get(case_court_type, "")

            # Prefer chunks from the same category
            category_chunks = []
            if matched_category and matched_category in chunks_by_category:
                for c in chunks_by_category[matched_category]:
                    if c["regulation_id"] not in positive_reg_ids:
                        category_chunks.append(c)

            # Fallback: chunks from any category
            if not category_chunks:
                for cat, cat_chunks in chunks_by_category.items():
                    if cat_chunks and cat:
                        for c in cat_chunks:
                            if c["regulation_id"] not in positive_reg_ids:
                                category_chunks.append(c)

            if category_chunks:
                random.shuffle(category_chunks)
                for chunk in category_chunks[: args.max_random_neg]:
                    neg_text = chunk["content"]
                    if neg_text and len(neg_text) >= 20:
                        random_negatives.append(neg_text)

            # --- Write triplets ---
            all_negatives = hard_negatives + random_negatives
            if not all_negatives:
                # Use any random chunk as fallback (with max attempts to avoid infinite loop)
                for _ in range(100):
                    fallback = random.choice(all_chunks)
                    if fallback["regulation_id"] not in positive_reg_ids:
                        all_negatives = [fallback["content"]]
                        break
                else:
                    # Extremely unlikely: all chunks are positives. Skip this triplet.
                    stats["skipped_no_positive"] += 1
                    continue

            for negative_text in all_negatives:
                triplet = {
                    "query": query_text,
                    "positive": positive_text,
                    "negative": negative_text,
                }
                if split == "val":
                    val_triplets.append(triplet)
                else:
                    train_triplets.append(triplet)

    conn.close()

    # ----- Phase D: Write output -----
    logger.info("Phase D: Writing output files...")

    # Shuffle
    random.shuffle(train_triplets)
    random.shuffle(val_triplets)

    with open(train_path, "w", encoding="utf-8") as f:
        for t in train_triplets:
            f.write(json.dumps(t, ensure_ascii=False) + "\n")

    with open(val_path, "w", encoding="utf-8") as f:
        for t in val_triplets:
            f.write(json.dumps(t, ensure_ascii=False) + "\n")

    stats["total_triplets_train"] = len(train_triplets)
    stats["total_triplets_val"] = len(val_triplets)

    with open(stats_path, "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)

    logger.info(f"Triplet building complete!")
    logger.info(f"  Cases processed:           {stats['total_cases']}")
    logger.info(f"  Cases with citations:       {stats['cases_with_citations']}")
    logger.info(f"  Cases with resolved cites:  {stats['cases_with_resolved_citations']}")
    logger.info(f"  Positives found:            {stats['total_positives_found']}")
    logger.info(f"  Train triplets:             {stats['total_triplets_train']}")
    logger.info(f"  Val triplets:               {stats['total_triplets_val']}")
    logger.info(f"  Skipped (no positive):      {stats['skipped_no_positive']}")
    logger.info(f"  Skipped (no text):          {stats['skipped_no_text']}")
    logger.info(f"  Output: {train_path}, {val_path}")
    logger.info(f"  Stats:  {stats_path}")

    if stats["total_triplets_train"] < 500:
        logger.warning(
            f"Only {stats['total_triplets_train']} training triplets generated. "
            f"Fine-tuning may not yield significant improvement. "
            f"Consider scraping more judgments or relaxing the matching threshold."
        )


if __name__ == "__main__":
    main()
