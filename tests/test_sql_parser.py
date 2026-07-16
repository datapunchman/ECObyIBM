"""
tests.test_sql_parser
=====================
Comprehensive test suite for :class:`~enterprise.sql_parser.SQLMetadataParser`.

Covers:
- Table parsing (single, multiple, schema-qualified, bare names)
- View parsing (with FROM/JOIN table references → READS edges)
- Stored procedure parsing (body references → READS edges)
- Function parsing (scalar and table-valued → READS edges)
- Cross-file table reference resolution (tables.sql → views.sql)
- Schema extraction (dbo.obj_name → schema="dbo", name="obj_name")
- Quoted identifiers ([dbo].[table], `schema`.`obj`, "schema"."obj")
- OR REPLACE variants (CREATE OR REPLACE VIEW …)
- Multiple statements in one string
- Duplicate asset ID skipping (WARNING path)
- Empty SQL input → ([], [])
- Comment stripping (-- and /* */)
- Non-DDL statements silently ignored (INSERT, ALTER, DROP)
- Missing directory → ([], [])
- Missing single file → ([], [])
- Directory with no .sql files → ([], [])
- Real metadata/sql/ directory round-trip (integration smoke-test)
- Asset field verification (asset_type, system, schema, name, id)
- Relationship field verification (source, target, relationship type)
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from enterprise.sql_parser import (
    SQLMetadataParser,
    _asset_id,
    _extract_table_refs,
    _normalise_name,
    _schema_and_name,
    _split_statements,
    _strip_comments,
)
from graph.models import AssetType, RelationshipType, SystemType


# ===========================================================================
# Unit helpers
# ===========================================================================


class TestStripComments:
    def test_removes_line_comment(self):
        sql = "SELECT 1 -- this is a comment\nSELECT 2"
        result = _strip_comments(sql)
        assert "this is a comment" not in result
        assert "SELECT 2" in result

    def test_removes_block_comment(self):
        sql = "SELECT /* remove me */ 1"
        result = _strip_comments(sql)
        assert "remove me" not in result
        assert "SELECT" in result

    def test_multiline_block_comment(self):
        sql = "/* line1\nline2 */ SELECT 1"
        result = _strip_comments(sql)
        assert "line1" not in result
        assert "SELECT 1" in result

    def test_no_comments_unchanged_structurally(self):
        sql = "CREATE TABLE foo (id INT)"
        result = _strip_comments(sql)
        assert "CREATE TABLE foo" in result

    def test_empty_string(self):
        assert _strip_comments("") == ""


class TestNormaliseName:
    def test_square_brackets(self):
        assert _normalise_name("[dbo].[dim_customer]") == "dbo.dim_customer"

    def test_backticks(self):
        assert _normalise_name("`schema`.`table`") == "schema.table"

    def test_double_quotes(self):
        assert _normalise_name('"schema"."table"') == "schema.table"

    def test_lowercase(self):
        assert _normalise_name("DBO.DIM_CUSTOMER") == "dbo.dim_customer"

    def test_bare_name(self):
        assert _normalise_name("orders") == "orders"

    def test_mixed(self):
        assert _normalise_name("[DBO].dim_customer") == "dbo.dim_customer"


class TestAssetId:
    def test_schema_qualified(self):
        assert _asset_id("dbo.dim_customer") == "sql::dbo.dim_customer"

    def test_bare(self):
        assert _asset_id("orders") == "sql::orders"


class TestSchemaAndName:
    def test_two_part(self):
        schema, name = _schema_and_name("dbo.dim_customer")
        assert schema == "dbo"
        assert name == "dim_customer"

    def test_three_part(self):
        schema, name = _schema_and_name("catalog.dbo.orders")
        assert schema == "catalog.dbo"
        assert name == "orders"

    def test_bare(self):
        schema, name = _schema_and_name("orders")
        assert schema is None
        assert name == "orders"


class TestSplitStatements:
    def test_two_statements(self):
        sql = "CREATE TABLE a (id INT); CREATE TABLE b (id INT);"
        parts = _split_statements(sql)
        assert len(parts) == 2

    def test_no_trailing_semicolon(self):
        sql = "CREATE TABLE a (id INT)"
        parts = _split_statements(sql)
        assert len(parts) == 1

    def test_empty_string(self):
        assert _split_statements("") == []

    def test_only_whitespace(self):
        assert _split_statements("   \n  ;  \n  ") == []

    def test_strips_comments_before_split(self):
        sql = "-- comment\nCREATE TABLE a (id INT); -- another\nCREATE TABLE b (id INT)"
        parts = _split_statements(sql)
        assert len(parts) == 2


class TestExtractTableRefs:
    def test_from_clause(self):
        body = "SELECT * FROM dbo.orders"
        known = {"sql::dbo.orders"}
        refs = _extract_table_refs(body, known)
        assert refs == ["sql::dbo.orders"]

    def test_join_clause(self):
        body = "SELECT * FROM dbo.orders INNER JOIN dbo.customers ON 1=1"
        known = {"sql::dbo.orders", "sql::dbo.customers"}
        refs = _extract_table_refs(body, known)
        assert set(refs) == {"sql::dbo.orders", "sql::dbo.customers"}

    def test_unknown_ref_ignored(self):
        body = "SELECT * FROM dbo.unknown_table"
        known = {"sql::dbo.orders"}
        refs = _extract_table_refs(body, known)
        assert refs == []

    def test_deduplication(self):
        body = "SELECT * FROM dbo.orders AS o JOIN dbo.orders AS o2 ON 1=1"
        known = {"sql::dbo.orders"}
        refs = _extract_table_refs(body, known)
        assert refs == ["sql::dbo.orders"]

    def test_empty_body(self):
        refs = _extract_table_refs("", {"sql::dbo.orders"})
        assert refs == []


# ===========================================================================
# SQLMetadataParser — inline SQL string tests
# ===========================================================================


class TestParserTables:
    def test_single_table(self):
        sql = "CREATE TABLE dbo.orders (id INT NOT NULL);"
        p = SQLMetadataParser(source=sql)
        assets, rels = p.parse()
        assert len(assets) == 1
        a = assets[0]
        assert a.asset_type == AssetType.DATABASE_TABLE
        assert a.system == SystemType.DATABASE
        assert a.name == "orders"
        assert a.schema == "dbo"
        assert a.id == "sql::dbo.orders"
        assert rels == []

    def test_multiple_tables(self):
        sql = textwrap.dedent("""\
            CREATE TABLE dbo.dim_customer (id INT);
            CREATE TABLE dbo.dim_product (id INT);
            CREATE TABLE dbo.fact_sales (id INT);
        """)
        p = SQLMetadataParser(source=sql)
        assets, rels = p.parse()
        assert len(assets) == 3
        names = {a.name for a in assets}
        assert names == {"dim_customer", "dim_product", "fact_sales"}
        assert rels == []

    def test_bare_table_name(self):
        sql = "CREATE TABLE orders (id INT);"
        p = SQLMetadataParser(source=sql)
        assets, _ = p.parse()
        assert len(assets) == 1
        assert assets[0].schema is None
        assert assets[0].name == "orders"
        assert assets[0].id == "sql::orders"

    def test_table_case_insensitive_keyword(self):
        sql = "create table dbo.customers (id int);"
        p = SQLMetadataParser(source=sql)
        assets, _ = p.parse()
        assert len(assets) == 1
        assert assets[0].name == "customers"

    def test_table_if_not_exists(self):
        sql = "CREATE TABLE IF NOT EXISTS dbo.orders (id INT);"
        p = SQLMetadataParser(source=sql)
        assets, _ = p.parse()
        assert len(assets) == 1
        assert assets[0].name == "orders"

    def test_duplicate_table_skipped(self):
        sql = textwrap.dedent("""\
            CREATE TABLE dbo.orders (id INT);
            CREATE TABLE dbo.orders (name VARCHAR(10));
        """)
        p = SQLMetadataParser(source=sql)
        assets, _ = p.parse()
        assert len(assets) == 1

    def test_table_metadata_field(self):
        sql = "CREATE TABLE dbo.orders (id INT);"
        p = SQLMetadataParser(source=sql)
        assets, _ = p.parse()
        assert assets[0].metadata.get("sql_type") == "table"

    def test_quoted_identifiers_square_brackets(self):
        sql = "CREATE TABLE [dbo].[fact_sales] (id INT);"
        p = SQLMetadataParser(source=sql)
        assets, _ = p.parse()
        assert len(assets) == 1
        assert assets[0].name == "fact_sales"
        assert assets[0].schema == "dbo"

    def test_quoted_identifiers_backticks(self):
        sql = "CREATE TABLE `myschema`.`orders` (id INT);"
        p = SQLMetadataParser(source=sql)
        assets, _ = p.parse()
        assert assets[0].schema == "myschema"
        assert assets[0].name == "orders"


class TestParserViews:
    def _make_view_sql(self, tables=True):
        parts = []
        if tables:
            parts.append("CREATE TABLE dbo.dim_customer (id INT);")
            parts.append("CREATE TABLE dbo.dim_sales_territory (id INT);")
        parts.append(textwrap.dedent("""\
            CREATE VIEW dbo.vw_customer_360 AS
            SELECT c.id, t.territory_name
            FROM dbo.dim_customer AS c
            LEFT JOIN dbo.dim_sales_territory AS t ON c.id = t.id;
        """))
        return "\n".join(parts)

    def test_view_asset_type(self):
        sql = self._make_view_sql()
        p = SQLMetadataParser(source=sql)
        assets, _ = p.parse()
        views = [a for a in assets if a.asset_type == AssetType.SQL_VIEW]
        assert len(views) == 1
        assert views[0].name == "vw_customer_360"

    def test_view_schema(self):
        sql = self._make_view_sql()
        p = SQLMetadataParser(source=sql)
        assets, _ = p.parse()
        view = next(a for a in assets if a.asset_type == AssetType.SQL_VIEW)
        assert view.schema == "dbo"

    def test_view_reads_edges(self):
        # TABLE ──FEEDS──> VIEW direction (Fix 4: reversed for downstream BFS)
        sql = self._make_view_sql()
        p = SQLMetadataParser(source=sql)
        assets, rels = p.parse()
        view_id = "sql::dbo.vw_customer_360"
        feeds = [r for r in rels if r.target == view_id]
        sources = {r.source for r in feeds}
        assert "sql::dbo.dim_customer" in sources
        assert "sql::dbo.dim_sales_territory" in sources

    def test_view_reads_edge_type(self):
        # Edges are now FEEDS (TABLE→VIEW), not READS (VIEW→TABLE)
        sql = self._make_view_sql()
        p = SQLMetadataParser(source=sql)
        _, rels = p.parse()
        for r in rels:
            assert r.relationship == RelationshipType.FEEDS

    def test_view_no_reads_when_tables_unknown(self):
        # View only, no tables defined → no READS edges possible
        sql = textwrap.dedent("""\
            CREATE VIEW dbo.vw_customer_360 AS
            SELECT * FROM dbo.dim_customer;
        """)
        p = SQLMetadataParser(source=sql)
        _, rels = p.parse()
        assert rels == []

    def test_view_or_replace(self):
        # FEEDS edge: source=table, target=view
        sql = textwrap.dedent("""\
            CREATE TABLE dbo.orders (id INT);
            CREATE OR REPLACE VIEW dbo.vw_orders AS
            SELECT * FROM dbo.orders;
        """)
        p = SQLMetadataParser(source=sql)
        assets, rels = p.parse()
        views = [a for a in assets if a.asset_type == AssetType.SQL_VIEW]
        assert len(views) == 1
        assert len(rels) == 1
        assert rels[0].source == "sql::dbo.orders"
        assert rels[0].target == "sql::dbo.vw_orders"

    def test_duplicate_view_skipped(self):
        sql = textwrap.dedent("""\
            CREATE VIEW dbo.vw_orders AS SELECT * FROM orders;
            CREATE VIEW dbo.vw_orders AS SELECT * FROM orders;
        """)
        p = SQLMetadataParser(source=sql)
        assets, _ = p.parse()
        views = [a for a in assets if a.asset_type == AssetType.SQL_VIEW]
        assert len(views) == 1

    def test_view_system_type(self):
        sql = "CREATE VIEW dbo.vw_test AS SELECT 1 AS col;"
        p = SQLMetadataParser(source=sql)
        assets, _ = p.parse()
        assert all(a.system == SystemType.DATABASE for a in assets)

    def test_view_metadata_field(self):
        sql = "CREATE VIEW dbo.vw_test AS SELECT 1;"
        p = SQLMetadataParser(source=sql)
        assets, _ = p.parse()
        assert assets[0].metadata.get("sql_type") == "view"

    def test_view_multiple(self):
        sql = textwrap.dedent("""\
            CREATE TABLE dbo.fact_sales (id INT);
            CREATE VIEW dbo.vw_a AS SELECT * FROM dbo.fact_sales;
            CREATE VIEW dbo.vw_b AS SELECT * FROM dbo.fact_sales;
        """)
        p = SQLMetadataParser(source=sql)
        assets, rels = p.parse()
        views = [a for a in assets if a.asset_type == AssetType.SQL_VIEW]
        assert len(views) == 2
        assert len(rels) == 2


class TestParserProcedures:
    def _make_proc_sql(self):
        return textwrap.dedent("""\
            CREATE TABLE dbo.fact_internet_sales (id INT);
            CREATE TABLE dbo.dim_customer (id INT);
            CREATE TABLE dbo.dim_product (id INT);
            CREATE PROCEDURE dbo.usp_get_customer_orders
                @customer_id VARCHAR(20)
            AS
            BEGIN
                SELECT s.id, p.id
                FROM dbo.fact_internet_sales AS s
                INNER JOIN dbo.dim_customer AS c ON s.id = c.id
                INNER JOIN dbo.dim_product AS p ON s.id = p.id;
            END;
        """)

    def test_procedure_asset_type(self):
        sql = self._make_proc_sql()
        p = SQLMetadataParser(source=sql)
        assets, _ = p.parse()
        procs = [a for a in assets if a.asset_type == AssetType.STORED_PROCEDURE]
        assert len(procs) == 1
        assert procs[0].name == "usp_get_customer_orders"

    def test_procedure_schema(self):
        sql = self._make_proc_sql()
        p = SQLMetadataParser(source=sql)
        assets, _ = p.parse()
        proc = next(a for a in assets if a.asset_type == AssetType.STORED_PROCEDURE)
        assert proc.schema == "dbo"

    def test_procedure_reads_edges(self):
        # TABLE ──FEEDS──> PROCEDURE direction
        sql = self._make_proc_sql()
        p = SQLMetadataParser(source=sql)
        _, rels = p.parse()
        proc_id = "sql::dbo.usp_get_customer_orders"
        feeds = [r for r in rels if r.target == proc_id]
        sources = {r.source for r in feeds}
        assert "sql::dbo.fact_internet_sales" in sources
        assert "sql::dbo.dim_customer" in sources
        assert "sql::dbo.dim_product" in sources

    def test_procedure_reads_edge_type(self):
        # Edges are now FEEDS (TABLE→PROC), not READS (PROC→TABLE)
        sql = self._make_proc_sql()
        p = SQLMetadataParser(source=sql)
        _, rels = p.parse()
        for r in rels:
            assert r.relationship == RelationshipType.FEEDS

    def test_procedure_no_reads_when_no_tables(self):
        sql = textwrap.dedent("""\
            CREATE PROCEDURE dbo.usp_noop AS
            BEGIN
                SELECT * FROM dbo.some_table;
            END;
        """)
        p = SQLMetadataParser(source=sql)
        _, rels = p.parse()
        assert rels == []

    def test_procedure_or_replace(self):
        sql = textwrap.dedent("""\
            CREATE TABLE dbo.orders (id INT);
            CREATE OR REPLACE PROCEDURE dbo.usp_proc AS
            BEGIN
                SELECT * FROM dbo.orders;
            END;
        """)
        p = SQLMetadataParser(source=sql)
        _, rels = p.parse()
        assert len(rels) == 1

    def test_procedure_metadata_field(self):
        sql = textwrap.dedent("""\
            CREATE PROCEDURE dbo.usp_test AS BEGIN SELECT 1; END;
        """)
        p = SQLMetadataParser(source=sql)
        assets, _ = p.parse()
        proc = next(a for a in assets if a.asset_type == AssetType.STORED_PROCEDURE)
        assert proc.metadata.get("sql_type") == "procedure"

    def test_multiple_procedures(self):
        sql = textwrap.dedent("""\
            CREATE TABLE dbo.t (id INT);
            CREATE PROCEDURE dbo.usp_a AS BEGIN SELECT * FROM dbo.t; END;
            CREATE PROCEDURE dbo.usp_b AS BEGIN SELECT * FROM dbo.t; END;
        """)
        p = SQLMetadataParser(source=sql)
        assets, rels = p.parse()
        procs = [a for a in assets if a.asset_type == AssetType.STORED_PROCEDURE]
        assert len(procs) == 2
        assert len(rels) == 2


class TestParserFunctions:
    def _make_fn_sql(self):
        return textwrap.dedent("""\
            CREATE TABLE dbo.fact_internet_sales (id INT);
            CREATE TABLE dbo.dim_date (id INT);
            CREATE FUNCTION dbo.fn_sales_by_date_range
            (
                @start_date DATE,
                @end_date   DATE
            )
            RETURNS TABLE
            AS
            RETURN
            (
                SELECT s.id, d.id
                FROM dbo.fact_internet_sales AS s
                INNER JOIN dbo.dim_date AS d ON s.id = d.id
                WHERE d.id > 0
            );
        """)

    def test_function_asset_type(self):
        sql = self._make_fn_sql()
        p = SQLMetadataParser(source=sql)
        assets, _ = p.parse()
        fns = [a for a in assets if a.asset_type == AssetType.SQL_FUNCTION]
        assert len(fns) == 1
        assert fns[0].name == "fn_sales_by_date_range"

    def test_function_schema(self):
        sql = self._make_fn_sql()
        p = SQLMetadataParser(source=sql)
        assets, _ = p.parse()
        fn = next(a for a in assets if a.asset_type == AssetType.SQL_FUNCTION)
        assert fn.schema == "dbo"

    def test_function_reads_edges(self):
        # TABLE ──FEEDS──> FUNCTION direction
        sql = self._make_fn_sql()
        p = SQLMetadataParser(source=sql)
        _, rels = p.parse()
        fn_id = "sql::dbo.fn_sales_by_date_range"
        feeds = [r for r in rels if r.target == fn_id]
        sources = {r.source for r in feeds}
        assert "sql::dbo.fact_internet_sales" in sources
        assert "sql::dbo.dim_date" in sources

    def test_scalar_function(self):
        # FEEDS edge: source=table, target=function
        sql = textwrap.dedent("""\
            CREATE TABLE dbo.fact_sales (id INT);
            CREATE FUNCTION dbo.fn_clv(@customer_key INT)
            RETURNS DECIMAL(18,2)
            AS
            BEGIN
                DECLARE @clv DECIMAL(18,2);
                SELECT @clv = SUM(s.id)
                FROM dbo.fact_sales AS s
                WHERE s.id = @customer_key;
                RETURN ISNULL(@clv, 0.00);
            END;
        """)
        p = SQLMetadataParser(source=sql)
        assets, rels = p.parse()
        fns = [a for a in assets if a.asset_type == AssetType.SQL_FUNCTION]
        assert len(fns) == 1
        assert len(rels) == 1
        assert rels[0].source == "sql::dbo.fact_sales"
        assert rels[0].target == "sql::dbo.fn_clv"

    def test_function_or_replace(self):
        sql = textwrap.dedent("""\
            CREATE TABLE dbo.orders (id INT);
            CREATE OR REPLACE FUNCTION dbo.fn_test()
            RETURNS INT AS BEGIN RETURN (SELECT COUNT(*) FROM dbo.orders); END;
        """)
        p = SQLMetadataParser(source=sql)
        _, rels = p.parse()
        assert len(rels) == 1

    def test_function_metadata_field(self):
        sql = textwrap.dedent("""\
            CREATE FUNCTION dbo.fn_test() RETURNS INT AS BEGIN RETURN 1; END;
        """)
        p = SQLMetadataParser(source=sql)
        assets, _ = p.parse()
        fn = next(a for a in assets if a.asset_type == AssetType.SQL_FUNCTION)
        assert fn.metadata.get("sql_type") == "function"


# ===========================================================================
# Edge / corner cases
# ===========================================================================


class TestEmptyAndMalformedInputs:
    def test_empty_string(self):
        p = SQLMetadataParser(source="")
        assets, rels = p.parse()
        assert assets == []
        assert rels == []

    def test_comments_only(self):
        sql = "-- Just a comment\n/* block comment */"
        p = SQLMetadataParser(source=sql)
        assets, rels = p.parse()
        assert assets == []
        assert rels == []

    def test_non_ddl_statements_ignored(self):
        sql = textwrap.dedent("""\
            INSERT INTO dbo.orders VALUES (1, 'test');
            ALTER TABLE dbo.orders ADD COLUMN foo INT;
            DROP TABLE dbo.orders;
            SELECT * FROM dbo.orders;
        """)
        p = SQLMetadataParser(source=sql)
        assets, rels = p.parse()
        assert assets == []
        assert rels == []

    def test_mixed_ddl_and_dml(self):
        sql = textwrap.dedent("""\
            CREATE TABLE dbo.orders (id INT);
            INSERT INTO dbo.orders VALUES (1);
            CREATE TABLE dbo.customers (id INT);
        """)
        p = SQLMetadataParser(source=sql)
        assets, _ = p.parse()
        assert len(assets) == 2

    def test_whitespace_only(self):
        p = SQLMetadataParser(source="   \n\t  ")
        assets, rels = p.parse()
        assert assets == []
        assert rels == []


class TestMissingPathInputs:
    def test_missing_directory_returns_empty(self, tmp_path):
        missing = tmp_path / "nonexistent_dir"
        p = SQLMetadataParser(source=missing)
        assets, rels = p.parse()
        assert assets == []
        assert rels == []

    def test_missing_file_returns_empty(self, tmp_path):
        missing = tmp_path / "missing.sql"
        p = SQLMetadataParser(source=missing)
        assets, rels = p.parse()
        assert assets == []
        assert rels == []

    def test_empty_directory_returns_empty(self, tmp_path):
        p = SQLMetadataParser(source=tmp_path)
        assets, rels = p.parse()
        assert assets == []
        assert rels == []

    def test_default_path_missing_returns_empty(self, monkeypatch, tmp_path):
        """Monkeypatch DEFAULT_SQL_DIR to a path that does not exist."""
        monkeypatch.chdir(tmp_path)
        p = SQLMetadataParser(source=None)
        p.DEFAULT_SQL_DIR = tmp_path / "metadata" / "sql"
        assets, rels = p.parse()
        assert assets == []
        assert rels == []


class TestDirectoryLoading:
    def test_single_file_in_directory(self, tmp_path):
        f = tmp_path / "tables.sql"
        f.write_text("CREATE TABLE dbo.orders (id INT);", encoding="utf-8")
        p = SQLMetadataParser(source=tmp_path)
        assets, _ = p.parse()
        assert len(assets) == 1
        assert assets[0].name == "orders"

    def test_tables_loaded_before_views(self, tmp_path):
        """Alphabetical sort ensures tables.sql precedes views.sql."""
        (tmp_path / "tables.sql").write_text(
            "CREATE TABLE dbo.orders (id INT);", encoding="utf-8"
        )
        (tmp_path / "views.sql").write_text(
            "CREATE VIEW dbo.vw_orders AS SELECT * FROM dbo.orders;",
            encoding="utf-8",
        )
        p = SQLMetadataParser(source=tmp_path)
        assets, rels = p.parse()
        views = [a for a in assets if a.asset_type == AssetType.SQL_VIEW]
        assert len(views) == 1
        assert len(rels) == 1
        # FEEDS direction: source=table, target=view
        assert rels[0].source == "sql::dbo.orders"
        assert rels[0].target == "sql::dbo.vw_orders"

    def test_multiple_sql_files_in_dir(self, tmp_path):
        (tmp_path / "a_tables.sql").write_text(
            "CREATE TABLE dbo.t1 (id INT); CREATE TABLE dbo.t2 (id INT);",
            encoding="utf-8",
        )
        (tmp_path / "b_views.sql").write_text(
            "CREATE VIEW dbo.vw1 AS SELECT * FROM dbo.t1;",
            encoding="utf-8",
        )
        p = SQLMetadataParser(source=tmp_path)
        assets, rels = p.parse()
        assert len([a for a in assets if a.asset_type == AssetType.DATABASE_TABLE]) == 2
        assert len([a for a in assets if a.asset_type == AssetType.SQL_VIEW]) == 1
        assert len(rels) == 1


class TestSingleFileLoading:
    def test_single_file_path(self, tmp_path):
        f = tmp_path / "my_tables.sql"
        f.write_text(
            "CREATE TABLE dbo.a (id INT); CREATE TABLE dbo.b (id INT);",
            encoding="utf-8",
        )
        p = SQLMetadataParser(source=f)
        assets, _ = p.parse()
        assert len(assets) == 2

    def test_single_file_empty(self, tmp_path):
        f = tmp_path / "empty.sql"
        f.write_text("", encoding="utf-8")
        p = SQLMetadataParser(source=f)
        assets, rels = p.parse()
        assert assets == []
        assert rels == []

    def test_single_file_bom(self, tmp_path):
        f = tmp_path / "bom.sql"
        f.write_bytes(b"\xef\xbb\xbfCREATE TABLE dbo.orders (id INT);")
        p = SQLMetadataParser(source=f)
        assets, _ = p.parse()
        assert len(assets) == 1
        assert assets[0].name == "orders"


# ===========================================================================
# Mixed DDL in a single string
# ===========================================================================


class TestMixedDDL:
    def test_table_view_proc_function(self):
        # FEEDS direction: source=table, target=view/proc/fn
        sql = textwrap.dedent("""\
            CREATE TABLE dbo.orders (id INT);
            CREATE VIEW dbo.vw_orders AS SELECT * FROM dbo.orders;
            CREATE PROCEDURE dbo.usp_orders AS BEGIN SELECT * FROM dbo.orders; END;
            CREATE FUNCTION dbo.fn_orders() RETURNS INT
            AS BEGIN RETURN (SELECT COUNT(*) FROM dbo.orders); END;
        """)
        p = SQLMetadataParser(source=sql)
        assets, rels = p.parse()

        tables = [a for a in assets if a.asset_type == AssetType.DATABASE_TABLE]
        views = [a for a in assets if a.asset_type == AssetType.SQL_VIEW]
        procs = [a for a in assets if a.asset_type == AssetType.STORED_PROCEDURE]
        fns = [a for a in assets if a.asset_type == AssetType.SQL_FUNCTION]

        assert len(tables) == 1
        assert len(views) == 1
        assert len(procs) == 1
        assert len(fns) == 1
        # Each of view, proc, fn should have a FEEDS edge from orders
        assert len(rels) == 3
        targets = {r.target for r in rels}
        assert "sql::dbo.vw_orders" in targets
        assert "sql::dbo.usp_orders" in targets
        assert "sql::dbo.fn_orders" in targets

    def test_all_edges_point_to_table(self):
        # FEEDS direction: source=table, targets=view/proc
        sql = textwrap.dedent("""\
            CREATE TABLE dbo.orders (id INT);
            CREATE VIEW dbo.vw_orders AS SELECT * FROM dbo.orders;
            CREATE PROCEDURE dbo.usp_orders AS BEGIN SELECT * FROM dbo.orders; END;
        """)
        p = SQLMetadataParser(source=sql)
        _, rels = p.parse()
        for r in rels:
            assert r.source == "sql::dbo.orders"
            assert r.relationship == RelationshipType.FEEDS

    def test_cross_join_view(self):
        # FEEDS: source=each table, target=view
        sql = textwrap.dedent("""\
            CREATE TABLE dbo.a (id INT);
            CREATE TABLE dbo.b (id INT);
            CREATE TABLE dbo.c (id INT);
            CREATE VIEW dbo.vw_abc AS
            SELECT a.id, b.id, c.id
            FROM dbo.a
            JOIN dbo.b ON a.id = b.id
            JOIN dbo.c ON a.id = c.id;
        """)
        p = SQLMetadataParser(source=sql)
        _, rels = p.parse()
        sources = {r.source for r in rels}
        assert sources == {"sql::dbo.a", "sql::dbo.b", "sql::dbo.c"}


# ===========================================================================
# Parser constructor / defaults
# ===========================================================================


class TestParserConstructor:
    def test_owner_propagated_to_assets(self):
        sql = "CREATE TABLE dbo.orders (id INT);"
        p = SQLMetadataParser(source=sql, owner="data-engineering")
        assets, _ = p.parse()
        assert assets[0].owner == "data-engineering"

    def test_default_criticality(self):
        from graph.models import Criticality
        sql = "CREATE TABLE dbo.orders (id INT);"
        p = SQLMetadataParser(source=sql)
        assets, _ = p.parse()
        assert assets[0].criticality == Criticality.MEDIUM

    def test_source_file_field(self):
        sql = "CREATE TABLE dbo.orders (id INT);"
        p = SQLMetadataParser(source=sql)
        assets, _ = p.parse()
        assert assets[0].source_file == "inline_sql"

    def test_source_file_from_directory(self, tmp_path):
        (tmp_path / "tables.sql").write_text(
            "CREATE TABLE dbo.orders (id INT);", encoding="utf-8"
        )
        p = SQLMetadataParser(source=tmp_path)
        assets, _ = p.parse()
        assert assets[0].source_file == "tables.sql"


# ===========================================================================
# Integration smoke-test — real metadata/sql/ directory
# ===========================================================================


class TestRealMetadataSqlDir:
    """Smoke-tests against the actual metadata/sql/ files in the project.

    These tests do not hardcode exact counts; they verify the shape and
    correctness of what the parser produces from the real files.
    """

    @pytest.fixture(autouse=True)
    def _parse_result(self):
        sql_dir = Path("metadata") / "sql"
        if not sql_dir.exists():
            pytest.skip("metadata/sql/ directory not found")
        p = SQLMetadataParser(source=sql_dir)
        self.assets, self.rels = p.parse()

    def test_produces_assets(self):
        assert len(self.assets) > 0

    def test_produces_tables(self):
        tables = [a for a in self.assets if a.asset_type == AssetType.DATABASE_TABLE]
        assert len(tables) >= 5  # dim_customer, dim_product, dim_date, dim_sales_territory, fact_internet_sales

    def test_produces_views(self):
        views = [a for a in self.assets if a.asset_type == AssetType.SQL_VIEW]
        assert len(views) >= 4  # vw_customer_360, vw_monthly_sales, vw_territory_performance, vw_product_sales

    def test_produces_procedures(self):
        procs = [a for a in self.assets if a.asset_type == AssetType.STORED_PROCEDURE]
        assert len(procs) >= 3  # usp_get_customer_orders, usp_refresh_territory_summary, usp_get_top_products

    def test_produces_functions(self):
        fns = [a for a in self.assets if a.asset_type == AssetType.SQL_FUNCTION]
        assert len(fns) >= 3  # fn_customer_lifetime_value, fn_sales_by_date_range, fn_discount_percentage

    def test_produces_reads_relationships(self):
        # Renamed: edges are now FEEDS (TABLE→VIEW/PROC/FN)
        feeds = [r for r in self.rels if r.relationship == RelationshipType.FEEDS]
        assert len(feeds) > 0

    def test_all_reads_source_is_non_table(self):
        # FEEDS source IS the table; target is the downstream consumer
        table_ids = {
            a.id for a in self.assets if a.asset_type == AssetType.DATABASE_TABLE
        }
        for r in self.rels:
            if r.relationship == RelationshipType.FEEDS:
                assert r.source in table_ids, (
                    f"FEEDS source should be a TABLE: {r.source}"
                )

    def test_all_reads_target_is_table(self):
        # FEEDS target is a downstream consumer (view/proc/fn), not a table
        table_ids = {
            a.id for a in self.assets if a.asset_type == AssetType.DATABASE_TABLE
        }
        for r in self.rels:
            if r.relationship == RelationshipType.FEEDS:
                assert r.target not in table_ids, (
                    f"FEEDS target should NOT be a table: {r.target}"
                )

    def test_specific_table_exists(self):
        ids = {a.id for a in self.assets}
        assert "sql::dbo.dim_customer" in ids
        assert "sql::dbo.fact_internet_sales" in ids

    def test_specific_view_exists(self):
        ids = {a.id for a in self.assets}
        assert "sql::dbo.vw_customer_360" in ids
        assert "sql::dbo.vw_monthly_sales" in ids

    def test_specific_procedure_exists(self):
        ids = {a.id for a in self.assets}
        assert "sql::dbo.usp_get_customer_orders" in ids

    def test_specific_function_exists(self):
        ids = {a.id for a in self.assets}
        assert "sql::dbo.fn_customer_lifetime_value" in ids

    def test_view_customer_360_reads_dim_customer(self):
        # FEEDS direction: table→view
        feeds = [
            r for r in self.rels
            if r.source == "sql::dbo.dim_customer"
            and r.target == "sql::dbo.vw_customer_360"
        ]
        assert len(feeds) == 1

    def test_view_customer_360_reads_dim_territory(self):
        feeds = [
            r for r in self.rels
            if r.source == "sql::dbo.dim_sales_territory"
            and r.target == "sql::dbo.vw_customer_360"
        ]
        assert len(feeds) == 1

    def test_procedure_reads_fact_sales(self):
        feeds = [
            r for r in self.rels
            if r.source == "sql::dbo.fact_internet_sales"
            and r.target == "sql::dbo.usp_get_customer_orders"
        ]
        assert len(feeds) == 1

    def test_function_clv_reads_fact_sales(self):
        feeds = [
            r for r in self.rels
            if r.source == "sql::dbo.fact_internet_sales"
            and r.target == "sql::dbo.fn_customer_lifetime_value"
        ]
        assert len(feeds) == 1

    def test_all_assets_have_system_database(self):
        for a in self.assets:
            assert a.system == SystemType.DATABASE

    def test_all_asset_ids_start_with_sql(self):
        for a in self.assets:
            assert a.id.startswith("sql::"), f"Bad ID: {a.id}"

    def test_no_duplicate_asset_ids(self):
        ids = [a.id for a in self.assets]
        assert len(ids) == len(set(ids))
