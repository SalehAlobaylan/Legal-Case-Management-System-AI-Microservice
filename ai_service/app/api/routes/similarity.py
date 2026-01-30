from __future__ import annotations

from fastapi import APIRouter

from app.core.similarity import SimilarityService
from app.api.schemas.requests import SimilarityRequest
from app.api.schemas.responses import SimilarityResponse, SimilarityResultItem

router = APIRouter()


@router.post("/similarity/", response_model=SimilarityResponse)
async def similarity_rank(payload: SimilarityRequest) -> SimilarityResponse:
    """
    POST /similarity/

    Request:
      {
        "queries": [...],
        "corpus": [...],
        "top_k": 3
      }

    Response:
      {
        "results": [
          [ {"doc": "...", "score": 0.9}, ... ],   # for query 1
          ...
        ]
      }
    """
    service = SimilarityService()
    ranked = service.rank(
        queries=payload.queries,
        corpus=payload.corpus,
        top_k=payload.top_k,
    )

    wrapped: list[list[SimilarityResultItem]] = []
    for per_query in ranked:
        items = [SimilarityResultItem(doc=doc, score=score) for doc, score in per_query]
        wrapped.append(items)

    return SimilarityResponse(results=wrapped)
