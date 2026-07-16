"""
enterprise.metadata_loader
==========================
Unified Enterprise Metadata Loader.

This module provides a single entry-point — :class:`EnterpriseMetadataLoader`
— that orchestrates every metadata parser and merges their output into one
:class:`~graph.enterprise_graph.EnterpriseGraph`.

Sources loaded by default
-------------------------
+---------------------+-------------------------------------------+
| Source              | Parser                                    |
+=====================+===========================================+
| Power BI            | :class:`~enterprise.parsers.PowerBIParser`|
|                     | (via MetadataEngine)                      |
+---------------------+-------------------------------------------+
| Databricks notebooks| :class:`~enterprise.notebook_parser.      |
|                     | EcoNotebookParser`                        |
+---------------------+-------------------------------------------+
| Databricks workflow | :class:`~enterprise.workflow_parser.      |
|                     | DatabricksWorkflowParser`                 |
+---------------------+-------------------------------------------+
| SQL DDL             | :class:`~enterprise.sql_parser.           |
|                     | SQLMetadataParser`                        |
+---------------------+-------------------------------------------+
| ADLS inventory      | :class:`~enterprise.adls_parser.          |
|                     | ADLSMetadataParser`                       |
+---------------------+-------------------------------------------+

Duplicate handling
------------------
* **Assets**: when two parsers emit an asset with the same ``id``, the *first*
  emitted asset is kept and subsequent duplicates are silently discarded.
  This preserves the richer asset produced by the primary source while
  avoiding key collisions in :class:`~graph.enterprise_graph.EnterpriseGraph`.

* **Relationships**: an edge with the same ``(source, target, relationship)``
  triple is deduplicated — only the first occurrence is kept.

Fault tolerance
---------------
* A missing directory / file never raises; the corresponding parser simply
  contributes zero assets and zero edges (each parser is already fault-tolerant
  by contract).
* An exception thrown by any individual parser is caught, logged as a WARNING,
  and the loader continues with the remaining parsers.
* :meth:`~EnterpriseMetadataLoader.load` always returns a valid (possibly empty)
  :class:`~graph.enterprise_graph.EnterpriseGraph`.

Usage
-----
Default paths (all sources active)::

    from enterprise.metadata_loader import EnterpriseMetadataLoader

    graph = EnterpriseMetadataLoader().load()

Custom paths::

    graph = EnterpriseMetadataLoader(
        powerbi_semantic_model_path="my.SemanticModel",
        powerbi_report_path="my.Report",
        databricks_dir=Path("metadata/databricks"),
        sql_dir=Path("metadata/sql"),
        adls_csv=Path("metadata/adls/adls_inventory.csv"),
    ).load()

Disable individual sources::

    graph = EnterpriseMetadataLoader(
        load_powerbi=False,
        load_sql=False,
    ).load()
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from enterprise.adls_parser import ADLSMetadataParser
from enterprise.notebook_parser import EcoNotebookParser
from enterprise.parsers import BaseMetadataParser
from enterprise.sql_parser import SQLMetadataParser
from enterprise.workflow_parser import DatabricksWorkflowParser
from graph.enterprise_graph import EnterpriseGraph
from graph.models import Asset, AssetType, Relationship, RelationshipType, SystemType

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Delta table → Power BI table bridge
# ---------------------------------------------------------------------------

def _build_delta_bridge_relationships(graph: EnterpriseGraph) -> List[Relationship]:
    """Return FEEDS edges bridging delta_table assets to matching table assets.

    The medallion pipeline writes to fully-qualified Delta tables such as
    ``delta_table::databricks_course_ws.gold.customer_360`` while the Power BI
    semantic model stores the same data as ``table::customer_360`` (bare name).
    These two assets have different IDs and asset types, so BFS cannot cross
    the boundary without explicit bridge edges.

    For every ``delta_table::*`` asset in *graph* whose **bare table name**
    (the last dot-separated segment) matches the name of a ``table::*`` asset
    in the same graph, a ``delta_table --FEEDS--> table`` edge is added.

    The function never creates duplicate assets — it only adds ``Relationship``
    objects.  Duplicate bridge edges (same source/target/type) are harmless
    because :meth:`~enterprise.metadata_loader.EnterpriseMetadataLoader._build_graph`
    deduplicates on the ``(source, target, relationship_type)`` triple.

    Args:
        graph: The fully assembled :class:`~graph.enterprise_graph.EnterpriseGraph`.

    Returns:
        List of new :class:`~graph.models.Relationship` objects to add to the graph.
    """
    # Build a lookup: bare_name → table:: asset id
    table_by_name: Dict[str, str] = {}
    for asset in graph.assets.values():
        if asset.asset_type == AssetType.TABLE:
            # asset.name is already the bare name (e.g. "customer_360")
            table_by_name[asset.name.lower()] = asset.id

    if not table_by_name:
        return []

    bridge_rels: List[Relationship] = []
    existing_edges: Set[Tuple[str, str, str]] = {
        (r.source, r.target, r.relationship.value)
        for r in graph.relationships
    }

    for asset in graph.assets.values():
        if asset.asset_type != AssetType.DELTA_TABLE:
            continue
        # Extract the bare table name: last segment after the final dot
        # e.g. "databricks_course_ws.gold.customer_360" → "customer_360"
        #      "bronze.dim_customer"                    → "dim_customer"
        raw_ref = asset.id.split("::", 1)[-1]   # strip "delta_table::" prefix
        bare_name = raw_ref.rsplit(".", 1)[-1].lower()

        target_id = table_by_name.get(bare_name)
        if target_id is None:
            continue

        edge_key = (asset.id, target_id, RelationshipType.FEEDS.value)
        if edge_key in existing_edges:
            continue

        bridge_rels.append(Relationship(
            source=asset.id,
            target=target_id,
            relationship=RelationshipType.FEEDS,
            properties={"via": "delta_table_bridge"},
        ))
        existing_edges.add(edge_key)
        logger.debug(
            "_build_delta_bridge_relationships: %s --FEEDS--> %s",
            asset.id, target_id,
        )

    if bridge_rels:
        logger.info(
            "EnterpriseMetadataLoader: added %d delta→table bridge edge(s)",
            len(bridge_rels),
        )
    return bridge_rels


def _build_powerbi_downstream_edges(graph: EnterpriseGraph) -> List[Relationship]:
    """Return FEEDS edges for Power BI downstream impact traversal.

    The :class:`~graph.adapter.MetadataAdapter` creates edges in the direction
    that represents semantic dependency:
        ``column   --DEPENDS_ON--> table``
        ``measure  --DEPENDS_ON--> table``
        ``measure  --DEPENDS_ON--> measure``
        ``measure  --USES-->       column``
        ``report   --USES-->       table``
        ``report   --DISPLAYS-->   measure``

    For downstream BFS (source→target = upstream→downstream), these edges are
    traversed **backwards** — a change to a ``table`` never reaches its
    consuming ``column``, ``measure``, or ``report`` because those assets point
    *toward* the table, not away from it.

    This function adds the inverse ``FEEDS`` edges so that BFS correctly
    propagates a table change to all downstream Power BI consumers:

    - ``table  --FEEDS--> column``   (column depends on the table)
    - ``table  --FEEDS--> measure``  (measure references the table)
    - ``column --FEEDS--> measure``  (measure uses the column)
    - ``measure --FEEDS--> measure`` (downstream measure depends on upstream)
    - ``table  --FEEDS--> report``   (report uses the table directly)
    - ``measure --FEEDS--> report``  (report displays the measure)

    Duplicate edges are skipped.

    Args:
        graph: The fully assembled :class:`~graph.enterprise_graph.EnterpriseGraph`.

    Returns:
        List of new :class:`~graph.models.Relationship` objects to add.
    """
    reverse_feeds: List[Relationship] = []
    existing_edges: Set[Tuple[str, str, str]] = {
        (r.source, r.target, r.relationship.value)
        for r in graph.relationships
    }

    # Relationship types for which we add a reversed FEEDS edge
    _REVERSE_TYPES = frozenset({
        RelationshipType.DEPENDS_ON.value,
        RelationshipType.USES.value,
        RelationshipType.DISPLAYS.value,
    })

    for rel in graph.relationships:
        if rel.relationship.value not in _REVERSE_TYPES:
            continue
        # Only invert edges that involve Power BI / database assets
        src_asset = graph.assets.get(rel.source)
        tgt_asset = graph.assets.get(rel.target)
        if src_asset is None or tgt_asset is None:
            continue
        # Only create reverse-FEEDS for Power BI and database semantic-model edges
        relevant_types = {
            AssetType.COLUMN.value,
            AssetType.TABLE.value,
            AssetType.MEASURE.value,
            AssetType.REPORT.value,
            AssetType.DATABASE_TABLE.value,
            AssetType.DATABASE_COLUMN.value,
            AssetType.PRIMARY_KEY.value,
            AssetType.FOREIGN_KEY.value,
        }
        if src_asset.asset_type.value not in relevant_types:
            continue
        if tgt_asset.asset_type.value not in relevant_types:
            continue

        # Inverted: target --FEEDS--> source
        edge_key = (rel.target, rel.source, RelationshipType.FEEDS.value)
        if edge_key not in existing_edges:
            reverse_feeds.append(Relationship(
                source=rel.target,
                target=rel.source,
                relationship=RelationshipType.FEEDS,
                properties={"via": "powerbi_reverse"},
            ))
            existing_edges.add(edge_key)

    if reverse_feeds:
        logger.info(
            "EnterpriseMetadataLoader: added %d Power BI downstream FEEDS edge(s)",
            len(reverse_feeds),
        )
    return reverse_feeds


# ---------------------------------------------------------------------------
# Type alias
# ---------------------------------------------------------------------------

_ParseResult = Tuple[List[Asset], List[Relationship]]


# ---------------------------------------------------------------------------
# EnterpriseMetadataLoader
# ---------------------------------------------------------------------------


class EnterpriseMetadataLoader:
    """Orchestrate all metadata parsers and merge their output into one graph.

    Each source is loaded independently.  A failure in one source never
    prevents the others from running.

    Parameters
    ----------
    powerbi_semantic_model_path:
        Path to the Power BI semantic model directory
        (e.g. ``"sales.SemanticModel"``).  Defaults to ``"sales.SemanticModel"``
        relative to the working directory.  Ignored when ``load_powerbi=False``.
    powerbi_report_path:
        Path to the Power BI report directory (e.g. ``"sales.Report"``).
        Ignored when ``load_powerbi=False``.
    databricks_dir:
        Directory containing Databricks notebook ``.py`` files and the
        ``pipeline.yml`` workflow definition.
        Defaults to ``metadata/databricks``.
        Set to ``None`` or a non-existent path to skip Databricks sources.
    sql_dir:
        Directory containing ``.sql`` DDL files.
        Defaults to ``metadata/sql``.
        Set to ``None`` or a non-existent path to skip SQL.
    adls_csv:
        Path to the ADLS inventory CSV file.
        Defaults to ``metadata/adls/adls_inventory.csv``.
        Set to ``None`` or a non-existent path to skip ADLS.
    load_powerbi:
        ``True`` (default) to attempt loading Power BI metadata.
    load_databricks:
        ``True`` (default) to load Databricks notebooks and workflows.
    load_sql:
        ``True`` (default) to load SQL DDL metadata.
    load_adls:
        ``True`` (default) to load ADLS inventory metadata.
    """

    #: Default paths — relative to the process working directory.
    DEFAULT_POWERBI_SEMANTIC_MODEL = Path("sales.SemanticModel")
    DEFAULT_POWERBI_REPORT         = Path("sales.Report")
    DEFAULT_DATABRICKS_DIR         = Path("metadata") / "databricks"
    DEFAULT_SQL_DIR                = Path("metadata") / "sql"
    DEFAULT_ADLS_CSV               = Path("metadata") / "adls" / "adls_inventory.csv"

    def __init__(
        self,
        *,
        powerbi_semantic_model_path: Optional[Any] = None,
        powerbi_report_path: Optional[Any] = None,
        databricks_dir: Optional[Any] = None,
        sql_dir: Optional[Any] = None,
        adls_csv: Optional[Any] = None,
        load_powerbi: bool = True,
        load_databricks: bool = True,
        load_sql: bool = True,
        load_adls: bool = True,
    ) -> None:
        self._powerbi_semantic = (
            Path(powerbi_semantic_model_path)
            if powerbi_semantic_model_path is not None
            else self.DEFAULT_POWERBI_SEMANTIC_MODEL
        )
        self._powerbi_report = (
            Path(powerbi_report_path)
            if powerbi_report_path is not None
            else self.DEFAULT_POWERBI_REPORT
        )
        self._databricks_dir: Optional[Path] = (
            Path(databricks_dir) if databricks_dir is not None else self.DEFAULT_DATABRICKS_DIR
        )
        self._sql_dir: Optional[Path] = (
            Path(sql_dir) if sql_dir is not None else self.DEFAULT_SQL_DIR
        )
        self._adls_csv: Optional[Path] = (
            Path(adls_csv) if adls_csv is not None else self.DEFAULT_ADLS_CSV
        )

        self._load_powerbi    = load_powerbi
        self._load_databricks = load_databricks
        self._load_sql        = load_sql
        self._load_adls       = load_adls

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load(self) -> EnterpriseGraph:
        """Run all enabled parsers and merge results into one EnterpriseGraph.

        Returns
        -------
        EnterpriseGraph
            Combined graph from all sources.  Assets with duplicate IDs are
            merged (first-wins).  Duplicate edges are removed.
            Never raises — returns an empty graph on total failure.
        """
        all_assets:        List[Asset]        = []
        all_relationships: List[Relationship] = []

        def _collect(label: str, loader_fn) -> None:  # type: ignore[type-arg]
            """Run *loader_fn* and extend accumulators; catch any exception."""
            try:
                assets, rels = loader_fn()
                all_assets.extend(assets)
                all_relationships.extend(rels)
            except Exception as exc:  # pylint: disable=broad-except
                logger.warning(
                    "EnterpriseMetadataLoader: %s raised an unexpected error — %s",
                    label, exc,
                )

        # ── Power BI ──────────────────────────────────────────────────────
        if self._load_powerbi:
            _collect("PowerBI", self._load_powerbi_source)

        # ── Databricks notebooks (ECO METADATA blocks) ────────────────────
        if self._load_databricks:
            _collect("EcoNotebooks", self._load_eco_notebooks)
            # ── Databricks Workflow (pipeline.yml) ─────────────────────────
            _collect("DatabricksWorkflow", self._load_databricks_workflow)

        # ── SQL DDL ───────────────────────────────────────────────────────
        if self._load_sql:
            _collect("SQL", self._load_sql_source)

        # ── ADLS inventory ────────────────────────────────────────────────
        if self._load_adls:
            _collect("ADLS", self._load_adls_source)

        # ── Merge into one graph ──────────────────────────────────────────
        graph = self._build_graph(all_assets, all_relationships)

        # ── Delta → table bridge (Fix 3) ──────────────────────────────────
        # Must run AFTER the full graph is assembled so both delta_table and
        # table assets are present.  Bridge edges are added directly to the
        # graph (not via _build_graph) because deduplication already ran.
        for bridge_rel in _build_delta_bridge_relationships(graph):
            graph.add_relationship(bridge_rel)

        # ── Power BI reverse FEEDS edges (Fix 5) ──────────────────────────
        # Invert DEPENDS_ON / USES / DISPLAYS edges so that downstream BFS
        # from a table correctly reaches columns, measures, and reports.
        for bridge_rel in _build_powerbi_downstream_edges(graph):
            graph.add_relationship(bridge_rel)

        logger.info(
            "EnterpriseMetadataLoader: built graph with %d assets, %d relationships",
            len(graph.assets),
            len(graph.relationships),
        )
        return graph

    # ------------------------------------------------------------------
    # Source loaders
    # ------------------------------------------------------------------

    def _load_powerbi_source(self) -> _ParseResult:
        """Load Power BI metadata via MetadataEngine → PowerBIParser.

        Returns ``([], [])`` when the semantic model / report directories are
        absent so the loader never throws on a machine without PBIP files.
        """
        sm_path = self._powerbi_semantic
        rpt_path = self._powerbi_report

        if not sm_path.exists() or not rpt_path.exists():
            logger.info(
                "EnterpriseMetadataLoader: Power BI paths not found "
                "(%s, %s) — skipping",
                sm_path,
                rpt_path,
            )
            return [], []

        try:
            # Import lazily to avoid pulling metadata engine into tests
            # that do not exercise Power BI.
            from enterprise.parsers import PowerBIParser  # noqa: PLC0415
            from metadata.loader import MetadataEngine     # noqa: PLC0415

            engine = MetadataEngine(
                semantic_model_path=sm_path,
                report_path=rpt_path,
            )
            payload = engine.load()
            parser = PowerBIParser(payload=payload)
            return parser.parse()

        except Exception as exc:  # pylint: disable=broad-except
            logger.warning(
                "EnterpriseMetadataLoader: Power BI load failed — %s", exc
            )
            return [], []

    def _load_eco_notebooks(self) -> _ParseResult:
        """Load ECO METADATA blocks from ``*.py`` notebooks in *databricks_dir*."""
        db_dir = self._databricks_dir
        if db_dir is None or not db_dir.exists():
            logger.info(
                "EnterpriseMetadataLoader: Databricks directory %s not found — skipping notebooks",
                db_dir,
            )
            return [], []

        py_files = sorted(db_dir.glob("*.py"))
        if not py_files:
            logger.info(
                "EnterpriseMetadataLoader: no *.py files in %s — skipping notebooks", db_dir
            )
            return [], []

        try:
            parser = EcoNotebookParser.from_files(py_files)
            return parser.parse()
        except Exception as exc:  # pylint: disable=broad-except
            logger.warning(
                "EnterpriseMetadataLoader: EcoNotebookParser failed — %s", exc
            )
            return [], []

    def _load_databricks_workflow(self) -> _ParseResult:
        """Load pipeline workflow from ``pipeline.yml`` in *databricks_dir*."""
        db_dir = self._databricks_dir
        if db_dir is None or not db_dir.exists():
            return [], []

        pipeline_yml = db_dir / "pipeline.yml"
        try:
            parser = DatabricksWorkflowParser(source=pipeline_yml)
            return parser.parse()
        except Exception as exc:  # pylint: disable=broad-except
            logger.warning(
                "EnterpriseMetadataLoader: DatabricksWorkflowParser failed — %s", exc
            )
            return [], []

    def _load_sql_source(self) -> _ParseResult:
        """Load SQL DDL metadata from the *sql_dir* directory."""
        sql_dir = self._sql_dir
        if sql_dir is None or not sql_dir.exists():
            logger.info(
                "EnterpriseMetadataLoader: SQL directory %s not found — skipping",
                sql_dir,
            )
            return [], []

        try:
            parser = SQLMetadataParser(source=sql_dir)
            return parser.parse()
        except Exception as exc:  # pylint: disable=broad-except
            logger.warning(
                "EnterpriseMetadataLoader: SQLMetadataParser failed — %s", exc
            )
            return [], []

    def _load_adls_source(self) -> _ParseResult:
        """Load ADLS file inventory from the *adls_csv* path."""
        adls_csv = self._adls_csv
        if adls_csv is None or not adls_csv.exists():
            logger.info(
                "EnterpriseMetadataLoader: ADLS CSV %s not found — skipping",
                adls_csv,
            )
            return [], []

        try:
            parser = ADLSMetadataParser(source=adls_csv)
            return parser.parse()
        except Exception as exc:  # pylint: disable=broad-except
            logger.warning(
                "EnterpriseMetadataLoader: ADLSMetadataParser failed — %s", exc
            )
            return [], []

    # ------------------------------------------------------------------
    # Graph assembly
    # ------------------------------------------------------------------

    @staticmethod
    def _build_graph(
        assets: List[Asset],
        relationships: List[Relationship],
    ) -> EnterpriseGraph:
        """Merge *assets* and *relationships* into a deduplicated EnterpriseGraph.

        Duplicate asset IDs: first-wins — subsequent occurrences are logged at
        DEBUG level and discarded.

        Duplicate edges: an edge ``(source, target, relationship_type)`` is
        deduplicated — first occurrence wins.

        Parameters
        ----------
        assets:
            All assets collected from every parser (may contain duplicates).
        relationships:
            All edges collected from every parser (may contain duplicates).

        Returns
        -------
        EnterpriseGraph
            Deduplicated graph ready for analysis.
        """
        graph = EnterpriseGraph()

        # ── Assets (first-wins deduplication) ────────────────────────────
        for asset in assets:
            if asset.id not in graph.assets:
                graph.add_asset(asset)
            else:
                logger.debug(
                    "EnterpriseMetadataLoader: duplicate asset id %r — keeping first",
                    asset.id,
                )

        # ── Relationships (first-wins deduplication on (src, tgt, type)) ─
        seen_edges: Set[Tuple[str, str, str]] = set()
        for rel in relationships:
            edge_key = (rel.source, rel.target, rel.relationship.value)
            if edge_key not in seen_edges:
                seen_edges.add(edge_key)
                graph.add_relationship(rel)
            else:
                logger.debug(
                    "EnterpriseMetadataLoader: duplicate edge %s→%s (%s) — skipped",
                    rel.source,
                    rel.target,
                    rel.relationship.value,
                )

        return graph
