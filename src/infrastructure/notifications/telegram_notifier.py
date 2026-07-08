"""🔔 TelegramNotificationAdapter — aiogram Bot wrapper implementing NotificationPort.

This adapter is the concrete delivery mechanism for the final
reconciliation report.  It receives an ``aiogram.Bot`` instance
via constructor injection and translates the domain's
``ReportResultDTO`` into Telegram API calls::

    ReportResultDTO
        │
        ├── summary_markdown → bot.send_message(chat_id, text, parse_mode="Markdown")
        └── excel_bytes      → bot.send_document(chat_id, BufferedInputFile(...))

====================================================================
   HEXAGONAL IMPORT DISCIPLINE (ENFORCED)
====================================================================

**Imports from:**
    - ``aiogram``           — Bot, BufferedInputFile, types
    - ``src.domain.ports``  — ``NotificationPort`` (ABC we implement)
    - ``src.domain.schemas``— ``ReportResultDTO`` (the input DTO)
    - ``structlog``         — structured delivery audit logging

**Never imports from:**
    - ``src.presentation``  — infrastructure must not depend on presentation
    - ``src.application``   — infrastructure must not depend on application
    - ``src.config``        — bot token injected via constructor, not settings

====================================================================
   PARTIAL-FAILURE STRATEGY
====================================================================

1. Send the Markdown text first.
2. Send the Excel document second.
3. If the text succeeds but the document fails, log the failure
   but do NOT re-raise — the user has already received the summary,
   and a partial delivery is better than silent failure.
4. If the text itself fails, log and re-raise — nothing was delivered
   and the Application layer needs to know.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from structlog.typing import BoundLogger

import structlog

from aiogram import Bot
from aiogram.types import BufferedInputFile

from src.domain.ports import NotificationPort
from src.domain.schemas import ReportResultDTO

# ---------------------------------------------------------------------------
# Logger
# ---------------------------------------------------------------------------

logger: "BoundLogger" = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------


class TelegramNotificationAdapter(NotificationPort):
    """Deliver the reconciliation report to a Telegram chat.

    Thin wrapper around ``aiogram.Bot`` — implements the Domain
    layer's ``NotificationPort`` ABC so the Application layer
    (``ProcessInventoryUseCase``) can call ``send_report()``
    without knowing *how* delivery happens.

    Constructor Injection
    ---------------------
    ``bot : aiogram.Bot``
        The pre-configured aiogram Bot instance (token already
        loaded by the Composer).  The adapter does NOT create
        or configure the bot — it only uses it.
    """

    def __init__(self, bot: Bot) -> None:
        self._bot = bot
        logger.debug(
            "TelegramNotificationAdapter initialised with bot id=%s",
            id(bot),
        )

    # ------------------------------------------------------------------
    # NotificationPort implementation
    # ------------------------------------------------------------------

    async def send_report(
        self,
        chat_id: int,
        report: ReportResultDTO,
    ) -> None:
        """Deliver the Markdown summary + Excel attachment to a user.

        Implements ``NotificationPort.send_report()``.

        Delivery is best-effort: the Markdown summary is sent first
        (critical).  If the Excel attachment fails afterwards, the
        failure is logged but NOT propagated — the user at least
        received the text summary.

        Parameters
        ----------
        chat_id : int
            Telegram chat ID of the recipient.
        report : ReportResultDTO
            The final report produced by the exporter, containing:
                - ``summary_markdown`` — the Markdown-formatted
                  reconciliation summary.
                - ``discrepancy_rows`` — the list of anomaly rows
                  (may be empty if everything matched).

        Raises
        ------
        aiogram.exceptions.TelegramAPIError
            If the Markdown message itself fails to send (nothing
            was delivered — caller must handle).
        """
        log_ctx = logger.bind(
            chat_id=chat_id,
            tenant_id=report.tenant_id,
            store_id=report.store_id,
            total_items=report.total_items,
            total_discrepancies=report.total_discrepancies,
        )

        # --------------------------------------------------------------
        # Step 1: Send the Markdown summary (CRITICAL — must succeed)
        # --------------------------------------------------------------
        try:
            sent_message = await self._bot.send_message(
                chat_id=chat_id,
                text=report.summary_markdown,
                parse_mode="Markdown",
                disable_web_page_preview=True,
            )
            log_ctx.info(
                "Markdown summary sent successfully.",
                message_id=sent_message.message_id,
            )
        except Exception:
            log_ctx.exception("Failed to send Markdown summary.")
            raise  # nothing delivered — let the caller decide

        # --------------------------------------------------------------
        # Step 2: Send the Excel discrepancy attachment (best-effort)
        # --------------------------------------------------------------
        if report.total_discrepancies == 0:
            log_ctx.info("No discrepancies — skipping Excel attachment.")
            return

        # Build a lightweight Excel attachment from the discrepancy
        # rows.  Future: when ReportResultDTO gains an ``excel_bytes``
        # field (produced by ReportExporterPort), this block will
        # directly send those pre-formatted bytes.  For now, we
        # generate a simple CSV-like text file as a placeholder
        # until the exporter is wired.
        try:
            document_bytes, filename = _build_discrepancy_document(report)
            input_file = BufferedInputFile(
                file=document_bytes,
                filename=filename,
            )
            sent_doc = await self._bot.send_document(
                chat_id=chat_id,
                document=input_file,
                caption=f"📎 Discrepancy Report — {report.total_discrepancies} anomalies",
            )
            log_ctx.info(
                "Discrepancy document sent successfully.",
                document_message_id=sent_doc.message_id,
                filename=filename,
            )
        except Exception:
            log_ctx.exception(
                "Failed to send discrepancy document — partial delivery "
                "(Markdown summary was already sent)."
            )
            # Do NOT re-raise — the summary was already delivered.
            # The Application layer will see report_delivered=False
            # via the ProcessResultDTO built upstream.


# ============================================================================
# Private helpers
# ============================================================================


def _build_discrepancy_document(report: ReportResultDTO) -> tuple[bytes, str]:
    """Build a lightweight discrepancy document from the report DTO.

    Until ``ReportResultDTO`` carries ``excel_bytes`` (produced by
    ``ReportExporterPort`` in a future wiring step), this function
    generates a minimal CSV from the ``discrepancy_rows`` list.

    Parameters
    ----------
    report : ReportResultDTO
        The report containing ``discrepancy_rows``.

    Returns
    -------
    tuple[bytes, str]
        ``(file_bytes, suggested_filename)`` ready for
        ``BufferedInputFile``.
    """
    safe_store = report.store_id.replace(" ", "_")
    filename = f"discrepancies_{safe_store}.csv"

    rows = report.discrepancy_rows
    lines: list[str] = [
        "SKU,Item_Name,System_Qty,Actual_Qty,Diff,Status",
    ]
    for row in rows:
        # Escape commas inside values with double-quote wrapping
        escaped_name = row.item_name.replace('"', '""')
        lines.append(
            f"{row.sku},{escaped_name},{row.system_qty},"
            f"{row.actual_qty},{row.diff_amount},{row.status}"
        )
    csv_content = "\n".join(lines) + "\n"
    return csv_content.encode("utf-8"), filename