"""
Backend integration endpoint for finding related regulations.

This endpoint is designed specifically for the backend API to call:
- Input: Case text + list of regulations from database
- Output: Ranked list of regulations with similarity scores and IDs
"""

from __future__ import annotations

from math import sqrt

from fastapi import APIRouter, HTTPException

from app.api.schemas.requests import CaseFragment, FindRelatedRequest
from app.api.schemas.responses import (
    FindRelatedResponse,
    MatchEvidence,
    RelatedRegulation,
)
from app.core.embeddings import EmbeddingService
from app.utils.logger import logger

router = APIRouter()


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sqrt(sum(x * x for x in a)) or 1.0
    norm_b = sqrt(sum(x * x for x in b)) or 1.0
    return float(dot / (norm_a * norm_b))


@router.post("/similarity/find-related", response_model=FindRelatedResponse)
async def find_related_regulations(payload: FindRelatedRequest) -> FindRelatedResponse:
    try:
        if not payload.case_text or not payload.case_text.strip():
            raise HTTPException(status_code=400, detail="case_text cannot be empty")

        if not payload.regulations:
            raise HTTPException(status_code=400, detail="regulations list cannot be empty")

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

        regulation_texts: list[str] = []
        regulation_ids: list[int] = []
        regulation_metadata: dict[int, dict[str, str | None]] = {}
        for reg in payload.regulations:
            reg_id = reg.id
            regulation_ids.append(reg_id)
            text_parts = [reg.title]
            if reg.category:
                text_parts.append(f"({reg.category})")
            if reg.content_text:
                text_parts.append(reg.content_text)
            regulation_texts.append(" ".join(text_parts))
            regulation_metadata[reg_id] = {
                "title": reg.title,
                "category": reg.category,
            }

        logger.info(
            "Finding related regulations",
            extra={
                "case_text_len": len(payload.case_text),
                "fragments_count": len(fragments),
                "num_candidates": len(payload.regulations),
                "top_k": payload.top_k,
                "threshold": payload.threshold,
            },
        )

        embedder = EmbeddingService()
        corpus_embeddings = embedder.embed_documents(regulation_texts, normalize=True)
        fragment_embeddings = embedder.embed_documents(
            [fragment.text for fragment in fragments], normalize=True
        )

        aggregate: dict[int, dict[str, object]] = {}
        for fragment_index, fragment in enumerate(fragments):
            fragment_embedding = fragment_embeddings[fragment_index]
            for corpus_index, regulation_embedding in enumerate(corpus_embeddings):
                score = _cosine(fragment_embedding, regulation_embedding)
                if score < payload.threshold:
                    continue

                regulation_id = regulation_ids[corpus_index]
                entry = aggregate.get(regulation_id)
                if not entry:
                    entry = {
                        "score": score,
                        "evidence": [],
                    }
                    aggregate[regulation_id] = entry
                else:
                    entry["score"] = max(float(entry["score"]), score)

                evidence_list = entry["evidence"]
                if isinstance(evidence_list, list):
                    evidence_list.append(
                        MatchEvidence(
                            fragment_id=fragment.fragment_id,
                            source=fragment.source,
                            document_id=fragment.document_id,
                            document_name=fragment.document_name,
                            score=score,
                        )
                    )

        ranked_regulations = sorted(
            aggregate.items(),
            key=lambda item: float(item[1]["score"]),
            reverse=True,
        )

        related_regulations: list[RelatedRegulation] = []
        safe_top_k = max(1, payload.top_k)
        for regulation_id, entry in ranked_regulations[:safe_top_k]:
            metadata = regulation_metadata[regulation_id]
            evidence = sorted(
                list(entry["evidence"]), key=lambda item: item.score, reverse=True
            )[:2]
            related_regulations.append(
                RelatedRegulation(
                    regulation_id=regulation_id,
                    title=(metadata["title"] or f"Regulation #{regulation_id}"),
                    category=metadata["category"],
                    similarity_score=float(entry["score"]),
                    evidence=evidence,
                )
            )

        logger.info(
            f"Found {len(related_regulations)} related regulations",
            extra={
                "case_text_len": len(payload.case_text),
                "fragments_count": len(fragments),
                "total_candidates": len(payload.regulations),
                "matches": len(related_regulations),
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
