"""🎯 Application Use Cases — Orchestration layer for the AutoEntry Bot.

Each use case in this package:
    - Receives its dependencies (outbound ports) via **explicit
      manual Constructor Injection** in ``__init__``.
    - Orchestrates domain entities and infrastructure adapters
      to fulfil a single business operation.
    - Contains **NO core business rules** — those belong to the
      ``src.domain`` layer.
    - Returns only DTOs (``ProcessResultDTO``, ``AuthContextDTO``)
      to the Presentation layer.

Use Cases:
    - ``ProcessInventoryUseCase``: Full pipeline — parse Excel →
      reconcile → persist → export report → notify user.
    - ``VerifyTelegramUserUseCase``: Authenticate a Telegram user
      and resolve their tenant/role (Phase 3 / Task 3.1).
"""

from src.application.use_cases.process_inventory import ProcessInventoryUseCase

__all__ = [
    "ProcessInventoryUseCase",
]
