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
    - Exceptions: DomainError, InvalidSheetSchemaError, SheetEmptyError,
      TenantNotFoundError, UnauthorizedUserError, ReconciliationError
    - Ports: FileProcessingPort, AuthVerificationPort, FileParserPort,
      InventoryRepositoryPort, TenantRepositoryPort, ReportExporterPort,
      NotificationPort
    - DTOs: ParsedRowDTO, ParsedSheetDTO, DiscrepancyRowDTO,
      ReportResultDTO, AuthContextDTO, ReconciliationSummaryDTO,
      ProcessResultDTO
    - Services: (Phase 1.6 — `src.domain.services`)
"""

from __future__ import annotations

from src.domain.exceptions import (
    DomainError,
    InvalidSheetSchemaError,
    ReconciliationError,
    SheetEmptyError,
    TenantNotFoundError,
    UnauthorizedUserError,
)

from src.domain.models import (
    DiscrepancyItem,
    InventoryItem,
    InventorySnapshot,
    TelegramUser,
    Tenant,
)

from src.domain.ports import (
    AuthVerificationPort,
    FileParserPort,
    FileProcessingPort,
    InventoryRepositoryPort,
    NotificationPort,
    ReportExporterPort,
    TenantRepositoryPort,
)

from src.domain.schemas import (
    AuthContextDTO,
    DiscrepancyRowDTO,
    ParsedRowDTO,
    ParsedSheetDTO,
    ProcessResultDTO,
    ReconciliationSummaryDTO,
    ReportResultDTO,
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
    # Exceptions
    "DomainError",
    "InvalidSheetSchemaError",
    "ReconciliationError",
    "SheetEmptyError",
    "TenantNotFoundError",
    "UnauthorizedUserError",
    # Ports
    "AuthVerificationPort",
    "FileParserPort",
    "FileProcessingPort",
    "InventoryRepositoryPort",
    "NotificationPort",
    "ReportExporterPort",
    "TenantRepositoryPort",
    # DTOs
    "AuthContextDTO",
    "DiscrepancyRowDTO",
    "ParsedRowDTO",
    "ParsedSheetDTO",
    "ProcessResultDTO",
    "ReconciliationSummaryDTO",
    "ReportResultDTO",
]
