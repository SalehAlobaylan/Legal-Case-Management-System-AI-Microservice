from __future__ import annotations

import hashlib
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Optional

from bs4 import BeautifulSoup
from docx import Document as DocxDocument
from pypdf import PdfReader

from app.config import settings
from app.core.ocr.factory import create_ocr_provider


def normalize_text(text: str) -> str:
    return " ".join((text or "").split()).strip()


def hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


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


def parse_docx(content: bytes) -> str:
    document = DocxDocument(BytesIO(content))
    parts: list[str] = []
    for paragraph in document.paragraphs:
        value = (paragraph.text or "").strip()
        if value:
            parts.append(value)
    for table in document.tables:
        for row in table.rows:
            row_values = [(cell.text or "").strip() for cell in row.cells]
            merged = " | ".join([value for value in row_values if value])
            if merged:
                parts.append(merged)
    return "\n".join(parts).strip()


@dataclass
class ByteExtractionResult:
    status: str
    extraction_method: str
    extracted_text: Optional[str]
    normalized_text_hash: Optional[str]
    raw_html: Optional[str]
    ocr_provider_used: str = "none"
    fallback_stage: str = "none"
    warnings: list[str] | None = None
    error_code: Optional[str] = None


async def extract_text_from_bytes(
    *,
    content: bytes,
    content_type: str,
    file_name: str = "document",
    max_chars: Optional[int] = None,
) -> ByteExtractionResult:
    if len(content) > settings.extraction_max_bytes:
        return ByteExtractionResult(
            status="error",
            extraction_method="none",
            extracted_text=None,
            normalized_text_hash=None,
            raw_html=None,
            error_code="payload_too_large",
            warnings=[
                f"Uploaded payload exceeds {settings.extraction_max_bytes} bytes limit."
            ],
        )

    ctype = (content_type or "").lower()
    extension = Path(file_name or "").suffix.lower()
    warnings: list[str] = []
    raw_html: str | None = None
    parser_text = ""
    extraction_method = "parser_fallback"

    is_html = "text/html" in ctype or extension in (".html", ".htm")
    is_pdf = "application/pdf" in ctype or extension == ".pdf"
    is_text = ctype.startswith("text/") or extension in (".txt", ".md")
    is_docx = (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        in ctype
        or extension == ".docx"
    )
    is_doc = "application/msword" in ctype or extension == ".doc"
    is_image = ctype.startswith("image/")

    if is_doc:
        return ByteExtractionResult(
            status="error",
            extraction_method="none",
            extracted_text=None,
            normalized_text_hash=None,
            raw_html=None,
            error_code="unsupported_file_type",
            warnings=[
                "Legacy .doc files are not supported yet. Upload .docx/PDF/text/image instead."
            ],
        )

    try:
        if is_html:
            raw_html, parser_text = parse_html(content)
            extraction_method = "parser_html"
        elif is_pdf:
            parser_text = parse_pdf(content)
            extraction_method = "parser_pdf"
        elif is_docx:
            parser_text = parse_docx(content)
            extraction_method = "parser_docx"
        elif is_text:
            parser_text = parse_plain_text(content)
            extraction_method = "parser_text"
        elif is_image:
            parser_text = ""
            extraction_method = "parser_image"
        else:
            parser_text = parse_plain_text(content)
            extraction_method = "parser_fallback"
    except Exception as exc:
        warnings.append(f"Parser failed: {exc}")
        parser_text = ""

    parsed_length = len(normalize_text(parser_text))
    needs_ocr = parsed_length < settings.ocr_min_text_chars and (is_pdf or is_image)

    ocr_provider_used = "none"
    fallback_stage = "none"
    final_text = parser_text

    if needs_ocr:
        primary_provider = create_ocr_provider(settings.ocr_primary_provider)
        primary = await primary_provider.extract_text(
            content=content,
            content_type=ctype or "application/octet-stream",
            file_name=file_name or "document",
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
                content_type=ctype or "application/octet-stream",
                file_name=file_name or "document",
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

    max_chars_value = max_chars or settings.extraction_max_chars
    normalized = normalize_text(final_text)
    if len(normalized) > max_chars_value:
        normalized = normalized[:max_chars_value]
        warnings.append(f"Extracted text truncated to {max_chars_value} chars.")

    if not normalized:
        if settings.ocr_strict_mode:
            return ByteExtractionResult(
                status="error",
                extraction_method=extraction_method,
                extracted_text=None,
                normalized_text_hash=None,
                raw_html=raw_html,
                ocr_provider_used=ocr_provider_used,
                fallback_stage=fallback_stage,
                error_code="empty_extracted_text",
                warnings=warnings or ["No text could be extracted."],
            )
        warnings.append(
            "No text could be extracted. Returning empty content in non-strict mode."
        )

    return ByteExtractionResult(
        status="ok",
        extraction_method=extraction_method,
        extracted_text=normalized,
        normalized_text_hash=hash_text(normalized) if normalized else None,
        raw_html=raw_html,
        ocr_provider_used=ocr_provider_used,
        fallback_stage=fallback_stage,
        warnings=warnings,
    )
