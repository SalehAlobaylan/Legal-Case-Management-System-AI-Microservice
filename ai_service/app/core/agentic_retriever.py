"""
Agentic retrieval for multi-domain legal cases (Phase 3 — experimental).

For complex cases that touch multiple legal domains, a single retrieval pass
may miss regulations from secondary domains.  This module implements an
iterative refinement loop:

  1. Review initial retrieval results via Gemini
  2. Identify coverage gaps (missing legal domains / topics)
  3. Generate focused refinement queries
  4. Run additional embedding-based retrieval rounds
  5. Return merged, deduplicated query set for downstream scoring

Feature-flagged via settings.agentic_retrieval_enabled.  Requires Gemini to
be configured.  Falls back gracefully — on any failure the caller proceeds
with the original results unchanged.

Pipeline position (inside find_related):
    composite scoring (initial) -> agentic expansion -> re-scoring -> ...

This module does NOT score or rank results.  It only generates additional
retrieval queries.  Scoring happens in the main pipeline using the same
composite logic applied to all candidates.
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


# ---------------------------------------------------------------------------
# Prompt templates
# ---------------------------------------------------------------------------

_GAP_ANALYSIS_PROMPT = """أنت خبير قانوني سعودي. راجعت نتائج البحث الأولية عن الأنظمة المتعلقة بقضية معينة.

نص القضية:
{case_text}

الأنظمة التي تم العثور عليها:
{found_regulations}

قم بتحليل ما إذا كانت هناك فجوات في التغطية القانونية. هل هناك مجالات قانونية مفقودة؟

أجب بتنسيق JSON فقط:
{{
  "has_gaps": true/false,
  "gap_description": "وصف مختصر للفجوات",
  "refined_queries": [
    "استعلام بحث مركز باللغة العربية أو الإنجليزية",
    "استعلام بحث آخر إذا لزم الأمر"
  ],
  "missing_domains": ["labor_law", "commercial_law"]
}}

أجب فقط بتنسيق JSON بدون أي نص إضافي. إذا لم تكن هناك فجوات، اجعل has_gaps=false و refined_queries قائمة فارغة."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _strip_fences(text: str) -> str:
    """Remove markdown code fences from LLM output."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        lines = [l for l in lines if not l.strip().startswith("```")]
        text = "\n".join(lines).strip()
    return text


def _parse_gap_analysis(raw_text: str) -> dict[str, Any] | None:
    """Parse Gemini gap analysis JSON response."""
    text = _strip_fences(raw_text)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        logger.warning("Agentic: gap analysis response is not valid JSON")
        return None

    if not isinstance(parsed, dict):
        return None

    return {
        "has_gaps": bool(parsed.get("has_gaps", False)),
        "gap_description": str(parsed.get("gap_description", "")),
        "refined_queries": [
            str(q) for q in (parsed.get("refined_queries") or [])
            if isinstance(q, str) and len(q.strip()) >= 5
        ],
        "missing_domains": [
            str(d) for d in (parsed.get("missing_domains") or [])
            if isinstance(d, str)
        ],
    }


def _format_found_regulations(
    found: list[dict[str, Any]],
    max_items: int = 10,
) -> str:
    """Format found regulations for the gap analysis prompt."""
    lines: list[str] = []
    for item in found[:max_items]:
        title = item.get("title", "Unknown")
        category = item.get("category", "N/A")
        score = item.get("score", 0.0)
        lines.append(f"- {title} ({category}) — score: {score:.3f}")
    if len(found) > max_items:
        lines.append(f"... and {len(found) - max_items} more")
    return "\n".join(lines) if lines else "(no regulations found)"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def analyze_gaps_and_generate_queries(
    case_text: str,
    found_regulations: list[dict[str, Any]],
) -> tuple[list[str], list[str]]:
    """
    Analyze retrieval results for coverage gaps and generate refined queries.

    Args:
        case_text: The original case text
        found_regulations: List of dicts with at least {title, category, score}

    Returns:
        (refined_queries, warnings)
        - refined_queries: List of query strings for additional retrieval.
                           Empty if no gaps found or on failure.
        - warnings: Pipeline warnings for logging/response.
    """
    warnings: list[str] = []

    if not settings.agentic_retrieval_enabled:
        return [], ["agentic_disabled"]

    if not settings.gemini_api_key:
        logger.warning("Agentic retrieval enabled but no Gemini API key")
        return [], ["agentic_no_api_key"]

    if genai is None:
        logger.warning("google-generativeai not installed for agentic retrieval")
        return [], ["agentic_package_missing"]

    # Don't bother with gap analysis if we already have many candidates
    if len(found_regulations) >= settings.agentic_min_candidates_for_refinement * 5:
        return [], ["agentic_sufficient_candidates"]

    found_block = _format_found_regulations(found_regulations)
    prompt = _GAP_ANALYSIS_PROMPT.format(
        case_text=case_text[:4000],
        found_regulations=found_block,
    )

    try:
        genai.configure(api_key=settings.gemini_api_key)
        model = genai.GenerativeModel(settings.gemini_model)

        response = await asyncio.wait_for(
            asyncio.to_thread(
                model.generate_content,
                prompt,
                generation_config=genai.GenerationConfig(
                    temperature=0.2,
                    max_output_tokens=1024,
                ),
            ),
            timeout=settings.agentic_timeout_seconds,
        )

        raw_text = (response.text or "").strip()
        if not raw_text:
            logger.warning("Agentic: Gemini returned empty gap analysis")
            return [], ["agentic_empty_response"]

        analysis = _parse_gap_analysis(raw_text)
        if analysis is None:
            return [], ["agentic_parse_failed"]

        if not analysis["has_gaps"]:
            logger.info("agentic:no_gaps_found")
            return [], []

        queries = analysis["refined_queries"]
        if not queries:
            logger.info("agentic:gaps_found_but_no_queries")
            return [], ["agentic_no_queries_generated"]

        # Cap to max_rounds queries
        max_q = settings.agentic_max_rounds
        queries = queries[:max_q]

        logger.info(
            "agentic:queries_generated",
            extra={
                "num_queries": len(queries),
                "missing_domains": analysis["missing_domains"],
                "gap_description": analysis["gap_description"][:200],
            },
        )
        return queries, warnings

    except asyncio.TimeoutError:
        logger.warning(
            f"Agentic gap analysis timed out after {settings.agentic_timeout_seconds}s"
        )
        return [], ["agentic_timeout"]
    except Exception as exc:
        logger.warning(f"Agentic gap analysis failed: {type(exc).__name__}: {exc}")
        return [], [f"agentic_error:{type(exc).__name__}"]
