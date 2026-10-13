"""Application settings (12-factor: env vars, GST_ prefix)."""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration; every value overridable via GST_* env vars."""

    model_config = SettingsConfigDict(env_prefix="GST_", env_file=".env", extra="ignore")

    # Dev mode gates: dev_otp echo in responses, relaxed token checks.
    dev_mode: bool = True

    # JWT secrets — dev default only; prod MUST set GST_JWT_SECRET.
    jwt_secret: str = "dev-only-insecure-secret-change-me"  # noqa: S105
    jwt_algorithm: str = "HS256"  # noqa: S105
    access_token_minutes: int = 15
    refresh_token_days: int = 7
    stepup_token_minutes: int = 15

    # OTP policy (SECURITY_AND_ACCESS.md §1).
    otp_length: int = 6
    otp_expiry_seconds: int = 300  # 5 minutes
    otp_max_verify_attempts: int = 5
    otp_request_limit_per_hour: int = 5

    # MinIO object storage — bootstrap_stack.py :9001.
    minio_endpoint: str = "127.0.0.1:9001"
    minio_use_tls: bool = False
    minio_access_key: str = "gst_admin"  # noqa: S105
    minio_secret_key: str = "gst_minio_dev_pass"  # noqa: S105

    # Redis (queue + OTP store + refresh families) — bootstrap_stack.py :6380.
    redis_url: str = "redis://127.0.0.1:6380/0"  # noqa: S105

    # LLM extraction service (EXTRACTION_SPEC.md §9).
    extraction_llm_base_url: str = "http://127.0.0.1:3001/v1"
    extraction_llm_model: str = "llama3.1:8b"
    extraction_llm_timeout_seconds: int = 120
    extraction_llm_max_tokens_per_doc: int = 8192
    extraction_llm_max_docs_per_day: int = 5000

    # Google Gemini Flash extraction (Free tier with API key)
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.0-flash"

    # OCR / preprocess knobs (EXTRACTION_SPEC.md §2/§9).
    extraction_blur_threshold_photo: float = 80.0
    extraction_blur_threshold_whatsapp: float = 60.0
    extraction_ocr_dpi: int = 300

    # Upload guard per SECURITY_AND_ACCESS.md §6.
    max_document_pages: int = 50

    # GSP API — dev placeholder values; prod MUST override via env vars.
    gsp_client_id: str = "dev-client-id"
    gsp_client_secret: str = "dev-client-secret"  # noqa: S105
    gsp_username: str = "dev-username"
    gsp_password: str = "dev-password"  # noqa: S105
    gsp_sandbox_mode: bool = True
    gsp_api_base_url: str = "https://sandbox.gst.gov.in"

    # IRP API — dev placeholder values; prod MUST override via env vars.
    irp_client_id: str = "dev-client-id"
    irp_client_secret: str = "dev-client-secret"  # noqa: S105
    irp_username: str = "dev-username"
    irp_password: str = "dev-password"  # noqa: S105
    irp_sandbox_mode: bool = True
    irp_api_base_url: str = "https://einvoice1-trial.nic.in"


@lru_cache
def get_settings() -> Settings:
    """Process-wide settings singleton."""
    return Settings()
