"""
change.analyzer
===============
Enterprise Change Analyzer — rule-based impact discovery layer.

This module sits between the graph traversal engine and the AI reasoning
layer.  It does NOT call Granite, generate executive summaries, or produce
deployment plans.  Its only responsibilities are:

1. Parse a free-text change request into a typed :class:`~change.models.ChangeRequest`
   using lightweight regex rules (no NLP libraries).
2. Resolve the target artifact to a graph :class:`~graph.models.Asset`.
3. Drive :class:`~graph.query_engine.EnterpriseQueryEngine` to discover the
   downstream impact.
4. Return a fully populated :class:`~change.models.EnterpriseChangeAnalysis`.

The Granite AI layer may consume ``EnterpriseChangeAnalysis`` as structured
context for reasoning — that wiring is handled elsewhere.

Rule-based parser coverage
--------------------------
Pattern                             → ChangeType             target / new_name
----------------------------------  ----------------------  ------------------
rename <X> to <Y>                   COLUMN_RENAME /         X / Y
                                    TABLE_RENAME
drop/remove/delete <X> column       COLUMN_DELETE           X / –
add/create/introduce <X> column     COLUMN_ADD              X / –
drop/remove/delete <X> table        TABLE_DELETE            X / –
add/create/introduce <X> table      TABLE_ADD               X / –
change/update/modify … view         VIEW_CHANGE             – / –
change/update/modify … procedure    STORED_PROCEDURE_CHANGE – / –
change/update/modify … notebook     NOTEBOOK_CHANGE         – / –
change/update/modify … pipeline     PIPELINE_CHANGE         – / –
(nothing matches)                   UNKNOWN                 – / –
"""

from __future__ import annotations

import dataclasses
import logging
import re
from difflib import get_close_matches
from typing import Dict, List, Optional, Set, Tuple

from change.models import ChangeRequest, ChangeType, EnterpriseChangeAnalysis
from graph.enterprise_graph import EnterpriseGraph
from graph.models import Asset, AssetType, SystemType
from graph.query_engine import EnterpriseQueryEngine

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Compiled regex patterns (order matters — more specific first)
# ---------------------------------------------------------------------------

# "rename <X> [column|table|…] to <Y>"  or  "rename <X> to <Y>"
#
# ROOT CAUSE FIX (Bug 1):
# The previous pattern used `\w[\w\s]*?` which greedily accumulated
# everything up to the keyword "to", producing targets like
# "the Revenue column in sales_dashboard" instead of just "Revenue".
#
# Fix: split the rename pattern into two forms:
#   Form A — "rename <TABLE>[./<sep>]<COL> to <NEW>"  (qualified name)
#   Form B — "rename <SINGLE_WORD_OR_UNDERSCORED_NAME> to <NEW>"
#
# The table qualifier ("in <table>") is now captured separately as an
# optional trailing hint, not folded into the target name.

# Form A: rename [the] <word> [column|table] [in|from|within] <table> to <new…>
#
# FIX (Bug 4 — multi-word new_name):
# new_name now captures ALL remaining words after "to" ([\w\s]+?) trimmed at
# end-of-string, so "rename X to dragon table" yields new_name="dragon table".
# The trailing strip() in _parse() removes any final whitespace.
_RE_RENAME_QUALIFIED = re.compile(
    r"\brename\s+(?:the\s+)?(?P<target>\w+)\s+"
    r"(?:column\s+|table\s+)?(?:in|from|within|of)\s+(?P<table>\w+)\s+"
    r"(?:column\s+|table\s+)?to\s+(?P<new_name>[\w][\w\s]*?)(?:\s+(?:in|from|within|of)\s+\w+)?(?:\s*$)",
    re.IGNORECASE,
)

# Form B: rename <word> [column|table] to <new…>  (no table qualifier)
# The new_name stops before an optional trailing "in|from|within TABLE" hint.
_RE_RENAME_SIMPLE = re.compile(
    r"\brename\s+(?:the\s+)?(?P<target>\w+)\s+(?:column\s+|table\s+)?to\s+(?P<new_name>[\w][\w\s]*?)(?:\s+(?:in|from|within|of)\s+\w+)?(?:\s*$)",
    re.IGNORECASE,
)

# Form C: rename <target> to <new…>  (bare — no "column"/"table" keyword)
# The new_name stops before an optional trailing "in|from|within TABLE" hint.
_RE_RENAME_BARE = re.compile(
    r"\brename\s+(?P<target>\w+)\s+to\s+(?P<new_name>[\w][\w\s]*?)(?:\s+(?:in|from|within|of)\s+\w+)?(?:\s*$)",
    re.IGNORECASE,
)

# "drop|remove|delete [the] <X> column"
_RE_COL_DELETE = re.compile(
    r"\b(?:drop|remove|delete)\s+(?:the\s+)?(?P<target>\w+)\s+column\b",
    re.IGNORECASE,
)

# "add|create|introduce [a|an|the] <X> column"
_RE_COL_ADD = re.compile(
    r"\b(?:add|create|introduce)\s+(?:a\s+|an\s+|the\s+)?(?:new\s+)?(?P<target>\w+)\s+column\b",
    re.IGNORECASE,
)

# "drop|remove|delete [the] <X> table"  or  "drop|remove|delete table <X>"
# target allows dots/hyphens/slashes to match filenames like "f.pq"
_RE_TABLE_DELETE = re.compile(
    r"\b(?:drop|remove|delete)\s+(?:the\s+)?(?:table\s+)?(?P<target>[\w.\-/]+)\s*(?:table\b|$)",
    re.IGNORECASE,
)

# "add|create [the|a] <X> table"  or  "add|create table <X>"
_RE_TABLE_ADD = re.compile(
    r"\b(?:add|create)\s+(?:the\s+|a\s+)?(?:table\s+)?(?P<target>[\w.\-/]+)\s*(?:table\b|$)",
    re.IGNORECASE,
)

# Keyword-only patterns for artefact-class changes (no name extraction needed)
_RE_VIEW = re.compile(r"\b(?:change|update|modify|alter)\b.+\bview\b", re.IGNORECASE)
_RE_PROC = re.compile(
    r"\b(?:change|update|modify|alter)\b.+\b(?:procedure|proc|stored.procedure)\b",
    re.IGNORECASE,
)
_RE_NOTEBOOK = re.compile(
    r"\b(?:change|update|modify|alter)\b.+\bnotebook\b", re.IGNORECASE
)
_RE_PIPELINE = re.compile(
    r"\b(?:change|update|modify|alter)\b.+\bpipeline\b", re.IGNORECASE
)

# Hint words that appear in requests and map to a system context
_SYSTEM_HINTS: List[tuple[str, str]] = [
    (r"\bdatabricks\b", SystemType.DATABRICKS.value),
    (r"\bsql\s+server\b|\bsql\b",  SystemType.SQL.value),
    (r"\bpower\s*bi\b|\bpbi\b",    SystemType.POWERBI.value),
    (r"\bpipeline\b",              SystemType.PIPELINE.value),
    (r"\bdatabase\b|\bdb\b",       SystemType.DATABASE.value),
    (r"\bapi\b",                   SystemType.API.value),
]

# Stopwords stripped when the regex still captures determiner phrases
_STOPWORDS = frozenset({
    "the", "a", "an", "column", "table", "field", "attribute",
    "in", "from", "within", "of",
})


# ---------------------------------------------------------------------------
# Phase 6: Asset-type → typed impact bucket mapping
# ---------------------------------------------------------------------------

# Maps AssetType value strings to the bucket field name on EnterpriseChangeAnalysis.
_ASSET_TYPE_TO_PHASE6_BUCKET: Dict[str, str] = {
    # Databricks / notebooks
    AssetType.DATABRICKS_NOTEBOOK.value: "databricks_notebooks",
    AssetType.NOTEBOOK.value:            "databricks_notebooks",
    AssetType.SPARK_JOB.value:           "databricks_notebooks",
    AssetType.JOB.value:                 "databricks_notebooks",

    # Databricks / pipelines
    AssetType.PIPELINE.value:            "databricks_pipelines",
    AssetType.ADF_PIPELINE.value:        "databricks_pipelines",
    AssetType.FABRIC_PIPELINE.value:     "databricks_pipelines",
    AssetType.AIRFLOW_DAG.value:         "databricks_pipelines",
    AssetType.DATAFLOW.value:            "databricks_pipelines",

    # Workflow tasks
    AssetType.PIPELINE_TASK.value:       "workflow_tasks",

    # SQL views
    AssetType.SQL_VIEW.value:            "sql_views",
    AssetType.VIEW.value:                "sql_views",
    AssetType.MATERIALIZED_VIEW.value:   "sql_views",

    # SQL procedures
    AssetType.STORED_PROCEDURE.value:    "sql_procedures",

    # SQL functions
    AssetType.SQL_FUNCTION.value:        "sql_functions",
    AssetType.FUNCTION.value:            "sql_functions",

    # ADLS files
    AssetType.ADLS_FILE.value:           "adls_files",

    # Power BI reports / visuals
    AssetType.REPORT.value:              "powerbi_reports",
    AssetType.POWERBI_REPORT.value:      "powerbi_reports",
    AssetType.VISUAL.value:              "powerbi_reports",
    AssetType.POWERBI_VISUAL.value:      "powerbi_reports",
    AssetType.DASHBOARD.value:           "powerbi_reports",
    AssetType.POWERBI_DASHBOARD.value:   "powerbi_reports",

    # Semantic models / measures
    AssetType.SEMANTIC_MODEL.value:      "semantic_models",
    AssetType.POWERBI_DATASET.value:     "semantic_models",
    AssetType.MEASURE.value:             "semantic_models",
    AssetType.POWERBI_MEASURE.value:     "semantic_models",
}

# Empty bucket template — always returned so callers can iterate unconditionally.
_EMPTY_BUCKETS: Dict[str, List[Asset]] = {
    "databricks_notebooks": [],
    "databricks_pipelines": [],
    "workflow_tasks":       [],
    "sql_views":            [],
    "sql_procedures":       [],
    "sql_functions":        [],
    "adls_files":           [],
    "powerbi_reports":      [],
    "semantic_models":      [],
}


def _classify_into_buckets(assets: List[Asset]) -> Dict[str, List[Asset]]:
    """Distribute *assets* into Phase-6 typed impact buckets.

    Every bucket key is always present in the returned dict (empty list
    when no asset of that type was found).

    Args:
        assets: Downstream assets from the graph traversal.

    Returns:
        Dict mapping bucket name → list of matching assets.
    """
    buckets: Dict[str, List[Asset]] = {k: [] for k in _EMPTY_BUCKETS}
    for asset in assets:
        bucket = _ASSET_TYPE_TO_PHASE6_BUCKET.get(asset.asset_type.value)
        if bucket:
            buckets[bucket].append(asset)
    return buckets


def _build_executive_summary(
    parsed: ChangeRequest,
    source_asset: Optional[Asset],
    buckets: Dict[str, List[Asset]],
) -> str:
    """Build a short executive summary string for a non-technical audience.

    Args:
        parsed: The parsed change request.
        source_asset: Resolved source asset, or ``None``.
        buckets: Phase-6 typed impact buckets.

    Returns:
        A concise plain-English summary paragraph.
    """
    if source_asset is None:
        searched = parsed.target_name or "(unspecified)"
        return (
            f"No enterprise asset named {searched!r} was found in the metadata "
            f"graph. No impact analysis could be performed."
        )

    change_desc = parsed.change_type.value.replace("_", " ").title()
    if parsed.new_name:
        change_desc += f" ({parsed.target_name} \u2192 {parsed.new_name})"

    non_empty = {k: v for k, v in buckets.items() if v}
    if not non_empty:
        return (
            f"{change_desc} on '{source_asset.name}' "
            f"({source_asset.asset_type.value}, {source_asset.system.value}). "
            f"No downstream assets are impacted by this change."
        )

    parts = [f"{len(v)} {k.replace('_', ' ')}" for k, v in non_empty.items()]
    return (
        f"{change_desc} on '{source_asset.name}' "
        f"({source_asset.asset_type.value}, {source_asset.system.value}). "
        f"Downstream impact: {', '.join(parts)}."
    )


def _build_deployment_plan(
    parsed: ChangeRequest,
    source_asset: Optional[Asset],
    buckets: Dict[str, List[Asset]],
) -> List[str]:
    """Produce a minimal deterministic deployment plan from graph facts.

    Args:
        parsed: The parsed change request.
        source_asset: Resolved source asset, or ``None``.
        buckets: Phase-6 typed impact buckets.

    Returns:
        Ordered list of deployment step strings.
    """
    if source_asset is None:
        return ["Resolve the source asset before planning deployment."]

    steps: List[str] = []
    order = 1

    steps.append(
        f"{order}. Apply {parsed.change_type.value} to "
        f"'{source_asset.name}' ({source_asset.system.value})."
    )
    order += 1

    if buckets.get("sql_views") or buckets.get("sql_procedures") or buckets.get("sql_functions"):
        steps.append(f"{order}. Update all affected SQL views, procedures, and functions.")
        order += 1

    if buckets.get("databricks_notebooks"):
        nb_names = ", ".join(a.name for a in buckets["databricks_notebooks"][:3])
        steps.append(f"{order}. Update Databricks notebooks: {nb_names}.")
        order += 1

    if buckets.get("databricks_pipelines"):
        steps.append(f"{order}. Re-run or reconfigure affected Databricks pipelines.")
        order += 1

    if buckets.get("workflow_tasks"):
        steps.append(f"{order}. Review and update workflow task configurations.")
        order += 1

    if buckets.get("adls_files"):
        steps.append(f"{order}. Refresh affected ADLS file ingestion mappings.")
        order += 1

    if buckets.get("semantic_models"):
        steps.append(f"{order}. Refresh Power BI semantic models and measures.")
        order += 1

    if buckets.get("powerbi_reports"):
        steps.append(f"{order}. Republish and validate Power BI reports.")
        order += 1

    steps.append(f"{order}. Run end-to-end regression tests.")
    return steps


def _build_validation_checklist(buckets: Dict[str, List[Asset]]) -> List[str]:
    """Build a post-deployment validation checklist from impacted buckets.

    Args:
        buckets: Phase-6 typed impact buckets.

    Returns:
        List of validation check strings.
    """
    checks: List[str] = ["Verify source asset change applied successfully."]
    if buckets.get("sql_views"):
        checks.append("Confirm all SQL views return correct results.")
    if buckets.get("sql_procedures"):
        checks.append("Execute and validate all affected stored procedures.")
    if buckets.get("sql_functions"):
        checks.append("Test all affected SQL functions with representative inputs.")
    if buckets.get("databricks_notebooks"):
        checks.append("Re-run Databricks notebooks and check output tables.")
    if buckets.get("databricks_pipelines"):
        checks.append("Trigger a full pipeline run and verify data quality.")
    if buckets.get("workflow_tasks"):
        checks.append("Confirm all workflow tasks complete without errors.")
    if buckets.get("adls_files"):
        checks.append("Validate ADLS file ingestion completes successfully.")
    if buckets.get("semantic_models"):
        checks.append("Refresh Power BI semantic models and verify measure values.")
    if buckets.get("powerbi_reports"):
        checks.append("Open Power BI reports and confirm visuals render correctly.")
    checks.append("Run automated regression test suite.")
    return checks


def _build_rollback_plan(
    parsed: ChangeRequest,
    source_asset: Optional[Asset],
    buckets: Dict[str, List[Asset]],
) -> List[str]:
    """Build a rollback plan in reverse deployment order.

    Args:
        parsed: The parsed change request.
        source_asset: Resolved source asset, or ``None``.
        buckets: Phase-6 typed impact buckets.

    Returns:
        Ordered list of rollback step strings.
    """
    if source_asset is None:
        return ["No rollback required (source asset was not resolved)."]

    steps: List[str] = []
    order = 1

    if buckets.get("powerbi_reports"):
        steps.append(f"{order}. Revert Power BI reports to last published version.")
        order += 1
    if buckets.get("semantic_models"):
        steps.append(f"{order}. Revert Power BI semantic model changes.")
        order += 1
    if buckets.get("adls_files"):
        steps.append(f"{order}. Restore previous ADLS ingestion mappings.")
        order += 1
    if buckets.get("workflow_tasks"):
        steps.append(f"{order}. Restore previous workflow task configurations.")
        order += 1
    if buckets.get("databricks_pipelines"):
        steps.append(f"{order}. Revert Databricks pipeline configurations.")
        order += 1
    if buckets.get("databricks_notebooks"):
        steps.append(f"{order}. Revert Databricks notebooks to previous version.")
        order += 1
    if buckets.get("sql_views") or buckets.get("sql_procedures") or buckets.get("sql_functions"):
        steps.append(f"{order}. Restore previous SQL view/procedure/function definitions.")
        order += 1

    steps.append(
        f"{order}. Revert {parsed.change_type.value} on "
        f"'{source_asset.name if source_asset else 'source'}'."
    )
    steps.append(f"{order + 1}. Validate rollback by running regression tests.")
    return steps


# ---------------------------------------------------------------------------
# Normalisation helpers
# ---------------------------------------------------------------------------


def _norm(s: str) -> str:
    """Return a case-insensitive, separator-insensitive token.

    Strips spaces, underscores, and hyphens so that:
        ``Customer_ID``  → ``customerid``
        ``CustomerID``   → ``customerid``
        ``customer id``  → ``customerid``
    """
    return re.sub(r"[\s_\-]+", "", s).lower()


def _clean_target(raw: str) -> str:
    """Strip stopwords and extra whitespace from a regex-captured target.

    Converts ``"the Revenue column in sales_dashboard"``
    → ``"Revenue"``.
    """
    tokens = re.split(r"\s+", raw.strip())
    kept = [t for t in tokens if t.lower() not in _STOPWORDS]
    # If all tokens were stopwords, fall back to the full raw string
    return " ".join(kept) if kept else raw.strip()


def _correct_change_type(
    parsed: ChangeRequest,
    source_asset: "Asset",
) -> ChangeRequest:
    """Correct misclassified change types after asset resolution.

    The regex parser classifies ``"Delete CustomerID"`` as
    :attr:`~change.models.ChangeType.TABLE_DELETE` because the request
    contains no explicit ``column`` keyword — the ``$`` anchor in
    ``_RE_TABLE_DELETE`` matches bare ``delete X``.  Once the asset is
    resolved we know its actual type, so we correct the
    :attr:`~change.models.ChangeRequest.change_type` here.

    Corrections applied
    -------------------
    TABLE_DELETE  + resolved column  → COLUMN_DELETE
    TABLE_RENAME  + resolved column  → COLUMN_RENAME
    COLUMN_DELETE + resolved table   → TABLE_DELETE
    COLUMN_RENAME + resolved table   → TABLE_RENAME

    Parameters
    ----------
    parsed:
        The :class:`~change.models.ChangeRequest` returned by ``_parse()``.
    source_asset:
        The resolved :class:`~graph.models.Asset`.  Must not be ``None``.

    Returns
    -------
    ChangeRequest
        A new :class:`~change.models.ChangeRequest` with the corrected
        :attr:`change_type`; all other fields are preserved.  Returns
        *parsed* unchanged when no correction is needed.
    """
    from graph.models import AssetType  # local import — avoids circular import at module level

    asset_type = source_asset.asset_type
    ct = parsed.change_type

    correction: Optional[ChangeType] = None

    if ct == ChangeType.TABLE_DELETE and asset_type == AssetType.COLUMN:
        correction = ChangeType.COLUMN_DELETE
    elif ct == ChangeType.TABLE_RENAME and asset_type == AssetType.COLUMN:
        correction = ChangeType.COLUMN_RENAME
    elif ct == ChangeType.COLUMN_DELETE and asset_type == AssetType.TABLE:
        correction = ChangeType.TABLE_DELETE
    elif ct == ChangeType.COLUMN_RENAME and asset_type == AssetType.TABLE:
        correction = ChangeType.TABLE_RENAME

    if correction is not None:
        logger.info(
            "_correct_change_type: corrected %s → %s for asset %r (type=%s)",
            ct.value, correction.value, source_asset.id, asset_type.value,
        )
        return dataclasses.replace(parsed, change_type=correction)

    return parsed


# ---------------------------------------------------------------------------
# EnterpriseChangeAnalyzer
# ---------------------------------------------------------------------------


class EnterpriseChangeAnalyzer:
    """Rule-based impact analyzer over an :class:`~graph.enterprise_graph.EnterpriseGraph`.

    Parameters
    ----------
    graph:
        A fully populated :class:`~graph.enterprise_graph.EnterpriseGraph`
        (e.g. as returned by ``MetadataEngine.get_enterprise_graph()``).
    """

    def __init__(self, graph: EnterpriseGraph) -> None:
        self._graph = graph
        self._query = EnterpriseQueryEngine(graph)

        # Pre-build normalisation index for O(1) lookup
        # Maps norm(asset.name) → [asset, ...] (multiple assets can share a normalised name)
        self._norm_index: Dict[str, List[Asset]] = {}
        for asset in graph.assets.values():
            key = _norm(asset.name)
            self._norm_index.setdefault(key, []).append(asset)

        logger.debug(
            "EnterpriseChangeAnalyzer ready — %d assets, %d relationships, "
            "%d normalised name keys",
            len(graph.assets),
            len(graph.relationships),
            len(self._norm_index),
        )

        # --- diagnostic log (requirement 1) ---
        all_ids = sorted(graph.assets.keys())
        col_ids = [aid for aid in all_ids if aid.startswith("column::")]
        tbl_ids = [aid for aid in all_ids if aid.startswith("table::")]
        logger.info(
            "Graph loaded: total=%d  columns=%d  tables=%d",
            len(all_ids), len(col_ids), len(tbl_ids),
        )
        logger.debug("First 50 asset IDs   : %s", all_ids[:50])
        logger.debug("First 50 column IDs  : %s", col_ids[:50])
        logger.debug("First 50 table IDs   : %s", tbl_ids[:50])

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def analyze(self, change_request: str) -> EnterpriseChangeAnalysis:
        """Parse *change_request* and return a full impact analysis.

        Parameters
        ----------
        change_request:
            Free-text description of the proposed change.
            Examples::

                "Rename Customer_ID to Client_ID"
                "Remove DOB column"
                "Drop Customers table"

        Returns
        -------
        EnterpriseChangeAnalysis
            Fully populated analysis including the parsed request, the
            identified source asset (or ``None``), the complete downstream
            impact list, dependency paths, and a human-readable summary.
        """
        # Step 1 — parse the request
        parsed = self._parse(change_request)
        logger.info(
            "analyze: '%s…' → type=%s target=%r",
            change_request[:80],
            parsed.change_type.value,
            parsed.target_name,
        )

        # Step 2 — resolve the source asset
        source_asset = self._resolve_asset(parsed)

        # FIX 1 — change_type correction after asset resolution.
        # The parser classifies "Delete CustomerID" as TABLE_DELETE because there
        # is no explicit "column" keyword in the request.  Once the asset is
        # resolved we know its type, so we correct the change_type here.
        if source_asset is not None:
            parsed = _correct_change_type(parsed, source_asset)

        if source_asset is None:
            logger.info(
                "No matching asset found for target=%r — returning empty analysis",
                parsed.target_name,
            )

        # Step 3 — graph traversal (skipped when no source asset was found)
        if source_asset is not None:
            downstream   = self._query.find_downstream(source_asset.id)
            paths        = self._query.find_dependency_paths(source_asset.id)
            full_impact  = self._query.find_full_impact(source_asset.id)
            # Remove the bookkeeping "source" key; keep only system buckets
            system_breakdown = {
                k: v for k, v in full_impact.items() if k != "source"
            }
        else:
            downstream       = []
            paths            = []
            system_breakdown = {s.value: [] for s in SystemType}

        # Step 4 — Phase-6 typed bucket classification
        buckets = _classify_into_buckets(downstream)

        # Step 5 — systems_impacted: distinct system values in impact set
        systems_impacted: List[str] = sorted(
            {a.system.value for a in downstream}
        )

        # Step 6 — build legacy summary
        # FIX 3 — unknown-asset: _build_summary now returns a meaningful message
        # when source_asset is None, rather than a bare "Source asset: not found".
        summary = self._build_summary(parsed, source_asset, system_breakdown)

        # Step 7 — build Phase-6 prose / plan fields
        executive_summary    = _build_executive_summary(parsed, source_asset, buckets)
        deployment_plan      = _build_deployment_plan(parsed, source_asset, buckets)
        validation_checklist = _build_validation_checklist(buckets)
        rollback_plan        = _build_rollback_plan(parsed, source_asset, buckets)

        return EnterpriseChangeAnalysis(
            change_request=parsed,
            source_asset=source_asset,
            impact_count=len(downstream),
            impacted_assets=downstream,
            system_breakdown=system_breakdown,
            dependency_paths=paths,
            summary=summary,
            # Phase-6 typed buckets
            systems_impacted=systems_impacted,
            databricks_notebooks=buckets["databricks_notebooks"],
            databricks_pipelines=buckets["databricks_pipelines"],
            workflow_tasks=buckets["workflow_tasks"],
            sql_views=buckets["sql_views"],
            sql_procedures=buckets["sql_procedures"],
            sql_functions=buckets["sql_functions"],
            adls_files=buckets["adls_files"],
            powerbi_reports=buckets["powerbi_reports"],
            semantic_models=buckets["semantic_models"],
            # Phase-6 prose / plan fields
            executive_summary=executive_summary,
            deployment_plan=deployment_plan,
            validation_checklist=validation_checklist,
            rollback_plan=rollback_plan,
        )

    # ------------------------------------------------------------------
    # Internal: request parser
    # ------------------------------------------------------------------

    def _parse(self, request: str) -> ChangeRequest:
        """Classify *request* with lightweight regex rules.

        Returns
        -------
        ChangeRequest
            Fully populated where information is available; ``UNKNOWN``
            change type when no pattern matches.
        """
        text = request.strip()
        system_hint = self._extract_system_hint(text)

        # ── rename (qualified: "rename X in table_Y to Z") ──────────────────
        m = _RE_RENAME_QUALIFIED.search(text)
        if m:
            target = _clean_target(m.group("target"))
            table_hint = m.group("table").strip() if m.group("table") else None
            new_name = m.group("new_name").strip()  # strip trailing whitespace from multi-word capture
            change_type = (
                ChangeType.TABLE_RENAME
                if re.search(r"\btable\b", text, re.IGNORECASE)
                else ChangeType.COLUMN_RENAME
            )
            return ChangeRequest(
                original_request=text,
                change_type=change_type,
                target_name=target,
                new_name=new_name,
                table_name=table_hint,
                system=system_hint,
            )

        # ── rename (simple: "rename [the] X [column] to Y") ─────────────────
        m = _RE_RENAME_SIMPLE.search(text)
        if m:
            target = _clean_target(m.group("target"))
            new_name = m.group("new_name").strip()  # strip trailing whitespace
            # Extract optional "in <table>" from the full text as a table hint
            table_hint = self._extract_table_hint(text)
            change_type = (
                ChangeType.TABLE_RENAME
                if re.search(r"\btable\b", text, re.IGNORECASE)
                else ChangeType.COLUMN_RENAME
            )
            return ChangeRequest(
                original_request=text,
                change_type=change_type,
                target_name=target,
                new_name=new_name,
                table_name=table_hint,
                system=system_hint,
            )

        # ── rename (bare: "rename X to Y") ───────────────────────────────────
        m = _RE_RENAME_BARE.search(text)
        if m:
            target = _clean_target(m.group("target"))
            new_name = m.group("new_name").strip()  # strip trailing whitespace
            table_hint = self._extract_table_hint(text)
            change_type = (
                ChangeType.TABLE_RENAME
                if re.search(r"\btable\b", text, re.IGNORECASE)
                else ChangeType.COLUMN_RENAME
            )
            return ChangeRequest(
                original_request=text,
                change_type=change_type,
                target_name=target,
                new_name=new_name,
                table_name=table_hint,
                system=system_hint,
            )

        # ── column delete ────────────────────────────────────────────────────
        m = _RE_COL_DELETE.search(text)
        if m:
            return ChangeRequest(
                original_request=text,
                change_type=ChangeType.COLUMN_DELETE,
                target_name=_clean_target(m.group("target")),
                table_name=self._extract_table_hint(text),
                system=system_hint,
            )

        # ── column add ───────────────────────────────────────────────────────
        m = _RE_COL_ADD.search(text)
        if m:
            return ChangeRequest(
                original_request=text,
                change_type=ChangeType.COLUMN_ADD,
                target_name=_clean_target(m.group("target")),
                table_name=self._extract_table_hint(text),
                system=system_hint,
            )

        # ── table delete ─────────────────────────────────────────────────────
        m = _RE_TABLE_DELETE.search(text)
        if m:
            target = _clean_target(m.group("target"))
            if target and target.lower() not in _STOPWORDS:
                return ChangeRequest(
                    original_request=text,
                    change_type=ChangeType.TABLE_DELETE,
                    target_name=target,
                    system=system_hint,
                )

        # ── table add ────────────────────────────────────────────────────────
        m = _RE_TABLE_ADD.search(text)
        if m:
            target = _clean_target(m.group("target"))
            if target and target.lower() not in _STOPWORDS:
                return ChangeRequest(
                    original_request=text,
                    change_type=ChangeType.TABLE_ADD,
                    target_name=target,
                    system=system_hint,
                )

        # ── keyword-only artefact classes ────────────────────────────────────
        if _RE_VIEW.search(text):
            return ChangeRequest(
                original_request=text,
                change_type=ChangeType.VIEW_CHANGE,
                system=system_hint,
            )
        if _RE_PROC.search(text):
            return ChangeRequest(
                original_request=text,
                change_type=ChangeType.STORED_PROCEDURE_CHANGE,
                system=system_hint,
            )
        if _RE_NOTEBOOK.search(text):
            return ChangeRequest(
                original_request=text,
                change_type=ChangeType.NOTEBOOK_CHANGE,
                system=system_hint,
            )
        if _RE_PIPELINE.search(text):
            return ChangeRequest(
                original_request=text,
                change_type=ChangeType.PIPELINE_CHANGE,
                system=system_hint,
            )

        # ── nothing matched ──────────────────────────────────────────────────
        return ChangeRequest(
            original_request=text,
            change_type=ChangeType.UNKNOWN,
            system=system_hint,
        )

    # ------------------------------------------------------------------
    # Internal: asset resolution
    # ------------------------------------------------------------------

    def _resolve_asset(self, parsed: ChangeRequest) -> Optional[Asset]:
        """Find the best-matching graph asset for the parsed change request.

        Resolution is performed in ranked tiers (best match first):

        Tier 1 — exact normalised name match within the hinted table
            ``Revenue`` in ``sales_dashboard``
            → ``column::sales_dashboard::Revenue``  ✓

        Tier 2 — exact normalised name match (any table, system-preferred)
            ``CustomerID`` (normalised: ``customerid``)
            matches ``CustomerID`` in any table  ✓

        Tier 3 — normalised name is a suffix of any asset ID segment
            Handles qualified names like ``sales_dashboard[Revenue]``

        Tier 4 — fuzzy fallback via :func:`difflib.get_close_matches`
            Returns the closest name even when exact matching fails,
            instead of returning ``None``.

        Returns
        -------
        Asset | None
            Best-matching asset, or ``None`` only when the graph is empty
            or the target name is absent.
        """
        if not parsed.target_name:
            return None

        raw_target = parsed.target_name
        needle_norm = _norm(raw_target)
        needle_lower = raw_target.lower()

        # --- diagnostic log (requirement 2) ---
        logger.info(
            "Resolver: searching for %r  (normalised: %r)  table_hint=%r  system=%r",
            raw_target, needle_norm, parsed.table_name, parsed.system,
        )
        logger.debug(
            "Resolver: graph has %d assets, %d normalised keys",
            len(self._graph.assets), len(self._norm_index),
        )

        # Resolve optional table hint for narrowing
        table_hint_norm = _norm(parsed.table_name) if parsed.table_name else None

        # ── Tier 1: exact normalised + table hint ────────────────────────────
        if table_hint_norm:
            tier1: List[Asset] = []
            for asset in self._norm_index.get(needle_norm, []):
                props = asset.properties or {}
                tbl = props.get("table_name", "")
                if _norm(tbl) == table_hint_norm or _norm(asset.id.split("::")[-2] if asset.id.count("::") >= 2 else "") == table_hint_norm:
                    tier1.append(asset)
            if tier1:
                logger.info(
                    "Resolver: Tier-1 match (norm+table) → %d candidates: %s",
                    len(tier1), [a.id for a in tier1],
                )
                return self._prefer_system(tier1, parsed.system, parsed.change_type)

        # ── Tier 2: exact normalised match (any table) ───────────────────────
        tier2 = self._norm_index.get(needle_norm, [])
        if tier2:
            logger.info(
                "Resolver: Tier-2 match (norm only) → %d candidates: %s",
                len(tier2), [a.id for a in tier2],
            )
            if table_hint_norm:
                # soft table preference — rank assets whose ID contains the hint
                preferred = [a for a in tier2 if table_hint_norm in _norm(a.id)]
                if preferred:
                    return self._prefer_system(preferred, parsed.system, parsed.change_type)
            return self._prefer_system(list(tier2), parsed.system, parsed.change_type)

        # ── Tier 3: normalised substring / suffix match ──────────────────────
        tier3: List[Asset] = []
        for asset in self._graph.assets.values():
            asset_norm = _norm(asset.name)
            if needle_norm in asset_norm or asset_norm in needle_norm:
                tier3.append(asset)
        if tier3:
            logger.info(
                "Resolver: Tier-3 match (norm substring) → %d candidates: %s",
                len(tier3), [a.id for a in tier3],
            )
            return self._prefer_system(tier3, parsed.system, parsed.change_type)

        # ── Tier 4: fuzzy fallback ────────────────────────────────────────────
        all_names = list(self._norm_index.keys())
        close = get_close_matches(needle_norm, all_names, n=5, cutoff=0.6)
        if close:
            fuzzy_assets: List[Asset] = []
            for norm_name in close:
                fuzzy_assets.extend(self._norm_index[norm_name])
            logger.warning(
                "Resolver: no exact match for %r — fuzzy suggestions: %s → assets: %s",
                raw_target,
                close,
                [a.name for a in fuzzy_assets],
            )
            return self._prefer_system(fuzzy_assets, parsed.system, parsed.change_type)

        logger.warning(
            "Resolver: no match at any tier for %r (norm=%r). "
            "Graph asset names (first 20): %s",
            raw_target, needle_norm,
            sorted(a.name for a in self._graph.assets.values())[:20],
        )
        return None

    @staticmethod
    def _prefer_system(
        candidates: List[Asset],
        system_hint: Optional[str],
        change_type: Optional[ChangeType] = None,
    ) -> Asset:
        """Return the best candidate from *candidates*.

        FIX 5 — improved ranking:
        Priority order within a tier:
        1. Exact system + asset_type match (when change_type gives us asset_type
           context, e.g. COLUMN_DELETE → prefer COLUMN assets).
        2. System match only.
        3. First candidate (alphabetical tie-break via norm_index ordering).

        Parameters
        ----------
        candidates:
            Non-empty list of candidate assets.
        system_hint:
            SystemType value string from the request, or ``None``.
        change_type:
            Parsed change_type used to infer the expected asset_type.
        """
        # Infer expected asset_type from change_type context
        _TYPE_FROM_CHANGE: Dict[str, List[str]] = {
            ChangeType.COLUMN_RENAME.value:  ["column", "database_column"],
            ChangeType.COLUMN_DELETE.value:  ["column", "database_column"],
            ChangeType.COLUMN_ADD.value:     ["column", "database_column"],
            ChangeType.TABLE_RENAME.value:   ["table", "database_table"],
            ChangeType.TABLE_DELETE.value:   ["table", "database_table"],
            ChangeType.TABLE_ADD.value:      ["table", "database_table"],
        }
        expected_types = (
            _TYPE_FROM_CHANGE.get(change_type.value, []) if change_type else []
        )

        # Priority 1: system + asset_type match
        if system_hint and expected_types:
            p1 = [
                a for a in candidates
                if a.system.value == system_hint and a.asset_type.value in expected_types
            ]
            if p1:
                return p1[0]

        # Priority 2: asset_type match only (no system constraint)
        if expected_types:
            p2 = [a for a in candidates if a.asset_type.value in expected_types]
            if p2:
                # Within P2, prefer system match if hinted
                if system_hint:
                    p2s = [a for a in p2 if a.system.value == system_hint]
                    if p2s:
                        return p2s[0]
                return p2[0]

        # Priority 3: system match only
        if system_hint:
            p3 = [a for a in candidates if a.system.value == system_hint]
            if p3:
                return p3[0]

        # Fallback: first candidate
        return candidates[0]

    # ------------------------------------------------------------------
    # Internal: summary builder
    # ------------------------------------------------------------------

    @staticmethod
    def _build_summary(
        parsed: ChangeRequest,
        source_asset: Optional[Asset],
        system_breakdown: dict,
    ) -> str:
        """Compose a concise human-readable impact summary string.

        FIX 3 — unknown-asset handling:
        When *source_asset* is ``None`` the summary now explicitly states that
        no enterprise asset was found and that no impact analysis could be
        performed.  This prevents empty ``executive_summary`` fields in the
        v2 response.

        Example output (resolved)::

            Change type : COLUMN_RENAME (Revenue → GrossRevenue)
            Source asset: column::sales_dashboard::Revenue (column, database)
            Total impact: 12 downstream assets
            Breakdown   : 8 Power BI assets, 4 Database assets

        Example output (unresolved)::

            Change type : UNKNOWN
            Source asset: not found (searched for 'UnicornTable')
            No matching enterprise asset named 'UnicornTable' was found in the
            metadata graph. Impact analysis could not be performed because no
            source asset could be resolved. All impact metrics are zero.
        """
        lines: List[str] = []

        # Change description
        rename_suffix = (
            f" ({parsed.target_name} -> {parsed.new_name})"
            if parsed.new_name
            else ""
        )
        lines.append(
            f"Change type : {parsed.change_type.value.upper()}{rename_suffix}"
        )

        # Source asset — FIX 3: meaningful message when not found
        if source_asset:
            lines.append(
                f"Source asset: {source_asset.id} "
                f"({source_asset.asset_type.value}, {source_asset.system.value})"
            )
        else:
            searched = parsed.target_name or "(unspecified)"
            lines.append(f"Source asset: not found (searched for {searched!r})")
            lines.append(
                f"No matching enterprise asset named {searched!r} was found in the "
                f"metadata graph. Impact analysis could not be performed because no "
                f"source asset could be resolved. All impact metrics are zero."
            )
            return "\n".join(lines)

        # Total downstream count
        total = sum(len(v) for v in system_breakdown.values())
        lines.append(f"Total impact: {total} downstream asset{'s' if total != 1 else ''}")

        # Per-system breakdown — skip empty systems for readability
        _LABELS: dict = {
            "database":   "Database",
            "sql":        "SQL",
            "databricks": "Databricks",
            "pipeline":   "Pipeline",
            "powerbi":    "Power BI",
            "api":        "API",
        }
        non_empty = [
            f"{len(v)} {_LABELS.get(k, k)} asset{'s' if len(v) != 1 else ''}"
            for k, v in system_breakdown.items()
            if v
        ]
        if non_empty:
            lines.append("Breakdown   : " + ", ".join(non_empty))
        else:
            lines.append("Breakdown   : no downstream assets identified")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Internal: system hint extractor
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_table_hint(text: str) -> Optional[str]:
        """Scan *text* for a table-qualifier phrase like "in <table_name>".

        Extracts the word following "in", "from", "within", or "of" when it
        looks like a table name (word characters only, not a stopword).

        Returns
        -------
        str | None
            The table name token, or ``None`` when not found.
        """
        m = re.search(
            r"\b(?:in|from|within|of)\s+(?P<table>\w+)",
            text,
            re.IGNORECASE,
        )
        if m:
            candidate = m.group("table").strip()
            if candidate.lower() not in _STOPWORDS:
                return candidate
        return None

    @staticmethod
    def _extract_system_hint(text: str) -> Optional[str]:
        """Scan *text* for known system names and return the first match.

        Returns
        -------
        str | None
            A :class:`~graph.models.SystemType` value string, or ``None``.
        """
        for pattern, system_value in _SYSTEM_HINTS:
            if re.search(pattern, text, re.IGNORECASE):
                return system_value
        return None
