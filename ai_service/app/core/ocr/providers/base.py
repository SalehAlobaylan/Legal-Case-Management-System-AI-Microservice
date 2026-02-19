from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class OCRResult:
    ok: bool
    text: Optional[str] = None
    error: Optional[str] = None
    provider: str = "none"


class OCRProvider:
    name = "base"

    async def extract_text(
        self,
        *,
        content: bytes,
        content_type: str,
        file_name: str = "document",
    ) -> OCRResult:
        raise NotImplementedError
