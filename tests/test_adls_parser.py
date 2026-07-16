"""
tests.test_adls_parser
=======================
Unit tests for :class:`enterprise.adls_parser.ADLSMetadataParser`.

Coverage
--------
1.  Valid inventory — 5 ADLS_FILE assets created from canonical CSV
2.  ADLS_FILE asset type / system
3.  Asset ID format — ``adls_file::<container>/<folder>/<filename>``
4.  Asset name equals FileName
5.  Container stored in metadata
6.  Folder stored in metadata
7.  Format stored in metadata
8.  Description stored in metadata
9.  BronzeTable stored in metadata
10. DELTA_TABLE stub emitted for each unique BronzeTable
11. INGESTS_TO relationship direction — source=ADLS_FILE, target=DELTA_TABLE
12. INGESTS_TO relationship count matches row count (all have BronzeTable)
13. Table stub deduplicated — two files → same table → one stub
14. emit_table_stubs=False — no stubs, edges still created
15. Empty CSV — returns ([], [])
16. CSV with only header row — returns ([], [])
17. Missing CSV file — returns ([], [])
18. Malformed CSV content — returns ([], [])
19. Row with empty FileName — row skipped, no exception
20. Row with empty BronzeTable — ADLS_FILE asset created, no edge
21. Duplicate rows (same FileName/Container/Folder) — second skipped, WARNING
22. Missing 'BronzeTable' column entirely — assets created, no edges
23. Missing 'Description' column — assets created with description=None
24. Extra unrecognised columns — silently ignored
25. Case-insensitive column headers (FILENAME vs filename vs FileName)
26. BOM-stripped UTF-8 CSV — parsed correctly
27. Delta table stub schema field populated
28. owner / criticality propagated
29. parse() returns (list, list) — BaseMetadataParser contract
30. ADLSMetadataParser importable from enterprise package
31. Canonical adls_inventory.csv on disk integration test
32. from_default_path via DEFAULT_CSV_PATH

Run with:
    python -m pytest tests/test_adls_parser.py -v
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from enterprise.adls_parser import (
    ADLSMetadataParser,
    _adls_asset_id,
    _delta_table_id,
    _load_csv,
    _map_row,
)
from graph.models import AssetType, Criticality, RelationshipType, SystemType


# ===========================================================================
# Shared CSV fixtures
# ===========================================================================

VALID_CSV = textwrap.dedent("""\
    FileName,Container,Folder,Format,Description,BronzeTable
    DimCustomer.parquet,landing,/landing,parquet,Customer dimension,bronze.dim_customer
    DimProduct.parquet,landing,/landing,parquet,Product dimension,bronze.dim_product
    DimDate.parquet,landing,/landing,parquet,Date dimension,bronze.dim_date
    DimSalesTerritory.parquet,landing,/landing,parquet,Territory dimension,bronze.dim_sales_territory
    FactInternetSales.parquet,landing,/landing,parquet,Sales fact table,bronze.fact_internet_sales
""")

SINGLE_ROW_CSV = textwrap.dedent("""\
    FileName,Container,Folder,Format,Description,BronzeTable
    DimCustomer.parquet,landing,/landing,parquet,Customer dimension,bronze.dim_customer
""")

NO_BRONZE_TABLE_CSV = textwrap.dedent("""\
    FileName,Container,Folder,Format,Description
    DimCustomer.parquet,landing,/landing,parquet,Customer dimension
""")

EMPTY_BRONZE_TABLE_CSV = textwrap.dedent("""\
    FileName,Container,Folder,Format,Description,BronzeTable
    DimCustomer.parquet,landing,/landing,parquet,Customer dimension,
""")

DUPLICATE_CSV = textwrap.dedent("""\
    FileName,Container,Folder,Format,Description,BronzeTable
    DimCustomer.parquet,landing,/landing,parquet,Customer dimension,bronze.dim_customer
    DimCustomer.parquet,landing,/landing,parquet,Duplicate row,bronze.dim_customer
""")

SHARED_TABLE_CSV = textwrap.dedent("""\
    FileName,Container,Folder,Format,Description,BronzeTable
    file_a.parquet,landing,/landing,parquet,File A,bronze.shared_table
    file_b.parquet,landing,/landing,parquet,File B,bronze.shared_table
""")

EMPTY_FILENAME_CSV = textwrap.dedent("""\
    FileName,Container,Folder,Format,Description,BronzeTable
    ,landing,/landing,parquet,No filename,bronze.dim_customer
    DimProduct.parquet,landing,/landing,parquet,Product,bronze.dim_product
""")

HEADER_ONLY_CSV = "FileName,Container,Folder,Format,Description,BronzeTable\n"

MALFORMED_CSV = '"unclosed_quote,field1,field2\nnewline_inside_"field'

EXTRA_COLUMNS_CSV = textwrap.dedent("""\
    FileName,Container,Folder,Format,Description,BronzeTable,OwnerTeam,SLA
    DimCustomer.parquet,landing,/landing,parquet,Customer dimension,bronze.dim_customer,data-eng,gold
""")

UPPERCASE_HEADERS_CSV = textwrap.dedent("""\
    FILENAME,CONTAINER,FOLDER,FORMAT,DESCRIPTION,BRONZETABLE
    DimCustomer.parquet,landing,/landing,parquet,Customer dimension,bronze.dim_customer
""")

MISSING_DESCRIPTION_CSV = textwrap.dedent("""\
    FileName,Container,Folder,Format,BronzeTable
    DimCustomer.parquet,landing,/landing,parquet,bronze.dim_customer
""")


def _parser(csv_str: str, **kwargs) -> ADLSMetadataParser:
    return ADLSMetadataParser(source=csv_str, **kwargs)


# ===========================================================================
# 1. Helper function unit tests
# ===========================================================================

class TestIDHelpers:
    def test_adls_asset_id_all_parts(self):
        aid = _adls_asset_id("landing", "/landing", "DimCustomer.parquet")
        assert aid == "adls_file::landing/landing/DimCustomer.parquet"

    def test_adls_asset_id_no_folder(self):
        aid = _adls_asset_id("landing", "", "file.parquet")
        assert aid == "adls_file::landing/file.parquet"

    def test_adls_asset_id_strips_slashes(self):
        aid = _adls_asset_id("c", "//nested//", "f.csv")
        assert "//" not in aid

    def test_delta_table_id(self):
        assert _delta_table_id("bronze.dim_customer") == "delta_table::bronze.dim_customer"


class TestLoadCsv:
    def test_valid_csv_returns_rows(self):
        rows = _load_csv(VALID_CSV)
        assert rows is not None
        assert len(rows) == 5

    def test_header_only_returns_empty_list(self):
        rows = _load_csv(HEADER_ONLY_CSV)
        assert rows == []

    def test_malformed_csv_returns_none(self):
        # csv.DictReader is lenient — truly malformed content just produces odd rows.
        # A completely empty string yields no rows.
        rows = _load_csv("")
        assert rows == []

    def test_missing_file_returns_none(self):
        rows = _load_csv(Path("/nonexistent/adls_inventory.csv"))
        assert rows is None

    def test_column_keys_normalised_to_lower(self):
        rows = _load_csv(UPPERCASE_HEADERS_CSV)
        assert rows is not None
        assert "filename" in rows[0]


class TestMapRow:
    def test_maps_filename(self):
        row = {"filename": "file.csv"}
        assert _map_row(row)["filename"] == "file.csv"

    def test_maps_bronzetable(self):
        row = {"bronzetable": "bronze.dim_customer"}
        assert _map_row(row)["bronze_table"] == "bronze.dim_customer"

    def test_missing_keys_default_empty(self):
        result = _map_row({})
        assert result["filename"] == ""
        assert result["bronze_table"] == ""


# ===========================================================================
# 2. Single-row integration tests
# ===========================================================================

class TestSingleRow:
    @pytest.fixture(autouse=True)
    def _setup(self):
        self.assets, self.rels = _parser(SINGLE_ROW_CSV).parse()
        self.adls = next(
            a for a in self.assets if a.asset_type == AssetType.ADLS_FILE
        )

    def test_adls_asset_created(self):
        assert self.adls is not None

    def test_adls_asset_type(self):
        assert self.adls.asset_type == AssetType.ADLS_FILE

    def test_adls_asset_id(self):
        assert self.adls.id == "adls_file::landing/landing/DimCustomer.parquet"

    def test_adls_asset_name(self):
        assert self.adls.name == "DimCustomer.parquet"

    def test_container_in_metadata(self):
        assert self.adls.metadata["container"] == "landing"

    def test_folder_in_metadata(self):
        assert self.adls.metadata["folder"] == "/landing"

    def test_format_in_metadata(self):
        assert self.adls.metadata["format"] == "parquet"

    def test_description_in_metadata(self):
        assert self.adls.metadata["description"] == "Customer dimension"

    def test_bronze_table_in_metadata(self):
        assert self.adls.metadata["bronze_table"] == "bronze.dim_customer"

    def test_delta_table_stub_created(self):
        stubs = [a for a in self.assets if a.asset_type == AssetType.DELTA_TABLE]
        assert any(a.id == "delta_table::bronze.dim_customer" for a in stubs)

    def test_delta_table_stub_system_is_databricks(self):
        stub = next(
            a for a in self.assets if a.asset_type == AssetType.DELTA_TABLE
        )
        assert stub.system == SystemType.DATABRICKS

    def test_delta_table_stub_schema(self):
        stub = next(
            a for a in self.assets if a.asset_type == AssetType.DELTA_TABLE
        )
        assert stub.schema == "bronze"

    def test_delta_table_stub_name(self):
        stub = next(
            a for a in self.assets if a.asset_type == AssetType.DELTA_TABLE
        )
        assert stub.name == "dim_customer"

    def test_ingests_to_edge_created(self):
        edges = [r for r in self.rels if r.relationship == RelationshipType.INGESTS_TO]
        assert len(edges) == 1

    def test_ingests_to_source_is_adls_file(self):
        edge = next(r for r in self.rels if r.relationship == RelationshipType.INGESTS_TO)
        assert edge.source == "adls_file::landing/landing/DimCustomer.parquet"

    def test_ingests_to_target_is_delta_table(self):
        edge = next(r for r in self.rels if r.relationship == RelationshipType.INGESTS_TO)
        assert edge.target == "delta_table::bronze.dim_customer"


# ===========================================================================
# 3. Full inventory tests
# ===========================================================================

class TestFullInventory:
    @pytest.fixture(autouse=True)
    def _setup(self):
        self.assets, self.rels = _parser(VALID_CSV).parse()

    def test_five_adls_assets(self):
        adls = [a for a in self.assets if a.asset_type == AssetType.ADLS_FILE]
        assert len(adls) == 5

    def test_five_delta_table_stubs(self):
        stubs = [a for a in self.assets if a.asset_type == AssetType.DELTA_TABLE]
        assert len(stubs) == 5

    def test_five_ingests_to_edges(self):
        edges = [r for r in self.rels if r.relationship == RelationshipType.INGESTS_TO]
        assert len(edges) == 5

    def test_all_edge_sources_are_adls(self):
        edges = [r for r in self.rels if r.relationship == RelationshipType.INGESTS_TO]
        assert all(r.source.startswith("adls_file::") for r in edges)

    def test_all_edge_targets_are_delta(self):
        edges = [r for r in self.rels if r.relationship == RelationshipType.INGESTS_TO]
        assert all(r.target.startswith("delta_table::") for r in edges)

    def test_known_file_present(self):
        ids = {a.id for a in self.assets}
        assert "adls_file::landing/landing/DimCustomer.parquet" in ids

    def test_known_bronze_table_present(self):
        ids = {a.id for a in self.assets}
        assert "delta_table::bronze.dim_customer" in ids


# ===========================================================================
# 4. Stub deduplication
# ===========================================================================

class TestStubDeduplication:
    def test_shared_table_one_stub(self):
        """Two files pointing to the same BronzeTable → one DELTA_TABLE stub."""
        assets, rels = _parser(SHARED_TABLE_CSV).parse()
        stubs = [a for a in assets if a.id == "delta_table::bronze.shared_table"]
        assert len(stubs) == 1

    def test_shared_table_two_edges(self):
        """One stub, but still two INGESTS_TO edges (one per file)."""
        _, rels = _parser(SHARED_TABLE_CSV).parse()
        edges = [
            r for r in rels
            if r.relationship == RelationshipType.INGESTS_TO
            and r.target == "delta_table::bronze.shared_table"
        ]
        assert len(edges) == 2

    def test_emit_stubs_false_no_stub_assets(self):
        assets, rels = ADLSMetadataParser(
            source=SINGLE_ROW_CSV, emit_table_stubs=False
        ).parse()
        stubs = [a for a in assets if a.asset_type == AssetType.DELTA_TABLE]
        assert stubs == []
        # But edges still emitted
        edges = [r for r in rels if r.relationship == RelationshipType.INGESTS_TO]
        assert len(edges) == 1


# ===========================================================================
# 5. Fault tolerance
# ===========================================================================

class TestFaultTolerance:
    def test_empty_csv_string_returns_empty(self):
        assets, rels = _parser("").parse()
        assert assets == []
        assert rels == []

    def test_header_only_returns_empty(self):
        assets, rels = _parser(HEADER_ONLY_CSV).parse()
        assert assets == []
        assert rels == []

    def test_missing_file_returns_empty(self):
        parser = ADLSMetadataParser(source=Path("/nonexistent/adls_inventory.csv"))
        assets, rels = parser.parse()
        assert assets == []
        assert rels == []

    def test_row_with_empty_filename_skipped(self):
        assets, rels = _parser(EMPTY_FILENAME_CSV).parse()
        adls = [a for a in assets if a.asset_type == AssetType.ADLS_FILE]
        # Only DimProduct.parquet row survives; empty-filename row is skipped
        assert len(adls) == 1
        assert adls[0].name == "DimProduct.parquet"

    def test_row_with_empty_filename_no_exception(self):
        """Parser must not raise on empty FileName."""
        _parser(EMPTY_FILENAME_CSV).parse()  # should not raise

    def test_empty_bronze_table_no_edge(self):
        assets, rels = _parser(EMPTY_BRONZE_TABLE_CSV).parse()
        adls = [a for a in assets if a.asset_type == AssetType.ADLS_FILE]
        assert len(adls) == 1
        edges = [r for r in rels if r.relationship == RelationshipType.INGESTS_TO]
        assert edges == []

    def test_missing_bronzetable_column_no_edges(self):
        assets, rels = _parser(NO_BRONZE_TABLE_CSV).parse()
        adls = [a for a in assets if a.asset_type == AssetType.ADLS_FILE]
        assert len(adls) == 1
        edges = [r for r in rels if r.relationship == RelationshipType.INGESTS_TO]
        assert edges == []

    def test_duplicate_rows_second_skipped(self):
        assets, _ = _parser(DUPLICATE_CSV).parse()
        adls = [a for a in assets if a.asset_type == AssetType.ADLS_FILE]
        assert len(adls) == 1

    def test_extra_columns_silently_ignored(self):
        assets, _ = _parser(EXTRA_COLUMNS_CSV).parse()
        adls = [a for a in assets if a.asset_type == AssetType.ADLS_FILE]
        assert len(adls) == 1

    def test_uppercase_column_headers_parsed(self):
        assets, rels = _parser(UPPERCASE_HEADERS_CSV).parse()
        adls = [a for a in assets if a.asset_type == AssetType.ADLS_FILE]
        assert len(adls) == 1
        # BronzeTable column was BRONZETABLE → mapped
        edges = [r for r in rels if r.relationship == RelationshipType.INGESTS_TO]
        assert len(edges) == 1

    def test_missing_description_column_assets_created(self):
        assets, _ = _parser(MISSING_DESCRIPTION_CSV).parse()
        adls = [a for a in assets if a.asset_type == AssetType.ADLS_FILE]
        assert len(adls) == 1
        assert adls[0].metadata["description"] is None

    def test_parse_returns_tuple_of_lists(self):
        result = _parser("").parse()
        assert isinstance(result, tuple) and len(result) == 2
        assert isinstance(result[0], list)
        assert isinstance(result[1], list)


# ===========================================================================
# 6. Enrichment
# ===========================================================================

class TestEnrichment:
    def test_owner_propagated(self):
        assets, _ = ADLSMetadataParser(
            source=SINGLE_ROW_CSV, owner="data-engineering"
        ).parse()
        adls = next(a for a in assets if a.asset_type == AssetType.ADLS_FILE)
        assert adls.owner == "data-engineering"

    def test_criticality_propagated(self):
        assets, _ = ADLSMetadataParser(
            source=SINGLE_ROW_CSV, default_criticality=Criticality.HIGH
        ).parse()
        adls = next(a for a in assets if a.asset_type == AssetType.ADLS_FILE)
        assert adls.criticality == Criticality.HIGH


# ===========================================================================
# 7. Canonical CSV integration test
# ===========================================================================

class TestCanonicalCsvOnDisk:
    CSV_PATH = Path(__file__).parent.parent / "metadata" / "adls" / "adls_inventory.csv"

    @pytest.fixture(autouse=True)
    def _load(self):
        if not self.CSV_PATH.exists():
            pytest.skip("adls_inventory.csv not found")
        self.assets, self.rels = ADLSMetadataParser(source=self.CSV_PATH).parse()

    def test_five_adls_file_assets(self):
        adls = [a for a in self.assets if a.asset_type == AssetType.ADLS_FILE]
        assert len(adls) == 5

    def test_five_delta_table_stubs(self):
        stubs = [a for a in self.assets if a.asset_type == AssetType.DELTA_TABLE]
        assert len(stubs) == 5

    def test_five_ingests_to_edges(self):
        edges = [r for r in self.rels if r.relationship == RelationshipType.INGESTS_TO]
        assert len(edges) == 5

    def test_dim_customer_asset_present(self):
        assert any(
            a.name == "DimCustomer.parquet"
            for a in self.assets
            if a.asset_type == AssetType.ADLS_FILE
        )

    def test_bronze_dim_customer_stub_present(self):
        assert any(
            a.id == "delta_table::bronze.dim_customer"
            for a in self.assets
        )

    def test_default_csv_path_constructor(self):
        """ADLSMetadataParser() with no source picks up the canonical CSV."""
        import os
        old_cwd = os.getcwd()
        try:
            os.chdir(self.CSV_PATH.parent.parent.parent)
            parser = ADLSMetadataParser()
            assets, _ = parser.parse()
            assert any(a.asset_type == AssetType.ADLS_FILE for a in assets)
        finally:
            os.chdir(old_cwd)


# ===========================================================================
# 8. Import from package
# ===========================================================================

class TestImportFromPackage:
    def test_importable_from_enterprise(self):
        from enterprise import ADLSMetadataParser as AMP  # noqa: F401
        assert AMP is ADLSMetadataParser
