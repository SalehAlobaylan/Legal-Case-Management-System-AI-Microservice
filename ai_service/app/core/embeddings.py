from __future__ import annotations

import math
from typing import List, Sequence

from app.config import settings

try:
    # Only needed when using the real BGE model
    from sentence_transformers import SentenceTransformer  # type: ignore
except ImportError:
    SentenceTransformer = None  # type: ignore


class EmbeddingService:
    """
    Embedding wrapper with two modes:

    - provider="fake":
        * Very fast, deterministic, 4-dim vectors.
        * Used for tests (/embed, /similarity, pytest).

    - provider="bge":
        * Uses BAAI/bge-m3 via sentence-transformers.
        * Used in experiments (eval_bge_m3, eval_alarb_*).
    """

    def __init__(
        self,
        provider: str | None = None,
        model_name: str | None = None,
        device: str | None = None,
    ) -> None:
        # Decide provider from config or explicit override
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
        else:
            # fake provider → no heavy model
            self.model = None

    # ---------- internal helpers ---------- #

    @staticmethod
    def _normalize_vector(vec: Sequence[float]) -> List[float]:
        norm = math.sqrt(sum(x * x for x in vec)) or 1.0
        return [float(x / norm) for x in vec]

    def _embed_fake(self, texts: Sequence[str]) -> List[List[float]]:
        """
        Very simple deterministic embedding:

        4-dim vector per text:
          dim 0: 1.0 if "contract"/"عقد" appears
          dim 1: 1.0 if "weather"/"طقس" appears
          dim 2: 1.0 if "case"/"قضية"/"دعوى" appears
          dim 3: scaled text length
        """
        vectors: List[List[float]] = []

        for text in texts:
            t = text.lower()

            c_contract = 1.0 if ("contract" in t or "عقد" in t) else 0.0
            c_weather = 1.0 if ("weather" in t or "طقس" in t) else 0.0
            c_case = 1.0 if ("case" in t or "قضية" in t or "دعوى" in t) else 0.0
            length_feat = min(len(text) / 100.0, 10.0)

            vectors.append([c_contract, c_weather, c_case, length_feat])

        return vectors

    # ---------- public API ---------- #

    def embed_documents(
        self,
        texts: Sequence[str],
        normalize: bool | None = None,
    ) -> List[List[float]]:
        """
        Embed a batch of texts.

        normalize:
          - True  → L2-normalize each vector
          - False → leave as-is
          - None  → default:
              * provider="bge": normalize=True
              * provider="fake": normalize=False
        """
        if self.provider == "bge":
            raw_vectors = self.model.encode(  # type: ignore[union-attr]
                list(texts),
                convert_to_numpy=True,
            )
            vectors: List[List[float]] = [v.tolist() for v in raw_vectors]
        else:
            vectors = self._embed_fake(texts)

        if normalize is None:
            normalize = (self.provider == "bge")

        if normalize:
            vectors = [self._normalize_vector(v) for v in vectors]

        return vectors

    def embed_query(
        self,
        text: str,
        normalize: bool | None = None,
    ) -> List[float]:
        """Embed a single query string."""
        return self.embed_documents([text], normalize=normalize)[0]
