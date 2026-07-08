"""Application Composer — Manual Dependency Injection Container.

This module is the **central wiring point** where all concrete adapters
are instantiated and connected to their respective domain port interfaces.
It follows the *Manual Constructor Injection* pattern — no framework magic,
just explicit object creation and wire-up.

Hexagonal Architecture Role:
    The Composer is a *Configuration* component.  It knows about every
    concrete adapter class (importing from infrastructure and presentation)
    and wires them into the domain's port contracts.  This is the only
    place in the entire codebase where cross-layer imports are not only
    permitted but *required* — the Composer's job is to bridge layers.

What it produces:
    A dictionary of ready-to-use dependencies that `main.py` injects
    into the aiogram Dispatcher and middleware chain::

        deps = await build_application_state(bot)
        # deps["process_inventory_use_case"] → ProcessInventoryUseCase
        # deps["auth_port"]                → StubAuthUseCase (wraps repo)
        # deps["db_engine"]               → AsyncEngine (for shutdown)

Stubs for missing adapters:
    - ``StubExcelExporter``: implements ``ReportExporterPort``,
      returns ``b"dummy_excel_bytes"``.  Replaced when the real
      ``ExcelExporter`` is built (Phase 3 gap).
    - ``StubAuthUseCase``: implements ``AuthVerificationPort``,
      delegates directly to ``PostgresInventoryRepository.get_by_telegram_id()``
      and converts the result to an ``AuthContextDTO``.  Bridging the
      gap until ``VerifyTelegramUserUseCase`` is built (Task 2.2).
"""

from __future__ import annotations

from typing import Any

from aiogram import Bot
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
import structlog

from src.application.use_cases.process_inventory import ProcessInventoryUseCase
from src.config.settings import settings
from src.domain.exceptions import TenantNotFoundError, UnauthorizedUserError
from src.domain.ports import (
    AuthVerificationPort,
    FileParserPort,
    NotificationPort,
    ReportExporterPort,
)
from src.domain.schemas import AuthContextDTO, DiscrepancyRowDTO, ReportResultDTO
from src.domain.value_objects import StoreId, TelegramUserId
from src.infrastructure.notifications.telegram_notifier import TelegramNotificationAdapter
from src.infrastructure.parsers.excel_parser import PolarsExcelParser
from src.infrastructure.repositories.postgres_repository import PostgresInventoryRepository

# ---------------------------------------------------------------------------
# Logger
# ---------------------------------------------------------------------------
logger = structlog.get_logger(__name__)


# ============================================================================
# Stub adapters — bridging Phase 3/Phase 2 gaps
# ============================================================================


class StubExcelExporter(ReportExporterPort):
    """Temporary stub implementing ``ReportExporterPort``.

    Returns a minimal ``ReportResultDTO`` with placeholder ``excel_bytes``
    (``b"dummy_excel_bytes"``) and a simple Markdown summary derived from
    the snapshot's own ``build_markdown_report()`` method.

    This stub is replaced by the real ``ExcelExporter`` once it is built
    (Infrastructure Phase 3 gap — ``src/infrastructure/excel_exporter``).
    """

    async def export(self, snapshot: Any) -> ReportResultDTO:
        """Build a stub report from an ``InventorySnapshot``.

        Parameters
        ----------
        snapshot : InventorySnapshot
            The domain aggregate containing items + discrepancies.

        Returns
        -------
        ReportResultDTO
            Minimal report with dummy Excel bytes and the snapshot's
            own Markdown summary.
        """
        logger.debug(
            "StubExcelExporter.export called — returning dummy bytes.",
            snapshot_id=str(snapshot.id),
        )
        discrepancy_rows = _build_discrepancy_rows(snapshot)
        return ReportResultDTO(
            tenant_id=snapshot.tenant_id.value,
            store_id=snapshot.store_id.value,
            total_items=snapshot.total_items,
            total_discrepancies=len(snapshot.discrepancies),
            summary_markdown=snapshot.build_markdown_report(),
            discrepancy_rows=discrepancy_rows,
            excel_bytes=b"dummy_excel_bytes",
        )


class StubAuthUseCase(AuthVerificationPort):
    """Temporary auth adapter wrapping ``TenantRepositoryPort`` directly.

    Bypasses the missing ``VerifyTelegramUserUseCase`` (Task 2.2, not yet
    built) by calling the repository's ``get_by_telegram_id()`` method
    directly and building an ``AuthContextDTO`` from the returned
    ``(Tenant, TelegramUser)`` tuple.

    The ``StoreId`` is derived from the tenant name (simplistic mapping
    for MVP — the real auth use case will resolve the store properly).
    """

    def __init__(self, repo: PostgresInventoryRepository) -> None:
        self._repo = repo
        logger.debug("StubAuthUseCase initialised with %s", type(repo).__name__)

    async def verify(self, telegram_user_id: TelegramUserId) -> AuthContextDTO:
        """Resolve a Telegram user via the tenant repository.

        Parameters
        ----------
        telegram_user_id : TelegramUserId
            The caller's Telegram identifier.

        Returns
        -------
        AuthContextDTO
            Populated with tenant_id, store_id, user_role, is_active.

        Raises
        ------
        UnauthorizedUserError
            If the user record is inactive.
        TenantNotFoundError
            Propagated from the repository if no matching whitelist row exists.
        """
        try:
            tenant, telegram_user = await self._repo.get_by_telegram_id(telegram_user_id)
        except TenantNotFoundError:
            raise UnauthorizedUserError(
                telegram_user_id=telegram_user_id.value,
                reason="not_whitelisted",
            ) from None

        if not telegram_user.is_active:
            raise UnauthorizedUserError(
                telegram_user_id=telegram_user_id.value,
                reason="account_deactivated",
            )

        # Derive a store_id from the tenant name (MVP simplification).
        # The real VerifyTelegramUserUseCase will resolve store_id from
        # a proper store assignment table or tenant configuration.
        store_id = StoreId(value=tenant.name)

        return AuthContextDTO(
            tenant_id=tenant.id,
            store_id=store_id,
            telegram_user_id=telegram_user.user_id,
            user_role=telegram_user.role,
            is_active=telegram_user.is_active,
        )


# ============================================================================
# DI Container — build_application_state
# ============================================================================


async def build_application_state(bot: Bot) -> dict[str, object]:
    """Construct the full dependency graph and return a dictionary of
    ready-to-use wired dependencies.

    This is the **single source of truth** for how all adapters connect
    to the domain's port interfaces.  Every concrete adapter is
    instantiated here and injected into the use case.

    Workflow
    --------
    1. Create the async database engine + session factory from ``settings``.
    2. Instantiate all concrete adapters:
       - ``PolarsExcelParser``           (FileParserPort)
       - ``PostgresInventoryRepository`` (InventoryRepositoryPort + TenantRepositoryPort)
       - ``TelegramNotificationAdapter`` (NotificationPort, injected with *bot*)
       - ``StubExcelExporter``           (ReportExporterPort — temporary)
       - ``StubAuthUseCase``             (AuthVerificationPort — temporary)
    3. Wire all five outbound ports into ``ProcessInventoryUseCase``.
    4. Return a dictionary containing the use case, auth stub, engine,
       and any other dependencies needed by ``main.py``.

    Parameters
    ----------
    bot : aiogram.Bot
        The pre-configured Telegram Bot instance (token already loaded).
        Passed into ``TelegramNotificationAdapter``.

    Returns
    -------
    dict[str, object]
        ``{"process_inventory_use_case": ProcessInventoryUseCase,``
        `` "auth_port": StubAuthUseCase,``
        `` "db_engine": AsyncEngine}``

    Raises
    ------
    Exception
        Propagates any adapter instantiation failure (e.g., invalid DB URL).
        The caller (``main.py``) is responsible for logging and gracefully
        exiting.
    """
    logger.info("build_application_state.starting")

    # ──────────────────────────────────────────────────────────────────
    # Step 1: Database Engine & Session Factory
    # ──────────────────────────────────────────────────────────────────
    database_url = str(settings.database_url)
    engine: AsyncEngine = create_async_engine(
        database_url,
        echo=False,
        pool_size=10,
        max_overflow=20,
        pool_pre_ping=True,
    )
    session_factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
        engine,
        expire_on_commit=False,
    )
    logger.info("async_db_engine.created", database_url=database_url)

    # ──────────────────────────────────────────────────────────────────
    # Step 2: Instantiate All Concrete Adapters
    # ──────────────────────────────────────────────────────────────────

    # 2a. Excel Parser (FileParserPort)
    parser_adapter: FileParserPort = PolarsExcelParser()
    logger.debug("adapter.instantiated", adapter="PolarsExcelParser")

    # 2b. Repository (implements BOTH InventoryRepositoryPort AND TenantRepositoryPort)
    repo_adapter = PostgresInventoryRepository(session_factory)
    logger.debug("adapter.instantiated", adapter="PostgresInventoryRepository")

    # 2c. Telegram Notifier (NotificationPort) — receives the bot
    notification_adapter: NotificationPort = TelegramNotificationAdapter(bot)
    logger.debug("adapter.instantiated", adapter="TelegramNotificationAdapter")

    # 2d. Stub Excel Exporter (ReportExporterPort) — temporary bridge
    exporter_adapter: ReportExporterPort = StubExcelExporter()
    logger.debug("adapter.instantiated", adapter="StubExcelExporter")

    # 2e. Stub Auth Use Case (AuthVerificationPort) — wraps repo directly
    #     Bridges the missing VerifyTelegramUserUseCase (Task 2.2).
    auth_adapter: AuthVerificationPort = StubAuthUseCase(repo_adapter)
    logger.debug("adapter.instantiated", adapter="StubAuthUseCase")

    # ──────────────────────────────────────────────────────────────────
    # Step 3: Wire ProcessInventoryUseCase (all 5 outbound ports)
    # ──────────────────────────────────────────────────────────────────
    # Note: auth_port in the use case is used for re-verification inside
    #       the pipeline (Step 1 of execute()), NOT for middleware auth.
    #       The middleware uses the same auth_adapter separately.
    use_case = ProcessInventoryUseCase(
        auth_port=auth_adapter,
        parser_port=parser_adapter,
        repo_port=repo_adapter,
        exporter_port=exporter_adapter,
        notification_port=notification_adapter,
    )
    logger.info("use_case.wired", use_case="ProcessInventoryUseCase")

    # ──────────────────────────────────────────────────────────────────
    # Step 4: Return the dependency dictionary
    # ──────────────────────────────────────────────────────────────────
    deps: dict[str, object] = {
        "process_inventory_use_case": use_case,
        "auth_port": auth_adapter,
        "db_engine": engine,
    }
    logger.info("build_application_state.complete", available_keys=list(deps.keys()))
    return deps


# ============================================================================
# Private helpers
# ============================================================================


def _build_discrepancy_rows(snapshot: Any) -> list[DiscrepancyRowDTO]:
    """Convert an ``InventorySnapshot``'s discrepancies into DTOs.

    This is a private helper extracted so the stub exporter can
    produce valid ``DiscrepancyRowDTO`` items from the domain
    aggregate's ``discrepancies`` collection (which holds
    ``DiscrepancyItem`` entities, not DTOs).

    Parameters
    ----------
    snapshot : InventorySnapshot
        The domain aggregate root.

    Returns
    -------
    list[DiscrepancyRowDTO]
        One DTO per discrepancy item in the snapshot.
    """
    rows: list[DiscrepancyRowDTO] = []
    for disc in snapshot.discrepancies:
        rows.append(
            DiscrepancyRowDTO(
                sku=disc.sku,
                item_name=disc.item_name,
                system_qty=disc.inventory_item.system_qty.value,
                actual_qty=disc.inventory_item.actual_qty.value,
                diff_amount=disc.diff.value,
                status=disc.status.value,
            )
        )
    return rows


async def shutdown_application_state(deps: dict[str, object]) -> None:
    """Gracefully dispose of resources held by the dependency graph.

    Currently disposes the database async engine.  Extendable for
    future resources (connection pools, Redis clients, etc.).

    Parameters
    ----------
    deps : dict[str, object]
        The dictionary returned by ``build_application_state()``.
    """
    engine = deps.get("db_engine")
    if isinstance(engine, AsyncEngine):
        await engine.dispose()
        logger.info("async_db_engine.disposed")
