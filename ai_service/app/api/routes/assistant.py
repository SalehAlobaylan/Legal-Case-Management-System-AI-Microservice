"""
Assistant routes: chat, case analysis, and document summarization.

The /chat endpoint uses RAG context (regulation/document chunks) passed
from the backend to generate grounded, cited responses via Gemini.
The /chat/stream endpoint provides the same functionality with SSE streaming.
"""

from __future__ import annotations

import json
import re

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from app.api.schemas.requests import (
    AnalyzeCaseRequest,
    ChatRequest,
    SummarizeDocumentRequest,
)
from app.api.schemas.responses import (
    AnalyzeCaseResponse,
    ChatCitation,
    ChatResponse,
    SummarizeDocumentResponse,
)
from app.core import chat_engine

router = APIRouter()


def _safe_summary(text: str, limit: int = 320) -> str:
    normalized = " ".join((text or "").split()).strip()
    if not normalized:
        return "No content provided."
    if len(normalized) <= limit:
        return normalized
    return f"{normalized[:limit].rstrip()}..."


# ---------------------------------------------------------------------------
# Chat endpoints
# ---------------------------------------------------------------------------


@router.post("/chat", response_model=ChatResponse)
async def chat(payload: ChatRequest) -> ChatResponse:
    """Non-streaming chat endpoint with RAG context."""
    prompt = (payload.message or "").strip()
    if not prompt:
        return ChatResponse(response="No message provided.", citations=[])

    result = await chat_engine.chat_response(
        message=prompt,
        history=payload.history,
        regulation_chunks=[c.model_dump() for c in payload.regulation_chunks] if payload.regulation_chunks else None,
        document_chunks=[c.model_dump() for c in payload.document_chunks] if payload.document_chunks else None,
        case_context=payload.case_context.model_dump() if payload.case_context else None,
        org_cases=[c.model_dump() for c in payload.org_cases] if payload.org_cases else None,
        language=payload.language,
    )

    citations = [
        ChatCitation(**c)
        for c in result.get("citations", [])
        if isinstance(c, dict) and c.get("regulation_id") and c.get("regulation_title")
    ]

    return ChatResponse(
        response=result["response"],
        citations=citations,
        language=result.get("language", "ar"),
        disclaimer=result.get("disclaimer", ""),
    )


@router.post("/chat/stream")
async def chat_stream(payload: ChatRequest):
    """SSE streaming chat endpoint with RAG context."""
    prompt = (payload.message or "").strip()
    if not prompt:
        async def _empty():
            yield f"data: {json.dumps({'type': 'error', 'message': 'No message provided.'})}\n\n"
            yield "data: [DONE]\n\n"
        return StreamingResponse(
            _empty(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    async def event_generator():
        async for event in chat_engine.stream_chat_response(
            message=prompt,
            history=payload.history,
            regulation_chunks=[c.model_dump() for c in payload.regulation_chunks] if payload.regulation_chunks else None,
            document_chunks=[c.model_dump() for c in payload.document_chunks] if payload.document_chunks else None,
            case_context=payload.case_context.model_dump() if payload.case_context else None,
            org_cases=[c.model_dump() for c in payload.org_cases] if payload.org_cases else None,
            language=payload.language,
        ):
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------------------------------------------------------------------------
# Case analysis (placeholder — kept for backward compat)
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Document summarization (placeholder — kept for backward compat)
# ---------------------------------------------------------------------------


@router.post("/summarize-document", response_model=SummarizeDocumentResponse)
async def summarize_document(
    payload: SummarizeDocumentRequest,
) -> SummarizeDocumentResponse:
    content = payload.content or ""
    summary = _safe_summary(content, 400)

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
