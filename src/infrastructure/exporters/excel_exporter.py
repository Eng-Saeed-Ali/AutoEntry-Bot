"""Polars-backed Excel report exporter for InventorySnapshot aggregates.

Implements ``ReportExporterPort`` (defined in ``src.domain.ports``)
by converting the snapshot's discrepancy items into a ``polars.DataFrame``
and writing it to an in-memory ``io.BytesIO`` buffer via polars'
``write_excel`` method.  The resulting raw bytes are embedded in a
``ReportResultDTO`` alongside the snapshot's own Markdown summary.

Design Principles
-----------------
* **Zero disk I/O:** The Excel workbook is built entirely in an
  ``io.BytesIO()`` buffer.  ``.getvalue()`` returns the raw ``bytes`` —
  no temporary ``.xlsx`` file is ever created on disk.
* **Hexagonal purity:** Only imports from ``src.domain`` (ports,
  schemas, exceptions) and Python stdlib / infrastructure frameworks
  (``polars``, ``io``, ``structlog``).  Never references the
  Application or Presentation layers.
* **Shared column constants:** The column headers written to the
  Excel worksheet use the canonical ``EXPECTED_EXCEL_COLUMNS`` tuple
  from ``src.domain.schemas``, ensuring that parsers, handlers, and
  this exporter all agree on the column names.
"""

from __future__ import annotations

import io
from typing import Any

import polars
import structlog

from src.domain.exceptions import DomainError
from src.domain.models import (
    DiscrepancyItem,
    InventorySnapshot,
)
from src.domain.ports import ReportExporterPort
from src.domain.schemas import (
    EXPECTED_EXCEL_COLUMNS,
    DiscrepancyRowDTO,
    ReportResultDTO,
)

# ---------------------------------------------------------------------------
# Logger
# ---------------------------------------------------------------------------
logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Excel column names (in the order they appear in the generated sheet)
# ---------------------------------------------------------------------------
# The first four match EXPECTED_EXCEL_COLUMNS exactly, then two
# canonical extras: Diff (difference computed from the domain entity)
# and Status (discrepancy classification like SHORTAGE / SURPLUS etc.)
_EXCEL_HEADER_SKU: str = EXPECTED_EXCEL_COLUMNS[0]  # "SKU"
_EXCEL_HEADER_ITEM_NAME: str = EXPECTED_EXCEL_COLUMNS[1]  # "Item_Name"
_EXCEL_HEADER_SYSTEM_QTY: str = EXPECTED_EXCEL_COLUMNS[2]  # "System_Qty"
_EXCEL_HEADER_ACTUAL_QTY: str = EXPECTED_EXCEL_COLUMNS[3]  # "Actual_Qty"
_EXCEL_HEADER_DIFF: str = "Diff"
_EXCEL_HEADER_STATUS: str = "Status"

_ALL_EXCEL_HEADERS: tuple[str, ...] = (
    _EXCEL_HEADER_SKU,
    _EXCEL_HEADER_ITEM_NAME,
    _EXCEL_HEADER_SYSTEM_QTY,
    _EXCEL_HEADER_ACTUAL_QTY,
    _EXCEL_HEADER_DIFF,
    _EXCEL_HEADER_STATUS,
)


# ============================================================================
# PolarsReportExporter
# ============================================================================


class PolarsReportExporter(ReportExporterPort):
    """Generate the discrepancy Excel report using ``polars`` + ``fastexcel``.

    Inherits from ``ReportExporterPort`` (ABC declared in
    ``src.domain.ports``) and provides a concrete ``.export()``
    implementation that:

    1. Iterates the snapshot's ``.discrepancies`` collection (each
       item is a ``DiscrepancyItem`` domain entity) and builds a
       ``list[dict[str, str|int]]`` of flat row data.
    2. Loads the rows into a ``polars.DataFrame``.
    3. Writes the DataFrame to an in-memory ``io.BytesIO`` Excel
       workbook via ``polars.DataFrame.write_excel()`` (which
       internally uses ``fastexcel``, the Rust-backed streaming
       Excel writer declared in ``pyproject.toml``).
    4. Reads back the bytes from the buffer, constructs a
       ``ReportResultDTO``, and returns it.

    Column ordering
    ---------------
    Columns in the generated Excel file (left to right):

    * ``SKU``           — from ``EXPECTED_EXCEL_COLUMNS[0]``
    * ``Item_Name``     — from ``EXPECTED_EXCEL_COLUMNS[1]``
    * ``System_Qty``    — from ``EXPECTED_EXCEL_COLUMNS[2]``
    * ``Actual_Qty``    — from ``EXPECTED_EXCEL_COLUMNS[3]``
    * ``Diff``          — canonical extra (actual - system)
    * ``Status``        — domain classification string

    """

    async def export(self, snapshot: InventorySnapshot) -> ReportResultDTO:
        """Produce a fully-populated ``ReportResultDTO`` from the snapshot.

        Parameters
        ----------
        snapshot : InventorySnapshot
            The domain aggregate root containing ``.items``,
            ``.discrepancies``, and contextual metadata (tenant_id,
            store_id, parsed timestamp).

        Returns
        -------
        ReportResultDTO
            A frozen DTO carrying:

            * ``tenant_id``          — string form of ``TenantId``
            * ``store_id``           — string form of ``StoreId``
            * ``total_items``        — ``len(snapshot.items)``
            * ``total_discrepancies``— ``len(snapshot.discrepancies)``
            * ``summary_markdown``   — from ``snapshot.build_markdown_report()``
            * ``discrepancy_rows``   — ``list[DiscrepancyRowDTO]`` (derived from
              ``snapshot.discrepancies``, used by presentation layer for quick
              iteration without touching bytes)
            * ``excel_bytes``        — raw ``bytes`` of the generated ``.xlsx``
              workbook (only discrepancy rows)
        """
        logger.debug(
            "PolarsReportExporter.export.called",
            snapshot_id=str(snapshot.id),
            total_items=snapshot.total_items,
            total_discrepancies=len(snapshot.discrepancies),
        )

        # ------------------------------------------------------------------
        # 1. Convert domain DiscrepancyItem entities → flat row dicts
        # ------------------------------------------------------------------
        discrepancy_rows_dto: list[DiscrepancyRowDTO] = []
        flat_rows: list[dict[str, Any]] = []

        for disc in snapshot.discrepancies:
            # Build the DiscrepancyRowDTO (used by the presentation
            # layer / NotificationPort for quick access without
            # parsing the Excel bytes).
            dto = _discrepancy_item_to_row_dto(disc)
            discrepancy_rows_dto.append(dto)

            # Build a flat dict for the polars DataFrame / Excel export.
            flat_rows.append(
                {
                    _EXCEL_HEADER_SKU: disc.sku,
                    _EXCEL_HEADER_ITEM_NAME: disc.item_name,
                    _EXCEL_HEADER_SYSTEM_QTY: disc.inventory_item.system_qty.value,
                    _EXCEL_HEADER_ACTUAL_QTY: disc.inventory_item.actual_qty.value,
                    _EXCEL_HEADER_DIFF: disc.diff.value,
                    _EXCEL_HEADER_STATUS: disc.status.value,
                }
            )

        # ------------------------------------------------------------------
        # 2. Build polars.DataFrame
        # ------------------------------------------------------------------
        df = polars.DataFrame(flat_rows, schema=_ALL_EXCEL_HEADERS)
        logger.debug(
            "polars.dataframe.built",
            row_count=len(df),
            column_count=len(df.columns),
        )

        # ------------------------------------------------------------------
        # 3. Write to in-memory io.BytesIO buffer (zero disk I/O)
        # ------------------------------------------------------------------
        buffer = io.BytesIO()
        try:
            # polars.write_excel uses fastexcel (Rust-backed) by default
            # when available.  The workbook= parameter accepts a writable
            # file-like object — BytesIO qualifies.
            df.write_excel(
                workbook=buffer,
                worksheet="Discrepancies",
                autofit=True,  # auto-compute column widths
                hide_gridlines=False,
                table_style="TableStyleMedium2",
            )
        except Exception as exc:
            # Translate infrastructure-level Excel write failures
            # into domain exceptions so the use case's try/except
            # can produce a meaningful ProcessResultDTO.
            logger.exception(
                "polars.write_excel.failed",
                snapshot_id=str(snapshot.id),
                exc_info=True,
            )
            raise DomainError(
                f"Excel generation failed: {exc.__class__.__name__}: {exc!s}"
            ) from exc

        # ------------------------------------------------------------------
        # 4. Extract raw bytes from the buffer
        # ------------------------------------------------------------------
        buffer.seek(0)
        excel_bytes: bytes = buffer.getvalue()
        buffer.close()

        logger.info(
            "PolarsReportExporter.export.complete",
            snapshot_id=str(snapshot.id),
            excel_file_size_bytes=len(excel_bytes),
            discrepancy_row_count=len(discrepancy_rows_dto),
        )

        # ------------------------------------------------------------------
        # 5. Assemble and return ReportResultDTO
        # ------------------------------------------------------------------
        return ReportResultDTO(
            tenant_id=str(snapshot.tenant_id),
            store_id=str(snapshot.store_id),
            total_items=snapshot.total_items,
            total_discrepancies=len(snapshot.discrepancies),
            summary_markdown=snapshot.build_markdown_report(),
            discrepancy_rows=discrepancy_rows_dto,
            excel_bytes=excel_bytes,
        )


# ============================================================================
# Private helpers
# ============================================================================


def _discrepancy_item_to_row_dto(disc: DiscrepancyItem) -> DiscrepancyRowDTO:
    """Convert a single ``DiscrepancyItem`` domain entity into a
    ``DiscrepancyRowDTO`` ready for the presentation layer.

    Parameters
    ----------
    disc : DiscrepancyItem
        The auto-classified domain discrepancy entity.

    Returns
    -------
    DiscrepancyRowDTO
        Immutable DTO carrying the same row-level fields expected
        by ``ReportResultDTO.discrepancy_rows``.
    """
    return DiscrepancyRowDTO(
        sku=disc.sku,
        item_name=disc.item_name,
        system_qty=disc.inventory_item.system_qty.value,
        actual_qty=disc.inventory_item.actual_qty.value,
        diff_amount=disc.diff.value,
        status=disc.status.value,
    )
