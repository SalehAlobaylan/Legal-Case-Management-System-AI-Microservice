"""
Shared FastAPI dependencies.

Provides singleton instances of expensive services (e.g. EmbeddingService)
so they are initialised once at startup rather than per-request.
"""

from __future__ import annotations

from functools import lru_cache

from app.core.embeddings import EmbeddingService


@lru_cache(maxsize=1)
def get_embedding_service() -> EmbeddingService:
    """
    Return a process-wide singleton EmbeddingService.

    When provider="bge", this avoids reloading the model on every request.
    """
    return EmbeddingService()
