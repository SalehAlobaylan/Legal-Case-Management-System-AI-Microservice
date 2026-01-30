from __future__ import annotations

from typing import List, Sequence, Tuple

from app.core.embeddings import EmbeddingService

Score = float
RankedDoc = Tuple[str, Score]
RankedResults = List[List[RankedDoc]]


class SimilarityService:
    """
    Small helper around EmbeddingService that ranks corpus texts
    by similarity to one or more query texts.

    - By default it creates its own EmbeddingService() using settings.
    - For experiments you can inject a specific embedder:
        SimilarityService(embedder=EmbeddingService(provider="bge"))
    """

    def __init__(self, embedder: EmbeddingService | None = None) -> None:
        # If no embedder is provided, fall back to the default one
        self.embedder = embedder or EmbeddingService()

    @staticmethod
    def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
        """Plain cosine similarity without numpy (works with list[float])."""
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = (sum(x * x for x in a) ** 0.5) or 1.0
        norm_b = (sum(x * x for x in b) ** 0.5) or 1.0
        return dot / (norm_a * norm_b)

    def rank(self, queries: Sequence[str], corpus: Sequence[str], top_k: int = 5) -> RankedResults:
        """
        For each query, return a list of (doc, score) tuples sorted
        from most similar to least similar.

        Shape of return value:
            [
              [ (doc1, score1), (doc2, score2), ... ],  # for query 1
              [ (doc1, score1), (doc2, score2), ... ],  # for query 2
              ...
            ]
        """
        if not queries:
            return []

        # If corpus is empty, return empty list for each query
        if not corpus:
            return [[] for _ in queries]

        corpus_texts = list(corpus)
        corpus_embs = self.embedder.embed_documents(corpus_texts)

        all_results: RankedResults = []

        for q in queries:
            q_emb = self.embedder.embed_query(q)
            scored: List[RankedDoc] = []

            for doc, d_emb in zip(corpus_texts, corpus_embs):
                score = self._cosine(q_emb, d_emb)
                scored.append((doc, float(score)))

            scored.sort(key=lambda x: x[1], reverse=True)
            all_results.append(scored[:top_k])

        return all_results
