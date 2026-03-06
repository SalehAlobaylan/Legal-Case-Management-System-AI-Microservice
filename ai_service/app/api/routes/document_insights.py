from __future__ import annotations

import re
from dataclasses import dataclass
from math import sqrt

from fastapi import APIRouter

from app.api.schemas.requests import DocumentCaseInsightsRequest
from app.api.schemas.responses import (
    DocumentCaseHighlight,
    DocumentCaseInsightsResponse,
)
from app.config import settings
from app.api.deps import get_embedding_service
from app.utils.logger import logger

router = APIRouter()


@dataclass
class _SentenceChunk:
    text: str
    start: int
    end: int


def _normalize_text(value: str) -> str:
    return " ".join((value or "").split()).strip()


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sqrt(sum(x * x for x in a)) or 1.0
    norm_b = sqrt(sum(x * x for x in b)) or 1.0
    return float(dot / (norm_a * norm_b))


def _split_sentence_chunks(text: str) -> list[_SentenceChunk]:
    chunks: list[_SentenceChunk] = []
    normalized = text.strip()
    if not normalized:
        return chunks

    split_pattern = re.compile(r"(?<=[\.\!\?؟؛;\n])\s+")
    parts = split_pattern.split(normalized)
    cursor = 0
    for raw in parts:
        sentence = raw.strip()
        if not sentence:
            continue
        start = normalized.find(sentence, cursor)
        if start < 0:
            start = cursor
        end = start + len(sentence)
        cursor = end
        chunks.append(_SentenceChunk(text=sentence, start=start, end=end))

    if not chunks:
        chunks.append(_SentenceChunk(text=normalized, start=0, end=len(normalized)))

    return chunks


def _truncate_chunks(
    chunks: list[_SentenceChunk], max_chars: int
) -> list[_SentenceChunk]:
    if max_chars <= 0:
        return []

    output: list[_SentenceChunk] = []
    consumed = 0
    for chunk in chunks:
        if consumed >= max_chars:
            break
        remaining = max_chars - consumed
        if remaining <= 0:
            break
        text = chunk.text[:remaining].strip()
        if not text:
            continue
        output.append(
            _SentenceChunk(text=text, start=chunk.start, end=chunk.start + len(text))
        )
        consumed += len(text)

    return output


@router.post("/documents/case-insights", response_model=DocumentCaseInsightsResponse)
async def document_case_insights(
    payload: DocumentCaseInsightsRequest,
) -> DocumentCaseInsightsResponse:
    case_text = _normalize_text(payload.case_text)
    document_text = _normalize_text(payload.document_text)
    if not case_text or not document_text:
        return DocumentCaseInsightsResponse(
            status="error",
            summary="",
            highlights=[],
            method="embedding_extractive_v1",
            warnings=["case_text and document_text are required."],
            error_code="validation_error",
        )

    max_chars = max(
        500,
        min(payload.max_source_chars, settings.insights_max_source_chars),
    )
    top_k = min(20, max(1, payload.top_k or settings.insights_default_top_k))

    chunks = _truncate_chunks(_split_sentence_chunks(document_text), max_chars)
    if not chunks:
        return DocumentCaseInsightsResponse(
            status="error",
            summary="",
            highlights=[],
            method="embedding_extractive_v1",
            warnings=["No candidate sentences available after preprocessing."],
            error_code="empty_document",
        )

    embedder = get_embedding_service()
    case_embedding = embedder.embed_query(case_text, normalize=True)
    sentence_embeddings = embedder.embed_documents(
        [chunk.text for chunk in chunks], normalize=True
    )

    scored = []
    for index, sentence_embedding in enumerate(sentence_embeddings):
        score = _cosine(case_embedding, sentence_embedding)
        scored.append((index, score))
    scored.sort(key=lambda item: item[1], reverse=True)

    highlight_indices = [index for index, _ in scored[:top_k]]
    highlights: list[DocumentCaseHighlight] = []
    for index in highlight_indices:
        chunk = chunks[index]
        score = next((s for idx, s in scored if idx == index), 0.0)
        highlights.append(
            DocumentCaseHighlight(
                snippet=chunk.text,
                score=score,
                sentence_start=chunk.start,
                sentence_end=chunk.end,
            )
        )

    max_summary_sentences = max(1, settings.insights_summary_sentences)
    summary_indices = sorted(highlight_indices[:max_summary_sentences])
    summary = " ".join(chunks[index].text for index in summary_indices).strip()
    if not summary:
        summary = chunks[scored[0][0]].text

    logger.info(
        "Generated document case insights",
        extra={
            "document_name": payload.document_name,
            "chunks_count": len(chunks),
            "top_k": top_k,
            "summary_len": len(summary),
            "highlights_count": len(highlights),
        },
    )

    return DocumentCaseInsightsResponse(
        status="ok",
        summary=summary,
        highlights=highlights,
        method="embedding_extractive_v1",
        warnings=[],
        error_code=None,
    )
