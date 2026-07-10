"""🔧 Real Report Export Adapters — Infrastructure Layer.

This subpackage provides concrete implementations of the domain's
``ReportExporterPort`` (defined in ``src.domain.ports``).  Each
adapter generates a formatted Excel / CSV report from an
``InventorySnapshot`` domain aggregate and returns a
``ReportResultDTO`` containing the Markdown summary and the
attachment bytes.

Import Discipline (Hexagonal — Infrastructure Layer):
    * MAY import from ``src.domain`` (ports, schemas, models, exceptions)
    * MAY import framework-specific libraries (``polars``, ``io.BytesIO``,
      ``structlog``)
    * MUST NOT import from ``src.application`` or ``src.presentation``
"""

from src.infrastructure.exporters.excel_exporter import PolarsReportExporter

__all__ = ["PolarsReportExporter"]
