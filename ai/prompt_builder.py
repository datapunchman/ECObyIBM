"""
ai.prompt_builder
=================
Assembles a concise, relevance-filtered prompt from a business change request
and the current metadata snapshot.

Token budget
------------
Target: < 1 500 input tokens.

Strategy
~~~~~~~~
1. Extract **keywords** from the request text (table names, measure names,
   column names, known domain words).
2. Score every metadata artifact against those keywords.
3. Keep only the top-N most relevant artifacts per category.
4. Emit a compact plain-text prompt — no JSON serialisation of metadata,
   no markdown tables, no full DAX expressions.
5. Log the word-count × 1.3 token estimate before returning.

The output schema the model must fill is kept intentionally minimal —
nested sub-objects (DeploymentStep, ValidationCheck) have been collapsed
to plain-string fields so the model can answer quickly and reliably.
"""

from __future__ import annotations

import logging
import re
import textwrap
from typing import Any, Dict, List, Optional, Set, Tuple

from ai.models import (
    AnalysisRequest,
    MetadataSnapshot,
    PromptPackage,
    PromptSection,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------

# How many artifacts to keep per category after relevance scoring
_MAX_TABLES: int = 5
_MAX_MEASURES: int = 8
_MAX_COLUMNS: int = 10
_MAX_REPORTS: int = 4
_MAX_RELATIONSHIPS: int = 5

# DAX expression characters shown per measure (just enough context)
_MAX_DAX_CHARS: int = 80

# Token-budget warning threshold
_TOKEN_WARN_THRESHOLD: int = 1_500


# ---------------------------------------------------------------------------
# PromptBuilder
# ---------------------------------------------------------------------------


class PromptBuilder:
    """Constructs a relevance-filtered, token-efficient prompt.

    The builder is stateless — call :meth:`build` any number of times with
    different requests or snapshots.

    Parameters
    ----------
    model_name:
        Informational label embedded in the system section.
    max_tables / max_measures / max_columns / max_reports / max_relationships:
        Per-category artifact limits after relevance filtering.
    max_dax_chars:
        Characters of DAX shown per measure.
    """

    def __init__(
        self,
        model_name: str = "IBM Granite",
        max_tables: int = _MAX_TABLES,
        max_measures: int = _MAX_MEASURES,
        max_columns: int = _MAX_COLUMNS,
        max_reports: int = _MAX_REPORTS,
        max_relationships: int = _MAX_RELATIONSHIPS,
        max_dax_chars: int = _MAX_DAX_CHARS,
    ) -> None:
        self.model_name = model_name
        self.max_tables = max_tables
        self.max_measures = max_measures
        self.max_columns = max_columns
        self.max_reports = max_reports
        self.max_relationships = max_relationships
        self.max_dax_chars = max_dax_chars

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Graph-grounded prompt (v2 architecture)
    # ------------------------------------------------------------------

    def build_from_graph(
        self,
        request: AnalysisRequest,
        graph_result: Any,  # EnterpriseGraphResult — typed as Any to avoid circular import
    ) -> str:
        """Build a graph-grounded prompt for Granite v2 reasoning.

        The prompt passes the full deterministic impact graph to Granite and
        enforces two layers of hallucination prevention:

        1. **Positive constraint** — only non-empty buckets are shown to the
           model.  Buckets that are empty are listed explicitly under
           ``SYSTEMS NOT IMPACTED`` with a hard prohibition so Granite cannot
           speculate about them even if it recognises the technology name.

        2. **Per-item prohibition** — the ``OUTPUT RULES`` section reiterates
           the absent systems individually, e.g. "Do NOT mention notebooks —
           none were discovered".  This targets the most common failure mode
           where the model infers a likely downstream system from general
           knowledge rather than from the supplied facts.

        Parameters
        ----------
        request:
            The original ``AnalysisRequest``.
        graph_result:
            An ``EnterpriseGraphResult`` produced by
            :class:`~ai.graph_orchestrator.GraphOrchestrator`.

        Returns
        -------
        str
            Complete prompt text, ready to send to
            :meth:`~ai.granite_client.GraniteClient.generate`.
        """
        ga = graph_result.graph_analysis

        # -- Classify buckets into present / absent --------------------------
        present_buckets: List[str] = []   # non-empty buckets
        absent_buckets: List[str] = []    # empty buckets

        for bucket, assets in ga.items():
            if bucket in ("dependency_paths", "metrics"):
                continue
            if assets:
                present_buckets.append(bucket)
            else:
                absent_buckets.append(bucket)

        # -- Compact asset listing (present buckets only) --------------------
        bucket_lines: List[str] = []
        for bucket in present_buckets:
            names = ", ".join(a.asset.name for a in ga[bucket])
            bucket_lines.append(f"  {bucket}: {names}")

        # -- Dependency paths (first 10 to stay within token budget) ---------
        paths = graph_result.dependency_paths or []
        path_lines: List[str] = []
        for p in paths[:10]:
            path_lines.append("  " + " → ".join(str(node) for node in p))

        # -- Metrics ---------------------------------------------------------
        m = graph_result.metrics or {}
        metrics_text = (
            f"total_assets={m.get('total_assets', 0)}, "
            f"critical_assets={m.get('critical_assets', 0)}, "
            f"max_depth={m.get('max_depth', 0)}, "
            f"systems_impacted={m.get('systems_impacted', 0)}"
        )

        # -- Source asset ----------------------------------------------------
        src = graph_result.source_asset
        source_line = src.name if src else "(unknown — no matching asset in graph)"

        # -- Per-item prohibition lines for absent systems -------------------
        # Map each bucket to natural-language system name for clearer prohibition
        _BUCKET_LABEL: Dict[str, str] = {
            "database_tables":      "database tables",
            "views":                "views",
            "materialized_views":   "materialized views",
            "stored_procedures":    "stored procedures",
            "functions":            "functions",
            "databricks_notebooks": "Databricks notebooks",
            "spark_jobs":           "Spark jobs",
            "delta_live_tables":    "Delta Live Tables",
            "unity_catalog":        "Unity Catalog assets",
            "pipelines":            "pipelines",
            "data_factory":         "Data Factory pipelines",
            "airflow":              "Airflow DAGs",
            "fabric_pipelines":     "Fabric pipelines",
            "semantic_models":      "semantic models",
            "powerbi_reports":      "Power BI reports",
            "dashboards":           "dashboards",
            "apis":                 "APIs",
            "external_consumers":   "external consumers",
        }
        prohibition_lines: List[str] = [
            f"  - Do NOT mention {_BUCKET_LABEL.get(b, b)} — none were discovered by the graph."
            for b in absent_buckets
        ]

        # -- Absent-system summary line for the IMPACTED ASSETS section ------
        if absent_buckets:
            absent_summary = (
                "NOT impacted (graph found zero assets in these buckets — "
                "do NOT mention them):\n"
                + "\n".join(
                    f"  {b}: EMPTY"
                    for b in absent_buckets
                )
            )
        else:
            absent_summary = ""

        # -- Assemble --------------------------------------------------------
        impacted_content_parts = [
            "\n".join(bucket_lines) if bucket_lines
            else "  (no downstream assets found in graph)"
        ]
        if absent_summary:
            impacted_content_parts.append(absent_summary)
        impacted_content = "\n\n".join(impacted_content_parts)

        output_rules = textwrap.dedent("""\
            Respond with this exact JSON structure (fill every field).

            ABSOLUTE RULES — violation is not permitted:
            1. Only reference assets that appear in IMPACTED ASSETS above.
            2. If a system bucket is EMPTY above, do NOT mention that system
               anywhere in your response — not in the summary, not in the plan,
               not in the checklist, not in the rollback.
            3. Do NOT infer, assume, or speculate about systems not listed.
            4. Do NOT add steps for systems whose bucket is empty.
        """).rstrip()

        if prohibition_lines:
            output_rules += (
                "\n\nSPECIFIC PROHIBITIONS for this request:\n"
                + "\n".join(prohibition_lines)
            )

        output_rules += textwrap.dedent("""

            {
              "executive_summary": "<2-3 sentence summary — only mention systems with non-empty buckets>",
              "risk_level": "<low|medium|high|critical>",
              "risk_rationale": "<1 sentence — based only on discovered assets>",
              "deployment_plan": ["<step referencing only discovered assets>", "..."],
              "validation_checklist": ["<check referencing only discovered assets>", "..."],
              "rollback_plan": ["<step referencing only discovered assets>", "..."]
            }
        """).rstrip()

        sections: List[PromptSection] = [
            PromptSection(
                heading="ROLE",
                content=(
                    "You are an Enterprise Data Platform Architect performing change "
                    "impact analysis.\n"
                    "Return ONLY a valid JSON object — no preamble, no markdown fences.\n\n"
                    "FUNDAMENTAL CONSTRAINT: The graph engine has already determined which "
                    "assets are impacted with confidence=1.0. Your role is to REASON over "
                    "those facts — not to discover, infer, or speculate about additional "
                    "systems. If a system bucket is empty, that system is NOT part of this "
                    "change and you MUST NOT mention it."
                ),
            ),
            PromptSection(
                heading="CHANGE REQUEST",
                content=(
                    f"Request: {request.request}\n"
                    f"Source asset (direct target): {source_line}"
                ),
            ),
            PromptSection(
                heading="IMPACTED ASSETS (graph-authoritative, confidence=1.0)",
                content=impacted_content,
            ),
            PromptSection(
                heading="DEPENDENCY PATHS",
                content=(
                    "\n".join(path_lines) if path_lines
                    else "  (no paths recorded)"
                ),
            ),
            PromptSection(
                heading="GRAPH METRICS",
                content=metrics_text,
            ),
            PromptSection(
                heading="OUTPUT SCHEMA AND RULES",
                content=output_rules,
            ),
        ]

        prompt_text = "\n\n".join(
            f"=== {s.heading} ===\n{s.content}" for s in sections
        )
        token_estimate = int(len(prompt_text.split()) * 1.3)

        if token_estimate > _TOKEN_WARN_THRESHOLD:
            logger.warning(
                "Graph-grounded prompt token estimate %d exceeds budget %d",
                token_estimate,
                _TOKEN_WARN_THRESHOLD,
            )
        else:
            logger.info(
                "Graph-grounded prompt built: ~%d tokens, %d present buckets, "
                "%d absent buckets",
                token_estimate,
                len(present_buckets),
                len(absent_buckets),
            )

        return prompt_text

    # ------------------------------------------------------------------
    # Metadata-snapshot prompt (v1 / legacy)
    # ------------------------------------------------------------------

    def build(
        self,
        request: AnalysisRequest,
        snapshot: MetadataSnapshot,
    ) -> PromptPackage:
        """Build and return a :class:`PromptPackage`.

        Parameters
        ----------
        request:
            The ``AnalysisRequest`` describing the proposed change.
        snapshot:
            The ``MetadataSnapshot`` from the Metadata Engine.

        Returns
        -------
        PromptPackage
            Contains the prompt string, individual sections, and estimated
            token count.
        """
        keywords = _extract_keywords(request.request)
        logger.debug("Prompt keywords extracted: %s", sorted(keywords))

        filtered = _filter_snapshot(
            snapshot=snapshot,
            keywords=keywords,
            max_tables=self.max_tables,
            max_measures=self.max_measures,
            max_columns=self.max_columns,
            max_reports=self.max_reports,
            max_relationships=self.max_relationships,
        )

        sections: List[PromptSection] = [
            self._build_system_section(),
            self._build_request_section(request),
            self._build_metadata_section(filtered, snapshot),
            self._build_output_section(),
        ]

        prompt_text = "\n\n".join(
            f"=== {s.heading} ===\n{s.content}" for s in sections
        )
        token_estimate = int(len(prompt_text.split()) * 1.3)

        logger.info(
            "Prompt built: ~%d tokens (%d tables, %d measures, %d columns, "
            "%d reports, %d relationships)",
            token_estimate,
            len(filtered["tables"]),
            len(filtered["measures"]),
            len(filtered["columns"]),
            len(filtered["reports"]),
            len(filtered["relationships"]),
        )
        if token_estimate > _TOKEN_WARN_THRESHOLD:
            logger.warning(
                "Prompt token estimate %d exceeds budget of %d — "
                "consider tightening filter limits.",
                token_estimate,
                _TOKEN_WARN_THRESHOLD,
            )

        return PromptPackage(
            request=request,
            metadata_snapshot=snapshot,
            prompt_text=prompt_text,
            sections=sections,
            token_estimate=token_estimate,
        )

    # ------------------------------------------------------------------
    # Section builders
    # ------------------------------------------------------------------

    def _build_system_section(self) -> PromptSection:
        content = textwrap.dedent(f"""\
            You are an Enterprise Data Platform Architect performing impact analysis \
for a Power BI + Databricks platform.
            Return ONLY a valid JSON object — no preamble, no markdown fences.
        """).strip()
        return PromptSection(heading="ROLE", content=content)

    def _build_request_section(self, request: AnalysisRequest) -> PromptSection:
        lines = [
            f"Request: {request.request}",
            f"Type: {request.change_type.value}",
        ]
        if request.context:
            for k, v in request.context.items():
                lines.append(f"{k}: {v}")
        return PromptSection(heading="CHANGE REQUEST", content="\n".join(lines))

    def _build_metadata_section(
        self,
        filtered: Dict[str, List[Any]],
        snapshot: MetadataSnapshot,
    ) -> PromptSection:
        parts: List[str] = []

        # Tables
        if filtered["tables"]:
            parts.append("TABLES: " + ", ".join(
                t["name"] for t in filtered["tables"]
            ))

        # Relationships
        if filtered["relationships"]:
            rels = [
                f"{r['from_table']}[{r['from_column']}]->{r['to_table']}[{r['to_column']}]"
                for r in filtered["relationships"]
            ]
            parts.append("RELATIONSHIPS: " + "; ".join(rels))

        # Columns (grouped by table)
        if filtered["columns"]:
            by_table: Dict[str, List[str]] = {}
            for c in filtered["columns"]:
                by_table.setdefault(c["table_name"], []).append(
                    f"{c['name']}:{c.get('data_type','?')}"
                )
            col_lines = [
                f"  {tbl}: {', '.join(cols)}" for tbl, cols in by_table.items()
            ]
            parts.append("COLUMNS:\n" + "\n".join(col_lines))

        # Measures (name + trimmed DAX)
        if filtered["measures"]:
            m_lines: List[str] = []
            for m in filtered["measures"]:
                dax = (m.get("expression") or "").replace("\n", " ").replace("\t", " ").strip()
                if len(dax) > self.max_dax_chars:
                    dax = dax[: self.max_dax_chars] + "…"
                m_lines.append(f"  [{m['name']}] = {dax}")
            parts.append("MEASURES:\n" + "\n".join(m_lines))

        # Report pages
        if filtered["reports"]:
            r_lines = [
                f"  {rpt.get('display_name', rpt.get('page_name', '?'))}: "
                + ", ".join(f"[{x}]" for x in rpt.get("used_measures", [])[:5])
                for rpt in filtered["reports"]
            ]
            parts.append("REPORT PAGES:\n" + "\n".join(r_lines))

        parts.append(
            f"TOTAL ARTIFACTS: {len(snapshot.tables)} tables, "
            f"{len(snapshot.measures)} measures, "
            f"{snapshot.dependency_edge_count} dependency edges"
        )

        return PromptSection(
            heading="RELEVANT METADATA (filtered)",
            content="\n".join(parts),
        )

    def _build_output_section(self) -> PromptSection:
        content = textwrap.dedent("""\
            Respond with this exact JSON structure (fill every field):
            {
              "executive_summary": "<2-3 sentence summary>",
              "risk_level": "<low|medium|high|critical>",
              "risk_rationale": "<1 sentence>",
              "affected_tables": ["<name>"],
              "affected_columns": ["<Table[Col]>"],
              "affected_measures": ["<[Measure]>"],
              "affected_reports": ["<page name>"],
              "impact_analysis": "<prose>",
              "deployment_plan": "<ordered steps as plain text>",
              "validation_checklist": "<checks as plain text>",
              "rollback_plan": "<steps as plain text>",
              "dependencies_impacted": 0
            }
        """).strip()
        return PromptSection(heading="OUTPUT SCHEMA", content=content)


# ---------------------------------------------------------------------------
# Keyword extraction
# ---------------------------------------------------------------------------


def _extract_keywords(text: str) -> Set[str]:
    """Return a set of lowercase tokens from the request useful for matching.

    Strips stopwords and short tokens; keeps identifiers, table names, etc.
    """
    _STOPWORDS: Set[str] = {
        "a", "an", "the", "to", "in", "of", "for", "and", "or", "is",
        "are", "be", "been", "this", "that", "with", "add", "new", "change",
        "update", "remove", "rename", "modify", "create", "delete", "want",
        "need", "should", "would", "will", "on", "at", "by", "from",
    }
    tokens: Set[str] = set()
    for raw in re.split(r"[\s,.\-/\\()\[\]\"']+", text.lower()):
        tok = raw.strip()
        if len(tok) >= 3 and tok not in _STOPWORDS:
            tokens.add(tok)
    return tokens


# ---------------------------------------------------------------------------
# Relevance scoring & filtering
# ---------------------------------------------------------------------------


def _score(name: str, extra: str, keywords: Set[str]) -> int:
    """Return a relevance score: count of keywords found in name + extra text."""
    combined = (name + " " + extra).lower()
    return sum(1 for kw in keywords if kw in combined)


def _filter_snapshot(
    snapshot: MetadataSnapshot,
    keywords: Set[str],
    max_tables: int,
    max_measures: int,
    max_columns: int,
    max_reports: int,
    max_relationships: int,
) -> Dict[str, List[Any]]:
    """Score and return the top-N artifacts per category."""

    def top(
        items: List[Dict[str, Any]],
        name_key: str,
        extra_keys: List[str],
        limit: int,
    ) -> List[Dict[str, Any]]:
        scored: List[Tuple[int, Dict[str, Any]]] = []
        for item in items:
            extra = " ".join(str(item.get(k, "")) for k in extra_keys)
            s = _score(str(item.get(name_key, "")), extra, keywords)
            scored.append((s, item))
        # Sort by score descending, stable alphabetical within same score
        scored.sort(key=lambda x: (-x[0], str(x[1].get(name_key, ""))))
        # If nothing scores > 0, fall back to the first `limit` items
        if not any(score > 0 for score, _ in scored):
            return [item for _, item in scored[:limit]]
        return [item for score, item in scored if score > 0][:limit]

    # Tables
    tables = top(
        snapshot.tables, "name",
        ["description", "source_type"], max_tables
    )

    # Measures — also consider referenced_tables / referenced_columns
    measures = top(
        snapshot.measures, "name",
        ["expression", "display_folder",
         "referenced_tables", "referenced_columns", "referenced_measures"],
        max_measures,
    )

    # Columns — boost columns from matched tables
    matched_table_names = {t["name"] for t in tables}
    cols_scored: List[Tuple[int, Dict[str, Any]]] = []
    for c in snapshot.columns:
        extra = str(c.get("data_type", "")) + " " + str(c.get("description", ""))
        s = _score(c.get("name", ""), extra, keywords)
        # Extra point if the column belongs to a relevant table
        if c.get("table_name") in matched_table_names:
            s += 1
        cols_scored.append((s, c))
    cols_scored.sort(key=lambda x: (-x[0], str(x[1].get("name", ""))))
    columns = [c for s, c in cols_scored if s > 0][:max_columns]
    if not columns:
        columns = [c for _, c in cols_scored[:max_columns]]

    # Relationships — keep those that touch matched tables
    rels: List[Dict[str, Any]] = []
    for r in snapshot.relationships:
        if (
            r.get("from_table") in matched_table_names
            or r.get("to_table") in matched_table_names
        ):
            rels.append(r)
    relationships = rels[:max_relationships]

    # Reports — keep pages that use any matched measure or table
    matched_measure_names = {m["name"] for m in measures}
    reports: List[Dict[str, Any]] = []
    for rpt in snapshot.reports:
        used_m = set(rpt.get("used_measures", []))
        used_t = set(rpt.get("used_tables", []))
        if used_m & matched_measure_names or used_t & matched_table_names:
            reports.append(rpt)
    if not reports:
        reports = snapshot.reports[:max_reports]
    reports = reports[:max_reports]

    return {
        "tables": tables,
        "measures": measures,
        "columns": columns,
        "relationships": relationships,
        "reports": reports,
    }
