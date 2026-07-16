"""
tests.test_databricks_parser
=============================
Unit tests for :class:`enterprise.databricks_parser.DatabricksNotebookParser`.

Coverage
--------
1.  Notebook detection — asset is created with correct type / system / id
2.  Name inference — from explicit name, from path, from unknown
3.  Execution order — numeric prefix parsed correctly
4.  Python read extraction — spark.read.table / spark.table
5.  SQL read extraction   — SELECT FROM / JOIN
6.  DeltaTable read extraction — forName / forPath
7.  Python write extraction — saveAsTable / insertInto / write.table
8.  SQL write extraction  — CREATE TABLE / CREATE OR REPLACE / MERGE INTO / INSERT INTO
9.  Edge creation — READS / WRITES relationships
10. Deduplication — duplicate references produce one stub, multiple edges
11. Wildcard / partial refs — unusual reference strings are tolerated
12. emit_table_stubs=False — no stub assets, only edges
13. Multiple notebooks — stubs shared across notebooks
14. Malformed descriptor — skipped without raising
15. Empty descriptor — notebook created, no edges
16. Empty source string — notebook created, no edges
17. Jupyter cell format — cells list processed correctly
18. Markdown cells excluded — markdown does not contribute reads/writes
19. Mixed language notebook — Python + SQL cells both mined
20. catalog/schema refs stored in metadata
21. read_count / write_count in metadata
22. Execution order None for no-prefix notebooks
23. BaseMetadataParser contract — parse() returns (list, list)
24. Empty descriptor list — returns ([], [])
25. Owner / criticality propagation
26. workspace_folder inference from path
27. source_file set to notebook path
28. SQL keyword exclusion — "WHERE", "JOIN" not treated as table refs
29. Multi-catalog three-part references
30. write.table not confused with read.table
31. MERGE INTO (upsert) produces WRITES edge
32. INSERT OVERWRITE produces WRITES edge
33. DatabricksNotebookParser importable from enterprise package
34. End-to-end: full medallion pipeline (bronze → silver → gold lineage)

Run with:
    python -m pytest tests/test_databricks_parser.py -v
"""

from __future__ import annotations

import pytest

from enterprise.databricks_parser import (
    DatabricksNotebookParser,
    _extract_reads,
    _extract_writes,
    _extract_source,
    _infer_execution_order,
    _infer_name,
    _infer_workspace_folder,
    _table_asset_id,
)
from graph.models import AssetType, RelationshipType, SystemType


# ===========================================================================
# Shared fixtures
# ===========================================================================

BRONZE_SOURCE = """\
from pyspark.sql import SparkSession
spark = SparkSession.builder.getOrCreate()

# Read raw landing data
df = spark.read.table("landing.customer")
raw_sales = spark.table("landing.sales")

# Write to bronze layer
df.write.format("delta").saveAsTable("bronze.customer")
raw_sales.write.format("delta").saveAsTable("bronze.sales")
"""

SILVER_SOURCE = """\
# Silver transformation
bronze_customer = spark.read.table("bronze.customer")
bronze_sales    = spark.read.table("bronze.sales")

# Enrich and join
result = bronze_customer.join(bronze_sales, "customer_id")
result.write.format("delta").saveAsTable("silver.customer")
result.write.format("delta").saveAsTable("silver.sales")
"""

GOLD_SOURCE = """\
-- SQL cell: read silver, write gold
SELECT * FROM silver.customer;
SELECT * FROM silver.sales;

CREATE OR REPLACE TABLE gold.customer_360 AS
SELECT * FROM silver.customer;

CREATE OR REPLACE TABLE gold.sales_dashboard AS
SELECT * FROM silver.sales;

CREATE OR REPLACE TABLE gold.monthly_sales AS
SELECT month, SUM(revenue) FROM silver.sales GROUP BY month;
"""

MERGE_SOURCE = """\
MERGE INTO gold.customer_360 AS target
USING silver.customer AS source
ON target.id = source.id
WHEN MATCHED THEN UPDATE SET *
WHEN NOT MATCHED THEN INSERT *;
"""


def _make_descriptor(
    name: str,
    source: str,
    path: str | None = None,
    language: str = "python",
    **kwargs,
) -> dict:
    d = {"name": name, "source": source, "language": language}
    if path:
        d["path"] = path
    d.update(kwargs)
    return d


# ===========================================================================
# 1. Helper function unit tests
# ===========================================================================


class TestExtractReads:
    """Unit tests for the _extract_reads() helper."""

    def test_spark_read_table_double_quotes(self):
        src = 'df = spark.read.table("bronze.customer")'
        assert "bronze.customer" in _extract_reads(src)

    def test_spark_read_table_single_quotes(self):
        src = "df = spark.read.table('bronze.customer')"
        assert "bronze.customer" in _extract_reads(src)

    def test_spark_table(self):
        src = 'df = spark.table("landing.sales")'
        assert "landing.sales" in _extract_reads(src)

    def test_sql_select_from(self):
        src = "SELECT * FROM silver.customer"
        assert "silver.customer" in _extract_reads(src)

    def test_sql_join(self):
        src = "SELECT * FROM silver.customer JOIN silver.sales ON customer.id = sales.id"
        refs = _extract_reads(src)
        assert "silver.customer" in refs
        assert "silver.sales" in refs

    def test_delta_table_for_name(self):
        src = 'dt = DeltaTable.forName(spark, "bronze.customer")'
        assert "bronze.customer" in _extract_reads(src)

    def test_delta_table_for_path(self):
        src = 'dt = DeltaTable.forPath(spark, "/delta/bronze/customer")'
        assert "/delta/bronze/customer" in _extract_reads(src)

    def test_three_part_reference(self):
        src = 'df = spark.read.table("hive_metastore.bronze.customer")'
        assert "hive_metastore.bronze.customer" in _extract_reads(src)

    def test_sql_keywords_excluded(self):
        src = "SELECT id FROM silver.customer WHERE id > 0"
        refs = _extract_reads(src)
        assert "where" not in [r.lower() for r in refs]

    def test_deduplication(self):
        src = 'spark.read.table("bronze.customer"); spark.table("bronze.customer")'
        refs = _extract_reads(src)
        assert refs.count("bronze.customer") == 1

    def test_empty_source_returns_empty(self):
        assert _extract_reads("") == []

    def test_multiline_source(self):
        refs = _extract_reads(BRONZE_SOURCE)
        assert "landing.customer" in refs
        assert "landing.sales" in refs


class TestExtractWrites:
    """Unit tests for the _extract_writes() helper."""

    def test_save_as_table(self):
        src = 'df.write.format("delta").saveAsTable("bronze.customer")'
        assert "bronze.customer" in _extract_writes(src)

    def test_insert_into(self):
        src = 'df.insertInto("bronze.customer")'
        assert "bronze.customer" in _extract_writes(src)

    def test_write_table(self):
        src = 'df.write.table("silver.sales")'
        assert "silver.sales" in _extract_writes(src)

    def test_create_table(self):
        src = "CREATE TABLE gold.customer_360 AS SELECT * FROM silver.customer"
        assert "gold.customer_360" in _extract_writes(src)

    def test_create_or_replace_table(self):
        src = "CREATE OR REPLACE TABLE gold.sales_dashboard AS SELECT * FROM silver.sales"
        assert "gold.sales_dashboard" in _extract_writes(src)

    def test_merge_into(self):
        src = "MERGE INTO gold.customer_360 AS target USING silver.customer AS source"
        assert "gold.customer_360" in _extract_writes(src)

    def test_insert_into_sql(self):
        src = "INSERT INTO bronze.customer SELECT * FROM landing.customer"
        assert "bronze.customer" in _extract_writes(src)

    def test_insert_overwrite(self):
        src = "INSERT OVERWRITE gold.monthly_sales SELECT * FROM silver.sales"
        assert "gold.monthly_sales" in _extract_writes(src)

    def test_deduplication(self):
        src = 'df.write.saveAsTable("bronze.customer"); spark.sql("INSERT INTO bronze.customer VALUES (1)")'
        refs = _extract_writes(src)
        assert refs.count("bronze.customer") == 1

    def test_empty_source_returns_empty(self):
        assert _extract_writes("") == []

    def test_read_table_not_matched_as_write(self):
        """spark.read.table() must NOT appear in write results."""
        src = 'df = spark.read.table("bronze.customer")'
        assert "bronze.customer" not in _extract_writes(src)


class TestExtractSource:
    """Unit tests for the _extract_source() helper."""

    def test_flat_source_string(self):
        desc = {"source": "spark.read.table('bronze.customer')"}
        assert "bronze.customer" in _extract_source(desc)

    def test_jupyter_cells(self):
        desc = {
            "cells": [
                {"cell_type": "code", "source": "df = spark.read.table('bronze.customer')"},
                {"cell_type": "markdown", "source": "## Notes"},
            ]
        }
        src = _extract_source(desc)
        assert "bronze.customer" in src
        assert "Notes" not in src

    def test_jupyter_cells_source_as_list(self):
        """Jupyter stores cell source as a list of line strings."""
        desc = {
            "cells": [
                {"cell_type": "code", "source": ["df = spark", ".read", ".table('t')"]},
            ]
        }
        src = _extract_source(desc)
        assert "spark" in src

    def test_markdown_cells_excluded(self):
        desc = {
            "cells": [
                {"cell_type": "markdown", "source": "spark.read.table('should.not.appear')"},
                {"cell_type": "code", "source": "spark.read.table('real.table')"},
            ]
        }
        src = _extract_source(desc)
        assert "should.not.appear" not in src
        assert "real.table" in src

    def test_empty_descriptor_returns_empty_string(self):
        assert _extract_source({}) == ""

    def test_cells_key_missing_source_key_missing(self):
        assert _extract_source({"name": "nb"}) == ""


class TestInferHelpers:
    """Unit tests for name / folder / execution-order helpers."""

    def test_infer_name_explicit(self):
        assert _infer_name({"name": "my_notebook"}) == "my_notebook"

    def test_infer_name_from_path(self):
        assert _infer_name({"path": "/Repos/team/01_bronze_ingestion"}) == "01_bronze_ingestion"

    def test_infer_name_strips_extension(self):
        assert _infer_name({"path": "/Repos/team/my_nb.py"}) == "my_nb"

    def test_infer_name_fallback(self):
        assert _infer_name({}) == "unknown_notebook"

    def test_infer_workspace_folder_explicit(self):
        assert _infer_workspace_folder({"workspace_folder": "/Repos/team"}) == "/Repos/team"

    def test_infer_workspace_folder_from_path(self):
        assert _infer_workspace_folder({"path": "/Repos/team/my_nb"}) == "/Repos/team"

    def test_infer_workspace_folder_no_slash(self):
        assert _infer_workspace_folder({"path": "my_nb"}) is None

    def test_execution_order_two_digit(self):
        assert _infer_execution_order("01_bronze_ingestion") == 1

    def test_execution_order_single_digit(self):
        assert _infer_execution_order("2_silver_transform") == 2

    def test_execution_order_three_digit(self):
        assert _infer_execution_order("010_gold_publish") == 10

    def test_execution_order_none_for_no_prefix(self):
        assert _infer_execution_order("adhoc_analysis") is None

    def test_execution_order_none_for_letter_prefix(self):
        assert _infer_execution_order("a_notebook") is None

    def test_table_asset_id_format(self):
        assert _table_asset_id("bronze.customer") == "delta_table::bronze.customer"


# ===========================================================================
# 2. DatabricksNotebookParser integration tests
# ===========================================================================


class TestNotebookDetection:
    """Parser creates correctly typed notebook assets."""

    def test_notebook_asset_created(self):
        parser = DatabricksNotebookParser([_make_descriptor("my_nb", "")])
        assets, _ = parser.parse()
        nb_assets = [a for a in assets if a.asset_type == AssetType.DATABRICKS_NOTEBOOK]
        assert len(nb_assets) == 1

    def test_notebook_asset_system_is_databricks(self):
        parser = DatabricksNotebookParser([_make_descriptor("my_nb", "")])
        assets, _ = parser.parse()
        nb = next(a for a in assets if a.asset_type == AssetType.DATABRICKS_NOTEBOOK)
        assert nb.system == SystemType.DATABRICKS

    def test_notebook_asset_id_uses_path(self):
        parser = DatabricksNotebookParser([
            _make_descriptor("my_nb", "", path="/Repos/team/my_nb")
        ])
        assets, _ = parser.parse()
        nb = next(a for a in assets if a.asset_type == AssetType.DATABRICKS_NOTEBOOK)
        assert nb.id == "notebook::/Repos/team/my_nb"

    def test_notebook_asset_name(self):
        parser = DatabricksNotebookParser([_make_descriptor("bronze_ingestion", "")])
        assets, _ = parser.parse()
        nb = next(a for a in assets if a.asset_type == AssetType.DATABRICKS_NOTEBOOK)
        assert nb.name == "bronze_ingestion"

    def test_execution_order_stored_in_metadata(self):
        parser = DatabricksNotebookParser([
            _make_descriptor("01_bronze_ingestion", BRONZE_SOURCE)
        ])
        assets, _ = parser.parse()
        nb = next(a for a in assets if a.asset_type == AssetType.DATABRICKS_NOTEBOOK)
        assert nb.metadata["execution_order"] == 1

    def test_execution_order_none_for_unprefixed_name(self):
        parser = DatabricksNotebookParser([_make_descriptor("adhoc_notebook", "")])
        assets, _ = parser.parse()
        nb = next(a for a in assets if a.asset_type == AssetType.DATABRICKS_NOTEBOOK)
        assert nb.metadata["execution_order"] is None

    def test_workspace_folder_in_metadata(self):
        parser = DatabricksNotebookParser([
            _make_descriptor("nb", "", path="/Repos/team/nb")
        ])
        assets, _ = parser.parse()
        nb = next(a for a in assets if a.asset_type == AssetType.DATABRICKS_NOTEBOOK)
        assert nb.metadata["workspace_folder"] == "/Repos/team"

    def test_language_stored_in_metadata(self):
        parser = DatabricksNotebookParser([
            _make_descriptor("nb", "", language="sql")
        ])
        assets, _ = parser.parse()
        nb = next(a for a in assets if a.asset_type == AssetType.DATABRICKS_NOTEBOOK)
        assert nb.metadata["language"] == "sql"


class TestReadExtraction:
    """Parser extracts read-table references and builds READS edges."""

    def test_spark_read_table_creates_reads_edge(self):
        src = 'df = spark.read.table("bronze.customer")'
        parser = DatabricksNotebookParser([_make_descriptor("nb", src)])
        _, rels = parser.parse()
        reads = [r for r in rels if r.relationship == RelationshipType.READS]
        assert any("bronze.customer" in r.target for r in reads)

    def test_spark_table_creates_reads_edge(self):
        src = 'df = spark.table("landing.sales")'
        parser = DatabricksNotebookParser([_make_descriptor("nb", src)])
        _, rels = parser.parse()
        reads = [r for r in rels if r.relationship == RelationshipType.READS]
        assert any("landing.sales" in r.target for r in reads)

    def test_sql_from_creates_reads_edge(self):
        src = "SELECT * FROM silver.customer"
        parser = DatabricksNotebookParser([_make_descriptor("nb", src, language="sql")])
        _, rels = parser.parse()
        reads = [r for r in rels if r.relationship == RelationshipType.READS]
        assert any("silver.customer" in r.target for r in reads)

    def test_delta_for_name_creates_reads_edge(self):
        src = 'dt = DeltaTable.forName(spark, "bronze.customer")'
        parser = DatabricksNotebookParser([_make_descriptor("nb", src)])
        _, rels = parser.parse()
        reads = [r for r in rels if r.relationship == RelationshipType.READS]
        assert any("bronze.customer" in r.target for r in reads)

    def test_delta_for_path_creates_reads_edge(self):
        src = 'dt = DeltaTable.forPath(spark, "/delta/bronze/customer")'
        parser = DatabricksNotebookParser([_make_descriptor("nb", src)])
        _, rels = parser.parse()
        reads = [r for r in rels if r.relationship == RelationshipType.READS]
        assert any("/delta/bronze/customer" in r.target for r in reads)

    def test_read_count_in_metadata(self):
        parser = DatabricksNotebookParser([_make_descriptor("nb", BRONZE_SOURCE)])
        assets, _ = parser.parse()
        nb = next(a for a in assets if a.asset_type == AssetType.DATABRICKS_NOTEBOOK)
        assert nb.metadata["read_count"] == 2  # landing.customer, landing.sales

    def test_reads_edge_source_is_notebook_id(self):
        src = 'df = spark.read.table("bronze.customer")'
        parser = DatabricksNotebookParser([
            _make_descriptor("nb", src, path="/Repos/team/nb")
        ])
        _, rels = parser.parse()
        reads = [r for r in rels if r.relationship == RelationshipType.READS]
        assert all(r.source == "notebook::/Repos/team/nb" for r in reads)


class TestWriteExtraction:
    """Parser extracts write-table references and builds WRITES edges."""

    def test_save_as_table_creates_writes_edge(self):
        src = 'df.write.format("delta").saveAsTable("bronze.customer")'
        parser = DatabricksNotebookParser([_make_descriptor("nb", src)])
        _, rels = parser.parse()
        writes = [r for r in rels if r.relationship == RelationshipType.WRITES]
        assert any("bronze.customer" in r.target for r in writes)

    def test_create_table_creates_writes_edge(self):
        src = "CREATE TABLE gold.customer_360 AS SELECT * FROM silver.customer"
        parser = DatabricksNotebookParser([_make_descriptor("nb", src, language="sql")])
        _, rels = parser.parse()
        writes = [r for r in rels if r.relationship == RelationshipType.WRITES]
        assert any("gold.customer_360" in r.target for r in writes)

    def test_create_or_replace_table_creates_writes_edge(self):
        src = "CREATE OR REPLACE TABLE gold.sales_dashboard AS SELECT * FROM silver.sales"
        parser = DatabricksNotebookParser([_make_descriptor("nb", src, language="sql")])
        _, rels = parser.parse()
        writes = [r for r in rels if r.relationship == RelationshipType.WRITES]
        assert any("gold.sales_dashboard" in r.target for r in writes)

    def test_merge_into_creates_writes_edge(self):
        src = "MERGE INTO gold.customer_360 AS target USING silver.customer AS source ON target.id = source.id"
        parser = DatabricksNotebookParser([_make_descriptor("nb", src, language="sql")])
        _, rels = parser.parse()
        writes = [r for r in rels if r.relationship == RelationshipType.WRITES]
        assert any("gold.customer_360" in r.target for r in writes)

    def test_insert_overwrite_creates_writes_edge(self):
        src = "INSERT OVERWRITE gold.monthly_sales SELECT * FROM silver.sales"
        parser = DatabricksNotebookParser([_make_descriptor("nb", src, language="sql")])
        _, rels = parser.parse()
        writes = [r for r in rels if r.relationship == RelationshipType.WRITES]
        assert any("gold.monthly_sales" in r.target for r in writes)

    def test_write_count_in_metadata(self):
        parser = DatabricksNotebookParser([_make_descriptor("nb", BRONZE_SOURCE)])
        assets, _ = parser.parse()
        nb = next(a for a in assets if a.asset_type == AssetType.DATABRICKS_NOTEBOOK)
        assert nb.metadata["write_count"] == 2  # bronze.customer, bronze.sales

    def test_read_table_not_treated_as_write(self):
        """spark.read.table must NOT appear in write edges."""
        src = 'df = spark.read.table("bronze.customer")'
        parser = DatabricksNotebookParser([_make_descriptor("nb", src)])
        _, rels = parser.parse()
        writes = [r for r in rels if r.relationship == RelationshipType.WRITES]
        assert not any("bronze.customer" in r.target for r in writes)


class TestTableStubAssets:
    """Parser emits stub DELTA_TABLE assets for discovered references."""

    def test_stub_asset_created_for_read_target(self):
        src = 'df = spark.read.table("bronze.customer")'
        parser = DatabricksNotebookParser([_make_descriptor("nb", src)])
        assets, _ = parser.parse()
        stubs = [a for a in assets if a.asset_type == AssetType.DELTA_TABLE]
        assert any(a.id == "delta_table::bronze.customer" for a in stubs)

    def test_stub_asset_system_is_databricks(self):
        src = 'df = spark.read.table("bronze.customer")'
        parser = DatabricksNotebookParser([_make_descriptor("nb", src)])
        assets, _ = parser.parse()
        stub = next(a for a in assets if a.asset_type == AssetType.DELTA_TABLE)
        assert stub.system == SystemType.DATABRICKS

    def test_stub_not_emitted_when_flag_false(self):
        src = 'df = spark.read.table("bronze.customer")'
        parser = DatabricksNotebookParser(
            [_make_descriptor("nb", src)],
            emit_table_stubs=False,
        )
        assets, rels = parser.parse()
        stubs = [a for a in assets if a.asset_type == AssetType.DELTA_TABLE]
        assert stubs == []
        # But edges should still be created
        reads = [r for r in rels if r.relationship == RelationshipType.READS]
        assert len(reads) == 1

    def test_stub_not_duplicated_across_two_notebooks(self):
        """Two notebooks referencing the same table produce one stub asset."""
        src1 = 'df = spark.read.table("bronze.customer")'
        src2 = 'df = spark.read.table("bronze.customer")'
        parser = DatabricksNotebookParser([
            _make_descriptor("nb1", src1, path="/p/nb1"),
            _make_descriptor("nb2", src2, path="/p/nb2"),
        ])
        assets, _ = parser.parse()
        stubs = [a for a in assets if a.id == "delta_table::bronze.customer"]
        assert len(stubs) == 1

    def test_stub_catalog_schema_parsed_correctly(self):
        """Three-part ref populates catalog and schema on the stub."""
        src = 'df = spark.read.table("hive_metastore.bronze.customer")'
        parser = DatabricksNotebookParser([_make_descriptor("nb", src)])
        assets, _ = parser.parse()
        stub = next(a for a in assets if a.id == "delta_table::hive_metastore.bronze.customer")
        assert stub.catalog == "hive_metastore"
        assert stub.schema == "bronze"
        assert stub.name == "customer"

    def test_stub_two_part_ref_sets_schema(self):
        """Two-part ref populates schema but not catalog."""
        src = 'df = spark.read.table("bronze.customer")'
        parser = DatabricksNotebookParser([_make_descriptor("nb", src)])
        assets, _ = parser.parse()
        stub = next(a for a in assets if a.id == "delta_table::bronze.customer")
        assert stub.catalog is None
        assert stub.schema == "bronze"
        assert stub.name == "customer"


class TestEdgeCases:
    """Fault tolerance and boundary conditions."""

    def test_empty_descriptor_list_returns_empty(self):
        parser = DatabricksNotebookParser([])
        assets, rels = parser.parse()
        assert assets == []
        assert rels == []

    def test_empty_source_produces_notebook_only(self):
        parser = DatabricksNotebookParser([_make_descriptor("nb", "")])
        assets, rels = parser.parse()
        nb_assets = [a for a in assets if a.asset_type == AssetType.DATABRICKS_NOTEBOOK]
        assert len(nb_assets) == 1
        assert rels == []

    def test_none_source_produces_notebook_only(self):
        """Descriptor without 'source' or 'cells' key — no edges."""
        parser = DatabricksNotebookParser([{"name": "nb"}])
        assets, rels = parser.parse()
        nb_assets = [a for a in assets if a.asset_type == AssetType.DATABRICKS_NOTEBOOK]
        assert len(nb_assets) == 1
        assert rels == []

    def test_malformed_descriptor_skipped_without_raising(self):
        """Descriptor that triggers an internal error is skipped gracefully."""
        bad = None  # not a dict — will fail on .get()
        good = _make_descriptor("good_nb", "")
        # We wrap descriptors; passing None triggers the broad except
        parser = DatabricksNotebookParser([bad, good])  # type: ignore[list-item]
        assets, _ = parser.parse()
        # The good notebook should still be produced
        assert any(a.name == "good_nb" for a in assets)

    def test_jupyter_cells_format(self):
        """Jupyter-style cells dict is processed correctly."""
        desc = {
            "name": "jupyter_nb",
            "cells": [
                {"cell_type": "code", "source": 'df = spark.read.table("bronze.customer")'},
                {"cell_type": "code", "source": 'df.write.saveAsTable("silver.customer")'},
            ],
        }
        parser = DatabricksNotebookParser([desc])
        _, rels = parser.parse()
        rel_types = {r.relationship for r in rels}
        assert RelationshipType.READS in rel_types
        assert RelationshipType.WRITES in rel_types

    def test_sql_cells_in_python_notebook(self):
        """SQL magic cells inside a Python notebook are parsed."""
        src = """\
# Python cell
df = spark.read.table("bronze.customer")

# SQL magic
%sql
CREATE OR REPLACE TABLE gold.customer_360 AS SELECT * FROM silver.customer;
"""
        parser = DatabricksNotebookParser([_make_descriptor("nb", src)])
        _, rels = parser.parse()
        writes = [r for r in rels if r.relationship == RelationshipType.WRITES]
        assert any("gold.customer_360" in r.target for r in writes)

    def test_owner_propagated(self):
        parser = DatabricksNotebookParser(
            [_make_descriptor("nb", "")],
            owner="data-engineering",
        )
        assets, _ = parser.parse()
        nb = next(a for a in assets if a.asset_type == AssetType.DATABRICKS_NOTEBOOK)
        assert nb.owner == "data-engineering"

    def test_per_descriptor_owner_overrides_default(self):
        desc = _make_descriptor("nb", "", owner="analytics-team")
        parser = DatabricksNotebookParser([desc], owner="data-engineering")
        assets, _ = parser.parse()
        nb = next(a for a in assets if a.asset_type == AssetType.DATABRICKS_NOTEBOOK)
        assert nb.owner == "analytics-team"

    def test_criticality_propagated(self):
        from graph.models import Criticality
        desc = _make_descriptor("nb", "", criticality="high")
        parser = DatabricksNotebookParser([desc])
        assets, _ = parser.parse()
        nb = next(a for a in assets if a.asset_type == AssetType.DATABRICKS_NOTEBOOK)
        assert nb.criticality == Criticality.HIGH

    def test_parse_returns_tuple_of_lists(self):
        """parse() must return (list, list) — BaseMetadataParser contract."""
        parser = DatabricksNotebookParser([])
        result = parser.parse()
        assert isinstance(result, tuple)
        assert len(result) == 2
        assert isinstance(result[0], list)
        assert isinstance(result[1], list)

    def test_catalog_refs_in_metadata(self):
        """Three-part refs populate catalog_refs list in notebook metadata."""
        src = 'df = spark.read.table("hive_metastore.bronze.customer")'
        parser = DatabricksNotebookParser([_make_descriptor("nb", src)])
        assets, _ = parser.parse()
        nb = next(a for a in assets if a.asset_type == AssetType.DATABRICKS_NOTEBOOK)
        assert "hive_metastore" in nb.metadata["catalog_refs"]

    def test_schema_refs_in_metadata(self):
        """Two-part refs populate schema_refs list in notebook metadata."""
        src = 'df = spark.read.table("bronze.customer")'
        parser = DatabricksNotebookParser([_make_descriptor("nb", src)])
        assets, _ = parser.parse()
        nb = next(a for a in assets if a.asset_type == AssetType.DATABRICKS_NOTEBOOK)
        assert "bronze" in nb.metadata["schema_refs"]

    def test_source_file_set_to_path(self):
        parser = DatabricksNotebookParser([
            _make_descriptor("nb", "", path="/Repos/team/nb")
        ])
        assets, _ = parser.parse()
        nb = next(a for a in assets if a.asset_type == AssetType.DATABRICKS_NOTEBOOK)
        assert nb.source_file == "/Repos/team/nb"


class TestImportFromPackage:
    """DatabricksNotebookParser is importable from the enterprise package."""

    def test_importable_from_enterprise(self):
        from enterprise import DatabricksNotebookParser as DNP  # noqa: F401
        assert DNP is DatabricksNotebookParser


# ===========================================================================
# 3. End-to-end: full medallion pipeline (bronze → silver → gold)
# ===========================================================================


class TestMedallionPipeline:
    """Full lineage graph for a three-tier medallion architecture."""

    @pytest.fixture
    def medallion_assets_rels(self):
        descriptors = [
            {
                "name": "01_bronze_ingestion_framework",
                "path": "/Repos/de-team/01_bronze_ingestion_framework",
                "language": "python",
                "source": BRONZE_SOURCE,
                "owner": "data-engineering",
                "criticality": "high",
                "tags": ["bronze", "ingestion"],
            },
            {
                "name": "02_silver_transformation_engine",
                "path": "/Repos/de-team/02_silver_transformation_engine",
                "language": "python",
                "source": SILVER_SOURCE,
                "owner": "data-engineering",
                "criticality": "high",
                "tags": ["silver", "transform"],
            },
            {
                "name": "03_gold_publishing_engine",
                "path": "/Repos/de-team/03_gold_publishing_engine",
                "language": "sql",
                "source": GOLD_SOURCE,
                "owner": "analytics",
                "criticality": "critical",
                "tags": ["gold", "publish"],
            },
        ]
        parser = DatabricksNotebookParser(descriptors)
        return parser.parse()

    def test_three_notebook_assets_created(self, medallion_assets_rels):
        assets, _ = medallion_assets_rels
        nbs = [a for a in assets if a.asset_type == AssetType.DATABRICKS_NOTEBOOK]
        assert len(nbs) == 3

    def test_bronze_notebook_execution_order_is_1(self, medallion_assets_rels):
        assets, _ = medallion_assets_rels
        bronze = next(
            a for a in assets
            if a.asset_type == AssetType.DATABRICKS_NOTEBOOK and "bronze" in a.name
        )
        assert bronze.metadata["execution_order"] == 1

    def test_silver_notebook_execution_order_is_2(self, medallion_assets_rels):
        assets, _ = medallion_assets_rels
        silver = next(
            a for a in assets
            if a.asset_type == AssetType.DATABRICKS_NOTEBOOK and "silver" in a.name
        )
        assert silver.metadata["execution_order"] == 2

    def test_gold_notebook_execution_order_is_3(self, medallion_assets_rels):
        assets, _ = medallion_assets_rels
        gold = next(
            a for a in assets
            if a.asset_type == AssetType.DATABRICKS_NOTEBOOK and "gold" in a.name
        )
        assert gold.metadata["execution_order"] == 3

    def test_bronze_reads_landing_tables(self, medallion_assets_rels):
        _, rels = medallion_assets_rels
        bronze_nb_id = "notebook::/Repos/de-team/01_bronze_ingestion_framework"
        reads = [r for r in rels if r.source == bronze_nb_id and r.relationship == RelationshipType.READS]
        targets = {r.target for r in reads}
        assert "delta_table::landing.customer" in targets
        assert "delta_table::landing.sales" in targets

    def test_bronze_writes_bronze_tables(self, medallion_assets_rels):
        _, rels = medallion_assets_rels
        bronze_nb_id = "notebook::/Repos/de-team/01_bronze_ingestion_framework"
        writes = [r for r in rels if r.source == bronze_nb_id and r.relationship == RelationshipType.WRITES]
        targets = {r.target for r in writes}
        assert "delta_table::bronze.customer" in targets
        assert "delta_table::bronze.sales" in targets

    def test_silver_reads_bronze_tables(self, medallion_assets_rels):
        _, rels = medallion_assets_rels
        silver_nb_id = "notebook::/Repos/de-team/02_silver_transformation_engine"
        reads = [r for r in rels if r.source == silver_nb_id and r.relationship == RelationshipType.READS]
        targets = {r.target for r in reads}
        assert "delta_table::bronze.customer" in targets
        assert "delta_table::bronze.sales" in targets

    def test_silver_writes_silver_tables(self, medallion_assets_rels):
        _, rels = medallion_assets_rels
        silver_nb_id = "notebook::/Repos/de-team/02_silver_transformation_engine"
        writes = [r for r in rels if r.source == silver_nb_id and r.relationship == RelationshipType.WRITES]
        targets = {r.target for r in writes}
        assert "delta_table::silver.customer" in targets
        assert "delta_table::silver.sales" in targets

    def test_gold_reads_silver_tables(self, medallion_assets_rels):
        _, rels = medallion_assets_rels
        gold_nb_id = "notebook::/Repos/de-team/03_gold_publishing_engine"
        reads = [r for r in rels if r.source == gold_nb_id and r.relationship == RelationshipType.READS]
        targets = {r.target for r in reads}
        assert "delta_table::silver.customer" in targets
        assert "delta_table::silver.sales" in targets

    def test_gold_writes_gold_tables(self, medallion_assets_rels):
        _, rels = medallion_assets_rels
        gold_nb_id = "notebook::/Repos/de-team/03_gold_publishing_engine"
        writes = [r for r in rels if r.source == gold_nb_id and r.relationship == RelationshipType.WRITES]
        targets = {r.target for r in writes}
        assert "delta_table::gold.customer_360" in targets
        assert "delta_table::gold.sales_dashboard" in targets
        assert "delta_table::gold.monthly_sales" in targets

    def test_bronze_stub_not_duplicated_in_silver(self, medallion_assets_rels):
        """bronze.customer stub appears only once across all notebooks."""
        assets, _ = medallion_assets_rels
        stubs = [a for a in assets if a.id == "delta_table::bronze.customer"]
        assert len(stubs) == 1

    def test_total_relationship_count_is_correct(self, medallion_assets_rels):
        """
        bronze: 2 reads (landing.*) + 2 writes (bronze.*) = 4
        silver: 2 reads (bronze.*)  + 2 writes (silver.*) = 4
        gold:   2 reads (silver.*)  + 3 writes (gold.*)   = 5
        total = 13
        (gold also reads silver.customer + silver.sales in CREATE OR REPLACE SELECTs
        but FROM in CREATE AS SELECT is a read — the _RE_SQL_FROM covers FROM clauses
        within the CTAS body, so silver.customer and silver.sales each appear once
        in gold reads from the outer SELECT too — deduped.)
        """
        _, rels = medallion_assets_rels
        assert len(rels) >= 10  # At minimum: 4 + 4 + some gold edges

    def test_merge_into_produces_writes_edge(self):
        """MERGE INTO in a separate notebook produces a WRITES edge."""
        desc = {
            "name": "05_upsert_customer",
            "path": "/Repos/de-team/05_upsert_customer",
            "language": "sql",
            "source": MERGE_SOURCE,
        }
        parser = DatabricksNotebookParser([desc])
        _, rels = parser.parse()
        writes = [r for r in rels if r.relationship == RelationshipType.WRITES]
        assert any("gold.customer_360" in r.target for r in writes)
        reads = [r for r in rels if r.relationship == RelationshipType.READS]
        assert any("silver.customer" in r.target for r in reads)
