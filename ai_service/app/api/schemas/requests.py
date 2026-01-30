from __future__ import annotations

from typing import List, Optional
from pydantic import BaseModel


class EmbedRequest(BaseModel):
    texts: List[str]
    normalize: bool = True


class SimilarityRequest(BaseModel):
    queries: List[str]
    corpus: List[str]
    top_k: int = 5


class RegulationCandidate(BaseModel):
    """Represents a regulation from the backend database."""
    id: int
    title: str
    category: Optional[str] = None
    content_text: Optional[str] = None


class FindRelatedRequest(BaseModel):
    """
    Request to find regulations related to a case.
    Used by backend API to trigger AI matching.
    """
    case_text: str
    regulations: List[RegulationCandidate]
    top_k: int = 10
    threshold: float = 0.3
