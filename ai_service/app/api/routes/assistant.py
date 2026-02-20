from __future__ import annotations

import re

from fastapi import APIRouter

from app.api.schemas.requests import (
    AnalyzeCaseRequest,
    ChatRequest,
    SummarizeDocumentRequest,
)
from app.api.schemas.responses import (
    AnalyzeCaseResponse,
    ChatResponse,
    SummarizeDocumentResponse,
)

router = APIRouter()


def _safe_summary(text: str, limit: int = 320) -> str:
    normalized = " ".join((text or "").split()).strip()
    if not normalized:
        return "No content provided."
    if len(normalized) <= limit:
        return normalized
    return f"{normalized[:limit].rstrip()}..."


@router.post("/chat", response_model=ChatResponse)
async def chat(payload: ChatRequest) -> ChatResponse:
    prompt = (payload.message or "").strip()
    if not prompt:
        return ChatResponse(response="No message provided.", citations=[])

    return ChatResponse(
        response=(
            "Compatibility assistant response: "
            f"{_safe_summary(prompt, 260)}"
        ),
        citations=[],
    )


@router.post("/analyze-case", response_model=AnalyzeCaseResponse)
async def analyze_case(payload: AnalyzeCaseRequest) -> AnalyzeCaseResponse:
    summary = _safe_summary(
        f"{payload.title}. {payload.description}. Case type: {payload.case_type}. "
        f"Status: {payload.status}. Jurisdiction: {payload.court_jurisdiction}.",
        360,
    )
    return AnalyzeCaseResponse(
        summary=summary,
        strengths=[
            "Facts are clearly documented in the provided narrative.",
            "Case framing is structured with explicit type and status.",
        ],
        weaknesses=[
            "Detailed documentary evidence is not fully enumerated in this request.",
            "Timeline precision may require hearing/event dates for stronger analysis.",
        ],
        recommendedStrategy=(
            "Collect and organize key supporting documents, align facts with applicable "
            "regulations, and prioritize strongest claims for verification."
        ),
        successProbability=0.65,
        predictedTimeline="3-6 months (estimate)",
    )


@router.post("/summarize-document", response_model=SummarizeDocumentResponse)
async def summarize_document(
    payload: SummarizeDocumentRequest,
) -> SummarizeDocumentResponse:
    content = payload.content or ""
    summary = _safe_summary(content, 400)

    # Lightweight entity heuristic for compatibility mode.
    tokens = re.findall(r"[A-Za-z\u0600-\u06FF][A-Za-z0-9\u0600-\u06FF_-]{2,}", content)
    entities: list[str] = []
    for token in tokens:
        value = token.strip()
        if value not in entities:
            entities.append(value)
        if len(entities) >= 8:
            break

    return SummarizeDocumentResponse(
        summary=summary,
        keyEntities=entities,
        effectiveDate=None,
        clauses=[],
    )
