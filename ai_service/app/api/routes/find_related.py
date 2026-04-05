"""
Backend integration endpoint for finding related regulations.

This endpoint is designed specifically for the backend API to call:
- Input: Case text + list of regulations from database
- Output: Ranked list of regulations with similarity scores and IDs
"""

from __future__ import annotations

from math import sqrt
import re

from fastapi import APIRouter, HTTPException

from app.api.schemas.requests import CaseFragment, FindRelatedRequest
from app.api.schemas.responses import (
    FindRelatedResponse,
    LineMatch,
    MatchEvidence,
    RelatedRegulation,
    ScoreBreakdown,
)
from app.api.deps import get_embedding_service
from app.utils.logger import logger

router = APIRouter()

STRICT_MIN_FINAL_SCORE = 0.45
STRICT_MIN_PAIR_SCORE = 0.40
STRICT_MIN_SUPPORTING_MATCHES = 1
DEFAULT_SEMANTIC_WEIGHT = 0.55
DEFAULT_SUPPORT_WEIGHT = 0.20
DEFAULT_LEXICAL_WEIGHT = 0.15
DEFAULT_CATEGORY_WEIGHT = 0.10
DEFAULT_REQUIRE_CASE_SUPPORT = True


class RegulationUnit(dict):
    text: str


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sqrt(sum(x * x for x in a)) or 1.0
    norm_b = sqrt(sum(x * x for x in b)) or 1.0
    return float(dot / (norm_a * norm_b))


def _clip(text: str, max_chars: int = 320) -> str:
    normalized = re.sub(r"\s+", " ", (text or "")).strip()
    if len(normalized) <= max_chars:
        return normalized
    return f"{normalized[:max_chars].rstrip()}..."


def _tokenize(text: str) -> set[str]:
    tokens = re.findall(r"[\w\u0600-\u06FF]{2,}", (text or "").lower())
    return set(tokens)


def _jaccard(a: str, b: str) -> float:
    tokens_a = _tokenize(a)
    tokens_b = _tokenize(b)
    if not tokens_a or not tokens_b:
        return 0.0
    union = tokens_a | tokens_b
    if not union:
        return 0.0
    return float(len(tokens_a & tokens_b) / len(union))


def _category_prior(case_type: str | None, regulation_category: str | None) -> float:
    if not case_type or not regulation_category:
        return 0.0

    expected_map = {
        "labor": "labor_law",
        "commercial": "commercial_law",
        "civil": "civil_law",
        "criminal": "criminal_law",
        "administrative": "procedural_law",
        "family": "civil_law",
    }
    expected = expected_map.get(case_type)
    if not expected:
        return 0.0
    return 1.0 if regulation_category == expected else 0.0


def _build_regulation_units(reg: object) -> tuple[list[RegulationUnit], list[str]]:
    warnings: list[str] = []
    units: list[RegulationUnit] = []

    candidate_chunks = getattr(reg, "candidate_chunks", None)
    if candidate_chunks:
        for chunk in candidate_chunks:
            chunk_text = (getattr(chunk, "text", "") or "").strip()
            if not chunk_text:
                continue
            units.append(
                RegulationUnit(
                    text=chunk_text,
                    chunk_id=getattr(chunk, "chunk_id", None),
                    line_start=getattr(chunk, "line_start", None),
                    line_end=getattr(chunk, "line_end", None),
                    article_ref=getattr(chunk, "article_ref", None),
                )
            )

    if units:
        return units, warnings

    text_parts = [getattr(reg, "title", "") or ""]
    if getattr(reg, "category", None):
        text_parts.append(f"({getattr(reg, 'category')})")
    if getattr(reg, "content_text", None):
        text_parts.append(getattr(reg, "content_text") or "")

    fallback_text = " ".join(
        part.strip() for part in text_parts if part and part.strip()
    ).strip()
    if fallback_text:
        warnings.append("regulation_chunk_index_fallback_used")
        units.append(RegulationUnit(text=fallback_text))
    return units, warnings


def _resolve_scoring_profile(
    payload: FindRelatedRequest,
) -> dict[str, float | int | bool]:
    profile = payload.scoring_profile
    semantic_weight = (
        float(profile.semantic_weight)
        if profile and profile.semantic_weight is not None
        else DEFAULT_SEMANTIC_WEIGHT
    )
    support_weight = (
        float(profile.support_weight)
        if profile and profile.support_weight is not None
        else DEFAULT_SUPPORT_WEIGHT
    )
    lexical_weight = (
        float(profile.lexical_weight)
        if profile and profile.lexical_weight is not None
        else DEFAULT_LEXICAL_WEIGHT
    )
    category_weight = (
        float(profile.category_weight)
        if profile and profile.category_weight is not None
        else DEFAULT_CATEGORY_WEIGHT
    )
    weight_sum = semantic_weight + support_weight + lexical_weight + category_weight
    if weight_sum <= 0:
        semantic_weight = DEFAULT_SEMANTIC_WEIGHT
        support_weight = DEFAULT_SUPPORT_WEIGHT
        lexical_weight = DEFAULT_LEXICAL_WEIGHT
        category_weight = DEFAULT_CATEGORY_WEIGHT
        weight_sum = semantic_weight + support_weight + lexical_weight + category_weight

    strict_min_final_score = (
        float(profile.strict_min_final_score)
        if profile and profile.strict_min_final_score is not None
        else STRICT_MIN_FINAL_SCORE
    )
    strict_min_pair_score = (
        float(profile.strict_min_pair_score)
        if profile and profile.strict_min_pair_score is not None
        else STRICT_MIN_PAIR_SCORE
    )
    strict_min_supporting_matches = (
        int(profile.strict_min_supporting_matches)
        if profile and profile.strict_min_supporting_matches is not None
        else STRICT_MIN_SUPPORTING_MATCHES
    )
    require_case_support = (
        bool(profile.require_case_support)
        if profile and profile.require_case_support is not None
        else DEFAULT_REQUIRE_CASE_SUPPORT
    )

    return {
        "semantic_weight": semantic_weight / weight_sum,
        "support_weight": support_weight / weight_sum,
        "lexical_weight": lexical_weight / weight_sum,
        "category_weight": category_weight / weight_sum,
        "strict_min_final_score": max(0.0, min(1.0, strict_min_final_score)),
        "strict_min_pair_score": max(0.0, min(1.0, strict_min_pair_score)),
        "strict_min_supporting_matches": max(1, min(10, strict_min_supporting_matches)),
        "require_case_support": require_case_support,
    }


@router.post("/similarity/find-related", response_model=FindRelatedResponse)
async def find_related_regulations(payload: FindRelatedRequest) -> FindRelatedResponse:
    try:
        if not payload.case_text or not payload.case_text.strip():
            raise HTTPException(status_code=400, detail="case_text cannot be empty")

        if not payload.regulations:
            raise HTTPException(
                status_code=400, detail="regulations list cannot be empty"
            )

        fragments = [
            fragment
            for fragment in (payload.case_fragments or [])
            if fragment.text and fragment.text.strip()
        ]
        if not fragments:
            fragments = [
                CaseFragment(
                    fragment_id="case_text",
                    text=payload.case_text,
                    source="case",
                )
            ]

        logger.info(
            "Finding related regulations",
            extra={
                "case_text_len": len(payload.case_text),
                "fragments_count": len(fragments),
                "num_candidates": len(payload.regulations),
                "top_k": payload.top_k,
                "threshold": payload.threshold,
                "strict_mode": payload.strict_mode,
            },
        )

        scoring_profile = _resolve_scoring_profile(payload)

        regulation_units: dict[int, list[RegulationUnit]] = {}
        regulation_warnings: dict[int, list[str]] = {}
        regulation_metadata: dict[int, dict[str, object]] = {}
        unit_texts: list[str] = []
        unit_refs: list[tuple[int, int]] = []

        for reg in payload.regulations:
            units, warnings = _build_regulation_units(reg)
            if not units:
                continue

            regulation_units[reg.id] = units
            regulation_warnings[reg.id] = warnings
            regulation_metadata[reg.id] = {
                "title": reg.title,
                "category": reg.category,
                "regulation_version_id": reg.regulation_version_id,
            }

            for idx, unit in enumerate(units):
                unit_texts.append(unit["text"])
                unit_refs.append((reg.id, idx))

        if not unit_texts:
            return FindRelatedResponse(
                related_regulations=[],
                query_length=len(payload.case_text),
                candidates_count=len(payload.regulations),
            )

        embedder = get_embedding_service()
        fragment_embeddings = embedder.embed_documents(
            [fragment.text for fragment in fragments], normalize=True
        )
        unit_embeddings = embedder.embed_documents(unit_texts, normalize=True)

        embedded_units_by_reg: dict[int, list[tuple[RegulationUnit, list[float]]]] = {}
        for embedding_index, (reg_id, unit_index) in enumerate(unit_refs):
            embedded_units_by_reg.setdefault(reg_id, []).append(
                (regulation_units[reg_id][unit_index], unit_embeddings[embedding_index])
            )

        related_regulations: list[RelatedRegulation] = []
        strict_mode = bool(payload.strict_mode)

        for reg in payload.regulations:
            reg_id = reg.id
            if reg_id not in embedded_units_by_reg:
                continue

            pair_scores: list[dict[str, object]] = []
            for fragment_index, fragment in enumerate(fragments):
                fragment_embedding = fragment_embeddings[fragment_index]
                for unit, unit_embedding in embedded_units_by_reg[reg_id]:
                    score = _cosine(fragment_embedding, unit_embedding)
                    pair_scores.append(
                        {
                            "fragment": fragment,
                            "unit": unit,
                            "score": score,
                        }
                    )

            if not pair_scores:
                continue

            pair_scores.sort(key=lambda item: float(item["score"]), reverse=True)
            semantic_max = float(pair_scores[0]["score"])
            support_floor = max(0.0, float(payload.threshold))
            supporting_pairs = [
                item for item in pair_scores if float(item["score"]) >= support_floor
            ]

            supported_fragment_ids = {
                item["fragment"].fragment_id
                for item in supporting_pairs  # type: ignore[index]
            }
            support_coverage = float(
                len(supported_fragment_ids) / max(1, len(fragments))
            )
            lexical_overlap = max(
                (
                    _jaccard(
                        item["fragment"].text,  # type: ignore[index]
                        item["unit"]["text"],  # type: ignore[index]
                    )
                    for item in pair_scores[: min(4, len(pair_scores))]
                ),
                default=0.0,
            )
            category_prior = _category_prior(
                payload.case_profile.case_type if payload.case_profile else None,
                reg.category,
            )

            final_score = float(
                (
                    float(scoring_profile["semantic_weight"]) * semantic_max
                    + float(scoring_profile["support_weight"]) * support_coverage
                    + float(scoring_profile["lexical_weight"]) * lexical_overlap
                    + float(scoring_profile["category_weight"]) * category_prior
                )
            )

            has_case_support = any(
                item["fragment"].source == "case"
                and float(item["score"]) >= support_floor  # type: ignore[index]
                for item in pair_scores
            )
            strong_support_count = sum(
                1
                for item in pair_scores
                if float(item["score"])
                >= float(scoring_profile["strict_min_pair_score"])
            )

            if strict_mode:
                if final_score < float(scoring_profile["strict_min_final_score"]):
                    continue
                if strong_support_count < int(
                    scoring_profile["strict_min_supporting_matches"]
                ):
                    continue
                if (
                    bool(scoring_profile["require_case_support"])
                    and not has_case_support
                ):
                    continue
            else:
                if final_score < max(0.0, float(payload.threshold)):
                    continue

            top_line_pairs = pair_scores[: min(3, len(pair_scores))]
            line_matches: list[LineMatch] = []
            for item in top_line_pairs:
                fragment = item["fragment"]
                unit = item["unit"]
                pair_score = float(item["score"])
                contribution = pair_score / semantic_max if semantic_max > 0 else 0.0
                line_matches.append(
                    LineMatch(
                        case_fragment_id=fragment.fragment_id,
                        case_snippet=_clip(fragment.text),
                        regulation_chunk_id=unit.get("chunk_id"),
                        regulation_snippet=_clip(unit["text"]),
                        line_start=unit.get("line_start"),
                        line_end=unit.get("line_end"),
                        article_ref=unit.get("article_ref"),
                        pair_score=pair_score,
                        contribution=float(max(0.0, min(1.0, contribution))),
                    )
                )

            evidence: list[MatchEvidence] = []
            seen_fragments: set[str] = set()
            for item in pair_scores:
                fragment = item["fragment"]
                if fragment.fragment_id in seen_fragments:
                    continue
                seen_fragments.add(fragment.fragment_id)
                evidence.append(
                    MatchEvidence(
                        fragment_id=fragment.fragment_id,
                        source=fragment.source,
                        document_id=fragment.document_id,
                        document_name=fragment.document_name,
                        score=float(item["score"]),
                    )
                )
                if len(evidence) >= 2:
                    break

            metadata = regulation_metadata.get(reg_id, {})
            related_regulations.append(
                RelatedRegulation(
                    regulation_id=reg_id,
                    matched_regulation_version_id=metadata.get("regulation_version_id"),
                    title=(metadata.get("title") or f"Regulation #{reg_id}"),
                    category=metadata.get("category"),
                    similarity_score=final_score,
                    evidence=evidence,
                    line_matches=line_matches,
                    score_breakdown=ScoreBreakdown(
                        semantic_max=semantic_max,
                        support_coverage=support_coverage,
                        lexical_overlap=lexical_overlap,
                        category_prior=category_prior,
                        final_score=final_score,
                        has_case_support=has_case_support,
                        strong_support_count=strong_support_count,
                    ),
                    warnings=regulation_warnings.get(reg_id, []),
                )
            )

        related_regulations.sort(key=lambda item: item.similarity_score, reverse=True)
        safe_top_k = max(1, payload.top_k)
        related_regulations = related_regulations[:safe_top_k]

        logger.info(
            f"Found {len(related_regulations)} related regulations",
            extra={
                "case_text_len": len(payload.case_text),
                "fragments_count": len(fragments),
                "total_candidates": len(payload.regulations),
                "matches": len(related_regulations),
                "strict_mode": strict_mode,
            },
        )

        return FindRelatedResponse(
            related_regulations=related_regulations,
            query_length=len(payload.case_text),
            candidates_count=len(payload.regulations),
        )
    except HTTPException:
        raise
    except Exception as error:
        logger.error(
            f"Error finding related regulations: {str(error)}",
            extra={"error_type": type(error).__name__},
        )
        raise HTTPException(
            status_code=500,
            detail=f"Failed to find related regulations: {str(error)}",
        )
