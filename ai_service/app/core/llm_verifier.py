"""
Gemini-based LLM verification and reranking for regulation matching.

This module provides a post-retrieval verification layer that:
1. Validates whether each candidate regulation is legally applicable
2. Identifies relevant articles
3. Assigns a confidence level
4. Provides an Arabic explanation

Feature-flagged via settings.gemini_enabled. Falls back gracefully on
timeout, error, or disabled state.
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

# Confidence → numeric score for blending with composite score
_CONFIDENCE_SCORES: dict[str, float] = {
    "high": 1.0,
    "medium": 0.6,
    "low": 0.3,
}

# Weight given to LLM confidence when blending with composite score
_LLM_BLEND_WEIGHT = 0.15


def _build_prompt(case_text: str, candidates: list[dict[str, Any]]) -> str:
    """Build the verification prompt for Gemini."""
    reg_block = ""
    for c in candidates:
        reg_block += (
            f"\n--- Regulation ID: {c['regulation_id']} ---\n"
            f"Title: {c['title']}\n"
            f"Category: {c.get('category', 'N/A')}\n"
            f"Excerpt: {c['excerpt'][:2000]}\n"
        )

    return f"""أنت خبير قانوني سعودي. بناءً على نص القضية التالي، قم بتقييم كل نظام من الأنظمة المرشحة أدناه.

نص القضية:
{case_text[:4000]}

الأنظمة المرشحة:
{reg_block}

لكل نظام، أجب بتنسيق JSON كالتالي:
{{
  "results": [
    {{
      "regulation_id": <int>,
      "applicable": true/false,
      "confidence": "high" | "medium" | "low",
      "relevant_articles": ["Article X", "Article Y"],
      "explanation_ar": "شرح مختصر بالعربية لسبب انطباق أو عدم انطباق هذا النظام"
    }}
  ]
}}

أجب فقط بتنسيق JSON بدون أي نص إضافي."""


def _parse_response(raw_text: str, candidate_ids: set[int]) -> dict[int, dict[str, Any]]:
    """Parse Gemini JSON response into a dict keyed by regulation_id."""
    results: dict[int, dict[str, Any]] = {}

    # Strip markdown fences if present
    text = raw_text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        # Remove first and last fence lines
        lines = [l for l in lines if not l.strip().startswith("```")]
        text = "\n".join(lines).strip()

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        logger.warning("Gemini response is not valid JSON, skipping verification")
        return results

    items = parsed if isinstance(parsed, list) else parsed.get("results", [])
    for item in items:
        if not isinstance(item, dict):
            continue
        reg_id = item.get("regulation_id")
        if reg_id not in candidate_ids:
            continue
        results[reg_id] = {
            "applicable": bool(item.get("applicable", False)),
            "confidence": str(item.get("confidence", "low")).lower(),
            "relevant_articles": item.get("relevant_articles") or [],
            "explanation_ar": item.get("explanation_ar") or "",
        }

    return results


async def verify_candidates(
    case_text: str,
    candidates: list[dict[str, Any]],
) -> tuple[dict[int, dict[str, Any]], list[str]]:
    """
    Send top candidates to Gemini for verification.

    Returns:
        (results_by_reg_id, warnings)
        - results_by_reg_id: dict mapping regulation_id → verification result
        - warnings: list of pipeline warnings (e.g. fallback used)

    On any failure, returns empty results so the caller can fall back to
    the original ranking.
    """
    warnings: list[str] = []

    if not settings.gemini_enabled:
        return {}, ["gemini_disabled"]

    if not settings.gemini_api_key:
        logger.warning("Gemini enabled but no API key configured")
        return {}, ["gemini_no_api_key"]

    if genai is None:
        logger.warning("google-generativeai package not installed")
        return {}, ["gemini_package_missing"]

    if not candidates:
        return {}, []

    candidate_ids = {c["regulation_id"] for c in candidates}
    prompt = _build_prompt(case_text, candidates)

    try:
        genai.configure(api_key=settings.gemini_api_key)
        model = genai.GenerativeModel(settings.gemini_model)

        response = await asyncio.wait_for(
            asyncio.to_thread(
                model.generate_content,
                prompt,
                generation_config=genai.GenerationConfig(
                    temperature=0.1,
                    max_output_tokens=4096,
                ),
            ),
            timeout=settings.gemini_timeout_seconds,
        )

        raw_text = response.text or ""
        if not raw_text.strip():
            logger.warning("Gemini returned empty response")
            return {}, ["gemini_empty_response"]

        results = _parse_response(raw_text, candidate_ids)
        if not results:
            warnings.append("gemini_parse_failed")
        return results, warnings

    except asyncio.TimeoutError:
        logger.warning(
            f"Gemini verification timed out after {settings.gemini_timeout_seconds}s"
        )
        return {}, ["gemini_timeout"]
    except Exception as exc:
        logger.warning(f"Gemini verification failed: {type(exc).__name__}: {exc}")
        return {}, [f"gemini_error:{type(exc).__name__}"]


def blend_scores(
    composite_score: float,
    verification: dict[str, Any] | None,
) -> tuple[float, float | None]:
    """
    Blend composite score with LLM verification confidence.

    Returns (blended_score, llm_score_component).
    If verification is None or not applicable, returns original score unchanged.
    """
    if not verification or not verification.get("applicable", False):
        return composite_score, None

    confidence = verification.get("confidence", "low")
    llm_score = _CONFIDENCE_SCORES.get(confidence, 0.3)
    blended = (1.0 - _LLM_BLEND_WEIGHT) * composite_score + _LLM_BLEND_WEIGHT * llm_score
    return blended, llm_score
