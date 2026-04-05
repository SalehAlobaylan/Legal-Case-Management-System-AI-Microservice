from __future__ import annotations

from typing import List, Optional
from pydantic import BaseModel, Field


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
    regulation_version_id: Optional[int] = None
    content_text: Optional[str] = None
    candidate_chunks: Optional[List["RegulationChunkCandidate"]] = None


class CaseFragment(BaseModel):
    fragment_id: str
    text: str
    source: str = "case"
    document_id: Optional[int] = None
    document_name: Optional[str] = None


class RegulationChunkCandidate(BaseModel):
    chunk_id: int
    chunk_index: int
    line_start: Optional[int] = None
    line_end: Optional[int] = None
    article_ref: Optional[str] = None
    text: str


class CaseProfile(BaseModel):
    case_id: Optional[int] = None
    title: Optional[str] = None
    description: Optional[str] = None
    case_type: Optional[str] = None
    status: Optional[str] = None
    court_jurisdiction: Optional[str] = None
    client_info: Optional[str] = None


class ScoringProfile(BaseModel):
    semantic_weight: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    support_weight: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    lexical_weight: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    category_weight: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    strict_min_final_score: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    strict_min_pair_score: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    strict_min_supporting_matches: Optional[int] = Field(default=None, ge=1, le=10)
    require_case_support: Optional[bool] = None


class FindRelatedRequest(BaseModel):
    """
    Request to find regulations related to a case.
    Used by backend API to trigger AI matching.
    """
    case_text: str
    regulations: List[RegulationCandidate]
    top_k: int = 10
    threshold: float = 0.3
    case_fragments: Optional[List[CaseFragment]] = None
    case_profile: Optional[CaseProfile] = None
    strict_mode: bool = True
    scoring_profile: Optional[ScoringProfile] = None
    # --- Pipeline toggles (optional per-request overrides) ---
    enable_llm_verification: Optional[bool] = None
    enable_cross_encoder: Optional[bool] = None
    enable_hyde: Optional[bool] = None
    enable_agentic: Optional[bool] = None
    enable_colbert: Optional[bool] = None


class RegulationExtractRequest(BaseModel):
    source_url: str
    if_none_match: Optional[str] = None
    if_modified_since: Optional[str] = None
    max_chars: Optional[int] = None


class ChatRegulationChunkContext(BaseModel):
    """A pre-retrieved regulation chunk passed from the backend."""
    chunk_id: int
    regulation_id: int
    regulation_title: str
    article_ref: Optional[str] = None
    content: str
    similarity_score: Optional[float] = None


class ChatDocumentChunkContext(BaseModel):
    """A pre-retrieved document chunk passed from the backend."""
    chunk_id: int
    document_id: int
    document_name: str
    content: str


class ChatCaseContext(BaseModel):
    """Case metadata for case-aware chat."""
    case_id: int
    title: str
    case_type: Optional[str] = None
    description: Optional[str] = None


class OrgCaseSummary(BaseModel):
    """Summary of a case in the user's organization."""
    case_id: int
    case_number: str
    title: str
    case_type: str
    status: str
    client_info: Optional[str] = None
    filing_date: Optional[str] = None
    next_hearing: Optional[str] = None


class ChatRequest(BaseModel):
    message: str
    context: Optional[dict] = None  # backward compat
    history: Optional[List[dict]] = None
    regulation_chunks: Optional[List[ChatRegulationChunkContext]] = None
    document_chunks: Optional[List[ChatDocumentChunkContext]] = None
    case_context: Optional[ChatCaseContext] = None
    org_cases: Optional[List[OrgCaseSummary]] = None
    language: Optional[str] = None  # "ar" or "en", auto-detect if omitted
    session_id: Optional[str] = None
    stream: bool = False


class AnalyzeCaseRequest(BaseModel):
    title: str
    description: str = ""
    case_type: str = "general"
    status: str = "open"
    court_jurisdiction: str = ""


class SummarizeDocumentRequest(BaseModel):
    content: str
    file_name: str = "document"


class DocumentCaseInsightsRequest(BaseModel):
    case_text: str
    document_text: str
    document_name: str = "document"
    top_k: int = 5
    max_source_chars: int = 15000


class RegulationSummaryAnalysisRequest(BaseModel):
    regulation_text: str
    regulation_title: str = "regulation"
    source_metadata: Optional[dict] = None
    language_code: str = "ar"
    max_source_chars: int = 40000


class RegulationAmendmentImpactRequest(BaseModel):
    regulation_title: str = "regulation"
    old_text: str
    new_text: str
    from_version_label: str = "old"
    to_version_label: str = "new"
    diff_summary: Optional[dict] = None
    language_code: str = "ar"
    max_source_chars: int = 40000
