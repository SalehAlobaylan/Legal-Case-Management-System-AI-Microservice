from __future__ import annotations

import logging
from io import BytesIO
from typing import Optional

from app.core.ocr.providers.base import OCRProvider, OCRResult

logger = logging.getLogger(__name__)


class TesseractOCRProvider(OCRProvider):
    """Self-hosted OCR provider using Tesseract with Arabic support.

    Requires system packages:
      - tesseract (``brew install tesseract``)
      - Arabic trained data (``ara.traineddata`` in tessdata dir)
      - poppler (``brew install poppler``) — needed by pdf2image for PDF→PNG

    Python packages: ``pytesseract``, ``pdf2image``, ``Pillow``
    """

    name = "tesseract"

    # Languages to pass to Tesseract.  "ara+eng" handles mixed
    # Arabic/English documents well.
    _LANGUAGES = "ara+eng"

    # DPI for PDF-to-image conversion.  300 is the standard for OCR.
    _DPI = 300

    async def extract_text(
        self,
        *,
        content: bytes,
        content_type: str,
        file_name: str = "document",
    ) -> OCRResult:
        try:
            import pytesseract
        except ImportError:
            return OCRResult(
                ok=False,
                error="pytesseract is not installed",
                provider=self.name,
            )

        ctype = (content_type or "").lower()
        is_pdf = "application/pdf" in ctype or file_name.lower().endswith(".pdf")

        try:
            if is_pdf:
                text = self._ocr_pdf(content, pytesseract)
            else:
                text = self._ocr_image(content, pytesseract)
        except Exception as exc:
            logger.warning("Tesseract OCR failed: %s", exc)
            return OCRResult(
                ok=False,
                error=f"tesseract_error: {exc}",
                provider=self.name,
            )

        if not text or not text.strip():
            return OCRResult(
                ok=False,
                error="tesseract_empty_text",
                provider=self.name,
            )

        return OCRResult(ok=True, text=text.strip(), provider=self.name)

    # ------------------------------------------------------------------
    # Internal helpers (synchronous — Tesseract is CPU-bound)
    # ------------------------------------------------------------------

    def _ocr_pdf(self, content: bytes, pytesseract) -> str:
        """Convert PDF pages to images, then OCR each page."""
        from pdf2image import convert_from_bytes

        images = convert_from_bytes(content, dpi=self._DPI)
        pages: list[str] = []
        for img in images:
            page_text = pytesseract.image_to_string(
                img, lang=self._LANGUAGES, config="--psm 6"
            )
            if page_text and page_text.strip():
                pages.append(page_text.strip())
        return "\n\n".join(pages)

    def _ocr_image(self, content: bytes, pytesseract) -> str:
        """OCR a single image (PNG, JPEG, TIFF, etc.)."""
        from PIL import Image

        img = Image.open(BytesIO(content))
        return pytesseract.image_to_string(img, lang=self._LANGUAGES, config="--psm 6")
