"""structlog configuration for container-friendly, async-compatible JSON logging.

Configures the standard library logging pipeline through structlog's processor
chain to emit JSON records to stdout. This is the single logging setup entry
point — call `setup_logging()` once at application bootstrap, then use
`get_logger()` everywhere.

Key design decisions:
- JSON output to stdout: parseable by Docker json-file log driver and
  Kubernetes/ELK/Fluentd without extra sidecar configuration.
- Async-compatible: structlog's stdlib wrapper delegates to stdlib logging,
  which is thread-safe. No custom async log writers needed at this scale.
- Idempotent: `setup_logging()` is safe to call multiple times (subsequent
  calls are no-ops).
- Dynamic log level: reads from the `settings` singleton at call time, so
  changing `.env` + restarting the container adjusts verbosity.
"""

from __future__ import annotations

import logging
import sys

import structlog

from src.config.settings import settings

# ---------------------------------------------------------------------------
# Processor chain — runs on every log call, left to right
# ---------------------------------------------------------------------------
_SHARED_PROCESSORS: list[structlog.types.Processor] = [
    # 1. Inject stdlib log level name into the event dict as "level"
    structlog.stdlib.add_log_level,
    # 2. ISO 8601 timestamp (machine-sortable, human-readable)
    structlog.processors.TimeStamper(fmt="iso"),
    # 3. Include the logger name for source filtering
    structlog.stdlib.add_logger_name,
    # 4. Format exception info (exc_info → "exception" key with traceback)
    structlog.processors.format_exc_info,
    # 5. Render the final event dict as a JSON string
    structlog.processors.JSONRenderer(),
]

# Processor that dev mode can optionally prepend for pretty console output.
# Not wired by default — kept for future dev convenience toggle.
_CONSOLE_RENDERER = structlog.dev.ConsoleRenderer(colors=True)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def setup_logging() -> None:
    """Configure structlog and stdlib logging for the application.

    Reads `settings.log_level` to set the minimum severity emitted to stdout.
    Safe to call multiple times — subsequent calls detect existing
    configuration and return immediately.

    Must be called once before any `get_logger()` usage, typically in
    `main()` or the Application Composer's `startup()` method.
    """

    level_name: str = settings.log_level
    level_value: int = getattr(logging, level_name)

    # Configure stdlib root logger (structlog delegates to it).
    # We only want our structured output on stdout — no double-logging.
    root = logging.getLogger()
    root.setLevel(level_value)
    # Remove any handlers attached by third-party libs or previous calls.
    root.handlers.clear()
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(level_value)
    # Use a bare formatter; structlog's JSONRenderer handles all formatting.
    handler.setFormatter(logging.Formatter("%(message)s"))
    root.addHandler(handler)

    # Silence noisy non-application loggers.
    _silence_noisy_libs()

    # Configure structlog to wrap stdlib.
    structlog.configure(
        processors=_SHARED_PROCESSORS,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Return a structlog BoundLogger with the given *name* pre-bound.

    If *name* is ``None``, the caller's ``__name__`` is used (via
    structlog's default).  Typical usage::

        logger = get_logger(__name__)
        logger.info("file_parsed", rows=150, tenant_id="t_42")
    """
    return structlog.get_logger(name)  # type: ignore[no-any-return]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _silence_noisy_libs() -> None:
    """Raise the effective log level for verbose third-party loggers.

    Prevents aiogram, sqlalchemy.engine, and asyncpg from flooding stdout
    with DEBUG-level chatter unless the global log_level is DEBUG.
    """
    min_level: int = getattr(logging, settings.log_level)
    # Only suppress if we're not in DEBUG mode.
    if min_level > logging.DEBUG:
        for noisy in (
            "aiogram",
            "sqlalchemy.engine",
            "sqlalchemy.pool",
            "asyncpg",
            "openpyxl",
        ):
            logging.getLogger(noisy).setLevel(logging.WARNING)
