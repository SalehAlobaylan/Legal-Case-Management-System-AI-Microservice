from __future__ import annotations

import random
from typing import List

import requests
from datasets import load_dataset

# Base URL of your FastAPI service
BASE_URL = "http://127.0.0.1:8000"
SIM_URL = f"{BASE_URL}/similarity/"   # <-- matches test_api.py


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
    # Fixed seed so results are reproducible
    random.seed(42)

    print("Loading THIQAH-RD/ALARB from Hugging Face...\n")
    ds = load_dataset("THIQAH-RD/ALARB")
    train = ds["train"]

    print(f"Train rows: {len(train)}")
    print("Building global pool of law articles for negatives...")
    law_pool = build_global_law_pool(train)
    print(f"Total distinct law articles in pool: {len(law_pool)}\n")

    print("Starting API-level evaluation against /similarity/ ...")
    print(f"Using API URL: {SIM_URL}\n")

    # ── TUNE THESE TO CONTROL RUNTIME ─────────────────────────────────────────
    num_cases = 20           # how many random cases to sample
    top_k = 5                # size of top-k to check
    negatives_per_case = 10  # number of random negative articles per case
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

        if not pos_laws:
            print("  -> Skipping (no applicable_laws).")
            continue

        # Build query from facts
        query_text = " ".join(facts_sentences)

        # Sample negatives
        neg_candidates = [law for law in law_pool if law not in pos_laws]
        if not neg_candidates:
            print("  -> Skipping (no negative candidates).")
            continue

        sample_size = min(negatives_per_case, len(neg_candidates))
        neg_sample = random.sample(neg_candidates, k=sample_size)

        corpus = pos_laws + neg_sample

        payload = {
            "queries": [query_text],
            "corpus": corpus,   # matches test_api.py key
            "top_k": top_k,
        }

        try:
            resp = requests.post(SIM_URL, json=payload, timeout=60)
        except requests.exceptions.RequestException as e:
            print(f"  !! Request error: {e}")
            continue

        if resp.status_code != 200:
            print(f"  !! API returned status {resp.status_code}: {resp.text}")
            continue

        data = resp.json()

        # Expecting shape: {"results": [ [ {"doc": str, "score": float}, ... ] ]}
        try:
            ranked = data["results"][0]
        except (KeyError, IndexError, TypeError) as e:
            print(f"  !! Unexpected response format: {e}, data={data}")
            continue

        ranked_docs = [item["doc"] if isinstance(item, dict) else item[0] for item in ranked]

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
    print(f"\nAPI /similarity/ ALARB hit-rate@{top_k}: {correct}/{total} = {accuracy:.2f}")


if __name__ == "__main__":
    main()
