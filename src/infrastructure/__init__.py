"""Infrastructure layer — secondary adapters implementing domain ports.

Currently implemented adapters:
- repositories/   — SQLAlchemy async repository adapter
                    (``PostgresInventoryRepository``)
- parsers/        — Polars + fastexcel file parser adapter
                    (``PolarsExcelParser``)
- notifications/  — aiogram Telegram notification adapter
                    (``TelegramNotificationAdapter``)
- logging/        — structlog JSON logging configuration

Pending adapters (not yet built):
- excel_exporter/ — openpyxl report exporter adapter
                    (replacing ``StubExcelExporter`` in composer.py)
- auth/           — dedicated Telegram whitelist auth adapter
                    (replacing ``StubAuthUseCase`` in composer.py)
"""
