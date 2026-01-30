from __future__ import annotations

import math
from typing import List

from app.core.embeddings import EmbeddingService


def cosine(a: List[float], b: List[float]) -> float:
    """Plain Python cosine similarity (no numpy needed)."""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a)) or 1.0
    norm_b = math.sqrt(sum(x * x for x in b)) or 1.0
    return dot / (norm_a * norm_b)


def main() -> None:
    # Force real model regardless of .env
    embedder = EmbeddingService(provider="bge")

    # Very small toy “dataset” just to sanity-check Arabic behaviour
    corpus = [
        "تم توقيع العقد بين الطرفين أمس في المحكمة التجارية.",
        "قررت المحكمة تأجيل الجلسة إلى الأسبوع القادم.",
        "الطقس اليوم مشمس والحرارة معتدلة في الرياض.",
        "تم رفع دعوى جديدة بخصوص نزاع حول ملكية الأرض.",
    ]

    queries = [
        # should be close to doc 0
        "هل تم توقيع العقد بين الطرفين؟",
        # should be close to doc 1
        "متى تم تأجيل جلسة المحكمة؟",
        # should be close to doc 3
        "أريد معلومات عن دعوى تتعلق بملكية أرض.",
        # unrelated, maybe doc 2 (weather)
        "كيف هو الطقس اليوم في الرياض؟",
    ]

    # Ground-truth “best” doc index for each query (manual labels)
    expected_best = [0, 1, 3, 2]

    print("Loading BGE-M3 and computing embeddings...\n")

    corpus_embs = embedder.embed_documents(corpus)

    correct_top1 = 0

    for qi, q in enumerate(queries):
        q_emb = embedder.embed_query(q)

        scores = [cosine(q_emb, d_emb) for d_emb in corpus_embs]
        ranked = sorted(enumerate(scores), key=lambda x: x[1], reverse=True)

        best_idx, best_score = ranked[0]
        expected_idx = expected_best[qi]

        print(f"Query {qi+1}: {q}")
        print(f"  Top-1 doc index: {best_idx}, score={best_score:.3f}")
        print(f"  Top-1 doc text : {corpus[best_idx]}")
        print(f"  Expected index : {expected_idx}")
        print("  MATCH ✅" if best_idx == expected_idx else "  MISMATCH ❌")
        print("-" * 80)

        if best_idx == expected_idx:
            correct_top1 += 1

    accuracy = correct_top1 / len(queries)
    print(f"\nToy top-1 accuracy: {correct_top1}/{len(queries)} = {accuracy:.2f}")


if __name__ == "__main__":
    main()
