"""
enterprise.workflow_parser
==========================
Databricks Workflow Parser — reads ``pipeline.yml`` and converts it into
:class:`~graph.enterprise_graph.EnterpriseGraph`-compatible assets and edges.

This parser does **NOT** parse or execute notebook source code.  It reads
only the structured ``pipeline.yml`` YAML file that declares the Databricks
Workflow definition.

YAML Schema
-----------
.. code-block:: yaml

    pipeline:
      name:        medallion_data_pipeline   # required
      platform:    databricks                # optional
      description: "..."                     # optional

      tasks:
        - name:            ingest_customer_data  # required
          execution_order: 1                     # optional int
          layer:           bronze                # optional
          catalog:         hive_metastore        # optional
          schema:          bronze                # optional
          notebook:        /Repos/de-team/01_...  # optional
          depends_on:      []                    # optional list of task names

Graph Model
-----------
For a pipeline with N tasks::

    PIPELINE(pipeline_name)
        ──TRIGGERS──>  PIPELINE_TASK(task_1)
        ──TRIGGERS──>  PIPELINE_TASK(task_2)
        ──TRIGGERS──>  PIPELINE_TASK(task_N)

    PIPELINE_TASK(task_A)
        ──TRIGGERS──>  PIPELINE_TASK(task_B)   ← when task_B depends_on task_A

    PIPELINE_TASK(task_X)
        ──CALLS──>     DATABRICKS_NOTEBOOK(notebook_path)   ← when notebook present

Asset IDs:
    pipeline:  ``pipeline::<pipeline_name>``
    task:      ``pipeline_task::<pipeline_name>::<task_name>``
    notebook:  ``notebook::<notebook_path>``  (stub, unresolved=True)

Fault Tolerance
---------------
- Missing YAML file          → returns ``([], [])`` with a WARNING log.
- Malformed YAML             → returns ``([], [])`` with a WARNING log.
- Missing ``pipeline`` key   → returns ``([], [])`` with a WARNING log.
- Missing task ``name``      → task is skipped with a WARNING log.
- Missing ``notebook``       → task asset created, no CALLS edge.
- Empty ``depends_on``       → no TRIGGERS edges from that task to predecessors.
- Non-list ``depends_on``    → treated as empty, WARNING log.
- Unknown depends_on target  → edge still created (unresolved=True); the
                               registry resolves cross-source references.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from enterprise.parsers import BaseMetadataParser, ParseResult
from graph.models import (
    Asset,
    AssetType,
    Criticality,
    Relationship,
    RelationshipType,
    SystemType,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# YAML import — stdlib only; PyYAML is the standard Databricks dependency
# ---------------------------------------------------------------------------

try:
    import yaml as _yaml  # type: ignore[import]
    _YAML_AVAILABLE = True
except ImportError:  # pragma: no cover
    _yaml = None  # type: ignore[assignment]
    _YAML_AVAILABLE = False


# ---------------------------------------------------------------------------
# ID helpers
# ---------------------------------------------------------------------------

def _pipeline_id(pipeline_name: str) -> str:
    """Return canonical asset ID for a pipeline.

    Args:
        pipeline_name: Name of the pipeline.

    Returns:
        Asset ID string, e.g. ``"pipeline::medallion_data_pipeline"``.
    """
    return f"pipeline::{pipeline_name}"


def _task_id(pipeline_name: str, task_name: str) -> str:
    """Return canonical asset ID for a pipeline task.

    Args:
        pipeline_name: Name of the parent pipeline.
        task_name:     Name of the task.

    Returns:
        Asset ID string, e.g.
        ``"pipeline_task::medallion_data_pipeline::ingest_customer_data"``.
    """
    return f"pipeline_task::{pipeline_name}::{task_name}"


def _normalise_notebook_path(notebook_path: str) -> str:
    """Return the basename of a notebook path for ID normalisation.

    The workflow YAML records full Repos paths such as
    ``/Repos/de-team/01_bronze_ingestion_framework`` while the
    EcoNotebookParser uses only the bare notebook name extracted from
    the ``NOTEBOOK_NAME`` field (``01_bronze_ingestion_framework``).

    Stripping to the basename makes both parsers produce the same
    canonical ``notebook::<name>`` ID so that CALLS edges from pipeline
    tasks connect to the real notebook assets in the graph.

    Examples:
        ``/Repos/de-team/01_bronze`` → ``01_bronze``
        ``01_bronze``                → ``01_bronze``
        ``/01_bronze/``              → ``01_bronze``

    Args:
        notebook_path: Raw path string from the YAML ``notebook`` field.

    Returns:
        Basename string with leading/trailing slashes stripped.
    """
    return notebook_path.strip("/").rsplit("/", 1)[-1]


def _notebook_stub_id(notebook_path: str) -> str:
    """Return canonical asset ID for a notebook stub reference.

    Normalises *notebook_path* to its basename so that the stub ID
    matches the ID produced by :class:`~enterprise.notebook_parser.EcoNotebookParser`
    for the same notebook.

    Args:
        notebook_path: Notebook path string as declared in the YAML,
            e.g. ``/Repos/de-team/01_bronze_ingestion_framework``.

    Returns:
        Asset ID string using the basename only, e.g.
        ``"notebook::01_bronze_ingestion_framework"``.
    """
    return f"notebook::{_normalise_notebook_path(notebook_path)}"


# ---------------------------------------------------------------------------
# YAML loading helper
# ---------------------------------------------------------------------------

def _load_yaml(source: Union[str, Path]) -> Optional[Dict[str, Any]]:
    """Load and parse a YAML file or string.

    Args:
        source: A :class:`pathlib.Path` pointing to the YAML file, or a
                raw YAML string for testing.

    Returns:
        Parsed dict, or ``None`` on any error (file not found, malformed YAML,
        missing PyYAML).
    """
    if not _YAML_AVAILABLE:  # pragma: no cover
        logger.error(
            "DatabricksWorkflowParser: PyYAML is not installed — "
            "install it with: pip install pyyaml"
        )
        return None

    if isinstance(source, Path):
        try:
            text = source.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning(
                "DatabricksWorkflowParser: cannot read %s — %s", source, exc
            )
            return None
    else:
        text = str(source)

    try:
        data = _yaml.safe_load(text)
    except _yaml.YAMLError as exc:
        logger.warning(
            "DatabricksWorkflowParser: malformed YAML — %s", exc
        )
        return None

    if not isinstance(data, dict):
        logger.warning(
            "DatabricksWorkflowParser: YAML root is not a mapping — got %r",
            type(data).__name__,
        )
        return None

    return data


# ---------------------------------------------------------------------------
# DatabricksWorkflowParser
# ---------------------------------------------------------------------------


class DatabricksWorkflowParser(BaseMetadataParser):
    """Parse a Databricks Workflow ``pipeline.yml`` into graph assets and edges.

    Reads a single ``pipeline.yml`` file and produces:

    * One :class:`~graph.models.Asset` with
      ``asset_type=PIPELINE, system=DATABRICKS`` for the pipeline.
    * One :class:`~graph.models.Asset` with
      ``asset_type=PIPELINE_TASK, system=DATABRICKS`` for every task.
    * ``pipeline ──TRIGGERS──> task`` edges for every task.
    * ``task_A ──TRIGGERS──> task_B`` edges for every ``depends_on`` entry.
    * ``task ──CALLS──> notebook_stub`` edges for tasks that declare a notebook.

    The parser never executes, imports, or inspects notebook source code.

    Parameters:
        source: Either a :class:`pathlib.Path` pointing to ``pipeline.yml``,
                or a raw YAML string (useful for unit testing without disk I/O).
        owner: Default owner tag for all produced assets.
        default_criticality: Default :class:`~graph.models.Criticality`.
        emit_notebook_stubs: When ``True`` (default), emit a stub
                             ``DATABRICKS_NOTEBOOK`` asset for every task
                             that declares a notebook path.  Set ``False``
                             if callers manage notebook assets separately.
    """

    def __init__(
        self,
        source: Union[str, Path],
        owner: Optional[str] = None,
        default_criticality: Criticality = Criticality.MEDIUM,
        emit_notebook_stubs: bool = True,
    ) -> None:
        """Initialise the parser.

        Args:
            source: Path to ``pipeline.yml`` or raw YAML string.
            owner: Default owner for produced assets.
            default_criticality: Default criticality.
            emit_notebook_stubs: Emit stub DATABRICKS_NOTEBOOK assets.
        """
        super().__init__(
            source_name="databricks_workflow_parser",
            owner=owner,
            default_criticality=default_criticality,
        )
        self._source = source
        self._emit_notebook_stubs = emit_notebook_stubs

    # ------------------------------------------------------------------
    # Alternative constructor
    # ------------------------------------------------------------------

    @classmethod
    def from_default_path(
        cls,
        base_dir: Union[str, Path, None] = None,
        owner: Optional[str] = None,
        default_criticality: Criticality = Criticality.MEDIUM,
    ) -> "DatabricksWorkflowParser":
        """Construct a parser pointing at the canonical pipeline.yml location.

        The default path is ``metadata/databricks/pipeline.yml`` relative to
        *base_dir* (or the current working directory when *base_dir* is
        ``None``).

        Args:
            base_dir: Optional base directory.  Defaults to ``Path.cwd()``.
            owner: Default owner for produced assets.
            default_criticality: Default criticality.

        Returns:
            A new :class:`DatabricksWorkflowParser` instance.
        """
        base = Path(base_dir) if base_dir else Path.cwd()
        path = base / "metadata" / "databricks" / "pipeline.yml"
        return cls(source=path, owner=owner, default_criticality=default_criticality)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def parse(self) -> ParseResult:
        """Parse the workflow YAML and return assets and relationships.

        Returns:
            Tuple ``(assets, relationships)``.  Both lists are empty when the
            source cannot be read or parsed.  Never raises.
        """
        data = _load_yaml(self._source)
        if data is None:
            return [], []

        pipeline_block = data.get("pipeline")
        if not isinstance(pipeline_block, dict):
            logger.warning(
                "DatabricksWorkflowParser: no 'pipeline' mapping found in YAML"
            )
            return [], []

        try:
            return self._build_graph(pipeline_block)
        except Exception as exc:  # pylint: disable=broad-except
            logger.warning(
                "DatabricksWorkflowParser: unexpected error building graph — %s",
                exc,
                exc_info=False,
            )
            return [], []

    # ------------------------------------------------------------------
    # Internal: graph builder
    # ------------------------------------------------------------------

    def _build_graph(
        self, pipeline_block: Dict[str, Any]
    ) -> Tuple[List[Asset], List[Relationship]]:
        """Build assets and relationships from a parsed pipeline block.

        Args:
            pipeline_block: The ``pipeline:`` mapping from the YAML.

        Returns:
            ``(assets, relationships)`` tuple.
        """
        assets: List[Asset] = []
        relationships: List[Relationship] = []
        emitted_notebook_ids: set[str] = set()

        # ── Pipeline asset ────────────────────────────────────────────────
        pipeline_name = str(pipeline_block.get("name") or "unknown_pipeline").strip()
        platform     = str(pipeline_block.get("platform") or "databricks").strip()
        description  = pipeline_block.get("description")

        pip_id = _pipeline_id(pipeline_name)
        pipeline_asset = self._make_asset(
            id=pip_id,
            name=pipeline_name,
            asset_type=AssetType.PIPELINE,
            system=SystemType.DATABRICKS,
            metadata={
                "platform":    platform,
                "description": str(description).strip() if description else None,
                "task_count":  len(pipeline_block.get("tasks") or []),
            },
        )
        assets.append(pipeline_asset)

        # ── Task loop ─────────────────────────────────────────────────────
        raw_tasks = pipeline_block.get("tasks") or []
        if not isinstance(raw_tasks, list):
            logger.warning(
                "DatabricksWorkflowParser: 'tasks' is not a list in pipeline %r",
                pipeline_name,
            )
            raw_tasks = []

        for idx, task in enumerate(raw_tasks):
            if not isinstance(task, dict):
                logger.warning(
                    "DatabricksWorkflowParser: task at index %d is not a mapping — skipped",
                    idx,
                )
                continue

            task_name = str(task.get("name") or "").strip()
            if not task_name:
                logger.warning(
                    "DatabricksWorkflowParser: task at index %d has no name — skipped",
                    idx,
                )
                continue

            task_id = _task_id(pipeline_name, task_name)

            # Execution order — use declared value if present, else position+1
            raw_order = task.get("execution_order")
            try:
                execution_order: Optional[int] = int(raw_order) if raw_order is not None else idx + 1
            except (TypeError, ValueError):
                execution_order = idx + 1

            task_asset = self._make_asset(
                id=task_id,
                name=task_name,
                asset_type=AssetType.PIPELINE_TASK,
                system=SystemType.DATABRICKS,
                catalog=str(task.get("catalog") or "").strip() or None,
                schema=str(task.get("schema") or "").strip() or None,
                metadata={
                    "layer":           str(task.get("layer") or "").strip() or None,
                    "execution_order": execution_order,
                    "notebook":        str(task.get("notebook") or "").strip() or None,
                    "pipeline_name":   pipeline_name,
                },
            )
            assets.append(task_asset)

            # PIPELINE ──TRIGGERS──> TASK
            relationships.append(Relationship(
                source=pip_id,
                target=task_id,
                relationship=RelationshipType.TRIGGERS,
            ))

            # TASK ──CALLS──> NOTEBOOK (stub)
            notebook_path = str(task.get("notebook") or "").strip()
            if notebook_path:
                nb_id = _notebook_stub_id(notebook_path)
                if self._emit_notebook_stubs and nb_id not in emitted_notebook_ids:
                    nb_stub = self._make_asset(
                        id=nb_id,
                        name=notebook_path.rstrip("/").rsplit("/", 1)[-1],
                        asset_type=AssetType.DATABRICKS_NOTEBOOK,
                        system=SystemType.DATABRICKS,
                        metadata={"path": notebook_path, "stub": True},
                    )
                    assets.append(nb_stub)
                    emitted_notebook_ids.add(nb_id)
                relationships.append(Relationship(
                    source=task_id,
                    target=nb_id,
                    relationship=RelationshipType.CALLS,
                    properties={"unresolved": True},
                ))

            # TASK ──TRIGGERS──> depends_on tasks
            raw_deps = task.get("depends_on")
            if raw_deps is None:
                raw_deps = []
            if not isinstance(raw_deps, list):
                logger.warning(
                    "DatabricksWorkflowParser: depends_on for task %r is not a list — ignored",
                    task_name,
                )
                raw_deps = []

            for dep_name in raw_deps:
                dep_name = str(dep_name).strip()
                if not dep_name:
                    continue
                dep_id = _task_id(pipeline_name, dep_name)
                relationships.append(Relationship(
                    source=dep_id,
                    target=task_id,
                    relationship=RelationshipType.TRIGGERS,
                    properties={"unresolved": True},
                ))

        logger.info(
            "DatabricksWorkflowParser: pipeline %r → %d assets, %d relationships",
            pipeline_name, len(assets), len(relationships),
        )
        return assets, relationships
