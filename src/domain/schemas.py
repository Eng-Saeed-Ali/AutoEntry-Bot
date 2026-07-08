"""📦 Domain Schemas (DTOs) for the AutoEntry Bot.

Data Transfer Objects are the *lingua franca* crossing port boundaries
in the Hexagonal Architecture. They are plain Pydantic V2 ``BaseModel``
instances — carriers of structured data across layer boundaries,
not holders of business logic.

Design Principles:
    - ``frozen=True`` on every DTO: absolute immutability across
      layer boundaries. Once constructed, a DTO cannot be mutated —
      this prevents accidental or malicious tampering mid-pipeline.
    - ``validate_assignment=False``: redundant with freeze but
      explicit — no attribute reassignment bypass.
    - Raw types (``str``, ``int``) for infrastructure-to-domain
      DTOs; domain Value Objects for domain-internal DTOs.
      Infrastructure adapters produce raw types (openpyxl cells
      are strings/integers); the Application layer wraps them
      into domain VOs/entities.
    - Zero cross-layer imports: only ``pydantic``, Python stdlib,
      and ``src.domain.value_objects`` (for DTOs using VOs).
      No ``src.infrastructure``, ``src.application``, or
      ``src.presentation`` imports — the Iron Law of Hexagonal
      Architecture.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

# Value Objects are imported for DTOs that sit within the
# domain/application boundary where validated types are expected
# (e.g., AuthContextDTO uses TenantId/StoreId/TelegramUserId —
# these fields are already authenticated and validated by the time
# this DTO is constructed).
from src.domain.value_objects import StoreId, TelegramUserId, TenantId

# ---------------------------------------------------------------------------
# Shared Excel Column Constants
# ---------------------------------------------------------------------------
# These are the canonical column names expected in every uploaded inventory
# Excel file.  They live in the domain layer so that infrastructure parsers,
# presentation error messages, and future exporters all reference a single
# source of truth — no duplicated magic strings across layers.

EXPECTED_EXCEL_COLUMNS: tuple[str, str, str, str] = (
    "SKU",
    "Item_Name",
    "System_Qty",
    "Actual_Qty",
)

EXCEL_COLUMN_DTYPE_MAP: dict[str, type] = {
    "SKU": str,
    "Item_Name": str,
    "System_Qty": int,
    "Actual_Qty": int,
}

# ---------------------------------------------------------------------------
# 1. ParsedRowDTO  (Raw row from Excel sheet)
# ---------------------------------------------------------------------------


class ParsedRowDTO(BaseModel, frozen=True, validate_assignment=False):
    """A single raw row extracted from the Excel sheet by the
    infrastructure adapter BEFORE entering the application pipeline.

    The infrastructure adapter (``ExcelParser``) reads openpyxl cells
    as raw strings/integers.  Wrapping into domain Value Objects
    (``Sku``, ``ItemName``, ``Quantity``) happens in the Application
    layer's use case, not here — this DTO is a "dumb pipe" across
    the infrastructure→domain boundary.
    """

    sku: str = Field(
        ...,
        min_length=1,
        max_length=50,
        description="Raw SKU string from the Excel cell (e.g. 'ABC-123').",
    )
    item_name: str = Field(
        ...,
        min_length=1,
        max_length=200,
        description="Raw item/product name from the Excel cell.",
    )
    system_qty: int = Field(
        ...,
        ge=0,
        description="Expected quantity according to the ERP/system.",
    )
    actual_qty: int = Field(
        ...,
        ge=0,
        description="Physically counted quantity.",
    )


# ---------------------------------------------------------------------------
# 2. ParsedSheetDTO  (Full sheet metadata + payload)
# ---------------------------------------------------------------------------


class ParsedSheetDTO(BaseModel, frozen=True, validate_assignment=False):
    """The full sheet metadata and payload delivered from the
    presentation/infrastructure boundary.

    Returned by ``FileParserPort.parse()`` after successful extraction
    and validation.  Carries the tenant/uploader context alongside the
    list of validated rows.
    """

    tenant_id: str = Field(
        ...,
        min_length=1,
        description="Tenant identifier string (to be resolved to TenantId VO downstream).",
    )
    store_id: str = Field(
        ...,
        min_length=1,
        description="Store identifier string (to be resolved to StoreId VO downstream).",
    )
    uploaded_by: int = Field(
        ...,
        gt=0,
        description="Telegram user ID of the uploading store manager.",
    )
    rows: list[ParsedRowDTO] = Field(
        default_factory=list,
        description="Validated rows extracted from the Excel sheet. Empty list = no data rows (should be caught by SheetEmptyError upstream).",
    )


# ---------------------------------------------------------------------------
# 3. DiscrepancyRowDTO  (Granular discrepancy row for export/presentation)
# ---------------------------------------------------------------------------


class DiscrepancyRowDTO(BaseModel, frozen=True, validate_assignment=False):
    """A granular row ready for the generated discrepancy Excel sheet
    or presentation view (Telegram Markdown / REST API response).

    Carries already-validated, post-reconciliation values derived from
    ``DiscrepancyItem`` entities by the Application layer.
    """

    sku: str = Field(
        ...,
        min_length=1,
        max_length=50,
        description="The SKU of the discrepant item.",
    )
    item_name: str = Field(
        ...,
        min_length=1,
        max_length=200,
        description="Human-readable item name.",
    )
    system_qty: int = Field(
        ...,
        ge=0,
        description="Expected system quantity.",
    )
    actual_qty: int = Field(
        ...,
        ge=0,
        description="Physically counted quantity.",
    )
    diff_amount: int = Field(
        ...,
        description="Difference (actual - system). Negative = shortage, positive = surplus, zero should not appear here (matched items excluded).",
    )
    status: str = Field(
        ...,
        min_length=1,
        description="Discrepancy classification, e.g. 'SHORTAGE', 'SURPLUS', 'UNTRACKED_ITEM', 'MISSING_ENTIRELY'.",
    )


# ---------------------------------------------------------------------------
# 4. ReportResultDTO  (Final execution summary + discrepancy data)
# ---------------------------------------------------------------------------


class ReportResultDTO(BaseModel, frozen=True, validate_assignment=False):
    """The final execution summary metrics and data returned by the
    domain to be passed to the presentation adapters (Telegram,
    Markdown renderer, REST API, etc.).

    Produced by ``ReportExporterPort.export()`` and consumed by
    ``NotificationPort.send_report()``.  This is the terminal DTO
    in the outbound pipeline.
    """

    tenant_id: str = Field(
        ...,
        min_length=1,
        description="Tenant scope identifier (string form).",
    )
    store_id: str = Field(
        ...,
        min_length=1,
        description="Store identifier (string form).",
    )
    total_items: int = Field(
        ...,
        ge=0,
        description="Total number of items processed in the sheet.",
    )
    total_discrepancies: int = Field(
        ...,
        ge=0,
        description="Number of rows with non-zero diff (shortages + surpluses + untracked + missing).",
    )
    summary_markdown: str = Field(
        ...,
        min_length=1,
        description="Telegram-compatible Markdown summary string produced by InventorySnapshot.build_markdown_report().",
    )
    discrepancy_rows: list[DiscrepancyRowDTO] = Field(
        default_factory=list,
        description="Only the rows with discrepancies. May be empty if everything matches.",
    )
    excel_bytes: bytes | None = Field(
        default=None,
        description="Generated Excel report file bytes (None if exporter not yet built or export skipped).",
    )


# ---------------------------------------------------------------------------
# 5. AuthContextDTO  (Authentication context — domain-internal DTO)
# ---------------------------------------------------------------------------

# TelegramUserRole is defined in models.py as Literal["admin", "manager"].
# We import the tuple for runtime iteration but represent the role
# as a plain str in the DTO for maximum compatibility across layers.
_AUTH_ROLE_OPTIONS: tuple[str, ...] = ("admin", "manager")


class AuthContextDTO(BaseModel, frozen=True, validate_assignment=False):
    """Authorisation context returned after successful Telegram user
    verification.

    Returned by ``AuthVerificationPort.verify()`` and consumed by
    the Application layer's middleware → handler pipeline.  The
    ``tenant_id`` and ``store_id`` here are validated domain VOs
    because this DTO sits within the domain/application boundary
    where type-safety is expected.

    Unlike infrastructure-crossing DTOs (``ParsedSheetDTO``,
    ``ReportResultDTO``), this one uses domain Value Objects
    because its fields come from an already-authenticated,
    already-validated database lookup.
    """

    tenant_id: TenantId = Field(
        description="Resolved tenant scope for all subsequent operations.",
    )
    store_id: StoreId = Field(
        description="Resolved store/location within the tenant.",
    )
    telegram_user_id: TelegramUserId = Field(
        description="The authenticated Telegram user's immutable ID.",
    )
    user_role: str = Field(
        ...,
        description="Authorisation role: 'admin' (full access) or 'manager' (submit-only).",
    )
    is_active: bool = Field(
        description="Whether the whitelist entry is currently active.",
    )


# ---------------------------------------------------------------------------
# 6. ReconciliationSummaryDTO  (Counts breakdown)
# ---------------------------------------------------------------------------


class ReconciliationSummaryDTO(BaseModel, frozen=True, validate_assignment=False):
    """Aggregated reconciliation counts produced by the domain
    reconciliation service (Task 1.6).

    Passed between the reconciliation service and the report
    generation / persistence pipeline.  Provides a structured
    breakdown of matched vs. discrepant rows.
    """

    total: int = Field(
        ...,
        ge=0,
        description="Total number of rows processed.",
    )
    matched: int = Field(
        ...,
        ge=0,
        description="Rows where System_Qty == Actual_Qty (no anomaly).",
    )
    shortages: int = Field(
        ...,
        ge=0,
        description="Rows where Actual_Qty < System_Qty (potential loss/theft).",
    )
    surpluses: int = Field(
        ...,
        ge=0,
        description="Rows where Actual_Qty > System_Qty (overstock anomaly).",
    )
    untracked: int = Field(
        ...,
        ge=0,
        description="Items found physically (Actual_Qty > 0) but missing from ERP (System_Qty == 0).",
    )
    missing: int = Field(
        ...,
        ge=0,
        description="Items in ERP (System_Qty > 0) but not found during physical count (Actual_Qty == 0).",
    )


# ---------------------------------------------------------------------------
# 7. ProcessResultDTO  (Pipeline completion summary)
# ---------------------------------------------------------------------------


class ProcessResultDTO(BaseModel, frozen=True, validate_assignment=False):
    """Terminal summary returned by the full inventory processing
    pipeline to the Presentation layer.

    Returned by ``FileProcessingPort.process()`` (implemented by
    ``ProcessInventoryUseCase`` in the Application layer).  Provides
    the presentation adapter (Telegram handler) with enough
    information to send a user-facing confirmation message.
    """

    success: bool = Field(
        description="True if the entire pipeline completed without errors.",
    )
    summary: str = Field(
        ...,
        min_length=1,
        description="Human-readable summary string suitable for a Telegram reply (e.g. '✅ Processed 127 items, 4 discrepancies found — full report sent.').",
    )
    duration_ms: int = Field(
        ...,
        ge=0,
        description="Total pipeline execution time in milliseconds.",
    )
    snapshot_id: str | None = Field(
        default=None,
        description="UUID of the persisted InventorySnapshot (string form). None if persistence failed or was skipped.",
    )
    report_delivered: bool = Field(
        default=False,
        description="True if the NotificationPort confirmed successful delivery of the Markdown + Excel report to the user.",
    )
