from __future__ import annotations

import hashlib
import math
from typing import List, Sequence

from app.config import settings

try:
    # Required only for the real BGE model path.
    from sentence_transformers import SentenceTransformer  # type: ignore
except ImportError:
    SentenceTransformer = None  # type: ignore


class EmbeddingService:
    """
    Embedding wrapper with two modes.

    - provider="fake":
        Fast deterministic vectors for local/dev and tests.
        Emits 1024-dim vectors so backend pgvector pipelines can run end-to-end.

    - provider="bge":
        Uses BAAI/bge-m3 via sentence-transformers.
    """

    def __init__(
        self,
        provider: str | None = None,
        model_name: str | None = None,
        device: str | None = None,
    ) -> None:
        self.provider = (provider or settings.embeddings_provider).lower()
        self.model = None

        if self.provider == "bge":
            if SentenceTransformer is None:
                raise ImportError(
                    "sentence-transformers is required for provider='bge'. "
                    "Install it with `pip install sentence-transformers`."
                )
            name = model_name or settings.embedding_model_name
            dev = device or settings.embedding_device
            self.model = SentenceTransformer(name, device=dev)

    @staticmethod
    def _normalize_vector(vec: Sequence[float]) -> List[float]:
        norm = math.sqrt(sum(x * x for x in vec)) or 1.0
        return [float(x / norm) for x in vec]

    def _embed_fake(self, texts: Sequence[str]) -> List[List[float]]:
        """
        Deterministic 1024-dim embedding.

        - First 4 dims are simple hand-crafted features.
        - Remaining dims are hashed token counts for lexical overlap.
        """
        dimensions = 1024
        vectors: List[List[float]] = []

        for text in texts:
            lowered = text.lower()
            vector = [0.0] * dimensions

            # Lightweight anchor features.
            vector[0] = 1.0 if "contract" in lowered else 0.0
            vector[1] = 1.0 if "labor" in lowered else 0.0
            vector[2] = 1.0 if "case" in lowered else 0.0
            vector[3] = min(len(text) / 100.0, 10.0)

            # Hashed bag-of-tokens into dims [4..1023].
            for token in lowered.split():
                if not token:
                    continue
                digest = hashlib.sha1(token.encode("utf-8")).digest()
                hashed = int.from_bytes(digest[:4], byteorder="little", signed=False)
                idx = 4 + (hashed % (dimensions - 4))
                vector[idx] += 1.0

            vectors.append(vector)

        return vectors

    def embed_documents(
        self,
        texts: Sequence[str],
        normalize: bool | None = None,
    ) -> List[List[float]]:
        if self.provider == "bge":
            raw_vectors = self.model.encode(  # type: ignore[union-attr]
                list(texts),
                convert_to_numpy=True,
            )
            vectors: List[List[float]] = [v.tolist() for v in raw_vectors]
        else:
            vectors = self._embed_fake(texts)

        if normalize is None:
            normalize = self.provider == "bge"

        if normalize:
            vectors = [self._normalize_vector(v) for v in vectors]

        return vectors

    def embed_query(
        self,
        text: str,
        normalize: bool | None = None,
    ) -> List[float]:
        return self.embed_documents([text], normalize=normalize)[0]

