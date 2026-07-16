"""
tests.test_graph_validator
============================
Unit tests for :class:`enterprise.validator.GraphValidator`.

Covers all five checks:
  1. Cycles
  2. Broken references
  3. Duplicate IDs
  4. Dangling assets
  5. Disconnected subgraphs

Run with:
    python -m pytest tests/test_graph_validator.py -v
"""

from __future__ import annotations

import pytest

from enterprise.validator import GraphValidator, ValidationReport
from graph.enterprise_graph import EnterpriseGraph
from graph.models import Asset, AssetType, Relationship, RelationshipType, SystemType


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _asset(aid: str, name: str = "", atype=AssetType.TABLE, sys=SystemType.DATABASE) -> Asset:
    return Asset(id=aid, name=name or aid, asset_type=atype, system=sys)


def _rel(src: str, tgt: str, rtype=RelationshipType.READS) -> Relationship:
    return Relationship(source=src, target=tgt, relationship=rtype)


def _graph(*asset_ids, rels=None) -> EnterpriseGraph:
    g = EnterpriseGraph()
    for aid in asset_ids:
        g.add_asset(_asset(aid))
    for src, tgt in (rels or []):
        g.add_relationship(_rel(src, tgt))
    return g


# ---------------------------------------------------------------------------
# Check 1 — Cycles
# ---------------------------------------------------------------------------

class TestCycleDetection:

    def test_no_cycle_linear(self):
        g = _graph("a", "b", "c", rels=[("a", "b"), ("b", "c")])
        report = GraphValidator(g).validate()
        assert report.cycles == []

    def test_simple_cycle(self):
        # a → b → c → a
        g = _graph("a", "b", "c", rels=[("a", "b"), ("b", "c"), ("c", "a")])
        report = GraphValidator(g).validate()
        assert len(report.cycles) >= 1

    def test_self_loop(self):
        g = _graph("a", rels=[("a", "a")])
        report = GraphValidator(g).validate()
        assert len(report.cycles) >= 1

    def test_no_cycle_diamond(self):
        # a → b, a → c, b → d, c → d
        g = _graph("a", "b", "c", "d", rels=[("a","b"),("a","c"),("b","d"),("c","d")])
        report = GraphValidator(g).validate()
        assert report.cycles == []

    def test_empty_graph_no_cycles(self):
        g = EnterpriseGraph()
        report = GraphValidator(g).validate()
        assert report.cycles == []


# ---------------------------------------------------------------------------
# Check 2 — Broken references
# ---------------------------------------------------------------------------

class TestBrokenReferences:

    def test_valid_references(self):
        g = _graph("x", "y", rels=[("x", "y")])
        report = GraphValidator(g).validate()
        assert report.broken_references == []

    def test_missing_source(self):
        g = EnterpriseGraph()
        g.add_asset(_asset("y"))
        g.add_relationship(_rel("ghost", "y"))
        report = GraphValidator(g).validate()
        assert any("ghost" in w for w in report.broken_references)

    def test_missing_target(self):
        g = EnterpriseGraph()
        g.add_asset(_asset("x"))
        g.add_relationship(_rel("x", "ghost"))
        report = GraphValidator(g).validate()
        assert any("ghost" in w for w in report.broken_references)

    def test_unresolved_flag_suppresses_warning(self):
        """Relationships flagged unresolved=True are cross-source and excluded."""
        g = EnterpriseGraph()
        g.add_asset(_asset("x"))
        g.add_relationship(Relationship(
            source="x", target="delta::gold.revenue",
            relationship=RelationshipType.WRITES,
            properties={"unresolved": True},
        ))
        report = GraphValidator(g).validate()
        assert report.broken_references == []


# ---------------------------------------------------------------------------
# Check 3 — Duplicate IDs
# ---------------------------------------------------------------------------

class TestDuplicateIds:

    def test_no_duplicates(self):
        g = _graph("a", "b", "c")
        report = GraphValidator(g).validate()
        assert report.duplicate_ids == []

    def test_dict_already_deduplicates(self):
        """The EnterpriseGraph dict silently overwrites — validator shows no dupe."""
        g = EnterpriseGraph()
        g.add_asset(_asset("a", "first"))
        g.add_asset(_asset("a", "second"))   # overwrites silently
        report = GraphValidator(g).validate()
        assert report.duplicate_ids == []
        assert g.assets["a"].name == "second"


# ---------------------------------------------------------------------------
# Check 4 — Dangling assets
# ---------------------------------------------------------------------------

class TestDanglingAssets:

    def test_connected_assets_not_dangling(self):
        g = _graph("x", "y", rels=[("x", "y")])
        report = GraphValidator(g).validate()
        # x and y are both connected — neither should appear as dangling
        assert not any("x" in w for w in report.dangling_assets)
        assert not any("y" in w for w in report.dangling_assets)

    def test_isolated_asset_is_dangling(self):
        g = _graph("x", "y", "orphan", rels=[("x", "y")])
        report = GraphValidator(g).validate()
        assert any("orphan" in w for w in report.dangling_assets)

    def test_empty_graph_no_dangling(self):
        g = EnterpriseGraph()
        report = GraphValidator(g).validate()
        assert report.dangling_assets == []

    def test_single_connected_pair_no_dangling(self):
        g = _graph("a", "b", rels=[("a", "b")])
        report = GraphValidator(g).validate()
        assert report.dangling_assets == []


# ---------------------------------------------------------------------------
# Check 5 — Disconnected subgraphs
# ---------------------------------------------------------------------------

class TestDisconnectedSubgraphs:

    def test_single_component_no_warning(self):
        g = _graph("a", "b", "c", rels=[("a", "b"), ("b", "c")])
        report = GraphValidator(g).validate()
        assert report.disconnected_subgraphs == []

    def test_two_components_warning(self):
        g = _graph("a", "b", "c", "d", rels=[("a", "b"), ("c", "d")])
        report = GraphValidator(g).validate()
        assert len(report.disconnected_subgraphs) == 1

    def test_three_components(self):
        g = _graph("a", "b", "c", "d", "e", "f",
                   rels=[("a", "b"), ("c", "d"), ("e", "f")])
        report = GraphValidator(g).validate()
        # Two non-main components
        assert len(report.disconnected_subgraphs) == 2

    def test_empty_graph_no_warning(self):
        g = EnterpriseGraph()
        report = GraphValidator(g).validate()
        assert report.disconnected_subgraphs == []


# ---------------------------------------------------------------------------
# ValidationReport helpers
# ---------------------------------------------------------------------------

class TestValidationReport:

    def test_has_warnings_true(self):
        r = ValidationReport(cycles=["Cycle: a → a"])
        assert r.has_warnings is True

    def test_has_warnings_false(self):
        r = ValidationReport()
        assert r.has_warnings is False

    def test_warnings_flat_list(self):
        r = ValidationReport(
            cycles=["c1"],
            broken_references=["b1", "b2"],
            dangling_assets=["d1"],
        )
        assert len(r.warnings) == 4

    def test_summary_format(self):
        r = ValidationReport(
            cycles=["c1"],
            broken_references=["b1"],
        )
        s = r.summary()
        assert "cycles=1" in s
        assert "broken_refs=1" in s
