"""Structured logging package — structlog configuration for the AutoEntry Bot.

Provides container-friendly JSON logging to stdout. All log messages are
key-value structured for machine parsing by Docker log drivers and log
aggregation tools.

Usage:
    from src.infrastructure.logging import setup_logging, get_logger

    setup_logging()                   # call once at startup
    logger = get_logger(__name__)     # get bound logger anywhere

    logger.info("event", key="value", extra_context=42)
"""

from __future__ import annotations

from src.infrastructure.logging.setup import get_logger, setup_logging

__all__ = ["get_logger", "setup_logging"]