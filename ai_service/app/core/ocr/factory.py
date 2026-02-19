from __future__ import annotations

from app.core.ocr.providers.alapi import AlapiOCRProvider
from app.core.ocr.providers.base import OCRProvider
from app.core.ocr.providers.none import NoneOCRProvider


def create_ocr_provider(name: str) -> OCRProvider:
    normalized = (name or "").strip().lower()
    if normalized == "alapi":
        return AlapiOCRProvider()
    if normalized in ("none", "disabled", "parser_only", ""):
        return NoneOCRProvider()

    # Unknown providers are treated as disabled so extraction can still
    # continue with parser-only fallback.
    return NoneOCRProvider()
