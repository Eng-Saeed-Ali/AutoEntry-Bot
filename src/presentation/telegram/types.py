"""TypedDict for aiogram ``data`` dependency-injection dictionary.

Defines the exact shape of the dictionary passed by the aiogram dispatcher
to every handler and middleware.  Using a TypedDict enables IDE autocompletion,
``mypy`` validation, and prevents key-typo bugs between ``main.py`` (where
keys are inserted) and the presentation layer (where keys are read).

Design:
    - ``total=False``: not all keys are present on every aiogram update.
      Some are injected by ``main.py`` at startup (e.g., ``process_inventory_use_case``),
      others by middleware during the update lifecycle (e.g., ``auth_context``),
      and others may be absent entirely (e.g., ``db_engine`` for read-only operations).
    - Imports only from ``src.domain`` (ports, DTOs) — hexagonal discipline preserved.
"""

from __future__ import annotations

from typing import TypedDict

from sqlalchemy.ext.asyncio import AsyncEngine

from src.application.use_cases.process_inventory import ProcessInventoryUseCase
from src.domain.ports import AuthVerificationPort
from src.domain.schemas import AuthContextDTO

# Re-export well-known data keys so that handlers and middleware can import
# them from a single location instead of defining their own constants.

DATA_KEY_USE_CASE = "process_inventory_use_case"
"""Key for ``ProcessInventoryUseCase`` instance."""

DATA_KEY_AUTH = "auth_context"
"""Key for ``AuthContextDTO`` instance (set by auth middleware)."""

DATA_KEY_MAX_FILE_SIZE = "max_file_size_mb"
"""Key for the maximum allowed Excel file size in MB (set by main.py from config)."""

DATA_KEY_DB_ENGINE = "db_engine"
"""Key for ``AsyncEngine`` (read-only, injected at startup for status checks)."""

DATA_KEY_DOCUMENT = "document"
"""Key for the raw Telegram document File object (set by aiogram / middleware)."""


class AiogramDataDict(TypedDict, total=False):
    """Shape of the aiogram ``data`` dictionary.

    Each field is optional (``total=False``) because the dictionary is
    progressively populated:
        1. ``main.py`` injects ``process_inventory_use_case``,
           ``max_file_size_mb``, and optionally ``db_engine`` at
           bot startup.
        2. The auth middleware injects ``auth_context`` after verifying
           the Telegram user.
        3. aiogram itself may inject ``document`` for document messages.

    Handlers and middleware access these keys via their well-known constants
    (``DATA_KEY_USE_CASE``, etc.) to avoid magic-string duplication.
    """

    process_inventory_use_case: ProcessInventoryUseCase
    auth_port: AuthVerificationPort
    auth_context: AuthContextDTO
    max_file_size_mb: int
    db_engine: AsyncEngine
    document: object  # aiogram File type — kept loose to avoid aiogram import here
