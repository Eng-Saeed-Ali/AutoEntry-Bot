"""🧠 AutoEntry Bot — Core Domain Layer.

This layer contains the pure business logic of the AutoEntry system:
inventory reconciliation, schema validation, and report generation.
It is the innermost ring of the Hexagonal Architecture and depends
on **nothing** except `pydantic` (for DTOs/VOs) and the Python
standard library.

Exports:
    - Value Objects: Sku, Quantity, StoreId, TenantId, TelegramUserId,
      ItemName, DiffAmount, ExcelFileChecksum, DiscrepancyStatus
    - Entities: Tenant, InventoryItem, DiscrepancyItem,
      InventorySnapshot, TelegramUser
    - Ports: (Phase 1.4 — `src.domain.ports`)
    - Services: (Phase 1.6 — `src.domain.services`)
    - Exceptions: (Phase 1.3 — `src.domain.exceptions`)
"""

from __future__ import annotations

from src.domain.models import (
    DiscrepancyItem,
    InventoryItem,
    InventorySnapshot,
    TelegramUser,
    Tenant,
)

from src.domain.value_objects import (
    DiffAmount,
    DiscrepancyStatus,
    ExcelFileChecksum,
    ItemName,
    Quantity,
    Sku,
    StoreId,
    TelegramUserId,
    TenantId,
)

__all__ = [
    # Value Objects
    "DiffAmount",
    "DiscrepancyStatus",
    "ExcelFileChecksum",
    "ItemName",
    "Quantity",
    "Sku",
    "StoreId",
    "TelegramUserId",
    "TenantId",
    # Entities
    "DiscrepancyItem",
    "InventoryItem",
    "InventorySnapshot",
    "TelegramUser",
    "Tenant",
]
