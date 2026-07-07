"""Infrastructure layer — secondary adapters implementing domain ports.

Contains:
- persistence/     — SQLAlchemy async repository adapters
- excel_parser/    — openpyxl + polars file parser adapter
- excel_exporter/  — openpyxl report exporter adapter
- auth/            — Telegram whitelist auth adapter
- logging/         — structlog JSON logging configuration
"""