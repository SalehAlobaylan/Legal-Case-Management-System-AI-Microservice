"""
HyDE (Hypothetical Document Embeddings) for regulation matching (Phase 2).

Bridges the vocabulary gap between how lawyers describe cases and how
regulations are written.  Before embedding the case text, asks Gemini to
generate a hypothetical regulation article that would govern this case,
then embeds *that* text alongside the original case text.

Feature-flagged via settings.hyde_enabled.  Requires Gemini to be configured
(reuses the same API key / model).  Falls back gracefully — if generation
fails the pipeline continues with the original case text only.

Pipeline position:
    HyDE generation (optional) -> embedding -> composite scoring -> ...
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from app.config import settings
from app.utils.logger import logger

try:
    import google.generativeai as genai  # type: ignore[import-untyped]
except ImportError:
    genai = None  # type: ignore[assignment]


_HYDE_PROMPT_AR = """أنت خبير قانوني سعودي. بناءً على وصف القضية التالي، اكتب مادة نظامية افتراضية (من نظام سعودي) تحكم هذه القضية مباشرة.

نص القضية:
{case_text}

اكتب مادة نظامية واحدة فقط بأسلوب الأنظمة السعودية الرسمية. لا تذكر رقم المادة. اكتب النص فقط بدون أي مقدمة."""

_HYDE_PROMPT_EN = """You are a Saudi legal expert. Based on the following case description, write a hypothetical regulation article (from a Saudi law) that would directly govern this case.

Case text:
{case_text}

Write exactly one regulation article in formal Saudi legal style. Do not include an article number. Write only the regulation text with no preamble."""


def _detect_arabic(text: str) -> bool:
    """Heuristic: if >30% of alpha chars are Arabic, treat as Arabic."""
    arabic_count = sum(1 for c in text if "\u0600" <= c <= "\u06FF")
    alpha_count = sum(1 for c in text if c.isalpha()) or 1
    return arabic_count / alpha_count > 0.3


async def generate_hypothetical_regulation(
    case_text: str,
) -> tuple[str | None, list[str]]:
    """
    Generate a hypothetical regulation article for the given case text.

    Returns:
        (hypothetical_text, warnings)
        - hypothetical_text: Generated regulation text, or None on failure
        - warnings: list of pipeline warnings

    On any failure, returns None so the caller continues with original text.
    """
    warnings: list[str] = []

    if not settings.hyde_enabled:
        return None, ["hyde_disabled"]

    if not settings.gemini_api_key:
        logger.warning("HyDE enabled but no Gemini API key configured")
        return None, ["hyde_no_api_key"]

    if genai is None:
        logger.warning("google-generativeai package not installed for HyDE")
        return None, ["hyde_package_missing"]

    # Truncate case text to safe limit
    max_chars = settings.hyde_max_query_chars
    trimmed_case = case_text[:max_chars]

    # Pick prompt based on language
    is_arabic = _detect_arabic(trimmed_case)
    prompt_template = _HYDE_PROMPT_AR if is_arabic else _HYDE_PROMPT_EN
    prompt = prompt_template.format(case_text=trimmed_case)

    try:
        genai.configure(api_key=settings.gemini_api_key)
        model = genai.GenerativeModel(settings.gemini_model)

        response = await asyncio.wait_for(
            asyncio.to_thread(
                model.generate_content,
                prompt,
                generation_config=genai.GenerationConfig(
                    temperature=0.3,
                    max_output_tokens=1024,
                ),
            ),
            timeout=settings.gemini_timeout_seconds,
        )

        raw_text = (response.text or "").strip()
        if not raw_text or len(raw_text) < 20:
            logger.warning("HyDE: Gemini returned empty or too-short response")
            return None, ["hyde_empty_response"]

        # Sanity cap: don't let HyDE text be longer than case text
        if len(raw_text) > max_chars:
            raw_text = raw_text[:max_chars]

        logger.info(
            "hyde:generated",
            extra={
                "hyde_text_len": len(raw_text),
                "is_arabic": is_arabic,
            },
        )
        return raw_text, warnings

    except asyncio.TimeoutError:
        logger.warning(
            f"HyDE generation timed out after {settings.gemini_timeout_seconds}s"
        )
        return None, ["hyde_timeout"]
    except Exception as exc:
        logger.warning(f"HyDE generation failed: {type(exc).__name__}: {exc}")
        return None, [f"hyde_error:{type(exc).__name__}"]
