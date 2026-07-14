"""
metadata.dependency_builder
===========================
Infers dependency edges between every metadata artifact and assembles
the complete dependency graph.

Inference rules
---------------
1.  **Column → Table** (``COLUMN_BELONGS_TO_TABLE``)
    Every column always depends on its parent table.

2.  **Measure → Column** (``MEASURE_USES_COLUMN``)
    Extracted from DAX via the pattern ``TableName[ColumnName]``.

3.  **Measure → Measure** (``MEASURE_USES_MEASURE``)
    Extracted from DAX via the pattern ``[MeasureName]`` (square-bracket
    reference without a table prefix).

4.  **Measure → Table** (``MEASURE_USES_TABLE``)
    Derived from the set of tables referenced in DAX column refs.

5.  **Relationship → Table (×2)** (``RELATIONSHIP_LINKS_TABLES``)
    Both the from-table and to-table are registered as targets.

6.  **Report → Measure** (``REPORT_USES_MEASURE``)
    From each visual's bound field refs where ``field_type == 'measure'``.

7.  **Report → Column** (``REPORT_USES_COLUMN``)
    From each visual's bound field refs where ``field_type == 'column'``.

8.  **Report → Table** (``REPORT_USES_TABLE``)
    Derived from the set of entity names referenced in any visual field.
"""

from __future__ import annotations

import logging
import re
from typing import Dict, List, Set

from metadata.models import (
    ColumnMetadata,
    DependencyMetadata,
    DependencyType,
    MeasureMetadata,
    RelationshipMetadata,
    ReportMetadata,
    TableMetadata,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# DAX reference patterns
# ---------------------------------------------------------------------------

# Matches table-qualified column references: TableName[ColumnName]
# Handles single-quoted table names: 'My Table'[Col]
_RE_DAX_COLUMN_REF = re.compile(
    r"(?:'([^']+)'|([A-Za-z_]\w*))\[([^\]]+)\]"
)

# Matches bare measure references: [MeasureName] — no table prefix
_RE_DAX_MEASURE_REF = re.compile(r"(?<!\w)\[([^\]]+)\]")


def _extract_dax_column_refs(expression: str) -> List[tuple[str, str]]:
    """Return a list of ``(table_name, column_name)`` tuples from a DAX expression."""
    refs: List[tuple[str, str]] = []
    for m in _RE_DAX_COLUMN_REF.finditer(expression):
        table = m.group(1) or m.group(2)  # quoted or unquoted table
        column = m.group(3)
        refs.append((table, column))
    return refs


def _extract_dax_measure_refs(expression: str, all_measure_names: Set[str]) -> List[str]:
    """Return measure names referenced as ``[Name]`` in a DAX expression.

    Only returns names that are present in ``all_measure_names`` to avoid
    false positives from column references already captured by the column
    regex.
    """
    refs: List[str] = []
    # Strip column refs first so column [Name] patterns don't match
    stripped = _RE_DAX_COLUMN_REF.sub("__COL__", expression)
    for m in _RE_DAX_MEASURE_REF.finditer(stripped):
        name = m.group(1)
        if name in all_measure_names:
            refs.append(name)
    return refs


# ---------------------------------------------------------------------------
# DependencyBuilder
# ---------------------------------------------------------------------------


class DependencyBuilder:
    """Builds the complete dependency graph from the parsed metadata catalogs.

    Parameters
    ----------
    tables:
        All :class:`~metadata.models.TableMetadata` objects.
    columns:
        All :class:`~metadata.models.ColumnMetadata` objects.
    measures:
        All :class:`~metadata.models.MeasureMetadata` objects.
    relationships:
        All :class:`~metadata.models.RelationshipMetadata` objects.
    reports:
        All :class:`~metadata.models.ReportMetadata` objects.
    """

    def __init__(
        self,
        tables: List[TableMetadata],
        columns: List[ColumnMetadata],
        measures: List[MeasureMetadata],
        relationships: List[RelationshipMetadata],
        reports: List[ReportMetadata],
    ) -> None:
        self._tables = tables
        self._columns = columns
        self._measures = measures
        self._relationships = relationships
        self._reports = reports

        # Fast lookup indexes
        self._table_names: Set[str] = {t.name for t in tables}
        self._measure_names: Set[str] = {m.name for m in measures}
        # Qualified column index: "TableName[ColName]" → ColumnMetadata
        self._column_index: Dict[str, ColumnMetadata] = {
            c.qualified_name: c for c in columns
        }

        self._edges: List[DependencyMetadata] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def build(self) -> List[DependencyMetadata]:
        """Run all inference rules and return the complete edge list."""
        self._edges.clear()

        self._infer_column_to_table()
        self._infer_measure_deps()
        self._infer_relationship_deps()
        self._infer_report_deps()

        logger.info("Dependency graph: %d edges inferred", len(self._edges))
        return self._edges

    def annotate_measures(self) -> List[MeasureMetadata]:
        """Return measures with ``referenced_tables``, ``referenced_columns``,
        and ``referenced_measures`` fields populated.

        Must be called *after* :meth:`build`.
        """
        annotated: List[MeasureMetadata] = []
        for meas in self._measures:
            col_refs = _extract_dax_column_refs(meas.expression)
            meas_refs = _extract_dax_measure_refs(meas.expression, self._measure_names)
            table_refs = list({t for t, _ in col_refs if t in self._table_names})
            col_qualified = [
                f"{t}[{c}]"
                for t, c in col_refs
                if f"{t}[{c}]" in self._column_index
            ]
            annotated.append(
                meas.model_copy(
                    update={
                        "referenced_tables": table_refs,
                        "referenced_columns": col_qualified,
                        "referenced_measures": list(set(meas_refs)),
                    }
                )
            )
        return annotated

    def annotate_reports(self) -> List[ReportMetadata]:
        """Return reports with ``used_measures``, ``used_columns``,
        and ``used_tables`` populated from their visual field bindings.

        Must be called *after* :meth:`build`.
        """
        annotated: List[ReportMetadata] = []
        for report in self._reports:
            used_measures: Set[str] = set()
            used_columns: Set[str] = set()
            used_tables: Set[str] = set()
            for visual in report.visuals:
                for field in visual.fields:
                    if field.field_type == "measure":
                        used_measures.add(field.property_name)
                    elif field.field_type == "column":
                        used_columns.add(f"{field.entity}[{field.property_name}]")
                    if field.entity:
                        used_tables.add(field.entity)
            annotated.append(
                report.model_copy(
                    update={
                        "used_measures": sorted(used_measures),
                        "used_columns": sorted(used_columns),
                        "used_tables": sorted(used_tables),
                    }
                )
            )
        return annotated

    # ------------------------------------------------------------------
    # Inference rules (private)
    # ------------------------------------------------------------------

    def _add(
        self,
        source_type: str,
        source_name: str,
        target_type: str,
        target_name: str,
        dep_type: DependencyType,
        metadata: Dict | None = None,
    ) -> None:
        self._edges.append(
            DependencyMetadata(
                source_type=source_type,
                source_name=source_name,
                target_type=target_type,
                target_name=target_name,
                dependency_type=dep_type,
                metadata=metadata or {},
            )
        )

    def _infer_column_to_table(self) -> None:
        """Rule 1 — Every column depends on its parent table."""
        for col in self._columns:
            if col.table_name in self._table_names:
                self._add(
                    source_type="column",
                    source_name=col.qualified_name,
                    target_type="table",
                    target_name=col.table_name,
                    dep_type=DependencyType.COLUMN_BELONGS_TO_TABLE,
                )

    def _infer_measure_deps(self) -> None:
        """Rules 2, 3, 4 — DAX column, measure, and table references."""
        for meas in self._measures:
            meas_qname = f"[{meas.name}]"
            expr = meas.expression
            if not expr:
                continue

            # --- Column refs ---
            seen_tables: Set[str] = set()
            for table_name, col_name in _extract_dax_column_refs(expr):
                qualified = f"{table_name}[{col_name}]"
                if qualified in self._column_index:
                    self._add(
                        source_type="measure",
                        source_name=meas_qname,
                        target_type="column",
                        target_name=qualified,
                        dep_type=DependencyType.MEASURE_USES_COLUMN,
                    )
                else:
                    logger.debug(
                        "Measure '%s' references unknown column %s", meas.name, qualified
                    )
                if table_name in self._table_names:
                    seen_tables.add(table_name)

            # --- Table refs (derived from column refs) ---
            for table_name in seen_tables:
                self._add(
                    source_type="measure",
                    source_name=meas_qname,
                    target_type="table",
                    target_name=table_name,
                    dep_type=DependencyType.MEASURE_USES_TABLE,
                )

            # --- Measure refs ---
            for ref_name in _extract_dax_measure_refs(expr, self._measure_names):
                if ref_name != meas.name:  # skip self-references
                    self._add(
                        source_type="measure",
                        source_name=meas_qname,
                        target_type="measure",
                        target_name=f"[{ref_name}]",
                        dep_type=DependencyType.MEASURE_USES_MEASURE,
                    )

    def _infer_relationship_deps(self) -> None:
        """Rule 5 — Relationships link two tables."""
        for rel in self._relationships:
            for tbl in (rel.from_table, rel.to_table):
                if tbl in self._table_names:
                    self._add(
                        source_type="relationship",
                        source_name=rel.relationship_id,
                        target_type="table",
                        target_name=tbl,
                        dep_type=DependencyType.RELATIONSHIP_LINKS_TABLES,
                        metadata={
                            "from_table": rel.from_table,
                            "from_column": rel.from_column,
                            "to_table": rel.to_table,
                            "to_column": rel.to_column,
                            "is_active": rel.is_active,
                        },
                    )

    def _infer_report_deps(self) -> None:
        """Rules 6, 7, 8 — Reports reference measures, columns, and tables."""
        for report in self._reports:
            report_qname = report.display_name or report.page_name
            seen_tables: Set[str] = set()

            for visual in report.visuals:
                for field in visual.fields:
                    entity = field.entity
                    prop = field.property_name

                    if field.field_type == "measure":
                        self._add(
                            source_type="report",
                            source_name=report_qname,
                            target_type="measure",
                            target_name=f"[{prop}]",
                            dep_type=DependencyType.REPORT_USES_MEASURE,
                            metadata={"visual": visual.visual_name, "visual_type": visual.visual_type},
                        )
                    elif field.field_type == "column":
                        qualified = f"{entity}[{prop}]"
                        self._add(
                            source_type="report",
                            source_name=report_qname,
                            target_type="column",
                            target_name=qualified,
                            dep_type=DependencyType.REPORT_USES_COLUMN,
                            metadata={"visual": visual.visual_name, "visual_type": visual.visual_type},
                        )

                    if entity:
                        seen_tables.add(entity)

            for table_name in seen_tables:
                self._add(
                    source_type="report",
                    source_name=report_qname,
                    target_type="table",
                    target_name=table_name,
                    dep_type=DependencyType.REPORT_USES_TABLE,
                )
