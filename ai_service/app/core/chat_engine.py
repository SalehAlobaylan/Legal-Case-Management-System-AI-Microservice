"""
Chat engine for legal Q&A with RAG context.

Receives pre-retrieved regulation/document chunks from the backend,
builds a bilingual system prompt with legal context, and generates
responses via Gemini with optional streaming.

Feature-flagged via settings.chat_enabled. Requires Gemini to be
configured (reuses the same API key / model as llm_verifier and hyde).
"""

from __future__ import annotations

import asyncio
import json
import queue as _queue
import re
from typing import Any, AsyncGenerator, Optional

from app.config import settings
from app.utils.logger import logger

# Maximum allowed length for a single user message (characters)
MAX_MESSAGE_LENGTH = 10_000
# Maximum allowed length for a single history message (characters)
MAX_HISTORY_MESSAGE_LENGTH = 5_000

try:
    import google.generativeai as genai  # type: ignore[import-untyped]
except ImportError:
    genai = None  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Language detection (reused pattern from hyde.py)
# ---------------------------------------------------------------------------

def _detect_arabic(text: str) -> bool:
    """Heuristic: if >30% of alpha chars are Arabic, treat as Arabic."""
    arabic_count = sum(1 for c in text if "\u0600" <= c <= "\u06FF")
    alpha_count = sum(1 for c in text if c.isalpha()) or 1
    return arabic_count / alpha_count > 0.3


# ---------------------------------------------------------------------------
# System prompts
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT_AR = """أنت مساعد قانوني ذكي لمنصة صلة القانونية. تساعد المحامين والمستشارين القانونيين في المملكة العربية السعودية.

## التعليمات:
- لديك إمكانية الوصول إلى بيانات قضايا المستخدم الخاصة بمؤسسته (في قسم "قضايا المؤسسة" أدناه)، والأنظمة واللوائح، ووثائق القضايا.
- عند سؤال المستخدم عن قضاياه أو قضايا مؤسسته، أجب بناءً على بيانات القضايا المقدمة أدناه.
- عند الاستشهاد بنظام، اذكر اسم النظام ورقم المادة بوضوح.
- إذا لم تجد معلومات كافية في السياق المقدم، قل ذلك بوضوح ولا تختلق معلومات.
- أجب بشكل مختصر ودقيق وعملي.
- استخدم اللغة العربية الفصحى في إجاباتك.

## تنويه قانوني:
هذه المعلومات للاطلاع والاسترشاد فقط ولا تعتبر استشارة قانونية رسمية. يرجى الرجوع إلى محامٍ مختص للحصول على مشورة قانونية.

## السياق:
{context_block}"""

_SYSTEM_PROMPT_EN = """You are a legal AI assistant for the Silah Legal platform. You assist lawyers and legal consultants in Saudi Arabia.

## Instructions:
- You have access to the user's organization case data (in the "Organization Cases" section below), regulations, and case documents.
- When the user asks about their cases or their organization's cases, answer based on the case data provided below.
- When citing a regulation, clearly state the regulation name and article number.
- If insufficient information is available in the provided context, state that clearly and do not fabricate information.
- Be concise, accurate, and practical.

## Legal Disclaimer:
This information is for reference and guidance only and does not constitute formal legal advice. Please consult a qualified lawyer for legal counsel.

## Context:
{context_block}"""


# ---------------------------------------------------------------------------
# Context assembly
# ---------------------------------------------------------------------------

def build_context_block(
    regulation_chunks: list[dict[str, Any]] | None = None,
    document_chunks: list[dict[str, Any]] | None = None,
    case_context: dict[str, Any] | None = None,
    org_cases: list[dict[str, Any]] | None = None,
) -> str:
    """Format retrieved chunks into a context block for the LLM prompt."""
    parts: list[str] = []
    max_chars = settings.chat_max_context_chars

    # Organization cases — always include so the LLM can answer
    # questions like "how many commercial cases do I have?"
    if org_cases:
        parts.append("### Organization Cases:\n")
        # Group by type for readability
        by_type: dict[str, list[dict[str, Any]]] = {}
        for c in org_cases:
            ct = c.get("case_type", "other")
            by_type.setdefault(ct, []).append(c)

        total = len(org_cases)
        summary_parts = [f"Total: {total} case(s)"]
        for ct, lst in sorted(by_type.items()):
            summary_parts.append(f"  {ct}: {len(lst)}")
        parts.append(" | ".join(summary_parts) + "\n")

        for c in org_cases:
            line = (
                f"- [{c.get('case_number', '?')}] {c.get('title', 'Untitled')} "
                f"| Type: {c.get('case_type', '?')} | Status: {c.get('status', '?')}"
            )
            if c.get("client_info"):
                line += f" | Client: {c['client_info']}"
            if c.get("filing_date"):
                line += f" | Filed: {c['filing_date']}"
            if c.get("next_hearing"):
                line += f" | Next Hearing: {c['next_hearing']}"
            parts.append(line)
        parts.append("")  # blank line separator

    # Active case context (when user opened chat from a specific case)
    if case_context:
        case_block = f"### Active Case: {case_context.get('title', 'N/A')}\n"
        if case_context.get("case_type"):
            case_block += f"Type: {case_context['case_type']}\n"
        if case_context.get("description"):
            desc = case_context["description"][:1000]
            case_block += f"Description: {desc}\n"
        parts.append(case_block)

    # Regulation chunks
    if regulation_chunks:
        parts.append("### Regulations:\n")
        for chunk in regulation_chunks:
            article = chunk.get("article_ref") or ""
            title = chunk.get("regulation_title", "")
            content = chunk.get("content", "")
            header = f"[{title}"
            if article:
                header += f" - {article}"
            header += f"] (ID: {chunk.get('regulation_id', '?')})"
            parts.append(f"{header}\n{content}\n")

    # Document chunks
    if document_chunks:
        parts.append("### Case Documents:\n")
        for chunk in document_chunks:
            name = chunk.get("document_name", "Document")
            content = chunk.get("content", "")
            parts.append(f"[{name}]\n{content}\n")

    context = "\n".join(parts)
    if len(context) > max_chars:
        context = context[:max_chars] + "\n...(truncated)"
    return context if context.strip() else "No context provided."


def _build_messages(
    message: str,
    history: list[dict[str, str]] | None,
    language: str,
    context_block: str,
) -> list[dict[str, str]]:
    """Build the Gemini conversation as a list of content parts."""
    system_template = _SYSTEM_PROMPT_AR if language == "ar" else _SYSTEM_PROMPT_EN
    system_prompt = system_template.format(context_block=context_block)

    messages: list[dict[str, str]] = []

    # Gemini handles system instructions via the model parameter,
    # but for conversation history we use role-based content.
    # We'll prepend system as the first "user" turn with model ack.
    messages.append({"role": "user", "parts": [system_prompt]})
    messages.append({"role": "model", "parts": ["Understood. I will answer based only on the provided context and cite specific regulations."]})

    # Conversation history (trimmed to max turns, capped per-message)
    max_turns = settings.chat_max_history_turns
    if history:
        trimmed = history[-max_turns * 2:]  # each turn = user + assistant
        for msg in trimmed:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if len(content) > MAX_HISTORY_MESSAGE_LENGTH:
                content = content[:MAX_HISTORY_MESSAGE_LENGTH] + "…(truncated)"
            if role == "user":
                messages.append({"role": "user", "parts": [content]})
            elif role in ("assistant", "model"):
                messages.append({"role": "model", "parts": [content]})

    # Current user message
    messages.append({"role": "user", "parts": [message]})

    return messages


# ---------------------------------------------------------------------------
# Citation extraction
# ---------------------------------------------------------------------------

def _extract_citations(
    response_text: str,
    regulation_chunks: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """
    Match regulation chunks referenced in the response.

    Looks for regulation titles or article references mentioned in the
    response text and maps them back to the provided chunks.
    """
    if not regulation_chunks:
        return []

    citations: list[dict[str, Any]] = []
    seen_reg_ids: set[int] = set()

    for chunk in regulation_chunks:
        reg_id = chunk.get("regulation_id")
        if reg_id in seen_reg_ids:
            continue

        title = chunk.get("regulation_title", "")
        article = chunk.get("article_ref", "")

        # Check if the regulation title or article ref appears in the response
        referenced = False
        if title and title in response_text:
            referenced = True
        if article and article in response_text:
            referenced = True

        if referenced:
            seen_reg_ids.add(reg_id)
            citations.append({
                "regulation_id": reg_id,
                "regulation_title": title,
                "article_ref": article or None,
                "chunk_id": chunk.get("chunk_id"),
            })

    return citations


# ---------------------------------------------------------------------------
# Preflight checks
# ---------------------------------------------------------------------------

def _preflight() -> str | None:
    """Return an error message if Gemini is not available, else None."""
    if not settings.chat_enabled:
        return "Chat is currently disabled."
    if genai is None:
        return "Chat service unavailable (missing google-generativeai package)."
    if not settings.gemini_api_key:
        return "Chat service unavailable (no API key configured)."
    return None


def _get_model() -> Any:
    """Configure and return a Gemini GenerativeModel."""
    genai.configure(api_key=settings.gemini_api_key)
    return genai.GenerativeModel(settings.gemini_model)


def _generation_config() -> Any:
    """Return Gemini generation config for chat."""
    return genai.GenerationConfig(
        temperature=settings.chat_temperature,
        max_output_tokens=settings.chat_max_output_tokens,
    )


# ---------------------------------------------------------------------------
# Non-streaming chat
# ---------------------------------------------------------------------------

async def chat_response(
    message: str,
    history: list[dict[str, str]] | None = None,
    regulation_chunks: list[dict[str, Any]] | None = None,
    document_chunks: list[dict[str, Any]] | None = None,
    case_context: dict[str, Any] | None = None,
    org_cases: list[dict[str, Any]] | None = None,
    language: str | None = None,
) -> dict[str, Any]:
    """
    Generate a complete (non-streaming) chat response.

    Returns dict with: response, citations, language, disclaimer
    """
    error = _preflight()
    if error:
        return {
            "response": error,
            "citations": [],
            "language": language or "ar",
            "disclaimer": "",
        }

    if not language:
        language = "ar" if _detect_arabic(message) else "en"

    if len(message) > MAX_MESSAGE_LENGTH:
        return {
            "response": "Message is too long. Please shorten your question and try again.",
            "citations": [],
            "language": language,
            "disclaimer": "",
        }

    context_block = build_context_block(regulation_chunks, document_chunks, case_context, org_cases)
    messages = _build_messages(message, history, language, context_block)

    try:
        model = _get_model()
        response = await asyncio.wait_for(
            asyncio.to_thread(
                model.generate_content,
                messages,
                generation_config=_generation_config(),
            ),
            timeout=settings.gemini_timeout_seconds * 2,  # longer timeout for chat
        )

        response_text = (response.text or "").strip()
        if not response_text:
            response_text = "I could not generate a response. Please try rephrasing your question."

        citations = _extract_citations(response_text, regulation_chunks)

        disclaimer = (
            "هذه المعلومات للاطلاع فقط ولا تعتبر استشارة قانونية رسمية."
            if language == "ar"
            else "This information is for reference only and does not constitute formal legal advice."
        )

        logger.info(
            "chat:response",
            extra={
                "language": language,
                "response_len": len(response_text),
                "citations_count": len(citations),
                "has_case_context": case_context is not None,
                "regulation_chunks_count": len(regulation_chunks) if regulation_chunks else 0,
            },
        )

        return {
            "response": response_text,
            "citations": citations,
            "language": language,
            "disclaimer": disclaimer,
        }

    except asyncio.TimeoutError:
        logger.warning("Chat response timed out")
        return {
            "response": "The request timed out. Please try again.",
            "citations": [],
            "language": language,
            "disclaimer": "",
        }
    except Exception as exc:
        logger.error(f"Chat response failed: {type(exc).__name__}: {exc}")
        return {
            "response": "An error occurred while generating the response. Please try again.",
            "citations": [],
            "language": language,
            "disclaimer": "",
        }


# ---------------------------------------------------------------------------
# Streaming chat
# ---------------------------------------------------------------------------

async def stream_chat_response(
    message: str,
    history: list[dict[str, str]] | None = None,
    regulation_chunks: list[dict[str, Any]] | None = None,
    document_chunks: list[dict[str, Any]] | None = None,
    case_context: dict[str, Any] | None = None,
    org_cases: list[dict[str, Any]] | None = None,
    language: str | None = None,
) -> AsyncGenerator[dict[str, Any], None]:
    """
    Stream a chat response as SSE-compatible events.

    Yields dicts of shape:
      {"type": "token", "content": "..."}
      {"type": "citations", "citations": [...]}
      {"type": "done"}
      {"type": "error", "message": "..."}
    """
    error = _preflight()
    if error:
        yield {"type": "error", "message": error}
        return

    if len(message) > MAX_MESSAGE_LENGTH:
        yield {"type": "error", "message": "Message is too long. Please shorten your question and try again."}
        return

    if not language:
        language = "ar" if _detect_arabic(message) else "en"

    context_block = build_context_block(regulation_chunks, document_chunks, case_context, org_cases)
    messages = _build_messages(message, history, language, context_block)

    try:
        model = _get_model()

        # Gemini's generate_content with stream=True returns an iterator
        # We run it in a thread since the sync iterator blocks
        response = await asyncio.to_thread(
            model.generate_content,
            messages,
            generation_config=_generation_config(),
            stream=True,
        )

        full_response = ""

        # Stream chunks one at a time using a thread-safe queue so tokens
        # are yielded to the caller as soon as Gemini produces them.
        token_queue: _queue.Queue[str | None] = _queue.Queue()

        def _iter_chunks_into_queue():
            """Run in a worker thread — pushes tokens into a thread-safe queue."""
            try:
                for chunk in response:
                    text = chunk.text or ""
                    if text:
                        token_queue.put(text)
            except Exception as exc:
                token_queue.put(exc)  # type: ignore[arg-type]
            finally:
                token_queue.put(None)  # sentinel

        # Start producing tokens in a background thread
        loop = asyncio.get_running_loop()
        loop.run_in_executor(None, _iter_chunks_into_queue)

        # Consume tokens as they arrive
        while True:
            # Yield control briefly so FastAPI can flush previous token
            item = await asyncio.to_thread(token_queue.get)
            if item is None:
                break
            if isinstance(item, Exception):
                raise item
            full_response += item
            yield {"type": "token", "content": item}

        if not full_response.strip():
            yield {"type": "token", "content": "I could not generate a response. Please try rephrasing your question."}
            full_response = "I could not generate a response. Please try rephrasing your question."

        # Extract and emit citations
        citations = _extract_citations(full_response, regulation_chunks)
        if citations:
            yield {"type": "citations", "citations": citations}

        yield {"type": "done"}

        logger.info(
            "chat:stream_complete",
            extra={
                "language": language,
                "response_len": len(full_response),
                "citations_count": len(citations),
            },
        )

    except asyncio.TimeoutError:
        logger.warning("Chat stream timed out")
        yield {"type": "error", "message": "The request timed out. Please try again."}
    except Exception as exc:
        logger.error(f"Chat stream failed: {type(exc).__name__}: {exc}")
        yield {"type": "error", "message": "An error occurred while generating the response."}
