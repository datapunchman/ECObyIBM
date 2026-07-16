"""
tests.test_notebook_parser
===========================
Unit tests for :class:`enterprise.notebook_parser.EcoNotebookParser`.

Coverage
--------
1.  Block detection — ECO METADATA / END ECO METADATA sentinels found
2.  Scalar field extraction — NOTEBOOK_NAME, LAYER, CATALOG, SCHEMA, EXECUTION_ORDER
3.  List field extraction — READ_TABLES, WRITE_TABLES (comma-separated)
4.  Graph asset creation — DATABRICKS_NOTEBOOK asset emitted
5.  Graph asset metadata — layer, execution_order, read_tables, write_tables
6.  Table stub assets — DELTA_TABLE stubs emitted for each reference
7.  READS edges — direction: read_table ──READS──> notebook
8.  WRITES edges — direction: notebook ──WRITES──> write_table
9.  Multiple read tables
10. Multiple write tables
11. Multiple notebooks in one parse call
12. Cross-notebook table stub deduplication
13. Missing NOTEBOOK_NAME — falls back to descriptor name / path basename
14. Missing LAYER — metadata["layer"] is None
15. Missing CATALOG — asset.catalog is None
16. Missing SCHEMA — asset.schema is None
17. Missing EXECUTION_ORDER — metadata["execution_order"] is None
18. Missing READ_TABLES — empty list
19. Missing WRITE_TABLES — empty list
20. Empty notebook source — no edges, notebook asset still created
21. No ECO METADATA block — no edges, unknown_notebook asset
22. Malformed EXECUTION_ORDER (non-integer) — gracefully set to None
23. emit_table_stubs=False — no stub assets, edges still present
24. BaseMetadataParser contract — parse() returns (list, list)
25. Empty notebooks list — returns ([], [])
26. Malformed descriptor (None) — skipped, no exception raised
27. Case-insensitive sentinels — ECO METADATA and END ECO METADATA
28. Extra comment lines inside block — ignored safely
29. Whitespace tolerance — values with leading/trailing spaces trimmed
30. Duplicate table entries — deduplicated within a field
31. Three-part table ref — catalog/schema/name split correctly on stub
32. Two-part table ref — schema/name split correctly on stub
33. One-part table ref — name only, no catalog/schema
34. owner / criticality propagation from descriptor
35. from_files() alternative constructor with temp files
36. EcoNotebookParser importable from enterprise package
37. Notebook with only READ_TABLES (no writes) — only READS edges
38. Notebook with only WRITE_TABLES (no reads) — only WRITES edges
39. Multiple notebooks: execution orders preserved independently
40. Asset id format — notebook::<name> and delta_table::<ref>

Run with:
    python -m pytest tests/test_notebook_parser.py -v
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from enterprise.notebook_parser import (
    EcoNotebookParser,
    _extract_block,
    _notebook_asset_id,
    _split_table_ref,
    _table_asset_id,
)
from graph.models import AssetType, Criticality, RelationshipType, SystemType


# ===========================================================================
# Shared fixtures — inline notebook source strings
# ===========================================================================

def _block(
    name: str = "01_bronze_ingestion",
    layer: str = "bronze",
    catalog: str = "hive_metastore",
    schema: str = "bronze",
    read_tables: str = "landing.customer, landing.sales",
    write_tables: str = "bronze.customer, bronze.sales",
    execution_order: str = "1",
) -> str:
    """Return a minimal well-formed ECO METADATA block."""
    lines = [
        "# Databricks notebook source",
        "# MAGIC %python",
        "",
        "# ECO METADATA",
        f"# NOTEBOOK_NAME: {name}",
        f"# LAYER: {layer}",
        f"# CATALOG: {catalog}",
        f"# SCHEMA: {schema}",
        f"# READ_TABLES: {read_tables}",
        f"# WRITE_TABLES: {write_tables}",
        f"# EXECUTION_ORDER: {execution_order}",
        "# END ECO METADATA",
        "",
        "# PySpark code starts here — parser must ignore this",
        "df = spark.read.table('should.not.be.parsed')",
    ]
    return "\n".join(lines)


BRONZE_NB = _block(
    name="01_bronze_ingestion",
    layer="bronze",
    catalog="hive_metastore",
    schema="bronze",
    read_tables="landing.customer, landing.sales",
    write_tables="bronze.customer, bronze.sales",
    execution_order="1",
)

SILVER_NB = _block(
    name="02_silver_transform",
    layer="silver",
    catalog="hive_metastore",
    schema="silver",
    read_tables="bronze.customer, bronze.sales",
    write_tables="silver.customer, silver.sales",
    execution_order="2",
)

GOLD_NB = _block(
    name="03_gold_publish",
    layer="gold",
    catalog="hive_metastore",
    schema="gold",
    read_tables="silver.customer, silver.sales",
    write_tables="gold.customer_360, gold.sales_dashboard, gold.monthly_sales",
    execution_order="3",
)

EMPTY_NB = ""

NO_BLOCK_NB = """\
# Databricks notebook source
# MAGIC %python

df = spark.read.table("bronze.customer")
df.write.saveAsTable("silver.customer")
"""


def _desc(name: str, source: str, **kwargs) -> dict:
    d = {"name": name, "source": source}
    d.update(kwargs)
    return d


# ===========================================================================
# 1. Helper function unit tests
# ===========================================================================


class TestExtractBlock:
    """_extract_block() correctly parses every field from a well-formed block."""

    def test_notebook_name(self):
        b = _extract_block(BRONZE_NB)
        assert b["name"] == "01_bronze_ingestion"

    def test_layer(self):
        b = _extract_block(BRONZE_NB)
        assert b["layer"] == "bronze"

    def test_catalog(self):
        b = _extract_block(BRONZE_NB)
        assert b["catalog"] == "hive_metastore"

    def test_schema(self):
        b = _extract_block(BRONZE_NB)
        assert b["schema"] == "bronze"

    def test_execution_order_integer(self):
        b = _extract_block(BRONZE_NB)
        assert b["execution_order"] == 1
        assert isinstance(b["execution_order"], int)

    def test_read_tables_two_entries(self):
        b = _extract_block(BRONZE_NB)
        assert b["read_tables"] == ["landing.customer", "landing.sales"]

    def test_write_tables_two_entries(self):
        b = _extract_block(BRONZE_NB)
        assert b["write_tables"] == ["bronze.customer", "bronze.sales"]

    def test_empty_source_returns_defaults(self):
        b = _extract_block("")
        assert b["name"] is None
        assert b["layer"] is None
        assert b["catalog"] is None
        assert b["schema"] is None
        assert b["execution_order"] is None
        assert b["read_tables"] == []
        assert b["write_tables"] == []

    def test_no_block_returns_defaults(self):
        b = _extract_block(NO_BLOCK_NB)
        assert b["name"] is None
        assert b["read_tables"] == []

    def test_missing_layer_is_none(self):
        src = "# ECO METADATA\n# NOTEBOOK_NAME: nb\n# END ECO METADATA"
        assert _extract_block(src)["layer"] is None

    def test_missing_execution_order_is_none(self):
        src = "# ECO METADATA\n# NOTEBOOK_NAME: nb\n# END ECO METADATA"
        assert _extract_block(src)["execution_order"] is None

    def test_malformed_execution_order_is_none(self):
        src = "# ECO METADATA\n# EXECUTION_ORDER: not_a_number\n# END ECO METADATA"
        assert _extract_block(src)["execution_order"] is None

    def test_whitespace_around_values_stripped(self):
        src = "# ECO METADATA\n# NOTEBOOK_NAME:   spaced_name   \n# END ECO METADATA"
        assert _extract_block(src)["name"] == "spaced_name"

    def test_duplicate_read_tables_deduplicated(self):
        src = (
            "# ECO METADATA\n"
            "# READ_TABLES: bronze.customer, bronze.customer, bronze.sales\n"
            "# END ECO METADATA"
        )
        b = _extract_block(src)
        assert b["read_tables"] == ["bronze.customer", "bronze.sales"]

    def test_extra_comment_lines_inside_block_ignored(self):
        src = (
            "# ECO METADATA\n"
            "# This is a comment about the notebook\n"
            "# NOTEBOOK_NAME: my_nb\n"
            "# END ECO METADATA"
        )
        assert _extract_block(src)["name"] == "my_nb"

    def test_lines_after_end_sentinel_ignored(self):
        src = (
            "# ECO METADATA\n"
            "# NOTEBOOK_NAME: my_nb\n"
            "# END ECO METADATA\n"
            "# NOTEBOOK_NAME: should_be_ignored\n"
        )
        assert _extract_block(src)["name"] == "my_nb"

    def test_case_insensitive_open_sentinel(self):
        src = "# eco metadata\n# NOTEBOOK_NAME: nb\n# END ECO METADATA"
        assert _extract_block(src)["name"] == "nb"

    def test_case_insensitive_close_sentinel(self):
        src = "# ECO METADATA\n# NOTEBOOK_NAME: nb\n# end eco metadata"
        assert _extract_block(src)["name"] == "nb"

    def test_three_write_tables(self):
        src = (
            "# ECO METADATA\n"
            "# WRITE_TABLES: gold.customer_360, gold.sales_dashboard, gold.monthly_sales\n"
            "# END ECO METADATA"
        )
        b = _extract_block(src)
        assert b["write_tables"] == [
            "gold.customer_360",
            "gold.sales_dashboard",
            "gold.monthly_sales",
        ]


class TestHelpers:
    """Unit tests for standalone helper functions."""

    def test_table_asset_id(self):
        assert _table_asset_id("bronze.customer") == "delta_table::bronze.customer"

    def test_notebook_asset_id(self):
        assert _notebook_asset_id("01_bronze") == "notebook::01_bronze"

    def test_split_three_parts(self):
        cat, sch, tbl = _split_table_ref("hive_metastore.bronze.customer")
        assert cat == "hive_metastore"
        assert sch == "bronze"
        assert tbl == "customer"

    def test_split_two_parts(self):
        cat, sch, tbl = _split_table_ref("bronze.customer")
        assert cat is None
        assert sch == "bronze"
        assert tbl == "customer"

    def test_split_one_part(self):
        cat, sch, tbl = _split_table_ref("customer")
        assert cat is None
        assert sch is None
        assert tbl == "customer"


# ===========================================================================
# 2. EcoNotebookParser integration tests
# ===========================================================================


class TestSingleNotebook:
    """Full parse of a single well-formed notebook."""

    @pytest.fixture(autouse=True)
    def _setup(self):
        self.parser = EcoNotebookParser([_desc("01_bronze_ingestion", BRONZE_NB)])
        self.assets, self.rels = self.parser.parse()
        self.nb = next(
            a for a in self.assets if a.asset_type == AssetType.DATABRICKS_NOTEBOOK
        )

    def test_notebook_asset_created(self):
        nbs = [a for a in self.assets if a.asset_type == AssetType.DATABRICKS_NOTEBOOK]
        assert len(nbs) == 1

    def test_notebook_asset_type(self):
        assert self.nb.asset_type == AssetType.DATABRICKS_NOTEBOOK

    def test_notebook_system_is_databricks(self):
        assert self.nb.system == SystemType.DATABRICKS

    def test_notebook_id_format(self):
        assert self.nb.id == "notebook::01_bronze_ingestion"

    def test_notebook_name(self):
        assert self.nb.name == "01_bronze_ingestion"

    def test_notebook_catalog(self):
        assert self.nb.catalog == "hive_metastore"

    def test_notebook_schema(self):
        assert self.nb.schema == "bronze"

    def test_metadata_layer(self):
        assert self.nb.metadata["layer"] == "bronze"

    def test_metadata_execution_order(self):
        assert self.nb.metadata["execution_order"] == 1

    def test_metadata_read_tables(self):
        assert self.nb.metadata["read_tables"] == ["landing.customer", "landing.sales"]

    def test_metadata_write_tables(self):
        assert self.nb.metadata["write_tables"] == ["bronze.customer", "bronze.sales"]

    def test_two_reads_edges(self):
        reads = [r for r in self.rels if r.relationship == RelationshipType.READS]
        assert len(reads) == 2

    def test_two_writes_edges(self):
        writes = [r for r in self.rels if r.relationship == RelationshipType.WRITES]
        assert len(writes) == 2

    def test_reads_edge_direction(self):
        """READS edges go from table → notebook (read_table ──READS──> notebook)."""
        reads = [r for r in self.rels if r.relationship == RelationshipType.READS]
        notebook_id = self.nb.id
        assert all(r.target == notebook_id for r in reads)

    def test_writes_edge_direction(self):
        """WRITES edges go from notebook → table (notebook ──WRITES──> write_table)."""
        writes = [r for r in self.rels if r.relationship == RelationshipType.WRITES]
        notebook_id = self.nb.id
        assert all(r.source == notebook_id for r in writes)

    def test_reads_edge_sources_are_table_ids(self):
        reads = [r for r in self.rels if r.relationship == RelationshipType.READS]
        sources = {r.source for r in reads}
        assert "delta_table::landing.customer" in sources
        assert "delta_table::landing.sales" in sources

    def test_writes_edge_targets_are_table_ids(self):
        writes = [r for r in self.rels if r.relationship == RelationshipType.WRITES]
        targets = {r.target for r in writes}
        assert "delta_table::bronze.customer" in targets
        assert "delta_table::bronze.sales" in targets

    def test_table_stub_assets_created(self):
        stubs = [a for a in self.assets if a.asset_type == AssetType.DELTA_TABLE]
        stub_ids = {a.id for a in stubs}
        assert "delta_table::landing.customer" in stub_ids
        assert "delta_table::landing.sales" in stub_ids
        assert "delta_table::bronze.customer" in stub_ids
        assert "delta_table::bronze.sales" in stub_ids

    def test_stub_system_is_databricks(self):
        stubs = [a for a in self.assets if a.asset_type == AssetType.DELTA_TABLE]
        assert all(s.system == SystemType.DATABRICKS for s in stubs)

    def test_pyspark_code_not_parsed(self):
        """The PySpark table ref 'should.not.be.parsed' must not appear."""
        all_ids = {a.id for a in self.assets}
        assert "delta_table::should.not.be.parsed" not in all_ids


class TestMultipleReadTables:
    """GOLD_NB has three write tables — all emitted correctly."""

    @pytest.fixture(autouse=True)
    def _setup(self):
        parser = EcoNotebookParser([_desc("03_gold_publish", GOLD_NB)])
        self.assets, self.rels = parser.parse()

    def test_three_writes_edges(self):
        writes = [r for r in self.rels if r.relationship == RelationshipType.WRITES]
        assert len(writes) == 3

    def test_three_write_stub_assets(self):
        targets = {
            r.target
            for r in self.rels
            if r.relationship == RelationshipType.WRITES
        }
        assert "delta_table::gold.customer_360" in targets
        assert "delta_table::gold.sales_dashboard" in targets
        assert "delta_table::gold.monthly_sales" in targets

    def test_two_reads_edges(self):
        reads = [r for r in self.rels if r.relationship == RelationshipType.READS]
        assert len(reads) == 2


class TestMultipleNotebooks:
    """Three-notebook medallion: bronze → silver → gold."""

    @pytest.fixture(autouse=True)
    def _setup(self):
        parser = EcoNotebookParser([
            _desc("01_bronze_ingestion", BRONZE_NB),
            _desc("02_silver_transform", SILVER_NB),
            _desc("03_gold_publish",     GOLD_NB),
        ])
        self.assets, self.rels = parser.parse()

    def test_three_notebook_assets(self):
        nbs = [a for a in self.assets if a.asset_type == AssetType.DATABRICKS_NOTEBOOK]
        assert len(nbs) == 3

    def test_execution_orders(self):
        nbs = {
            a.name: a.metadata["execution_order"]
            for a in self.assets
            if a.asset_type == AssetType.DATABRICKS_NOTEBOOK
        }
        assert nbs["01_bronze_ingestion"] == 1
        assert nbs["02_silver_transform"] == 2
        assert nbs["03_gold_publish"] == 3

    def test_cross_notebook_table_stub_dedup(self):
        """bronze.customer written by notebook-1 and read by notebook-2 → one stub."""
        stubs = [a for a in self.assets if a.id == "delta_table::bronze.customer"]
        assert len(stubs) == 1

    def test_total_relationship_count(self):
        # bronze: 2 READS + 2 WRITES = 4
        # silver: 2 READS + 2 WRITES = 4
        # gold:   2 READS + 3 WRITES = 5
        assert len(self.rels) == 13


class TestMissingMetadata:
    """Missing fields produce safe defaults — never raise."""

    def test_missing_notebook_name_uses_descriptor_name(self):
        src = "# ECO METADATA\n# LAYER: bronze\n# END ECO METADATA"
        parser = EcoNotebookParser([{"name": "my_notebook", "source": src}])
        assets, _ = parser.parse()
        nb = next(a for a in assets if a.asset_type == AssetType.DATABRICKS_NOTEBOOK)
        assert nb.name == "my_notebook"

    def test_missing_notebook_name_uses_path_basename(self):
        src = "# ECO METADATA\n# LAYER: bronze\n# END ECO METADATA"
        parser = EcoNotebookParser([{"path": "/repos/team/my_nb.py", "source": src}])
        assets, _ = parser.parse()
        nb = next(a for a in assets if a.asset_type == AssetType.DATABRICKS_NOTEBOOK)
        assert nb.name == "my_nb"

    def test_missing_all_fields_fallback_name(self):
        parser = EcoNotebookParser([{"source": "# ECO METADATA\n# END ECO METADATA"}])
        assets, _ = parser.parse()
        nb = next(a for a in assets if a.asset_type == AssetType.DATABRICKS_NOTEBOOK)
        assert nb.name == "unknown_notebook"

    def test_missing_layer_is_none(self):
        src = "# ECO METADATA\n# NOTEBOOK_NAME: nb\n# END ECO METADATA"
        parser = EcoNotebookParser([_desc("nb", src)])
        assets, _ = parser.parse()
        nb = next(a for a in assets if a.asset_type == AssetType.DATABRICKS_NOTEBOOK)
        assert nb.metadata["layer"] is None

    def test_missing_catalog_is_none(self):
        src = "# ECO METADATA\n# NOTEBOOK_NAME: nb\n# END ECO METADATA"
        parser = EcoNotebookParser([_desc("nb", src)])
        assets, _ = parser.parse()
        nb = next(a for a in assets if a.asset_type == AssetType.DATABRICKS_NOTEBOOK)
        assert nb.catalog is None

    def test_missing_schema_is_none(self):
        src = "# ECO METADATA\n# NOTEBOOK_NAME: nb\n# END ECO METADATA"
        parser = EcoNotebookParser([_desc("nb", src)])
        assets, _ = parser.parse()
        nb = next(a for a in assets if a.asset_type == AssetType.DATABRICKS_NOTEBOOK)
        assert nb.schema is None

    def test_missing_execution_order_is_none(self):
        src = "# ECO METADATA\n# NOTEBOOK_NAME: nb\n# END ECO METADATA"
        parser = EcoNotebookParser([_desc("nb", src)])
        assets, _ = parser.parse()
        nb = next(a for a in assets if a.asset_type == AssetType.DATABRICKS_NOTEBOOK)
        assert nb.metadata["execution_order"] is None

    def test_missing_read_tables_is_empty(self):
        src = "# ECO METADATA\n# NOTEBOOK_NAME: nb\n# END ECO METADATA"
        parser = EcoNotebookParser([_desc("nb", src)])
        assets, _ = parser.parse()
        nb = next(a for a in assets if a.asset_type == AssetType.DATABRICKS_NOTEBOOK)
        assert nb.metadata["read_tables"] == []

    def test_missing_write_tables_is_empty(self):
        src = "# ECO METADATA\n# NOTEBOOK_NAME: nb\n# END ECO METADATA"
        parser = EcoNotebookParser([_desc("nb", src)])
        assets, _ = parser.parse()
        nb = next(a for a in assets if a.asset_type == AssetType.DATABRICKS_NOTEBOOK)
        assert nb.metadata["write_tables"] == []

    def test_malformed_execution_order_is_none(self):
        src = (
            "# ECO METADATA\n"
            "# NOTEBOOK_NAME: nb\n"
            "# EXECUTION_ORDER: not_a_number\n"
            "# END ECO METADATA"
        )
        parser = EcoNotebookParser([_desc("nb", src)])
        assets, _ = parser.parse()
        nb = next(a for a in assets if a.asset_type == AssetType.DATABRICKS_NOTEBOOK)
        assert nb.metadata["execution_order"] is None


class TestEmptyAndMalformed:
    """Empty notebooks and malformed descriptors handled gracefully."""

    def test_empty_notebook_source_creates_asset(self):
        parser = EcoNotebookParser([_desc("nb", "")])
        assets, rels = parser.parse()
        nbs = [a for a in assets if a.asset_type == AssetType.DATABRICKS_NOTEBOOK]
        assert len(nbs) == 1

    def test_empty_notebook_source_no_edges(self):
        parser = EcoNotebookParser([_desc("nb", "")])
        _, rels = parser.parse()
        assert rels == []

    def test_no_eco_block_creates_asset(self):
        parser = EcoNotebookParser([_desc("nb", NO_BLOCK_NB)])
        assets, _ = parser.parse()
        nbs = [a for a in assets if a.asset_type == AssetType.DATABRICKS_NOTEBOOK]
        assert len(nbs) == 1

    def test_no_eco_block_no_edges(self):
        parser = EcoNotebookParser([_desc("nb", NO_BLOCK_NB)])
        _, rels = parser.parse()
        assert rels == []

    def test_no_eco_block_pyspark_refs_not_parsed(self):
        """The PySpark spark.read.table() in NO_BLOCK_NB must NOT produce stubs."""
        parser = EcoNotebookParser([_desc("nb", NO_BLOCK_NB)])
        assets, _ = parser.parse()
        stubs = [a for a in assets if a.asset_type == AssetType.DELTA_TABLE]
        assert stubs == []

    def test_empty_descriptor_list_returns_empty(self):
        parser = EcoNotebookParser([])
        assets, rels = parser.parse()
        assert assets == []
        assert rels == []

    def test_malformed_descriptor_none_skipped(self):
        """None in the descriptor list is caught, surrounding notebooks unaffected."""
        good = _desc("nb", BRONZE_NB)
        parser = EcoNotebookParser([None, good])  # type: ignore[list-item]
        assets, _ = parser.parse()
        # descriptor name "nb" takes priority over ECO block NOTEBOOK_NAME
        assert any(a.name == "nb" for a in assets)

    def test_parse_returns_tuple_of_lists(self):
        result = EcoNotebookParser([]).parse()
        assert isinstance(result, tuple)
        assert len(result) == 2
        assert isinstance(result[0], list)
        assert isinstance(result[1], list)


class TestEdgeDirections:
    """Edge direction: read_table ──READS──> notebook ──WRITES──> write_table."""

    def test_reads_edge_source_is_table_not_notebook(self):
        """The SOURCE of a READS edge is the table, not the notebook."""
        parser = EcoNotebookParser([_desc("01_bronze_ingestion", BRONZE_NB)])
        _, rels = parser.parse()
        reads = [r for r in rels if r.relationship == RelationshipType.READS]
        for r in reads:
            assert r.source.startswith("delta_table::")

    def test_reads_edge_target_is_notebook_not_table(self):
        """The TARGET of a READS edge is the notebook."""
        parser = EcoNotebookParser([_desc("01_bronze_ingestion", BRONZE_NB)])
        _, rels = parser.parse()
        reads = [r for r in rels if r.relationship == RelationshipType.READS]
        for r in reads:
            assert r.target.startswith("notebook::")

    def test_writes_edge_source_is_notebook_not_table(self):
        """The SOURCE of a WRITES edge is the notebook."""
        parser = EcoNotebookParser([_desc("01_bronze_ingestion", BRONZE_NB)])
        _, rels = parser.parse()
        writes = [r for r in rels if r.relationship == RelationshipType.WRITES]
        for r in writes:
            assert r.source.startswith("notebook::")

    def test_writes_edge_target_is_table_not_notebook(self):
        """The TARGET of a WRITES edge is the table."""
        parser = EcoNotebookParser([_desc("01_bronze_ingestion", BRONZE_NB)])
        _, rels = parser.parse()
        writes = [r for r in rels if r.relationship == RelationshipType.WRITES]
        for r in writes:
            assert r.target.startswith("delta_table::")

    def test_read_only_notebook_no_writes(self):
        src = (
            "# ECO METADATA\n"
            "# NOTEBOOK_NAME: read_only_nb\n"
            "# READ_TABLES: bronze.customer\n"
            "# END ECO METADATA"
        )
        parser = EcoNotebookParser([_desc("read_only_nb", src)])
        _, rels = parser.parse()
        writes = [r for r in rels if r.relationship == RelationshipType.WRITES]
        assert writes == []
        reads = [r for r in rels if r.relationship == RelationshipType.READS]
        assert len(reads) == 1

    def test_write_only_notebook_no_reads(self):
        src = (
            "# ECO METADATA\n"
            "# NOTEBOOK_NAME: write_only_nb\n"
            "# WRITE_TABLES: bronze.new_table\n"
            "# END ECO METADATA"
        )
        parser = EcoNotebookParser([_desc("write_only_nb", src)])
        _, rels = parser.parse()
        reads = [r for r in rels if r.relationship == RelationshipType.READS]
        assert reads == []
        writes = [r for r in rels if r.relationship == RelationshipType.WRITES]
        assert len(writes) == 1


class TestTableStubs:
    """DELTA_TABLE stub assets are correctly shaped."""

    def test_emit_stubs_false_no_stub_assets(self):
        parser = EcoNotebookParser(
            [_desc("01_bronze_ingestion", BRONZE_NB)],
            emit_table_stubs=False,
        )
        assets, rels = parser.parse()
        stubs = [a for a in assets if a.asset_type == AssetType.DELTA_TABLE]
        assert stubs == []
        # But edges are still created
        assert len(rels) > 0

    def test_three_part_stub_has_catalog_schema_name(self):
        src = (
            "# ECO METADATA\n"
            "# NOTEBOOK_NAME: nb\n"
            "# READ_TABLES: hive_metastore.bronze.customer\n"
            "# END ECO METADATA"
        )
        parser = EcoNotebookParser([_desc("nb", src)])
        assets, _ = parser.parse()
        stub = next(
            a for a in assets if a.id == "delta_table::hive_metastore.bronze.customer"
        )
        assert stub.catalog == "hive_metastore"
        assert stub.schema == "bronze"
        assert stub.name == "customer"

    def test_two_part_stub_has_schema_and_name(self):
        src = (
            "# ECO METADATA\n"
            "# NOTEBOOK_NAME: nb\n"
            "# READ_TABLES: bronze.customer\n"
            "# END ECO METADATA"
        )
        parser = EcoNotebookParser([_desc("nb", src)])
        assets, _ = parser.parse()
        stub = next(a for a in assets if a.id == "delta_table::bronze.customer")
        assert stub.catalog is None
        assert stub.schema == "bronze"
        assert stub.name == "customer"

    def test_one_part_stub_name_only(self):
        src = (
            "# ECO METADATA\n"
            "# NOTEBOOK_NAME: nb\n"
            "# READ_TABLES: orders\n"
            "# END ECO METADATA"
        )
        parser = EcoNotebookParser([_desc("nb", src)])
        assets, _ = parser.parse()
        stub = next(a for a in assets if a.id == "delta_table::orders")
        assert stub.catalog is None
        assert stub.schema is None
        assert stub.name == "orders"


class TestEnrichment:
    """Owner, criticality, and tags propagated correctly."""

    def test_owner_from_descriptor(self):
        parser = EcoNotebookParser(
            [_desc("nb", BRONZE_NB, owner="de-team")],
        )
        assets, _ = parser.parse()
        nb = next(a for a in assets if a.asset_type == AssetType.DATABRICKS_NOTEBOOK)
        assert nb.owner == "de-team"

    def test_owner_from_parser_default(self):
        parser = EcoNotebookParser(
            [_desc("nb", BRONZE_NB)],
            owner="platform-team",
        )
        assets, _ = parser.parse()
        nb = next(a for a in assets if a.asset_type == AssetType.DATABRICKS_NOTEBOOK)
        assert nb.owner == "platform-team"

    def test_descriptor_owner_overrides_parser_default(self):
        parser = EcoNotebookParser(
            [_desc("nb", BRONZE_NB, owner="analytics")],
            owner="platform-team",
        )
        assets, _ = parser.parse()
        nb = next(a for a in assets if a.asset_type == AssetType.DATABRICKS_NOTEBOOK)
        assert nb.owner == "analytics"

    def test_criticality_from_descriptor(self):
        parser = EcoNotebookParser(
            [_desc("nb", BRONZE_NB, criticality="high")],
        )
        assets, _ = parser.parse()
        nb = next(a for a in assets if a.asset_type == AssetType.DATABRICKS_NOTEBOOK)
        assert nb.criticality == Criticality.HIGH

    def test_tags_from_descriptor(self):
        parser = EcoNotebookParser(
            [_desc("nb", BRONZE_NB, tags=["ingestion", "bronze"])],
        )
        assets, _ = parser.parse()
        nb = next(a for a in assets if a.asset_type == AssetType.DATABRICKS_NOTEBOOK)
        assert "ingestion" in nb.tags
        assert "bronze" in nb.tags


class TestFromFiles:
    """from_files() constructor reads actual .py files."""

    def test_from_files_reads_eco_block(self):
        content = BRONZE_NB
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False, encoding="utf-8"
        ) as f:
            f.write(content)
            tmp_path = Path(f.name)

        try:
            parser = EcoNotebookParser.from_files([tmp_path])
            assets, _ = parser.parse()
            nbs = [a for a in assets if a.asset_type == AssetType.DATABRICKS_NOTEBOOK]
            assert len(nbs) == 1
            assert nbs[0].metadata["layer"] == "bronze"
        finally:
            tmp_path.unlink(missing_ok=True)

    def test_from_files_unreadable_path_skipped(self):
        parser = EcoNotebookParser.from_files([Path("/nonexistent/path/nb.py")])
        assets, rels = parser.parse()
        assert assets == []
        assert rels == []


class TestImportFromPackage:
    """EcoNotebookParser is importable from the enterprise package."""

    def test_importable_from_enterprise(self):
        from enterprise import EcoNotebookParser as ENP  # noqa: F401
        assert ENP is EcoNotebookParser
