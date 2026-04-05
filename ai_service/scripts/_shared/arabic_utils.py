"""Arabic text normalization and number word conversion utilities."""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher
from typing import List, Optional, Tuple

# ---------------------------------------------------------------------------
# Arabic text normalization
# ---------------------------------------------------------------------------

# Alef variants → bare alef
_ALEF_VARIANTS = re.compile("[\u0622\u0623\u0625]")  # آ أ إ → ا

# Tashkeel / diacritics
_TASHKEEL = re.compile("[\u064B-\u065F\u0670]")

# Tatweel (kashida)
_TATWEEL = "\u0640"


def normalize_arabic(text: str) -> str:
    """Normalize Arabic text for consistent matching.

    * Unify alef variants (آ أ إ → ا)
    * Strip tashkeel / diacritics
    * Remove tatweel (kashida)
    * Collapse whitespace
    """
    text = _ALEF_VARIANTS.sub("\u0627", text)
    text = _TASHKEEL.sub("", text)
    text = text.replace(_TATWEEL, "")
    text = re.sub(r"\s+", " ", text).strip()
    return text


# ---------------------------------------------------------------------------
# Arabic number word → integer conversion
# ---------------------------------------------------------------------------

# Feminine ordinals (most common in legal citations: المادة الخامسة)
_ORDINALS: dict[str, int] = {
    "الاولى": 1, "الاول": 1,
    "الثانية": 2, "الثاني": 2,
    "الثالثة": 3, "الثالث": 3,
    "الرابعة": 4, "الرابع": 4,
    "الخامسة": 5, "الخامس": 5,
    "السادسة": 6, "السادس": 6,
    "السابعة": 7, "السابع": 7,
    "الثامنة": 8, "الثامن": 8,
    "التاسعة": 9, "التاسع": 9,
    "العاشرة": 10, "العاشر": 10,
    "الحادية عشرة": 11, "الحادي عشر": 11,
    "الثانية عشرة": 12, "الثاني عشر": 12,
    "الثالثة عشرة": 13, "الثالث عشر": 13,
    "الرابعة عشرة": 14, "الرابع عشر": 14,
    "الخامسة عشرة": 15, "الخامس عشر": 15,
    "السادسة عشرة": 16, "السادس عشر": 16,
    "السابعة عشرة": 17, "السابع عشر": 17,
    "الثامنة عشرة": 18, "الثامن عشر": 18,
    "التاسعة عشرة": 19, "التاسع عشر": 19,
}

# Tens
_TENS: dict[str, int] = {
    "العشرون": 20, "العشرين": 20,
    "الثلاثون": 30, "الثلاثين": 30,
    "الاربعون": 40, "الاربعين": 40,
    "الخمسون": 50, "الخمسين": 50,
    "الستون": 60, "الستين": 60,
    "السبعون": 70, "السبعين": 70,
    "الثمانون": 80, "الثمانين": 80,
    "التسعون": 90, "التسعين": 90,
    "المائة": 100, "المئة": 100,
}

# Ones (without the "ال" prefix, used in compound numbers)
_ONES_BARE: dict[str, int] = {
    "واحدة": 1, "واحد": 1,
    "اثنتين": 2, "اثنين": 2, "اثنتان": 2, "اثنان": 2,
    "ثلاث": 3, "ثلاثة": 3,
    "اربع": 4, "اربعة": 4,
    "خمس": 5, "خمسة": 5,
    "ست": 6, "ستة": 6,
    "سبع": 7, "سبعة": 7,
    "ثمان": 8, "ثمانية": 8, "ثماني": 8,
    "تسع": 9, "تسعة": 9,
}

# Combined lookup (all ordinals + tens, after normalization)
_ALL_WORDS: dict[str, int] = {}
_ALL_WORDS.update(_ORDINALS)
_ALL_WORDS.update(_TENS)


def arabic_number_to_int(word: str) -> Optional[int]:
    """Convert an Arabic number word/phrase to an integer.

    Handles:
    * Simple ordinals: الخامسة → 5
    * Tens: السبعون → 70
    * Compound: الخامسة والسبعون → 75
    * Plain digits: ٧٥ or 75 → 75
    * Mixed: المادة 75 → 75
    """
    word = normalize_arabic(word.strip())

    # Try plain Arabic-Indic or Western digits
    digits = re.sub(r"[^\d٠-٩]", "", word)
    if digits:
        # Convert Arabic-Indic digits to Western
        western = digits.translate(
            str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")
        )
        try:
            return int(western)
        except ValueError:
            pass

    # Direct lookup
    if word in _ALL_WORDS:
        return _ALL_WORDS[word]

    # Compound: "X و Y" pattern (e.g., الخامسة والسبعون = 5 + 70)
    if " و" in word:
        parts = re.split(r"\s+و", word)
        total = 0
        for part in parts:
            part = part.strip()
            if part in _ALL_WORDS:
                total += _ALL_WORDS[part]
            else:
                # Try bare ones (without ال)
                bare = re.sub(r"^ال", "", part)
                if bare in _ONES_BARE:
                    total += _ONES_BARE[bare]
                else:
                    return None
        return total if total > 0 else None

    return None


# ---------------------------------------------------------------------------
# Regulation name normalization and fuzzy matching
# ---------------------------------------------------------------------------

_REG_PREFIXES = re.compile(
    r"^(نظام|لائحة|قانون|مرسوم|قرار)\s+", flags=re.UNICODE
)


def normalize_regulation_name(name: str) -> str:
    """Normalize a regulation name for matching.

    Strips common prefixes (نظام, لائحة, etc.), normalizes Arabic,
    and lowercases for comparison.
    """
    name = normalize_arabic(name.strip())
    name = _REG_PREFIXES.sub("", name)
    return name.strip()


def _tokenize(text: str) -> set[str]:
    """Split Arabic text into word-level tokens."""
    return {w for w in re.split(r"\s+", text) if len(w) > 1}


def fuzzy_match_regulation(
    extracted_name: str,
    db_regulations: List[dict],
    threshold: float = 0.6,
) -> Optional[Tuple[int, str, float]]:
    """Match an extracted regulation name against DB regulation titles.

    Args:
        extracted_name: Raw regulation name from citation extraction.
        db_regulations: List of dicts with keys: id, title, category.
        threshold: Minimum similarity score to accept a match.

    Returns:
        (regulation_id, title, score) or None if no match above threshold.
    """
    norm_extracted = normalize_regulation_name(extracted_name)
    extracted_tokens = _tokenize(norm_extracted)

    best: Optional[Tuple[int, str, float]] = None

    for reg in db_regulations:
        norm_title = normalize_regulation_name(reg["title"])
        title_tokens = _tokenize(norm_title)

        # Token overlap (Jaccard-like)
        if not extracted_tokens or not title_tokens:
            continue
        overlap = len(extracted_tokens & title_tokens)
        union = len(extracted_tokens | title_tokens)
        token_score = overlap / union if union else 0.0

        # Sequence ratio (handles partial names better)
        seq_score = SequenceMatcher(None, norm_extracted, norm_title).ratio()

        # Weighted combination
        score = 0.4 * token_score + 0.6 * seq_score

        if score >= threshold and (best is None or score > best[2]):
            best = (reg["id"], reg["title"], score)

    return best
