from __future__ import annotations

import random
from typing import List

from datasets import load_dataset

from app.core.embeddings import EmbeddingService
from app.core.similarity import SimilarityService


def build_global_law_pool(rows, max_cases: int = 2000) -> List[str]:
    """
    Collect a large pool of distinct law articles from many cases.
    We use this pool later to sample negatives.
    """
    pool: List[str] = []

    for i, row in enumerate(rows):
        if i >= max_cases:
            break

        for law in row["applicable_laws"]:
            if law not in pool:
                pool.append(law)

    return pool


def main() -> None:
    print("Loading THIQAH-RD/ALARB from Hugging Face...\n")
    ds = load_dataset("THIQAH-RD/ALARB")
    train = ds["train"]

    print(f"Train rows: {len(train)}")
    print("Building global pool of law articles for negatives...")
    law_pool = build_global_law_pool(train)
    print(f"Total distinct law articles in pool: {len(law_pool)}\n")

    # Use real BGE model regardless of .env
    print("Initializing embedding + similarity services (BGE-M3)...")
    embedder = EmbeddingService(provider="bge")
    sim_service = SimilarityService(embedder=embedder)
    print("Models ready. Starting evaluation...\n")

    # ── TUNE THESE TO CONTROL RUNTIME ─────────────────────────────────────────
    num_cases = 30
    top_k = 5           
    negatives_per_case = 20 
    # ──────────────────────────────────────────────────────────────────────────

    correct = 0
    total = 0

    indices = list(range(len(train)))
    random.shuffle(indices)
    indices = indices[:num_cases]

    for i, idx in enumerate(indices, start=1):
        print(f"[{i}/{len(indices)}] Evaluating case index={idx} ...")

        row = train[idx]
        facts_sentences: List[str] = row["case_facts"]
        pos_laws: List[str] = row["applicable_laws"]

        # Skip cases with no applicable laws
        if not pos_laws:
            print("  -> Skipping (no applicable_laws).")
            continue

        # Build query text by joining the facts
        query_text = " ".join(facts_sentences)

        # Sample negatives from the global pool
        neg_candidates = [law for law in law_pool if law not in pos_laws]
        if not neg_candidates:
            print("  -> Skipping (no negative candidates).")
            continue

        sample_size = min(negatives_per_case, len(neg_candidates))
        neg_sample = random.sample(neg_candidates, k=sample_size)

        corpus = pos_laws + neg_sample

        # Use similarity service to rank corpus by relevance to query
        results = sim_service.rank(
            queries=[query_text],
            corpus=corpus,
            top_k=top_k,
        )

        ranked_docs = [doc for doc, score in results[0]]

        # Hit if at least one true law is in top_k
        hit = any(law in ranked_docs for law in pos_laws)

        total += 1
        if hit:
            correct += 1
            print(f"  -> HIT (at least one true law in top-{top_k}).")
        else:
            print(f"  -> MISS (no true law in top-{top_k}).")

    if total == 0:
        print("\nNo evaluable cases found.")
        return

    accuracy = correct / total
    print(f"\nALARB retrieval hit-rate@{top_k}: {correct}/{total} = {accuracy:.2f}")


if __name__ == "__main__":
    main()