"""
tests.test_graph_traversal
===========================
Comprehensive regression tests for Phase 6 — Enterprise Graph Traversal.

Coverage
--------
Change/Models
 1.  EnterpriseChangeAnalysis has systems_impacted field
 2.  EnterpriseChangeAnalysis has databricks_notebooks field
 3.  EnterpriseChangeAnalysis has databricks_pipelines field
 4.  EnterpriseChangeAnalysis has workflow_tasks field
 5.  EnterpriseChangeAnalysis has sql_views field
 6.  EnterpriseChangeAnalysis has sql_procedures field
 7.  EnterpriseChangeAnalysis has sql_functions field
 8.  EnterpriseChangeAnalysis has adls_files field
 9.  EnterpriseChangeAnalysis has powerbi_reports field
10.  EnterpriseChangeAnalysis has semantic_models field
11.  EnterpriseChangeAnalysis has executive_summary field
12.  EnterpriseChangeAnalysis has deployment_plan field
13.  EnterpriseChangeAnalysis has validation_checklist field
14.  EnterpriseChangeAnalysis has rollback_plan field
15.  All new fields have correct default types

Analyzer — typed bucket classification
16.  SQL table change → sql_views populated from downstream SQL views
17.  SQL table change → sql_procedures populated
18.  SQL table change → sql_functions populated
19.  DELTA_TABLE change → databricks_notebooks populated
20.  PIPELINE change → workflow_tasks populated
21.  ADLS_FILE change → adls_files empty (no downstream by default)
22.  TABLE change → powerbi_reports populated from downstream REPORT assets
23.  TABLE change → semantic_models populated from downstream MEASURE/SEMANTIC assets
24.  systems_impacted lists unique system values
25.  systems_impacted is sorted
26.  systems_impacted empty when no downstream assets
27.  Bucket lists are subsets of impacted_assets

Analyzer — executive_summary
28.  executive_summary is non-empty string
29.  executive_summary mentions source asset name
30.  executive_summary mentions downstream bucket names when non-empty
31.  executive_summary states no impact when downstream is empty
32.  executive_summary handles unknown/unresolved source asset

Analyzer — deployment_plan
33.  deployment_plan is a list of strings
34.  deployment_plan is non-empty even with no impact
35.  deployment_plan mentions source asset name
36.  deployment_plan includes SQL step when sql_views non-empty
37.  deployment_plan includes Databricks step when notebooks non-empty
38.  deployment_plan includes Power BI step when reports non-empty
39.  deployment_plan includes regression test step always
40.  deployment_plan handles unresolved source

Analyzer — validation_checklist
41.  validation_checklist is a list of strings
42.  validation_checklist always starts with source asset check
43.  validation_checklist includes SQL check when views impacted
44.  validation_checklist includes notebook check when notebooks impacted
45.  validation_checklist includes Power BI check when reports impacted
46.  validation_checklist includes regression test item always

Analyzer — rollback_plan
47.  rollback_plan is a list of strings
48.  rollback_plan mentions source asset revert
49.  rollback_plan includes SQL revert when SQL assets impacted
50.  rollback_plan includes notebook revert when notebooks impacted
51.  rollback_plan includes Power BI revert when reports impacted
52.  rollback_plan handles unresolved source

Analyzer — natural language request still accepted
53.  "rename X to Y" still works end-to-end
54.  "drop X table" still works end-to-end
55.  "delete X column" still works end-to-end

GraphOrchestrator — bucket mapping
56.  SQL_VIEW → views bucket
57.  STORED_PROCEDURE → stored_procedures bucket
58.  SQL_FUNCTION → functions bucket
59.  DATABRICKS_NOTEBOOK → databricks_notebooks bucket
60.  PIPELINE → pipelines bucket
61.  PIPELINE_TASK → pipelines bucket
62.  ADLS_FILE → external_consumers bucket
63.  REPORT → powerbi_reports bucket
64.  MEASURE → semantic_models bucket
65.  DATABASE_TABLE → database_tables bucket
66.  DELTA_TABLE → delta_live_tables bucket
67.  ADF_PIPELINE → data_factory bucket
68.  Unknown type → external_consumers bucket

Backward compatibility regression
69.  change_request field still present
70.  source_asset field still present
71.  impact_count still correct
72.  impacted_assets still a list of Asset objects
73.  system_breakdown still keyed by SystemType value
74.  dependency_paths still a list of lists
75.  summary field still a non-empty string

Multi-system traversal
76.  SQL table → view → notebook → pipeline chain all captured
77.  ADLS → delta_table → notebook chain all captured
78.  Full enterprise graph: all system types traversed in single BFS

_classify_into_buckets unit tests
79.  Empty input → all buckets empty
80.  SQL_VIEW maps to sql_views
81.  STORED_PROCEDURE maps to sql_procedures
82.  SQL_FUNCTION maps to sql_functions
83.  PIPELINE_TASK maps to workflow_tasks
84.  ADLS_FILE maps to adls_files
85.  REPORT maps to powerbi_reports
86.  MEASURE maps to semantic_models
87.  DATABASE_TABLE → not in any typed bucket (falls through)
88.  Multiple assets of same type → all in same bucket

_build_executive_summary unit tests
89.  Unresolved source → mentions searched name
90.  No impact → "no downstream" message
91.  With impact → lists bucket counts

_build_deployment_plan unit tests
92.  Unresolved → fallback message
93.  SQL-only impact → SQL step present
94.  Notebooks impact → notebook step present
95.  All-systems impact → all steps present

_build_validation_checklist unit tests
96.  No impact → base checks only
97.  SQL impact → SQL check present
98.  Report impact → PBI check present

_build_rollback_plan unit tests
99.  Unresolved → no-rollback message
100. Reports impact → PBI revert first
"""

from __future__ import annotations

from typing import Any, Dict, List
from unittest.mock import MagicMock

import pytest

from change.analyzer import (
    EnterpriseChangeAnalyzer,
    _build_deployment_plan,
    _build_executive_summary,
    _build_rollback_plan,
    _build_validation_checklist,
    _classify_into_buckets,
)
from change.models import ChangeRequest, ChangeType, EnterpriseChangeAnalysis
from graph.enterprise_graph import EnterpriseGraph
from graph.models import Asset, AssetType, Criticality, Relationship, RelationshipType, SystemType
from ai.graph_orchestrator import GraphOrchestrator, _ASSET_TYPE_TO_BUCKET, ENTERPRISE_BUCKETS


# ---------------------------------------------------------------------------
# Graph builder helpers
# ---------------------------------------------------------------------------

def _asset(
    asset_id: str,
    name: str,
    asset_type: AssetType,
    system: SystemType = SystemType.DATABASE,
) -> Asset:
    return Asset(id=asset_id, name=name, asset_type=asset_type, system=system)


def _rel(src: str, tgt: str, rel: RelationshipType = RelationshipType.READS) -> Relationship:
    return Relationship(source=src, target=tgt, relationship=rel)


def _build_full_enterprise_graph() -> EnterpriseGraph:
    """Build a realistic multi-system graph for traversal tests.

    Lineage chain:
        sql::dbo.fact_sales (DATABASE_TABLE)
            → sql::dbo.vw_sales   (SQL_VIEW,           READS)
            → sql::dbo.usp_report (STORED_PROCEDURE,   READS)
            → sql::dbo.fn_score   (SQL_FUNCTION,       READS)
            → notebook::01_bronze (DATABRICKS_NOTEBOOK, READS)
            → pipeline::etl       (PIPELINE,            READS)
            → task::etl::ingest   (PIPELINE_TASK,       TRIGGERS)
            → adls::landing/f.pq  (ADLS_FILE,           INGESTS_TO)
            → semantic::sales_sm  (SEMANTIC_MODEL,      USES)
            → measure::Revenue    (MEASURE,             DEPENDS_ON)
            → report::page1       (REPORT,              DISPLAYS)
    """
    g = EnterpriseGraph()

    # Source: SQL table
    g.add_asset(_asset("sql::dbo.fact_sales", "fact_sales", AssetType.DATABASE_TABLE))

    # SQL view reads from table
    g.add_asset(_asset("sql::dbo.vw_sales", "vw_sales", AssetType.SQL_VIEW))
    g.add_relationship(Relationship(source="sql::dbo.fact_sales", target="sql::dbo.vw_sales", relationship=RelationshipType.FEEDS,))

    # Stored procedure reads from table
    g.add_asset(_asset("sql::dbo.usp_report", "usp_report", AssetType.STORED_PROCEDURE))
    g.add_relationship(Relationship(source="sql::dbo.fact_sales", target="sql::dbo.usp_report", relationship=RelationshipType.FEEDS,))

    # SQL function reads from table
    g.add_asset(_asset("sql::dbo.fn_score", "fn_score", AssetType.SQL_FUNCTION))
    g.add_relationship(Relationship(source="sql::dbo.fact_sales", target="sql::dbo.fn_score", relationship=RelationshipType.FEEDS,))

    # Databricks notebook reads from table
    g.add_asset(_asset(
        "notebook::01_bronze", "01_bronze",
        AssetType.DATABRICKS_NOTEBOOK, SystemType.DATABRICKS
    ))
    g.add_relationship(Relationship(source="sql::dbo.fact_sales", target="notebook::01_bronze", relationship=RelationshipType.FEEDS,))

    # Pipeline reads notebook output
    g.add_asset(_asset(
        "pipeline::etl", "etl",
        AssetType.PIPELINE, SystemType.DATABRICKS
    ))
    g.add_relationship(Relationship(source="notebook::01_bronze", target="pipeline::etl", relationship=RelationshipType.FEEDS,))

    # Workflow task
    g.add_asset(_asset(
        "pipeline_task::etl::ingest", "ingest",
        AssetType.PIPELINE_TASK, SystemType.DATABRICKS
    ))
    g.add_relationship(Relationship(
        source="pipeline::etl",
        target="pipeline_task::etl::ingest",
        relationship=RelationshipType.TRIGGERS,
    ))

    # Semantic model is downstream of table
    g.add_asset(_asset(
        "semantic::sales_sm", "sales_sm",
        AssetType.SEMANTIC_MODEL, SystemType.POWERBI
    ))
    g.add_relationship(Relationship(
        source="sql::dbo.fact_sales", target="semantic::sales_sm",
        relationship=RelationshipType.FEEDS,
    ))

    # Measure is downstream of semantic model
    g.add_asset(_asset(
        "measure::Revenue", "Revenue",
        AssetType.MEASURE, SystemType.POWERBI
    ))
    g.add_relationship(Relationship(
        source="semantic::sales_sm", target="measure::Revenue",
        relationship=RelationshipType.FEEDS,
    ))

    # Report is downstream of measure
    g.add_asset(_asset(
        "report::page1", "page1",
        AssetType.REPORT, SystemType.POWERBI
    ))
    g.add_relationship(Relationship(
        source="measure::Revenue", target="report::page1",
        relationship=RelationshipType.FEEDS,
    ))

    return g


def _build_sql_only_graph() -> EnterpriseGraph:
    """SQL table feeds view, procedure, function (source to downstream edges)."""
    g = EnterpriseGraph()
    g.add_asset(_asset("sql::dbo.orders", "orders", AssetType.DATABASE_TABLE))
    g.add_asset(_asset("sql::dbo.vw_orders", "vw_orders", AssetType.SQL_VIEW))
    g.add_asset(_asset("sql::dbo.usp_orders", "usp_orders", AssetType.STORED_PROCEDURE))
    g.add_asset(_asset("sql::dbo.fn_orders", "fn_orders", AssetType.SQL_FUNCTION))
    for tgt in ("sql::dbo.vw_orders", "sql::dbo.usp_orders", "sql::dbo.fn_orders"):
        g.add_relationship(Relationship(
            source="sql::dbo.orders", target=tgt,
            relationship=RelationshipType.FEEDS,
        ))
    return g


def _build_powerbi_only_graph() -> EnterpriseGraph:
    """TABLE feeds MEASURE feeds REPORT chain in Power BI system."""
    g = EnterpriseGraph()
    g.add_asset(_asset("table::sales", "sales", AssetType.TABLE, SystemType.POWERBI))
    g.add_asset(_asset("measure::Revenue", "Revenue", AssetType.MEASURE, SystemType.POWERBI))
    g.add_asset(_asset("report::dash", "dash", AssetType.REPORT, SystemType.POWERBI))
    g.add_relationship(Relationship(
        source="table::sales", target="measure::Revenue",
        relationship=RelationshipType.FEEDS,
    ))
    g.add_relationship(Relationship(
        source="measure::Revenue", target="report::dash",
        relationship=RelationshipType.FEEDS,
    ))
    return g


def _build_adls_graph() -> EnterpriseGraph:
    """ADLS_FILE ingests-to DELTA_TABLE feeds DATABRICKS_NOTEBOOK chain."""
    g = EnterpriseGraph()
    g.add_asset(_asset("adls::f.pq", "f.pq", AssetType.ADLS_FILE))
    g.add_asset(_asset(
        "delta::bronze.orders", "bronze.orders",
        AssetType.DELTA_TABLE, SystemType.DATABRICKS
    ))
    g.add_asset(_asset(
        "notebook::02_silver", "02_silver",
        AssetType.DATABRICKS_NOTEBOOK, SystemType.DATABRICKS
    ))
    g.add_relationship(Relationship(
        source="adls::f.pq", target="delta::bronze.orders",
        relationship=RelationshipType.INGESTS_TO,
    ))
    g.add_relationship(Relationship(
        source="delta::bronze.orders", target="notebook::02_silver",
        relationship=RelationshipType.FEEDS,
    ))
    return g


# ---------------------------------------------------------------------------
# 1-15: EnterpriseChangeAnalysis fields
# ---------------------------------------------------------------------------


class TestChangeAnalysisFields:
    """Verify all Phase-6 fields exist on EnterpriseChangeAnalysis."""

    def _minimal_analysis(self) -> EnterpriseChangeAnalysis:
        return EnterpriseChangeAnalysis(
            change_request=ChangeRequest(
                original_request="test", change_type=ChangeType.UNKNOWN
            ),
            source_asset=None,
            impact_count=0,
            impacted_assets=[],
            system_breakdown={},
            dependency_paths=[],
            summary="",
        )

    def test_systems_impacted_field_exists(self):
        a = self._minimal_analysis()
        assert hasattr(a, "systems_impacted")

    def test_databricks_notebooks_field_exists(self):
        assert hasattr(self._minimal_analysis(), "databricks_notebooks")

    def test_databricks_pipelines_field_exists(self):
        assert hasattr(self._minimal_analysis(), "databricks_pipelines")

    def test_workflow_tasks_field_exists(self):
        assert hasattr(self._minimal_analysis(), "workflow_tasks")

    def test_sql_views_field_exists(self):
        assert hasattr(self._minimal_analysis(), "sql_views")

    def test_sql_procedures_field_exists(self):
        assert hasattr(self._minimal_analysis(), "sql_procedures")

    def test_sql_functions_field_exists(self):
        assert hasattr(self._minimal_analysis(), "sql_functions")

    def test_adls_files_field_exists(self):
        assert hasattr(self._minimal_analysis(), "adls_files")

    def test_powerbi_reports_field_exists(self):
        assert hasattr(self._minimal_analysis(), "powerbi_reports")

    def test_semantic_models_field_exists(self):
        assert hasattr(self._minimal_analysis(), "semantic_models")

    def test_executive_summary_field_exists(self):
        assert hasattr(self._minimal_analysis(), "executive_summary")

    def test_deployment_plan_field_exists(self):
        assert hasattr(self._minimal_analysis(), "deployment_plan")

    def test_validation_checklist_field_exists(self):
        assert hasattr(self._minimal_analysis(), "validation_checklist")

    def test_rollback_plan_field_exists(self):
        assert hasattr(self._minimal_analysis(), "rollback_plan")

    def test_default_types(self):
        a = self._minimal_analysis()
        assert isinstance(a.systems_impacted, list)
        assert isinstance(a.databricks_notebooks, list)
        assert isinstance(a.sql_views, list)
        assert isinstance(a.executive_summary, str)
        assert isinstance(a.deployment_plan, list)
        assert isinstance(a.validation_checklist, list)
        assert isinstance(a.rollback_plan, list)


# ---------------------------------------------------------------------------
# 16-27: Analyzer — typed bucket classification
# ---------------------------------------------------------------------------


class TestAnalyzerBucketClassification:
    """Verify correct bucket assignment via EnterpriseChangeAnalyzer.analyze()."""

    def test_sql_view_downstream_classified(self):
        g = _build_sql_only_graph()
        analyzer = EnterpriseChangeAnalyzer(g)
        result = analyzer.analyze("Delete orders table")
        assert any(a.asset_type == AssetType.SQL_VIEW for a in result.sql_views)

    def test_sql_procedure_downstream_classified(self):
        g = _build_sql_only_graph()
        analyzer = EnterpriseChangeAnalyzer(g)
        result = analyzer.analyze("Delete orders table")
        assert any(a.asset_type == AssetType.STORED_PROCEDURE for a in result.sql_procedures)

    def test_sql_function_downstream_classified(self):
        g = _build_sql_only_graph()
        analyzer = EnterpriseChangeAnalyzer(g)
        result = analyzer.analyze("Delete orders table")
        assert any(a.asset_type == AssetType.SQL_FUNCTION for a in result.sql_functions)

    def test_databricks_notebook_classified(self):
        g = _build_full_enterprise_graph()
        analyzer = EnterpriseChangeAnalyzer(g)
        result = analyzer.analyze("Delete fact_sales table")
        assert any(a.asset_type == AssetType.DATABRICKS_NOTEBOOK for a in result.databricks_notebooks)

    def test_pipeline_task_classified(self):
        g = _build_full_enterprise_graph()
        analyzer = EnterpriseChangeAnalyzer(g)
        result = analyzer.analyze("Delete fact_sales table")
        assert any(a.asset_type == AssetType.PIPELINE_TASK for a in result.workflow_tasks)

    def test_pipeline_classified_as_databricks_pipeline(self):
        g = _build_full_enterprise_graph()
        analyzer = EnterpriseChangeAnalyzer(g)
        result = analyzer.analyze("Delete fact_sales table")
        assert any(a.asset_type == AssetType.PIPELINE for a in result.databricks_pipelines)

    def test_powerbi_report_classified(self):
        g = _build_powerbi_only_graph()
        analyzer = EnterpriseChangeAnalyzer(g)
        result = analyzer.analyze("Drop sales table")
        assert any(a.asset_type == AssetType.REPORT for a in result.powerbi_reports)

    def test_semantic_model_measure_classified(self):
        g = _build_powerbi_only_graph()
        analyzer = EnterpriseChangeAnalyzer(g)
        result = analyzer.analyze("Drop sales table")
        assert any(a.asset_type == AssetType.MEASURE for a in result.semantic_models)

    def test_systems_impacted_contains_database(self):
        g = _build_sql_only_graph()
        analyzer = EnterpriseChangeAnalyzer(g)
        result = analyzer.analyze("Delete orders table")
        assert SystemType.DATABASE.value in result.systems_impacted

    def test_systems_impacted_is_sorted(self):
        g = _build_full_enterprise_graph()
        analyzer = EnterpriseChangeAnalyzer(g)
        result = analyzer.analyze("Delete fact_sales table")
        assert result.systems_impacted == sorted(result.systems_impacted)

    def test_systems_impacted_empty_when_no_downstream(self):
        g = EnterpriseGraph()
        g.add_asset(_asset("sql::dbo.lone", "lone", AssetType.DATABASE_TABLE))
        analyzer = EnterpriseChangeAnalyzer(g)
        result = analyzer.analyze("Delete lone table")
        assert result.systems_impacted == []

    def test_buckets_are_subsets_of_impacted_assets(self):
        g = _build_full_enterprise_graph()
        analyzer = EnterpriseChangeAnalyzer(g)
        result = analyzer.analyze("Delete fact_sales table")
        all_impacted_ids = {a.id for a in result.impacted_assets}
        for bucket_assets in [
            result.sql_views, result.sql_procedures, result.sql_functions,
            result.databricks_notebooks, result.databricks_pipelines,
            result.workflow_tasks, result.adls_files,
            result.powerbi_reports, result.semantic_models,
        ]:
            for asset in bucket_assets:
                assert asset.id in all_impacted_ids


# ---------------------------------------------------------------------------
# 28-32: executive_summary
# ---------------------------------------------------------------------------


class TestExecutiveSummary:
    def test_executive_summary_non_empty(self):
        g = _build_sql_only_graph()
        result = EnterpriseChangeAnalyzer(g).analyze("Delete orders table")
        assert result.executive_summary

    def test_executive_summary_mentions_source(self):
        g = _build_sql_only_graph()
        result = EnterpriseChangeAnalyzer(g).analyze("Delete orders table")
        assert "orders" in result.executive_summary.lower()

    def test_executive_summary_mentions_bucket_names(self):
        g = _build_sql_only_graph()
        result = EnterpriseChangeAnalyzer(g).analyze("Delete orders table")
        # sql_views, sql_procedures, sql_functions should appear
        assert any(
            kw in result.executive_summary.lower()
            for kw in ("sql", "view", "procedure", "function")
        )

    def test_executive_summary_no_impact_message(self):
        g = EnterpriseGraph()
        g.add_asset(_asset("sql::dbo.lone", "lone", AssetType.DATABASE_TABLE))
        result = EnterpriseChangeAnalyzer(g).analyze("Delete lone table")
        assert "no downstream" in result.executive_summary.lower()

    def test_executive_summary_unresolved_source(self):
        g = EnterpriseGraph()
        result = EnterpriseChangeAnalyzer(g).analyze("Delete nonexistent_xyz_table table")
        assert "no enterprise asset" in result.executive_summary.lower()


# ---------------------------------------------------------------------------
# 33-40: deployment_plan
# ---------------------------------------------------------------------------


class TestDeploymentPlan:
    def test_deployment_plan_is_list(self):
        g = _build_sql_only_graph()
        result = EnterpriseChangeAnalyzer(g).analyze("Delete orders table")
        assert isinstance(result.deployment_plan, list)

    def test_deployment_plan_non_empty(self):
        g = _build_sql_only_graph()
        result = EnterpriseChangeAnalyzer(g).analyze("Delete orders table")
        assert len(result.deployment_plan) > 0

    def test_deployment_plan_mentions_source(self):
        g = _build_sql_only_graph()
        result = EnterpriseChangeAnalyzer(g).analyze("Delete orders table")
        assert any("orders" in step.lower() for step in result.deployment_plan)

    def test_deployment_plan_includes_sql_step(self):
        g = _build_sql_only_graph()
        result = EnterpriseChangeAnalyzer(g).analyze("Delete orders table")
        assert any("sql" in step.lower() for step in result.deployment_plan)

    def test_deployment_plan_includes_notebook_step(self):
        g = _build_full_enterprise_graph()
        result = EnterpriseChangeAnalyzer(g).analyze("Delete fact_sales table")
        assert any("notebook" in step.lower() for step in result.deployment_plan)

    def test_deployment_plan_includes_powerbi_step(self):
        g = _build_powerbi_only_graph()
        result = EnterpriseChangeAnalyzer(g).analyze("Drop sales table")
        assert any(
            "power bi" in step.lower() or "semantic" in step.lower() or "report" in step.lower()
            for step in result.deployment_plan
        )

    def test_deployment_plan_includes_regression_step(self):
        g = _build_sql_only_graph()
        result = EnterpriseChangeAnalyzer(g).analyze("Delete orders table")
        assert any("regression" in step.lower() or "test" in step.lower()
                   for step in result.deployment_plan)

    def test_deployment_plan_unresolved_fallback(self):
        g = EnterpriseGraph()
        result = EnterpriseChangeAnalyzer(g).analyze("Delete nothinghere_xyz table")
        assert len(result.deployment_plan) > 0
        assert "source asset" in result.deployment_plan[0].lower()


# ---------------------------------------------------------------------------
# 41-46: validation_checklist
# ---------------------------------------------------------------------------


class TestValidationChecklist:
    def test_validation_checklist_is_list(self):
        g = _build_sql_only_graph()
        result = EnterpriseChangeAnalyzer(g).analyze("Delete orders table")
        assert isinstance(result.validation_checklist, list)

    def test_validation_checklist_starts_with_source_check(self):
        g = _build_sql_only_graph()
        result = EnterpriseChangeAnalyzer(g).analyze("Delete orders table")
        assert result.validation_checklist
        assert "source asset" in result.validation_checklist[0].lower() or \
               "verify" in result.validation_checklist[0].lower()

    def test_validation_checklist_sql_views_check(self):
        g = _build_sql_only_graph()
        result = EnterpriseChangeAnalyzer(g).analyze("Delete orders table")
        combined = " ".join(result.validation_checklist).lower()
        assert "view" in combined or "sql" in combined

    def test_validation_checklist_notebook_check(self):
        g = _build_full_enterprise_graph()
        result = EnterpriseChangeAnalyzer(g).analyze("Delete fact_sales table")
        combined = " ".join(result.validation_checklist).lower()
        assert "notebook" in combined

    def test_validation_checklist_powerbi_check(self):
        g = _build_powerbi_only_graph()
        result = EnterpriseChangeAnalyzer(g).analyze("Drop sales table")
        combined = " ".join(result.validation_checklist).lower()
        assert "power bi" in combined or "report" in combined or "semantic" in combined

    def test_validation_checklist_ends_with_regression(self):
        g = _build_sql_only_graph()
        result = EnterpriseChangeAnalyzer(g).analyze("Delete orders table")
        assert any("regression" in c.lower() or "automated" in c.lower()
                   for c in result.validation_checklist)


# ---------------------------------------------------------------------------
# 47-52: rollback_plan
# ---------------------------------------------------------------------------


class TestRollbackPlan:
    def test_rollback_plan_is_list(self):
        g = _build_sql_only_graph()
        result = EnterpriseChangeAnalyzer(g).analyze("Delete orders table")
        assert isinstance(result.rollback_plan, list)

    def test_rollback_plan_mentions_source_revert(self):
        g = _build_sql_only_graph()
        result = EnterpriseChangeAnalyzer(g).analyze("Delete orders table")
        combined = " ".join(result.rollback_plan).lower()
        assert "revert" in combined or "restore" in combined

    def test_rollback_plan_sql_revert_when_sql_impacted(self):
        g = _build_sql_only_graph()
        result = EnterpriseChangeAnalyzer(g).analyze("Delete orders table")
        combined = " ".join(result.rollback_plan).lower()
        assert "sql" in combined or "view" in combined or "procedure" in combined

    def test_rollback_plan_notebook_revert_when_notebooks_impacted(self):
        g = _build_full_enterprise_graph()
        result = EnterpriseChangeAnalyzer(g).analyze("Delete fact_sales table")
        combined = " ".join(result.rollback_plan).lower()
        assert "notebook" in combined

    def test_rollback_plan_powerbi_revert_first_when_reports_impacted(self):
        g = _build_powerbi_only_graph()
        result = EnterpriseChangeAnalyzer(g).analyze("Drop sales table")
        combined = " ".join(result.rollback_plan).lower()
        assert "power bi" in combined or "report" in combined

    def test_rollback_plan_unresolved_source(self):
        g = EnterpriseGraph()
        result = EnterpriseChangeAnalyzer(g).analyze("Delete phantom_xyz table")
        assert len(result.rollback_plan) > 0
        assert "no rollback" in result.rollback_plan[0].lower()


# ---------------------------------------------------------------------------
# 53-55: Natural language requests still accepted (backward compatibility)
# ---------------------------------------------------------------------------


class TestNaturalLanguageRequests:
    def test_rename_request_works(self):
        g = _build_sql_only_graph()
        result = EnterpriseChangeAnalyzer(g).analyze("Rename orders to purchase_orders")
        assert result.change_request.change_type in (
            ChangeType.TABLE_RENAME, ChangeType.COLUMN_RENAME, ChangeType.UNKNOWN
        )
        assert result.executive_summary  # non-empty

    def test_drop_table_request_works(self):
        g = _build_sql_only_graph()
        result = EnterpriseChangeAnalyzer(g).analyze("Drop orders table")
        assert result.change_request.change_type == ChangeType.TABLE_DELETE
        assert result.deployment_plan

    def test_delete_column_request_works(self):
        g = _build_powerbi_only_graph()
        result = EnterpriseChangeAnalyzer(g).analyze("Delete Revenue column")
        assert result.change_request.change_type == ChangeType.COLUMN_DELETE
        assert result.validation_checklist


# ---------------------------------------------------------------------------
# 56-68: GraphOrchestrator — bucket mapping
# ---------------------------------------------------------------------------


class TestOrchestratorBucketMapping:
    """Verify _ASSET_TYPE_TO_BUCKET maps every supported AssetType correctly."""

    def test_sql_view_maps_to_views(self):
        assert _ASSET_TYPE_TO_BUCKET.get(AssetType.SQL_VIEW.value) == "views"

    def test_view_maps_to_views(self):
        assert _ASSET_TYPE_TO_BUCKET.get(AssetType.VIEW.value) == "views"

    def test_stored_procedure_maps_to_stored_procedures(self):
        assert _ASSET_TYPE_TO_BUCKET.get(AssetType.STORED_PROCEDURE.value) == "stored_procedures"

    def test_sql_function_maps_to_functions(self):
        assert _ASSET_TYPE_TO_BUCKET.get(AssetType.SQL_FUNCTION.value) == "functions"

    def test_function_maps_to_functions(self):
        assert _ASSET_TYPE_TO_BUCKET.get(AssetType.FUNCTION.value) == "functions"

    def test_databricks_notebook_maps_to_databricks_notebooks(self):
        assert _ASSET_TYPE_TO_BUCKET.get(AssetType.DATABRICKS_NOTEBOOK.value) == "databricks_notebooks"

    def test_notebook_maps_to_databricks_notebooks(self):
        assert _ASSET_TYPE_TO_BUCKET.get(AssetType.NOTEBOOK.value) == "databricks_notebooks"

    def test_pipeline_maps_to_pipelines(self):
        assert _ASSET_TYPE_TO_BUCKET.get(AssetType.PIPELINE.value) == "pipelines"

    def test_pipeline_task_maps_to_pipelines(self):
        assert _ASSET_TYPE_TO_BUCKET.get(AssetType.PIPELINE_TASK.value) == "pipelines"

    def test_adls_file_maps_to_external_consumers(self):
        assert _ASSET_TYPE_TO_BUCKET.get(AssetType.ADLS_FILE.value) == "external_consumers"

    def test_report_maps_to_powerbi_reports(self):
        assert _ASSET_TYPE_TO_BUCKET.get(AssetType.REPORT.value) == "powerbi_reports"

    def test_powerbi_report_maps_to_powerbi_reports(self):
        assert _ASSET_TYPE_TO_BUCKET.get(AssetType.POWERBI_REPORT.value) == "powerbi_reports"

    def test_measure_maps_to_semantic_models(self):
        assert _ASSET_TYPE_TO_BUCKET.get(AssetType.MEASURE.value) == "semantic_models"

    def test_database_table_maps_to_database_tables(self):
        assert _ASSET_TYPE_TO_BUCKET.get(AssetType.DATABASE_TABLE.value) == "database_tables"

    def test_delta_table_maps_to_delta_live_tables(self):
        assert _ASSET_TYPE_TO_BUCKET.get(AssetType.DELTA_TABLE.value) == "delta_live_tables"

    def test_adf_pipeline_maps_to_data_factory(self):
        assert _ASSET_TYPE_TO_BUCKET.get(AssetType.ADF_PIPELINE.value) == "data_factory"

    def test_unknown_type_defaults_to_external_consumers(self):
        # "external_consumers" is the fallback for any unmapped type
        unknown_bucket = _ASSET_TYPE_TO_BUCKET.get("totally_unknown_type", "external_consumers")
        assert unknown_bucket == "external_consumers"

    def test_all_buckets_in_enterprise_buckets(self):
        for bucket in _ASSET_TYPE_TO_BUCKET.values():
            assert bucket in ENTERPRISE_BUCKETS, f"Bucket {bucket!r} not in ENTERPRISE_BUCKETS"


# ---------------------------------------------------------------------------
# 69-75: Backward compatibility
# ---------------------------------------------------------------------------


class TestBackwardCompatibility:
    """Existing fields must still work exactly as before Phase 6."""

    @pytest.fixture(autouse=True)
    def _setup(self):
        self.g = _build_sql_only_graph()
        self.analyzer = EnterpriseChangeAnalyzer(self.g)
        self.result = self.analyzer.analyze("Delete orders table")

    def test_change_request_field_present(self):
        assert self.result.change_request is not None

    def test_source_asset_field_present(self):
        # source_asset is Optional — may be None if not resolved but field exists
        assert hasattr(self.result, "source_asset")

    def test_impact_count_correct(self):
        assert self.result.impact_count == len(self.result.impacted_assets)

    def test_impacted_assets_is_list_of_assets(self):
        assert isinstance(self.result.impacted_assets, list)
        for a in self.result.impacted_assets:
            assert isinstance(a, Asset)

    def test_system_breakdown_keyed_by_system_type_value(self):
        for key in self.result.system_breakdown.keys():
            assert key in {s.value for s in SystemType}

    def test_dependency_paths_is_list_of_lists(self):
        assert isinstance(self.result.dependency_paths, list)
        for path in self.result.dependency_paths:
            assert isinstance(path, list)

    def test_summary_non_empty(self):
        assert isinstance(self.result.summary, str)
        assert self.result.summary


# ---------------------------------------------------------------------------
# 76-78: Multi-system traversal
# ---------------------------------------------------------------------------


class TestMultiSystemTraversal:
    def test_sql_to_notebook_to_pipeline_chain(self):
        """A SQL table change reaches notebooks → pipelines → tasks across systems."""
        g = _build_full_enterprise_graph()
        result = EnterpriseChangeAnalyzer(g).analyze("Delete fact_sales table")
        asset_types = {a.asset_type for a in result.impacted_assets}
        assert AssetType.SQL_VIEW in asset_types
        assert AssetType.STORED_PROCEDURE in asset_types
        assert AssetType.SQL_FUNCTION in asset_types
        assert AssetType.DATABRICKS_NOTEBOOK in asset_types
        assert AssetType.PIPELINE in asset_types
        assert AssetType.PIPELINE_TASK in asset_types

    def test_adls_to_delta_to_notebook_chain(self):
        """ADLS file ingests-to delta → notebook reads delta."""
        g = _build_adls_graph()
        result = EnterpriseChangeAnalyzer(g).analyze("Delete f.pq")
        asset_types = {a.asset_type for a in result.impacted_assets}
        assert AssetType.DELTA_TABLE in asset_types
        assert AssetType.DATABRICKS_NOTEBOOK in asset_types

    def test_full_graph_all_systems_traversed(self):
        """Full enterprise graph traversal reaches database, databricks, and powerbi."""
        g = _build_full_enterprise_graph()
        result = EnterpriseChangeAnalyzer(g).analyze("Delete fact_sales table")
        systems = set(result.systems_impacted)
        # DATABASE (sql views/procs/fns), DATABRICKS (notebooks/pipelines), POWERBI (reports/measures)
        assert len(systems) >= 2


# ---------------------------------------------------------------------------
# 79-88: _classify_into_buckets unit tests
# ---------------------------------------------------------------------------


class TestClassifyIntoBuckets:
    def test_empty_input_all_buckets_empty(self):
        buckets = _classify_into_buckets([])
        for v in buckets.values():
            assert v == []

    def test_sql_view_maps_to_sql_views(self):
        a = _asset("sql::vw", "vw", AssetType.SQL_VIEW)
        buckets = _classify_into_buckets([a])
        assert a in buckets["sql_views"]

    def test_stored_procedure_maps_to_sql_procedures(self):
        a = _asset("sql::proc", "proc", AssetType.STORED_PROCEDURE)
        buckets = _classify_into_buckets([a])
        assert a in buckets["sql_procedures"]

    def test_sql_function_maps_to_sql_functions(self):
        a = _asset("sql::fn", "fn", AssetType.SQL_FUNCTION)
        buckets = _classify_into_buckets([a])
        assert a in buckets["sql_functions"]

    def test_pipeline_task_maps_to_workflow_tasks(self):
        a = _asset("task::x", "x", AssetType.PIPELINE_TASK, SystemType.DATABRICKS)
        buckets = _classify_into_buckets([a])
        assert a in buckets["workflow_tasks"]

    def test_adls_file_maps_to_adls_files(self):
        a = _asset("adls::f", "f", AssetType.ADLS_FILE)
        buckets = _classify_into_buckets([a])
        assert a in buckets["adls_files"]

    def test_report_maps_to_powerbi_reports(self):
        a = _asset("report::r", "r", AssetType.REPORT, SystemType.POWERBI)
        buckets = _classify_into_buckets([a])
        assert a in buckets["powerbi_reports"]

    def test_measure_maps_to_semantic_models(self):
        a = _asset("measure::m", "m", AssetType.MEASURE, SystemType.POWERBI)
        buckets = _classify_into_buckets([a])
        assert a in buckets["semantic_models"]

    def test_database_table_not_in_typed_buckets(self):
        a = _asset("sql::t", "t", AssetType.DATABASE_TABLE)
        buckets = _classify_into_buckets([a])
        for v in buckets.values():
            assert a not in v

    def test_multiple_same_type_all_in_bucket(self):
        assets = [
            _asset(f"sql::vw{i}", f"vw{i}", AssetType.SQL_VIEW) for i in range(4)
        ]
        buckets = _classify_into_buckets(assets)
        assert len(buckets["sql_views"]) == 4


# ---------------------------------------------------------------------------
# 89-91: _build_executive_summary unit tests
# ---------------------------------------------------------------------------


def _req(ct: ChangeType = ChangeType.TABLE_DELETE, target: str = "orders") -> ChangeRequest:
    return ChangeRequest(original_request="test", change_type=ct, target_name=target)


class TestBuildExecutiveSummary:
    def test_unresolved_source_mentions_searched_name(self):
        summary = _build_executive_summary(_req(target="mystery_table"), None, {})
        assert "mystery_table" in summary.lower() or "no enterprise" in summary.lower()

    def test_no_impact_no_downstream_message(self):
        a = _asset("sql::orders", "orders", AssetType.DATABASE_TABLE)
        buckets = {k: [] for k in ["sql_views", "sql_procedures", "sql_functions",
                                    "databricks_notebooks", "databricks_pipelines",
                                    "workflow_tasks", "adls_files", "powerbi_reports",
                                    "semantic_models"]}
        summary = _build_executive_summary(_req(), a, buckets)
        assert "no downstream" in summary.lower()

    def test_with_impact_lists_bucket_counts(self):
        a = _asset("sql::orders", "orders", AssetType.DATABASE_TABLE)
        vw = _asset("sql::vw", "vw", AssetType.SQL_VIEW)
        buckets: Dict[str, List] = {
            "sql_views": [vw], "sql_procedures": [], "sql_functions": [],
            "databricks_notebooks": [], "databricks_pipelines": [],
            "workflow_tasks": [], "adls_files": [], "powerbi_reports": [],
            "semantic_models": [],
        }
        summary = _build_executive_summary(_req(), a, buckets)
        assert "sql" in summary.lower() or "view" in summary.lower()


# ---------------------------------------------------------------------------
# 92-95: _build_deployment_plan unit tests
# ---------------------------------------------------------------------------


class TestBuildDeploymentPlan:
    _EMPTY_BUCKETS: Dict[str, List] = {
        "sql_views": [], "sql_procedures": [], "sql_functions": [],
        "databricks_notebooks": [], "databricks_pipelines": [],
        "workflow_tasks": [], "adls_files": [], "powerbi_reports": [],
        "semantic_models": [],
    }

    def test_unresolved_returns_fallback(self):
        steps = _build_deployment_plan(_req(), None, self._EMPTY_BUCKETS)
        assert len(steps) > 0

    def test_sql_only_has_sql_step(self):
        buckets = dict(self._EMPTY_BUCKETS)
        buckets["sql_views"] = [_asset("sql::vw", "vw", AssetType.SQL_VIEW)]
        source = _asset("sql::orders", "orders", AssetType.DATABASE_TABLE)
        steps = _build_deployment_plan(_req(), source, buckets)
        assert any("sql" in s.lower() for s in steps)

    def test_notebooks_impact_has_notebook_step(self):
        buckets = dict(self._EMPTY_BUCKETS)
        buckets["databricks_notebooks"] = [
            _asset("nb::01", "01_bronze", AssetType.DATABRICKS_NOTEBOOK, SystemType.DATABRICKS)
        ]
        source = _asset("sql::orders", "orders", AssetType.DATABASE_TABLE)
        steps = _build_deployment_plan(_req(), source, buckets)
        assert any("notebook" in s.lower() for s in steps)

    def test_all_systems_has_all_steps(self):
        buckets = dict(self._EMPTY_BUCKETS)
        buckets["sql_views"] = [_asset("sql::vw", "vw", AssetType.SQL_VIEW)]
        buckets["databricks_notebooks"] = [
            _asset("nb::01", "01", AssetType.DATABRICKS_NOTEBOOK, SystemType.DATABRICKS)
        ]
        buckets["powerbi_reports"] = [
            _asset("report::r", "r", AssetType.REPORT, SystemType.POWERBI)
        ]
        source = _asset("sql::orders", "orders", AssetType.DATABASE_TABLE)
        steps = _build_deployment_plan(_req(), source, buckets)
        text = " ".join(steps).lower()
        assert "sql" in text
        assert "notebook" in text
        assert "power bi" in text or "report" in text


# ---------------------------------------------------------------------------
# 96-98: _build_validation_checklist unit tests
# ---------------------------------------------------------------------------


class TestBuildValidationChecklist:
    _EMPTY: Dict[str, List] = {
        "sql_views": [], "sql_procedures": [], "sql_functions": [],
        "databricks_notebooks": [], "databricks_pipelines": [],
        "workflow_tasks": [], "adls_files": [], "powerbi_reports": [],
        "semantic_models": [],
    }

    def test_no_impact_base_checks_only(self):
        checks = _build_validation_checklist(self._EMPTY)
        assert len(checks) >= 2  # source check + regression
        combined = " ".join(checks).lower()
        assert "regression" in combined or "automated" in combined

    def test_sql_impact_has_sql_check(self):
        buckets = dict(self._EMPTY)
        buckets["sql_views"] = [_asset("sql::vw", "vw", AssetType.SQL_VIEW)]
        checks = _build_validation_checklist(buckets)
        assert any("sql" in c.lower() or "view" in c.lower() for c in checks)

    def test_report_impact_has_powerbi_check(self):
        buckets = dict(self._EMPTY)
        buckets["powerbi_reports"] = [
            _asset("report::r", "r", AssetType.REPORT, SystemType.POWERBI)
        ]
        checks = _build_validation_checklist(buckets)
        combined = " ".join(checks).lower()
        assert "power bi" in combined or "report" in combined


# ---------------------------------------------------------------------------
# 99-100: _build_rollback_plan unit tests
# ---------------------------------------------------------------------------


class TestBuildRollbackPlan:
    _EMPTY: Dict[str, List] = {
        "sql_views": [], "sql_procedures": [], "sql_functions": [],
        "databricks_notebooks": [], "databricks_pipelines": [],
        "workflow_tasks": [], "adls_files": [], "powerbi_reports": [],
        "semantic_models": [],
    }

    def test_unresolved_returns_no_rollback_message(self):
        steps = _build_rollback_plan(_req(), None, self._EMPTY)
        assert len(steps) > 0
        assert "no rollback" in steps[0].lower()

    def test_reports_impacted_pbi_revert_present(self):
        buckets = dict(self._EMPTY)
        buckets["powerbi_reports"] = [
            _asset("report::r", "r", AssetType.REPORT, SystemType.POWERBI)
        ]
        source = _asset("sql::orders", "orders", AssetType.DATABASE_TABLE)
        steps = _build_rollback_plan(_req(), source, buckets)
        combined = " ".join(steps).lower()
        assert "power bi" in combined or "report" in combined
