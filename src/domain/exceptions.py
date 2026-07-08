"""Domain-specific exceptions for the AutoEntry Bot.

All exceptions in this module inherit from ``DomainError`` — a
common base class that marks every domain-originated error.  This
allows the Application layer (use cases) to catch ``DomainError``
broadly while still distinguishing specific failures when needed.

Design Principles:
    - Plain Python exception classes (NOT pydantic models).
      Exceptions are control-flow constructs, not data-transfer
      objects.  They carry contextual fields as plain public
      instance attributes stored via ``__init__``.
    - Zero imports from ``src.domain`` (no ``value_objects``, no
      ``models``).  Fields accept raw Python primitives (``int``,
      ``str``) to keep the module maximally independent — even
      Value Objects may be created *after* an exception is caught.
    - Every concrete exception overrides ``__str__`` to produce a
      human-readable diagnostic message suitable for logging or
      user-facing error replies (the presentation layer may
      further sanitise these).
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# 1. Base Domain Error
# ---------------------------------------------------------------------------


class DomainError(Exception):
    """Common base for every exception originating in the Domain layer.

    The Application layer should catch ``DomainError`` as the
    broadest safety-net for unexpected domain failures, while
    individual use cases may catch more-specific subclasses for
    targeted recovery (e.g., returning a user-friendly schema
    hint when ``InvalidSheetSchemaError`` is raised).

    Parameters:
        message: Human-readable description of the failure.
            Required — every domain error must explain *why* it
            occurred.
    """

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)

    def __str__(self) -> str:
        return self.message

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(message={self.message!r})"


# ---------------------------------------------------------------------------
# 2. Schema / Parsing Exceptions
# ---------------------------------------------------------------------------


class InvalidSheetSchemaError(DomainError):
    """Raised when an uploaded Excel sheet does not match the expected
    column schema.

    The Excel parser adapter (infrastructure) validates columns
    before domain reconciliation.  If columns are missing or
    unexpected extras appear, this exception is raised so the
    Application layer can respond with a user-friendly message
    listing the expected format.

    Parameters:
        missing_columns: Column names that were expected but absent.
        unexpected_columns: Column names that were present but not
            recognised (optional — some validators ignore extras).
        filename: Original upload filename for traceability.
            Optional; may be ``None`` when the source is a byte
            stream without a name.
    """

    def __init__(
        self,
        missing_columns: list[str],
        unexpected_columns: list[str] | None = None,
        filename: str | None = None,
    ) -> None:
        self.missing_columns = missing_columns
        self.unexpected_columns = unexpected_columns or []
        self.filename = filename
        msg_parts = ["Invalid sheet schema"]
        if filename:
            msg_parts.append(f"in file '{filename}'")
        if missing_columns:
            msg_parts.append(f"| missing columns: {missing_columns}")
        if self.unexpected_columns:
            msg_parts.append(f"| unexpected columns: {self.unexpected_columns}")
        super().__init__(" ".join(msg_parts))

    def __repr__(self) -> str:
        return (
            f"{self.__class__.__name__}("
            f"missing_columns={self.missing_columns!r}, "
            f"unexpected_columns={self.unexpected_columns!r}, "
            f"filename={self.filename!r}"
            f")"
        )


class SheetEmptyError(DomainError):
    """Raised when an Excel sheet is parsed successfully but contains
    zero data rows (i.e., only a header row or completely blank).

    An empty sheet is structurally valid but semantically
    meaningless for inventory reconciliation — there are no items
    to reconcile.

    Parameters:
        filename: Original upload filename. Optional.
        row_count: Number of data rows found (always 0 for this
            exception, but explicit for logging clarity).
    """

    def __init__(
        self,
        filename: str | None = None,
        row_count: int = 0,
    ) -> None:
        self.filename = filename
        self.row_count = row_count
        msg = "Sheet is empty (0 data rows)"
        if filename:
            msg += f" in file '{filename}'"
        super().__init__(msg)

    def __repr__(self) -> str:
        return (
            f"{self.__class__.__name__}("
            f"filename={self.filename!r}, "
            f"row_count={self.row_count!r}"
            f")"
        )


# ---------------------------------------------------------------------------
# 3. Tenant / Authentication Exceptions
# ---------------------------------------------------------------------------


class TenantNotFoundError(DomainError):
    """Raised when a tenant lookup fails — no tenant matches the given
    identifier or Telegram user reference.

    This is a domain-level "not found" distinct from infrastructure
    errors (e.g., database connection failures).  The Application
    layer may translate this into a user-facing message or a
    silent ignore depending on context.

    Parameters:
        tenant_id: The tenant identifier that was looked up.
            Raw value (int / str / None) — not a VO to keep the
            exceptions module independent.
        telegram_user_id: The Telegram user ID used in the lookup
            (if the lookup was via Telegram whitelist). Optional.
    """

    def __init__(
        self,
        tenant_id: object = None,
        telegram_user_id: object = None,
    ) -> None:
        self.tenant_id = tenant_id
        self.telegram_user_id = telegram_user_id
        msg_parts = ["Tenant not found"]
        if tenant_id is not None:
            msg_parts.append(f"| tenant_id={tenant_id}")
        if telegram_user_id is not None:
            msg_parts.append(f"| telegram_user_id={telegram_user_id}")
        super().__init__(" ".join(msg_parts))

    def __repr__(self) -> str:
        return (
            f"{self.__class__.__name__}("
            f"tenant_id={self.tenant_id!r}, "
            f"telegram_user_id={self.telegram_user_id!r}"
            f")"
        )


class UnauthorizedUserError(DomainError):
    """Raised when a Telegram user is denied access to the bot.

    This covers two scenarios:
        1. The user is **not in the whitelist** at all.
        2. The user **is whitelisted but deactivated**
           (``is_active == False``).

    The Application layer (middleware / use case) catches this to
    silently ignore or reply with a polite access-denied message.

    Parameters:
        telegram_user_id: The Telegram user ID that was rejected.
        reason: Short machine-readable reason string.  Suggested
            values: ``"not_in_whitelist"``, ``"account_deactivated"``.
    """

    def __init__(
        self,
        telegram_user_id: object,
        reason: str,
    ) -> None:
        self.telegram_user_id = telegram_user_id
        self.reason = reason
        super().__init__(
            f"Unauthorized user {telegram_user_id} | reason: {reason}"
        )

    def __repr__(self) -> str:
        return (
            f"{self.__class__.__name__}("
            f"telegram_user_id={self.telegram_user_id!r}, "
            f"reason={self.reason!r}"
            f")"
        )


# ---------------------------------------------------------------------------
# 4. Reconciliation Exception
# ---------------------------------------------------------------------------


class ReconciliationError(DomainError):
    """Raised when the pure domain reconciliation logic encounters an
    unexpected failure that cannot be classified as a normal
    business outcome.

    This is a **safety-net** exception — it should NOT be raised
    for expected discrepancy results (shortages, surpluses, etc.
    are normal).  It wraps edge-cases such as:
        - A line item whose quantities cannot produce a valid
          ``DiscrepancyStatus`` (impossible under current rules,
          but defensive).
        - Invalid data that passed schema validation but violates
          a deeper domain invariant.

    Parameters:
        sku: The SKU of the item that caused the failure.
            Optional — may be ``None`` for aggregate-level failures.
        detail: Additional diagnostic detail (e.g., raw values
            that triggered the edge-case).
    """

    def __init__(
        self,
        message: str,
        sku: str | None = None,
        detail: str | None = None,
    ) -> None:
        self.sku = sku
        self.detail = detail
        msg_parts = [message]
        if sku is not None:
            msg_parts.append(f"| SKU={sku}")
        if detail is not None:
            msg_parts.append(f"| detail={detail}")
        super().__init__(" ".join(msg_parts))

    def __repr__(self) -> str:
        return (
            f"{self.__class__.__name__}("
            f"message={self.message!r}, "
            f"sku={self.sku!r}, "
            f"detail={self.detail!r}"
            f")"
        )


# ---------------------------------------------------------------------------
# 5. Infrastructure-Aware Domain Exception
# ---------------------------------------------------------------------------


class DatabaseError(DomainError):
    """Raised when an infrastructure database operation fails.

    This exception allows the Application layer to catch
    ``DatabaseError`` without importing SQLAlchemy or knowing
    about ``IntegrityError`` / ``OperationalError``.  The
    infrastructure adapter maps concrete DB driver exceptions
    into this domain-safe type.

    Parameters:
        message: Human-readable failure description.
        original_exception: The string representation of the
            underlying DB driver exception (for logging/debugging).
            Optional — may be ``None`` when the failure context
            is self-describing.
    """

    def __init__(
        self,
        message: str,
        original_exception: str | None = None,
    ) -> None:
        self.original_exception = original_exception
        msg_parts = [message]
        if original_exception is not None:
            msg_parts.append(f"| original={original_exception}")
        super().__init__(" ".join(msg_parts))

    def __repr__(self) -> str:
        return (
            f"{self.__class__.__name__}("
            f"message={self.message!r}, "
            f"original_exception={self.original_exception!r}"
            f")"
        )
