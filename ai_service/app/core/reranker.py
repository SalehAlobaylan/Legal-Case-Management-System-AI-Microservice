"""
Cross-encoder reranking for regulation matching (Phase 2).

Provides a precision layer between bi-encoder retrieval and LLM verification.
Cross-encoders process (query, document) pairs jointly, capturing token-level
interactions that bi-encoders miss.

Feature-flagged via settings.cross_encoder_enabled.  When the real model is
not loaded (provider="fake" or model unavailable), a lightweight deterministic
fallback is used so tests and dev environments still work.

Pipeline position:
    composite scoring (top 30) -> cross-encoder reranker (top N) -> Gemini LLM
"""

from __future__ import annotations

import functools
import re
from typing import List, Sequence, Tuple

from app.config import settings
from app.utils.logger import logger

try:
    from sentence_transformers import CrossEncoder  # type: ignore[import-untyped]
except ImportError:
    CrossEncoder = None  # type: ignore[assignment,misc]


class RerankerService:
    """
    Cross-encoder reranker with two modes:

    - provider="fake":
        Deterministic score based on Jaccard token overlap.
        Fast, reproducible, no model download.

    - provider="cross_encoder":
        Uses a real cross-encoder model (e.g. BAAI/bge-reranker-v2-m3).
        Requires sentence-transformers with CrossEncoder support.
    """

    def __init__(
        self,
        provider: str | None = None,
        model_name: str | None = None,
        device: str | None = None,
    ) -> None:
        self.provider = (provider or "fake").lower()
        self.model = None

        if self.provider == "cross_encoder":
            if CrossEncoder is None:
                logger.warning(
                    "sentence-transformers CrossEncoder not available, "
                    "falling back to fake reranker"
                )
                self.provider = "fake"
            else:
                name = model_name or settings.cross_encoder_model_name
                dev = device or settings.embedding_device
                logger.info(f"Loading cross-encoder model: {name} on {dev}")
                self.model = CrossEncoder(name, device=dev)

    # ---------- internal helpers ---------- #

    @staticmethod
    def _tokenize(text: str) -> set[str]:
        """Extract 2+ char tokens (Arabic-aware)."""
        return set(re.findall(r"[\w\u0600-\u06FF]{2,}", (text or "").lower()))

    def _score_fake(self, pairs: Sequence[Tuple[str, str]]) -> List[float]:
        """
        Deterministic reranker fallback using Jaccard overlap.

        Returns scores in [0.0, 1.0].  This is intentionally simple —
        it exists so that tests and dev environments can exercise the
        full pipeline without downloading a model.
        """
        scores: List[float] = []
        for query, doc in pairs:
            q_tokens = self._tokenize(query)
            d_tokens = self._tokenize(doc)
            if not q_tokens or not d_tokens:
                scores.append(0.0)
                continue
            union = q_tokens | d_tokens
            jaccard = len(q_tokens & d_tokens) / len(union) if union else 0.0
            scores.append(float(jaccard))
        return scores

    # ---------- public API ---------- #

    def score_pairs(
        self,
        pairs: Sequence[Tuple[str, str]],
    ) -> List[float]:
        """
        Score a batch of (query, document) pairs.

        Returns a list of relevance scores (higher = more relevant).
        Real cross-encoder scores are unbounded; fake scores are in [0, 1].
        """
        if not pairs:
            return []

        if self.provider == "cross_encoder" and self.model is not None:
            # Real cross-encoder inference
            raw_scores = self.model.predict(
                list(pairs),
                show_progress_bar=False,
            )
            return [float(s) for s in raw_scores]

        return self._score_fake(pairs)

    def rerank(
        self,
        query: str,
        documents: Sequence[str],
        top_n: int | None = None,
    ) -> List[Tuple[int, float]]:
        """
        Rerank documents by relevance to query.

        Returns list of (original_index, score) sorted descending by score,
        truncated to top_n if provided.
        """
        if not documents:
            return []

        pairs = [(query, doc) for doc in documents]
        scores = self.score_pairs(pairs)

        indexed = list(enumerate(scores))
        indexed.sort(key=lambda x: x[1], reverse=True)

        if top_n is not None and top_n > 0:
            indexed = indexed[:top_n]

        return indexed


@functools.lru_cache(maxsize=1)
def get_reranker_service() -> RerankerService:
    """
    Singleton factory for the reranker.

    Uses real cross-encoder only when:
    - cross_encoder_enabled is True in config
    - CrossEncoder class is importable
    Otherwise returns a fake (Jaccard) reranker.
    """
    if settings.cross_encoder_enabled and CrossEncoder is not None:
        return RerankerService(provider="cross_encoder")
    return RerankerService(provider="fake")
