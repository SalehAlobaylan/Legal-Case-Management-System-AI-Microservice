from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import AliasChoices, Field, field_validator
from typing import List, Any
import json

# Resolve .env relative to this file: ai_service/app/config.py -> ../../.env
_ENV_FILE = Path(__file__).parent.parent.parent / ".env"


class Settings(BaseSettings):
    # Pydantic v2 settings config
    _REPO_ROOT = Path(__file__).resolve().parents[2]
    model_config = SettingsConfigDict(
        # Resolve env files robustly no matter which working directory uvicorn uses.
        # Priority (later entries can override earlier ones):
        # - repo root .env
        # - ai_service/.env
        # - current working directory .env
        env_file=(
            _REPO_ROOT / ".env",
            _REPO_ROOT / "ai_service" / ".env",
            ".env",
        ),
        case_sensitive=False,
        extra="ignore",
    )

    # Embedding backend selection
    embeddings_provider: str = Field(
        default="fake",  # "fake" for tests, "bge" for real model in future
        # Support both names to avoid silent fallback to default "fake".
        validation_alias=AliasChoices("EMBEDDINGS_PROVIDER", "EMBEDDINGS_MODEL"),
    )
    embedding_model_name: str = Field(
        default="BAAI/bge-m3",
        validation_alias="EMBEDDING_MODEL_NAME",
    )
    embedding_device: str = Field(
        default="cpu",  # later you can try "cuda"
        validation_alias="EMBEDDING_DEVICE",
    )
    hf_embed_provider_order: List[str] = Field(
        default=["serverless", "endpoint", "bge"],
        validation_alias="HF_EMBED_PROVIDER_ORDER",
    )
    hf_serverless_api_base: str = Field(
        default="https://api-inference.huggingface.co/models",
        validation_alias="HF_SERVERLESS_API_BASE",
    )
    hf_serverless_model_name: str = Field(
        default="",
        validation_alias="HF_SERVERLESS_MODEL_NAME",
    )
    hf_serverless_api_token: str = Field(
        default="",
        validation_alias="HF_SERVERLESS_API_TOKEN",
    )
    hf_endpoint_url: str = Field(
        default="",
        validation_alias="HF_ENDPOINT_URL",
    )
    hf_endpoint_api_token: str = Field(
        default="",
        validation_alias="HF_ENDPOINT_API_TOKEN",
    )
    hf_embed_request_timeout_seconds: float = Field(
        default=30.0,
        validation_alias="HF_EMBED_REQUEST_TIMEOUT_SECONDS",
    )
    hf_embed_retry_attempts: int = Field(
        default=1,
        validation_alias="HF_EMBED_RETRY_ATTEMPTS",
    )
    hf_embed_max_batch_size: int = Field(
        default=16,
        validation_alias="HF_EMBED_MAX_BATCH_SIZE",
    )
    hf_embed_cache_size: int = Field(
        default=2048,
        validation_alias="HF_EMBED_CACHE_SIZE",
    )

    # Basic app info
    app_name: str = Field(default="AI Microservice", validation_alias="APP_NAME")
    app_version: str = Field(default="0.1.0", validation_alias="APP_VERSION")
    env: str = Field(default="development", validation_alias="ENV")
    debug: bool = Field(default=True, validation_alias="DEBUG")
    log_level: str = Field(default="INFO", validation_alias="LOG_LEVEL")

    # Server bind options
    host: str = Field(default="127.0.0.1", validation_alias="HOST")
    port: int = Field(default=8000, validation_alias="PORT")

    # CORS
    cors_origins: List[str] = Field(
        default=["http://localhost:3000", "http://localhost:5173"],
        validation_alias="CORS_ORIGINS",
    )

    # Backend API integration
    backend_api_url: str = Field(
        default="https://orca-app-uayze.ondigitalocean.app",
        validation_alias="BACKEND_API_URL",
    )
    backend_api_key: str = Field(
        default="",  # Optional API key for backend authentication
        validation_alias="BACKEND_API_KEY",
    )

    # Regulation extraction / OCR
    ocr_primary_provider: str = Field(
        default="alapi",
        validation_alias="OCR_PRIMARY_PROVIDER",
    )
    ocr_secondary_provider: str = Field(
        default="none",
        validation_alias="OCR_SECONDARY_PROVIDER",
    )
    alapi_base_url: str = Field(
        default="https://alapi.deep.sa",
        validation_alias="ALAPI_BASE_URL",
    )
    alapi_api_key: str = Field(
        default="",
        validation_alias="ALAPI_API_KEY",
    )
    alapi_ocr_path: str = Field(
        default="/ocr",
        validation_alias="ALAPI_OCR_PATH",
    )
    source_whitelist_domains: List[str] = Field(
        default=["laws.boe.gov.sa", "laws.moj.gov.sa", "boe.gov.sa", "moj.gov.sa"],
        validation_alias="SOURCE_WHITELIST_DOMAINS",
    )
    extraction_timeout_seconds: float = Field(
        default=30.0,
        validation_alias="EXTRACTION_TIMEOUT_SECONDS",
    )
    extraction_max_bytes: int = Field(
        default=15_000_000,
        validation_alias="EXTRACTION_MAX_BYTES",
    )
    extraction_max_chars: int = Field(
        default=120_000,
        validation_alias="EXTRACTION_MAX_CHARS",
    )
    ocr_min_text_chars: int = Field(
        default=400,
        validation_alias="OCR_MIN_TEXT_CHARS",
    )
    ocr_strict_mode: bool = Field(
        default=False,
        validation_alias="OCR_STRICT_MODE",
    )
    insights_default_top_k: int = Field(
        default=5,
        validation_alias="INSIGHTS_DEFAULT_TOP_K",
    )
    insights_max_source_chars: int = Field(
        default=15000,
        validation_alias="INSIGHTS_MAX_SOURCE_CHARS",
    )
    insights_summary_sentences: int = Field(
        default=4,
        validation_alias="INSIGHTS_SUMMARY_SENTENCES",
    )
    reg_insights_max_source_chars: int = Field(
        default=40000,
        validation_alias="REG_INSIGHTS_MAX_SOURCE_CHARS",
    )
    reg_impact_max_source_chars: int = Field(
        default=40000,
        validation_alias="REG_IMPACT_MAX_SOURCE_CHARS",
    )
    llm_provider: str = Field(
        default="heuristic",
        validation_alias="LLM_PROVIDER",
    )
    llm_base_url: str = Field(
        default="",
        validation_alias="LLM_BASE_URL",
    )
    llm_api_key: str = Field(
        default="",
        validation_alias="LLM_API_KEY",
    )
    llm_model: str = Field(
        default="",
        validation_alias="LLM_MODEL",
    )
    llm_timeout_seconds: float = Field(
        default=30.0,
        validation_alias="LLM_TIMEOUT_SECONDS",
    )

    # --- Gemini verification pipeline (Phase 1) ---
    gemini_api_key: str = Field(
        default="",
        validation_alias="GEMINI_API_KEY",
    )
    gemini_model: str = Field(
        default="gemini-2.0-flash",
        validation_alias="GEMINI_MODEL",
    )
    gemini_enabled: bool = Field(
        default=False,
        validation_alias="GEMINI_ENABLED",
    )
    gemini_timeout_seconds: float = Field(
        default=25.0,
        validation_alias="GEMINI_TIMEOUT_SECONDS",
    )
    gemini_top_n_candidates: int = Field(
        default=15,
        validation_alias="GEMINI_TOP_N_CANDIDATES",
    )

    # --- Chat settings ---
    chat_enabled: bool = Field(
        default=True,
        validation_alias="CHAT_ENABLED",
    )
    chat_max_history_turns: int = Field(
        default=10,
        validation_alias="CHAT_MAX_HISTORY_TURNS",
    )
    chat_max_context_chars: int = Field(
        default=12000,
        validation_alias="CHAT_MAX_CONTEXT_CHARS",
    )
    chat_temperature: float = Field(
        default=0.3,
        validation_alias="CHAT_TEMPERATURE",
    )
    chat_max_output_tokens: int = Field(
        default=2048,
        validation_alias="CHAT_MAX_OUTPUT_TOKENS",
    )

    # --- Chunk overlap (Phase 1) ---
    chunk_overlap_ratio: float = Field(
        default=0.0,
        validation_alias="CHUNK_OVERLAP_RATIO",
    )

    # --- Instruction-tuned embedding queries (Phase 1) ---
    embedding_query_instruction_ar: str = Field(
        default="",
        validation_alias="EMBEDDING_QUERY_INSTRUCTION_AR",
    )
    embedding_query_instruction_en: str = Field(
        default="",
        validation_alias="EMBEDDING_QUERY_INSTRUCTION_EN",
    )

    # --- Cross-encoder reranking (Phase 2 — flags only, not yet implemented) ---
    cross_encoder_enabled: bool = Field(
        default=False,
        validation_alias="CROSS_ENCODER_ENABLED",
    )
    cross_encoder_model_name: str = Field(
        default="BAAI/bge-reranker-v2-m3",
        validation_alias="CROSS_ENCODER_MODEL_NAME",
    )
    cross_encoder_top_n: int = Field(
        default=15,
        validation_alias="CROSS_ENCODER_TOP_N",
    )

    # --- HyDE (Phase 2) ---
    hyde_enabled: bool = Field(
        default=False,
        validation_alias="HYDE_ENABLED",
    )
    hyde_max_query_chars: int = Field(
        default=4000,
        validation_alias="HYDE_MAX_QUERY_CHARS",
    )

    # --- ColBERT / late-interaction reranking (Phase 3 — experimental) ---
    colbert_enabled: bool = Field(
        default=False,
        validation_alias="COLBERT_ENABLED",
    )
    colbert_top_n: int = Field(
        default=15,
        validation_alias="COLBERT_TOP_N",
    )

    # --- Agentic retrieval (Phase 3 — experimental) ---
    agentic_retrieval_enabled: bool = Field(
        default=False,
        validation_alias="AGENTIC_RETRIEVAL_ENABLED",
    )
    agentic_max_rounds: int = Field(
        default=2,
        validation_alias="AGENTIC_MAX_ROUNDS",
    )
    agentic_min_candidates_for_refinement: int = Field(
        default=3,
        validation_alias="AGENTIC_MIN_CANDIDATES_FOR_REFINEMENT",
    )
    agentic_timeout_seconds: float = Field(
        default=30.0,
        validation_alias="AGENTIC_TIMEOUT_SECONDS",
    )

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_cors(cls, v: Any) -> Any:
        """
        Accept:
          - Python list (already parsed)
          - JSON array string: '["http://...","http://..."]'
          - Comma-separated string: 'http://...,http://...'
        """
        if isinstance(v, list):
            return v

        if isinstance(v, str):
            s = v.strip()
            if not s:
                return []

            # Try JSON array first
            if s.startswith("[") and s.endswith("]"):
                try:
                    return json.loads(s)
                except Exception:
                    # fall through to comma-split
                    pass

            # Fallback: comma-separated
            return [item.strip() for item in s.split(",") if item.strip()]

        return v

    @field_validator("source_whitelist_domains", mode="before")
    @classmethod
    def parse_domains(cls, v: Any) -> Any:
        if isinstance(v, list):
            return v

        if isinstance(v, str):
            s = v.strip()
            if not s:
                return []

            if s.startswith("[") and s.endswith("]"):
                try:
                    return json.loads(s)
                except Exception:
                    pass

            return [item.strip() for item in s.split(",") if item.strip()]

        return v

    @field_validator("hf_embed_provider_order", mode="before")
    @classmethod
    def parse_hf_embed_provider_order(cls, v: Any) -> Any:
        if isinstance(v, str):
            s = v.strip()
            if s and not s.startswith("["):
                return [item.strip().lower() for item in s.split(",") if item.strip()]

        if isinstance(v, list):
            return [str(item).strip().lower() for item in v if str(item).strip()]

        return v


# Singleton settings instance
settings = Settings()
