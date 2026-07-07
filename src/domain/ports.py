"""📥📤 Domain Port Interfaces for the AutoEntry Bot.

Ports are **abstract contracts** that declare WHAT the domain
needs from the outside world — they are the "plugs and sockets"
of the Hexagonal Architecture.

====================================================================
  THE IRON LAW OF HEXAGONAL ARCHITECTURE (re: Ports)
====================================================================
Ports are defined **inside** the Domain layer because:

1. **Dependency Inversion Principle (DIP):**
   High-level policy (Domain) MUST NOT depend on low-level
   details (Infrastructure).  Instead, BOTH depend on
   abstractions.  The port IS that abstraction — it lives in
   the layer that *owns* the policy.

2. **Domain Vocabulary:**
   Port signatures use domain types: ``InventorySnapshot``,
   ``TenantId``, ``Tenant``, etc.  The domain declares its
   needs in its own language.  Adapters TRANSLATE between
   the domain vocabulary and external technology details
   (SQL rows, Telegram API calls, Excel file bytes).

3. **Testability:**
   Because ports are abstract, use-case tests inject fake
   adapters that implement the same port — no real database,
   no real Telegram bot, no real Excel file needed.
   Swapping adapters is the whole point of Hexagonal.

====================================================================
  PORT CATEGORIES
====================================================================

* **Inbound (Driving) Ports:**
  Called by the Presentation layer (Telegram handlers, REST
  controllers, etc.) to invoke domain use cases.
  - ``FileProcessingPort``
  - ``AuthVerificationPort``

* **Outbound (Driven) Ports:**
  Called by the Domain / Application layer to interact with
  external systems.  Implemented by Infrastructure adapters.
  - ``FileParserPort``        (Infra: openpyxl + polars)
  - ``InventoryRepositoryPort`` (Infra: SQLAlchemy 2.0 Async)
  - ``TenantRepositoryPort``    (Infra: SQLAlchemy 2.0 Async)
  - ``ReportExporterPort``      (Infra: openpyxl)
  - ``NotificationPort``        (Infra/Presentation: aiogram)
====================================================================

Design Principles:
    - Every port is an ``abc.ABC`` with ``@abstractmethod``.
      Concrete adapters in Infrastructure MUST inherit and
      implement every abstract method — the interpreter will
      reject instantiation of incomplete adapters.
    - All methods are ``async def``.  The AutoEntry Bot is
      async-first; every IO boundary is awaitable.
    - Ports that reference DTOs from ``src.domain.schemas``
      (``ParsedSheetDTO``, ``ProcessResultDTO``, etc.) import
      those types under ``TYPE_CHECKING`` and use string
      annotations.  ``schemas.py`` will be built in Task 1.5.
    - Ports referencing domain entities/VOs (``InventorySnapshot``,
      ``Tenant``, ``TenantId``, etc.) import them directly at
      runtime — these already exist from Tasks 1.1–1.2.
"""

from __future__ import annotations

import abc
from typing import TYPE_CHECKING

from src.domain.models import (
    InventorySnapshot,
    TelegramUser,
    Tenant,
)
from src.domain.value_objects import (
    TenantId,
    TelegramUserId,
)

if TYPE_CHECKING:
    from src.domain.schemas import (
        AuthContextDTO,
        ParsedSheetDTO,
        ProcessResultDTO,
        ReportResultDTO,
    )

# ============================================================================
# INBOUND (DRIVING) PORTS
# ============================================================================


class FileProcessingPort(abc.ABC):
    """Inbound port: process an uploaded inventory Excel file end-to-end.

    Called by the Presentation layer (e.g., a Telegram file-upload
    handler) to trigger the full reconciliation pipeline:
    parse → validate → reconcile → persist → report → notify.

    The concrete implementation lives in the Application layer
    (``ProcessInventoryUseCase``), NOT in Infrastructure —
    inbound ports are fulfilled by use cases.
    """

    @abc.abstractmethod
    async def process(
        self,
        file_bytes: bytes,
        filename: str,
        tenant_id: TenantId,
        chat_id: int,
    ) -> "ProcessResultDTO":
        """Execute the full inventory processing pipeline.

        Parameters:
            file_bytes: Raw bytes of the uploaded Excel file
                (downloaded from Telegram or other channel).
            filename: Original filename (e.g. ``"store_42_20260707.xlsx"``)
                — used for traceability and error messages.
            tenant_id: The authenticated tenant scope.  All data
                will be persisted under this tenant.
            chat_id: The delivery address for the final report
                (Telegram chat ID, but the domain treats it as
                an opaque recipient identifier).

        Returns:
            ``ProcessResultDTO`` with summary and timing information.

        Raises:
            InvalidSheetSchemaError: Columns mismatch.
            SheetEmptyError: Zero data rows.
            UnauthorizedUserError: (handled earlier by middleware,
                but use case may re-verify).
            ReconciliationError: Unexpected domain failure.
        """
        ...


class AuthVerificationPort(abc.ABC):
    """Inbound port: verify a Telegram user's identity and resolve their
    tenant scope.

    Called by aiogram middleware (or any future auth mechanism) to
    authenticate an incoming request before the file-processing
    pipeline begins.

    The concrete implementation lives in the Application layer
    (``VerifyTelegramUserUseCase``).
    """

    @abc.abstractmethod
    async def verify(
        self,
        telegram_user_id: TelegramUserId,
    ) -> "AuthContextDTO":
        """Authenticate a Telegram user and return their authorisation
        context.

        Parameters:
            telegram_user_id: The calling user's Telegram identifier.

        Returns:
            ``AuthContextDTO`` with ``tenant_id``, ``store_id``,
            ``user_role``, and ``is_active``.

        Raises:
            UnauthorizedUserError: User not in whitelist or deactivated.
            TenantNotFoundError: Whitelist entry points to a
                non-existent tenant (data integrity issue).
        """
        ...


# ============================================================================
# OUTBOUND (DRIVEN) PORTS
# ============================================================================


class FileParserPort(abc.ABC):
    """Outbound port: parse raw Excel bytes into a structured, validated
    inventory DataFrame.

    Implemented by ``src.infrastructure.excel_parser.parser.ExcelParser``
    (openpyxl extraction + polars DataFrame + pandera schema check).

    The domain does NOT care about openpyxl, polars, or pandera —
    it only cares about receiving a valid ``ParsedSheetDTO``.
    """

    @abc.abstractmethod
    async def parse(self, file_bytes: bytes) -> "ParsedSheetDTO":
        """Extract and validate inventory data from Excel bytes.

        Parameters:
            file_bytes: Raw Excel file content (``.xlsx`` format).

        Returns:
            ``ParsedSheetDTO`` containing a validated polars
            DataFrame + metadata (row count, parse timestamp,
            filename).

        Raises:
            InvalidSheetSchemaError: Required columns missing or
                unexpected columns present.
            SheetEmptyError: Header present but zero data rows.
        """
        ...


class InventoryRepositoryPort(abc.ABC):
    """Outbound port: persist an ``InventorySnapshot`` aggregate
    (items + discrepancies) to durable storage.

    Implemented by ``src.infrastructure.persistence.repository``
    (SQLAlchemy 2.0 Async against PostgreSQL).

    **Hexagonal Highlight:** This port receives the **domain
    aggregate** directly — ``InventorySnapshot`` with its nested
    ``items`` and ``discrepancies`` lists.  The infrastructure
    adapter unpacks the aggregate into ORM rows.  The domain
    never sees SQL.
    """

    @abc.abstractmethod
    async def save_snapshot(self, snapshot: InventorySnapshot) -> None:
        """Persist an entire inventory snapshot to the database.

        The adapter MUST:
            1. INSERT a row into ``inventory_snapshots``.
            2. BULK INSERT all ``InventoryItem`` rows.
            3. BULK INSERT only the ``DiscrepancyItem`` rows
               (matched items may be omitted from the discrepancy
               table for storage efficiency).
            4. Wrap the above in a single database transaction
               so the snapshot is atomic.

        Parameters:
            snapshot: The fully-populated domain aggregate root
                containing tenant_id, store_id, parsed_at,
                items list, and discrepancies list.

        Raises:
            The adapter may raise infrastructure-specific exceptions
            (e.g., ``sqlalchemy.exc.IntegrityError``, connection
            failures).  The Application layer wraps these as needed.
            The Domain layer does NOT define persistence exceptions
            — those are infrastructure concerns.
        """
        ...


class TenantRepositoryPort(abc.ABC):
    """Outbound port: resolve a Telegram user into their tenant and
    whitelist record.

    Implemented by ``src.infrastructure.persistence.repository``
    (SQLAlchemy 2.0 Async — joins ``telegram_users`` ↔ ``tenants``).

    Used by the auth flow (middleware → ``VerifyTelegramUserUseCase``
    → this port) to authenticate every incoming request.
    """

    @abc.abstractmethod
    async def get_by_telegram_id(
        self,
        telegram_user_id: TelegramUserId,
    ) -> tuple[Tenant, TelegramUser]:
        """Look up the tenant and whitelist entry for a Telegram user.

        Parameters:
            telegram_user_id: The Telegram-provided user identifier.

        Returns:
            A tuple of ``(Tenant, TelegramUser)``.  The ``Tenant``
            provides the ``tenant_id`` scope for all subsequent
            operations; the ``TelegramUser`` provides role and
            active-status information.

        Raises:
            TenantNotFoundError: No tenant found for this Telegram
                user (user not whitelisted, or whitelist entry
                points to a deleted tenant).
        """
        ...


class ReportExporterPort(abc.ABC):
    """Outbound port: generate a Markdown summary + an .xlsx discrepancy
    report from an ``InventorySnapshot``.

    Implemented by ``src.infrastructure.excel_exporter.exporter.ExcelExporter``
    (openpyxl for the .xlsx attachment).

    The Markdown text produced here follows the domain's report
    format (defined in ``InventorySnapshot.build_markdown_report()``)
    but may be enriched with adapter-specific formatting
    (e.g., Telegram MarkdownV2 escaping).
    """

    @abc.abstractmethod
    async def export(
        self,
        snapshot: InventorySnapshot,
    ) -> "ReportResultDTO":
        """Build the final user-facing report from a reconciled snapshot.

        Parameters:
            snapshot: The fully-populated aggregate after
                reconciliation (items + discrepancies present).

        Returns:
            ``ReportResultDTO`` containing:
                - ``markdown_text``: The summary string for
                  ``send_message``.
                - ``excel_bytes``: The .xlsx attachment bytes
                  (only discrepancy rows, formatted).
                - ``suggested_filename``: e.g.
                  ``"discrepancies_store42_20260707.xlsx"``.
        """
        ...


class NotificationPort(abc.ABC):
    """Outbound port: deliver the final report to the end user.

    Implemented by a thin adapter wrapping the messaging platform's
    SDK (e.g., ``aiogram.Bot`` for Telegram).  The domain does NOT
    know about Telegram, WhatsApp, or Discord — it only knows
    "send a report to recipient at address X."

    The adapter is responsible for:
        1. Sending the Markdown text message.
        2. Sending the Excel file attachment.
        3. Handling partial failures (e.g., text sent but file
           upload failed — log and optionally retry).
    """

    @abc.abstractmethod
    async def send_report(
        self,
        chat_id: int,
        report: "ReportResultDTO",
    ) -> None:
        """Deliver the reconciliation report to a recipient.

        Parameters:
            chat_id: Opaque recipient address (Telegram chat ID,
                but could be a phone number for WhatsApp, a
                channel ID for Discord, etc.).
            report: The ``ReportResultDTO`` produced by
                ``ReportExporterPort.export()``, containing both
                Markdown text and Excel attachment bytes.

        Raises:
            The adapter may raise IO/network exceptions; the
            Application layer decides whether to retry, log,
            or notify a fallback channel.
        """
        ...