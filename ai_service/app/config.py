from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field, field_validator
from typing import List, Any
import json


class Settings(BaseSettings):
    # Pydantic v2 settings config
    model_config = SettingsConfigDict(
        env_file=".env",
        case_sensitive=False,
        extra="ignore",
    )

    # Embedding backend selection
    embeddings_provider: str = Field(
        default="fake",  # "fake" for tests, "bge" for real model in future
        validation_alias="EMBEDDINGS_PROVIDER",
    )
    embedding_model_name: str = Field(
        default="BAAI/bge-m3",
        validation_alias="EMBEDDING_MODEL_NAME",
    )
    embedding_device: str = Field(
        default="cpu",  # later you can try "cuda"
        validation_alias="EMBEDDING_DEVICE",
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


# Singleton settings instance
settings = Settings()
