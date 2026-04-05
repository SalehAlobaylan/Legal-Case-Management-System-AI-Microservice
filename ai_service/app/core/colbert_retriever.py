"""
ColBERT / late-interaction reranking for regulation matching (Phase 3).

ColBERT keeps per-token embeddings instead of pooling into a single vector.
MaxSim scoring: for each query token, find the max cosine similarity to any
document token, then average across all query tokens.  This preserves
fine-grained token-level signal — critical for long regulations where only
a few articles are relevant while the rest is noise.

Feature-flagged via settings.colbert_enabled.  When the real model is not
loaded (embeddings_provider="fake" or model unavailable), a deterministic
character n-gram fallback is used so tests and dev environments still work.

Pipeline position:
    composite scoring -> agentic expansion -> **ColBERT reranking** -> cross-encoder -> Gemini LLM
"""

from __future__ import annotations

import functools
import hashlib
import re
from math import sqrt
from typing import List, Sequence, Tuple

import numpy as np

from app.config import settings
from app.utils.logger import logger


class ColBERTService:
    """
    Late-interaction (ColBERT-style) reranker with two modes:

    - provider="fake":
        Deterministic MaxSim approximation using character 4-gram hashing.
        Fast, reproducible, no model download.  Good enough for integration
        tests that exercise the full pipeline.

    - provider="colbert":
        Uses the SentenceTransformer model's token-level embeddings and
        real MaxSim computation.  Reuses the already-loaded BGE-M3 model
        to avoid doubling memory.
    """

    def __init__(
        self,
        provider: str | None = None,
        model: object | None = None,
    ) -> None:
        self.provider = (provider or "fake").lower()
        self.model = model  # SentenceTransformer instance (or None for fake)

        if self.provider == "colbert" and self.model is None:
            logger.warning(
                "ColBERT provider requested but no model supplied, "
                "falling back to fake ColBERT"
            )
            self.provider = "fake"

    # ------------------------------------------------------------------ #
    #  Fake provider helpers                                               #
    # ------------------------------------------------------------------ #

    _NGRAM_DIM = 16  # dimension for fake token embeddings (MD5 = 16 bytes)

    @staticmethod
    def _char_ngrams(text: str, n: int = 4) -> list[str]:
        """Extract overlapping character n-grams from text."""
        text = re.sub(r"\s+", " ", (text or "").lower().strip())
        if len(text) < n:
            return [text] if text else []
        return [text[i : i + n] for i in range(len(text) - n + 1)]

    @classmethod
    def _ngram_to_vector(cls, ngram: str) -> list[float]:
        """Deterministic pseudo-embedding from a character n-gram."""
        h = hashlib.md5(ngram.encode("utf-8")).digest()
        raw = [float(b) / 255.0 for b in h[: cls._NGRAM_DIM]]
        # L2-normalize
        norm = sqrt(sum(x * x for x in raw)) or 1.0
        return [x / norm for x in raw]

    def _fake_token_encode(
        self, texts: Sequence[str]
    ) -> list[np.ndarray]:
        """
        Produce per-token pseudo-embeddings from character n-grams.

        Returns a list of 2-D numpy arrays, each of shape
        (num_tokens, _NGRAM_DIM).
        """
        results: list[np.ndarray] = []
        for text in texts:
            ngrams = self._char_ngrams(text)
            if not ngrams:
                results.append(
                    np.zeros((1, self._NGRAM_DIM), dtype=np.float32)
                )
                continue
            # Deduplicate adjacent identical n-grams for efficiency
            unique_ngrams = [ngrams[0]]
            for ng in ngrams[1:]:
                if ng != unique_ngrams[-1]:
                    unique_ngrams.append(ng)
            vecs = [self._ngram_to_vector(ng) for ng in unique_ngrams]
            results.append(np.array(vecs, dtype=np.float32))
        return results

    # ------------------------------------------------------------------ #
    #  Real provider helpers                                               #
    # ------------------------------------------------------------------ #

    def _real_token_encode(
        self, texts: Sequence[str]
    ) -> list[np.ndarray]:
        """
        Get per-token embeddings from the SentenceTransformer model.

        Uses the model's encode() with output_value='token_embeddings'
        which returns last-hidden-state vectors before pooling.
        Falls back to fake encoding on any failure.
        """
        try:
            # sentence-transformers >= 2.3 supports output_value
            embeddings = self.model.encode(  # type: ignore[union-attr]
                list(texts),
                output_value="token_embeddings",
                convert_to_numpy=True,
                show_progress_bar=False,
                normalize_embeddings=True,
            )
            # embeddings is a list of np.ndarray, each (seq_len, dim)
            return [np.asarray(e, dtype=np.float32) for e in embeddings]
        except Exception as exc:
            logger.warning(
                f"ColBERT real token encoding failed, falling back to fake: {exc}"
            )
            return self._fake_token_encode(texts)

    # ------------------------------------------------------------------ #
    #  MaxSim computation                                                  #
    # ------------------------------------------------------------------ #

    @staticmethod
    def maxsim(
        query_tokens: np.ndarray, doc_tokens: np.ndarray
    ) -> float:
        """
        Compute ColBERT MaxSim score.

        For each query token embedding, find the max cosine similarity
        to any document token embedding.  The final score is the average
        of all per-query-token max similarities.

        Args:
            query_tokens: shape (Q, D) — Q query token embeddings
            doc_tokens:   shape (N, D) — N document token embeddings

        Returns:
            float in roughly [0, 1] for normalized embeddings.
        """
        if query_tokens.size == 0 or doc_tokens.size == 0:
            return 0.0

        # (Q, D) @ (D, N) -> (Q, N) similarity matrix
        sim_matrix = query_tokens @ doc_tokens.T

        # Max over document tokens for each query token -> (Q,)
        max_sims = sim_matrix.max(axis=1)

        # Average MaxSim across query tokens
        return float(np.mean(max_sims))

    # ------------------------------------------------------------------ #
    #  Public API                                                          #
    # ------------------------------------------------------------------ #

    def encode_tokens(
        self, texts: Sequence[str]
    ) -> list[np.ndarray]:
        """
        Get per-token embeddings for a batch of texts.

        Returns a list of 2-D numpy arrays, each (num_tokens, embed_dim).
        """
        if self.provider == "colbert" and self.model is not None:
            return self._real_token_encode(texts)
        return self._fake_token_encode(texts)

    def score_pairs(
        self, pairs: Sequence[Tuple[str, str]]
    ) -> List[float]:
        """
        Score (query, document) pairs using MaxSim.

        Returns a list of MaxSim scores (higher = more relevant).
        """
        if not pairs:
            return []

        queries = [p[0] for p in pairs]
        docs = [p[1] for p in pairs]

        query_token_embs = self.encode_tokens(queries)
        doc_token_embs = self.encode_tokens(docs)

        return [
            self.maxsim(q, d)
            for q, d in zip(query_token_embs, doc_token_embs)
        ]

    def rerank(
        self,
        query: str,
        documents: Sequence[str],
        top_n: int | None = None,
    ) -> List[Tuple[int, float]]:
        """
        Rerank documents by MaxSim relevance to query.

        Returns list of (original_index, maxsim_score) sorted descending,
        truncated to top_n if provided.
        """
        if not documents:
            return []

        # Encode query tokens once, reuse for all documents
        query_tokens = self.encode_tokens([query])[0]
        doc_tokens_list = self.encode_tokens(list(documents))

        scored = [
            (i, self.maxsim(query_tokens, dt))
            for i, dt in enumerate(doc_tokens_list)
        ]
        scored.sort(key=lambda x: x[1], reverse=True)

        if top_n is not None and top_n > 0:
            scored = scored[:top_n]

        return scored


@functools.lru_cache(maxsize=1)
def get_colbert_service() -> ColBERTService:
    """
    Singleton factory for ColBERT reranker.

    Reuses the existing SentenceTransformer model from the embedding
    service to avoid doubling GPU/CPU memory.  Falls back to fake
    provider when embeddings_provider="fake" or ColBERT is disabled.
    """
    if settings.colbert_enabled:
        if settings.embeddings_provider.lower() == "bge":
            # Import here to avoid circular imports
            from app.api.deps import get_embedding_service

            embedder = get_embedding_service()
            model = getattr(embedder, "model", None)
            if model is not None:
                logger.info(
                    "ColBERT service initialized with real model "
                    f"(reusing {settings.embedding_model_name})"
                )
                return ColBERTService(provider="colbert", model=model)

        logger.info("ColBERT service initialized with fake provider")
        return ColBERTService(provider="fake")

    return ColBERTService(provider="fake")
