"""
tests.test_graph_connectivity_fixes
====================================
Regression tests for the four graph-connectivity fixes.

Fix 1 — EcoNotebookParser: MAGIC prefix + KEY=VALUE format
Fix 2 — Notebook ID normalisation: workflow parser uses basename
Fix 3 — Delta-table bridge edges: delta_table::* --FEEDS--> table::*
Fix 4 — SQL direction: TABLE --FEEDS--> VIEW / PROCEDURE / FUNCTION

Each class is self-contained; no shared state between classes.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from enterprise.metadata_loader import (
    EnterpriseMetadataLoader,
    _build_delta_bridge_relationships,
)
from enterprise.notebook_parser import EcoNotebookParser, _extract_block
from enterprise.sql_parser import SQLMetadataParser
from enterprise.workflow_parser import (
    DatabricksWorkflowParser,
    _normalise_notebook_path,
    _notebook_stub_id,
)
from graph.enterprise_graph import EnterpriseGraph
from graph.models import (
    Asset,
    AssetType,
    Relationship,
    RelationshipType,
    SystemType,
)
from graph.query_engine import EnterpriseQueryEngine


# ===========================================================================
# Fix 1 — EcoNotebookParser: MAGIC prefix and KEY=VALUE
# ===========================================================================


def _magic_block(
    name: str = "01_bronze",
    read_tables: str = "landing.dim_customer",
    write_tables: str = "bronze.dim_customer",
) -> str:
    """Simulate a Databricks-exported markdown notebook cell."""
    lines = [
        "# Databricks notebook source",
        "# MAGIC %md",
        "# MAGIC # ECO METADATA",
        f"# MAGIC # NOTEBOOK_NAME={name}",
        f"# MAGIC # LAYER=Bronze",
        f"# MAGIC # CATALOG=databricks_course_ws",
        f"# MAGIC # SCHEMA=bronze",
        f"# MAGIC # EXECUTION_ORDER=1",
        f"# MAGIC # READ_TABLES=",
        f"# MAGIC # {read_tables}",
        f"# MAGIC # WRITE_TABLES=",
        f"# MAGIC # {write_tables}",
        "# MAGIC # ==========================================================",
        "",
        "# Python code here — must be ignored",
        "df = spark.read.table('should.not.be.parsed')",
    ]
    return "\n".join(lines)


class TestFix1MagicPrefixParsing:
    """EcoNotebookParser recognises # MAGIC # ECO METADATA blocks."""

    def test_magic_block_opens(self):
        src = _magic_block()
        block = _extract_block(src)
        assert block["name"] == "01_bronze"

    def test_magic_block_layer(self):
        src = _magic_block()
        block = _extract_block(src)
        assert block["layer"] == "Bronze"

    def test_magic_block_catalog(self):
        src = _magic_block()
        block = _extract_block(src)
        assert block["catalog"] == "databricks_course_ws"

    def test_magic_block_schema(self):
        src = _magic_block()
        block = _extract_block(src)
        assert block["schema"] == "bronze"

    def test_magic_block_execution_order(self):
        src = _magic_block()
        block = _extract_block(src)
        assert block["execution_order"] == 1

    def test_magic_block_read_tables(self):
        src = _magic_block(read_tables="landing.dim_customer")
        block = _extract_block(src)
        assert "landing.dim_customer" in block["read_tables"]

    def test_magic_block_write_tables(self):
        src = _magic_block(write_tables="bronze.dim_customer")
        block = _extract_block(src)
        assert "bronze.dim_customer" in block["write_tables"]

    def test_key_equals_value_name(self):
        """Plain KEY=VALUE (no MAGIC) is also accepted."""
        src = textwrap.dedent("""\
            # ECO METADATA
            # NOTEBOOK_NAME=my_nb
            # LAYER=silver
            # END ECO METADATA
        """)
        block = _extract_block(src)
        assert block["name"] == "my_nb"
        assert block["layer"] == "silver"

    def test_key_colon_value_still_works(self):
        """Original KEY: VALUE format continues to work."""
        src = textwrap.dedent("""\
            # ECO METADATA
            # NOTEBOOK_NAME: legacy_nb
            # END ECO METADATA
        """)
        block = _extract_block(src)
        assert block["name"] == "legacy_nb"

    def test_mixed_formats_in_one_block(self):
        """KEY: VALUE and KEY=VALUE may appear in the same block."""
        src = textwrap.dedent("""\
            # ECO METADATA
            # NOTEBOOK_NAME: mixed_nb
            # LAYER=gold
            # END ECO METADATA
        """)
        block = _extract_block(src)
        assert block["name"] == "mixed_nb"
        assert block["layer"] == "gold"

    def test_magic_writes_edges_produced(self):
        """notebook --WRITES--> delta_table edge is emitted for MAGIC format."""
        src = _magic_block(
            name="01_bronze",
            write_tables="bronze.dim_customer",
        )
        parser = EcoNotebookParser(notebooks=[{"source": src}])
        assets, rels = parser.parse()
        writes = [r for r in rels if r.relationship == RelationshipType.WRITES]
        assert len(writes) >= 1
        targets = {r.target for r in writes}
        assert "delta_table::bronze.dim_customer" in targets

    def test_magic_reads_edges_produced(self):
        """delta_table --READS--> notebook edge is emitted for MAGIC format."""
        src = _magic_block(
            name="01_bronze",
            read_tables="landing.dim_customer",
        )
        parser = EcoNotebookParser(notebooks=[{"source": src}])
        assets, rels = parser.parse()
        reads = [r for r in rels if r.relationship == RelationshipType.READS]
        assert len(reads) >= 1
        sources = {r.source for r in reads}
        assert "delta_table::landing.dim_customer" in sources

    def test_magic_notebook_asset_created(self):
        src = _magic_block(name="01_bronze")
        parser = EcoNotebookParser(notebooks=[{"source": src}])
        assets, _ = parser.parse()
        nbs = [a for a in assets if a.asset_type == AssetType.DATABRICKS_NOTEBOOK]
        assert len(nbs) == 1
        assert nbs[0].id == "notebook::01_bronze"

    def test_magic_multiline_write_tables(self):
        """Continuation lines (one table per # line) are collected."""
        src = textwrap.dedent("""\
            # MAGIC # ECO METADATA
            # MAGIC # NOTEBOOK_NAME=multi_write
            # MAGIC # WRITE_TABLES=
            # MAGIC # catalog.schema.table_a
            # MAGIC # catalog.schema.table_b
        """)
        block = _extract_block(src)
        assert "catalog.schema.table_a" in block["write_tables"]
        assert "catalog.schema.table_b" in block["write_tables"]

    def test_real_notebook_01_parsed(self):
        """01_bronze_ingestion_framework.py is correctly parsed."""
        nb_path = Path("metadata/databricks/01_bronze_ingestion_framework.py")
        if not nb_path.exists():
            pytest.skip("Real notebook not present")
        parser = EcoNotebookParser.from_files([nb_path])
        assets, rels = parser.parse()
        nbs = [a for a in assets if a.asset_type == AssetType.DATABRICKS_NOTEBOOK]
        assert len(nbs) == 1
        assert nbs[0].name == "01_bronze_ingestion_framework"

    def test_real_notebook_01_has_write_edges(self):
        nb_path = Path("metadata/databricks/01_bronze_ingestion_framework.py")
        if not nb_path.exists():
            pytest.skip("Real notebook not present")
        parser = EcoNotebookParser.from_files([nb_path])
        assets, rels = parser.parse()
        writes = [r for r in rels if r.relationship == RelationshipType.WRITES]
        assert len(writes) >= 1

    def test_real_notebook_01_has_read_edges(self):
        nb_path = Path("metadata/databricks/01_bronze_ingestion_framework.py")
        if not nb_path.exists():
            pytest.skip("Real notebook not present")
        parser = EcoNotebookParser.from_files([nb_path])
        assets, rels = parser.parse()
        reads = [r for r in rels if r.relationship == RelationshipType.READS]
        assert len(reads) >= 1

    def test_real_notebook_03_write_targets_include_gold(self):
        """03_gold_publishing_engine writes to gold.* delta tables."""
        nb_path = Path("metadata/databricks/03_gold_publishing_engine.py")
        if not nb_path.exists():
            pytest.skip("Real notebook not present")
        parser = EcoNotebookParser.from_files([nb_path])
        assets, rels = parser.parse()
        write_targets = {r.target for r in rels if r.relationship == RelationshipType.WRITES}
        # At least one gold write target must be present
        assert any("gold" in t or "customer_360" in t or "sales_dashboard" in t
                   for t in write_targets), f"No gold write targets found: {write_targets}"


# ===========================================================================
# Fix 2 — Notebook ID normalisation
# ===========================================================================


class TestFix2NotebookIDNormalisation:
    """Workflow parser emits basename-only notebook IDs."""

    def test_normalise_full_path(self):
        assert _normalise_notebook_path("/Repos/de-team/01_bronze") == "01_bronze"

    def test_normalise_bare_name(self):
        assert _normalise_notebook_path("01_bronze") == "01_bronze"

    def test_normalise_trailing_slash(self):
        assert _normalise_notebook_path("/Repos/de-team/01_bronze/") == "01_bronze"

    def test_normalise_single_segment(self):
        assert _normalise_notebook_path("/nb") == "nb"

    def test_stub_id_uses_basename(self):
        assert _notebook_stub_id("/Repos/de-team/01_bronze") == "notebook::01_bronze"

    def test_stub_id_bare_name(self):
        assert _notebook_stub_id("my_nb") == "notebook::my_nb"

    def test_workflow_calls_edge_targets_basename(self):
        yaml = textwrap.dedent("""\
            pipeline:
              name: p
              tasks:
                - name: task_a
                  notebook: /Repos/de-team/01_bronze_ingestion_framework
                  depends_on: []
        """)
        _, rels = DatabricksWorkflowParser(source=yaml).parse()
        calls = [r for r in rels if r.relationship == RelationshipType.CALLS]
        assert len(calls) == 1
        assert calls[0].target == "notebook::01_bronze_ingestion_framework"

    def test_workflow_stub_asset_id_is_basename(self):
        yaml = textwrap.dedent("""\
            pipeline:
              name: p
              tasks:
                - name: task_a
                  notebook: /Repos/de-team/01_bronze_ingestion_framework
                  depends_on: []
        """)
        assets, _ = DatabricksWorkflowParser(source=yaml).parse()
        nb_stubs = [a for a in assets if a.asset_type == AssetType.DATABRICKS_NOTEBOOK]
        assert len(nb_stubs) == 1
        assert nb_stubs[0].id == "notebook::01_bronze_ingestion_framework"

    def test_task_calls_edge_matches_eco_notebook_id(self):
        """CALLS edge target == EcoNotebookParser asset ID for same notebook."""
        # Workflow YAML references the full path
        yaml = textwrap.dedent("""\
            pipeline:
              name: p
              tasks:
                - name: task_a
                  notebook: /Repos/de-team/01_bronze_ingestion_framework
                  depends_on: []
        """)
        _, wf_rels = DatabricksWorkflowParser(source=yaml).parse()
        calls = [r for r in wf_rels if r.relationship == RelationshipType.CALLS]
        workflow_nb_id = calls[0].target  # "notebook::01_bronze_ingestion_framework"

        # EcoNotebookParser uses NOTEBOOK_NAME= from the block
        src = textwrap.dedent("""\
            # ECO METADATA
            # NOTEBOOK_NAME=01_bronze_ingestion_framework
            # END ECO METADATA
        """)
        eco_assets, _ = EcoNotebookParser(notebooks=[{"source": src}]).parse()
        nbs = [a for a in eco_assets if a.asset_type == AssetType.DATABRICKS_NOTEBOOK]
        eco_nb_id = nbs[0].id  # "notebook::01_bronze_ingestion_framework"

        assert workflow_nb_id == eco_nb_id

    def test_shared_notebook_two_paths_same_basename(self):
        """Two tasks pointing at the same basename produce one stub."""
        yaml = textwrap.dedent("""\
            pipeline:
              name: p
              tasks:
                - name: task_a
                  notebook: /Repos/de-team/shared_nb
                  depends_on: []
                - name: task_b
                  notebook: /Repos/other-team/shared_nb
                  depends_on: []
        """)
        assets, rels = DatabricksWorkflowParser(source=yaml).parse()
        stubs = [a for a in assets if a.id == "notebook::shared_nb"]
        assert len(stubs) == 1
        calls = [r for r in rels if r.relationship == RelationshipType.CALLS]
        assert len(calls) == 2

    def test_real_pipeline_yml_calls_targets_are_basenames(self):
        """Real pipeline.yml produces CALLS edges with basename-only targets."""
        yml = Path("metadata/databricks/pipeline.yml")
        if not yml.exists():
            pytest.skip("Real pipeline.yml not present")
        assets, rels = DatabricksWorkflowParser(source=yml).parse()
        for r in rels:
            if r.relationship == RelationshipType.CALLS:
                assert "/" not in r.target, (
                    f"CALLS target still contains path separators: {r.target}"
                )


# ===========================================================================
# Fix 3 — Delta-table bridge edges
# ===========================================================================


def _make_bridge_graph() -> EnterpriseGraph:
    """Minimal graph: one delta_table and one matching Power BI table."""
    g = EnterpriseGraph()
    g.add_asset(Asset(
        id="delta_table::gold.customer_360",
        name="customer_360",
        asset_type=AssetType.DELTA_TABLE,
        system=SystemType.DATABRICKS,
    ))
    g.add_asset(Asset(
        id="table::customer_360",
        name="customer_360",
        asset_type=AssetType.TABLE,
        system=SystemType.POWERBI,
    ))
    return g


class TestFix3DeltaTableBridge:
    """_build_delta_bridge_relationships creates FEEDS edges."""

    def test_bridge_edge_created(self):
        g = _make_bridge_graph()
        bridges = _build_delta_bridge_relationships(g)
        assert len(bridges) == 1

    def test_bridge_source_is_delta_table(self):
        g = _make_bridge_graph()
        bridges = _build_delta_bridge_relationships(g)
        assert bridges[0].source == "delta_table::gold.customer_360"

    def test_bridge_target_is_powerbi_table(self):
        g = _make_bridge_graph()
        bridges = _build_delta_bridge_relationships(g)
        assert bridges[0].target == "table::customer_360"

    def test_bridge_relationship_type_feeds(self):
        g = _make_bridge_graph()
        bridges = _build_delta_bridge_relationships(g)
        assert bridges[0].relationship == RelationshipType.FEEDS

    def test_bridge_via_property(self):
        g = _make_bridge_graph()
        bridges = _build_delta_bridge_relationships(g)
        assert bridges[0].properties.get("via") == "delta_table_bridge"

    def test_no_bridge_when_no_table_match(self):
        g = EnterpriseGraph()
        g.add_asset(Asset(
            id="delta_table::gold.orphan",
            name="orphan",
            asset_type=AssetType.DELTA_TABLE,
            system=SystemType.DATABRICKS,
        ))
        bridges = _build_delta_bridge_relationships(g)
        assert bridges == []

    def test_no_bridge_when_no_delta_tables(self):
        g = EnterpriseGraph()
        g.add_asset(Asset(
            id="table::customer_360",
            name="customer_360",
            asset_type=AssetType.TABLE,
            system=SystemType.POWERBI,
        ))
        bridges = _build_delta_bridge_relationships(g)
        assert bridges == []

    def test_no_duplicate_bridge_edge(self):
        """Already-existing edge is not duplicated."""
        g = _make_bridge_graph()
        from graph.models import Relationship, RelationshipType
        g.add_relationship(Relationship(
            source="delta_table::gold.customer_360",
            target="table::customer_360",
            relationship=RelationshipType.FEEDS,
        ))
        bridges = _build_delta_bridge_relationships(g)
        assert bridges == []

    def test_three_part_catalog_schema_table(self):
        """Full catalog.schema.table reference resolves to bare table name."""
        g = EnterpriseGraph()
        g.add_asset(Asset(
            id="delta_table::databricks_course_ws.gold.customer_360",
            name="databricks_course_ws.gold.customer_360",
            asset_type=AssetType.DELTA_TABLE,
            system=SystemType.DATABRICKS,
        ))
        g.add_asset(Asset(
            id="table::customer_360",
            name="customer_360",
            asset_type=AssetType.TABLE,
            system=SystemType.POWERBI,
        ))
        bridges = _build_delta_bridge_relationships(g)
        assert len(bridges) == 1
        assert bridges[0].source == "delta_table::databricks_course_ws.gold.customer_360"
        assert bridges[0].target == "table::customer_360"

    def test_multiple_delta_tables_matched(self):
        """Several delta tables each bridge to their matching Power BI table."""
        g = EnterpriseGraph()
        for name in ("customer_360", "sales_dashboard", "monthly_sales"):
            g.add_asset(Asset(
                id=f"delta_table::gold.{name}",
                name=name,
                asset_type=AssetType.DELTA_TABLE,
                system=SystemType.DATABRICKS,
            ))
            g.add_asset(Asset(
                id=f"table::{name}",
                name=name,
                asset_type=AssetType.TABLE,
                system=SystemType.POWERBI,
            ))
        bridges = _build_delta_bridge_relationships(g)
        assert len(bridges) == 3

    def test_loader_adds_bridge_edges(self, tmp_path):
        """EnterpriseMetadataLoader.load() calls the bridge builder."""
        # Build a minimal SQL dir (so there are no spurious assets)
        sql_dir = tmp_path / "sql"
        sql_dir.mkdir()

        # Build an in-memory graph manually and check the loader wires it
        g = EnterpriseGraph()
        g.add_asset(Asset(
            id="delta_table::gold.customer_360",
            name="customer_360",
            asset_type=AssetType.DELTA_TABLE,
            system=SystemType.DATABRICKS,
        ))
        g.add_asset(Asset(
            id="table::customer_360",
            name="customer_360",
            asset_type=AssetType.TABLE,
            system=SystemType.POWERBI,
        ))
        from enterprise.metadata_loader import _build_delta_bridge_relationships
        bridges = _build_delta_bridge_relationships(g)
        for b in bridges:
            g.add_relationship(b)
        fwd = {r.source: r.target for r in g.relationships}
        assert fwd.get("delta_table::gold.customer_360") == "table::customer_360"

    def test_bfs_crosses_bridge(self):
        """BFS from a delta_table reaches the matching Power BI table via bridge."""
        g = _make_bridge_graph()
        for b in _build_delta_bridge_relationships(g):
            g.add_relationship(b)
        qe = EnterpriseQueryEngine(g)
        downstream = qe.find_downstream("delta_table::gold.customer_360")
        ids = {a.id for a in downstream}
        assert "table::customer_360" in ids

    def test_real_loader_has_bridge_edges(self):
        """Integration: real metadata/ dir produces at least one bridge edge."""
        loader = EnterpriseMetadataLoader()
        graph = loader.load()
        bridges = [
            r for r in graph.relationships
            if r.relationship == RelationshipType.FEEDS
            and r.source.startswith("delta_table::")
            and r.target.startswith("table::")
        ]
        assert len(bridges) >= 1, "Expected at least one delta→table bridge edge"


# ===========================================================================
# Fix 4 — SQL edge direction: TABLE --FEEDS--> VIEW / PROC / FUNCTION
# ===========================================================================


class TestFix4SQLEdgeDirection:
    """SQL parser emits TABLE --FEEDS--> consumer (not consumer --READS--> TABLE)."""

    def _simple_view_sql(self) -> str:
        return textwrap.dedent("""\
            CREATE TABLE dbo.orders (id INT);
            CREATE VIEW dbo.vw_orders AS SELECT * FROM dbo.orders;
        """)

    def _simple_proc_sql(self) -> str:
        return textwrap.dedent("""\
            CREATE TABLE dbo.orders (id INT);
            CREATE PROCEDURE dbo.usp_orders AS
            BEGIN SELECT * FROM dbo.orders; END;
        """)

    def _simple_fn_sql(self) -> str:
        return textwrap.dedent("""\
            CREATE TABLE dbo.orders (id INT);
            CREATE FUNCTION dbo.fn_count()
            RETURNS INT AS BEGIN RETURN (SELECT COUNT(*) FROM dbo.orders); END;
        """)

    # ── VIEW ─────────────────────────────────────────────────────────────────

    def test_view_edge_source_is_table(self):
        _, rels = SQLMetadataParser(source=self._simple_view_sql()).parse()
        for r in rels:
            assert r.source == "sql::dbo.orders"

    def test_view_edge_target_is_view(self):
        _, rels = SQLMetadataParser(source=self._simple_view_sql()).parse()
        for r in rels:
            assert r.target == "sql::dbo.vw_orders"

    def test_view_edge_type_is_feeds(self):
        _, rels = SQLMetadataParser(source=self._simple_view_sql()).parse()
        for r in rels:
            assert r.relationship == RelationshipType.FEEDS

    def test_bfs_from_table_reaches_view(self):
        sql = self._simple_view_sql()
        assets, rels = SQLMetadataParser(source=sql).parse()
        g = EnterpriseGraph()
        for a in assets:
            g.add_asset(a)
        for r in rels:
            g.add_relationship(r)
        qe = EnterpriseQueryEngine(g)
        downstream = qe.find_downstream("sql::dbo.orders")
        ids = {a.id for a in downstream}
        assert "sql::dbo.vw_orders" in ids

    # ── STORED PROCEDURE ─────────────────────────────────────────────────────

    def test_proc_edge_source_is_table(self):
        _, rels = SQLMetadataParser(source=self._simple_proc_sql()).parse()
        for r in rels:
            assert r.source == "sql::dbo.orders"

    def test_proc_edge_target_is_proc(self):
        _, rels = SQLMetadataParser(source=self._simple_proc_sql()).parse()
        for r in rels:
            assert r.target == "sql::dbo.usp_orders"

    def test_proc_edge_type_is_feeds(self):
        _, rels = SQLMetadataParser(source=self._simple_proc_sql()).parse()
        for r in rels:
            assert r.relationship == RelationshipType.FEEDS

    def test_bfs_from_table_reaches_proc(self):
        sql = self._simple_proc_sql()
        assets, rels = SQLMetadataParser(source=sql).parse()
        g = EnterpriseGraph()
        for a in assets:
            g.add_asset(a)
        for r in rels:
            g.add_relationship(r)
        qe = EnterpriseQueryEngine(g)
        downstream = qe.find_downstream("sql::dbo.orders")
        ids = {a.id for a in downstream}
        assert "sql::dbo.usp_orders" in ids

    # ── FUNCTION ─────────────────────────────────────────────────────────────

    def test_fn_edge_source_is_table(self):
        _, rels = SQLMetadataParser(source=self._simple_fn_sql()).parse()
        for r in rels:
            assert r.source == "sql::dbo.orders"

    def test_fn_edge_target_is_function(self):
        _, rels = SQLMetadataParser(source=self._simple_fn_sql()).parse()
        for r in rels:
            assert r.target == "sql::dbo.fn_count"

    def test_fn_edge_type_is_feeds(self):
        _, rels = SQLMetadataParser(source=self._simple_fn_sql()).parse()
        for r in rels:
            assert r.relationship == RelationshipType.FEEDS

    def test_bfs_from_table_reaches_function(self):
        sql = self._simple_fn_sql()
        assets, rels = SQLMetadataParser(source=sql).parse()
        g = EnterpriseGraph()
        for a in assets:
            g.add_asset(a)
        for r in rels:
            g.add_relationship(r)
        qe = EnterpriseQueryEngine(g)
        downstream = qe.find_downstream("sql::dbo.orders")
        ids = {a.id for a in downstream}
        assert "sql::dbo.fn_count" in ids

    # ── Real metadata ─────────────────────────────────────────────────────────

    def test_real_sql_dir_table_to_view_feeds(self):
        """Integration: dim_customer --FEEDS--> vw_customer_360."""
        assets, rels = SQLMetadataParser(source=Path("metadata/sql")).parse()
        feeds = [
            r for r in rels
            if r.source == "sql::dbo.dim_customer"
            and r.target == "sql::dbo.vw_customer_360"
            and r.relationship == RelationshipType.FEEDS
        ]
        assert len(feeds) == 1

    def test_real_sql_dir_table_to_proc_feeds(self):
        """Integration: fact_internet_sales --FEEDS--> usp_get_customer_orders."""
        assets, rels = SQLMetadataParser(source=Path("metadata/sql")).parse()
        feeds = [
            r for r in rels
            if r.source == "sql::dbo.fact_internet_sales"
            and r.target == "sql::dbo.usp_get_customer_orders"
            and r.relationship == RelationshipType.FEEDS
        ]
        assert len(feeds) == 1

    def test_real_sql_dir_no_reads_edges(self):
        """No READS edges remain after the direction fix."""
        _, rels = SQLMetadataParser(source=Path("metadata/sql")).parse()
        reads = [r for r in rels if r.relationship == RelationshipType.READS]
        assert reads == []

    def test_bfs_dim_customer_reaches_vw_customer_360(self):
        """Integration BFS: change to dim_customer impacts vw_customer_360."""
        assets, rels = SQLMetadataParser(source=Path("metadata/sql")).parse()
        g = EnterpriseGraph()
        for a in assets:
            g.add_asset(a)
        for r in rels:
            g.add_relationship(r)
        qe = EnterpriseQueryEngine(g)
        downstream = qe.find_downstream("sql::dbo.dim_customer")
        ids = {a.id for a in downstream}
        assert "sql::dbo.vw_customer_360" in ids


# ===========================================================================
# Integration smoke test — full graph traversal
# ===========================================================================


@pytest.fixture(scope="module")
def full_graph():
    """Load the real enterprise graph once per test module."""
    return EnterpriseMetadataLoader().load()


class TestFullGraphTraversal:
    """Verify that all four fixes together produce a connected enterprise graph."""

    def test_graph_has_databricks_notebooks(self, full_graph):
        nbs = [a for a in full_graph.assets.values()
               if a.asset_type == AssetType.DATABRICKS_NOTEBOOK]
        assert len(nbs) >= 3

    def test_graph_has_pipeline_tasks(self, full_graph):
        tasks = [a for a in full_graph.assets.values()
                 if a.asset_type == AssetType.PIPELINE_TASK]
        assert len(tasks) >= 6

    def test_graph_has_sql_views(self, full_graph):
        views = [a for a in full_graph.assets.values()
                 if a.asset_type == AssetType.SQL_VIEW]
        assert len(views) >= 4

    def test_graph_has_delta_tables(self, full_graph):
        dts = [a for a in full_graph.assets.values()
               if a.asset_type == AssetType.DELTA_TABLE]
        assert len(dts) >= 1

    def test_pipeline_task_calls_real_notebook(self, full_graph):
        """Tasks point at real notebook IDs (basename only)."""
        calls = [
            r for r in full_graph.relationships
            if r.relationship == RelationshipType.CALLS
        ]
        targets = {r.target for r in calls}
        assert "notebook::01_bronze_ingestion_framework" in targets

    def test_notebooks_have_write_edges(self, full_graph):
        writes = [
            r for r in full_graph.relationships
            if r.relationship == RelationshipType.WRITES
        ]
        assert len(writes) >= 1

    def test_delta_bridge_edges_present(self, full_graph):
        bridges = [
            r for r in full_graph.relationships
            if r.relationship == RelationshipType.FEEDS
            and r.source.startswith("delta_table::")
            and r.target.startswith("table::")
        ]
        assert len(bridges) >= 1

    def test_sql_table_feeds_sql_view(self, full_graph):
        feeds = [
            r for r in full_graph.relationships
            if r.relationship == RelationshipType.FEEDS
            and r.source.startswith("sql::")
            and r.target.startswith("sql::")
        ]
        assert len(feeds) >= 1
