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


class MatchEvidence(BaseModel):
    fragment_id: str
    source: str
    document_id: Optional[int] = None
    document_name: Optional[str] = None
    score: float


class RelatedRegulation(BaseModel):
    """A regulation matched to a case with similarity score."""
    regulation_id: int
    title: str
    category: Optional[str] = None
    similarity_score: float
    evidence: Optional[List[MatchEvidence]] = None


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


class DocumentExtractResponse(BaseModel):
    status: str
    file_name: str
    content_type: Optional[str] = None
    extraction_method: str
    extracted_text: Optional[str] = None
    normalized_text_hash: Optional[str] = None
    ocr_provider_used: str = "none"
    fallback_stage: str = "none"
    warnings: List[str] = []
    error_code: Optional[str] = None


class ChatResponse(BaseModel):
    response: str
    citations: List[dict] = []


class AnalyzeCaseResponse(BaseModel):
    summary: str
    strengths: List[str]
    weaknesses: List[str]
    recommendedStrategy: str
    successProbability: float
    predictedTimeline: str


class SummarizeDocumentResponse(BaseModel):
    summary: str
    keyEntities: List[str]
    effectiveDate: Optional[str] = None
    clauses: List[dict] = []


class DocumentCaseHighlight(BaseModel):
    snippet: str
    score: float
    sentence_start: int
    sentence_end: int


class DocumentCaseInsightsResponse(BaseModel):
    status: str
    summary: str
    highlights: List[DocumentCaseHighlight] = []
    method: str = "embedding_extractive_v1"
    warnings: List[str] = []
    error_code: Optional[str] = None


class RegulationInsightBullet(BaseModel):
    title: str
    description: str
    severity: Optional[str] = None


class RegulationKeyDate(BaseModel):
    label: str
    value: str
    source: Optional[str] = None


class RegulationCitation(BaseModel):
    snippet: str
    section_ref: Optional[str] = None
    relevance: Optional[float] = None


class RegulationSummaryAnalysisResponse(BaseModel):
    status: str
    summary: str
    obligations: List[RegulationInsightBullet] = []
    risk_flags: List[RegulationInsightBullet] = []
    key_dates: List[RegulationKeyDate] = []
    citations: List[RegulationCitation] = []
    method: str = "regulation_summary_analysis_v1"
    warnings: List[str] = []
    error_code: Optional[str] = None


class RegulationAmendmentImpactResponse(BaseModel):
    status: str
    what_changed: List[RegulationInsightBullet] = []
    legal_impact: List[RegulationInsightBullet] = []
    affected_parties: List[RegulationInsightBullet] = []
    citations: List[RegulationCitation] = []
    method: str = "regulation_amendment_impact_v1"
    warnings: List[str] = []
    error_code: Optional[str] = None
