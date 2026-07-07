"""Excel Parser Adapters — Infrastructure implementations of FileParserPort.

Each parser adapter in this package implements ``src.domain.ports.FileParserPort``,
translating raw file bytes into validated ``ParsedSheetDTO`` instances via
the domain's type-safe boundary.

Current adapters:
    - PolarsExcelParser: Fast Rust-backed Excel parsing with Pandera schema validation.
"""

from __future__ import annotations

from src.infrastructure.parsers.excel_parser import PolarsExcelParser

__all__ = ["PolarsExcelParser"]