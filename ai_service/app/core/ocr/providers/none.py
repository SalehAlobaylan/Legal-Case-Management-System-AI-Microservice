from __future__ import annotations

from app.core.ocr.providers.base import OCRProvider, OCRResult


class NoneOCRProvider(OCRProvider):
    name = "none"

    async def extract_text(
        self,
        *,
        content: bytes,
        content_type: str,
        file_name: str = "document",
    ) -> OCRResult:
        return OCRResult(
            ok=False,
            error="OCR provider is disabled",
            provider=self.name,
        )
