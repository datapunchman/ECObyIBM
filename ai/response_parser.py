"""
ai.response_parser
==================
Extracts and validates a structured ``ImpactAnalysisResponse`` from the raw
text returned by a Granite (or any instruction-following) LLM.

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
from typing import Any, Dict, Optional, Tuple

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
