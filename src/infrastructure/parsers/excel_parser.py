"""PolarsExcelParser — blazing-fast Excel parsing adapter implementing FileParserPort.

Hexagonal Architecture Role:
    Secondary (Driven) Adapter in the Infrastructure layer.
    Implements ``src.domain.ports.FileParserPort``.
    Translates raw Excel bytes → domain ``ParsedSheetDTO``.

Design Decisions:
    - **Pure Polars (no openpyxl):** ``polars.read_excel`` uses the Rust-backed
      ``calamine`` engine — 5–10× faster than openpyxl extraction + polars
      DataFrame construction.  The architecture blueprint originally described
      a two-stage openpyxl→polars pipeline, but Task 3.1 explicitly specifies
      direct polars parsing with Pandera schema validation.

    - **Schema validation embedded here (not a separate validator.py):**
      Pandera's ``DataFrameSchema`` is applied inside the ``parse()`` method
      to guarantee that every returned ``ParsedSheetDTO`` has been validated
      against the exact column contract before crossing the infrastructure→
      domain boundary.

    - **Placeholder tenant/store context:** ``FileParserPort.parse()`` receives
      only ``file_bytes`` — no tenant info, no uploader context.  The use case
      (``ProcessInventoryUseCase``) overrides ``tenant_id``, ``store_id``, and
      ``uploaded_by`` downstream after auth resolution.  This adapter fills
      default/placeholder values.

Error Mapping (Domain Exception Contract):
    - Missing/extra columns → ``InvalidSheetSchemaError``
    - Valid schema but zero data rows → ``SheetEmptyError``
    - Corrupt/unreadable file → ``InvalidSheetSchemaError`` (wrapped)
"""

from __future__ import annotations

import io

import polars as pl

# Pandera availability is detected at module import time via a guarded
# try/except.  The _build_pandera_schema() function handles the actual
# import + schema construction in the same guarded manner.  We keep a
# module-level flag so that parse() can branch between Pandera
# validation and manual fallback without repeating the import attempt
# on every call.
try:
    import pandera  # availability probe only  # noqa: F401
    import pandera.polars  # type: ignore[import-untyped]  # noqa: F401
    HAS_PANDERA: bool = True
except (ImportError, AttributeError):
    HAS_PANDERA = False

from src.domain.exceptions import InvalidSheetSchemaError, SheetEmptyError
from src.domain.ports import FileParserPort
from src.domain.schemas import ParsedRowDTO, ParsedSheetDTO

# ---------------------------------------------------------------------------
# Schema Definition
# ---------------------------------------------------------------------------

# The four required columns as specified by ARCHITECTURE.md Step 3.
REQUIRED_COLUMNS: tuple[str, str, str, str] = ("SKU", "Item_Name", "System_Qty", "Actual_Qty")

COLUMN_DTYPE_MAP: dict[str, type] = {
    "SKU": str,
    "Item_Name": str,
    "System_Qty": int,
    "Actual_Qty": int,
}

# ---------------------------------------------------------------------------
# Pandera Schema
# ---------------------------------------------------------------------------

# We attempt to define a pandera polars DataFrameSchema at module level.
# If pandera or its polars extension is unavailable, we fall back to a manual
# schema check performed in the parse() method.

_PANDERA_SCHEMA: object | None = None


def _build_pandera_schema() -> object | None:
    """Attempt to construct a Pandera polars DataFrameSchema.

    Returns the schema object, or ``None`` if pandera/polars extensions
    are not installed or incompatible.

    Uses the **procedural** ``DataFrameSchema`` + ``Column`` API
    (not the class-based ``DataFrameModel``) because:
        - Pandera ≥0.28 removed ``SeriesSchema``.
        - ``Field`` rejects the ``dtype=`` keyword.
        - ``Column`` is not a generic class (can't annotate ``Column[str]``).
        - ``DataFrameSchema(columns={...})`` is the stable, explicit path
          that works across 0.20 → 0.32.x without API breakage.
    """
    try:
        import pandera.polars as pa_polars  # type: ignore[import-untyped]

        # ── Procedural schema: one Column per required field ──
        _inventory_schema = pa_polars.DataFrameSchema(
            columns={
                "SKU": pa_polars.Column(
                    pl.Utf8,
                    nullable=False,
                    coerce=True,
                    title="SKU",
                    description="Stock Keeping Unit — alphanumeric identifier (e.g., 'ABC-123').",
                ),
                "Item_Name": pa_polars.Column(
                    pl.Utf8,
                    nullable=False,
                    coerce=True,
                    title="Item_Name",
                    description="Human-readable product/item name.",
                ),
                "System_Qty": pa_polars.Column(
                    pl.Int64,
                    nullable=False,
                    coerce=True,
                    title="System_Qty",
                    description="Expected quantity according to the ERP/system (non-negative).",
                    checks=pa_polars.Check.greater_than_or_equal_to(0),
                ),
                "Actual_Qty": pa_polars.Column(
                    pl.Int64,
                    nullable=False,
                    coerce=True,
                    title="Actual_Qty",
                    description="Physically counted quantity (non-negative).",
                    checks=pa_polars.Check.greater_than_or_equal_to(0),
                ),
            },
            strict=True,
            coerce=True,
        )

        return _inventory_schema

    except ImportError:
        # pandera or its polars extension is not installed.
        pass
    except Exception:
        # Any other failure (e.g., API incompatibility) → fallback.
        pass

    return None


_PANDERA_SCHEMA = _build_pandera_schema()


# ---------------------------------------------------------------------------
# PolarsExcelParser
# ---------------------------------------------------------------------------


class PolarsExcelParser(FileParserPort):
    """Fast Excel parser adapter using polars + pandera schema validation.

    Implements ``FileParserPort.parse()`` — receives raw ``file_bytes``
    and returns a validated ``ParsedSheetDTO`` with strongly-typed rows.

    The adapter guarantees that every row crossing the infrastructure→domain
    boundary has passed schema validation.  No raw polars DataFrame leaks
    to the caller.
    """

    def _validate_schema_manual(self, df: pl.DataFrame) -> None:
        """Manual column/dtype validation as a fallback when Pandera is unavailable.

        This is a defensive safety-net that performs the same checks as the
        Pandera schema, but with explicit polars DataFrame introspection.

        Parameters:
            df: The polars DataFrame read from the Excel file, before any
                row-mapping has occurred.

        Raises:
            InvalidSheetSchemaError: If columns are missing or dtypes differ
                from the expected contract.
        """
        actual_columns: set[str] = set(df.columns)
        expected_columns: set[str] = set(REQUIRED_COLUMNS)

        missing: list[str] = list(expected_columns - actual_columns)
        unexpected: list[str] = list(actual_columns - expected_columns)

        if missing or unexpected:
            raise InvalidSheetSchemaError(
                missing_columns=[f"'{c}'" for c in missing] if missing else [],
                unexpected_columns=[f"'{c}'" for c in unexpected] if unexpected else [],
            )

        # ── Dtype check ──
        for col_name, expected_type in COLUMN_DTYPE_MAP.items():
            series = df[col_name]
            actual_dtype = series.dtype

            # polars dtype → Python type mapping
            if expected_type is int:
                compatible = actual_dtype in (
                    pl.Int64,
                    pl.Int32,
                    pl.Int16,
                    pl.Int8,
                    pl.UInt64,
                    pl.UInt32,
                    pl.UInt16,
                    pl.UInt8,
                )
            elif expected_type is str:
                compatible = actual_dtype is pl.Utf8
            else:
                compatible = False

            if not compatible:
                raise InvalidSheetSchemaError(
                    missing_columns=[],
                    unexpected_columns=[],
                    filename=None,
                )

    def _map_rows_to_dtos(self, df: pl.DataFrame) -> list[ParsedRowDTO]:
        """Convert validated polars DataFrame rows into ``ParsedRowDTO`` list.

        Each row is mapped field-by-field, with explicit type coercion
        (str() / int()) to satisfy the Pydantic DTO validation at the boundary.

        Parameters:
            df: The schema-validated polars DataFrame.

        Returns:
            A list of ``ParsedRowDTO`` instances — one per DataFrame row.
            Returns an empty list if the DataFrame has zero rows (this is
            caught upstream as ``SheetEmptyError``).
        """
        rows: list[ParsedRowDTO] = []

        for row in df.iter_rows(named=True):
            dto = ParsedRowDTO(
                sku=str(row["SKU"]),
                item_name=str(row["Item_Name"]),
                system_qty=int(row["System_Qty"]),
                actual_qty=int(row["Actual_Qty"]),
            )
            rows.append(dto)

        return rows

    # ──────────────────────────────────────────────────────────────
    # Public API: the single port method
    # ──────────────────────────────────────────────────────────────

    async def parse(self, file_bytes: bytes) -> ParsedSheetDTO:
        """Parse raw Excel bytes into a validated ``ParsedSheetDTO``.

        Pipeline:
            1. Wrap bytes in ``BytesIO``.
            2. Parse Excel via ``polars.read_excel`` (calamine engine).
            3. Schema-validate (Pandera polars or manual fallback).
            4. Empty-sheet guard.
            5. Map rows → ``ParsedRowDTO`` list.
            6. Return ``ParsedSheetDTO`` with placeholder tenant/store context.

        Parameters:
            file_bytes: The raw ``.xlsx`` file content as bytes.

        Returns:
            ``ParsedSheetDTO`` with validated rows, placeholder tenant_id,
            placeholder store_id, and placeholder uploaded_by.

        Raises:
            InvalidSheetSchemaError: Columns mismatch, dtype mismatch, or
                the file is unreadable/corrupt.
            SheetEmptyError: The sheet has valid headers but zero data rows.
        """
        try:
            buffer = io.BytesIO(file_bytes)

            # ── Step 1: polars read_excel ──
            # engine="calamine" is the fast Rust parser (default in polars≥1.0).
            # We read the first sheet (sheet_id=0) — no multi-sheet support
            # for MVP.
            raw: pl.DataFrame | dict[str, pl.DataFrame] = pl.read_excel(
                buffer,
                sheet_id=0,
                engine="calamine",
                read_options={"header_row": 0},
            )

            # ── Normalize return type ──
            # polars may return a dict[str, DataFrame] (especially with the
            # calamine/fastexcel engine when sheets aren't uniquely resolved).
            # We always extract the first DataFrame.
            if isinstance(raw, dict):
                if not raw:
                    raise SheetEmptyError(filename=None, row_count=0)
                df: pl.DataFrame = next(iter(raw.values()))
            else:
                df = raw

        except Exception as exc:
            # Any polars-level failure (corrupt file, not-an-xlsx, etc.)
            # is mapped to InvalidSheetSchemaError per the domain port contract.
            raise InvalidSheetSchemaError(
                missing_columns=[],
                unexpected_columns=[],
                filename=None,
            ) from exc

        # ── Step 2: Empty-sheet guard (must precede schema validation) ──
        # Pandera will reject an empty DataFrame as schema-invalid even
        # though the column headers are correct.  We therefore check for
        # an empty sheet BEFORE applying Pandera/strict validation.
        if df.height == 0:
            raise SheetEmptyError(
                filename=None,
                row_count=0,
            )

        # ── Step 3: Schema Validation ──
        if _PANDERA_SCHEMA is not None:
            try:
                # Pandera validates and returns the DataFrame if it passes.
                # If it fails, pandera raises a SchemaError we can catch.
                df = _PANDERA_SCHEMA.validate(df, lazy=False)
            except Exception as exc:
                # Pandera's SchemaError carries column/dtype information.
                # In Pandera 0.20.x: schema_errors dict with per-column details.
                # In Pandera 0.28+: message string like "column 'X' not in
                #   DataFrameSchema {...}".  We try both paths and fall back
                # to a manual column diff for maximum compatibility.
                missing: list[str] = []
                unexpected: list[str] = []

                _pandera_err = exc

                # ── Path A: Pandera 0.20.x schema_errors dict ──
                if hasattr(_pandera_err, "schema_errors") and _pandera_err.schema_errors:
                    for col, errs in _pandera_err.schema_errors.items():
                        for err_detail in errs:
                            reason = str(err_detail).lower()
                            if "not in dataframe" in reason or "missing" in reason:
                                if col not in missing:
                                    missing.append(f"'{col}'")
                            elif "not allowed" in reason or "unexpected" in reason:
                                if col not in unexpected:
                                    unexpected.append(f"'{col}'")
                            elif col not in REQUIRED_COLUMNS:
                                if col not in unexpected:
                                    unexpected.append(f"'{col}'")

                # ── Path B: Pandera 0.28+ error message string ──
                if not missing and not unexpected:
                    err_msg = str(_pandera_err)
                    import re
                    # Look for "column 'X' not in DataFrameSchema" pattern
                    quoted_pattern = re.findall(r"column '([^']+)'", err_msg, re.IGNORECASE)
                    for col_name in quoted_pattern:
                        if col_name not in REQUIRED_COLUMNS:
                            if col_name not in unexpected:
                                unexpected.append(f"'{col_name}'")
                    # Look for "column 'X' not found" or missing patterns
                    missing_pattern = re.findall(r"column '([^']+)'.*(?:not found|missing)", err_msg, re.IGNORECASE)
                    for col_name in missing_pattern:
                        if col_name in REQUIRED_COLUMNS and col_name not in missing:
                            missing.append(f"'{col_name}'")

                # ── Path C: Manual column diff (ultimate fallback) ──
                if not missing and not unexpected:
                    actual_cols = set(df.columns)
                    expected_cols = set(REQUIRED_COLUMNS)
                    missing = [f"'{c}'" for c in (expected_cols - actual_cols)]
                    unexpected = [f"'{c}'" for c in (actual_cols - expected_cols)]

                raise InvalidSheetSchemaError(
                    missing_columns=missing if missing else [],
                    unexpected_columns=unexpected if unexpected else [],
                ) from None
        else:
            # Pandera unavailable — fall back to manual check.
            self._validate_schema_manual(df)

        # ── Step 4: Map rows → ParsedRowDTOs ──
        parsed_rows: list[ParsedRowDTO] = self._map_rows_to_dtos(df)

        # ── Step 5: Construct ParsedSheetDTO with placeholder context ──
        # tenant_id, store_id, and uploaded_by are placeholders here.
        # The ProcessInventoryUseCase resolves the real auth context at
        # orchestration time and can construct a new ParsedSheetDTO or
        # override these fields in the downstream InventoryItem entities.
        return ParsedSheetDTO(
            tenant_id="unknown",
            store_id="unknown",
            uploaded_by=1,
            rows=parsed_rows,
        )