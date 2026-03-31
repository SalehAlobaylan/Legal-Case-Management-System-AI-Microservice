#!/usr/bin/env python3
"""Step 2: Extract regulation citations from scraped judgment texts.

Reads judgments.jsonl from Step 1, applies regex patterns to find citations
like "المادة X من نظام Y", resolves regulation names against the DB,
and writes citations.jsonl.

Usage:
    python -m ai_service.scripts.extract_citations

    # With pre-cached regulation titles (skip DB call)
    python -m ai_service.scripts.extract_citations --regulations-cache regs.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any, List, Optional

from loguru import logger

from ai_service.scripts._shared.arabic_utils import (
    arabic_number_to_int,
    fuzzy_match_regulation,
    normalize_arabic,
    normalize_regulation_name,
)
from ai_service.scripts._shared.paths import (
    CITATIONS_JSONL,
    CITATIONS_STATS,
    JUDGMENTS_JSONL,
    ensure_dirs,
)

# ---------------------------------------------------------------------------
# Citation regex patterns
# ---------------------------------------------------------------------------

# Common prepositions before المادة
_PREPOSITIONS = (
    r"(?:وفقا\s+ل|وفقاً\s+لـ?|بموجب|استنادا\s+الى|استناداً\s+إلى|"
    r"تطبيقا\s+ل|تطبيقاً\s+لـ?|حسب|طبقا\s+ل|طبقاً\s+لـ?|"
    r"بناء\s+على|بناءً\s+على|اعمالا\s+ل|إعمالاً\s+لـ?|"
    r"نص\s+|نصت\s+|الواردة\s+في\s+|المنصوص\s+عليها?\s+في\s+)?"
)

# Article reference: digits, Arabic-Indic digits, or Arabic word(s)
_ARTICLE_REF = (
    r"(?:ال[\u0600-\u06FF]+(?:\s+و(?:ال)?[\u0600-\u06FF]+)*"  # Arabic words
    r"|\d+(?:\s*/\s*\d+)?"  # Western digits (75, 75/1)
    r"|[٠-٩]+(?:\s*/\s*[٠-٩]+)?)"  # Arabic-Indic digits
)

# Full pattern: المادة [ref] من (نظام|لائحة) [name]
_PATTERN_FULL = re.compile(
    _PREPOSITIONS
    + r"المادة\s+"
    + r"(" + _ARTICLE_REF + r")"
    + r"\s+من\s+"
    + r"(نظام|لائحة|قانون|مرسوم)"
    + r"\s+"
    + r"([\u0600-\u06FF\s]{3,60}?)"
    + r"(?=[،.؛:\s\)\(]|$)",
    re.UNICODE,
)

# Numbered-only pattern: المادة [digits] من (نظام|لائحة) [name]
_PATTERN_NUMBERED = re.compile(
    r"المادة\s+"
    r"(\d+(?:\s*/\s*\d+)?|[٠-٩]+(?:\s*/\s*[٠-٩]+)?)"
    r"\s+من\s+"
    r"(نظام|لائحة|قانون)"
    r"\s+"
    r"([\u0600-\u06FF\s]{3,60}?)"
    r"(?=[،.؛:\s\)\(]|$)",
    re.UNICODE,
)

# Bare article reference (lower confidence, no regulation name)
_PATTERN_BARE = re.compile(
    r"المادة\s+"
    r"(\d+(?:\s*/\s*\d+)?|[٠-٩]+(?:\s*/\s*[٠-٩]+)?)"
    r"(?=[\s،.؛:\)\(]|$)",
    re.UNICODE,
)


# ---------------------------------------------------------------------------
# Extraction logic
# ---------------------------------------------------------------------------


def _clean_regulation_name(raw: str) -> str:
    """Clean trailing junk from the extracted regulation name."""
    name = raw.strip()
    # Remove trailing punctuation and common trailing words
    name = re.sub(r"[\s،.؛:]+$", "", name)
    # Remove trailing conjunctions
    name = re.sub(r"\s+(و|أو|في|على|من|إلى)$", "", name)
    return name.strip()


def extract_citations_from_text(
    text: str,
    db_regulations: List[dict],
) -> List[dict]:
    """Extract all regulation citations from a judgment text.

    Returns a list of citation dicts, each with:
    - article_ref: str (e.g., "المادة 75")
    - article_number: int or None
    - regulation_name_raw: str
    - regulation_type: str (نظام, لائحة, etc.)
    - regulation_db_id: int or None
    - regulation_db_title: str or None
    - confidence: str ("high", "medium", "low")
    - match_score: float
    """
    citations: List[dict] = []
    seen_keys: set[str] = set()  # Deduplicate by (article_ref, regulation_name)

    normalized_text = normalize_arabic(text)

    # --- Pattern 1: Full citation with regulation name ---
    for m in _PATTERN_FULL.finditer(normalized_text):
        article_raw = m.group(1).strip()
        reg_type = m.group(2).strip()
        reg_name_raw = _clean_regulation_name(m.group(3))

        article_number = arabic_number_to_int(article_raw)
        article_ref_str = f"المادة {article_number}" if article_number else f"المادة {article_raw}"

        dedup_key = f"{article_ref_str}|{normalize_regulation_name(reg_name_raw)}"
        if dedup_key in seen_keys:
            continue
        seen_keys.add(dedup_key)

        # Match against DB
        full_name = f"{reg_type} {reg_name_raw}"
        match = fuzzy_match_regulation(full_name, db_regulations, threshold=0.5)

        citations.append({
            "article_ref": article_ref_str,
            "article_number": article_number,
            "regulation_name_raw": full_name,
            "regulation_type": reg_type,
            "regulation_db_id": match[0] if match else None,
            "regulation_db_title": match[1] if match else None,
            "confidence": "high" if match and match[2] >= 0.7 else "medium",
            "match_score": round(match[2], 3) if match else 0.0,
        })

    # --- Pattern 2: Numbered-only fallback ---
    for m in _PATTERN_NUMBERED.finditer(normalized_text):
        article_raw = m.group(1).strip()
        reg_type = m.group(2).strip()
        reg_name_raw = _clean_regulation_name(m.group(3))

        article_number = arabic_number_to_int(article_raw)
        article_ref_str = f"المادة {article_number}" if article_number else f"المادة {article_raw}"

        dedup_key = f"{article_ref_str}|{normalize_regulation_name(reg_name_raw)}"
        if dedup_key in seen_keys:
            continue
        seen_keys.add(dedup_key)

        full_name = f"{reg_type} {reg_name_raw}"
        match = fuzzy_match_regulation(full_name, db_regulations, threshold=0.5)

        citations.append({
            "article_ref": article_ref_str,
            "article_number": article_number,
            "regulation_name_raw": full_name,
            "regulation_type": reg_type,
            "regulation_db_id": match[0] if match else None,
            "regulation_db_title": match[1] if match else None,
            "confidence": "medium" if match else "low",
            "match_score": round(match[2], 3) if match else 0.0,
        })

    # --- Pattern 3: Bare article reference (no regulation name, low confidence) ---
    for m in _PATTERN_BARE.finditer(normalized_text):
        article_raw = m.group(1).strip()
        article_number = arabic_number_to_int(article_raw)
        article_ref_str = f"المادة {article_number}" if article_number else f"المادة {article_raw}"

        # Only add if we haven't already found this article via full/numbered patterns
        dedup_key = f"{article_ref_str}|_bare_"
        if dedup_key in seen_keys:
            continue
        # Also skip if any existing citation already has this article_ref
        if any(c["article_ref"] == article_ref_str for c in citations):
            continue
        seen_keys.add(dedup_key)

        citations.append({
            "article_ref": article_ref_str,
            "article_number": article_number,
            "regulation_name_raw": "",
            "regulation_type": "",
            "regulation_db_id": None,
            "regulation_db_title": None,
            "confidence": "low",
            "match_score": 0.0,
        })

    return citations


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extract regulation citations from judgment texts."
    )
    parser.add_argument(
        "--input",
        type=str,
        default=str(JUDGMENTS_JSONL),
        help=f"Input JSONL from scraper (default: {JUDGMENTS_JSONL})",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=str(CITATIONS_JSONL),
        help=f"Output JSONL path (default: {CITATIONS_JSONL})",
    )
    parser.add_argument(
        "--regulations-cache",
        type=str,
        default=None,
        help="Path to a JSON file with pre-fetched regulation titles (skip DB)",
    )
    args = parser.parse_args()

    ensure_dirs()
    input_path = Path(args.input)
    output_path = Path(args.output)

    if not input_path.exists():
        logger.error(f"Input file not found: {input_path}")
        sys.exit(1)

    # Load regulation titles
    if args.regulations_cache:
        cache_path = Path(args.regulations_cache)
        logger.info(f"Loading regulations from cache: {cache_path}")
        with open(cache_path, "r", encoding="utf-8") as f:
            db_regulations = json.load(f)
    else:
        logger.info("Loading regulations from database...")
        from ai_service.scripts._shared.db_client import load_regulations
        db_regulations = load_regulations()

    logger.info(f"Loaded {len(db_regulations)} regulations for matching")

    # Process judgments
    total_cases = 0
    cases_with_citations = 0
    total_citations = 0
    confidence_counts: Counter = Counter()
    resolved_count = 0

    with open(input_path, "r", encoding="utf-8") as in_f, \
         open(output_path, "w", encoding="utf-8") as out_f:

        for line_num, line in enumerate(in_f, 1):
            line = line.strip()
            if not line:
                continue

            try:
                judgment = json.loads(line)
            except json.JSONDecodeError:
                logger.warning(f"Line {line_num}: invalid JSON, skipping")
                continue

            total_cases += 1
            plain_text = judgment.get("plain_text", "")
            if not plain_text or len(plain_text) < 50:
                # Write empty citations
                out_f.write(json.dumps({
                    "case_id": judgment.get("id", ""),
                    "case_text": plain_text,
                    "court_type": judgment.get("courtType", ""),
                    "court_name": judgment.get("courtName", ""),
                    "hijri_year": judgment.get("hijriYear", ""),
                    "citations": [],
                }, ensure_ascii=False) + "\n")
                continue

            citations = extract_citations_from_text(plain_text, db_regulations)

            if citations:
                cases_with_citations += 1
                total_citations += len(citations)
                for c in citations:
                    confidence_counts[c["confidence"]] += 1
                    if c["regulation_db_id"] is not None:
                        resolved_count += 1

            out_f.write(json.dumps({
                "case_id": judgment.get("id", ""),
                "case_text": plain_text,
                "court_type": judgment.get("courtType", ""),
                "court_name": judgment.get("courtName", ""),
                "hijri_year": judgment.get("hijriYear", ""),
                "citations": citations,
            }, ensure_ascii=False) + "\n")

            if total_cases % 1000 == 0:
                logger.info(
                    f"Processed {total_cases} cases, "
                    f"{cases_with_citations} with citations, "
                    f"{total_citations} total citations"
                )

    # Write stats
    stats = {
        "total_cases": total_cases,
        "cases_with_citations": cases_with_citations,
        "citation_rate": round(cases_with_citations / max(1, total_cases), 3),
        "total_citations": total_citations,
        "resolved_to_db": resolved_count,
        "resolution_rate": round(resolved_count / max(1, total_citations), 3),
        "confidence_distribution": dict(confidence_counts),
        "avg_citations_per_case_with_citations": round(
            total_citations / max(1, cases_with_citations), 2
        ),
    }

    stats_path = Path(str(output_path).replace(".jsonl", "_stats.json"))
    if str(stats_path) == str(output_path):
        stats_path = CITATIONS_STATS

    with open(stats_path, "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)

    logger.info(f"Citation extraction complete!")
    logger.info(f"  Cases processed:      {total_cases}")
    logger.info(f"  Cases with citations:  {cases_with_citations} ({stats['citation_rate']:.1%})")
    logger.info(f"  Total citations:       {total_citations}")
    logger.info(f"  Resolved to DB:        {resolved_count} ({stats['resolution_rate']:.1%})")
    logger.info(f"  Confidence: {dict(confidence_counts)}")
    logger.info(f"  Output: {output_path}")
    logger.info(f"  Stats:  {stats_path}")


if __name__ == "__main__":
    main()
