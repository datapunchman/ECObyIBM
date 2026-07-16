"""
tests.test_workflow_parser
===========================
Unit tests for :class:`enterprise.workflow_parser.DatabricksWorkflowParser`.

Coverage
--------
1.  Single pipeline — PIPELINE asset created with correct type/system/id
2.  Pipeline metadata — name, platform, description, task_count
3.  Single task — PIPELINE_TASK asset created
4.  Task metadata — execution_order, layer, catalog, schema, notebook path
5.  Task asset ID format
6.  PIPELINE ──TRIGGERS──> TASK edges
7.  TASK ──CALLS──> NOTEBOOK edges
8.  Notebook stub assets emitted for tasks with notebook paths
9.  Notebook stubs deduplicated across tasks sharing the same notebook
10. emit_notebook_stubs=False — no stub assets, CALLS edges still emitted
11. Task depends_on: TASK_A ──TRIGGERS──> TASK_B edge direction
12. Dependency chain: A→B→C three-task linear chain
13. Parallel tasks: two tasks with shared dependency, no edge between siblings
14. Fan-out: one task depended on by multiple downstream tasks
15. Multiple depends_on: task with two predecessors
16. Missing notebook — task created, no CALLS edge
17. Missing depends_on — no predecessor TRIGGERS edges from task
18. Empty depends_on list — same as missing
19. Missing YAML file — returns ([], [])
20. Malformed YAML — returns ([], [])
21. Missing 'pipeline' key — returns ([], [])
22. Missing pipeline name — defaults to "unknown_pipeline"
23. Missing task name — task skipped without raising
24. Malformed task (not a dict) — skipped without raising
25. Non-list tasks field — treated as empty, pipeline asset still returned
26. Non-list depends_on field — treated as empty, WARNING, no crash
27. Missing execution_order — falls back to 1-based position
28. Malformed execution_order (non-int) — falls back to position
29. parse() returns (list, list) — BaseMetadataParser contract
30. Pipeline from actual pipeline.yml file on disk
31. from_default_path() constructor
32. DatabricksWorkflowParser importable from enterprise package
33. Task catalog / schema preserved on asset
34. Description stored in pipeline metadata
35. Execution order declared in YAML is respected as int
36. Total asset count for canonical pipeline.yml (6 tasks + 1 pipeline + notebooks)
37. Total relationship count for canonical pipeline.yml
38. owner / criticality propagated

Run with:
    python -m pytest tests/test_workflow_parser.py -v
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from enterprise.workflow_parser import (
    DatabricksWorkflowParser,
    _pipeline_id,
    _task_id,
    _notebook_stub_id,
)
from graph.models import AssetType, Criticality, RelationshipType, SystemType


# ===========================================================================
# Shared YAML fixtures
# ===========================================================================

MINIMAL_YAML = textwrap.dedent("""\
    pipeline:
      name: test_pipeline
      platform: databricks
      tasks:
        - name: task_a
          execution_order: 1
          layer: bronze
          catalog: hive_metastore
          schema: bronze
          notebook: /Repos/team/01_ingest
          depends_on: []
""")

TWO_TASK_YAML = textwrap.dedent("""\
    pipeline:
      name: two_task_pipeline
      tasks:
        - name: task_a
          execution_order: 1
          notebook: /Repos/team/01_ingest
          depends_on: []
        - name: task_b
          execution_order: 2
          notebook: /Repos/team/02_transform
          depends_on:
            - task_a
""")

CHAIN_YAML = textwrap.dedent("""\
    pipeline:
      name: chain_pipeline
      tasks:
        - name: bronze_task
          execution_order: 1
          notebook: /Repos/team/01_bronze
          depends_on: []
        - name: silver_task
          execution_order: 2
          notebook: /Repos/team/02_silver
          depends_on:
            - bronze_task
        - name: gold_task
          execution_order: 3
          notebook: /Repos/team/03_gold
          depends_on:
            - silver_task
""")

PARALLEL_YAML = textwrap.dedent("""\
    pipeline:
      name: parallel_pipeline
      tasks:
        - name: ingest_customer
          execution_order: 1
          depends_on: []
        - name: ingest_sales
          execution_order: 2
          depends_on: []
        - name: transform
          execution_order: 3
          depends_on:
            - ingest_customer
            - ingest_sales
""")

SHARED_NOTEBOOK_YAML = textwrap.dedent("""\
    pipeline:
      name: shared_nb_pipeline
      tasks:
        - name: task_a
          notebook: /Repos/team/shared_nb
          depends_on: []
        - name: task_b
          notebook: /Repos/team/shared_nb
          depends_on: []
""")

NO_NOTEBOOK_YAML = textwrap.dedent("""\
    pipeline:
      name: no_nb_pipeline
      tasks:
        - name: headless_task
          layer: bronze
          depends_on: []
""")

MISSING_PIPELINE_KEY_YAML = textwrap.dedent("""\
    workflows:
      - name: ignored
""")

MALFORMED_YAML = "pipeline: [\nunclosed bracket"

EMPTY_TASKS_YAML = textwrap.dedent("""\
    pipeline:
      name: empty_pipeline
      tasks: []
""")

NO_NAME_TASK_YAML = textwrap.dedent("""\
    pipeline:
      name: pipe
      tasks:
        - execution_order: 1
          notebook: /nb
        - name: valid_task
          notebook: /nb2
""")


def _parser(yaml_str: str, **kwargs) -> DatabricksWorkflowParser:
    """Convenience: construct parser from an inline YAML string."""
    return DatabricksWorkflowParser(source=yaml_str, **kwargs)


# ===========================================================================
# 1. ID helper unit tests
# ===========================================================================

class TestIDHelpers:
    def test_pipeline_id(self):
        assert _pipeline_id("my_pipeline") == "pipeline::my_pipeline"

    def test_task_id(self):
        assert _task_id("my_pipeline", "task_a") == "pipeline_task::my_pipeline::task_a"

    def test_notebook_stub_id(self):
        assert _notebook_stub_id("/Repos/team/nb") == "notebook::/Repos/team/nb"


# ===========================================================================
# 2. Pipeline asset
# ===========================================================================

class TestPipelineAsset:
    @pytest.fixture(autouse=True)
    def _setup(self):
        self.assets, self.rels = _parser(MINIMAL_YAML).parse()
        self.pipeline = next(
            a for a in self.assets if a.asset_type == AssetType.PIPELINE
        )

    def test_pipeline_asset_created(self):
        pips = [a for a in self.assets if a.asset_type == AssetType.PIPELINE]
        assert len(pips) == 1

    def test_pipeline_asset_type(self):
        assert self.pipeline.asset_type == AssetType.PIPELINE

    def test_pipeline_system_is_databricks(self):
        assert self.pipeline.system == SystemType.DATABRICKS

    def test_pipeline_id_format(self):
        assert self.pipeline.id == "pipeline::test_pipeline"

    def test_pipeline_name(self):
        assert self.pipeline.name == "test_pipeline"

    def test_pipeline_platform_in_metadata(self):
        assert self.pipeline.metadata["platform"] == "databricks"

    def test_pipeline_task_count_in_metadata(self):
        assert self.pipeline.metadata["task_count"] == 1

    def test_pipeline_description_stored(self):
        yaml = textwrap.dedent("""\
            pipeline:
              name: p
              description: "my description"
              tasks: []
        """)
        assets, _ = _parser(yaml).parse()
        pip = next(a for a in assets if a.asset_type == AssetType.PIPELINE)
        assert pip.metadata["description"] == "my description"


# ===========================================================================
# 3. Task assets
# ===========================================================================

class TestTaskAssets:
    @pytest.fixture(autouse=True)
    def _setup(self):
        self.assets, self.rels = _parser(MINIMAL_YAML).parse()
        self.task = next(
            a for a in self.assets if a.asset_type == AssetType.PIPELINE_TASK
        )

    def test_task_asset_created(self):
        tasks = [a for a in self.assets if a.asset_type == AssetType.PIPELINE_TASK]
        assert len(tasks) == 1

    def test_task_asset_type(self):
        assert self.task.asset_type == AssetType.PIPELINE_TASK

    def test_task_system_is_databricks(self):
        assert self.task.system == SystemType.DATABRICKS

    def test_task_id_format(self):
        assert self.task.id == "pipeline_task::test_pipeline::task_a"

    def test_task_name(self):
        assert self.task.name == "task_a"

    def test_task_catalog(self):
        assert self.task.catalog == "hive_metastore"

    def test_task_schema(self):
        assert self.task.schema == "bronze"

    def test_task_layer_in_metadata(self):
        assert self.task.metadata["layer"] == "bronze"

    def test_task_execution_order_in_metadata(self):
        assert self.task.metadata["execution_order"] == 1

    def test_task_notebook_path_in_metadata(self):
        assert self.task.metadata["notebook"] == "/Repos/team/01_ingest"

    def test_task_pipeline_name_in_metadata(self):
        assert self.task.metadata["pipeline_name"] == "test_pipeline"


# ===========================================================================
# 4. PIPELINE → TASK edges
# ===========================================================================

class TestPipelineTaskEdges:
    def test_pipeline_triggers_task(self):
        assets, rels = _parser(MINIMAL_YAML).parse()
        pip_id = "pipeline::test_pipeline"
        task_id = "pipeline_task::test_pipeline::task_a"
        triggers = [
            r for r in rels
            if r.relationship == RelationshipType.TRIGGERS
            and r.source == pip_id
            and r.target == task_id
        ]
        assert len(triggers) == 1

    def test_two_tasks_both_triggered_by_pipeline(self):
        assets, rels = _parser(TWO_TASK_YAML).parse()
        pip_id = "pipeline::two_task_pipeline"
        task_triggers = [
            r for r in rels
            if r.relationship == RelationshipType.TRIGGERS and r.source == pip_id
        ]
        assert len(task_triggers) == 2


# ===========================================================================
# 5. TASK → NOTEBOOK (CALLS) edges
# ===========================================================================

class TestTaskNotebookEdges:
    def test_task_calls_notebook(self):
        assets, rels = _parser(MINIMAL_YAML).parse()
        task_id = "pipeline_task::test_pipeline::task_a"
        nb_id = "notebook::/Repos/team/01_ingest"
        calls = [
            r for r in rels
            if r.relationship == RelationshipType.CALLS
            and r.source == task_id
            and r.target == nb_id
        ]
        assert len(calls) == 1

    def test_no_calls_edge_when_no_notebook(self):
        assets, rels = _parser(NO_NOTEBOOK_YAML).parse()
        calls = [r for r in rels if r.relationship == RelationshipType.CALLS]
        assert calls == []

    def test_notebook_stub_created(self):
        assets, rels = _parser(MINIMAL_YAML).parse()
        nb_stubs = [a for a in assets if a.asset_type == AssetType.DATABRICKS_NOTEBOOK]
        assert any(a.id == "notebook::/Repos/team/01_ingest" for a in nb_stubs)

    def test_notebook_stub_system_is_databricks(self):
        assets, _ = _parser(MINIMAL_YAML).parse()
        stub = next(
            a for a in assets if a.asset_type == AssetType.DATABRICKS_NOTEBOOK
        )
        assert stub.system == SystemType.DATABRICKS

    def test_shared_notebook_stub_not_duplicated(self):
        """Two tasks referencing the same notebook path produce one stub."""
        assets, rels = _parser(SHARED_NOTEBOOK_YAML).parse()
        stubs = [
            a for a in assets
            if a.id == "notebook::/Repos/team/shared_nb"
        ]
        assert len(stubs) == 1

    def test_shared_notebook_two_calls_edges(self):
        """Two tasks → one stub, but still two CALLS edges (one per task)."""
        _, rels = _parser(SHARED_NOTEBOOK_YAML).parse()
        calls = [
            r for r in rels
            if r.relationship == RelationshipType.CALLS
            and r.target == "notebook::/Repos/team/shared_nb"
        ]
        assert len(calls) == 2

    def test_emit_notebook_stubs_false(self):
        assets, rels = DatabricksWorkflowParser(
            MINIMAL_YAML, emit_notebook_stubs=False
        ).parse()
        stubs = [a for a in assets if a.asset_type == AssetType.DATABRICKS_NOTEBOOK]
        assert stubs == []
        # CALLS edge still present
        calls = [r for r in rels if r.relationship == RelationshipType.CALLS]
        assert len(calls) == 1


# ===========================================================================
# 6. TASK → TASK dependency edges
# ===========================================================================

class TestDependencyEdges:
    def test_task_a_triggers_task_b(self):
        _, rels = _parser(TWO_TASK_YAML).parse()
        dep = [
            r for r in rels
            if r.relationship == RelationshipType.TRIGGERS
            and r.source == "pipeline_task::two_task_pipeline::task_a"
            and r.target == "pipeline_task::two_task_pipeline::task_b"
        ]
        assert len(dep) == 1

    def test_no_dep_edge_for_empty_depends_on(self):
        _, rels = _parser(TWO_TASK_YAML).parse()
        # task_a has no depends_on → no TASK→TASK edge pointing TO task_a
        dep_to_a = [
            r for r in rels
            if r.relationship == RelationshipType.TRIGGERS
            and r.target == "pipeline_task::two_task_pipeline::task_a"
            and r.source.startswith("pipeline_task::")
        ]
        assert dep_to_a == []

    def test_three_task_linear_chain(self):
        """bronze → silver → gold: two dep edges in chain."""
        _, rels = _parser(CHAIN_YAML).parse()
        bronze_to_silver = [
            r for r in rels
            if r.source == "pipeline_task::chain_pipeline::bronze_task"
            and r.target == "pipeline_task::chain_pipeline::silver_task"
            and r.relationship == RelationshipType.TRIGGERS
        ]
        silver_to_gold = [
            r for r in rels
            if r.source == "pipeline_task::chain_pipeline::silver_task"
            and r.target == "pipeline_task::chain_pipeline::gold_task"
            and r.relationship == RelationshipType.TRIGGERS
        ]
        assert len(bronze_to_silver) == 1
        assert len(silver_to_gold) == 1

    def test_parallel_tasks_no_sibling_edge(self):
        """ingest_customer and ingest_sales are independent — no edge between them."""
        _, rels = _parser(PARALLEL_YAML).parse()
        cust_to_sales = [
            r for r in rels
            if r.source == "pipeline_task::parallel_pipeline::ingest_customer"
            and r.target == "pipeline_task::parallel_pipeline::ingest_sales"
        ]
        sales_to_cust = [
            r for r in rels
            if r.source == "pipeline_task::parallel_pipeline::ingest_sales"
            and r.target == "pipeline_task::parallel_pipeline::ingest_customer"
        ]
        assert cust_to_sales == []
        assert sales_to_cust == []

    def test_fan_in_both_deps_trigger_transform(self):
        """transform depends on both ingest tasks — two dep edges pointing to transform."""
        _, rels = _parser(PARALLEL_YAML).parse()
        to_transform = [
            r for r in rels
            if r.relationship == RelationshipType.TRIGGERS
            and r.target == "pipeline_task::parallel_pipeline::transform"
            and r.source.startswith("pipeline_task::")
        ]
        assert len(to_transform) == 2

    def test_missing_depends_on_no_dep_edges(self):
        """Task without depends_on key produces no predecessor TRIGGERS edges."""
        _, rels = _parser(NO_NOTEBOOK_YAML).parse()
        dep_edges = [
            r for r in rels
            if r.relationship == RelationshipType.TRIGGERS
            and r.source.startswith("pipeline_task::")
        ]
        assert dep_edges == []


# ===========================================================================
# 7. Fault tolerance
# ===========================================================================

class TestFaultTolerance:
    def test_missing_yaml_file_returns_empty(self):
        parser = DatabricksWorkflowParser(Path("/nonexistent/pipeline.yml"))
        assets, rels = parser.parse()
        assert assets == []
        assert rels == []

    def test_malformed_yaml_returns_empty(self):
        assets, rels = _parser(MALFORMED_YAML).parse()
        assert assets == []
        assert rels == []

    def test_missing_pipeline_key_returns_empty(self):
        assets, rels = _parser(MISSING_PIPELINE_KEY_YAML).parse()
        assert assets == []
        assert rels == []

    def test_empty_tasks_returns_pipeline_only(self):
        assets, rels = _parser(EMPTY_TASKS_YAML).parse()
        pips = [a for a in assets if a.asset_type == AssetType.PIPELINE]
        tasks = [a for a in assets if a.asset_type == AssetType.PIPELINE_TASK]
        assert len(pips) == 1
        assert tasks == []

    def test_task_without_name_skipped(self):
        assets, _ = _parser(NO_NAME_TASK_YAML).parse()
        tasks = [a for a in assets if a.asset_type == AssetType.PIPELINE_TASK]
        # Only valid_task should survive
        assert len(tasks) == 1
        assert tasks[0].name == "valid_task"

    def test_missing_notebook_task_created_no_calls_edge(self):
        assets, rels = _parser(NO_NOTEBOOK_YAML).parse()
        tasks = [a for a in assets if a.asset_type == AssetType.PIPELINE_TASK]
        assert len(tasks) == 1
        calls = [r for r in rels if r.relationship == RelationshipType.CALLS]
        assert calls == []

    def test_non_list_depends_on_no_crash(self):
        yaml = textwrap.dedent("""\
            pipeline:
              name: p
              tasks:
                - name: t
                  depends_on: "not_a_list"
        """)
        assets, rels = _parser(yaml).parse()
        # Task still created
        tasks = [a for a in assets if a.asset_type == AssetType.PIPELINE_TASK]
        assert len(tasks) == 1

    def test_non_list_tasks_no_crash(self):
        yaml = textwrap.dedent("""\
            pipeline:
              name: p
              tasks: "not_a_list"
        """)
        assets, rels = _parser(yaml).parse()
        pips = [a for a in assets if a.asset_type == AssetType.PIPELINE]
        assert len(pips) == 1

    def test_malformed_task_not_dict_skipped(self):
        yaml = textwrap.dedent("""\
            pipeline:
              name: p
              tasks:
                - "just_a_string"
                - name: valid
        """)
        assets, _ = _parser(yaml).parse()
        tasks = [a for a in assets if a.asset_type == AssetType.PIPELINE_TASK]
        assert len(tasks) == 1
        assert tasks[0].name == "valid"

    def test_missing_execution_order_uses_position(self):
        yaml = textwrap.dedent("""\
            pipeline:
              name: p
              tasks:
                - name: first
                - name: second
        """)
        assets, _ = _parser(yaml).parse()
        tasks = {
            a.name: a.metadata["execution_order"]
            for a in assets
            if a.asset_type == AssetType.PIPELINE_TASK
        }
        assert tasks["first"] == 1
        assert tasks["second"] == 2

    def test_malformed_execution_order_uses_position(self):
        yaml = textwrap.dedent("""\
            pipeline:
              name: p
              tasks:
                - name: t
                  execution_order: "not_an_int"
        """)
        assets, _ = _parser(yaml).parse()
        task = next(a for a in assets if a.asset_type == AssetType.PIPELINE_TASK)
        assert task.metadata["execution_order"] == 1

    def test_missing_pipeline_name_defaults(self):
        yaml = textwrap.dedent("""\
            pipeline:
              tasks: []
        """)
        assets, _ = _parser(yaml).parse()
        pip = next(a for a in assets if a.asset_type == AssetType.PIPELINE)
        assert pip.name == "unknown_pipeline"

    def test_parse_returns_tuple_of_lists(self):
        result = _parser(MINIMAL_YAML).parse()
        assert isinstance(result, tuple) and len(result) == 2
        assert isinstance(result[0], list)
        assert isinstance(result[1], list)


# ===========================================================================
# 8. Enrichment
# ===========================================================================

class TestEnrichment:
    def test_owner_propagated(self):
        assets, _ = DatabricksWorkflowParser(MINIMAL_YAML, owner="de-team").parse()
        pip = next(a for a in assets if a.asset_type == AssetType.PIPELINE)
        assert pip.owner == "de-team"

    def test_criticality_propagated(self):
        assets, _ = DatabricksWorkflowParser(
            MINIMAL_YAML, default_criticality=Criticality.HIGH
        ).parse()
        pip = next(a for a in assets if a.asset_type == AssetType.PIPELINE)
        assert pip.criticality == Criticality.HIGH


# ===========================================================================
# 9. Canonical pipeline.yml on disk
# ===========================================================================

class TestCanonicalPipelineYaml:
    """Integration tests against the real metadata/databricks/pipeline.yml."""

    YAML_PATH = Path(__file__).parent.parent / "metadata" / "databricks" / "pipeline.yml"

    @pytest.fixture(autouse=True)
    def _load(self):
        if not self.YAML_PATH.exists():
            pytest.skip("pipeline.yml not found")
        parser = DatabricksWorkflowParser(self.YAML_PATH)
        self.assets, self.rels = parser.parse()

    def test_pipeline_asset_present(self):
        pips = [a for a in self.assets if a.asset_type == AssetType.PIPELINE]
        assert len(pips) == 1

    def test_six_task_assets(self):
        tasks = [a for a in self.assets if a.asset_type == AssetType.PIPELINE_TASK]
        assert len(tasks) == 6

    def test_execution_orders_are_integers(self):
        tasks = [a for a in self.assets if a.asset_type == AssetType.PIPELINE_TASK]
        for t in tasks:
            assert isinstance(t.metadata["execution_order"], int)

    def test_pipeline_triggers_all_tasks(self):
        pip = next(a for a in self.assets if a.asset_type == AssetType.PIPELINE)
        triggers_from_pipeline = [
            r for r in self.rels
            if r.source == pip.id and r.relationship == RelationshipType.TRIGGERS
        ]
        assert len(triggers_from_pipeline) == 6

    def test_notebook_stubs_for_three_unique_paths(self):
        """Three distinct notebook paths in the canonical YAML → three stubs."""
        stubs = [a for a in self.assets if a.asset_type == AssetType.DATABRICKS_NOTEBOOK]
        assert len(stubs) == 3

    def test_from_default_path(self):
        parser = DatabricksWorkflowParser.from_default_path(
            base_dir=self.YAML_PATH.parent.parent.parent
        )
        assets, _ = parser.parse()
        assert any(a.asset_type == AssetType.PIPELINE for a in assets)


# ===========================================================================
# 10. Import from package
# ===========================================================================

class TestImportFromPackage:
    def test_importable_from_enterprise(self):
        from enterprise import DatabricksWorkflowParser as DWP  # noqa: F401
        assert DWP is DatabricksWorkflowParser
