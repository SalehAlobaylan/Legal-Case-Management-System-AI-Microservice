"""
Multi-source case linking endpoint.

Type-agnostic counterpart to ``find_related.py``. Where the original endpoint
accepts only regulations, this one accepts a heterogeneous list of legal
sources (regulations, judicial decisions, gov data, web sources) and returns
trust-tier-aware ranked matches grouped by source type.

Design notes
------------
* Reuses the singleton ``EmbeddingService`` from ``deps`` so the BGE-M3 model
  is loaded once.
* Computes raw cosine similarity between the case query embedding and each
  source's chunk embeddings (best-of-chunks per source).
* Multiplies relevance × ``trust_multiplier`` to produce ``trust_weighted_score``
  used for ranking *within* a tier. Tiers are not blended in the response —
  the frontend renders them as separate groups.
* Stays intentionally simple: no HyDE, no cross-encoder, no LLM verification.
  Those advanced stages can be layered in later by reusing helpers from the
  legacy ``find_related`` module. Trust weighting + per-tier grouping is the
  core differentiator versus the legacy endpoint.
"""

from __future__ import annotations

import math
from typing import Literal, Sequence

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.api.deps import get_embedding_service

router = APIRouter()


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

SourceType = Literal["regulation", "judicial_decision", "gov_data", "web_source"]
TrustTier = Literal["official", "trusted", "discovered", "unverified"]


# Mirrored from backend's TRUST_TIER_MULTIPLIER (legal-sources.ts).
# Keep these two in sync.
TRUST_TIER_MULTIPLIER: dict[TrustTier, float] = {
    "official": 1.0,
    "trusted": 0.9,
    "discovered": 0.6,
    "unverified": 0.4,
}


class SourceChunk(BaseModel):
    chunk_index: int = 0
    text: str
    section_ref: str | None = None
    embedding: list[float] | None = Field(
        default=None,
        description=(
            "Optional pre-computed embedding. If absent, the service embeds "
            "the chunk text on the fly. Provide pre-computed embeddings for "
            "trusted sources to keep latency bounded."
        ),
    )


class LegalSourceCandidate(BaseModel):
    legal_source_id: int
    source_type: SourceType
    trust_tier: TrustTier
    source_authority: str
    title: str
    is_citable_in_court: bool = False
    chunks: list[SourceChunk] = Field(default_factory=list)
    # Surface URL for the response (so backend doesn't have to re-look it up)
    source_url: str | None = None


class FindRelatedMultiSourceRequest(BaseModel):
    case_text: str = Field(..., min_length=1)
    case_type: str | None = None
    sources: list[LegalSourceCandidate] = Field(default_factory=list)
    top_k_per_group: int = Field(default=5, ge=1, le=50)
    min_relevance: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Discard matches whose raw cosine relevance is below this threshold.",
    )


class MatchedChunk(BaseModel):
    chunk_index: int
    section_ref: str | None
    excerpt: str
    relevance: float


class MatchedSource(BaseModel):
    legal_source_id: int
    source_type: SourceType
    trust_tier: TrustTier
    source_authority: str
    title: str
    source_url: str | None
    is_citable_in_court: bool
    relevance_score: float
    trust_weighted_score: float
    best_chunk: MatchedChunk | None
    pipeline_stage: str = "multi_source_cosine_v1"


class SourceGroup(BaseModel):
    source_type: SourceType
    count: int
    any_citable: bool
    matches: list[MatchedSource]


class FindRelatedMultiSourceResponse(BaseModel):
    case_text_chars: int
    total_sources_evaluated: int
    groups: list[SourceGroup]


# ---------------------------------------------------------------------------
# Math helpers
# ---------------------------------------------------------------------------


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (math.sqrt(na) * math.sqrt(nb))


def _clip(text: str, max_chars: int = 320) -> str:
    text = text.strip()
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rsplit(" ", 1)[0] + "…"


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


@router.post(
    "/similarity/find-related-multi-source",
    response_model=FindRelatedMultiSourceResponse,
)
async def find_related_multi_source(
    payload: FindRelatedMultiSourceRequest,
) -> FindRelatedMultiSourceResponse:
    embedder = get_embedding_service()

    # 1. Embed the case query once.
    query_vec = embedder.embed_query(payload.case_text)

    # 2. Collect every chunk needing on-the-fly embedding so we batch them.
    chunks_to_embed: list[tuple[int, int, str]] = []  # (source_idx, chunk_idx, text)
    for s_idx, source in enumerate(payload.sources):
        for c_idx, chunk in enumerate(source.chunks):
            if chunk.embedding is None:
                chunks_to_embed.append((s_idx, c_idx, chunk.text))

    if chunks_to_embed:
        embeddings = embedder.embed_documents([t for _, _, t in chunks_to_embed])
        for (s_idx, c_idx, _), vec in zip(chunks_to_embed, embeddings):
            payload.sources[s_idx].chunks[c_idx].embedding = vec

    # 3. Score each source by best-of-chunks cosine, apply trust weighting.
    matches_by_type: dict[SourceType, list[MatchedSource]] = {
        "regulation": [],
        "judicial_decision": [],
        "gov_data": [],
        "web_source": [],
    }

    for source in payload.sources:
        best_score = -1.0
        best_chunk: MatchedChunk | None = None
        for chunk in source.chunks:
            if not chunk.embedding:
                continue
            score = _cosine(query_vec, chunk.embedding)
            if score > best_score:
                best_score = score
                best_chunk = MatchedChunk(
                    chunk_index=chunk.chunk_index,
                    section_ref=chunk.section_ref,
                    excerpt=_clip(chunk.text),
                    relevance=round(score, 4),
                )

        if best_score < payload.min_relevance:
            continue

        relevance = max(0.0, best_score)
        multiplier = TRUST_TIER_MULTIPLIER[source.trust_tier]
        trust_weighted = relevance * multiplier

        matches_by_type[source.source_type].append(
            MatchedSource(
                legal_source_id=source.legal_source_id,
                source_type=source.source_type,
                trust_tier=source.trust_tier,
                source_authority=source.source_authority,
                title=source.title,
                source_url=source.source_url,
                is_citable_in_court=source.is_citable_in_court,
                relevance_score=round(relevance, 4),
                trust_weighted_score=round(trust_weighted, 4),
                best_chunk=best_chunk,
            )
        )

    # 4a. Regulation demotion rule: keep top 4 regulations unconditionally.
    #     Any regulation ranked 5th or lower with raw relevance < 60% gets
    #     its trust_weighted_score halved so web/other sources surface higher
    #     in the final per-group caps.
    REGULATION_KEEP_TOP = 4
    REGULATION_DEMOTION_THRESHOLD = 0.60
    REGULATION_DEMOTION_FACTOR = 0.5

    regs = matches_by_type.get("regulation", [])
    regs.sort(key=lambda m: m.trust_weighted_score, reverse=True)
    for i, m in enumerate(regs):
        if i >= REGULATION_KEEP_TOP and m.relevance_score < REGULATION_DEMOTION_THRESHOLD:
            m.trust_weighted_score = round(
                m.trust_weighted_score * REGULATION_DEMOTION_FACTOR, 4
            )

    # 4b. Sort each group by trust-weighted score desc, cap to top_k_per_group.
    groups: list[SourceGroup] = []
    for source_type, items in matches_by_type.items():
        items.sort(key=lambda m: m.trust_weighted_score, reverse=True)
        capped = items[: payload.top_k_per_group]
        groups.append(
            SourceGroup(
                source_type=source_type,
                count=len(capped),
                any_citable=any(m.is_citable_in_court for m in capped),
                matches=capped,
            )
        )

    return FindRelatedMultiSourceResponse(
        case_text_chars=len(payload.case_text),
        total_sources_evaluated=len(payload.sources),
        groups=groups,
    )
