from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter

from app.api.schemas.requests import RegulationExtractRequest
from app.api.schemas.responses import RegulationExtractResponse
from app.config import settings
from app.core.extraction import extract_text_from_bytes
from app.utils.logger import logger

router = APIRouter()


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
            warnings=[
                "Source URL is not in the trusted whitelist or resolves to a private network."
            ],
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

    extraction = await extract_text_from_bytes(
        content=response.content,
        content_type=content_type,
        file_name=final_url.split("/")[-1] or "regulation-source",
        max_chars=payload.max_chars,
    )

    logger.info(
        "Regulation extraction completed",
        extra={
            "source_url": source_url,
            "final_url": final_url,
            "status": extraction.status,
            "method": extraction.extraction_method,
            "ocr_provider": extraction.ocr_provider_used,
            "fallback_stage": extraction.fallback_stage,
            "text_len": len(extraction.extracted_text or ""),
        },
    )

    return RegulationExtractResponse(
        status=extraction.status,
        source_url=source_url,
        final_url=final_url,
        etag=etag,
        last_modified=last_modified,
        content_type=content_type or None,
        extraction_method=extraction.extraction_method,
        extracted_text=extraction.extracted_text,
        normalized_text_hash=extraction.normalized_text_hash,
        raw_html=extraction.raw_html,
        ocr_provider_used=extraction.ocr_provider_used,
        fallback_stage=extraction.fallback_stage,
        warnings=extraction.warnings or [],
        error_code=extraction.error_code,
    )
