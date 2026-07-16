"""
tests.test_metadata_loader
==========================
Comprehensive test suite for :class:`~enterprise.metadata_loader.EnterpriseMetadataLoader`.

Coverage
--------
1.  Importable from enterprise package
2.  Default construction (no args) — returns valid EnterpriseGraph
3.  Empty metadata directory — returns empty graph (no crash)
4.  load() always returns EnterpriseGraph
5.  Power BI only — load_databricks=False, load_sql=False, load_adls=False
6.  Databricks only — load_powerbi=False, load_sql=False, load_adls=False
7.  SQL only — load_powerbi=False, load_databricks=False, load_adls=False
8.  ADLS only — load_powerbi=False, load_databricks=False, load_sql=False
9.  All sources together — integration smoke-test with real metadata/
10. Duplicate asset IDs → first-wins, graph keeps one copy
11. Duplicate edges → deduplicated, graph keeps one edge
12. Missing Databricks directory → contributes zero assets
13. Missing SQL directory → contributes zero assets
14. Missing ADLS CSV → contributes zero assets
15. Missing Power BI paths → contributes zero assets
16. load_powerbi=False disables Power BI source
17. load_databricks=False disables all Databricks sources
18. load_sql=False disables SQL source
19. load_adls=False disables ADLS source
20. Custom sql_dir Path → SQL assets loaded from that directory
21. Custom adls_csv Path → ADLS assets loaded from that file
22. Custom databricks_dir Path → notebooks + workflow from that directory
23. All sources disabled → returns empty graph
24. graph.assets is a dict keyed by asset id
25. graph.relationships is a list
26. Graph get_asset() works for loaded assets
27. Graph get_downstream() works for loaded edges
28. SQL assets present when sql_dir has tables.sql
29. ADLS assets present when valid adls_inventory.csv supplied
30. Databricks notebook assets present when *.py files supplied
31. Databricks workflow assets present when pipeline.yml supplied
32. No duplicate asset IDs in merged graph
33. No duplicate edges in merged graph
34. _build_graph static method returns EnterpriseGraph
35. _build_graph with empty lists returns empty graph
36. _build_graph deduplicates assets correctly
37. _build_graph deduplicates relationships correctly
38. Parser exception does not crash load()
39. EnterpriseMetadataLoader importable from enterprise package (__init__)
"""

from __future__ import annotations

import textwrap
from pathlib import Path
from typing import List
from unittest.mock import MagicMock, patch

import pytest

from enterprise.metadata_loader import EnterpriseMetadataLoader
from graph.enterprise_graph import EnterpriseGraph
from graph.models import Asset, AssetType, Criticality, Relationship, RelationshipType, SystemType


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_asset(asset_id: str, name: str = "test", asset_type: AssetType = AssetType.DATABASE_TABLE) -> Asset:
    """Build a minimal Asset for test use."""
    return Asset(
        id=asset_id,
        name=name,
        asset_type=asset_type,
        system=SystemType.DATABASE,
    )


def _make_rel(src: str, tgt: str, rel: RelationshipType = RelationshipType.READS) -> Relationship:
    """Build a minimal Relationship for test use."""
    return Relationship(source=src, target=tgt, relationship=rel)


# ---------------------------------------------------------------------------
# Import / construction
# ---------------------------------------------------------------------------


class TestImportAndConstruct:
    def test_importable_from_module(self):
        from enterprise.metadata_loader import EnterpriseMetadataLoader as EML  # noqa: PLC0415
        assert EML is not None

    def test_importable_from_enterprise_package(self):
        from enterprise import EnterpriseMetadataLoader as EML  # noqa: PLC0415
        assert EML is not None

    def test_default_construction(self):
        loader = EnterpriseMetadataLoader()
        assert loader is not None

    def test_custom_paths_accepted(self, tmp_path):
        loader = EnterpriseMetadataLoader(
            databricks_dir=tmp_path,
            sql_dir=tmp_path,
            adls_csv=tmp_path / "adls.csv",
        )
        assert loader is not None

    def test_load_flags_stored(self):
        loader = EnterpriseMetadataLoader(
            load_powerbi=False,
            load_databricks=False,
            load_sql=False,
            load_adls=False,
        )
        assert loader._load_powerbi is False
        assert loader._load_databricks is False
        assert loader._load_sql is False
        assert loader._load_adls is False


# ---------------------------------------------------------------------------
# _build_graph static method — unit tests
# ---------------------------------------------------------------------------


class TestBuildGraph:
    def test_returns_enterprise_graph(self):
        graph = EnterpriseMetadataLoader._build_graph([], [])
        assert isinstance(graph, EnterpriseGraph)

    def test_empty_inputs_empty_graph(self):
        graph = EnterpriseMetadataLoader._build_graph([], [])
        assert graph.assets == {}
        assert graph.relationships == []

    def test_single_asset_added(self):
        a = _make_asset("sql::test")
        graph = EnterpriseMetadataLoader._build_graph([a], [])
        assert "sql::test" in graph.assets

    def test_multiple_assets_added(self):
        assets = [_make_asset(f"sql::t{i}") for i in range(5)]
        graph = EnterpriseMetadataLoader._build_graph(assets, [])
        assert len(graph.assets) == 5

    def test_duplicate_asset_first_wins(self):
        a1 = Asset(
            id="sql::orders",
            name="orders_first",
            asset_type=AssetType.DATABASE_TABLE,
            system=SystemType.DATABASE,
        )
        a2 = Asset(
            id="sql::orders",
            name="orders_second",
            asset_type=AssetType.DATABASE_TABLE,
            system=SystemType.DATABASE,
        )
        graph = EnterpriseMetadataLoader._build_graph([a1, a2], [])
        assert len(graph.assets) == 1
        assert graph.assets["sql::orders"].name == "orders_first"

    def test_duplicate_assets_three_copies(self):
        assets = [_make_asset("sql::orders", name=f"v{i}") for i in range(3)]
        graph = EnterpriseMetadataLoader._build_graph(assets, [])
        assert len(graph.assets) == 1

    def test_single_relationship_added(self):
        a1 = _make_asset("sql::a")
        a2 = _make_asset("sql::b")
        r = _make_rel("sql::a", "sql::b")
        graph = EnterpriseMetadataLoader._build_graph([a1, a2], [r])
        assert len(graph.relationships) == 1

    def test_multiple_relationships_added(self):
        rels = [_make_rel(f"sql::a{i}", f"sql::b{i}") for i in range(4)]
        graph = EnterpriseMetadataLoader._build_graph([], rels)
        assert len(graph.relationships) == 4

    def test_duplicate_edge_deduplicated(self):
        r1 = _make_rel("sql::a", "sql::b", RelationshipType.READS)
        r2 = _make_rel("sql::a", "sql::b", RelationshipType.READS)
        graph = EnterpriseMetadataLoader._build_graph([], [r1, r2])
        assert len(graph.relationships) == 1

    def test_duplicate_edge_three_copies(self):
        rels = [_make_rel("sql::a", "sql::b") for _ in range(5)]
        graph = EnterpriseMetadataLoader._build_graph([], rels)
        assert len(graph.relationships) == 1

    def test_same_nodes_different_rel_type_kept(self):
        r1 = _make_rel("sql::a", "sql::b", RelationshipType.READS)
        r2 = _make_rel("sql::a", "sql::b", RelationshipType.WRITES)
        graph = EnterpriseMetadataLoader._build_graph([], [r1, r2])
        assert len(graph.relationships) == 2

    def test_same_rel_type_different_direction_kept(self):
        r1 = _make_rel("sql::a", "sql::b", RelationshipType.READS)
        r2 = _make_rel("sql::b", "sql::a", RelationshipType.READS)
        graph = EnterpriseMetadataLoader._build_graph([], [r1, r2])
        assert len(graph.relationships) == 2

    def test_get_asset_works(self):
        a = _make_asset("sql::orders")
        graph = EnterpriseMetadataLoader._build_graph([a], [])
        assert graph.get_asset("sql::orders") is a

    def test_get_downstream_works(self):
        r = _make_rel("sql::a", "sql::b")
        graph = EnterpriseMetadataLoader._build_graph([], [r])
        assert "sql::b" in graph.get_downstream("sql::a")


# ---------------------------------------------------------------------------
# Source isolation — verify each flag disables the correct source
# ---------------------------------------------------------------------------


class TestSourceIsolation:
    """Test each load_* flag by patching the individual _load_* methods."""

    def _make_loader(self, **kwargs) -> EnterpriseMetadataLoader:
        return EnterpriseMetadataLoader(
            load_powerbi=False,
            load_databricks=False,
            load_sql=False,
            load_adls=False,
            **kwargs,
        )

    def test_load_returns_enterprise_graph(self):
        loader = self._make_loader()
        graph = loader.load()
        assert isinstance(graph, EnterpriseGraph)

    def test_all_disabled_returns_empty_graph(self):
        loader = self._make_loader()
        graph = loader.load()
        assert graph.assets == {}
        assert graph.relationships == []

    def test_powerbi_flag_enables_powerbi(self):
        loader = EnterpriseMetadataLoader(
            load_powerbi=True,
            load_databricks=False,
            load_sql=False,
            load_adls=False,
        )
        with patch.object(loader, "_load_powerbi_source", return_value=([], [])) as mock:
            loader.load()
            mock.assert_called_once()

    def test_powerbi_flag_disabled_skips_powerbi(self):
        loader = EnterpriseMetadataLoader(
            load_powerbi=False,
            load_databricks=False,
            load_sql=False,
            load_adls=False,
        )
        with patch.object(loader, "_load_powerbi_source", return_value=([], [])) as mock:
            loader.load()
            mock.assert_not_called()

    def test_databricks_flag_enables_notebooks_and_workflow(self):
        loader = EnterpriseMetadataLoader(
            load_powerbi=False,
            load_databricks=True,
            load_sql=False,
            load_adls=False,
        )
        with (
            patch.object(loader, "_load_eco_notebooks", return_value=([], [])) as nb_mock,
            patch.object(loader, "_load_databricks_workflow", return_value=([], [])) as wf_mock,
        ):
            loader.load()
            nb_mock.assert_called_once()
            wf_mock.assert_called_once()

    def test_databricks_flag_disabled_skips_both(self):
        loader = EnterpriseMetadataLoader(
            load_powerbi=False,
            load_databricks=False,
            load_sql=False,
            load_adls=False,
        )
        with (
            patch.object(loader, "_load_eco_notebooks", return_value=([], [])) as nb_mock,
            patch.object(loader, "_load_databricks_workflow", return_value=([], [])) as wf_mock,
        ):
            loader.load()
            nb_mock.assert_not_called()
            wf_mock.assert_not_called()

    def test_sql_flag_enables_sql(self):
        loader = EnterpriseMetadataLoader(
            load_powerbi=False,
            load_databricks=False,
            load_sql=True,
            load_adls=False,
        )
        with patch.object(loader, "_load_sql_source", return_value=([], [])) as mock:
            loader.load()
            mock.assert_called_once()

    def test_sql_flag_disabled_skips_sql(self):
        loader = EnterpriseMetadataLoader(
            load_powerbi=False,
            load_databricks=False,
            load_sql=False,
            load_adls=False,
        )
        with patch.object(loader, "_load_sql_source", return_value=([], [])) as mock:
            loader.load()
            mock.assert_not_called()

    def test_adls_flag_enables_adls(self):
        loader = EnterpriseMetadataLoader(
            load_powerbi=False,
            load_databricks=False,
            load_sql=False,
            load_adls=True,
        )
        with patch.object(loader, "_load_adls_source", return_value=([], [])) as mock:
            loader.load()
            mock.assert_called_once()

    def test_adls_flag_disabled_skips_adls(self):
        loader = EnterpriseMetadataLoader(
            load_powerbi=False,
            load_databricks=False,
            load_sql=False,
            load_adls=False,
        )
        with patch.object(loader, "_load_adls_source", return_value=([], [])) as mock:
            loader.load()
            mock.assert_not_called()


# ---------------------------------------------------------------------------
# Missing paths — never raise, contribute zero assets
# ---------------------------------------------------------------------------


class TestMissingPaths:
    def test_missing_sql_dir_zero_assets(self, tmp_path):
        loader = EnterpriseMetadataLoader(
            load_powerbi=False,
            load_databricks=False,
            load_sql=True,
            load_adls=False,
            sql_dir=tmp_path / "nonexistent_sql",
        )
        graph = loader.load()
        assert graph.assets == {}
        assert graph.relationships == []

    def test_missing_adls_csv_zero_assets(self, tmp_path):
        loader = EnterpriseMetadataLoader(
            load_powerbi=False,
            load_databricks=False,
            load_sql=False,
            load_adls=True,
            adls_csv=tmp_path / "missing.csv",
        )
        graph = loader.load()
        assert graph.assets == {}

    def test_missing_databricks_dir_zero_assets(self, tmp_path):
        loader = EnterpriseMetadataLoader(
            load_powerbi=False,
            load_databricks=True,
            load_sql=False,
            load_adls=False,
            databricks_dir=tmp_path / "nonexistent_db",
        )
        graph = loader.load()
        assert graph.assets == {}

    def test_missing_powerbi_paths_zero_assets(self, tmp_path):
        loader = EnterpriseMetadataLoader(
            load_powerbi=True,
            load_databricks=False,
            load_sql=False,
            load_adls=False,
            powerbi_semantic_model_path=tmp_path / "NoModel",
            powerbi_report_path=tmp_path / "NoReport",
        )
        graph = loader.load()
        assert graph.assets == {}

    def test_empty_databricks_dir_zero_assets(self, tmp_path):
        """An existing but empty directory contributes zero assets."""
        loader = EnterpriseMetadataLoader(
            load_powerbi=False,
            load_databricks=True,
            load_sql=False,
            load_adls=False,
            databricks_dir=tmp_path,
        )
        graph = loader.load()
        assert graph.assets == {}

    def test_empty_sql_dir_zero_assets(self, tmp_path):
        loader = EnterpriseMetadataLoader(
            load_powerbi=False,
            load_databricks=False,
            load_sql=True,
            load_adls=False,
            sql_dir=tmp_path,
        )
        graph = loader.load()
        assert graph.assets == {}


# ---------------------------------------------------------------------------
# SQL source — with a real tmp directory
# ---------------------------------------------------------------------------


class TestSQLSource:
    def _make_sql_dir(self, tmp_path: Path) -> Path:
        sql_dir = tmp_path / "sql"
        sql_dir.mkdir()
        (sql_dir / "tables.sql").write_text(
            "CREATE TABLE dbo.orders (id INT NOT NULL);\n"
            "CREATE TABLE dbo.customers (id INT NOT NULL);\n",
            encoding="utf-8",
        )
        (sql_dir / "views.sql").write_text(
            "CREATE VIEW dbo.vw_orders AS\n"
            "SELECT * FROM dbo.orders;\n",
            encoding="utf-8",
        )
        return sql_dir

    def test_sql_assets_present(self, tmp_path):
        sql_dir = self._make_sql_dir(tmp_path)
        loader = EnterpriseMetadataLoader(
            load_powerbi=False, load_databricks=False,
            load_sql=True, load_adls=False,
            sql_dir=sql_dir,
        )
        graph = loader.load()
        assert len(graph.assets) > 0

    def test_sql_tables_present(self, tmp_path):
        sql_dir = self._make_sql_dir(tmp_path)
        loader = EnterpriseMetadataLoader(
            load_powerbi=False, load_databricks=False,
            load_sql=True, load_adls=False,
            sql_dir=sql_dir,
        )
        graph = loader.load()
        tables = [a for a in graph.assets.values() if a.asset_type == AssetType.DATABASE_TABLE]
        assert len(tables) == 2

    def test_sql_view_present(self, tmp_path):
        sql_dir = self._make_sql_dir(tmp_path)
        loader = EnterpriseMetadataLoader(
            load_powerbi=False, load_databricks=False,
            load_sql=True, load_adls=False,
            sql_dir=sql_dir,
        )
        graph = loader.load()
        views = [a for a in graph.assets.values() if a.asset_type == AssetType.SQL_VIEW]
        assert len(views) == 1

    def test_sql_reads_edge_present(self, tmp_path):
        # Edges are now FEEDS (TABLE→VIEW), not READS (VIEW→TABLE)
        sql_dir = self._make_sql_dir(tmp_path)
        loader = EnterpriseMetadataLoader(
            load_powerbi=False, load_databricks=False,
            load_sql=True, load_adls=False,
            sql_dir=sql_dir,
        )
        graph = loader.load()
        feeds = [r for r in graph.relationships if r.relationship == RelationshipType.FEEDS]
        assert len(feeds) == 1

    def test_no_duplicate_asset_ids(self, tmp_path):
        sql_dir = self._make_sql_dir(tmp_path)
        loader = EnterpriseMetadataLoader(
            load_powerbi=False, load_databricks=False,
            load_sql=True, load_adls=False,
            sql_dir=sql_dir,
        )
        graph = loader.load()
        ids = list(graph.assets.keys())
        assert len(ids) == len(set(ids))


# ---------------------------------------------------------------------------
# ADLS source
# ---------------------------------------------------------------------------


ADLS_CSV = textwrap.dedent("""\
    FileName,Container,Folder,Format,Description,BronzeTable
    DimCustomer.parquet,landing,/landing,parquet,Customer data,bronze.dim_customer
    DimProduct.parquet,landing,/landing,parquet,Product data,bronze.dim_product
""")


class TestADLSSource:
    def _make_adls_csv(self, tmp_path: Path) -> Path:
        csv_file = tmp_path / "adls_inventory.csv"
        csv_file.write_text(ADLS_CSV, encoding="utf-8")
        return csv_file

    def test_adls_assets_present(self, tmp_path):
        adls_csv = self._make_adls_csv(tmp_path)
        loader = EnterpriseMetadataLoader(
            load_powerbi=False, load_databricks=False,
            load_sql=False, load_adls=True,
            adls_csv=adls_csv,
        )
        graph = loader.load()
        adls_files = [a for a in graph.assets.values() if a.asset_type == AssetType.ADLS_FILE]
        assert len(adls_files) == 2

    def test_adls_delta_stubs_present(self, tmp_path):
        adls_csv = self._make_adls_csv(tmp_path)
        loader = EnterpriseMetadataLoader(
            load_powerbi=False, load_databricks=False,
            load_sql=False, load_adls=True,
            adls_csv=adls_csv,
        )
        graph = loader.load()
        stubs = [a for a in graph.assets.values() if a.asset_type == AssetType.DELTA_TABLE]
        assert len(stubs) == 2

    def test_adls_ingests_to_edges(self, tmp_path):
        adls_csv = self._make_adls_csv(tmp_path)
        loader = EnterpriseMetadataLoader(
            load_powerbi=False, load_databricks=False,
            load_sql=False, load_adls=True,
            adls_csv=adls_csv,
        )
        graph = loader.load()
        edges = [r for r in graph.relationships if r.relationship == RelationshipType.INGESTS_TO]
        assert len(edges) == 2

    def test_adls_empty_csv_zero_assets(self, tmp_path):
        csv_file = tmp_path / "empty.csv"
        csv_file.write_text("FileName,Container,Folder,Format,Description,BronzeTable\n", encoding="utf-8")
        loader = EnterpriseMetadataLoader(
            load_powerbi=False, load_databricks=False,
            load_sql=False, load_adls=True,
            adls_csv=csv_file,
        )
        graph = loader.load()
        assert graph.assets == {}


# ---------------------------------------------------------------------------
# Databricks source
# ---------------------------------------------------------------------------

_ECO_NOTEBOOK = textwrap.dedent("""\
    # Databricks notebook source
    # MAGIC %md
    # ECO METADATA
    # NOTEBOOK_NAME: test_notebook
    # LAYER: bronze
    # READ_TABLES: landing.dim_customer
    # WRITE_TABLES: bronze.dim_customer
    # END ECO METADATA
    spark.read.table("landing.dim_customer")
""")

_PIPELINE_YML = textwrap.dedent("""\
    pipeline:
      name: test_pipeline
      tasks:
        - name: ingest_customer
          execution_order: 1
          notebook: /Repos/de-team/test_notebook
""")


class TestDatabricksSource:
    def _make_databricks_dir(self, tmp_path: Path) -> Path:
        db_dir = tmp_path / "databricks"
        db_dir.mkdir()
        (db_dir / "test_notebook.py").write_text(_ECO_NOTEBOOK, encoding="utf-8")
        (db_dir / "pipeline.yml").write_text(_PIPELINE_YML, encoding="utf-8")
        return db_dir

    def test_notebook_asset_present(self, tmp_path):
        db_dir = self._make_databricks_dir(tmp_path)
        loader = EnterpriseMetadataLoader(
            load_powerbi=False, load_databricks=True,
            load_sql=False, load_adls=False,
            databricks_dir=db_dir,
        )
        graph = loader.load()
        notebooks = [
            a for a in graph.assets.values()
            if a.asset_type == AssetType.DATABRICKS_NOTEBOOK
        ]
        assert len(notebooks) >= 1

    def test_pipeline_asset_present(self, tmp_path):
        db_dir = self._make_databricks_dir(tmp_path)
        loader = EnterpriseMetadataLoader(
            load_powerbi=False, load_databricks=True,
            load_sql=False, load_adls=False,
            databricks_dir=db_dir,
        )
        graph = loader.load()
        pipelines = [
            a for a in graph.assets.values()
            if a.asset_type == AssetType.PIPELINE
        ]
        assert len(pipelines) == 1

    def test_pipeline_task_present(self, tmp_path):
        db_dir = self._make_databricks_dir(tmp_path)
        loader = EnterpriseMetadataLoader(
            load_powerbi=False, load_databricks=True,
            load_sql=False, load_adls=False,
            databricks_dir=db_dir,
        )
        graph = loader.load()
        tasks = [
            a for a in graph.assets.values()
            if a.asset_type == AssetType.PIPELINE_TASK
        ]
        assert len(tasks) == 1

    def test_pipeline_triggers_edge(self, tmp_path):
        db_dir = self._make_databricks_dir(tmp_path)
        loader = EnterpriseMetadataLoader(
            load_powerbi=False, load_databricks=True,
            load_sql=False, load_adls=False,
            databricks_dir=db_dir,
        )
        graph = loader.load()
        triggers = [r for r in graph.relationships if r.relationship == RelationshipType.TRIGGERS]
        assert len(triggers) >= 1

    def test_notebook_reads_edge(self, tmp_path):
        db_dir = self._make_databricks_dir(tmp_path)
        loader = EnterpriseMetadataLoader(
            load_powerbi=False, load_databricks=True,
            load_sql=False, load_adls=False,
            databricks_dir=db_dir,
        )
        graph = loader.load()
        reads = [r for r in graph.relationships if r.relationship == RelationshipType.READS]
        assert len(reads) >= 1

    def test_notebook_writes_edge(self, tmp_path):
        db_dir = self._make_databricks_dir(tmp_path)
        loader = EnterpriseMetadataLoader(
            load_powerbi=False, load_databricks=True,
            load_sql=False, load_adls=False,
            databricks_dir=db_dir,
        )
        graph = loader.load()
        writes = [r for r in graph.relationships if r.relationship == RelationshipType.WRITES]
        assert len(writes) >= 1

    def test_no_py_files_zero_eco_notebook_assets(self, tmp_path):
        """No .py notebooks → EcoNotebookParser emits nothing.
        (Workflow stubs from pipeline.yml are separate and are still emitted.)
        """
        db_dir = tmp_path / "db"
        db_dir.mkdir()
        # Only pipeline.yml, no .py notebooks
        (db_dir / "pipeline.yml").write_text(_PIPELINE_YML, encoding="utf-8")
        loader = EnterpriseMetadataLoader(
            load_powerbi=False, load_databricks=True,
            load_sql=False, load_adls=False,
            databricks_dir=db_dir,
        )
        graph = loader.load()
        # Non-stub DATABRICKS_NOTEBOOK assets come from EcoNotebookParser
        # Workflow parser emits stubs with metadata["stub"]==True
        real_notebooks = [
            a for a in graph.assets.values()
            if a.asset_type == AssetType.DATABRICKS_NOTEBOOK
            and not a.metadata.get("stub")
        ]
        assert real_notebooks == []

    def test_no_pipeline_yml_zero_pipeline_assets(self, tmp_path):
        db_dir = tmp_path / "db"
        db_dir.mkdir()
        # Only notebook, no pipeline.yml
        (db_dir / "test_notebook.py").write_text(_ECO_NOTEBOOK, encoding="utf-8")
        loader = EnterpriseMetadataLoader(
            load_powerbi=False, load_databricks=True,
            load_sql=False, load_adls=False,
            databricks_dir=db_dir,
        )
        graph = loader.load()
        pipelines = [a for a in graph.assets.values() if a.asset_type == AssetType.PIPELINE]
        assert pipelines == []


# ---------------------------------------------------------------------------
# Duplicate handling in combined load
# ---------------------------------------------------------------------------


class TestDuplicateHandling:
    def test_duplicate_assets_across_sources(self):
        """Two parsers that emit the same asset ID → graph keeps one."""
        a1 = _make_asset("sql::dbo.orders", "orders")
        a2 = _make_asset("sql::dbo.orders", "orders_dup")
        graph = EnterpriseMetadataLoader._build_graph([a1, a2], [])
        assert len(graph.assets) == 1
        assert graph.assets["sql::dbo.orders"].name == "orders"

    def test_duplicate_edges_across_sources(self):
        """Two parsers that emit the same edge → graph keeps one."""
        r1 = _make_rel("sql::a", "sql::b", RelationshipType.READS)
        r2 = _make_rel("sql::a", "sql::b", RelationshipType.READS)
        graph = EnterpriseMetadataLoader._build_graph([], [r1, r2])
        assert len(graph.relationships) == 1

    def test_mixed_duplicates(self):
        """3 assets (2 dupes) + 4 edges (3 dupes) → 2 assets + 2 edges."""
        assets = [
            _make_asset("sql::a"),
            _make_asset("sql::b"),
            _make_asset("sql::a"),  # dup
        ]
        rels = [
            _make_rel("sql::a", "sql::b", RelationshipType.READS),
            _make_rel("sql::a", "sql::b", RelationshipType.READS),  # dup
            _make_rel("sql::a", "sql::b", RelationshipType.WRITES),
            _make_rel("sql::a", "sql::b", RelationshipType.WRITES),  # dup
        ]
        graph = EnterpriseMetadataLoader._build_graph(assets, rels)
        assert len(graph.assets) == 2
        assert len(graph.relationships) == 2


# ---------------------------------------------------------------------------
# Parser exception resilience
# ---------------------------------------------------------------------------


class TestParserExceptionResilience:
    def test_sql_source_exception_does_not_crash(self):
        loader = EnterpriseMetadataLoader(
            load_powerbi=False, load_databricks=False,
            load_sql=True, load_adls=False,
        )
        with patch.object(
            loader, "_load_sql_source", side_effect=RuntimeError("boom")
        ):
            # load() catches exception internally, _load_sql_source is called
            # directly in load() — we verify load() itself doesn't reraise
            # by calling it in a try/except wrapper and asserting no raise.
            try:
                loader.load()
            except RuntimeError:
                pytest.fail("load() must not propagate parser exceptions")

    def test_adls_source_exception_does_not_crash(self):
        loader = EnterpriseMetadataLoader(
            load_powerbi=False, load_databricks=False,
            load_sql=False, load_adls=True,
        )
        with patch.object(
            loader, "_load_adls_source", side_effect=RuntimeError("adls boom")
        ):
            try:
                loader.load()
            except RuntimeError:
                pytest.fail("load() must not propagate ADLS parser exceptions")

    def test_notebook_source_exception_does_not_crash(self):
        loader = EnterpriseMetadataLoader(
            load_powerbi=False, load_databricks=True,
            load_sql=False, load_adls=False,
        )
        with patch.object(
            loader, "_load_eco_notebooks", side_effect=RuntimeError("nb boom")
        ):
            try:
                loader.load()
            except RuntimeError:
                pytest.fail("load() must not propagate notebook parser exceptions")


# ---------------------------------------------------------------------------
# Integration smoke-tests against real metadata/ directory
# ---------------------------------------------------------------------------


class TestRealMetadataIntegration:
    """Load from the actual project metadata directories.

    These tests assert shape (non-zero, correct types) rather than exact counts
    so they stay robust as the metadata files evolve.
    """

    @pytest.fixture(autouse=True)
    def _skip_if_no_metadata(self):
        if not Path("metadata").exists():
            pytest.skip("metadata/ directory not found")

    def test_sql_only_produces_assets(self):
        sql_dir = Path("metadata") / "sql"
        if not sql_dir.exists():
            pytest.skip("metadata/sql/ not found")
        loader = EnterpriseMetadataLoader(
            load_powerbi=False, load_databricks=False,
            load_sql=True, load_adls=False,
            sql_dir=sql_dir,
        )
        graph = loader.load()
        assert len(graph.assets) > 0

    def test_adls_only_produces_assets(self):
        adls_csv = Path("metadata") / "adls" / "adls_inventory.csv"
        if not adls_csv.exists():
            pytest.skip("adls_inventory.csv not found")
        loader = EnterpriseMetadataLoader(
            load_powerbi=False, load_databricks=False,
            load_sql=False, load_adls=True,
            adls_csv=adls_csv,
        )
        graph = loader.load()
        assert len(graph.assets) > 0

    def test_databricks_only_produces_assets(self):
        db_dir = Path("metadata") / "databricks"
        if not db_dir.exists():
            pytest.skip("metadata/databricks/ not found")
        loader = EnterpriseMetadataLoader(
            load_powerbi=False, load_databricks=True,
            load_sql=False, load_adls=False,
            databricks_dir=db_dir,
        )
        graph = loader.load()
        assert len(graph.assets) > 0

    def test_all_sources_returns_graph(self):
        """Full loader with all real sources — just verify it runs and returns a graph."""
        loader = EnterpriseMetadataLoader(load_powerbi=False)
        graph = loader.load()
        assert isinstance(graph, EnterpriseGraph)

    def test_all_sources_no_duplicate_ids(self):
        loader = EnterpriseMetadataLoader(load_powerbi=False)
        graph = loader.load()
        ids = list(graph.assets.keys())
        assert len(ids) == len(set(ids)), "Duplicate asset IDs found in merged graph"

    def test_all_sources_no_duplicate_edges(self):
        loader = EnterpriseMetadataLoader(load_powerbi=False)
        graph = loader.load()
        edge_keys = [
            (r.source, r.target, r.relationship.value) for r in graph.relationships
        ]
        assert len(edge_keys) == len(set(edge_keys)), "Duplicate edges found in merged graph"

    def test_all_sources_sql_tables_present(self):
        sql_dir = Path("metadata") / "sql"
        if not sql_dir.exists():
            pytest.skip("metadata/sql/ not found")
        loader = EnterpriseMetadataLoader(load_powerbi=False)
        graph = loader.load()
        tables = [a for a in graph.assets.values() if a.asset_type == AssetType.DATABASE_TABLE]
        assert len(tables) >= 5  # dim_customer, dim_product, dim_date, dim_territory, fact_sales

    def test_all_sources_adls_files_present(self):
        adls_csv = Path("metadata") / "adls" / "adls_inventory.csv"
        if not adls_csv.exists():
            pytest.skip("adls_inventory.csv not found")
        loader = EnterpriseMetadataLoader(load_powerbi=False)
        graph = loader.load()
        adls_assets = [a for a in graph.assets.values() if a.asset_type == AssetType.ADLS_FILE]
        assert len(adls_assets) >= 1

    def test_all_sources_pipeline_present(self):
        pipeline_yml = Path("metadata") / "databricks" / "pipeline.yml"
        if not pipeline_yml.exists():
            pytest.skip("pipeline.yml not found")
        loader = EnterpriseMetadataLoader(load_powerbi=False)
        graph = loader.load()
        pipelines = [a for a in graph.assets.values() if a.asset_type == AssetType.PIPELINE]
        assert len(pipelines) >= 1

    def test_all_sources_graph_has_relationships(self):
        loader = EnterpriseMetadataLoader(load_powerbi=False)
        graph = loader.load()
        assert len(graph.relationships) > 0
