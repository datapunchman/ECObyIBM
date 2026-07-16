"""
change.models
=============
Data models for the Enterprise Change Analyzer layer.

These are pure data containers — no business logic lives here.
Serialisation, graph traversal, and AI reasoning are handled by
their respective layers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional

from graph.models import Asset, SystemType


# ---------------------------------------------------------------------------
# ChangeType
# ---------------------------------------------------------------------------


class ChangeType(str, Enum):
    """Broad semantic category of a proposed data-platform change.

    Used by the rule-based parser in :class:`~change.analyzer.EnterpriseChangeAnalyzer`
    to classify a free-text change request before graph traversal.
    """

    COLUMN_RENAME = "column_rename"
    COLUMN_DELETE = "column_delete"
    COLUMN_ADD = "column_add"

    TABLE_RENAME = "table_rename"
    TABLE_DELETE = "table_delete"
    TABLE_ADD = "table_add"

    VIEW_CHANGE = "view_change"
    STORED_PROCEDURE_CHANGE = "stored_procedure_change"
    NOTEBOOK_CHANGE = "notebook_change"
    PIPELINE_CHANGE = "pipeline_change"

    UNKNOWN = "unknown"


# ---------------------------------------------------------------------------
# ChangeRequest
# ---------------------------------------------------------------------------


@dataclass
class ChangeRequest:
    """A parsed representation of a free-text change request.

    Attributes
    ----------
    original_request:
        The verbatim input string supplied by the user.
    change_type:
        Semantic category inferred by the rule-based parser.
    target_name:
        The name of the artifact being changed (column, table, etc.).
        ``None`` when it could not be extracted.
    new_name:
        The replacement name for rename operations.  ``None`` for
        delete/add/change operations.
    table_name:
        Parent table name when the target is a column.  ``None`` when
        it could not be inferred from the request.
    system:
        Explicit system hint supplied in the request (e.g. "databricks").
        ``None`` when none was mentioned.
    """

    original_request: str
    change_type: ChangeType = ChangeType.UNKNOWN
    target_name: Optional[str] = None
    new_name: Optional[str] = None
    table_name: Optional[str] = None
    system: Optional[str] = None


# ---------------------------------------------------------------------------
# EnterpriseChangeAnalysis
# ---------------------------------------------------------------------------


@dataclass
class EnterpriseChangeAnalysis:
    """The complete impact analysis for a single change request.

    Produced by :meth:`~change.analyzer.EnterpriseChangeAnalyzer.analyze`.

    Attributes
    ----------
    change_request:
        The parsed change request that triggered this analysis.
    source_asset:
        The :class:`~graph.models.Asset` identified as the direct target of
        the change.  ``None`` when no matching asset was found in the graph.
    impact_count:
        Total number of downstream assets affected.
    impacted_assets:
        Ordered list of downstream :class:`~graph.models.Asset` objects
        (BFS order, source excluded).
    system_breakdown:
        Downstream assets grouped by :class:`~graph.models.SystemType` value,
        e.g. ``{"powerbi": [...], "databricks": [...]}``.  All system keys
        are always present even when the list is empty.
    dependency_paths:
        All root-to-leaf dependency paths from the source asset.
        Each path is a list of asset ID strings.
    summary:
        Human-readable impact summary listing per-system counts, suitable
        for display or as context for a downstream AI reasoning step.

    Typed impact buckets (Phase 6 — Enterprise Graph Traversal)
    -----------------------------------------------------------
    Each bucket contains the subset of *impacted_assets* matching that type.
    All buckets are always present, even when empty.

    systems_impacted:
        Sorted list of distinct :class:`~graph.models.SystemType` value
        strings found in the impact set.
    databricks_notebooks:
        Assets of type ``DATABRICKS_NOTEBOOK`` or ``NOTEBOOK``.
    databricks_pipelines:
        Assets of type ``PIPELINE``, ``ADF_PIPELINE``, or ``FABRIC_PIPELINE``.
    workflow_tasks:
        Assets of type ``PIPELINE_TASK``.
    sql_views:
        Assets of type ``SQL_VIEW`` or ``VIEW``.
    sql_procedures:
        Assets of type ``STORED_PROCEDURE``.
    sql_functions:
        Assets of type ``SQL_FUNCTION`` or ``FUNCTION``.
    adls_files:
        Assets of type ``ADLS_FILE``.
    powerbi_reports:
        Assets of type ``REPORT``, ``POWERBI_REPORT``, ``VISUAL``, or
        ``POWERBI_VISUAL``.
    semantic_models:
        Assets of type ``SEMANTIC_MODEL``, ``POWERBI_DATASET``,
        ``MEASURE``, or ``POWERBI_MEASURE``.
    executive_summary:
        Short prose summary of the change and its overall impact,
        suitable for a non-technical stakeholder.
    deployment_plan:
        Ordered list of deployment step strings.
    validation_checklist:
        List of post-deployment validation items.
    rollback_plan:
        Step-by-step instructions to revert the change.
    """

    change_request: ChangeRequest
    source_asset: Optional[Asset]
    impact_count: int
    impacted_assets: List[Asset]
    system_breakdown: Dict[str, List[Asset]]
    dependency_paths: List[List[str]]
    summary: str

    # ── Phase 6: typed impact buckets ────────────────────────────────────
    systems_impacted:      List[str]        = field(default_factory=list)
    databricks_notebooks:  List[Asset]      = field(default_factory=list)
    databricks_pipelines:  List[Asset]      = field(default_factory=list)
    workflow_tasks:        List[Asset]      = field(default_factory=list)
    sql_views:             List[Asset]      = field(default_factory=list)
    sql_procedures:        List[Asset]      = field(default_factory=list)
    sql_functions:         List[Asset]      = field(default_factory=list)
    adls_files:            List[Asset]      = field(default_factory=list)
    powerbi_reports:       List[Asset]      = field(default_factory=list)
    semantic_models:       List[Asset]      = field(default_factory=list)

    # ── Phase 6: prose / plan fields ─────────────────────────────────────
    executive_summary:     str              = field(default="")
    deployment_plan:       List[str]        = field(default_factory=list)
    validation_checklist:  List[str]        = field(default_factory=list)
    rollback_plan:         List[str]        = field(default_factory=list)
