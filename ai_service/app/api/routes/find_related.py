"""
Backend integration endpoint for finding related regulations.

This endpoint is designed specifically for the backend API to call:
- Input: Case text + list of regulations from database
- Output: Ranked list of regulations with similarity scores and IDs

Pipeline stages:
  0.5. HyDE query expansion       (when hyde_enabled + request opt-in)
  1.   embedding & unit build      (always)
  2.   composite scoring           (always)
  2.1. agentic retrieval expansion (when agentic_retrieval_enabled + request opt-in)
  2.2. ColBERT late-interaction    (when colbert_enabled + request opt-in)
  2.5. cross-encoder reranking     (when cross_encoder_enabled + request opt-in)
  3.   LLM verification            (when gemini_enabled + request opt-in)
"""

from __future__ import annotations

import time
from math import sqrt
import re
from typing import Any

from fastapi import APIRouter, HTTPException

from app.api.schemas.requests import CaseFragment, FindRelatedRequest
from app.api.schemas.responses import (
    FindRelatedResponse,
    LineMatch,
    MatchEvidence,
    RelatedRegulation,
    ScoreBreakdown,
    VerificationDetail,
)
from app.api.deps import get_embedding_service
from app.config import settings
from app.core.agentic_retriever import analyze_gaps_and_generate_queries
from app.core.hyde import generate_hypothetical_regulation
from app.core.llm_verifier import verify_candidates, blend_scores
from app.core.colbert_retriever import get_colbert_service
from app.core.reranker import get_reranker_service
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


def _should_run_llm_verification(payload: FindRelatedRequest) -> bool:
    """Determine if LLM verification should run for this request."""
    if payload.enable_llm_verification is not None:
        return payload.enable_llm_verification and settings.gemini_enabled
    return settings.gemini_enabled


def _should_run_cross_encoder(payload: FindRelatedRequest) -> bool:
    """Determine if cross-encoder reranking should run for this request."""
    if payload.enable_cross_encoder is not None:
        return payload.enable_cross_encoder and settings.cross_encoder_enabled
    return settings.cross_encoder_enabled


def _should_run_hyde(payload: FindRelatedRequest) -> bool:
    """Determine if HyDE query expansion should run for this request."""
    if payload.enable_hyde is not None:
        return payload.enable_hyde and settings.hyde_enabled
    return settings.hyde_enabled


def _should_run_agentic(payload: FindRelatedRequest) -> bool:
    """Determine if agentic retrieval expansion should run for this request."""
    if payload.enable_agentic is not None:
        return payload.enable_agentic and settings.agentic_retrieval_enabled
    return settings.agentic_retrieval_enabled


def _should_run_colbert(payload: FindRelatedRequest) -> bool:
    """Determine if ColBERT late-interaction reranking should run."""
    if payload.enable_colbert is not None:
        return payload.enable_colbert and settings.colbert_enabled
    return settings.colbert_enabled


def _build_pipeline_label(
    use_reranker: bool, use_llm: bool, use_hyde: bool,
    use_agentic: bool = False, use_colbert: bool = False,
) -> str:
    """Build a descriptive pipeline label from enabled stages."""
    parts = ["composite"]
    if use_hyde:
        parts.insert(0, "hyde")
    if use_agentic:
        parts.append("agentic")
    if use_colbert:
        parts.append("colbert")
    if use_reranker:
        parts.append("rerank")
    if use_llm:
        parts.append("gemini")
    return "_".join(parts) + "_v1"


def _best_excerpt(units: list[RegulationUnit], max_chars: int = 2000) -> str:
    """Build a representative text excerpt from regulation units."""
    parts: list[str] = []
    total = 0
    for u in units:
        text = u.get("text", "")
        if total + len(text) > max_chars:
            remaining = max_chars - total
            if remaining > 50:
                parts.append(text[:remaining])
            break
        parts.append(text)
        total += len(text)
    return "\n".join(parts)


@router.post("/similarity/find-related", response_model=FindRelatedResponse)
async def find_related_regulations(payload: FindRelatedRequest) -> FindRelatedResponse:
    try:
        t_start = time.perf_counter()

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

        use_llm = _should_run_llm_verification(payload)
        use_reranker = _should_run_cross_encoder(payload)
        use_hyde = _should_run_hyde(payload)
        use_agentic = _should_run_agentic(payload)
        use_colbert = _should_run_colbert(payload)
        pipeline_label = _build_pipeline_label(
            use_reranker, use_llm, use_hyde, use_agentic, use_colbert
        )
        pipeline_warnings: list[str] = []

        logger.info(
            "find_related:start",
            extra={
                "case_text_len": len(payload.case_text),
                "fragments_count": len(fragments),
                "num_candidates": len(payload.regulations),
                "top_k": payload.top_k,
                "threshold": payload.threshold,
                "strict_mode": payload.strict_mode,
                "pipeline": pipeline_label,
            },
        )

        scoring_profile = _resolve_scoring_profile(payload)

        # ---- Stage 0.5: HyDE query expansion (optional) ----
        hyde_fragment: CaseFragment | None = None
        if use_hyde:
            t_hyde = time.perf_counter()
            hyde_text, hyde_warnings = await generate_hypothetical_regulation(
                payload.case_text
            )
            pipeline_warnings.extend(hyde_warnings)

            if hyde_text:
                hyde_fragment = CaseFragment(
                    fragment_id="hyde_hypothetical",
                    text=hyde_text,
                    source="hyde",
                )
                fragments.append(hyde_fragment)

            hyde_ms = (time.perf_counter() - t_hyde) * 1000
            logger.info(
                "find_related:hyde_done",
                extra={
                    "hyde_generated": hyde_text is not None,
                    "hyde_text_len": len(hyde_text) if hyde_text else 0,
                    "stage_hyde_ms": round(hyde_ms, 1),
                },
            )

        # ---- Stage 1: build regulation units & embed ----
        t_stage1 = time.perf_counter()

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
                pipeline=pipeline_label,
                pipeline_warnings=pipeline_warnings or None,
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

        stage1_ms = (time.perf_counter() - t_stage1) * 1000

        # ---- Stage 2: composite scoring ----
        t_stage2 = time.perf_counter()

        # Count of real (non-HyDE) fragments for support coverage calculation
        real_fragment_count = sum(
            1 for f in fragments if f.fragment_id != "hyde_hypothetical"
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

            # Support coverage: only count real fragments (exclude HyDE)
            supported_fragment_ids = {
                item["fragment"].fragment_id
                for item in supporting_pairs  # type: ignore[index]
                if item["fragment"].fragment_id != "hyde_hypothetical"
            }
            support_coverage = float(
                len(supported_fragment_ids) / max(1, real_fragment_count)
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
                    pipeline_stage="composite",
                )
            )

        related_regulations.sort(key=lambda item: item.similarity_score, reverse=True)

        stage2_ms = (time.perf_counter() - t_stage2) * 1000
        retrieval_count = len(related_regulations)

        logger.info(
            "find_related:composite_done",
            extra={
                "retrieval_candidates": retrieval_count,
                "stage1_embed_ms": round(stage1_ms, 1),
                "stage2_score_ms": round(stage2_ms, 1),
            },
        )

        # ---- Stage 2.1: Agentic retrieval expansion (optional, experimental) ----
        agentic_rounds_run: int = 0

        if use_agentic and related_regulations:
            t_agentic = time.perf_counter()

            # Summarize current results for gap analysis
            found_summary: list[dict[str, Any]] = [
                {
                    "title": rr.title,
                    "category": rr.category or "",
                    "score": rr.similarity_score,
                }
                for rr in related_regulations[:15]
            ]

            refined_queries, agentic_warnings = await analyze_gaps_and_generate_queries(
                payload.case_text, found_summary
            )
            pipeline_warnings.extend(agentic_warnings)

            if refined_queries:
                existing_reg_ids = {rr.regulation_id for rr in related_regulations}

                for round_idx, query in enumerate(refined_queries):
                    # Embed the refined query
                    query_embedding = embedder.embed_documents(
                        [query], normalize=True
                    )[0]

                    # Score the query against all regulation units
                    for reg_id, unit_emb_list in embedded_units_by_reg.items():
                        if reg_id in existing_reg_ids:
                            continue  # Already scored

                        best_score = 0.0
                        best_unit: RegulationUnit | None = None
                        for unit, unit_embedding in unit_emb_list:
                            score = _cosine(query_embedding, unit_embedding)
                            if score > best_score:
                                best_score = score
                                best_unit = unit

                        # Only add if above a relaxed threshold
                        agentic_threshold = max(
                            0.0, float(payload.threshold) * 0.8
                        )
                        if best_score < agentic_threshold or best_unit is None:
                            continue

                        metadata = regulation_metadata.get(reg_id, {})
                        category_prior = _category_prior(
                            payload.case_profile.case_type
                            if payload.case_profile
                            else None,
                            metadata.get("category"),
                        )

                        # Simplified scoring for agentic-discovered candidates
                        final_score = float(
                            float(scoring_profile["semantic_weight"]) * best_score
                            + float(scoring_profile["category_weight"]) * category_prior
                        )

                        if final_score < max(0.0, float(payload.threshold) * 0.7):
                            continue

                        lm = LineMatch(
                            case_fragment_id=f"agentic_round_{round_idx}",
                            case_snippet=_clip(query),
                            regulation_chunk_id=best_unit.get("chunk_id"),
                            regulation_snippet=_clip(best_unit["text"]),
                            line_start=best_unit.get("line_start"),
                            line_end=best_unit.get("line_end"),
                            article_ref=best_unit.get("article_ref"),
                            pair_score=best_score,
                            contribution=1.0,
                        )

                        related_regulations.append(
                            RelatedRegulation(
                                regulation_id=reg_id,
                                matched_regulation_version_id=metadata.get(
                                    "regulation_version_id"
                                ),
                                title=(
                                    metadata.get("title")
                                    or f"Regulation #{reg_id}"
                                ),
                                category=metadata.get("category"),
                                similarity_score=final_score,
                                evidence=[],
                                line_matches=[lm],
                                score_breakdown=ScoreBreakdown(
                                    semantic_max=best_score,
                                    support_coverage=0.0,
                                    lexical_overlap=0.0,
                                    category_prior=category_prior,
                                    final_score=final_score,
                                    has_case_support=False,
                                    strong_support_count=0,
                                ),
                                warnings=(
                                    regulation_warnings.get(reg_id, [])
                                    + ["agentic_discovery"]
                                ),
                                pipeline_stage="agentic",
                            )
                        )
                        existing_reg_ids.add(reg_id)

                    agentic_rounds_run += 1

                # Re-sort after agentic expansion
                related_regulations.sort(
                    key=lambda item: item.similarity_score, reverse=True
                )

            agentic_ms = (time.perf_counter() - t_agentic) * 1000
            logger.info(
                "find_related:agentic_done",
                extra={
                    "agentic_rounds": agentic_rounds_run,
                    "agentic_queries": len(refined_queries),
                    "candidates_after_agentic": len(related_regulations),
                    "stage_agentic_ms": round(agentic_ms, 1),
                },
            )

        # ---- Stage 2.2: ColBERT late-interaction reranking (optional) ----
        colbert_count: int | None = None

        if use_colbert and related_regulations:
            t_colbert = time.perf_counter()

            colbert_svc = get_colbert_service()
            colbert_top_n = settings.colbert_top_n

            # Build (case_text, best_regulation_excerpt) pairs
            colbert_docs: list[str] = []
            for rr in related_regulations:
                reg_id = rr.regulation_id
                units = regulation_units.get(reg_id, [])
                colbert_docs.append(_best_excerpt(units))

            colbert_reranked = colbert_svc.rerank(
                query=payload.case_text,
                documents=colbert_docs,
                top_n=colbert_top_n,
            )

            # Apply ColBERT scores and reorder
            colbert_regulations: list[RelatedRegulation] = []
            for original_idx, colbert_score in colbert_reranked:
                rr = related_regulations[original_idx]
                rr.colbert_score = colbert_score
                if rr.pipeline_stage and "colbert" not in rr.pipeline_stage:
                    rr.pipeline_stage = rr.pipeline_stage + "+colbert"
                else:
                    rr.pipeline_stage = "composite+colbert"
                colbert_regulations.append(rr)

            related_regulations = colbert_regulations
            colbert_count = len(related_regulations)

            colbert_ms = (time.perf_counter() - t_colbert) * 1000
            logger.info(
                "find_related:colbert_done",
                extra={
                    "colbert_input": retrieval_count,
                    "colbert_output": colbert_count,
                    "stage_colbert_ms": round(colbert_ms, 1),
                },
            )

        # ---- Stage 2.5: Cross-encoder reranking (optional) ----
        reranker_count: int | None = None

        if use_reranker and related_regulations:
            t_rerank = time.perf_counter()

            reranker = get_reranker_service()
            rerank_top_n = settings.cross_encoder_top_n

            # Build (case_text, best_regulation_excerpt) pairs
            rerank_docs: list[str] = []
            for rr in related_regulations:
                reg_id = rr.regulation_id
                units = regulation_units.get(reg_id, [])
                rerank_docs.append(_best_excerpt(units))

            reranked = reranker.rerank(
                query=payload.case_text,
                documents=rerank_docs,
                top_n=rerank_top_n,
            )

            # Apply reranker scores and reorder
            reranked_regulations: list[RelatedRegulation] = []
            for original_idx, reranker_score in reranked:
                rr = related_regulations[original_idx]
                rr.reranker_score = reranker_score
                rr.pipeline_stage = "composite+rerank"
                reranked_regulations.append(rr)

            related_regulations = reranked_regulations
            reranker_count = len(related_regulations)

            rerank_ms = (time.perf_counter() - t_rerank) * 1000
            logger.info(
                "find_related:rerank_done",
                extra={
                    "rerank_input": retrieval_count,
                    "rerank_output": reranker_count,
                    "stage_rerank_ms": round(rerank_ms, 1),
                },
            )

        # ---- Stage 3: LLM verification (optional) ----
        llm_approved_count: int | None = None

        if use_llm and related_regulations:
            t_stage3 = time.perf_counter()
            top_n = min(settings.gemini_top_n_candidates, len(related_regulations))
            llm_candidates: list[dict[str, Any]] = []
            for rr in related_regulations[:top_n]:
                reg_id = rr.regulation_id
                units = regulation_units.get(reg_id, [])
                llm_candidates.append({
                    "regulation_id": reg_id,
                    "title": rr.title,
                    "category": rr.category or "",
                    "excerpt": _best_excerpt(units),
                })

            verification_results, llm_warnings = await verify_candidates(
                payload.case_text, llm_candidates
            )
            pipeline_warnings.extend(llm_warnings)

            if verification_results:
                # Apply verification metadata and re-score
                for rr in related_regulations[:top_n]:
                    v = verification_results.get(rr.regulation_id)
                    if v:
                        rr.verification = VerificationDetail(
                            status="approved" if v["applicable"] else "rejected",
                            confidence=v.get("confidence"),
                            explanation_ar=v.get("explanation_ar"),
                            relevant_articles=v.get("relevant_articles"),
                        )
                        blended, llm_score = blend_scores(
                            rr.similarity_score, v
                        )
                        rr.similarity_score = blended
                        rr.verification.llm_score = llm_score
                        # Build stage label reflecting all active stages
                        stage_parts = ["composite"]
                        if rr.reranker_score is not None:
                            stage_parts.append("rerank")
                        stage_parts.append("gemini")
                        rr.pipeline_stage = "+".join(stage_parts)
                    else:
                        rr.verification = VerificationDetail(status="skipped")

                # Filter out rejected candidates
                approved = [
                    rr for rr in related_regulations
                    if rr.verification is None
                    or rr.verification.status != "rejected"
                ]
                llm_approved_count = sum(
                    1 for rr in approved
                    if rr.verification and rr.verification.status == "approved"
                )
                related_regulations = approved
                # Re-sort after score blending
                related_regulations.sort(
                    key=lambda item: item.similarity_score, reverse=True
                )
            else:
                # Fallback: keep original ranking
                pipeline_warnings.append("gemini_fallback_used")

            stage3_ms = (time.perf_counter() - t_stage3) * 1000
            logger.info(
                "find_related:llm_done",
                extra={
                    "llm_candidates_sent": top_n,
                    "llm_approved": llm_approved_count,
                    "stage3_llm_ms": round(stage3_ms, 1),
                    "llm_warnings": llm_warnings,
                },
            )

        # ---- Final: truncate and return ----
        safe_top_k = max(1, payload.top_k)
        related_regulations = related_regulations[:safe_top_k]

        total_ms = (time.perf_counter() - t_start) * 1000
        logger.info(
            "find_related:done",
            extra={
                "case_text_len": len(payload.case_text),
                "fragments_count": len(fragments),
                "total_candidates": len(payload.regulations),
                "retrieval_candidates": retrieval_count,
                "agentic_rounds": agentic_rounds_run,
                "colbert_candidates": colbert_count,
                "reranker_candidates": reranker_count,
                "llm_approved": llm_approved_count,
                "matches_returned": len(related_regulations),
                "strict_mode": strict_mode,
                "pipeline": pipeline_label,
                "total_ms": round(total_ms, 1),
            },
        )

        return FindRelatedResponse(
            related_regulations=related_regulations,
            query_length=len(payload.case_text),
            candidates_count=len(payload.regulations),
            pipeline=pipeline_label,
            pipeline_warnings=pipeline_warnings or None,
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
