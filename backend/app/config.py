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

    # Redis (queue + OTP store + refresh families) — bootstrap_stack.py :6380.
    redis_url: str = "redis://127.0.0.1:6380/0"  # noqa: S105


@lru_cache
def get_settings() -> Settings:
    """Process-wide settings singleton."""
    return Settings()
