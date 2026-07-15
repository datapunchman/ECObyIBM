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

import logging
import re
from difflib import get_close_matches
from typing import Dict, List, Optional, Tuple

from change.models import ChangeRequest, ChangeType, EnterpriseChangeAnalysis
from graph.enterprise_graph import EnterpriseGraph
from graph.models import Asset, SystemType
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

# Form A: rename [the] <word> [column|table] [in|from|within] <table> to <new>
_RE_RENAME_QUALIFIED = re.compile(
    r"\brename\s+(?:the\s+)?(?P<target>\w+)\s+"
    r"(?:column\s+|table\s+)?(?:in|from|within|of)\s+(?P<table>\w+)\s+"
    r"(?:column\s+|table\s+)?to\s+(?P<new_name>\w+)",
    re.IGNORECASE,
)

# Form B: rename <word> [column|table] to <new>  (no table qualifier)
_RE_RENAME_SIMPLE = re.compile(
    r"\brename\s+(?:the\s+)?(?P<target>\w+)\s+(?:column\s+|table\s+)?to\s+(?P<new_name>\w+)",
    re.IGNORECASE,
)

# Form C: rename <target> to <new>  (bare — no "column"/"table" keyword)
# Kept as final fallback for bare "rename X to Y" with single-word X
_RE_RENAME_BARE = re.compile(
    r"\brename\s+(?P<target>\w+)\s+to\s+(?P<new_name>\w+)",
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
_RE_TABLE_DELETE = re.compile(
    r"\b(?:drop|remove|delete)\s+(?:the\s+)?(?:table\s+)?(?P<target>\w+)\s*(?:table\b|$)",
    re.IGNORECASE,
)

# "add|create [the|a] <X> table"  or  "add|create table <X>"
_RE_TABLE_ADD = re.compile(
    r"\b(?:add|create)\s+(?:the\s+|a\s+)?(?:table\s+)?(?P<target>\w+)\s*(?:table\b|$)",
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
        if source_asset is None:
            logger.info(
                "No matching asset found for target=%r — returning empty analysis",
                parsed.target_name,
            )

        # Step 3 — graph traversal
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

        # Step 4 — build summary
        summary = self._build_summary(parsed, source_asset, system_breakdown)

        return EnterpriseChangeAnalysis(
            change_request=parsed,
            source_asset=source_asset,
            impact_count=len(downstream),
            impacted_assets=downstream,
            system_breakdown=system_breakdown,
            dependency_paths=paths,
            summary=summary,
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
            new_name = m.group("new_name").strip()
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
            new_name = m.group("new_name").strip()
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
            new_name = m.group("new_name").strip()
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
                return self._prefer_system(tier1, parsed.system)

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
                    return self._prefer_system(preferred, parsed.system)
            return self._prefer_system(list(tier2), parsed.system)

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
            return self._prefer_system(tier3, parsed.system)

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
            return self._prefer_system(fuzzy_assets, parsed.system)

        logger.warning(
            "Resolver: no match at any tier for %r (norm=%r). "
            "Graph asset names (first 20): %s",
            raw_target, needle_norm,
            sorted(a.name for a in self._graph.assets.values())[:20],
        )
        return None

    @staticmethod
    def _prefer_system(candidates: List[Asset], system_hint: Optional[str]) -> Asset:
        """Return the best candidate from *candidates*.

        Prefers assets whose system matches *system_hint* when provided.
        Falls back to the first candidate otherwise.
        """
        if system_hint:
            preferred = [a for a in candidates if a.system.value == system_hint]
            if preferred:
                return preferred[0]
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

        Example output::

            Change type : COLUMN_RENAME (Revenue → GrossRevenue)
            Source asset: column::sales_dashboard::Revenue (column, powerbi)
            Total impact: 12 downstream assets
            Breakdown   : 8 Power BI assets, 4 Databricks assets

        Parameters
        ----------
        parsed:
            The parsed change request.
        source_asset:
            The resolved source asset, or ``None``.
        system_breakdown:
            Dict mapping system name → list of downstream assets.

        Returns
        -------
        str
            Multi-line summary string.
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

        # Source asset
        if source_asset:
            lines.append(
                f"Source asset: {source_asset.id} "
                f"({source_asset.asset_type.value}, {source_asset.system.value})"
            )
        else:
            lines.append(
                f"Source asset: not found"
                + (f" (searched for '{parsed.target_name}')" if parsed.target_name else "")
            )

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
