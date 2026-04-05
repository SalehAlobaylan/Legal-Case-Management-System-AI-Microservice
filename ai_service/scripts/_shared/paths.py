"""Shared path constants for the fine-tuning pipeline."""

from pathlib import Path

# Root of the ai_service package
AI_SERVICE_ROOT = Path(__file__).resolve().parent.parent.parent

# Data directories
DATA_DIR = AI_SERVICE_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
CITATIONS_DIR = DATA_DIR / "citations"
TRIPLETS_DIR = DATA_DIR / "triplets"
QA_DIR = DATA_DIR / "qa"

# Model output
MODELS_DIR = AI_SERVICE_ROOT / "models"

# Default file paths
JUDGMENTS_JSONL = RAW_DIR / "judgments.jsonl"
ERRORS_JSONL = RAW_DIR / "errors.jsonl"
CITATIONS_JSONL = CITATIONS_DIR / "citations.jsonl"
CITATIONS_STATS = CITATIONS_DIR / "citations_stats.json"
TRAIN_JSONL = TRIPLETS_DIR / "train.jsonl"
VAL_JSONL = TRIPLETS_DIR / "val.jsonl"
TRIPLETS_STATS = TRIPLETS_DIR / "stats.json"
QA_SAMPLE_CSV = QA_DIR / "qa_sample.csv"
FINETUNED_MODEL_DIR = MODELS_DIR / "bge-m3-saudi-legal-v1"
EVAL_REPORT = MODELS_DIR / "evaluation_report.json"

# MOJ API
MOJ_API_BASE = "https://laws-gateway.moj.gov.sa/apis/legislations/v1/Judgements"
MOJ_LIST_URL = f"{MOJ_API_BASE}/judgements-list"
MOJ_DETAILS_URL = f"{MOJ_API_BASE}/get-details"


def ensure_dirs() -> None:
    """Create all output directories if they don't exist."""
    for d in [RAW_DIR, CITATIONS_DIR, TRIPLETS_DIR, QA_DIR, MODELS_DIR]:
        d.mkdir(parents=True, exist_ok=True)
