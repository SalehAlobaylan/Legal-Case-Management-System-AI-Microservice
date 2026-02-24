from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, File, Form, UploadFile

from app.api.schemas.responses import DocumentExtractResponse
from app.core.extraction import extract_text_from_bytes
from app.utils.logger import logger

router = APIRouter()


@router.post("/documents/extract", response_model=DocumentExtractResponse)
async def extract_document_content(
    file: UploadFile = File(...),
    max_chars: Optional[int] = Form(default=None),
) -> DocumentExtractResponse:
    file_name = file.filename or "document"
    content_type = file.content_type or "application/octet-stream"
    content = await file.read()

    extraction = await extract_text_from_bytes(
        content=content,
        content_type=content_type,
        file_name=file_name,
        max_chars=max_chars,
    )

    logger.info(
        "Document extraction completed",
        extra={
            "file_name": file_name,
            "content_type": content_type,
            "status": extraction.status,
            "method": extraction.extraction_method,
            "ocr_provider": extraction.ocr_provider_used,
            "fallback_stage": extraction.fallback_stage,
            "text_len": len(extraction.extracted_text or ""),
        },
    )

    return DocumentExtractResponse(
        status=extraction.status,
        file_name=file_name,
        content_type=content_type,
        extraction_method=extraction.extraction_method,
        extracted_text=extraction.extracted_text,
        normalized_text_hash=extraction.normalized_text_hash,
        ocr_provider_used=extraction.ocr_provider_used,
        fallback_stage=extraction.fallback_stage,
        warnings=extraction.warnings or [],
        error_code=extraction.error_code,
    )
