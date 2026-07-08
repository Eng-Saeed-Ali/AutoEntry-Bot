"""Infrastructure Repository Adapters for the AutoEntry Bot.

This subpackage contains concrete implementations of the outbound
repository ports defined in ``src.domain.ports``:

- ``PostgresInventoryRepository`` — implements both
  ``InventoryRepositoryPort`` and ``TenantRepositoryPort``
  using SQLAlchemy 2.0 Async (asyncpg driver).

Hexagonal Architecture Compliance:
    - Infrastructure depends on Domain (imports ports + entities).
    - NEVER imports from ``src.presentation`` or ``src.application``.
    - Returns pure domain entities (not ORM objects, not raw dicts).
    - Maps infrastructure errors → domain exceptions (DatabaseError).

Usage:
    The ``App`` composer (``src.application.composer``) instantiates
    this adapter with an ``async_sessionmaker`` and injects it into
    the ``ProcessInventoryUseCase``.

Future adapters:
    - ``SQLiteInventoryRepository`` (for lightweight local testing).
    - ``MongoDBInventoryRepository`` (if document-storage is needed).
"""

from src.infrastructure.repositories.postgres_repository import (
    PostgresInventoryRepository,
)

__all__ = [
    "PostgresInventoryRepository",
]