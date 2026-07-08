"""PostgreSQL async repository adapter implementing both
``InventoryRepositoryPort`` and ``TenantRepositoryPort``.

Uses SQLAlchemy 2.0 Core (``insert()`` / ``select()``) against
an ``AsyncSession`` backed by asyncpg.  Returns pure domain
entities — the domain never sees SQL or ORM artefacts.

Hexagonal Architecture Compliance:
    - Implements domain ABCs (``InventoryRepositoryPort``,
      ``TenantRepositoryPort``) exactly.
    - Returns ``InventorySnapshot``, ``Tenant``, ``TelegramUser``
      (domain entities) — never ORM objects or raw dictionaries.
    - Maps SQLAlchemy exceptions (``IntegrityError``, etc.) to
      ``DatabaseError`` (domain exception) so the Application
      layer only catches domain errors.
    - Zero imports from ``src.presentation``, ``src.application``,
      or ``src.config``.

Construction:
    Receives an ``async_sessionmaker[AsyncSession]`` via manual
    constructor injection (the ``App`` composer wires it up).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError, OperationalError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.domain.exceptions import DatabaseError, TenantNotFoundError
from src.domain.models import (
    InventoryItem,
    InventorySnapshot,
    TelegramUser,
    Tenant,
)
from src.domain.ports import (
    InventoryRepositoryPort,
    TenantRepositoryPort,
)
from src.domain.value_objects import (
    ItemName,
    Quantity,
    Sku,
    StoreId,
    TelegramUserId,
    TenantId,
)

# ---------------------------------------------------------------------------
# Table-name constants (single source of truth until ORM models are built)
# ---------------------------------------------------------------------------

TABLE_INVENTORY_SNAPSHOTS = "inventory_snapshots"
TABLE_INVENTORY_ITEMS = "inventory_items"
TABLE_DISCREPANCY_ITEMS = "discrepancy_items"
TABLE_TENANTS = "tenants"
TABLE_TELEGRAM_USERS = "telegram_users"

# Column-name constants — semantic alignment with domain entities
COL_SNAPSHOT_ID = "id"
COL_TENANT_ID = "tenant_id"
COL_STORE_ID = "store_id"
COL_PARSED_AT = "parsed_at"
COL_SKU = "sku"
COL_ITEM_NAME = "item_name"
COL_SYSTEM_QTY = "system_qty"
COL_ACTUAL_QTY = "actual_qty"
COL_DIFF_AMOUNT = "diff_amount"
COL_STATUS = "status"
COL_USER_ID = "telegram_user_id"
COL_IS_ACTIVE = "is_active"
COL_ROLE = "role"
COL_NAME = "name"
COL_CREATED_AT = "created_at"


class PostgresInventoryRepository(InventoryRepositoryPort, TenantRepositoryPort):
    """PostgreSQL adapter implementing both repository outbound ports.

    Built on SQLAlchemy 2.0 Core + asyncpg.  All methods are
    ``async def`` and map infrastructure failures to domain
    exceptions transparently.

    Parameters:
        session_factory: An ``async_sessionmaker[AsyncSession]``
            created by the ``App`` composer from the configured
            ``AsyncEngine``.
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory: async_sessionmaker[AsyncSession] = session_factory

    # ========================================================================
    # InventoryRepositoryPort  — save_snapshot
    # ========================================================================

    async def save_snapshot(self, snapshot: InventorySnapshot) -> None:
        """Persist a complete ``InventorySnapshot`` aggregate atomically.

        Transaction steps:
            1. INSERT the snapshot meta-row.
            2. BULK INSERT all ``InventoryItem`` rows.
            3. BULK INSERT all ``DiscrepancyItem`` rows.
            4. COMMIT (or ROLLBACK on error).

        Raises:
            DatabaseError: Any SQLAlchemy integrity / connection
                failure, wrapped as a domain exception.
        """
        async with self._session_factory() as session:
            try:
                await self._insert_snapshot_row(session, snapshot)
                await self._bulk_insert_items(session, snapshot.items)
                await self._bulk_insert_discrepancies(session, snapshot.discrepancies)
                await session.commit()
            except (IntegrityError, OperationalError, DBAPIError, SQLAlchemyError) as exc:
                await session.rollback()
                self._map_db_error(exc, context="save_snapshot")
            except Exception:
                await session.rollback()
                raise

    # ------------------------------------------------------------------
    # Private helpers — save_snapshot sub-steps
    # ------------------------------------------------------------------

    async def _insert_snapshot_row(
        self, session: AsyncSession, snapshot: InventorySnapshot
    ) -> None:
        stmt = text(
            f"INSERT INTO {TABLE_INVENTORY_SNAPSHOTS} "
            f"({COL_SNAPSHOT_ID}, {COL_TENANT_ID}, {COL_STORE_ID}, {COL_PARSED_AT}) "
            f"VALUES (:snap_id, :tenant_id, :store_id, :parsed_at) "
            f"ON CONFLICT ({COL_SNAPSHOT_ID}) DO NOTHING"
        )
        await session.execute(
            stmt,
            {
                "snap_id": str(snapshot.id),
                "tenant_id": snapshot.tenant_id.value,
                "store_id": snapshot.store_id.value,
                "parsed_at": snapshot.parsed_at,
            },
        )

    async def _bulk_insert_items(
        self, session: AsyncSession, items: list[InventoryItem]
    ) -> None:
        if not items:
            return

        keys = [
            "id",
            COL_SNAPSHOT_ID,
            COL_TENANT_ID,
            COL_SKU,
            COL_ITEM_NAME,
            COL_SYSTEM_QTY,
            COL_ACTUAL_QTY,
        ]
        sql = _build_multi_insert(TABLE_INVENTORY_ITEMS, keys, len(items))

        params: dict[str, object] = {}
        for i, item in enumerate(items):
            params[f"id_{i}"] = str(item.id)
            params[f"{COL_SNAPSHOT_ID}_{i}"] = str(item.snapshot_id)
            params[f"{COL_TENANT_ID}_{i}"] = item.tenant_id.value
            params[f"{COL_SKU}_{i}"] = str(item.sku)
            params[f"{COL_ITEM_NAME}_{i}"] = str(item.item_name)
            params[f"{COL_SYSTEM_QTY}_{i}"] = item.system_qty.value
            params[f"{COL_ACTUAL_QTY}_{i}"] = item.actual_qty.value

        await session.execute(text(sql), params)

    async def _bulk_insert_discrepancies(
        self,
        session: AsyncSession,
        discrepancies: list[Any],  # list[DiscrepancyItem]
    ) -> None:
        if not discrepancies:
            return

        keys = [
            COL_SNAPSHOT_ID,
            COL_TENANT_ID,
            COL_SKU,
            COL_ITEM_NAME,
            COL_SYSTEM_QTY,
            COL_ACTUAL_QTY,
            COL_DIFF_AMOUNT,
            COL_STATUS,
        ]
        sql = _build_multi_insert(TABLE_DISCREPANCY_ITEMS, keys, len(discrepancies))

        params: dict[str, object] = {}
        for i, d in enumerate(discrepancies):
            item = d.inventory_item
            params[f"{COL_SNAPSHOT_ID}_{i}"] = str(item.snapshot_id)
            params[f"{COL_TENANT_ID}_{i}"] = item.tenant_id.value
            params[f"{COL_SKU}_{i}"] = str(item.sku)
            params[f"{COL_ITEM_NAME}_{i}"] = str(item.item_name)
            params[f"{COL_SYSTEM_QTY}_{i}"] = item.system_qty.value
            params[f"{COL_ACTUAL_QTY}_{i}"] = item.actual_qty.value
            params[f"{COL_DIFF_AMOUNT}_{i}"] = d.diff.value
            params[f"{COL_STATUS}_{i}"] = d.status.value

        await session.execute(text(sql), params)

    # ========================================================================
    # TenantRepositoryPort  — get_by_telegram_id
    # ========================================================================

    async def get_by_telegram_id(
        self, telegram_user_id: TelegramUserId
    ) -> tuple[Tenant, TelegramUser]:
        """Resolve a Telegram user to their tenant + whitelist record.

        Joins ``telegram_users`` ↔ ``tenants`` on ``tenant_id``.

        Returns:
            ``(Tenant, TelegramUser)`` — pure domain entities.

        Raises:
            TenantNotFoundError: No matching row found.
            DatabaseError: Any SQLAlchemy connection / query failure.
        """
        query = text(
            f"""
            SELECT
                t.{COL_TENANT_ID}    AS tenant_id,
                t.{COL_NAME}         AS tenant_name,
                t.{COL_CREATED_AT}   AS tenant_created_at,
                tu.{COL_USER_ID}     AS tu_user_id,
                tu.{COL_TENANT_ID}   AS tu_tenant_id,
                tu.{COL_IS_ACTIVE}   AS tu_is_active,
                tu.{COL_ROLE}        AS tu_role
            FROM {TABLE_TELEGRAM_USERS} tu
            JOIN {TABLE_TENANTS} t ON t.{COL_TENANT_ID} = tu.{COL_TENANT_ID}
            WHERE tu.{COL_USER_ID} = :telegram_user_id
            """
        )

        async with self._session_factory() as session:
            try:
                result = await session.execute(
                    query, {"telegram_user_id": telegram_user_id.value}
                )
                row = result.first()
            except (IntegrityError, OperationalError, DBAPIError, SQLAlchemyError) as exc:
                self._map_db_error(exc, context="get_by_telegram_id")
                raise  # unreachable — _map_db_error always raises

        if row is None:
            raise TenantNotFoundError(
                telegram_user_id=telegram_user_id.value,
            )

        # Map raw DB columns → pure domain entities
        # Row is a SQLAlchemy RowProxy; access by position or key.
        tenant = Tenant(
            id=TenantId(value=row.tenant_id),
            name=row.tenant_name,
            created_at=row.tenant_created_at,
        )
        telegram_user = TelegramUser(
            user_id=TelegramUserId(value=row.tu_user_id),
            tenant_id=TenantId(value=row.tu_tenant_id),
            is_active=bool(row.tu_is_active),
            role=row.tu_role,
        )
        return tenant, telegram_user

    # ========================================================================
    # Error Mapping
    # ========================================================================

    @staticmethod
    def _map_db_error(exc: SQLAlchemyError, context: str) -> None:
        """Translate a SQLAlchemy exception to a domain ``DatabaseError``.

        Always raises — never returns.  The caller is expected to
        have already rolled back the transaction.

        Parameters:
            exc: The SQLAlchemy exception (IntegrityError, OperationalError,
                DBAPIError, or generic SQLAlchemyError).
            context: A short label for logging (e.g. ``"save_snapshot"``,
                ``"get_by_telegram_id"``).

        Raises:
            DatabaseError: Always.
        """
        original = f"{exc.__class__.__name__}: {exc!s}"
        msg = f"Database operation '{context}' failed | {original}"
        raise DatabaseError(message=msg, original_exception=original) from exc


# ========================================================================
# Utility: build a multi-row INSERT statement string
# ========================================================================

def _build_multi_insert(table: str, columns: object, row_count: int) -> str:
    """Build a parameterised multi-value INSERT for SQLAlchemy Core ``text()``.

    Produces SQL like:
        INSERT INTO table (col1, col2) VALUES (:col1_0, :col2_0), (:col1_1, :col2_1), ...

    Parameters:
        table: Table name (safe — these are constants, not user input).
        columns: Iterable of column-name strings.
        row_count: Number of rows to insert.

    Returns:
        A SQL string with named parameters ready for ``session.execute(text(sql), flat_params)``.
    """
    col_list = ", ".join(str(c) for c in columns)
    value_placeholders: list[str] = []
    for i in range(row_count):
        row_params = ", ".join(f":{c}_{i}" for c in columns)
        value_placeholders.append(f"({row_params})")

    values_clause = ", ".join(value_placeholders)
    return f"INSERT INTO {table} ({col_list}) VALUES {values_clause}"


