"""
metadata.loader
===============
MetadataEngine — the single public entry-point for the Metadata Engine.

Usage
-----
    from metadata import MetadataEngine

    engine = MetadataEngine(
        semantic_model_path="sales.SemanticModel",
        report_path="sales.Report",
    )
    payload = engine.load()   # returns MetadataPayload

The engine:
1. Discovers all TMDL table files and parses them via TmdlTableParser.
2. Parses relationships.tmdl via TmdlRelParser.
3. Discovers and parses all PBIR report pages via PbirPagesParser.
4. Hydrates Pydantic models from raw dicts.
5. Delegates to DependencyBuilder to build the dependency graph and
   annotate measures/reports with their inferred references.
6. Returns a fully populated MetadataPayload.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, List, Optional

from graph.adapter import MetadataAdapter
from graph.enterprise_graph import EnterpriseGraph
from metadata.dependency_builder import DependencyBuilder
from metadata.models import (
    ColumnMetadata,
    MeasureMetadata,
    MetadataPayload,
    RelationshipMetadata,
    ReportMetadata,
    TableMetadata,
    VisualFieldRef,
    VisualMetadata,
)
from metadata.parser import PbirPagesParser, TmdlRelParser, TmdlTableParser

logger = logging.getLogger(__name__)

# Tables created internally by Power BI for date auto-detection — skip them.
_INTERNAL_TABLE_PREFIXES = (
    "DateTableTemplate_",
    "LocalDateTable_",
)


class MetadataEngine:
    """Orchestrates the full metadata extraction pipeline.

    Parameters
    ----------
    semantic_model_path:
        Path to the PBIP semantic model root directory
        (e.g. ``"sales.SemanticModel"``).
    report_path:
        Path to the PBIP report root directory
        (e.g. ``"sales.Report"``).
    include_internal_tables:
        When ``False`` (default), Power BI internal date scaffolding tables
        (``DateTableTemplate_*``, ``LocalDateTable_*``) are excluded from
        the output.  Set to ``True`` to include them.
    """

    def __init__(
        self,
        semantic_model_path: str | Path = "sales.SemanticModel",
        report_path: str | Path = "sales.Report",
        include_internal_tables: bool = False,
    ) -> None:
        self.semantic_model_path = Path(semantic_model_path)
        self.report_path = Path(report_path)
        self.include_internal_tables = include_internal_tables
        self._payload: Optional[MetadataPayload] = None
        self._enterprise_graph: Optional[EnterpriseGraph] = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load(self) -> MetadataPayload:
        """Run the full extraction pipeline and return the metadata payload.

        Returns
        -------
        MetadataPayload
            Fully populated snapshot of every metadata artifact and the
            inferred dependency graph.

        Raises
        ------
        FileNotFoundError
            If either the semantic model or report directory does not exist.
        """
        self._validate_paths()

        logger.info("MetadataEngine: loading semantic model from %s", self.semantic_model_path)
        logger.info("MetadataEngine: loading report from %s", self.report_path)

        # --- Step 1: Parse TMDL tables ---
        raw_tables, raw_columns, raw_measures = self._load_tables()

        # --- Step 2: Parse relationships ---
        raw_relationships = self._load_relationships()

        # --- Step 3: Parse report pages ---
        raw_pages = self._load_report()

        # --- Step 4: Hydrate Pydantic models ---
        tables = [TableMetadata(**t) for t in raw_tables]
        columns = [ColumnMetadata(**c) for c in raw_columns]
        measures = [MeasureMetadata(**m) for m in raw_measures]
        relationships = [RelationshipMetadata(**r) for r in raw_relationships]
        reports = [ReportMetadata(**p) for p in raw_pages]

        # --- Step 5: Build dependency graph & annotate ---
        builder = DependencyBuilder(tables, columns, measures, relationships, reports)
        dependencies = builder.build()
        measures = builder.annotate_measures()
        reports = builder.annotate_reports()

        # --- Step 6: Assemble payload ---
        self._payload = MetadataPayload(
            tables=tables,
            columns=columns,
            measures=measures,
            relationships=relationships,
            reports=reports,
            dependencies=dependencies,
        )

        # --- Step 7: Build the enterprise graph ---
        self._enterprise_graph = MetadataAdapter.to_enterprise_graph(self._payload)

        logger.info(
            "MetadataEngine: loaded %d tables, %d columns, %d measures, "
            "%d relationships, %d pages, %d dependency edges",
            len(tables),
            len(columns),
            len(measures),
            len(relationships),
            len(reports),
            len(dependencies),
        )
        return self._payload

    def get_enterprise_graph(self) -> Optional[EnterpriseGraph]:
        """Return the :class:`~graph.enterprise_graph.EnterpriseGraph` built
        during the last :meth:`load` call, or ``None`` if :meth:`load` has
        not been called yet.

        The graph is rebuilt automatically on every :meth:`load` call.
        """
        return self._enterprise_graph

    def load_as_dict(self) -> Dict:
        """Convenience wrapper — returns :meth:`load` serialised to plain dict."""
        return self.load().to_dict()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _validate_paths(self) -> None:
        for p, label in [
            (self.semantic_model_path, "semantic_model_path"),
            (self.report_path, "report_path"),
        ]:
            if not p.exists():
                raise FileNotFoundError(f"{label} does not exist: {p.resolve()}")
            if not p.is_dir():
                raise FileNotFoundError(f"{label} must be a directory: {p.resolve()}")

    def _load_tables(
        self,
    ) -> tuple[List[Dict], List[Dict], List[Dict]]:
        """Parse all TMDL table files under ``definition/tables/``."""
        tables_dir = self.semantic_model_path / "definition" / "tables"
        if not tables_dir.is_dir():
            logger.warning("Tables directory not found: %s", tables_dir)
            return [], [], []

        all_tables: List[Dict] = []
        all_columns: List[Dict] = []
        all_measures: List[Dict] = []

        for tmdl_file in sorted(tables_dir.glob("*.tmdl")):
            raw = TmdlTableParser(tmdl_file).parse()
            if not raw or not raw.get("name"):
                logger.warning("Skipping unparseable TMDL file: %s", tmdl_file)
                continue

            table_name: str = raw["name"]

            # Optionally skip internal Power BI scaffolding tables
            if not self.include_internal_tables and any(
                table_name.startswith(prefix) for prefix in _INTERNAL_TABLE_PREFIXES
            ):
                logger.debug("Skipping internal table: %s", table_name)
                continue

            # Build TableMetadata dict (exclude column/measure lists)
            table_dict = {k: v for k, v in raw.items() if k not in ("columns", "measures")}
            all_tables.append(table_dict)

            # Flatten columns
            for col in raw.get("columns", []):
                all_columns.append(col)

            # Flatten measures
            for meas in raw.get("measures", []):
                all_measures.append(meas)

        logger.info(
            "Loaded %d tables, %d columns, %d measures from TMDL",
            len(all_tables),
            len(all_columns),
            len(all_measures),
        )
        return all_tables, all_columns, all_measures

    def _load_relationships(self) -> List[Dict]:
        """Parse ``relationships.tmdl``."""
        rel_file = self.semantic_model_path / "definition" / "relationships.tmdl"
        if not rel_file.exists():
            logger.warning("relationships.tmdl not found; skipping.")
            return []
        return TmdlRelParser(rel_file).parse()

    def _load_report(self) -> List[Dict]:
        """Parse all PBIR report pages."""
        parser = PbirPagesParser(self.report_path)
        raw_pages = parser.parse()

        # Hydrate VisualMetadata and VisualFieldRef inline so we can produce
        # ReportMetadata-compatible dicts
        hydrated: List[Dict] = []
        for page in raw_pages:
            visuals = []
            for v in page.get("visuals", []):
                fields = [VisualFieldRef(**f) for f in v.get("fields", [])]
                visuals.append(
                    VisualMetadata(
                        visual_name=v["visual_name"],
                        visual_type=v["visual_type"],
                        position=v.get("position", {}),
                        fields=fields,
                    )
                )
            hydrated.append(
                {
                    "page_name": page["page_name"],
                    "display_name": page["display_name"],
                    "display_option": page.get("display_option"),
                    "height": page.get("height"),
                    "width": page.get("width"),
                    "visuals": visuals,
                }
            )
        return hydrated
