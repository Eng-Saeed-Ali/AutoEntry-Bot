"""Immutable domain Value Objects for the AutoEntry Bot.

All Value Objects in this module are frozen Pydantic V2 models —
once constructed they cannot be mutated, enforcing integrity across
the domain. They carry domain-specific validation (e.g., SKUs are
always uppercase, quantities are non-negative, Telegram user IDs
are positive integers).

Design Principles:
    - `frozen=True` prevents any accidental mutation.
    - `validate_assignment=False` prevents bypassing validation via
      attribute reassignment (redundant with freeze, but explicit).
    - Every VO is self-validating at construction time. No invalid
      state can ever exist.
    - Zero external framework dependencies — only `pydantic` and
      the Python standard library.
"""

from __future__ import annotations

from enum import StrEnum
import re

from pydantic import BaseModel, Field, field_validator

# ---------------------------------------------------------------------------
# Discrepancy Status Enum
# ---------------------------------------------------------------------------


class DiscrepancyStatus(StrEnum):
    """Classification of an inventory row after reconciliation.

    These mirror the reconciliation logic defined in
    ``InventoryReconciliationService`` (Phase 1.6):

    * MATCHED: System_Qty == Actual_Qty — everything in order.
    * SHORTAGE: Actual_Qty < System_Qty — physical stock is less than
      system expectation (potential loss/theft).
    * SURPLUS: Actual_Qty > System_Qty — physical stock exceeds system
      expectation (overstock anomaly).
    * UNTRACKED_ITEM: System_Qty == 0 and Actual_Qty > 0 — item exists
      physically but not in ERP.
    * MISSING_ENTIRELY: System_Qty > 0 and Actual_Qty == 0 — item
      exists in ERP but was not found during physical count.
    """

    MATCHED = "MATCHED"
    SHORTAGE = "SHORTAGE"
    SURPLUS = "SURPLUS"
    UNTRACKED_ITEM = "UNTRACKED_ITEM"
    MISSING_ENTIRELY = "MISSING_ENTIRELY"


# ---------------------------------------------------------------------------
# Core Value Objects
# ---------------------------------------------------------------------------

_SKU_PATTERN = re.compile(r"^[A-Z0-9\-_]+$")


class Sku(BaseModel, frozen=True, validate_assignment=False):
    """Stock Keeping Unit identifier.

    Domain constraints:
        - Stored as uppercase, stripped of leading/trailing whitespace.
        - Must match ``^[A-Z0-9\\-_]+$`` (alphanumeric uppercase,
          hyphens, underscores).
        - Length 1–50 characters.
        - Typical format: ``ABC-123``, ``SKU_0042``.
    """

    value: str = Field(
        ...,
        min_length=1,
        max_length=50,
        description="Uppercase, whitespace-stripped SKU string (e.g. ABC-123-XYZ).",
    )

    @field_validator("value", mode="before")
    @classmethod
    def _normalize_sku(cls, raw: object) -> str:
        """Strip whitespace and convert to uppercase before validation."""
        if not isinstance(raw, str):
            raise ValueError(f"SKU must be a string, got {type(raw).__name__}")
        return raw.strip().upper()

    @field_validator("value", mode="after")
    @classmethod
    def _validate_sku_pattern(cls, cleaned: str) -> str:
        """Ensure the cleaned SKU only contains allowed characters."""
        if not _SKU_PATTERN.match(cleaned):
            raise ValueError(
                f"SKU '{cleaned}' contains invalid characters. "
                f"Allowed: uppercase A-Z, digits 0-9, hyphen, underscore."
            )
        return cleaned

    def __str__(self) -> str:
        return self.value

    def __hash__(self) -> int:
        return hash(self.value)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Sku):
            return self.value == other.value
        if isinstance(other, str):
            return self.value == other.strip().upper()
        return NotImplemented


class Quantity(BaseModel, frozen=True, validate_assignment=False):
    """A non-negative integer quantity counter for inventory counts.

    Domain constraints:
        - ``value >= 0``. Negative quantities have no physical meaning
          in a count context (negative diffs are captured by
          ``DiffAmount``).
        - Integer-only (float partial units are not supported for
          this domain's inventory counts).
    """

    value: int = Field(
        ...,
        ge=0,
        description="Non-negative integer representing an item count.",
    )

    def __str__(self) -> str:
        return str(self.value)

    def __int__(self) -> int:
        return self.value


class TenantId(BaseModel, frozen=True, validate_assignment=False):
    """Globally unique tenant identifier.

    A tenant is a business/organization using the AutoEntry platform.
    All data is scoped to a tenant for multi-tenant isolation.

    Domain constraints:
        - Positive, non-zero integer.
    """

    value: int = Field(
        ...,
        gt=0,
        description="Positive, non-zero integer identifying a tenant.",
    )

    def __str__(self) -> str:
        return str(self.value)

    def __int__(self) -> int:
        return self.value


class StoreId(BaseModel, frozen=True, validate_assignment=False):
    """A physical store location within a tenant's organization.

    Domain constraints:
        - Positive, non-zero integer.
        - Scoped under a ``TenantId`` (the tenant context is supplied
          externally by the application layer).
    """

    value: int = Field(
        ...,
        gt=0,
        description="Positive, non-zero integer identifying a store.",
    )

    def __str__(self) -> str:
        return str(self.value)

    def __int__(self) -> int:
        return self.value


class TelegramUserId(BaseModel, frozen=True, validate_assignment=False):
    """Telegram user identifier used for authentication whitelisting.

    Domain constraints:
        - Positive, non-zero integer.
        - This is a raw Telegram-provided user id (immutable,
          globally unique within Telegram).
    """

    value: int = Field(
        ...,
        gt=0,
        description="Positive, non-zero integer — Telegram's immutable user ID.",
    )

    def __str__(self) -> str:
        return str(self.value)

    def __int__(self) -> int:
        return self.value


class ItemName(BaseModel, frozen=True, validate_assignment=False):
    """Human-readable product/item display name.

    Domain constraints:
        - Stripped of leading/trailing whitespace.
        - Length 1–200 characters (accommodates verbose retail
          descriptions without unbounded storage).
    """

    value: str = Field(
        ...,
        min_length=1,
        max_length=200,
        description="Whitespace-stripped product name (1–200 chars).",
    )

    @field_validator("value", mode="before")
    @classmethod
    def _normalize_name(cls, raw: object) -> str:
        """Strip whitespace before validation."""
        if not isinstance(raw, str):
            raise ValueError(f"ItemName must be a string, got {type(raw).__name__}")
        return raw.strip()

    def __str__(self) -> str:
        return self.value

    def __hash__(self) -> int:
        return hash(self.value)


class DiffAmount(BaseModel, frozen=True, validate_assignment=False):
    """Difference between actual and system quantities for an item.

    Computed as ``Actual_Qty - System_Qty`` during reconciliation.

    Domain constraints:
        - Integer (may be negative: shortages produce negative diffs).
        - No range restriction — the magnitude reflects real counts.
    """

    value: int = Field(
        ...,
        description="Integer difference (Actual - System). Negative = shortage, Positive = surplus, Zero = matched.",
    )

    def __str__(self) -> str:
        return str(self.value)

    def __int__(self) -> int:
        return self.value

    @property
    def is_matched(self) -> bool:
        """True when the difference is zero (no discrepancy)."""
        return self.value == 0

    @property
    def is_shortage(self) -> bool:
        """True when the actual count is below the system expectation."""
        return self.value < 0

    @property
    def is_surplus(self) -> bool:
        """True when the actual count exceeds the system expectation."""
        return self.value > 0


_SHA256_HEX_PATTERN = re.compile(r"^[a-fA-F0-9]{64}$")


class ExcelFileChecksum(BaseModel, frozen=True, validate_assignment=False):
    """SHA-256 checksum of an uploaded Excel file.

    Stored as a lowercase hex-encoded string. Used for deduplication
    and tamper detection.

    Domain constraints:
        - Exactly 64 hexadecimal characters (SHA-256 digest).
        - Stored as lowercase.
    """

    value: str = Field(
        ...,
        min_length=64,
        max_length=64,
        description="Lowercase 64-character SHA-256 hex digest.",
    )

    @field_validator("value", mode="before")
    @classmethod
    def _normalize_checksum(cls, raw: object) -> str:
        """Strip whitespace and convert to lowercase."""
        if not isinstance(raw, str):
            raise ValueError(f"Checksum must be a string, got {type(raw).__name__}")
        return raw.strip().lower()

    @field_validator("value", mode="after")
    @classmethod
    def _validate_sha256_pattern(cls, cleaned: str) -> str:
        """Ensure the string matches the SHA-256 hex format."""
        if not _SHA256_HEX_PATTERN.match(cleaned):
            raise ValueError(
                f"'{cleaned}' is not a valid SHA-256 hex digest "
                f"(expected exactly 64 hex characters)."
            )
        return cleaned

    def __str__(self) -> str:
        return self.value

    def __hash__(self) -> int:
        return hash(self.value)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, ExcelFileChecksum):
            return self.value == other.value
        if isinstance(other, str):
            return self.value == other.strip().lower()
        return NotImplemented
