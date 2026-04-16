"""
Assistant routes: chat, case analysis, and document summarization.

The /chat endpoint uses RAG context (regulation/document chunks) passed
from the backend to generate grounded, cited responses via Gemini.
The /chat/stream endpoint provides the same functionality with SSE streaming.
"""

from __future__ import annotations

import asyncio
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
from app.config import settings
from app.core import chat_engine
from app.utils.logger import logger

try:
    import google.generativeai as genai  # type: ignore[import-untyped]
except ImportError:
    genai = None  # type: ignore[assignment]

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
# Case analysis — LLM-backed, with heuristic fallback
# ---------------------------------------------------------------------------


def _detect_ar(text: str) -> bool:
    """Heuristic: if >30% of alpha chars are Arabic, treat as Arabic."""
    arabic_count = sum(1 for c in text if "\u0600" <= c <= "\u06FF")
    alpha_count = sum(1 for c in text if c.isalpha()) or 1
    return arabic_count / alpha_count > 0.3


_ANALYZE_PROMPT_AR = """أنت مساعد قانوني خبير في الأنظمة السعودية. سيُقدَّم لك وصف قضية قانونية، ومهمتك إنتاج تحليل عملي موجز باللغة العربية الفصحى.

## بيانات القضية
- العنوان: {title}
- نوع القضية: {case_type}
- الحالة: {status}
- الجهة القضائية: {court_jurisdiction}
- الوصف:
{description}

## المطلوب
أعد استجابة بصيغة JSON صالحة فقط (بدون أي نص خارج JSON) وفق المخطط التالي بالضبط:

{{
  "summary": "ملخص تنفيذي للقضية في 3-5 جمل، يوضح الوقائع الأساسية والأطراف والنقطة المحورية.",
  "strengths": ["نقطة قوة 1", "نقطة قوة 2", "نقطة قوة 3"],
  "weaknesses": ["نقطة ضعف 1", "نقطة ضعف 2"],
  "risks": ["مخاطرة 1", "مخاطرة 2"],
  "recommendations": ["توصية عملية 1", "توصية عملية 2", "توصية عملية 3"],
  "recommendedStrategy": "استراتيجية مقترحة شاملة في جملة أو جملتين.",
  "successProbability": 0.65,
  "predictedTimeline": "نطاق زمني متوقع (مثلاً 3-6 أشهر)"
}}

## قواعد
- كل عنصر في المصفوفات يجب أن يكون جملة عربية واضحة ومحددة مستندة إلى وقائع القضية لا إلى عبارات عامة.
- successProbability رقم عشري بين 0 و 1.
- إن كانت المعلومات شحيحة، أذكر ذلك صراحة في نقاط الضعف أو المخاطر.
- لا تُصدر JSON محاطًا بعلامات markdown. فقط JSON نقي."""

_ANALYZE_PROMPT_EN = """You are an expert legal assistant specialized in Saudi regulations. You will receive a legal case description; produce a concise, practical analysis.

## Case Data
- Title: {title}
- Case type: {case_type}
- Status: {status}
- Jurisdiction: {court_jurisdiction}
- Description:
{description}

## Required Output
Return a single valid JSON object only (no prose outside JSON) matching this exact schema:

{{
  "summary": "Executive summary in 3-5 sentences covering core facts, parties, and pivotal legal question.",
  "strengths": ["concrete strength 1", "concrete strength 2"],
  "weaknesses": ["concrete weakness 1", "concrete weakness 2"],
  "risks": ["risk 1", "risk 2"],
  "recommendations": ["actionable recommendation 1", "actionable recommendation 2"],
  "recommendedStrategy": "overall strategy in one or two sentences",
  "successProbability": 0.65,
  "predictedTimeline": "e.g. 3-6 months"
}}

## Rules
- Every array item must be a specific sentence grounded in the case facts, not a generic cliche.
- successProbability is a decimal between 0 and 1.
- If information is thin, state that explicitly in weaknesses or risks.
- Do not wrap the JSON in markdown code fences. Return pure JSON only."""


def _extract_json(raw: str) -> dict | None:
    """Extract the first JSON object from the model output, tolerating code fences."""
    if not raw:
        return None
    text = raw.strip()
    # Strip ```json ... ``` fences if present
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, flags=re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    # Find the outermost { ... } block
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        return json.loads(text[start : end + 1])
    except Exception:
        return None


def _coerce_str_list(value) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        if isinstance(item, str):
            s = item.strip()
            if s:
                out.append(s)
        elif isinstance(item, dict):
            # Accept {title: "...", description: "..."} shapes; flatten.
            title = str(item.get("title") or "").strip()
            desc = str(item.get("description") or "").strip()
            combined = " — ".join([p for p in (title, desc) if p])
            if combined:
                out.append(combined)
    return out


def _fallback_response(payload: AnalyzeCaseRequest, is_arabic: bool) -> AnalyzeCaseResponse:
    """Deterministic fallback used when Gemini is unavailable."""
    summary = _safe_summary(
        f"{payload.title}. {payload.description}. "
        f"Case type: {payload.case_type}. Status: {payload.status}. "
        f"Jurisdiction: {payload.court_jurisdiction}.",
        360,
    )
    if is_arabic:
        return AnalyzeCaseResponse(
            summary=summary,
            strengths=[
                "الوقائع موثقة بوضوح في السرد المقدم.",
                "إطار القضية محدد بنوع وحالة صريحين.",
            ],
            weaknesses=[
                "الأدلة المستندية التفصيلية غير مُعدَّدة بالكامل في هذا الطلب.",
                "قد تتطلب الدقة الزمنية تواريخ الجلسات والأحداث لتحليل أقوى.",
            ],
            risks=[
                "احتمال وجود نقص في الأدلة الداعمة لبعض الادعاءات.",
                "قد تؤثر فجوات الجدول الزمني على قوة الموقف القانوني.",
            ],
            recommendations=[
                "جمع وتنظيم المستندات الداعمة الرئيسية.",
                "مطابقة الوقائع مع الأنظمة المعمول بها ذات الصلة.",
                "إعطاء الأولوية لأقوى الادعاءات للتحقق منها.",
            ],
            recommendedStrategy=(
                "جمع وتنظيم المستندات الداعمة، ومطابقة الوقائع مع الأنظمة المطبقة، "
                "وإعطاء الأولوية لأقوى الادعاءات للتحقق."
            ),
            successProbability=0.65,
            predictedTimeline="3-6 أشهر (تقديري)",
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
        risks=[
            "Possible gaps in supporting evidence for certain claims.",
            "Timeline gaps may weaken the legal position.",
        ],
        recommendations=[
            "Collect and organize key supporting documents.",
            "Align facts with applicable regulations.",
            "Prioritize strongest claims for verification.",
        ],
        recommendedStrategy=(
            "Collect and organize key supporting documents, align facts with applicable "
            "regulations, and prioritize strongest claims for verification."
        ),
        successProbability=0.65,
        predictedTimeline="3-6 months (estimate)",
    )


@router.post("/analyze-case", response_model=AnalyzeCaseResponse)
async def analyze_case(payload: AnalyzeCaseRequest) -> AnalyzeCaseResponse:
    description_text = (payload.description or "").strip()
    combined_for_lang = f"{payload.title or ''} {description_text}"
    is_arabic = _detect_ar(combined_for_lang)

    # Preflight — if Gemini isn't available, fall back deterministically.
    if genai is None or not settings.gemini_api_key:
        logger.info("analyze-case:fallback reason=gemini_unavailable")
        return _fallback_response(payload, is_arabic)

    if not description_text:
        # Nothing to analyze — fallback echoes the shape.
        return _fallback_response(payload, is_arabic)

    prompt_tmpl = _ANALYZE_PROMPT_AR if is_arabic else _ANALYZE_PROMPT_EN
    prompt = prompt_tmpl.format(
        title=payload.title or "—",
        case_type=payload.case_type or "—",
        status=payload.status or "—",
        court_jurisdiction=payload.court_jurisdiction or "—",
        description=description_text[:8000],  # guard against runaway inputs
    )

    # Frontend axios budget for analyze-case is 60s. Keep Gemini under that
    # so the route still has time to build + return the fallback if it stalls.
    llm_timeout = min(max(settings.gemini_timeout_seconds, 30), 45)

    try:
        genai.configure(api_key=settings.gemini_api_key)
        model = genai.GenerativeModel(settings.gemini_model)
        response = await asyncio.wait_for(
            asyncio.to_thread(
                model.generate_content,
                prompt,
                generation_config=genai.GenerationConfig(
                    temperature=0.3,
                    max_output_tokens=1536,
                ),
            ),
            timeout=llm_timeout,
        )
        raw = (getattr(response, "text", "") or "").strip()
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "analyze-case:gemini_error err={} title={!r}",
            str(exc),
            (payload.title or "")[:120],
        )
        return _fallback_response(payload, is_arabic)

    parsed = _extract_json(raw)
    if not parsed:
        logger.warning(
            "analyze-case:parse_error raw_preview={!r}",
            raw[:200],
        )
        return _fallback_response(payload, is_arabic)

    fallback = _fallback_response(payload, is_arabic)
    try:
        success_prob_val = parsed.get("successProbability", fallback.successProbability)
        try:
            success_prob = float(success_prob_val)
        except (TypeError, ValueError):
            success_prob = fallback.successProbability
        success_prob = max(0.0, min(1.0, success_prob))

        summary = str(parsed.get("summary") or "").strip() or fallback.summary
        strategy = str(parsed.get("recommendedStrategy") or "").strip() or fallback.recommendedStrategy
        timeline = str(parsed.get("predictedTimeline") or "").strip() or fallback.predictedTimeline

        strengths = _coerce_str_list(parsed.get("strengths")) or fallback.strengths
        weaknesses = _coerce_str_list(parsed.get("weaknesses")) or fallback.weaknesses
        risks = _coerce_str_list(parsed.get("risks")) or fallback.risks
        recommendations = _coerce_str_list(parsed.get("recommendations")) or fallback.recommendations

        return AnalyzeCaseResponse(
            summary=summary,
            strengths=strengths,
            weaknesses=weaknesses,
            risks=risks,
            recommendations=recommendations,
            recommendedStrategy=strategy,
            successProbability=success_prob,
            predictedTimeline=timeline,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("analyze-case:coerce_error err={}", str(exc))
        return fallback


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
