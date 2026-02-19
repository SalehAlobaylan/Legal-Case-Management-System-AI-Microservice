from __future__ import annotations

import hashlib
import ipaddress
import socket
from io import BytesIO
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup
from fastapi import APIRouter
from pypdf import PdfReader

from app.api.schemas.requests import RegulationExtractRequest
from app.api.schemas.responses import RegulationExtractResponse
from app.config import settings
from app.core.ocr.factory import create_ocr_provider
from app.utils.logger import logger

router = APIRouter()


def normalize_text(text: str) -> str:
    return " ".join((text or "").split()).strip()


def hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def is_private_host(hostname: str) -> bool:
    try:
        addresses = socket.getaddrinfo(hostname, None)
    except Exception:
        return True

    for item in addresses:
        ip = item[4][0]
        try:
            parsed = ipaddress.ip_address(ip)
        except ValueError:
            continue
        if (
            parsed.is_private
            or parsed.is_loopback
            or parsed.is_link_local
            or parsed.is_reserved
            or parsed.is_multicast
        ):
            return True

    return False


def is_whitelisted_url(source_url: str) -> bool:
    try:
        parsed = urlparse(source_url)
    except Exception:
        return False

    if parsed.scheme != "https" or not parsed.hostname:
        return False

    host = parsed.hostname.lower()
    allowed = any(
        host == domain or host.endswith(f".{domain}")
        for domain in settings.source_whitelist_domains
    )
    if not allowed:
        return False

    return not is_private_host(host)


def parse_html(content: bytes) -> tuple[str, str]:
    decoded = content.decode("utf-8", errors="replace")
    soup = BeautifulSoup(decoded, "html.parser")
    text = soup.get_text(separator=" ", strip=True)
    return decoded, text


def parse_pdf(content: bytes) -> str:
    reader = PdfReader(BytesIO(content))
    extracted = []
    for page in reader.pages:
        extracted.append(page.extract_text() or "")
    return "\n".join(extracted).strip()


def parse_plain_text(content: bytes) -> str:
    return content.decode("utf-8", errors="replace").strip()


@router.post("/regulations/extract", response_model=RegulationExtractResponse)
async def extract_regulation_content(
    payload: RegulationExtractRequest,
) -> RegulationExtractResponse:
    source_url = payload.source_url.strip()
    if not source_url:
        return RegulationExtractResponse(
            status="error",
            source_url=payload.source_url,
            extraction_method="none",
            error_code="empty_source_url",
            warnings=["Source URL is empty."],
        )

    if not is_whitelisted_url(source_url):
        return RegulationExtractResponse(
            status="error",
            source_url=source_url,
            extraction_method="none",
            error_code="source_not_allowed",
            warnings=["Source URL is not in the trusted whitelist or resolves to a private network."],
        )

    headers: dict[str, str] = {}
    if payload.if_none_match:
        headers["If-None-Match"] = payload.if_none_match
    if payload.if_modified_since:
        headers["If-Modified-Since"] = payload.if_modified_since

    try:
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=settings.extraction_timeout_seconds,
        ) as client:
            response = await client.get(source_url, headers=headers)
    except Exception as exc:
        return RegulationExtractResponse(
            status="error",
            source_url=source_url,
            extraction_method="none",
            error_code="fetch_failed",
            warnings=[f"Failed to fetch source URL: {exc}"],
        )

    final_url = str(response.url)
    etag = response.headers.get("etag")
    last_modified = response.headers.get("last-modified")
    content_type = (response.headers.get("content-type") or "").lower()

    if response.status_code == 304:
        return RegulationExtractResponse(
            status="not_modified",
            source_url=source_url,
            final_url=final_url,
            etag=etag,
            last_modified=last_modified,
            content_type=content_type or None,
            extraction_method="not_modified",
            extracted_text=None,
            normalized_text_hash=None,
        )

    if response.status_code >= 400:
        return RegulationExtractResponse(
            status="error",
            source_url=source_url,
            final_url=final_url,
            etag=etag,
            last_modified=last_modified,
            content_type=content_type or None,
            extraction_method="none",
            error_code=f"http_{response.status_code}",
            warnings=[f"Source returned HTTP {response.status_code}"],
        )

    content = response.content
    if len(content) > settings.extraction_max_bytes:
        return RegulationExtractResponse(
            status="error",
            source_url=source_url,
            final_url=final_url,
            etag=etag,
            last_modified=last_modified,
            content_type=content_type or None,
            extraction_method="none",
            error_code="payload_too_large",
            warnings=[f"Fetched payload exceeds {settings.extraction_max_bytes} bytes limit."],
        )

    parser_text = ""
    raw_html: str | None = None
    extraction_method = "parser_text"
    warnings: list[str] = []

    try:
        if "text/html" in content_type:
            raw_html, parser_text = parse_html(content)
            extraction_method = "parser_html"
        elif "application/pdf" in content_type or final_url.lower().endswith(".pdf"):
            parser_text = parse_pdf(content)
            extraction_method = "parser_pdf"
        elif content_type.startswith("text/"):
            parser_text = parse_plain_text(content)
            extraction_method = "parser_text"
        else:
            parser_text = parse_plain_text(content)
            extraction_method = "parser_fallback"
    except Exception as exc:
        warnings.append(f"Parser failed: {exc}")
        parser_text = ""

    parsed_length = len(normalize_text(parser_text))
    needs_ocr = (
        parsed_length < settings.ocr_min_text_chars
        and (
            "application/pdf" in content_type
            or content_type.startswith("image/")
            or final_url.lower().endswith(".pdf")
        )
    )

    ocr_provider_used = "none"
    fallback_stage = "none"
    final_text = parser_text

    if needs_ocr:
        primary_provider = create_ocr_provider(settings.ocr_primary_provider)
        primary = await primary_provider.extract_text(
            content=content,
            content_type=content_type or "application/octet-stream",
            file_name="regulation-source",
        )
        if primary.ok and primary.text:
            final_text = primary.text
            extraction_method = "ocr_primary"
            ocr_provider_used = primary.provider
        else:
            warnings.append(primary.error or "Primary OCR provider failed.")
            fallback_stage = "secondary"
            secondary_provider = create_ocr_provider(settings.ocr_secondary_provider)
            secondary = await secondary_provider.extract_text(
                content=content,
                content_type=content_type or "application/octet-stream",
                file_name="regulation-source",
            )
            if secondary.ok and secondary.text:
                final_text = secondary.text
                extraction_method = "ocr_secondary"
                ocr_provider_used = secondary.provider
            else:
                warnings.append(secondary.error or "Secondary OCR provider failed.")
                fallback_stage = "parser_only"
                extraction_method = f"{extraction_method}_fallback"
                ocr_provider_used = "none"

    max_chars = payload.max_chars or settings.extraction_max_chars
    normalized = normalize_text(final_text)
    if len(normalized) > max_chars:
        normalized = normalized[:max_chars]
        warnings.append(f"Extracted text truncated to {max_chars} chars.")

    if not normalized:
        if settings.ocr_strict_mode:
            return RegulationExtractResponse(
                status="error",
                source_url=source_url,
                final_url=final_url,
                etag=etag,
                last_modified=last_modified,
                content_type=content_type or None,
                extraction_method=extraction_method,
                ocr_provider_used=ocr_provider_used,
                fallback_stage=fallback_stage,
                error_code="empty_extracted_text",
                warnings=warnings or ["No text could be extracted."],
            )
        warnings.append("No text could be extracted. Returning empty content in non-strict mode.")

    logger.info(
        "Regulation extraction completed",
        extra={
            "source_url": source_url,
            "final_url": final_url,
            "method": extraction_method,
            "ocr_provider": ocr_provider_used,
            "fallback_stage": fallback_stage,
            "text_len": len(normalized),
        },
    )

    return RegulationExtractResponse(
        status="ok",
        source_url=source_url,
        final_url=final_url,
        etag=etag,
        last_modified=last_modified,
        content_type=content_type or None,
        extraction_method=extraction_method,
        extracted_text=normalized,
        normalized_text_hash=hash_text(normalized) if normalized else None,
        raw_html=raw_html,
        ocr_provider_used=ocr_provider_used,
        fallback_stage=fallback_stage,
        warnings=warnings,
    )
