from __future__ import annotations

from datasets import load_dataset


def main() -> None:
    """
    Just load the ALARB dataset and print some basic info.
    This is a first step before we plug it into the embedding API.
    """
    print("Loading THIQAH-RD/ALARB from Hugging Face...\n")
    ds = load_dataset("THIQAH-RD/ALARB")

    # Show splits
    print(ds)
    print("\nExample from train split:\n")
    example = ds["train"][0]
    for k, v in example.items():
        # Truncate long text when printing
        text = v
        if isinstance(text, str) and len(text) > 200:
            text = text[:200] + "..."
        print(f"- {k}: {text}")


if __name__ == "__main__":
    main()
