"""
tests.test_enterprise_models
==============================
Unit tests for the expanded graph models.

Covers:
  - All new AssetType values are present and have correct string values
  - All new SystemType values are present
  - All new RelationshipType values are present
  - Asset enrichment fields default correctly
  - Asset.to_dict() serialises all fields
  - Asset.fully_qualified_name() works correctly
  - Backward compatibility: old enum values unchanged

Run with:
    python -m pytest tests/test_enterprise_models.py -v
"""

from __future__ import annotations

import pytest

from graph.models import Asset, AssetType, Criticality, Relationship, RelationshipType, SystemType


class TestAssetTypeCompleteness:
    """All required asset types are present with correct values."""

    # ── Database layer
    def test_database_table(self): assert AssetType.DATABASE_TABLE.value == "database_table"
    def test_database_column(self): assert AssetType.DATABASE_COLUMN.value == "database_column"
    def test_primary_key(self):     assert AssetType.PRIMARY_KEY.value    == "primary_key"
    def test_foreign_key(self):     assert AssetType.FOREIGN_KEY.value    == "foreign_key"
    def test_sql_view(self):        assert AssetType.SQL_VIEW.value        == "sql_view"
    def test_materialized_view(self): assert AssetType.MATERIALIZED_VIEW.value == "materialized_view"
    def test_sql_function(self):    assert AssetType.SQL_FUNCTION.value    == "sql_function"

    # ── Databricks layer
    def test_databricks_notebook(self):   assert AssetType.DATABRICKS_NOTEBOOK.value   == "databricks_notebook"
    def test_spark_job(self):             assert AssetType.SPARK_JOB.value             == "spark_job"
    def test_delta_live_table(self):      assert AssetType.DELTA_LIVE_TABLE.value      == "delta_live_table"
    def test_unity_catalog_object(self):  assert AssetType.UNITY_CATALOG_OBJECT.value  == "unity_catalog_object"

    # ── Orchestration layer
    def test_adf_pipeline(self):    assert AssetType.ADF_PIPELINE.value    == "adf_pipeline"
    def test_fabric_pipeline(self): assert AssetType.FABRIC_PIPELINE.value == "fabric_pipeline"
    def test_airflow_dag(self):     assert AssetType.AIRFLOW_DAG.value     == "airflow_dag"
    def test_dataflow(self):        assert AssetType.DATAFLOW.value        == "dataflow"

    # ── API layer
    def test_rest_api(self): assert AssetType.REST_API.value == "rest_api"

    # ── Power BI layer
    def test_powerbi_dataset(self):    assert AssetType.POWERBI_DATASET.value    == "powerbi_dataset"
    def test_powerbi_measure(self):    assert AssetType.POWERBI_MEASURE.value    == "powerbi_measure"
    def test_powerbi_visual(self):     assert AssetType.POWERBI_VISUAL.value     == "powerbi_visual"
    def test_powerbi_report(self):     assert AssetType.POWERBI_REPORT.value     == "powerbi_report"
    def test_powerbi_dashboard(self):  assert AssetType.POWERBI_DASHBOARD.value  == "powerbi_dashboard"


class TestAssetTypeBackwardCompatibility:
    """Original enum values are unchanged."""

    def test_database(self):         assert AssetType.DATABASE.value         == "database"
    def test_table(self):            assert AssetType.TABLE.value            == "table"
    def test_column(self):           assert AssetType.COLUMN.value           == "column"
    def test_view(self):             assert AssetType.VIEW.value             == "view"
    def test_stored_procedure(self): assert AssetType.STORED_PROCEDURE.value == "stored_procedure"
    def test_function(self):         assert AssetType.FUNCTION.value         == "function"
    def test_notebook(self):         assert AssetType.NOTEBOOK.value         == "notebook"
    def test_delta_table(self):      assert AssetType.DELTA_TABLE.value      == "delta_table"
    def test_pipeline(self):         assert AssetType.PIPELINE.value         == "pipeline"
    def test_job(self):              assert AssetType.JOB.value              == "job"
    def test_api(self):              assert AssetType.API.value              == "api"
    def test_semantic_model(self):   assert AssetType.SEMANTIC_MODEL.value   == "semantic_model"
    def test_report(self):           assert AssetType.REPORT.value           == "report"
    def test_visual(self):           assert AssetType.VISUAL.value           == "visual"
    def test_measure(self):          assert AssetType.MEASURE.value          == "measure"
    def test_dashboard(self):        assert AssetType.DASHBOARD.value        == "dashboard"


class TestSystemTypeCompleteness:
    """New system types are present."""

    def test_delta_lake(self):    assert SystemType.DELTA_LAKE.value    == "delta_lake"
    def test_unity_catalog(self): assert SystemType.UNITY_CATALOG.value == "unity_catalog"
    def test_adf(self):           assert SystemType.ADF.value           == "adf"
    def test_fabric(self):        assert SystemType.FABRIC.value        == "fabric"
    def test_airflow(self):       assert SystemType.AIRFLOW.value       == "airflow"
    def test_synapse(self):       assert SystemType.SYNAPSE.value       == "synapse"
    def test_snowflake(self):     assert SystemType.SNOWFLAKE.value     == "snowflake"
    def test_bigquery(self):      assert SystemType.BIGQUERY.value      == "bigquery"
    def test_redshift(self):      assert SystemType.REDSHIFT.value      == "redshift"


class TestSystemTypeBackwardCompatibility:
    def test_database(self):   assert SystemType.DATABASE.value   == "database"
    def test_sql(self):        assert SystemType.SQL.value        == "sql"
    def test_databricks(self): assert SystemType.DATABRICKS.value == "databricks"
    def test_pipeline(self):   assert SystemType.PIPELINE.value   == "pipeline"
    def test_powerbi(self):    assert SystemType.POWERBI.value    == "powerbi"
    def test_api(self):        assert SystemType.API.value        == "api"


class TestRelationshipTypeCompleteness:
    def test_joins(self):     assert RelationshipType.JOINS.value     == "JOINS"
    def test_generates(self): assert RelationshipType.GENERATES.value == "GENERATES"
    def test_refreshes(self): assert RelationshipType.REFRESHES.value == "REFRESHES"
    def test_publishes(self): assert RelationshipType.PUBLISHES.value == "PUBLISHES"
    def test_owns(self):      assert RelationshipType.OWNS.value      == "OWNS"
    def test_contains(self):  assert RelationshipType.CONTAINS.value  == "CONTAINS"


class TestRelationshipTypeBackwardCompatibility:
    def test_uses(self):       assert RelationshipType.USES.value       == "USES"
    def test_reads(self):      assert RelationshipType.READS.value      == "READS"
    def test_writes(self):     assert RelationshipType.WRITES.value     == "WRITES"
    def test_feeds(self):      assert RelationshipType.FEEDS.value      == "FEEDS"
    def test_calls(self):      assert RelationshipType.CALLS.value      == "CALLS"
    def test_references(self): assert RelationshipType.REFERENCES.value == "REFERENCES"
    def test_displays(self):   assert RelationshipType.DISPLAYS.value   == "DISPLAYS"
    def test_depends_on(self): assert RelationshipType.DEPENDS_ON.value == "DEPENDS_ON"


class TestAssetEnrichmentFields:
    """Asset enrichment fields default correctly and serialise fully."""

    def _make(self, **kwargs) -> Asset:
        defaults = dict(
            id="test::asset",
            name="test_asset",
            asset_type=AssetType.DATABASE_TABLE,
            system=SystemType.DATABASE,
        )
        defaults.update(kwargs)
        return Asset(**defaults)

    def test_defaults_are_safe(self):
        a = self._make()
        assert a.catalog is None
        assert a.schema is None
        assert a.owner is None
        assert a.criticality == Criticality.MEDIUM
        assert a.tags == []
        assert a.source_file is None
        assert a.line_number is None
        assert a.metadata == {}
        assert a.properties == {}

    def test_enrichment_fields_stored(self):
        a = self._make(
            catalog="hive",
            schema="gold",
            owner="de-team",
            criticality=Criticality.CRITICAL,
            tags=["pii", "gold"],
            source_file="schema/orders.sql",
            line_number=42,
            metadata={"partitioned_by": "date"},
        )
        assert a.catalog == "hive"
        assert a.schema == "gold"
        assert a.owner == "de-team"
        assert a.criticality == Criticality.CRITICAL
        assert "pii" in a.tags
        assert a.source_file == "schema/orders.sql"
        assert a.line_number == 42
        assert a.metadata["partitioned_by"] == "date"

    def test_fully_qualified_name_all_parts(self):
        a = self._make(catalog="hive", schema="gold", name="orders")
        assert a.fully_qualified_name() == "hive.gold.orders"

    def test_fully_qualified_name_no_catalog(self):
        a = self._make(schema="dbo", name="orders")
        assert a.fully_qualified_name() == "dbo.orders"

    def test_fully_qualified_name_name_only(self):
        a = self._make(name="orders")
        assert a.fully_qualified_name() == "orders"

    def test_to_dict_contains_all_keys(self):
        a = self._make(catalog="hive", schema="gold", owner="team", tags=["x"])
        d = a.to_dict()
        for key in ("id", "name", "asset_type", "system", "catalog", "schema",
                    "owner", "criticality", "tags", "source_file", "line_number",
                    "metadata", "properties"):
            assert key in d, f"to_dict() missing key: {key!r}"

    def test_existing_properties_still_work(self):
        """properties dict is backward-compatible."""
        a = self._make(properties={"table_name": "orders", "is_hidden": False})
        assert a.properties["table_name"] == "orders"
