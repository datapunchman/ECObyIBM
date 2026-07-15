"""
tests.test_metadata_adapter
============================
Unit tests for :class:`graph.adapter.MetadataAdapter.to_enterprise_graph`.

Covers:
    ✓  MetadataPayload input  — existing typed model passes through unchanged
    ✓  dict input             — raw API dict is coerced via MetadataPayload.model_validate()
    ✗  invalid input          — any other type raises ValueError("Unsupported metadata type")

These tests are self-contained: they build minimal in-memory fixtures and
make no network calls, file I/O, or assumptions about the running environment.

Run with:
    python -m pytest tests/test_metadata_adapter.py -v
"""

from __future__ import annotations

import pytest

from graph.adapter import MetadataAdapter
from graph.enterprise_graph import EnterpriseGraph
from graph.models import AssetType, RelationshipType, SystemType
from metadata.models import (
    ColumnMetadata,
    MeasureMetadata,
    MetadataPayload,
    RelationshipMetadata,
    ReportMetadata,
    TableMetadata,
)


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

def _make_payload() -> MetadataPayload:
    """Return a minimal but structurally complete ``MetadataPayload``."""
    table = TableMetadata(
        name="sales",
        source_type="import",
        is_hidden=False,
        is_date_table=False,
    )
    column = ColumnMetadata(
        table_name="sales",
        name="Revenue",
        data_type="double",
    )
    measure = MeasureMetadata(
        table_name="sales",
        name="Total Revenue",
        expression="SUM(sales[Revenue])",
        referenced_tables=["sales"],
        referenced_columns=["sales[Revenue]"],
        referenced_measures=[],
    )
    relationship = RelationshipMetadata(
        relationship_id="rel-001",
        from_table="sales",
        from_column="CustomerID",
        to_table="customers",
        to_column="ID",
        is_active=True,
    )
    report = ReportMetadata(
        page_name="page_001",
        display_name="Sales Overview",
        used_measures=["Total Revenue"],
        used_tables=["sales"],
    )
    return MetadataPayload(
        tables=[table],
        columns=[column],
        measures=[measure],
        relationships=[relationship],
        reports=[report],
    )


def _assert_graph_shape(graph: EnterpriseGraph) -> None:
    """Assert the graph contains the assets and relationship types we expect
    from the minimal fixture above."""
    asset_ids = set(graph.assets)

    # Table
    assert "table::sales" in asset_ids, "Expected table::sales asset"
    assert graph.assets["table::sales"].asset_type == AssetType.TABLE
    assert graph.assets["table::sales"].system == SystemType.POWERBI

    # Column
    assert "column::sales::Revenue" in asset_ids, "Expected column::sales::Revenue asset"
    assert graph.assets["column::sales::Revenue"].asset_type == AssetType.COLUMN

    # Measure
    assert "measure::Total Revenue" in asset_ids, "Expected measure::Total Revenue asset"
    assert graph.assets["measure::Total Revenue"].asset_type == AssetType.MEASURE

    # Report
    assert "report::page_001" in asset_ids, "Expected report::page_001 asset"
    assert graph.assets["report::page_001"].asset_type == AssetType.REPORT

    # At least one DEPENDS_ON relationship (column → table or measure → table)
    rel_types = {r.relationship for r in graph.relationships}
    assert RelationshipType.DEPENDS_ON in rel_types, "Expected at least one DEPENDS_ON relationship"

    # DISPLAYS relationship from report to measure
    assert RelationshipType.DISPLAYS in rel_types, "Expected DISPLAYS relationship from report"

    # REFERENCES relationship from the model relationship
    assert RelationshipType.REFERENCES in rel_types, "Expected REFERENCES relationship"


# ---------------------------------------------------------------------------
# Test 1 — MetadataPayload input (pre-existing / v1 path)
# ---------------------------------------------------------------------------


class TestMetadataPayloadInput:
    """``to_enterprise_graph`` called with a typed ``MetadataPayload`` instance.

    This is the pre-existing v1 path: ``MetadataEngine.load()`` builds a
    ``MetadataPayload`` in memory and passes it directly to the adapter.
    """

    def test_returns_enterprise_graph(self) -> None:
        payload = _make_payload()
        graph = MetadataAdapter.to_enterprise_graph(payload)
        assert isinstance(graph, EnterpriseGraph)

    def test_graph_shape_is_correct(self) -> None:
        payload = _make_payload()
        graph = MetadataAdapter.to_enterprise_graph(payload)
        _assert_graph_shape(graph)

    def test_payload_is_not_mutated(self) -> None:
        payload = _make_payload()
        original_table_count = len(payload.tables)
        MetadataAdapter.to_enterprise_graph(payload)
        assert len(payload.tables) == original_table_count, "Adapter must not mutate payload"

    def test_empty_payload_produces_empty_graph(self) -> None:
        empty = MetadataPayload()
        graph = MetadataAdapter.to_enterprise_graph(empty)
        assert isinstance(graph, EnterpriseGraph)
        assert len(graph.assets) == 0
        assert len(graph.relationships) == 0


# ---------------------------------------------------------------------------
# Test 2 — dict input (v2 path — the bug scenario)
# ---------------------------------------------------------------------------


class TestDictInput:
    """``to_enterprise_graph`` called with a plain ``dict``.

    This is the v2 path: ``MetadataClient.fetch_enterprise_graph()`` calls
    ``GET /metadata`` and passes the raw JSON body (a ``dict``) straight into
    the adapter — which previously crashed with
    ``AttributeError: 'dict' object has no attribute 'tables'``.
    """

    def test_returns_enterprise_graph(self) -> None:
        raw = _make_payload().model_dump()
        graph = MetadataAdapter.to_enterprise_graph(raw)
        assert isinstance(graph, EnterpriseGraph)

    def test_graph_shape_matches_payload_path(self) -> None:
        """Dict and MetadataPayload inputs must produce identical graphs."""
        payload = _make_payload()
        raw = payload.model_dump()

        graph_from_payload = MetadataAdapter.to_enterprise_graph(payload)
        graph_from_dict = MetadataAdapter.to_enterprise_graph(raw)

        assert set(graph_from_dict.assets) == set(graph_from_payload.assets), (
            "Asset IDs must be identical regardless of input type"
        )
        # Compare relationship (source, target, type) triples
        def _rel_triples(g: EnterpriseGraph):
            return {(r.source, r.target, r.relationship) for r in g.relationships}

        assert _rel_triples(graph_from_dict) == _rel_triples(graph_from_payload), (
            "Relationships must be identical regardless of input type"
        )

    def test_empty_dict_produces_empty_graph(self) -> None:
        graph = MetadataAdapter.to_enterprise_graph({})
        assert len(graph.assets) == 0
        assert len(graph.relationships) == 0

    def test_dict_with_only_tables_key(self) -> None:
        raw = {
            "tables": [
                {"name": "orders", "source_type": "import"}
            ]
        }
        graph = MetadataAdapter.to_enterprise_graph(raw)
        assert "table::orders" in graph.assets


# ---------------------------------------------------------------------------
# Test 3 — invalid input (defensive validation)
# ---------------------------------------------------------------------------


class TestInvalidInput:
    """``to_enterprise_graph`` must raise ``ValueError`` for unsupported types."""

    @pytest.mark.parametrize("bad_input", [
        None,
        42,
        3.14,
        ["tables"],
        ("tables", "columns"),
        object(),
    ])
    def test_raises_value_error(self, bad_input) -> None:
        with pytest.raises(ValueError, match="Unsupported metadata type"):
            MetadataAdapter.to_enterprise_graph(bad_input)  # type: ignore[arg-type]
