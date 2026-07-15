"""
tests.test_asset_resolver
==========================
Unit tests for asset resolution in
:class:`change.analyzer.EnterpriseChangeAnalyzer`.

Covers every layer of the resolution pipeline:

    1. Regex parser — target_name and table_name extraction
    2. Normalisation helpers — _norm() and _clean_target()
    3. Tier-1 resolution — exact normalised + table hint
    4. Tier-2 resolution — exact normalised, no table hint
    5. Tier-3 resolution — normalised substring
    6. Tier-4 resolution — fuzzy fallback (no null return)
    7. Full end-to-end: change request text → resolved asset ID

Root causes fixed
-----------------
Bug 1 — Regex over-capture
    Old:  ``_RE_RENAME`` matched ``\\w[\\w\\s]*?`` which consumed everything up
    to the keyword "to", capturing phrases like
    ``"the Revenue column in sales_dashboard"`` as the target.
    New:  Three specialised patterns (QUALIFIED / SIMPLE / BARE) each capture
    only a single ``\\w+`` word as the target name.

Bug 2 — Underscore / case mismatch
    ``Customer_ID`` (user input) normalises to ``customerid``.
    ``CustomerID``  (graph name)  normalises to ``customerid``.
    Old resolver used ``str.lower()`` only — underscores were kept, so
    ``customer_id != customerid`` and the match failed.
    New resolver uses ``_norm()`` which strips underscores, spaces, and
    hyphens before comparison.

Run with:
    python -m pytest tests/test_asset_resolver.py -v
"""

from __future__ import annotations

import pytest

from change.analyzer import EnterpriseChangeAnalyzer, _clean_target, _norm
from change.models import ChangeRequest, ChangeType
from graph.enterprise_graph import EnterpriseGraph
from graph.models import Asset, AssetType, Relationship, RelationshipType, SystemType


# ---------------------------------------------------------------------------
# Shared graph fixture
# ---------------------------------------------------------------------------

def _build_graph() -> EnterpriseGraph:
    """Return a minimal but realistic ``EnterpriseGraph`` for testing.

    Asset IDs and names mirror real graph IDs produced by the adapter:
        column::<table_name>::<ColumnName>
        table::<table_name>
        measure::<Measure Name>
    """
    g = EnterpriseGraph()

    # Tables
    for tbl in ("sales_dashboard", "customer_360", "monthly_sales"):
        g.add_asset(Asset(
            id=f"table::{tbl}",
            name=tbl,
            asset_type=AssetType.TABLE,
            system=SystemType.POWERBI,
        ))

    # Columns — note: graph stores CamelCase names (no underscores)
    for tbl, col in [
        ("sales_dashboard", "Revenue"),
        ("sales_dashboard", "CustomerID"),
        ("sales_dashboard", "OrderDate"),
        ("customer_360",    "CustomerID"),
        ("customer_360",    "CustomerCode"),
        ("monthly_sales",   "Revenue"),
        ("monthly_sales",   "Profit"),
    ]:
        g.add_asset(Asset(
            id=f"column::{tbl}::{col}",
            name=col,
            asset_type=AssetType.COLUMN,
            system=SystemType.POWERBI,
            properties={"table_name": tbl},
        ))

    # Measures
    for m in ("Total Revenue", "Profit Margin %"):
        g.add_asset(Asset(
            id=f"measure::{m}",
            name=m,
            asset_type=AssetType.MEASURE,
            system=SystemType.POWERBI,
        ))

    return g


# ---------------------------------------------------------------------------
# 1. Normalisation helpers
# ---------------------------------------------------------------------------

class TestNormHelpers:
    """_norm() and _clean_target() produce correct tokens."""

    def test_norm_strips_underscores(self):
        assert _norm("Customer_ID") == "customerid"

    def test_norm_strips_spaces(self):
        assert _norm("customer id") == "customerid"

    def test_norm_strips_hyphens(self):
        assert _norm("customer-id") == "customerid"

    def test_norm_lowercases(self):
        assert _norm("CustomerID") == "customerid"

    def test_norm_mixed(self):
        assert _norm("Total_Revenue") == "totalrevenue"
        assert _norm("TotalRevenue")  == "totalrevenue"
        assert _norm("total revenue") == "totalrevenue"

    def test_clean_target_strips_stopwords(self):
        # Stopwords ("the", "column") are removed; non-stopword tokens kept.
        # "sales_dashboard" is not a stopword so it is retained.
        result = _clean_target("the Revenue column in sales_dashboard")
        assert "Revenue" in result
        assert "the" not in result.lower()
        assert "column" not in result.lower()

    def test_clean_target_single_word(self):
        assert _clean_target("Revenue") == "Revenue"

    def test_clean_target_preserves_non_stopword_phrase(self):
        # "OrderDate" has no stopwords → unchanged
        assert _clean_target("OrderDate") == "OrderDate"

    def test_clean_target_fallback_when_all_stopwords(self):
        # Shouldn't happen in practice, but guard the fallback
        result = _clean_target("the")
        # All tokens were stopwords → returns the original string
        assert result == "the"


# ---------------------------------------------------------------------------
# 2. Regex parser — target_name and table_name extraction
# ---------------------------------------------------------------------------

class TestParser:
    """``_parse()`` extracts correct target_name, table_name, change_type."""

    @pytest.fixture(autouse=True)
    def _setup(self):
        self.analyzer = EnterpriseChangeAnalyzer(_build_graph())

    # -- rename (qualified) --------------------------------------------------

    def test_rename_qualified_with_the(self):
        """'Rename the Revenue column in sales_dashboard to GrossRevenue'
        → target='Revenue', table='sales_dashboard'"""
        p = self.analyzer._parse(
            "Rename the Revenue column in sales_dashboard to GrossRevenue"
        )
        assert p.change_type == ChangeType.COLUMN_RENAME
        assert p.target_name == "Revenue"
        assert p.table_name == "sales_dashboard"
        assert p.new_name == "GrossRevenue"

    def test_rename_qualified_without_the(self):
        p = self.analyzer._parse(
            "Rename Revenue column in sales_dashboard to GrossRevenue"
        )
        assert p.target_name == "Revenue"
        assert p.table_name == "sales_dashboard"

    # -- rename (bare / in-suffix) -------------------------------------------

    def test_rename_bare_with_table_hint(self):
        """'Rename Customer_ID to Client_ID in customer_360'
        → target='Customer_ID', table='customer_360'"""
        p = self.analyzer._parse(
            "Rename Customer_ID to Client_ID in customer_360"
        )
        assert p.change_type == ChangeType.COLUMN_RENAME
        assert p.target_name == "Customer_ID"
        # table_name extracted from trailing "in customer_360"
        assert p.table_name == "customer_360"
        assert p.new_name == "Client_ID"

    def test_rename_bare_no_table_hint(self):
        p = self.analyzer._parse("rename Revenue to GrossRevenue")
        assert p.target_name == "Revenue"
        assert p.table_name is None

    # -- column delete --------------------------------------------------------

    def test_col_delete_with_table_hint(self):
        p = self.analyzer._parse(
            "Remove the OrderDate column from sales_dashboard"
        )
        assert p.change_type == ChangeType.COLUMN_DELETE
        assert p.target_name == "OrderDate"
        assert p.table_name == "sales_dashboard"

    # -- table delete --------------------------------------------------------

    def test_table_delete(self):
        p = self.analyzer._parse("Drop the monthly_sales table")
        assert p.change_type == ChangeType.TABLE_DELETE
        assert p.target_name == "monthly_sales"


# ---------------------------------------------------------------------------
# 3. Tier-1 resolution — exact normalised + table hint
# ---------------------------------------------------------------------------

class TestTier1Resolution:
    """Exact normalised name match within a specified table."""

    @pytest.fixture(autouse=True)
    def _setup(self):
        self.analyzer = EnterpriseChangeAnalyzer(_build_graph())

    def test_revenue_in_sales_dashboard(self):
        """'Revenue' + 'sales_dashboard' → column::sales_dashboard::Revenue"""
        p = ChangeRequest(
            original_request="",
            change_type=ChangeType.COLUMN_RENAME,
            target_name="Revenue",
            table_name="sales_dashboard",
        )
        asset = self.analyzer._resolve_asset(p)
        assert asset is not None
        assert asset.id == "column::sales_dashboard::Revenue"

    def test_revenue_in_monthly_sales(self):
        """Same column name, different table → resolves to the right one."""
        p = ChangeRequest(
            original_request="",
            change_type=ChangeType.COLUMN_RENAME,
            target_name="Revenue",
            table_name="monthly_sales",
        )
        asset = self.analyzer._resolve_asset(p)
        assert asset is not None
        assert asset.id == "column::monthly_sales::Revenue"

    def test_underscore_name_matches_camelcase_in_graph(self):
        """BEFORE (bug): 'Customer_ID'.lower() = 'customer_id' ≠ 'customerid' = 'CustomerID'.lower()
        AFTER  (fix):  _norm('Customer_ID') = 'customerid' = _norm('CustomerID') → match ✓"""
        p = ChangeRequest(
            original_request="",
            change_type=ChangeType.COLUMN_RENAME,
            target_name="Customer_ID",
            table_name="customer_360",
        )
        asset = self.analyzer._resolve_asset(p)
        assert asset is not None
        assert asset.id == "column::customer_360::CustomerID"


# ---------------------------------------------------------------------------
# 4. Tier-2 resolution — exact normalised, no table hint
# ---------------------------------------------------------------------------

class TestTier2Resolution:
    """Exact normalised name match when no table is specified."""

    @pytest.fixture(autouse=True)
    def _setup(self):
        self.analyzer = EnterpriseChangeAnalyzer(_build_graph())

    def test_customerid_any_table(self):
        """Without a table hint, resolves to one of the two CustomerID columns."""
        p = ChangeRequest(
            original_request="",
            change_type=ChangeType.COLUMN_RENAME,
            target_name="CustomerID",
        )
        asset = self.analyzer._resolve_asset(p)
        assert asset is not None
        assert asset.id in (
            "column::customer_360::CustomerID",
            "column::sales_dashboard::CustomerID",
        )

    def test_table_name_resolves(self):
        p = ChangeRequest(
            original_request="",
            change_type=ChangeType.TABLE_DELETE,
            target_name="monthly_sales",
        )
        asset = self.analyzer._resolve_asset(p)
        assert asset is not None
        assert asset.id == "table::monthly_sales"

    def test_measure_resolves(self):
        p = ChangeRequest(
            original_request="",
            change_type=ChangeType.UNKNOWN,
            target_name="Total Revenue",
        )
        asset = self.analyzer._resolve_asset(p)
        assert asset is not None
        assert asset.id == "measure::Total Revenue"


# ---------------------------------------------------------------------------
# 5. Tier-3 resolution — normalised substring
# ---------------------------------------------------------------------------

class TestTier3Resolution:
    """Normalised substring match handles partial names."""

    @pytest.fixture(autouse=True)
    def _setup(self):
        self.analyzer = EnterpriseChangeAnalyzer(_build_graph())

    def test_partial_name_resolves(self):
        """'profit' is a substring of 'Profit Margin %' normalised."""
        p = ChangeRequest(
            original_request="",
            change_type=ChangeType.UNKNOWN,
            target_name="profit",
        )
        asset = self.analyzer._resolve_asset(p)
        assert asset is not None  # at least one match found


# ---------------------------------------------------------------------------
# 6. Tier-4 resolution — fuzzy fallback
# ---------------------------------------------------------------------------

class TestTier4FuzzyFallback:
    """Fuzzy fallback returns closest match instead of None."""

    @pytest.fixture(autouse=True)
    def _setup(self):
        self.analyzer = EnterpriseChangeAnalyzer(_build_graph())

    def test_typo_resolves_via_fuzzy(self):
        """'CustomrID' (typo) should fuzzy-match to CustomerID."""
        p = ChangeRequest(
            original_request="",
            change_type=ChangeType.COLUMN_RENAME,
            target_name="CustomrID",
        )
        asset = self.analyzer._resolve_asset(p)
        assert asset is not None, "Fuzzy fallback must return an asset, not None"
        assert "CustomerID" in asset.name or "CustomerCode" in asset.name


# ---------------------------------------------------------------------------
# 7. Full end-to-end: request text → resolved asset ID
# ---------------------------------------------------------------------------

class TestEndToEnd:
    """Integration: raw change request text → correct resolved asset ID."""

    @pytest.fixture(autouse=True)
    def _setup(self):
        self.analyzer = EnterpriseChangeAnalyzer(_build_graph())

    @pytest.mark.parametrize("request_text, expected_id", [
        # Qualified rename (with "the … column in <table>") — Bug 1 scenario
        (
            "Rename the Revenue column in sales_dashboard to GrossRevenue",
            "column::sales_dashboard::Revenue",
        ),
        # Qualified rename (without "the")
        (
            "Rename Revenue column in sales_dashboard to GrossRevenue",
            "column::sales_dashboard::Revenue",
        ),
        # Bare rename + table hint suffix — Bug 2 scenario
        (
            "Rename Customer_ID to Client_ID in customer_360",
            "column::customer_360::CustomerID",
        ),
        # Column delete with table qualifier
        (
            "Remove the OrderDate column from sales_dashboard",
            "column::sales_dashboard::OrderDate",
        ),
        # Table delete
        (
            "Drop the monthly_sales table",
            "table::monthly_sales",
        ),
        # Table rename
        (
            "Rename the sales_dashboard table to sales_facts",
            "table::sales_dashboard",
        ),
    ])
    def test_resolution(self, request_text: str, expected_id: str):
        parsed = self.analyzer._parse(request_text)
        asset = self.analyzer._resolve_asset(parsed)
        assert asset is not None, (
            f"Expected asset {expected_id!r} but got None for request: {request_text!r}\n"
            f"  target_name={parsed.target_name!r}, table_name={parsed.table_name!r}"
        )
        assert asset.id == expected_id, (
            f"Expected {expected_id!r} but got {asset.id!r} for request: {request_text!r}"
        )
