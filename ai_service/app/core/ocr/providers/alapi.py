from __future__ import annotations

from typing import Any, Optional

import httpx

from app.config import settings
from app.core.ocr.providers.base import OCRProvider, OCRResult


class AlapiOCRProvider(OCRProvider):
    name = "alapi"

    def _extract_text_from_json(self, payload: Any) -> Optional[str]:
        if payload is None:
            return None

        if isinstance(payload, str):
            return payload.strip() or None

        if isinstance(payload, list):
            collected = [self._extract_text_from_json(item) for item in payload]
            merged = "\n".join(item for item in collected if item)
            return merged.strip() or None

        if isinstance(payload, dict):
            for key in ("text", "content", "result", "ocr_text", "extracted_text"):
                value = payload.get(key)
                text = self._extract_text_from_json(value)
                if text:
                    return text
            for value in payload.values():
                text = self._extract_text_from_json(value)
                if text:
                    return text

        return None

    async def extract_text(
        self,
        *,
        content: bytes,
        content_type: str,
        file_name: str = "document",
    ) -> OCRResult:
        if not settings.alapi_api_key:
            return OCRResult(
                ok=False,
                error="ALAPI_API_KEY is not configured",
                provider=self.name,
            )

        base_url = settings.alapi_base_url.rstrip("/")
        path = settings.alapi_ocr_path.strip()
        if not path.startswith("/"):
            path = f"/{path}"

        try:
            async with httpx.AsyncClient(timeout=settings.extraction_timeout_seconds) as client:
                response = await client.post(
                    f"{base_url}{path}",
                    headers={
                        "Authorization": f"Bearer {settings.alapi_api_key}",
                    },
                    files={
                        "file": (
                            file_name,
                            content,
                            content_type or "application/octet-stream",
                        )
                    },
                )
        except Exception as exc:
            return OCRResult(
                ok=False,
                error=f"alapi_request_failed: {exc}",
                provider=self.name,
            )

        if response.status_code >= 400:
            return OCRResult(
                ok=False,
                error=f"alapi_http_{response.status_code}",
                provider=self.name,
            )

        try:
            payload = response.json()
        except Exception:
            payload = response.text

        text = self._extract_text_from_json(payload)
        if not text:
            return OCRResult(
                ok=False,
                error="alapi_empty_text",
                provider=self.name,
            )

        return OCRResult(ok=True, text=text, provider=self.name)
