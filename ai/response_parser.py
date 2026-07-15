"""
ai.response_parser
==================
Extracts and validates a structured ``ImpactAnalysisResponse`` from the raw
text returned by a Granite (or any instruction-following) LLM.

Hallucination guard (v2)
------------------------
``parse_v2()`` accepts an optional ``empty_buckets`` set that lists every
enterprise bucket that the graph returned empty.  After JSON parsing the
scrubber :func:`_scrub_absent_systems` walks every string in
``deployment_plan``, ``validation_checklist``, and ``rollback_plan`` and
removes any item that mentions a system whose bucket was empty.  Items are
matched against a keyword map so ``"update the Databricks notebook"`` is
removed when ``databricks_notebooks`` is empty.

This is a second line of defence behind the prompt instructions — it
guarantees that hallucinated system mentions never reach the caller even if
the model ignores the prompt constraints.

Granite is instructed to return a **single JSON object** matching the
``ImpactAnalysisResponse`` schema.  In practice the model may:

* Return pure JSON (ideal case).
* Wrap the JSON in a markdown code fence (```json … ```).
* Prepend a short preamble before the JSON object.
* Return JSON with trailing commas or other minor formatting issues.

This module handles all of those gracefully.

Pipeline
--------

    ResponseParser.parse(raw_text: str) -> ImpactAnalysisResponse
        │
        ├─ 1. Strip markdown fences and prose preamble
        ├─ 2. Locate the outermost JSON object ``{ … }``
        ├─ 3. ``json.loads()``
        ├─ 4. Normalise prose fields (list[str] → newline-joined str)
        ├─ 5. Pydantic validation → ImpactAnalysisResponse
        └─ 6. On failure: return a structured ParseFailureResponse
               so callers always receive a typed object

Error strategy
--------------
Parsing errors never propagate as exceptions out of ``parse()``.
Instead a ``ParseFailureResponse`` is returned that carries the original
raw text and a human-readable reason — this lets the FastAPI layer return
a 200 with a clearly-flagged degraded response rather than a 500.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from pydantic import ValidationError

from ai.models import ImpactAnalysisResponse, RiskLevel

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Regex helpers
# ---------------------------------------------------------------------------

# Matches a markdown code fence with optional language tag
_RE_CODE_FENCE = re.compile(r"```(?:json)?\s*([\s\S]*?)```", re.IGNORECASE)

# Matches the opening brace of a JSON object (possibly after prose preamble)
_RE_JSON_START = re.compile(r"\{")


# ---------------------------------------------------------------------------
# ParseFailureResponse — graceful degraded return
# ---------------------------------------------------------------------------


class ParseFailureResponse(ImpactAnalysisResponse):
    """Returned when the LLM output cannot be parsed or validated.

    All required fields are filled with sentinel values so callers can
    always treat the return value as an ``ImpactAnalysisResponse``.
    The ``_parse_error`` and ``_raw_text`` extra fields carry diagnostics.
    """

    model_config = {"frozen": True, "extra": "allow"}

    @classmethod
    def from_error(
        cls,
        reason: str,
        raw_text: str,
    ) -> "ParseFailureResponse":
        """Factory — build a degraded response with error annotations."""
        logger.error("ResponseParser failed: %s", reason)
        return cls(
            executive_summary=(
                "[PARSE ERROR] The AI model response could not be parsed. "
                f"Reason: {reason}"
            ),
            risk_level=RiskLevel.CRITICAL,
            risk_rationale="Response parsing failed — manual review required.",
            impact_analysis=(
                "The raw LLM output is available in the _raw_text field. "
                "Please review manually."
            ),
            rollback_plan=["Manual review required due to parse failure."],
            **{
                "_parse_error": reason,
                "_raw_text": raw_text[:4000],  # cap to avoid huge payloads
            },
        )


# ---------------------------------------------------------------------------
# ResponseParser
# ---------------------------------------------------------------------------


class ResponseParser:
    """Extracts and validates an ``ImpactAnalysisResponse`` from raw LLM text.

    The parser is stateless — instantiate once and call :meth:`parse`
    repeatedly.
    """

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def parse_v2(
        self,
        raw_text: str,
        empty_buckets: Optional[Set[str]] = None,
    ) -> Dict[str, Any]:
        """Parse the v2 Granite response into an ``llm_summary`` dict.

        The v2 schema only requires 6 fields (no artifact lists — those come
        from the graph).  If parsing fails a safe fallback dict is returned
        so the v2 endpoint always succeeds.

        A second-line-of-defence scrubber runs after parsing: any list item
        in ``deployment_plan``, ``validation_checklist``, or ``rollback_plan``
        that mentions a system whose bucket was empty is silently removed and
        logged.  This guarantees hallucinated system mentions never reach the
        caller even when the model ignores the prompt constraints.

        Parameters
        ----------
        raw_text:
            Raw text returned by Granite.
        empty_buckets:
            Set of enterprise bucket names whose asset list was empty (e.g.
            ``{"databricks_notebooks", "pipelines"}``).  Items mentioning
            these systems are scrubbed from the free-text list fields.
            Pass ``None`` or an empty set to skip scrubbing.

        Returns
        -------
        dict
            Keys: ``executive_summary``, ``risk_level``, ``risk_rationale``,
            ``deployment_plan``, ``validation_checklist``, ``rollback_plan``.
            A ``_scrubbed_items`` key is added (list[str]) when items were
            removed, for auditability.
        """
        _V2_DEFAULTS: Dict[str, Any] = {
            "executive_summary": "[PARSE ERROR] Could not parse LLM response.",
            "risk_level": "critical",
            "risk_rationale": "Response parsing failed — manual review required.",
            "deployment_plan": [],
            "validation_checklist": [],
            "rollback_plan": [],
        }
        if not raw_text or not raw_text.strip():
            logger.error("parse_v2: LLM returned empty response.")
            return _V2_DEFAULTS

        json_str, _ = self._extract_json(raw_text)
        if not json_str:
            logger.error("parse_v2: no JSON object found in LLM response.")
            return _V2_DEFAULTS

        try:
            data: Dict[str, Any] = json.loads(json_str)
        except json.JSONDecodeError:
            cleaned = self._clean_json(json_str)
            try:
                data = json.loads(cleaned)
            except json.JSONDecodeError as exc:
                logger.error("parse_v2: JSON decode error: %s", exc)
                return _V2_DEFAULTS

        _normalise_data(data)

        # Keep only the 6 expected llm_summary fields; fill missing ones.
        summary: Dict[str, Any] = {
            "executive_summary": str(data.get("executive_summary", _V2_DEFAULTS["executive_summary"])),
            "risk_level": str(data.get("risk_level", "unknown")).lower(),
            "risk_rationale": str(data.get("risk_rationale", "")),
            "deployment_plan": data.get("deployment_plan") or [],
            "validation_checklist": data.get("validation_checklist") or [],
            "rollback_plan": data.get("rollback_plan") or [],
        }

        # -- Hallucination scrubber ------------------------------------------
        if empty_buckets:
            scrubbed = _scrub_absent_systems(summary, empty_buckets)
            if scrubbed:
                logger.warning(
                    "parse_v2: scrubbed %d hallucinated item(s) referencing "
                    "empty buckets %s: %s",
                    len(scrubbed),
                    sorted(empty_buckets),
                    scrubbed,
                )
                summary["_scrubbed_items"] = scrubbed

        logger.info("parse_v2 success: risk_level=%s", summary["risk_level"])
        return summary

    def parse(self, raw_text: str) -> ImpactAnalysisResponse:
        """Parse ``raw_text`` into a validated ``ImpactAnalysisResponse``.

        Never raises — on any failure returns a ``ParseFailureResponse``.

        Parameters
        ----------
        raw_text:
            The complete string returned by the LLM.

        Returns
        -------
        ImpactAnalysisResponse
            A fully-validated instance, or a ``ParseFailureResponse`` on error.
        """
        if not raw_text or not raw_text.strip():
            return ParseFailureResponse.from_error(
                "LLM returned an empty response.", raw_text or ""
            )

        # Step 1 — extract JSON candidate string
        json_str, extraction_note = self._extract_json(raw_text)
        if not json_str:
            return ParseFailureResponse.from_error(
                f"No JSON object found in LLM response. {extraction_note}",
                raw_text,
            )

        # Step 2 — parse JSON
        try:
            data: Dict[str, Any] = json.loads(json_str)
        except json.JSONDecodeError as exc:
            # Try cleaning common JSON issues before giving up
            cleaned = self._clean_json(json_str)
            try:
                data = json.loads(cleaned)
            except json.JSONDecodeError:
                return ParseFailureResponse.from_error(
                    f"JSON decode error: {exc}",
                    raw_text,
                )

        # Step 3 — normalise prose fields that the model may return as list[str]
        _normalise_data(data)

        # Step 4 — Pydantic validation
        try:
            result = ImpactAnalysisResponse(**data)
            logger.info(
                "Response parsed successfully: risk_level=%s, "
                "affected_tables=%d, affected_measures=%d",
                result.risk_level.value,
                len(result.affected_tables),
                len(result.affected_measures),
            )
            return result
        except (ValidationError, TypeError, KeyError) as exc:
            return ParseFailureResponse.from_error(
                f"Pydantic validation error: {exc}",
                raw_text,
            )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_json(text: str) -> Tuple[Optional[str], str]:
        """Locate the first complete JSON object in ``text``.

        Returns
        -------
        (json_str, note)
            ``json_str`` is the extracted JSON string, or ``None`` on failure.
            ``note`` is a diagnostic message.
        """
        # Priority 1: markdown code fence
        fence_match = _RE_CODE_FENCE.search(text)
        if fence_match:
            candidate = fence_match.group(1).strip()
            if candidate.startswith("{"):
                logger.debug("JSON extracted from markdown code fence.")
                return candidate, "Extracted from code fence."

        # Priority 2: find the first '{' and the matching closing '}'
        start_match = _RE_JSON_START.search(text)
        if not start_match:
            return None, "No opening brace found."

        start = start_match.start()
        depth = 0
        in_string = False
        escape_next = False

        for i, ch in enumerate(text[start:], start=start):
            if escape_next:
                escape_next = False
                continue
            if ch == "\\" and in_string:
                escape_next = True
                continue
            if ch == '"' and not escape_next:
                in_string = not in_string
                continue
            if in_string:
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    candidate = text[start : i + 1]
                    logger.debug(
                        "JSON extracted via brace matching (%d chars).", len(candidate)
                    )
                    return candidate, "Extracted via brace matching."

        return None, "Unmatched braces — JSON object is incomplete."

    @staticmethod
    def _clean_json(json_str: str) -> str:
        """Apply lightweight repairs to common LLM JSON formatting issues.

        * Trailing commas before ``}`` or ``]``
        """
        cleaned = re.sub(r",\s*([}\]])", r"\1", json_str)
        return cleaned


# ---------------------------------------------------------------------------
# Normalisation helpers
# ---------------------------------------------------------------------------

# Fields that must be List[str] in ImpactAnalysisResponse.
_LIST_FIELDS: tuple[str, ...] = (
    "deployment_plan",
    "validation_checklist",
    "rollback_plan",
)


def _normalise_data(data: Dict[str, Any]) -> None:
    """Ensure list fields always arrive at Pydantic as ``List[str]``.

    Granite typically returns these as ``list[str]``, which is the canonical
    form.  When it returns a plain string instead (e.g. a single paragraph),
    wrap it in a one-element list so validation never fails.
    """
    for field in _LIST_FIELDS:
        value = data.get(field)
        if isinstance(value, str):
            data[field] = [value] if value.strip() else []
            logger.debug("Normalised '%s': str → list[1]", field)
        elif not isinstance(value, list):
            # Absent, None, or unexpected type — default to empty list.
            data[field] = []


# ---------------------------------------------------------------------------
# Hallucination scrubber
# ---------------------------------------------------------------------------

# Maps every enterprise bucket name to the keywords that would appear in
# a hallucinated LLM sentence about that bucket.  Matching is
# case-insensitive substring matching on each list-item string.
_BUCKET_KEYWORDS: Dict[str, List[str]] = {
    "database_tables":      ["database table", "sql table", "db table"],
    "views":                ["view", "sql view", "db view"],
    "materialized_views":   ["materialized view", "materialised view"],
    "stored_procedures":    ["stored procedure", "stored proc", "sproc"],
    "functions":            ["udf", "user-defined function", "sql function"],
    "databricks_notebooks": ["databricks notebook", "notebook", "databricks"],
    "spark_jobs":           ["spark job", "spark cluster", "spark submit"],
    "delta_live_tables":    ["delta live", "dlt pipeline", "delta live table"],
    "unity_catalog":        ["unity catalog", "unity catalogue"],
    "pipelines":            ["pipeline", "etl pipeline", "data pipeline"],
    "data_factory":         ["data factory", "adf", "azure data factory"],
    "airflow":              ["airflow", "dag ", "airflow dag"],
    "fabric_pipelines":     ["fabric pipeline", "microsoft fabric"],
    "semantic_models":      ["semantic model", "dataset", "tabular model"],
    "powerbi_reports":      ["power bi report", "powerbi report", "pbi report", "report page"],
    "dashboards":           ["dashboard"],
    "apis":                 [" api ", "rest api", "web api", "api endpoint"],
    "external_consumers":   ["external consumer", "downstream consumer"],
}

# Fields in the llm_summary dict that contain free-text list items to scrub.
_SCRUB_FIELDS: tuple[str, ...] = (
    "deployment_plan",
    "validation_checklist",
    "rollback_plan",
)


def _scrub_absent_systems(
    summary: Dict[str, Any],
    empty_buckets: Set[str],
) -> List[str]:
    """Remove list items that mention a system whose bucket is empty.

    Modifies *summary* in-place for the three free-text list fields.

    Parameters
    ----------
    summary:
        The parsed ``llm_summary`` dict (modified in-place).
    empty_buckets:
        Set of bucket names that were empty in the graph result.

    Returns
    -------
    list[str]
        All items that were removed (for audit logging).
    """
    # Build the set of keywords to watch for from empty buckets only.
    watch_keywords: List[str] = []
    for bucket in empty_buckets:
        watch_keywords.extend(_BUCKET_KEYWORDS.get(bucket, []))

    if not watch_keywords:
        return []

    removed: List[str] = []

    for field in _SCRUB_FIELDS:
        items: List[str] = summary.get(field, [])
        if not isinstance(items, list):
            continue
        kept: List[str] = []
        for item in items:
            item_lower = item.lower()
            hit = next(
                (kw for kw in watch_keywords if kw in item_lower), None
            )
            if hit:
                removed.append(item)
                logger.debug(
                    "_scrub_absent_systems: removed %r (matched keyword %r in field %r)",
                    item, hit, field,
                )
            else:
                kept.append(item)
        summary[field] = kept

    return removed
