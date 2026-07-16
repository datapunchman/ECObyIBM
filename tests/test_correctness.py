"""
tests.test_correctness
=======================
Regression tests for the five correctness bugs fixed in the final pass.

Bug 1  — Change-type misclassification
    ``"Delete CustomerID"`` was classified as TABLE_DELETE because the
    ``_RE_TABLE_DELETE`` regex anchors with ``$`` and matches bare
    ``delete X``.  After resolution the change_type is now corrected to
    COLUMN_DELETE when the resolved asset is a column.

Bug 2  — System classification for import-mode tables/columns
    All TMDL tables/columns used to receive ``system=POWERBI``.
    Import-mode tables (``source_type == "m"``) are backed by a real
    database query and must receive ``system=DATABASE``.  Non-import
    tables (``calculated``, ``None``) still get ``system=POWERBI``.

Bug 3  — Unknown asset handling
    When no matching asset exists the ``summary`` field must contain a
    meaningful human-readable message (not an empty string).

Bug 4  — Multi-word new_name truncation
    ``"Rename UnicornTable to dragon table"`` previously yielded
    ``new_name="dragon"`` (single word) because all rename patterns used
    ``\\w+`` for the replacement name.  The fix uses ``[\\w][\\w\\s]*``
    so the full phrase is captured.

Bug 5  — Asset resolution priority
    When multiple candidates share the same normalised name (e.g.
    ``CustomerID`` appears in two tables), the resolver should prefer the
    candidate whose ``asset_type`` matches the operation semantics
    (COLUMN_DELETE → prefer COLUMN, not TABLE).

Run with:
    python -m pytest tests/test_correctness.py -v
"""

from __future__ import annotations

import pytest

from change.analyzer import EnterpriseChangeAnalyzer, _correct_change_type
from change.models import ChangeRequest, ChangeType
from graph.adapter import MetadataAdapter
from graph.enterprise_graph import EnterpriseGraph
from graph.models import Asset, AssetType, Relationship, RelationshipType, SystemType
from metadata.models import (
    ColumnMetadata,
    MetadataPayload,
    TableMetadata,
)


# ---------------------------------------------------------------------------
# Shared graph fixture
# ---------------------------------------------------------------------------

def _build_graph() -> EnterpriseGraph:
    """Return a realistic graph that exercises every bug scenario.

    Asset inventory
    ---------------
    Tables (system=POWERBI for testing purposes):
        customer_360, monthly_sales, territory_performance

    Columns:
        customer_360::CustomerID
        customer_360::FullName
        monthly_sales::Revenue
        monthly_sales::Profit
        territory_performance::CustomerID   ← same name as above

    Measures:
        Total Revenue
    """
    g = EnterpriseGraph()

    for tbl in ("customer_360", "monthly_sales", "territory_performance"):
        g.add_asset(Asset(
            id=f"table::{tbl}",
            name=tbl,
            asset_type=AssetType.TABLE,
            system=SystemType.POWERBI,
        ))

    for tbl, col in [
        ("customer_360",          "CustomerID"),
        ("customer_360",          "FullName"),
        ("monthly_sales",         "Revenue"),
        ("monthly_sales",         "Profit"),
        ("territory_performance", "CustomerID"),
    ]:
        g.add_asset(Asset(
            id=f"column::{tbl}::{col}",
            name=col,
            asset_type=AssetType.COLUMN,
            system=SystemType.POWERBI,
            properties={"table_name": tbl},
        ))
        g.add_relationship(Relationship(
            source=f"column::{tbl}::{col}",
            target=f"table::{tbl}",
            relationship=RelationshipType.DEPENDS_ON,
        ))

    g.add_asset(Asset(
        id="measure::Total Revenue",
        name="Total Revenue",
        asset_type=AssetType.MEASURE,
        system=SystemType.POWERBI,
    ))
    g.add_relationship(Relationship(
        source="measure::Total Revenue",
        target="table::monthly_sales",
        relationship=RelationshipType.DEPENDS_ON,
    ))

    return g


# ---------------------------------------------------------------------------
# Bug 1 — Change-type correction after asset resolution
# ---------------------------------------------------------------------------

class TestBug1ChangeTypeCorrection:
    """After resolution, TABLE_DELETE is corrected to COLUMN_DELETE when the
    resolved asset is a column (and vice-versa)."""

    @pytest.fixture(autouse=True)
    def _setup(self):
        self.analyzer = EnterpriseChangeAnalyzer(_build_graph())

    def test_delete_column_without_keyword_is_column_delete(self):
        """``"Delete CustomerID"`` — no ``column`` keyword → parser sees TABLE_DELETE
        but asset is a column → must be corrected to COLUMN_DELETE."""
        result = self.analyzer.analyze("Delete CustomerID")
        assert result.change_request.change_type == ChangeType.COLUMN_DELETE, (
            f"Expected COLUMN_DELETE, got {result.change_request.change_type}"
        )

    def test_delete_table_without_keyword_stays_table_delete(self):
        """``"Delete customer_360"`` resolves to a TABLE asset → TABLE_DELETE."""
        result = self.analyzer.analyze("Delete customer_360")
        assert result.change_request.change_type == ChangeType.TABLE_DELETE

    def test_explicit_column_delete_stays_column_delete(self):
        """``"Delete CustomerID column"`` — explicit keyword → no correction needed."""
        result = self.analyzer.analyze("Delete CustomerID column")
        assert result.change_request.change_type == ChangeType.COLUMN_DELETE

    def test_rename_without_keyword_corrected_to_column_rename(self):
        """``"Rename CustomerID to ClientID"`` resolves to a COLUMN asset → COLUMN_RENAME."""
        result = self.analyzer.analyze("Rename CustomerID to ClientID")
        assert result.change_request.change_type == ChangeType.COLUMN_RENAME


class TestCorrectChangeTypeHelper:
    """Unit tests for the ``_correct_change_type`` module-level function."""

    def _column_asset(self, name: str = "CustomerID") -> Asset:
        return Asset(
            id=f"column::customer_360::{name}",
            name=name,
            asset_type=AssetType.COLUMN,
            system=SystemType.POWERBI,
        )

    def _table_asset(self, name: str = "customer_360") -> Asset:
        return Asset(
            id=f"table::{name}",
            name=name,
            asset_type=AssetType.TABLE,
            system=SystemType.POWERBI,
        )

    def _req(self, ct: ChangeType) -> ChangeRequest:
        return ChangeRequest(
            original_request="dummy",
            change_type=ct,
            target_name="CustomerID",
        )

    # TABLE_DELETE + column asset → COLUMN_DELETE
    def test_table_delete_resolved_to_column(self):
        corrected = _correct_change_type(self._req(ChangeType.TABLE_DELETE), self._column_asset())
        assert corrected.change_type == ChangeType.COLUMN_DELETE

    # TABLE_RENAME + column asset → COLUMN_RENAME
    def test_table_rename_resolved_to_column(self):
        corrected = _correct_change_type(self._req(ChangeType.TABLE_RENAME), self._column_asset())
        assert corrected.change_type == ChangeType.COLUMN_RENAME

    # COLUMN_DELETE + table asset → TABLE_DELETE
    def test_column_delete_resolved_to_table(self):
        corrected = _correct_change_type(self._req(ChangeType.COLUMN_DELETE), self._table_asset())
        assert corrected.change_type == ChangeType.TABLE_DELETE

    # COLUMN_RENAME + table asset → TABLE_RENAME
    def test_column_rename_resolved_to_table(self):
        corrected = _correct_change_type(self._req(ChangeType.COLUMN_RENAME), self._table_asset())
        assert corrected.change_type == ChangeType.TABLE_RENAME

    # No correction needed — type already matches asset
    def test_column_delete_on_column_unchanged(self):
        req = self._req(ChangeType.COLUMN_DELETE)
        corrected = _correct_change_type(req, self._column_asset())
        assert corrected.change_type == ChangeType.COLUMN_DELETE

    def test_table_delete_on_table_unchanged(self):
        req = self._req(ChangeType.TABLE_DELETE)
        corrected = _correct_change_type(req, self._table_asset())
        assert corrected.change_type == ChangeType.TABLE_DELETE

    def test_other_change_types_are_not_modified(self):
        """VIEW_CHANGE, UNKNOWN, etc. must pass through unchanged."""
        for ct in (ChangeType.VIEW_CHANGE, ChangeType.UNKNOWN, ChangeType.COLUMN_ADD):
            req = self._req(ct)
            assert _correct_change_type(req, self._column_asset()).change_type == ct
            assert _correct_change_type(req, self._table_asset()).change_type == ct

    def test_original_request_preserved_after_correction(self):
        """The original_request field is preserved when change_type is corrected."""
        req = ChangeRequest(
            original_request="Delete CustomerID",
            change_type=ChangeType.TABLE_DELETE,
            target_name="CustomerID",
        )
        corrected = _correct_change_type(req, self._column_asset())
        assert corrected.original_request == "Delete CustomerID"
        assert corrected.target_name == "CustomerID"


# ---------------------------------------------------------------------------
# Bug 2 — System classification: import-mode tables → DATABASE
# ---------------------------------------------------------------------------

class TestBug2SystemClassification:
    """MetadataAdapter assigns system=DATABASE for import-mode tables (source_type='m')."""

    def _make_payload(
        self,
        table_name: str,
        source_type: str,
    ) -> MetadataPayload:
        return MetadataPayload(
            tables=[TableMetadata(name=table_name, source_type=source_type)],
            columns=[ColumnMetadata(table_name=table_name, name="SomeCol", data_type="string")],
            measures=[],
            relationships=[],
            reports=[],
        )

    def test_import_mode_table_is_database(self):
        """source_type='m' (import mode) → system=DATABASE."""
        payload = self._make_payload("customer_360", "m")
        graph = MetadataAdapter.to_enterprise_graph(payload)
        asset = graph.assets["table::customer_360"]
        assert asset.system == SystemType.DATABASE, (
            f"Expected DATABASE, got {asset.system}"
        )

    def test_import_mode_column_inherits_database(self):
        """Column under an import-mode table inherits system=DATABASE."""
        payload = self._make_payload("customer_360", "m")
        graph = MetadataAdapter.to_enterprise_graph(payload)
        col = graph.assets["column::customer_360::SomeCol"]
        assert col.system == SystemType.DATABASE

    def test_calculated_table_is_powerbi(self):
        """source_type='calculated' → system=POWERBI."""
        payload = self._make_payload("CalcTable", "calculated")
        graph = MetadataAdapter.to_enterprise_graph(payload)
        assert graph.assets["table::CalcTable"].system == SystemType.POWERBI

    def test_unknown_source_type_is_powerbi(self):
        """source_type='unknown' (default) → system=POWERBI."""
        payload = self._make_payload("SomeTable", "unknown")
        graph = MetadataAdapter.to_enterprise_graph(payload)
        assert graph.assets["table::SomeTable"].system == SystemType.POWERBI

    def test_none_source_type_is_powerbi(self):
        """source_type not 'm' → system=POWERBI (None/empty treated as BI model)."""
        # TableMetadata default is "unknown", test with non-m values
        for src_type in ("", "directQuery", "dual"):
            payload = self._make_payload("T", src_type)
            graph = MetadataAdapter.to_enterprise_graph(payload)
            assert graph.assets["table::T"].system == SystemType.POWERBI, (
                f"source_type={src_type!r} should map to POWERBI"
            )

    def test_import_mode_source_type_literal_is_still_powerbi(self):
        """source_type='import' (test fixture value, not actual TMDL) → POWERBI.
        Real TMDL files produce 'm' from the partition line; 'import' is only
        used in old test fixtures and must not trigger DATABASE classification."""
        payload = self._make_payload("orders", "import")
        graph = MetadataAdapter.to_enterprise_graph(payload)
        assert graph.assets["table::orders"].system == SystemType.POWERBI


# ---------------------------------------------------------------------------
# Bug 3 — Unknown asset: summary is populated even when no asset is found
# ---------------------------------------------------------------------------

class TestBug3UnknownAssetSummary:
    """When the target name matches nothing in the graph, the summary must be
    a non-empty, informative string."""

    @pytest.fixture(autouse=True)
    def _setup(self):
        self.analyzer = EnterpriseChangeAnalyzer(_build_graph())

    def test_unknown_target_summary_is_not_empty(self):
        result = self.analyzer.analyze("Delete NonExistentColumn")
        assert result.summary, "Expected non-empty summary for unknown asset"

    def test_unknown_target_summary_contains_target_name(self):
        result = self.analyzer.analyze("Delete NonExistentColumn")
        # Summary should mention what was searched for
        assert "NonExistentColumn" in result.summary or "nonexistentcolumn" in result.summary.lower()

    def test_unknown_target_source_asset_is_none(self):
        result = self.analyzer.analyze("Delete NonExistentColumn")
        assert result.source_asset is None

    def test_unknown_target_impact_count_is_zero(self):
        result = self.analyzer.analyze("Delete NonExistentColumn")
        assert result.impact_count == 0

    def test_unknown_target_impacted_assets_is_empty(self):
        result = self.analyzer.analyze("Delete NonExistentColumn")
        assert result.impacted_assets == []


# ---------------------------------------------------------------------------
# Bug 4 — Multi-word new_name must not be truncated
# ---------------------------------------------------------------------------

class TestBug4MultiWordNewName:
    """Rename patterns must capture multi-word replacement names."""

    @pytest.fixture(autouse=True)
    def _setup(self):
        self.analyzer = EnterpriseChangeAnalyzer(_build_graph())

    def test_rename_to_two_word_name(self):
        """``"Rename Revenue to gross profit"`` — new_name must be ``"gross profit"``."""
        result = self.analyzer.analyze("Rename Revenue to gross profit")
        assert result.change_request.new_name == "gross profit", (
            f"Expected 'gross profit', got {result.change_request.new_name!r}"
        )

    def test_rename_to_three_word_name(self):
        """``"Rename Revenue to net gross profit"``."""
        result = self.analyzer.analyze("Rename Revenue to net gross profit")
        assert result.change_request.new_name == "net gross profit"

    def test_rename_to_single_word_still_works(self):
        """Single-word new_name must not be broken by the multi-word fix."""
        result = self.analyzer.analyze("Rename Revenue to Sales")
        assert result.change_request.new_name == "Sales"

    def test_rename_qualified_multi_word(self):
        """Qualified rename pattern also captures multi-word new_name."""
        result = self.analyzer.analyze(
            "Rename CustomerID in customer_360 to client identifier"
        )
        assert result.change_request.new_name == "client identifier"

    def test_rename_table_multi_word(self):
        """Table rename with multi-word new_name is captured correctly.
        Pattern: 'Rename <TABLE> table to <NEW_NAME>' matches _RE_RENAME_SIMPLE."""
        result = self.analyzer.analyze("Rename customer_360 table to customer data")
        assert result.change_request.new_name == "customer data"


# ---------------------------------------------------------------------------
# Bug 5 — Asset resolution prefers the correct asset_type for the operation
# ---------------------------------------------------------------------------

class TestBug5AssetResolutionPriority:
    """When a name is shared between a column and a table (or appears in multiple
    tables), the resolver picks the asset whose type matches the operation."""

    @pytest.fixture(autouse=True)
    def _setup(self):
        self.analyzer = EnterpriseChangeAnalyzer(_build_graph())

    def test_column_delete_resolves_to_column_not_table(self):
        """``"Delete CustomerID column"`` → resolves to a COLUMN asset."""
        result = self.analyzer.analyze("Delete CustomerID column")
        assert result.source_asset is not None
        assert result.source_asset.asset_type == AssetType.COLUMN

    def test_table_delete_resolves_to_table(self):
        """``"Delete customer_360 table"`` → resolves to a TABLE asset."""
        result = self.analyzer.analyze("Delete customer_360 table")
        assert result.source_asset is not None
        assert result.source_asset.asset_type == AssetType.TABLE

    def test_column_rename_resolves_to_column(self):
        """``"Rename CustomerID to ClientID"`` → resolves to a COLUMN asset."""
        result = self.analyzer.analyze("Rename CustomerID to ClientID")
        assert result.source_asset is not None
        assert result.source_asset.asset_type == AssetType.COLUMN

    def test_table_rename_resolves_to_table(self):
        """``"Rename customer_360 table to client_data"`` → TABLE asset."""
        result = self.analyzer.analyze("Rename customer_360 table to client_data")
        assert result.source_asset is not None
        assert result.source_asset.asset_type == AssetType.TABLE

    def test_column_add_resolves_to_column_when_possible(self):
        """COLUMN_ADD with a known column name prefers COLUMN resolution."""
        result = self.analyzer.analyze("Add Revenue column")
        assert result.source_asset is not None
        assert result.source_asset.asset_type == AssetType.COLUMN
