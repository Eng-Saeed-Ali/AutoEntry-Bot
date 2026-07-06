# AutoEntry Bot — Agent Handoff Log

> **Purpose:** Maintain seamless continuity across development sessions.
> **Rule:** Read this file at the start of every task; update it at the end.

---

## 1. Recently Completed

### Task 0.1 — Initialize Project Skeleton
**Date:** 2026-07-07
**Files created:**

| File | Purpose |
|------|---------|
| `pyproject.toml` | PEP 621 project metadata + all runtime & dev deps with pinned ranges. Includes `[tool.ruff]`, `[tool.mypy]` (strict), `[tool.pytest]` (asyncio_mode=auto), `[tool.coverage]`, and `[tool.importlinter]` contracts enforcing hex layer boundaries. |
| `.gitignore` | Excludes `.env`, `__pycache__`, `.venv`, `.mypy_cache`, `htmlcov`, IDE cruft, and `*.xlsx` (except `fixtures/*.xlsx`). |
| `.env.example` | Documented template with `BOT_TOKEN`, `DATABASE_URL`, `POSTGRES_*`, `LOG_LEVEL`, `TELEGRAM_ALLOWED_UPDATES`. |
| `Makefile` | Targets: `dev`, `lint`, `format`, `test`, `test-unit`, `test-cov`, `migrate-up`, `migrate-down`, `build`, `up`, `down`, `clean`. Cross-platform (Windows cmd + Unix shell). |

### Task 0.2 — Create Config Module
**Date:** 2026-07-07
**Files created:**

| File | Purpose |
|------|---------|
| `src/config/__init__.py` | Empty init — makes `src.config` a package. |
| `src/config/settings.py` | `Settings(BaseSettings)` singleton using `pydantic-settings`. Fields: `bot_token: SecretStr`, `database_url: PostgresDsn`, `postgres: PostgresConfig` (nested), `log_level: Literal`, `telegram_allowed_updates: list[str]`, `excel_max_file_size_mb: int`, `report_max_anomalies: int`. Fails fast at import time if required env vars missing. Includes `_parse_allowed_updates` validator that handles both JSON-list strings and comma-separated plain strings. |

---

## 2. State & Conventions (READ BEFORE CODING)

### Naming & Style
- **Package:** `src/` is the root Python package (not `autoentry_bot/`). All imports use `src.xxx` format.
- **Quotes:** Double quotes everywhere (ruff format configured for `quote-style = "double"`).
- **Line length:** 120 chars (ruff).
- **Python target:** 3.12 (strict mypy, `from __future__ import annotations` in all modules).
- **Async:** Everything is `async def` / `await` where IO happens.
- **Type annotations:** Mandatory. All functions return typed. `disallow_untyped_defs = true`.

### Config Singleton
- Import as: `from src.config.settings import settings`
- Access token: `settings.bot_token.get_secret_value()` (SecretStr unwrap)
- Database URL: `str(settings.database_url)` (PostgresDsn → string)
- **DOMAIN LAYER MUST NEVER IMPORT `settings`.** This is a cross-cutting concern. Domain code receives values via DTOs/dependencies.

### Hexagonal Layer Boundaries
Enforced by `import-linter` in CI. The contracts:
- `src.domain` — independent of `src.infrastructure` and `src.presentation`
- `src.application` — independent of `src.infrastructure` and `src.presentation`
- `src.infrastructure` — independent of `src.presentation` only

### Key Variable/Class Names Established
- `Settings` class (singleton instance: `settings`)
- `PostgresConfig` nested model
- `src/config/settings.py` — the one true config location
- `.env` at project root (git-ignored, loaded by pydantic-settings)

---

## 3. Next Steps & Hints

### ⬜ Task 0.3 — Structured Logging (Structlog)
**File to create:** `src/infrastructure/logging.py`

Hints for the next agent:
- Use `structlog.stdlib.BoundLogger` with `structlog.configure()`
- Render as JSON to stdout (production-friendly, parseable by Docker log drivers)
- Provide a `setup_logging(level: str) -> None` function
- Processor chain: `structlog.stdlib.add_log_level`, `structlog.processors.TimeStamper(fmt="iso")`, `structlog.processors.format_exc_info`, `structlog.processors.JSONRenderer()`
- In `src/config/settings.py`, `settings.log_level` maps to stdlib levels via `getattr(logging, settings.log_level)`
- Export a module-level `logger` instance that domain/application/infrastructure can import

### ⬜ Task 0.4 — Docker Compose & Dockerfile
**Files to create:** `Dockerfile`, `docker-compose.yml`

Hints:
- Dockerfile: multi-stage, `python:3.12-alpine` base, install deps in venv, copy `src/` and `migrations/`, entrypoint runs the bot via `python -m src.main` (future module)
- docker-compose.yml: two services — `bot` + `postgres`
- `postgres` uses `postgres:16-alpine`, mounts volume `pgdata:/var/lib/postgresql/data`, reads `POSTGRES_*` env vars from `.env`
- `bot` service: `build: .`, `env_file: .env`, `depends_on: postgres` with healthcheck
- No Redis yet (future task)

### ⬜ Task 1.1 — Domain Value Objects
**Files to create:** `src/domain/value_objects.py`

Hints:
- `Sku(str)` — frozen, stripped, uppercase
- `Quantity(int)` — must be >= 0
- `StoreId(int)` — positive, non-zero
- `ExcelFileChecksum(str)` — SHA-256 of raw bytes
- All use `pydantic.BaseModel` with `frozen=True` and `validate_assignment=False` (immutable VOs)

---

## 4. File Inventory (Current State)

```
AutoEntry bot/
├── ARCHITECTURE.md          ✅ (blueprint)
├── AGENT_HANDOFF.md         ✅ (this file)
├── pyproject.toml           ✅
├── .gitignore               ✅
├── .env.example             ✅
├── Makefile                 ✅
└── src/
    └── config/
        ├── __init__.py      ✅ (empty)
        └── settings.py      ✅ (Settings singleton)
```

---

*End of handoff. Next session: read this file, then proceed to Task 0.3.*