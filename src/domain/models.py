"""Domain Entities and Aggregate Roots for the AutoEntry Bot.

All entities in this module are Pydantic V2 ``BaseModel`` instances
(not ``frozen=True`` — entities can evolve their internal state).
They compose Value Objects from ``src.domain.value_objects`` as typed
fields and encapsulate the core business logic (diff computation,
discrepancy classification, snapshot summarisation).

Design Principles:
    - Zero ORM-style foreign keys. Relationships use domain identity
      references: VO composition or plain ID references (UUID / int).
    - Business rules are expressed as properties / methods on the
      entities themselves — no anemic domain model.
    - ``from __future__ import annotations`` enables forward
      references and keeps startup lean.
    - Only imports from ``pydantic``, Python stdlib, and
      ``src.domain.value_objects`` — the Iron Law of Hexagonal
      Architecture.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal
import uuid

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, model_validator

from src.domain.value_objects import (
    DiffAmount,
    DiscrepancyStatus,
    ItemName,
    Quantity,
    Sku,
    StoreId,
    TelegramUserId,
    TenantId,
)

# ---------------------------------------------------------------------------
# 1. TelegramUser  (Whitelist entity — reference data)
# ---------------------------------------------------------------------------

TelegramUserRole = Literal["admin", "manager"]

TELEGRAM_USER_ROLES: tuple[TelegramUserRole, ...] = ("admin", "manager")


class TelegramUser(BaseModel):
    """A whitelisted Telegram user authorised to interact with the bot.

    Each ``TelegramUser`` is scoped to a single tenant via
    ``tenant_id``.  Only users whose ``telegram_user_id`` appears
    in the whitelist AND are ``is_active == True`` may submit
    inventory sheets.

    Domain invariant:
        - ``role`` must be one of the recognised roles
          (``"admin"`` / ``"manager"``).
    """

    model_config = ConfigDict(validate_assignment=True)

    user_id: TelegramUserId = Field(description="Immutable Telegram user ID from the chat platform.")
    tenant_id: TenantId = Field(description="Tenant this user belongs to.")
    is_active: bool = Field(default=True, description="False = user is soft-deactivated (denied access).")
    role: TelegramUserRole = Field(
        default="manager",
        description="Authorisation role: 'admin' (full access) or 'manager' (submit-only).",
    )

    @property
    def can_submit_inventory(self) -> bool:
        """A user may submit inventory sheets only when active."""
        return self.is_active


# ---------------------------------------------------------------------------
# 2. Tenant  (Organisation aggregate root)
# ---------------------------------------------------------------------------


class Tenant(BaseModel):
    """An organisation (business / store-chain) using the AutoEntry
    platform.

    A ``Tenant`` is the top-level aggregate root for multi-tenant
    isolation.  All inventory data, whitelist entries, and snapshots
    are scoped to a single tenant via ``TenantId``.
    """

    model_config = ConfigDict(validate_assignment=True)

    id: TenantId = Field(description="Globally unique tenant identifier.")
    name: str = Field(
        ..., min_length=1, max_length=200, description="Human-readable business / organisation name."
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="UTC timestamp of tenant registration.",
    )


# ---------------------------------------------------------------------------
# 3. InventoryItem  (Line-item entity belonging to an InventorySnapshot)
# ---------------------------------------------------------------------------


class InventoryItem(BaseModel):
    """A single inventory line item counted during a physical audit.

    Each ``InventoryItem`` captures the ERP-expected quantity
    (``system_qty``) alongside the physically-counted quantity
    (``actual_qty``).  The difference between the two is computed via
    ``compute_diff`` and used later by ``DiscrepancyItem`` for
    classification.

    Belongs to an ``InventorySnapshot`` aggregate via ``snapshot_id``
    and ``tenant_id``.
    """

    model_config = ConfigDict(validate_assignment=True)

    sku: Sku = Field(description="Stock Keeping Unit identifier.")
    item_name: ItemName = Field(description="Human-readable product display name.")
    system_qty: Quantity = Field(description="ERP-expected stock count (what the system thinks is there).")
    actual_qty: Quantity = Field(description="Physically-counted stock count (what was actually found).")
    tenant_id: TenantId = Field(description="Owning tenant (multi-tenant scoping).")
    snapshot_id: uuid.UUID = Field(
        default_factory=uuid.uuid4,
        description="FK-like reference to the parent InventorySnapshot aggregate.",
    )
    id: uuid.UUID = Field(
        default_factory=uuid.uuid4,
        description="Surrogate primary key for this line item.",
    )

    @property
    def diff_amount(self) -> DiffAmount:
        """Compute the numeric difference: Actual_Qty – System_Qty.

        Returns:
            ``DiffAmount`` value object.  Negative → shortage,
            zero → matched, positive → surplus.
        """
        return DiffAmount(value=self.actual_qty.value - self.system_qty.value)

    @property
    def is_matched(self) -> bool:
        """Convenience: True when the line item has zero discrepancy."""
        return self.diff_amount.is_matched


# ---------------------------------------------------------------------------
# 4. DiscrepancyItem  (Entity wrapping an InventoryItem with classification)
# ---------------------------------------------------------------------------


class DiscrepancyItem(BaseModel):
    """Encapsulates a single inventory line item along with its
    reconciliation result (diff + status).

    The ``status`` and ``diff`` are *derived* from the item's
    quantities — they are NEVER caller-supplied.  ``diff`` and
    ``status`` are stored in private attributes (``_diff``,
    ``_status``) computed by a ``@model_validator(mode="before")``
    that **strips** any caller-supplied values.  Exposed as read-only
    ``@property`` accessors.

    This guarantees the domain can never hold an inconsistent
    status — the classification is always computed from the
    inventory item's raw quantities.

    Only items with a non-zero diff or the special edge-cases
    (untracked / missing-entirely) are typically instantiated as
    ``DiscrepancyItem`` — matched items may be omitted from the
    discrepancy list for efficiency.
    """

    model_config = ConfigDict(validate_assignment=True)

    inventory_item: InventoryItem = Field(
        description="The raw inventory line item that generated the discrepancy."
    )

    # -- private backing stores (computed by the validator) ---------------
    _diff: DiffAmount = PrivateAttr(default_factory=lambda: DiffAmount(value=0))
    _status: DiscrepancyStatus = PrivateAttr(default=DiscrepancyStatus.MATCHED)

    @model_validator(mode="before")
    @classmethod
    def _strip_caller_overrides(cls, data: dict[str, object]) -> dict[str, object]:
        """Remove any caller-supplied ``diff`` / ``status`` keys.

        These fields do NOT appear in the ``__init__`` signature
        because there are no matching public ``Field`` declarations,
        but if a caller tries to pass them as kwargs Pydantic will
        accept them into ``data``.  We explicitly pop them so only
        the inventory_item drives classification.
        """
        _ = data.pop("diff", None)
        _ = data.pop("status", None)
        return data

    @model_validator(mode="after")
    def _derive_discrepancy(self) -> DiscrepancyItem:
        """Auto-compute ``_diff`` and ``_status`` from the inventory item.

        This implements the reconciliation logic from ARCHITECTURE.md
        Step 4 entirely within the domain entity:

        * ``diff == 0``                                  → ``MATCHED``
        * ``diff < 0``  (Actual < System)                → ``SHORTAGE``
        * ``diff > 0``  (Actual > System)                → ``SURPLUS``
        * ``System == 0`` AND ``Actual > 0``             → ``UNTRACKED_ITEM``
        * ``System > 0`` AND ``Actual == 0``             → ``MISSING_ENTIRELY``
        """
        item = self.inventory_item
        computed_diff = item.diff_amount

        self._diff = computed_diff

        sys_qty = item.system_qty.value
        act_qty = item.actual_qty.value
        diff_val = computed_diff.value

        if diff_val == 0:
            status = DiscrepancyStatus.MATCHED
        elif sys_qty == 0 and act_qty > 0:
            status = DiscrepancyStatus.UNTRACKED_ITEM
        elif sys_qty > 0 and act_qty == 0:
            status = DiscrepancyStatus.MISSING_ENTIRELY
        elif diff_val < 0:
            status = DiscrepancyStatus.SHORTAGE
        else:  # diff_val > 0
            status = DiscrepancyStatus.SURPLUS

        self._status = status
        return self

    # -- read-only public accessors ---------------------------------------

    @property
    def diff(self) -> DiffAmount:
        """Read-only: Actual minus System quantity (derived)."""
        return self._diff

    @property
    def status(self) -> DiscrepancyStatus:
        """Read-only: Classification derived from the item's quantities."""
        return self._status

    @property
    def sku(self) -> str:
        """Convenience accessor: return the SKU as a plain string."""
        return str(self.inventory_item.sku)

    @property
    def item_name(self) -> str:
        """Convenience accessor: return the ItemName as a plain string."""
        return str(self.inventory_item.item_name)


# ---------------------------------------------------------------------------
# 5. InventorySnapshot  (Aggregate Root for a single upload/audit)
# ---------------------------------------------------------------------------


class InventorySnapshot(BaseModel):
    """Aggregate Root representing a single inventory upload session.

    Owns a collection of ``InventoryItem`` line items (1:N) and
    a derived collection of ``DiscrepancyItem`` entries (computed
    from the items).

    Provides domain summarisation suitable for generating the
    Markdown report described in ARCHITECTURE.md Step 6.
    """

    model_config = ConfigDict(validate_assignment=True)

    id: uuid.UUID = Field(
        default_factory=uuid.uuid4,
        description="Surrogate primary key for the snapshot aggregate.",
    )
    tenant_id: TenantId = Field(description="Owning tenant for multi-tenant isolation.")
    store_id: StoreId = Field(description="Specific store location within the tenant.")
    parsed_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="UTC timestamp when the Excel sheet was parsed/reconciled.",
    )
    items: list[InventoryItem] = Field(
        default_factory=list,
        description="All inventory line items (both matched and discrepant).",
    )
    discrepancies: list[DiscrepancyItem] = Field(
        default_factory=list,
        description="Only the items that have a non-zero diff or anomaly status.",
    )

    @property
    def total_items(self) -> int:
        """Total number of line items in this snapshot."""
        return len(self.items)

    @property
    def matched_count(self) -> int:
        """Number of items whose diff is zero."""
        return sum(1 for item in self.items if item.is_matched)

    @property
    def shortage_count(self) -> int:
        """Number of discrepancy items classified as SHORTAGE."""
        return sum(1 for d in self.discrepancies if d.status == DiscrepancyStatus.SHORTAGE)

    @property
    def surplus_count(self) -> int:
        """Number of discrepancy items classified as SURPLUS."""
        return sum(1 for d in self.discrepancies if d.status == DiscrepancyStatus.SURPLUS)

    @property
    def untracked_count(self) -> int:
        """Number of discrepancy items classified as UNTRACKED_ITEM."""
        return sum(1 for d in self.discrepancies if d.status == DiscrepancyStatus.UNTRACKED_ITEM)

    @property
    def missing_count(self) -> int:
        """Number of discrepancy items classified as MISSING_ENTIRELY."""
        return sum(1 for d in self.discrepancies if d.status == DiscrepancyStatus.MISSING_ENTIRELY)

    @property
    def has_anomalies(self) -> bool:
        """True when at least one discrepancy exists in the snapshot."""
        return len(self.discrepancies) > 0

    @property
    def summary(self) -> dict[str, int]:
        """Return a dictionary suitable for report generation.

        Keys:
            * ``total`` — total line items
            * ``matched`` — items with zero diff
            * ``shortages`` — SHORTAGE classified items
            * ``surpluses`` — SURPLUS classified items
            * ``untracked`` — UNTRACKED_ITEM classified items
            * ``missing`` — MISSING_ENTIRELY classified items
        """
        return {
            "total": self.total_items,
            "matched": self.matched_count,
            "shortages": self.shortage_count,
            "surpluses": self.surplus_count,
            "untracked": self.untracked_count,
            "missing": self.missing_count,
        }

    def build_markdown_report(self) -> str:
        """Generate a concise Markdown summary string for Telegram.

        The format follows ARCHITECTURE.md Step 6 exactly, using
        Telegram-compatible MarkdownV2-style characters.

        Returns:
            A multi-line Markdown string ready for ``send_message``.
        """
        s = self.summary
        lines: list[str] = [
            "📊 *Inventory Reconciliation Report*",
            f"🏪 Store: {self.store_id}  |  📅 {self.parsed_at.strftime('%Y-%m-%d %H:%M UTC')}",
            "",
            f"✅ Matched: {s['matched']} items",
        ]

        if s["shortages"] > 0:
            lines.append(f"⚠️  Shortages: {s['shortages']} items")
        if s["surpluses"] > 0:
            lines.append(f"🔄 Surplus: {s['surpluses']} items")
        if s["untracked"] > 0:
            lines.append(f"❓ Untracked: {s['untracked']} new items found")
        if s["missing"] > 0:
            lines.append(f"🚫 Missing Entirely: {s['missing']} items not found")

        if self.has_anomalies:
            lines.append("")
            lines.append("⚠️ *Action Required:* Discrepancies need investigation.")

        return "\n".join(lines)
