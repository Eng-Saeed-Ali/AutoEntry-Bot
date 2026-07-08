"""🔔 Notification Infrastructure — Telegram Notification Adapter.

This subpackage contains secondary adapters implementing
``NotificationPort`` from the Domain layer.  For the MVP,
the sole adapter is ``TelegramNotificationAdapter`` which
wraps ``aiogram.Bot`` to deliver Markdown summaries and
Excel discrepancy reports to Telegram users.

====================================================================
   IMPORT DISCIPLINE
====================================================================

This subpackage may import:
    - ``aiogram`` (Bot, BufferedInputFile) — the concrete delivery SDK
    - ``src.domain.ports`` — ``NotificationPort`` ABC
    - ``src.domain.schemas`` — ``ReportResultDTO``
    - ``structlog`` — for structured delivery logging

Must NEVER import:
    - ``src.presentation`` — infrastructure must not depend on presentation
    - ``src.application`` — infrastructure must not depend on application
"""

from src.infrastructure.notifications.telegram_notifier import TelegramNotificationAdapter

__all__ = ["TelegramNotificationAdapter"]