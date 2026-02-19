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


class RegulationExtractResponse(BaseModel):
    status: str
    source_url: str
    final_url: Optional[str] = None
    etag: Optional[str] = None
    last_modified: Optional[str] = None
    content_type: Optional[str] = None
    extraction_method: str
    extracted_text: Optional[str] = None
    normalized_text_hash: Optional[str] = None
    raw_html: Optional[str] = None
    ocr_provider_used: str = "none"
    fallback_stage: str = "none"
    warnings: List[str] = []
    error_code: Optional[str] = None
