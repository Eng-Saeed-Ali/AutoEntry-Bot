"""Central configuration module using pydantic-settings.

Loads from .env file and environment variables. All settings are typed,
validated, and available as a singleton `settings` instance.

Hexagonal Architecture note:
    This module lives in `src/config`, outside hex layers. It is a
    cross-cutting bootstrap concern referenced by main.py and infrastructure
    adapters. Domain logic MUST NOT import this file.
"""

from __future__ import annotations

import json
from typing import Literal

from pydantic import PostgresDsn, SecretStr, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class PostgresConfig(BaseSettings):
    """Standalone PG parts used by Docker Compose / alembic env.py."""

    model_config = SettingsConfigDict(env_prefix="POSTGRES_")

    host: str = "localhost"
    port: int = 5432
    db: str = "autoentry"
    user: str = "autoentry"
    password: str = "autoentry"


class Settings(BaseSettings):
    """Aggregated application settings.

    All values are read from environment variables (or .env). Missing required
    fields raise pydantic ValidationError at startup — fail-fast behaviour.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",  # silently ignore unknown env vars
    )

    # ── Telegram ──────────────────────────────────────────────────────────
    bot_token: SecretStr
    """Bot token obtained from @BotFather. Never logged or serialized."""

    telegram_allowed_updates: list[str] = [
        "message",
        "edited_message",
        "callback_query",
    ]
    """Update types to receive via long-polling."""

    @field_validator("telegram_allowed_updates", mode="before")
    @classmethod
    def _parse_allowed_updates(cls, v: object) -> list[str]:
        if isinstance(v, str):
            try:
                return json.loads(v)
            except json.JSONDecodeError:
                # fallback: comma-separated plain string
                return [item.strip() for item in v.split(",") if item.strip()]
        return v  # type: ignore[return-value]

    # ── Database ──────────────────────────────────────────────────────────
    database_url: PostgresDsn
    """AsyncPG connection string.

    Example: postgresql+asyncpg://autoentry:autoentry@localhost:5432/autoentry
    """

    # ── Postgres parts (for Docker Compose / alembic convenience) ─────────
    postgres: PostgresConfig = PostgresConfig()

    # ── Logging ───────────────────────────────────────────────────────────
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    """Minimum log level emitted to stdout (structured JSON via structlog)."""

    # ── Application ───────────────────────────────────────────────────────
    excel_max_file_size_mb: int = 10
    """Maximum Excel file size accepted by the bot, in megabytes."""

    report_max_anomalies: int = 200
    """Cap on anomaly rows included in a Telegram report to avoid message overflow."""


# ---------------------------------------------------------------------------
# Singleton — import this everywhere (except domain)
# ---------------------------------------------------------------------------
try:
    settings = Settings()  # type: ignore[call-arg]
except ValidationError as exc:
    import sys

    print("❌ CONFIG ERROR: Invalid or missing environment variables.", file=sys.stderr)
    print(exc, file=sys.stderr)
    sys.exit(1)