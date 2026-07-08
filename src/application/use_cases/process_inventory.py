"""ProcessInventoryUseCase — Full pipeline orchestration for inventory
Excel sheet uploads.

This use case implements ``FileProcessingPort`` (the inbound driving
port declared in ``src.domain.ports``) and orchestrates the complete
data flow described in ARCHITECTURE.md Steps 1–7:

    Parse → Validate → Reconcile → Persist → Export → Notify

All outbound ports are received via **explicit manual Constructor
Injection** (no magic DI framework).  The use case contains **no core
business rules** — those belong to the ``src.domain`` layer
(``DiscrepancyItem`` auto-classifies, ``InventorySnapshot`` aggregates).
"""

from __future__ import annotations

import time
from typing import Optional
import uuid

from src.domain.exceptions import DomainError
from src.domain.models import (
    DiscrepancyItem,
    InventoryItem,
    InventorySnapshot,
)
from src.domain.ports import (
    AuthVerificationPort,
    FileParserPort,
    FileProcessingPort,
    InventoryRepositoryPort,
    NotificationPort,
    ReportExporterPort,
)
from src.domain.schemas import (
    ParsedSheetDTO,
    ProcessResultDTO,
    ReportResultDTO,
)
from src.domain.value_objects import (
    ItemName,
    Quantity,
    Sku,
    StoreId,
    TelegramUserId,
    TenantId,
)


class ProcessInventoryUseCase(FileProcessingPort):
    """Orchestrate the full inventory processing pipeline.

    Implements the ``FileProcessingPort`` inbound port so that the
    Presentation layer (Telegram handler) calls ``.process()`` and
    receives a ``ProcessResultDTO`` — unaware of the 5 adapter ports,
    8 orchestration steps, or domain entity graph being built inside.

    All outbound ports are supplied via **Constructor Injection** at
    application startup (by ``src.application.composer.App``, Phase 3
    Task 3.4).  The use case never imports infrastructure code.
    """

    # ------------------------------------------------------------------
    # Constructor — Manual Dependency Injection (5 ports)
    # ------------------------------------------------------------------

    def __init__(
        self,
        auth_port: AuthVerificationPort,
        parser_port: FileParserPort,
        repo_port: InventoryRepositoryPort,
        exporter_port: ReportExporterPort,
        notification_port: NotificationPort,
    ) -> None:
        """Wire all five outbound ports via explicit manual injection.

        Each argument is type-hinted to its **ABC** defined in
        ``src.domain.ports``, guaranteeing that the composer supplies
        a real adapter implementing every abstract method.  The use
        case never knows *which* concrete adapter sits behind the
        interface — it only calls ``.parse()``, ``.save_snapshot()``,
        etc.

        Parameters
        ----------
        auth_port : AuthVerificationPort
            Resolves a Telegram user ID into an ``AuthContextDTO``
            (tenant scope, store, role, active status).
        parser_port : FileParserPort
            Parses raw Excel bytes into a validated ``ParsedSheetDTO``
            (``openpyxl`` + ``polars`` + ``pandera`` behind the port).
        repo_port : InventoryRepositoryPort
            Persists the ``InventorySnapshot`` aggregate (items +
            discrepancies) to PostgreSQL via async SQLAlchemy.
        exporter_port : ReportExporterPort
            Builds a Markdown summary + discrepancy Excel attachment
            as a ``ReportResultDTO``.
        notification_port : NotificationPort
            Delivers the Markdown message + Excel file to the user
            via the Telegram Bot API (aiogram behind the port).
        """
        self._auth = auth_port
        self._parser = parser_port
        self._repo = repo_port
        self._exporter = exporter_port
        self._notifier = notification_port

    # ------------------------------------------------------------------
    # Orchestration Entry Point
    # ------------------------------------------------------------------

    async def process(
        self,
        file_bytes: bytes,
        filename: str,
        tenant_id: TenantId,
        chat_id: int,
    ) -> "ProcessResultDTO":
        """Execute the full inventory processing pipeline.

        Delegates to ``execute()`` — the ``.process()`` method exists
        to satisfy ``FileProcessingPort`` (inbound port contract).
        The Presentation layer calls this; the signature matches the
        port exactly.

        Parameters
        ----------
        file_bytes : bytes
            Raw Excel file content downloaded from Telegram.
        filename : str
            Original upload filename for traceability.
        tenant_id : TenantId
            Authenticated tenant scope (resolved by middleware/auth).
        chat_id : int
            Opaque delivery address for the final report.

        Returns
        -------
        ProcessResultDTO
            Terminal summary with success flag, duration, snapshot ID,
            and report delivery status.
        """
        return await self.execute(
            telegram_user_id=0,  # already authenticated upstream
            chat_id=chat_id,
            filename=filename,
            file_bytes=file_bytes,
        )

    async def execute(
        self,
        telegram_user_id: int,
        chat_id: int,
        filename: str,
        file_bytes: bytes,
    ) -> ProcessResultDTO:
        """Run the eight-step inventory processing pipeline.

        This is the primary orchestrator called by the Presentation
        layer (Telegram file-upload handler).  It wraps the entire
        pipeline in a try/except block so that ANY failure (domain
        validation error, parsing error, database outage, notification
        failure) returns a well-formed, failed ``ProcessResultDTO``
        rather than propagating an unhandled exception.

        Parameters
        ----------
        telegram_user_id : int
            Raw Telegram user ID (for auth verification).
        chat_id : int
            Delivery address for the final report.
        filename : str
            Original filename for traceability & error messages.
        file_bytes : bytes
            Raw ``.xlsx`` content.

        Returns
        -------
        ProcessResultDTO
            Always returns a DTO — ``success=True`` with timing +
            snapshot ID on happy path, ``success=False`` with a
            human-readable error summary on failure.
        """
        start_ms = int(time.time() * 1000)
        snapshot_id: Optional[str] = None
        report_delivered: bool = False

        try:
            # ----------------------------------------------------------
            # STEP 1: Verify the calling Telegram user
            # ----------------------------------------------------------
            auth_ctx = await self._auth.verify(
                TelegramUserId(value=telegram_user_id)
            )
            resolved_tenant: TenantId = auth_ctx.tenant_id
            resolved_store: StoreId = auth_ctx.store_id

            if not auth_ctx.is_active:
                from src.domain.exceptions import UnauthorizedUserError

                raise UnauthorizedUserError(
                    telegram_user_id=telegram_user_id,
                    reason="account_deactivated",
                )

            # ----------------------------------------------------------
            # STEP 2: Parse the raw Excel bytes into validated rows
            # ----------------------------------------------------------
            parsed_sheet: ParsedSheetDTO = await self._parser.parse(file_bytes)

            if not parsed_sheet.rows:
                from src.domain.exceptions import SheetEmptyError

                raise SheetEmptyError(
                    filename=filename,
                    row_count=0,
                )

            # ----------------------------------------------------------
            # STEP 3: Map raw ParsedRowDTO → InventoryItem domain entity
            #
            # Each raw Excel row (strings / ints) is wrapped into
            # validated domain Value Objects (Sku, ItemName, Quantity)
            # and assembled into an InventoryItem entity.  This step
            # absorbs ALL value-object validation — malformed SKU,
            # empty item name, negative quantity → Pydantic raises
            # ValidationError → caught by the outer try/except and
            # returned as a failed ProcessResultDTO.
            # ----------------------------------------------------------
            inventory_items: list[InventoryItem] = []
            for row in parsed_sheet.rows:
                item = InventoryItem(
                    sku=Sku(value=row.sku),
                    item_name=ItemName(value=row.item_name),
                    system_qty=Quantity(value=row.system_qty),
                    actual_qty=Quantity(value=row.actual_qty),
                    tenant_id=resolved_tenant,
                )
                inventory_items.append(item)

            # ----------------------------------------------------------
            # STEP 4: Derive DiscrepancyItem for every line item
            #
            # Each InventoryItem is wrapped in a DiscrepancyItem.
            # The DiscrepancyItem entity **auto-computes** its diff
            # and status via @model_validator — the use case never
            # supplies "diff" or "status".  Only items whose status
            # is NOT MATCHED are collected into the discrepancies list
            # (matched items are present in `items` but omitted from
            # `discrepancies` for storage efficiency).
            # ----------------------------------------------------------
            discrepancy_items: list[DiscrepancyItem] = []
            for item in inventory_items:
                disc = DiscrepancyItem(inventory_item=item)
                if disc.status.value != "MATCHED":
                    discrepancy_items.append(disc)

            # ----------------------------------------------------------
            # STEP 5: Build the InventorySnapshot aggregate root
            # ----------------------------------------------------------
            snapshot = InventorySnapshot(
                id=uuid.uuid4(),
                tenant_id=resolved_tenant,
                store_id=resolved_store,
                items=inventory_items,
                discrepancies=discrepancy_items,
            )
            snapshot_id = str(snapshot.id)

            # ----------------------------------------------------------
            # STEP 6: Persist to database via repository port
            # ----------------------------------------------------------
            await self._repo.save_snapshot(snapshot)

            # ----------------------------------------------------------
            # STEP 7: Generate Markdown + Excel report via exporter port
            # ----------------------------------------------------------
            report_result: ReportResultDTO = await self._exporter.export(snapshot)

            # ----------------------------------------------------------
            # STEP 8: Deliver the report to the user via notification port
            # ----------------------------------------------------------
            await self._notifier.send_report(chat_id, report_result)
            report_delivered = True

            # ----------------------------------------------------------
            # SUCCESS — Build and return the terminal DTO
            # ----------------------------------------------------------
            end_ms = int(time.time() * 1000)
            duration_ms = end_ms - start_ms

            summary_text = (
                f"✅ Processed {snapshot.total_items} items — "
                f"{snapshot.matched_count} matched, "
                f"{len(snapshot.discrepancies)} discrepancies found. "
                f"Full report sent."
            )

            return ProcessResultDTO(
                success=True,
                summary=summary_text,
                duration_ms=duration_ms,
                snapshot_id=snapshot_id,
                report_delivered=report_delivered,
            )

        except DomainError as exc:
            # Domain-level failures (invalid schema, empty sheet,
            # unauthorized user, reconciliation error — anything
            # inheriting from DomainError).
            end_ms = int(time.time() * 1000)
            duration_ms = end_ms - start_ms

            return ProcessResultDTO(
                success=False,
                summary=f"❌ Processing failed: {exc!s}",
                duration_ms=duration_ms,
                snapshot_id=snapshot_id,
                report_delivered=report_delivered,
            )

        except Exception as exc:
            # Infrastructure-level or unexpected failures (DB
            # connection lost, Telegram API timeout, etc.).
            end_ms = int(time.time() * 1000)
            duration_ms = end_ms - start_ms

            return ProcessResultDTO(
                success=False,
                summary=f"❌ Unexpected error: {exc.__class__.__name__}: {exc!s}",
                duration_ms=duration_ms,
                snapshot_id=snapshot_id,
                report_delivered=report_delivered,
            )
