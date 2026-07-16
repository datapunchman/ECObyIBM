"""
tests.test_enterprise_metrics
================================
Unit tests for :class:`enterprise.metrics.EnterpriseGraphMetrics`.

Covers:
  - assets_by_system
  - assets_by_type
  - dependency_depth
  - critical_path
  - blast_radius
  - top_connected_assets
  - leaf_assets
  - orphan_assets
  - parsers: SQLParser, NotebookParser, PipelineParser, registry

Run with:
    python -m pytest tests/test_enterprise_metrics.py -v
"""

from __future__ import annotations

import pytest

from enterprise.metrics import EnterpriseGraphMetrics
from enterprise.parsers import NotebookParser, PipelineParser, PowerBIParser, SQLParser
from enterprise.registry import EnterpriseAssetRegistry
from graph.enterprise_graph import EnterpriseGraph
from graph.models import Asset, AssetType, Relationship, RelationshipType, SystemType


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _asset(aid, atype=AssetType.TABLE, sys=SystemType.DATABASE) -> Asset:
    return Asset(id=aid, name=aid, asset_type=atype, system=sys)

def _rel(src, tgt, rtype=RelationshipType.READS) -> Relationship:
    return Relationship(source=src, target=tgt, relationship=rtype)

def _linear_graph() -> EnterpriseGraph:
    """a → b → c → d  (linear chain, depth 3 from a)"""
    g = EnterpriseGraph()
    for aid in ("a", "b", "c", "d"):
        g.add_asset(_asset(aid))
    for src, tgt in [("a","b"),("b","c"),("c","d")]:
        g.add_relationship(_rel(src, tgt))
    return g

def _diamond_graph() -> EnterpriseGraph:
    """a → b, a → c, b → d, c → d"""
    g = EnterpriseGraph()
    for aid in ("a","b","c","d"):
        g.add_asset(_asset(aid))
    for src, tgt in [("a","b"),("a","c"),("b","d"),("c","d")]:
        g.add_relationship(_rel(src, tgt))
    return g

def _multi_system_graph() -> EnterpriseGraph:
    g = EnterpriseGraph()
    g.add_asset(_asset("db_tbl", AssetType.DATABASE_TABLE, SystemType.DATABASE))
    g.add_asset(_asset("spark_nb", AssetType.DATABRICKS_NOTEBOOK, SystemType.DATABRICKS))
    g.add_asset(_asset("pbi_rpt", AssetType.REPORT, SystemType.POWERBI))
    g.add_relationship(_rel("db_tbl", "spark_nb"))
    g.add_relationship(_rel("spark_nb", "pbi_rpt"))
    return g


# ---------------------------------------------------------------------------
# assets_by_system
# ---------------------------------------------------------------------------

class TestAssetsBySystem:

    def test_single_system(self):
        g = _linear_graph()
        m = EnterpriseGraphMetrics(g).compute()
        assert m["assets_by_system"].get("database", 0) == 4

    def test_multi_system(self):
        g = _multi_system_graph()
        m = EnterpriseGraphMetrics(g).compute()
        assert m["assets_by_system"]["database"] == 1
        assert m["assets_by_system"]["databricks"] == 1
        assert m["assets_by_system"]["powerbi"] == 1

    def test_empty_systems_excluded(self):
        g = _linear_graph()
        m = EnterpriseGraphMetrics(g).compute()
        # No powerbi assets — key should be absent or 0
        assert m["assets_by_system"].get("powerbi", 0) == 0


# ---------------------------------------------------------------------------
# assets_by_type
# ---------------------------------------------------------------------------

class TestAssetsByType:

    def test_counts_correct(self):
        g = EnterpriseGraph()
        g.add_asset(_asset("t1", AssetType.DATABASE_TABLE))
        g.add_asset(_asset("t2", AssetType.DATABASE_TABLE))
        g.add_asset(_asset("nb", AssetType.DATABRICKS_NOTEBOOK, SystemType.DATABRICKS))
        m = EnterpriseGraphMetrics(g).compute()
        assert m["assets_by_type"]["database_table"] == 2
        assert m["assets_by_type"]["databricks_notebook"] == 1


# ---------------------------------------------------------------------------
# dependency_depth
# ---------------------------------------------------------------------------

class TestDependencyDepth:

    def test_linear_depth(self):
        g = _linear_graph()
        m = EnterpriseGraphMetrics(g).compute()
        depths = m["dependency_depth"]
        assert depths["a"] == 3   # a → b → c → d
        assert depths["b"] == 2
        assert depths["c"] == 1
        assert depths["d"] == 0   # leaf

    def test_diamond_depth(self):
        g = _diamond_graph()
        m = EnterpriseGraphMetrics(g).compute()
        depths = m["dependency_depth"]
        assert depths["a"] == 2   # a → b → d  or  a → c → d
        assert depths["d"] == 0

    def test_isolated_node_depth_zero(self):
        g = EnterpriseGraph()
        g.add_asset(_asset("lone"))
        m = EnterpriseGraphMetrics(g).compute()
        assert m["dependency_depth"]["lone"] == 0


# ---------------------------------------------------------------------------
# critical_path
# ---------------------------------------------------------------------------

class TestCriticalPath:

    def test_linear_critical_path(self):
        g = _linear_graph()
        m = EnterpriseGraphMetrics(g).compute()
        path = m["critical_path"]
        assert path[0] == "a"
        assert path[-1] == "d"
        assert len(path) == 4

    def test_empty_graph(self):
        g = EnterpriseGraph()
        m = EnterpriseGraphMetrics(g).compute()
        assert m["critical_path"] == []


# ---------------------------------------------------------------------------
# blast_radius
# ---------------------------------------------------------------------------

class TestBlastRadius:

    def test_linear_blast_radius(self):
        g = _linear_graph()
        m = EnterpriseGraphMetrics(g).compute()
        br = m["blast_radius"]
        assert br["a"] == 3   # reaches b, c, d
        assert br["b"] == 2   # reaches c, d
        assert br["d"] == 0   # leaf — no downstream

    def test_diamond_blast_radius(self):
        g = _diamond_graph()
        m = EnterpriseGraphMetrics(g).compute()
        br = m["blast_radius"]
        assert br["a"] == 3   # b, c, d
        assert br["d"] == 0


# ---------------------------------------------------------------------------
# top_connected_assets
# ---------------------------------------------------------------------------

class TestTopConnectedAssets:

    def test_returns_list_of_dicts(self):
        g = _linear_graph()
        m = EnterpriseGraphMetrics(g).compute()
        top = m["top_connected_assets"]
        assert isinstance(top, list)
        assert all(isinstance(x, dict) for x in top)
        assert all("degree" in x for x in top)

    def test_highest_degree_first(self):
        g = _diamond_graph()
        m = EnterpriseGraphMetrics(g).compute()
        top = m["top_connected_assets"]
        # d has 2 incoming, no outgoing = degree 2
        # a has 2 outgoing, no incoming = degree 2
        # b has 1 in + 1 out = degree 2
        degrees = [x["degree"] for x in top]
        assert degrees == sorted(degrees, reverse=True)

    def test_max_ten_returned(self):
        g = EnterpriseGraph()
        for i in range(20):
            g.add_asset(_asset(f"node_{i}"))
        m = EnterpriseGraphMetrics(g).compute()
        assert len(m["top_connected_assets"]) <= 10


# ---------------------------------------------------------------------------
# leaf_assets
# ---------------------------------------------------------------------------

class TestLeafAssets:

    def test_linear_leaf(self):
        g = _linear_graph()
        m = EnterpriseGraphMetrics(g).compute()
        assert "d" in m["leaf_assets"]
        assert "a" not in m["leaf_assets"]

    def test_diamond_leaf(self):
        g = _diamond_graph()
        m = EnterpriseGraphMetrics(g).compute()
        assert "d" in m["leaf_assets"]


# ---------------------------------------------------------------------------
# orphan_assets
# ---------------------------------------------------------------------------

class TestOrphanAssets:

    def test_orphan_detected(self):
        g = _graph_with_orphan()
        m = EnterpriseGraphMetrics(g).compute()
        assert "orphan" in m["orphan_assets"]

    def test_no_orphans_in_connected_graph(self):
        g = _linear_graph()
        m = EnterpriseGraphMetrics(g).compute()
        assert m["orphan_assets"] == []

def _graph_with_orphan() -> EnterpriseGraph:
    g = _linear_graph()
    g.add_asset(_asset("orphan"))
    return g


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------

class TestSQLParser:

    def test_produces_assets_and_relationships(self):
        parser = SQLParser([
            {"type": "table", "schema": "dbo", "name": "Orders",
             "columns": [{"name": "OrderID", "type": "int", "is_pk": True}],
             "reads": []},
        ])
        assets, rels = parser.parse()
        asset_ids = [a.id for a in assets]
        assert "sql::dbo.Orders" in asset_ids
        assert any("OrderID" in aid for aid in asset_ids)
        # Column CONTAINS table
        assert any(r.relationship == RelationshipType.CONTAINS for r in rels)

    def test_system_is_database(self):
        parser = SQLParser([{"type": "table", "schema": "dbo", "name": "T"}])
        assets, _ = parser.parse()
        assert all(a.system == SystemType.DATABASE for a in assets)

    def test_view_type(self):
        parser = SQLParser([{"type": "view", "schema": "dbo", "name": "V"}])
        assets, _ = parser.parse()
        assert assets[0].asset_type == AssetType.SQL_VIEW

    def test_reads_relationship_unresolved(self):
        parser = SQLParser([{
            "type": "view", "schema": "dbo", "name": "V",
            "reads": ["dbo.Orders"],
        }])
        _, rels = parser.parse()
        reads_rels = [r for r in rels if r.relationship == RelationshipType.READS]
        assert len(reads_rels) == 1
        assert reads_rels[0].properties.get("unresolved")


class TestNotebookParser:

    def test_produces_notebook_asset(self):
        parser = NotebookParser([{
            "name": "etl_notebook",
            "path": "/Repos/team/etl_notebook",
            "reads": ["sql::dbo.Orders"],
            "writes": ["delta::gold.revenue"],
        }])
        assets, rels = parser.parse()
        assert assets[0].asset_type == AssetType.DATABRICKS_NOTEBOOK
        assert assets[0].system == SystemType.DATABRICKS
        assert any(r.relationship == RelationshipType.READS for r in rels)
        assert any(r.relationship == RelationshipType.WRITES for r in rels)


class TestPipelineParser:

    def test_adf_pipeline(self):
        parser = PipelineParser([{
            "name": "pl_load", "type": "adf",
            "reads": ["sql::dbo.Orders"], "writes": ["delta::gold.rev"],
        }])
        assets, rels = parser.parse()
        assert assets[0].asset_type == AssetType.ADF_PIPELINE
        assert assets[0].system == SystemType.ADF

    def test_airflow_dag(self):
        parser = PipelineParser([{"name": "dag_daily", "type": "airflow"}])
        assets, _ = parser.parse()
        assert assets[0].asset_type == AssetType.AIRFLOW_DAG
        assert assets[0].system == SystemType.AIRFLOW

    def test_fabric_pipeline(self):
        parser = PipelineParser([{"name": "fab_pl", "type": "fabric"}])
        assets, _ = parser.parse()
        assert assets[0].asset_type == AssetType.FABRIC_PIPELINE
        assert assets[0].system == SystemType.FABRIC


class TestEnterpriseAssetRegistry:

    def test_multi_parser_merge(self):
        sql  = SQLParser([{"type": "table", "schema": "dbo", "name": "Orders"}])
        nb   = NotebookParser([{"name": "etl", "reads": ["sql::dbo.Orders"], "writes": []}])
        pl   = PipelineParser([{"name": "pl_load", "type": "adf", "calls": ["notebook::etl"]}])

        registry = EnterpriseAssetRegistry()
        registry.register(sql).register(nb).register(pl)
        graph = registry.build()

        assert "sql::dbo.Orders" in graph.assets
        assert "notebook::etl" in graph.assets
        assert "pipeline::pl_load" in graph.assets

    def test_duplicate_id_first_wins(self):
        sql1 = SQLParser([{"type": "table", "schema": "dbo", "name": "X"}])
        sql2 = SQLParser([{"type": "view",  "schema": "dbo", "name": "X"}])
        graph = EnterpriseAssetRegistry([sql1, sql2]).build()
        # First parser's asset_type wins
        assert graph.assets["sql::dbo.X"].asset_type == AssetType.DATABASE_TABLE

    def test_empty_registry_returns_empty_graph(self):
        graph = EnterpriseAssetRegistry().build()
        assert len(graph.assets) == 0
