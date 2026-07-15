"""
tests.test_hallucination_guard
================================
Regression tests for the two-layer hallucination-prevention system.

Layer 1 — Prompt construction
    :func:`PromptBuilder.build_from_graph` must:
    * Include each non-empty bucket in the IMPACTED ASSETS section.
    * Include each empty bucket in the NOT IMPACTED / EMPTY section.
    * Include a per-item "Do NOT mention" prohibition for every empty bucket.

Layer 2 — Post-parse scrubber
    :func:`ResponseParser.parse_v2` must:
    * Remove any deployment_plan / validation_checklist / rollback_plan item
      that mentions a system whose bucket was empty.
    * Leave items that only reference systems whose buckets are non-empty.
    * Record removed items in ``_scrubbed_items`` for audit.
    * Pass through clean responses unchanged.

Regression contract
    If Granite mentions "notebook" and ``databricks_notebooks`` is empty →
    that item MUST be absent from the returned llm_summary.

Run with:
    python -m pytest tests/test_hallucination_guard.py -v
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock

import pytest

from ai.graph_orchestrator import ENTERPRISE_BUCKETS
from ai.prompt_builder import PromptBuilder
from ai.response_parser import ResponseParser, _scrub_absent_systems


# ---------------------------------------------------------------------------
# Helpers — build minimal EnterpriseGraphResult-like objects
# ---------------------------------------------------------------------------

def _make_graph_result(
    present: Dict[str, List[str]],
) -> MagicMock:
    """Build a mock EnterpriseGraphResult.

    Parameters
    ----------
    present:
        Mapping of bucket_name → list of asset name strings to put in that
        bucket.  All other buckets are set to empty lists.
    """
    # Build ImpactedAsset-like mocks for each present asset
    def _make_impacted(name: str) -> MagicMock:
        ia = MagicMock()
        ia.asset.name = name
        return ia

    ga: Dict[str, Any] = {}
    for bucket in ENTERPRISE_BUCKETS:
        if bucket in present:
            ga[bucket] = [_make_impacted(n) for n in present[bucket]]
        else:
            ga[bucket] = []

    ga["dependency_paths"] = []
    ga["metrics"] = {
        "total_assets": sum(len(v) for v in ga.values() if isinstance(v, list)),
        "critical_assets": 0,
        "max_depth": 0,
        "systems_impacted": len(present),
    }

    gr = MagicMock()
    gr.graph_analysis = ga
    gr.dependency_paths = []
    gr.metrics = ga["metrics"]
    gr.source_asset = MagicMock()
    gr.source_asset.name = "Revenue"
    return gr


def _make_request() -> MagicMock:
    req = MagicMock()
    req.request = "Rename the Revenue column in sales_dashboard to GrossRevenue"
    return req


def _fake_llm_response(**fields) -> str:
    """Build a JSON string as if returned by Granite."""
    payload = {
        "executive_summary": fields.get("executive_summary", "Summary."),
        "risk_level": fields.get("risk_level", "medium"),
        "risk_rationale": fields.get("risk_rationale", "Rationale."),
        "deployment_plan": fields.get("deployment_plan", []),
        "validation_checklist": fields.get("validation_checklist", []),
        "rollback_plan": fields.get("rollback_plan", []),
    }
    return json.dumps(payload)


# ---------------------------------------------------------------------------
# Layer 1 — Prompt construction tests
# ---------------------------------------------------------------------------

class TestPromptConstruction:
    """build_from_graph() must correctly separate present and absent buckets."""

    @pytest.fixture(autouse=True)
    def _setup(self):
        self.builder = PromptBuilder()
        self.request = _make_request()

    def test_present_bucket_appears_in_impacted_section(self):
        gr = _make_graph_result({"powerbi_reports": ["Sales Overview"]})
        prompt = self.builder.build_from_graph(self.request, gr)
        assert "powerbi_reports: Sales Overview" in prompt, (
            "Non-empty bucket must appear in IMPACTED ASSETS section"
        )

    def test_empty_bucket_appears_as_empty_in_prompt(self):
        gr = _make_graph_result({"powerbi_reports": ["Sales Overview"]})
        prompt = self.builder.build_from_graph(self.request, gr)
        # databricks_notebooks is empty → must appear as EMPTY
        assert "databricks_notebooks: EMPTY" in prompt, (
            "Empty bucket must be listed as EMPTY so the model sees it explicitly"
        )

    def test_empty_bucket_has_explicit_do_not_mention_prohibition(self):
        gr = _make_graph_result({"powerbi_reports": ["Sales Overview"]})
        prompt = self.builder.build_from_graph(self.request, gr)
        assert "Do NOT mention Databricks notebooks" in prompt, (
            "Per-item prohibition must appear for each empty bucket"
        )

    def test_pipeline_empty_bucket_prohibition(self):
        gr = _make_graph_result({"database_tables": ["sales_dashboard"]})
        prompt = self.builder.build_from_graph(self.request, gr)
        assert "Do NOT mention pipelines" in prompt

    def test_no_prohibition_for_present_bucket(self):
        gr = _make_graph_result({"databricks_notebooks": ["etl_notebook"]})
        prompt = self.builder.build_from_graph(self.request, gr)
        assert "Do NOT mention Databricks notebooks" not in prompt, (
            "Must not prohibit a system that IS present in the graph"
        )

    def test_all_empty_produces_prohibition_for_each_bucket(self):
        """When no assets exist, every bucket should be prohibited."""
        gr = _make_graph_result({})
        prompt = self.builder.build_from_graph(self.request, gr)
        # At minimum, spot-check a few expected prohibitions
        for label in ["Databricks notebooks", "pipelines", "Power BI reports"]:
            assert f"Do NOT mention {label}" in prompt, (
                f"Expected prohibition for {label!r} when all buckets empty"
            )

    def test_absolute_rules_section_present(self):
        gr = _make_graph_result({})
        prompt = self.builder.build_from_graph(self.request, gr)
        assert "ABSOLUTE RULES" in prompt
        assert "violation is not permitted" in prompt

    def test_fundamental_constraint_in_role_section(self):
        gr = _make_graph_result({})
        prompt = self.builder.build_from_graph(self.request, gr)
        assert "FUNDAMENTAL CONSTRAINT" in prompt
        assert "REASON over" in prompt or "reason over" in prompt.lower()


# ---------------------------------------------------------------------------
# Layer 2 — Scrubber unit tests (_scrub_absent_systems)
# ---------------------------------------------------------------------------

class TestScrubAbsentSystems:
    """_scrub_absent_systems() removes items for empty buckets."""

    def test_notebook_mention_scrubbed_when_bucket_empty(self):
        summary = {
            "deployment_plan": [
                "Update the Databricks notebook to reflect schema change.",
                "Refresh the Power BI semantic model.",
            ],
            "validation_checklist": [],
            "rollback_plan": [],
        }
        removed = _scrub_absent_systems(summary, {"databricks_notebooks"})
        assert len(removed) == 1
        assert "Databricks notebook" in removed[0]
        assert len(summary["deployment_plan"]) == 1
        assert "Refresh the Power BI semantic model." in summary["deployment_plan"]

    def test_pipeline_mention_scrubbed_when_bucket_empty(self):
        summary = {
            "deployment_plan": [
                "Trigger the ETL pipeline to reload data.",
                "Validate the Power BI report.",
            ],
            "validation_checklist": [],
            "rollback_plan": [],
        }
        removed = _scrub_absent_systems(summary, {"pipelines"})
        assert any("pipeline" in r.lower() for r in removed)
        assert all("pipeline" not in item.lower() for item in summary["deployment_plan"])

    def test_multiple_empty_buckets_all_scrubbed(self):
        summary = {
            "deployment_plan": [
                "Update the Databricks notebook.",
                "Trigger the pipeline.",
                "Refresh the Power BI semantic model.",
            ],
            "validation_checklist": [],
            "rollback_plan": [],
        }
        removed = _scrub_absent_systems(
            summary, {"databricks_notebooks", "pipelines"}
        )
        assert len(removed) == 2
        assert len(summary["deployment_plan"]) == 1
        assert "semantic model" in summary["deployment_plan"][0].lower()

    def test_present_bucket_item_not_scrubbed(self):
        """Items referencing present systems must survive."""
        summary = {
            "deployment_plan": ["Refresh the Power BI report."],
            "validation_checklist": [],
            "rollback_plan": [],
        }
        # powerbi_reports is NOT in empty_buckets
        removed = _scrub_absent_systems(summary, {"databricks_notebooks"})
        assert removed == []
        assert len(summary["deployment_plan"]) == 1

    def test_empty_buckets_set_produces_no_scrubbing(self):
        summary = {
            "deployment_plan": ["Do something."],
            "validation_checklist": [],
            "rollback_plan": [],
        }
        removed = _scrub_absent_systems(summary, set())
        assert removed == []

    def test_scrubber_checks_all_three_fields(self):
        summary = {
            "deployment_plan":      ["Update the Databricks notebook."],
            "validation_checklist": ["Verify the Databricks notebook is updated."],
            "rollback_plan":        ["Revert the Databricks notebook changes."],
        }
        removed = _scrub_absent_systems(summary, {"databricks_notebooks"})
        assert len(removed) == 3
        assert summary["deployment_plan"] == []
        assert summary["validation_checklist"] == []
        assert summary["rollback_plan"] == []

    def test_clean_item_without_system_keywords_kept(self):
        summary = {
            "deployment_plan": ["Deploy schema change in production."],
            "validation_checklist": [],
            "rollback_plan": [],
        }
        removed = _scrub_absent_systems(
            summary, {"databricks_notebooks", "pipelines"}
        )
        assert removed == []
        assert len(summary["deployment_plan"]) == 1


# ---------------------------------------------------------------------------
# Layer 2 — parse_v2 integration (scrubber wired into parser)
# ---------------------------------------------------------------------------

class TestParseV2WithScrubber:
    """parse_v2() with empty_buckets scrubs hallucinated items."""

    @pytest.fixture(autouse=True)
    def _setup(self):
        self.parser = ResponseParser()

    def test_scrubbed_items_recorded_in_result(self):
        raw = _fake_llm_response(
            deployment_plan=[
                "Update the Databricks notebook.",
                "Refresh Power BI semantic model.",
            ]
        )
        result = self.parser.parse_v2(raw, empty_buckets={"databricks_notebooks"})
        assert "_scrubbed_items" in result
        assert any("Databricks notebook" in s for s in result["_scrubbed_items"])

    def test_no_scrubbed_key_when_nothing_removed(self):
        raw = _fake_llm_response(
            deployment_plan=["Refresh the Power BI semantic model."]
        )
        result = self.parser.parse_v2(raw, empty_buckets={"databricks_notebooks"})
        assert "_scrubbed_items" not in result

    def test_hallucinated_notebook_never_reaches_caller(self):
        """Core regression: notebook mention → gone when bucket empty."""
        raw = _fake_llm_response(
            deployment_plan=[
                "Run the ETL notebook in Databricks workspace.",
                "Validate Power BI report refresh.",
            ]
        )
        result = self.parser.parse_v2(raw, empty_buckets={"databricks_notebooks"})
        plan_text = " ".join(result["deployment_plan"]).lower()
        assert "notebook" not in plan_text, (
            "REGRESSION: 'notebook' appeared in deployment_plan even though "
            "databricks_notebooks bucket was empty"
        )

    def test_hallucinated_pipeline_never_reaches_caller(self):
        raw = _fake_llm_response(
            validation_checklist=[
                "Confirm the data pipeline ran successfully.",
                "Check Power BI report data is current.",
            ]
        )
        result = self.parser.parse_v2(raw, empty_buckets={"pipelines"})
        checklist_text = " ".join(result["validation_checklist"]).lower()
        assert "pipeline" not in checklist_text, (
            "REGRESSION: 'pipeline' appeared in validation_checklist even though "
            "pipelines bucket was empty"
        )

    def test_no_empty_buckets_arg_disables_scrubbing(self):
        raw = _fake_llm_response(
            deployment_plan=["Update the Databricks notebook."]
        )
        result = self.parser.parse_v2(raw)  # no empty_buckets
        assert "Update the Databricks notebook." in result["deployment_plan"]

    def test_parse_still_succeeds_with_empty_buckets(self):
        raw = _fake_llm_response()
        result = self.parser.parse_v2(raw, empty_buckets={"pipelines", "databricks_notebooks"})
        assert result["risk_level"] == "medium"
        assert isinstance(result["deployment_plan"], list)
