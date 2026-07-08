"""📱 Telegram Bot Adapter — aiogram 3.x Primary Delivery Mechanism.

This subpackage contains all Telegram-specific presentation logic:
handlers, middleware, keyboards, and the bot factory.  It is the
primary delivery mechanism for the AutoEntry Bot MVP.

====================================================================
   PACKAGE STRUCTURE
====================================================================

* ``handlers.py``  — aiogram Router with /start and document handlers
* ``bot.py``       — Bot + Dispatcher factory, polling entrypoint
                     (Task 4.2 / 4.5)
* ``middleware/``  — auth middleware intercepting every update
                     (Task 4.3)
* ``notifier.py``  — NotificationPort adapter wrapping aiogram Bot
                     (Task 2.6 — lives here because it imports aiogram)

====================================================================
   IMPORT DISCIPLINE

This subpackage may import:
    - ``aiogram`` (Bot framework)
    - ``src.application.use_cases`` (ProcessInventoryUseCase)
    - ``src.domain.ports`` (FileProcessingPort, for type hints)
    - ``src.domain.exceptions`` (DomainError, for error mapping)
    - ``src.domain.schemas`` (ProcessResultDTO, for DTO return)

Must NEVER import:
    - ``src.infrastructure`` (no direct adapter access)
    - ``src.config`` (settings injected at startup)

====================================================================
   DEPENDENCY PROVIDER PATTERN

Handlers receive ``ProcessInventoryUseCase`` (and future use cases)
via the aiogram ``data`` dictionary — injected by middleware or
the bot factory at startup.  The handler never constructs adapters
or resolves dependencies; it only calls ``use_case.process(...)``.
"""

from __future__ import annotations

from src.presentation.telegram.handlers import router as telegram_router

__all__ = ["telegram_router"]