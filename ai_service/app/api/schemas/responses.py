from __future__ import annotations

from typing import List, Optional
from pydantic import BaseModel


class EmbeddingResponse(BaseModel):
    embeddings: List[List[float]]
    dimension: int
    count: int


class SimilarityResultItem(BaseModel):
    doc: str
    score: float


class SimilarityResponse(BaseModel):
    results: List[List[SimilarityResultItem]]


class RelatedRegulation(BaseModel):
    """A regulation matched to a case with similarity score."""
    regulation_id: int
    title: str
    category: Optional[str] = None
    similarity_score: float


class FindRelatedResponse(BaseModel):
    """
    Response from AI service with regulations related to a case.
    Returned by POST /similarity/find-related endpoint.
    """
    related_regulations: List[RelatedRegulation]
    query_length: int
    candidates_count: int
