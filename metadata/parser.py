"""
metadata.parser
===============
Low-level parsers for every source file format consumed by the Metadata Engine.

Parsers
-------
* TmdlTableParser   — parses a single ``.tmdl`` table file
* TmdlRelParser     — parses the ``relationships.tmdl`` file
* PbirPageParser    — parses a single ``page.json`` + its ``visuals/`` directory
* PbirPagesParser   — discovers and iterates all pages via ``pages.json``

Design principles
-----------------
* Each parser accepts a ``pathlib.Path`` and returns plain Python objects
  (lists of dicts) — no Pydantic yet.  Models are instantiated in ``loader.py``.
* Parsers never raise — they log warnings and return partial data.
* All regex patterns are compiled once at module level.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Compiled regex patterns for TMDL parsing
# ---------------------------------------------------------------------------

# table header: "table sales_dashboard"
_RE_TABLE_HEADER = re.compile(r"^table\s+(.+)$")
# lineageTag at table level (\t) or column/measure level (\t\t)
_RE_LINEAGE_TAG = re.compile(r"^\t+lineageTag:\s+(.+)$")
# column declaration at single-tab indentation: "\tcolumn OrderDate"
_RE_COLUMN_HEADER = re.compile(r"^\t(?![\t\s])column\s+(.+)$")
# measure declaration — single-line or multi-line (single tab)
_RE_MEASURE_SINGLE = re.compile(r"^\tmeasure\s+'?(.+?)'?\s*=\s*(.+)$")
_RE_MEASURE_MULTILINE = re.compile(r"^\tmeasure\s+'?(.+?)'?\s*=$")
# dataType at two-tab level
_RE_DATA_TYPE = re.compile(r"^\t+dataType:\s+(.+)$")
# isHidden at two-tab level
_RE_IS_HIDDEN = re.compile(r"^\t+isHidden\b")
# formatString
_RE_FORMAT_STRING = re.compile(r"^\t+formatString:\s+(.+)$")
# displayFolder
_RE_DISPLAY_FOLDER = re.compile(r"^\t+displayFolder:\s+(.+)$")
# summarizeBy
_RE_SUMMARIZE_BY = re.compile(r"^\t+summarizeBy:\s+(.+)$")
# dataCategory
_RE_DATA_CATEGORY = re.compile(r"^\t+dataCategory:\s+(.+)$")
# sortByColumn
_RE_SORT_BY = re.compile(r"^\t+sortByColumn:\s+(.+)$")
# annotation at any indentation level
_RE_ANNOTATION = re.compile(r"^\t+annotation\s+(\w+)\s*=\s*(.+)$")
# partition at single-tab level
_RE_PARTITION = re.compile(r"^\tpartition\s+.+\s*=\s*(\w+)$")
# relationship header: "relationship <id>"
_RE_REL_HEADER = re.compile(r"^relationship\s+(.+)$")
# from/toColumn: "\tfromColumn: table.column"
_RE_FROM_COL = re.compile(r"^\t+fromColumn:\s+(\S+)\.(\S+)$")
_RE_TO_COL = re.compile(r"^\t+toColumn:\s+(\S+)\.(\S+)$")
_RE_IS_ACTIVE = re.compile(r"^\t+isActive:\s+(true|false)$", re.IGNORECASE)
_RE_CROSS_FILTER = re.compile(r"^\t+crossFilteringBehavior:\s+(.+)$")
_RE_FROM_CARDINALITY = re.compile(r"^\t+fromCardinality:\s+(.+)$")
_RE_TO_CARDINALITY = re.compile(r"^\t+toCardinality:\s+(.+)$")
_RE_JOIN_DATE = re.compile(r"^\t+joinOnDateBehavior:\s+(.+)$")


# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------


def _strip_quotes(value: str) -> str:
    """Remove surrounding single or double quotes from a TMDL string value."""
    value = value.strip()
    if (value.startswith('"') and value.endswith('"')) or (
        value.startswith("'") and value.endswith("'")
    ):
        return value[1:-1]
    return value


def _parse_annotation_value(raw: str) -> str:
    """Unwrap TMDL annotation value — strip outer quotes if present."""
    raw = raw.strip()
    # Quoted string annotation: "\"some text\""
    if raw.startswith('"') and raw.endswith('"'):
        return raw[1:-1].replace('\\"', '"')
    return raw


# ---------------------------------------------------------------------------
# TMDL table parser
# ---------------------------------------------------------------------------


class TmdlTableParser:
    """Parses a single TMDL table file.

    Parameters
    ----------
    path:
        Absolute path to the ``.tmdl`` file.

    Returns (via :meth:`parse`)
    ---------------------------
    A dict with keys:
        ``name``, ``lineage_tag``, ``source_type``, ``is_hidden``,
        ``is_date_table``, ``description``, ``annotations``,
        ``columns``, ``measures``.
    """

    def __init__(self, path: Path) -> None:
        self.path = path

    def parse(self) -> Dict[str, Any]:
        """Parse the TMDL file and return a raw metadata dict."""
        try:
            text = self.path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.error("Cannot read TMDL file %s: %s", self.path, exc)
            return {}

        lines = text.splitlines()
        result: Dict[str, Any] = {
            "name": "",
            "lineage_tag": None,
            "source_type": "unknown",
            "is_hidden": False,
            "is_date_table": False,
            "display_folder": None,
            "description": None,
            "annotations": {},
            "columns": [],
            "measures": [],
        }

        # --- first pass: extract table-level fields ---
        for line in lines:
            if m := _RE_TABLE_HEADER.match(line):
                result["name"] = m.group(1).strip()
                continue
            if m := _RE_LINEAGE_TAG.match(line):
                if not result["lineage_tag"]:
                    result["lineage_tag"] = m.group(1).strip()
                continue
            if m := _RE_PARTITION.match(line):
                result["source_type"] = m.group(1).strip()
                continue
            if m := _RE_ANNOTATION.match(line):
                key, val = m.group(1).strip(), _parse_annotation_value(m.group(2))
                result["annotations"][key] = val
                if key == "Description" and not result["description"]:
                    result["description"] = val
                if key == "__PBI_MarkAsDateTable":
                    result["is_date_table"] = True
                continue

        # --- second pass: extract columns and measures ---
        result["columns"] = self._parse_columns(lines, result["name"])
        result["measures"] = self._parse_measures(lines, result["name"])

        logger.debug(
            "Parsed table '%s': %d columns, %d measures",
            result["name"],
            len(result["columns"]),
            len(result["measures"]),
        )
        return result

    # ------------------------------------------------------------------
    # Column parsing
    # ------------------------------------------------------------------

    def _parse_columns(self, lines: List[str], table_name: str) -> List[Dict[str, Any]]:
        """Extract all column definitions from TMDL lines."""
        columns: List[Dict[str, Any]] = []
        i = 0
        while i < len(lines):
            line = lines[i]
            if m := _RE_COLUMN_HEADER.match(line):
                col_name = _strip_quotes(m.group(1))
                col: Dict[str, Any] = {
                    "table_name": table_name,
                    "name": col_name,
                    "data_type": "unknown",
                    "lineage_tag": None,
                    "is_hidden": False,
                    "is_key": False,
                    "summarize_by": None,
                    "display_folder": None,
                    "data_category": None,
                    "sort_by_column": None,
                    "format_string": None,
                    "description": None,
                    "annotations": {},
                }
                # Scan subsequent lines for this column's properties.
                # A new single-tab declaration (column/measure/partition) ends the block.
                i += 1
                while i < len(lines):
                    inner = lines[i]
                    # Non-indented line ends the table block entirely
                    if inner and inner[0] != "\t":
                        break
                    # A sibling single-\t declaration starts a new block
                    if (
                        _RE_COLUMN_HEADER.match(inner)
                        or _RE_MEASURE_SINGLE.match(inner)
                        or _RE_MEASURE_MULTILINE.match(inner)
                        or _RE_PARTITION.match(inner)
                    ):
                        break
                    if m2 := _RE_DATA_TYPE.match(inner):
                        col["data_type"] = m2.group(1).strip()
                    elif _RE_IS_HIDDEN.match(inner):
                        col["is_hidden"] = True
                    elif m2 := _RE_LINEAGE_TAG.match(inner):
                        if not col["lineage_tag"]:
                            col["lineage_tag"] = m2.group(1).strip()
                    elif m2 := _RE_FORMAT_STRING.match(inner):
                        col["format_string"] = m2.group(1).strip()
                    elif m2 := _RE_DISPLAY_FOLDER.match(inner):
                        col["display_folder"] = m2.group(1).strip()
                    elif m2 := _RE_SUMMARIZE_BY.match(inner):
                        col["summarize_by"] = m2.group(1).strip()
                    elif m2 := _RE_DATA_CATEGORY.match(inner):
                        col["data_category"] = m2.group(1).strip()
                    elif m2 := _RE_SORT_BY.match(inner):
                        col["sort_by_column"] = m2.group(1).strip()
                    elif m2 := _RE_ANNOTATION.match(inner):
                        key, val = m2.group(1).strip(), _parse_annotation_value(m2.group(2))
                        col["annotations"][key] = val
                        if key == "Description" and not col["description"]:
                            col["description"] = val
                    i += 1
                    continue
                # Mark likely FK columns as is_key based on display folder
                if (col.get("display_folder") or "").startswith("_Keys"):
                    col["is_key"] = True
                columns.append(col)
                continue  # outer loop already advanced
            i += 1
        return columns

    # ------------------------------------------------------------------
    # Measure parsing
    # ------------------------------------------------------------------

    def _parse_measures(self, lines: List[str], table_name: str) -> List[Dict[str, Any]]:
        """Extract all measure definitions (single-line and multi-line) from TMDL."""
        measures: List[Dict[str, Any]] = []
        i = 0
        while i < len(lines):
            line = lines[i]
            # Single-line measure: measure 'Name' = EXPRESSION
            if m := _RE_MEASURE_SINGLE.match(line):
                meas: Dict[str, Any] = self._blank_measure(table_name, m.group(1))
                meas["expression"] = m.group(2).strip()
                i, meas = self._collect_measure_properties(lines, i + 1, meas)
                measures.append(meas)
                continue
            # Multi-line measure: measure 'Name' =
            if m := _RE_MEASURE_MULTILINE.match(line):
                meas = self._blank_measure(table_name, m.group(1))
                expr_lines: List[str] = []
                i += 1
                # Expression body is indented deeper than the measure declaration
                while i < len(lines):
                    inner = lines[i]
                    # Properties appear at same indent level as measure keyword
                    if _RE_FORMAT_STRING.match(inner) or _RE_DISPLAY_FOLDER.match(inner):
                        break
                    if _RE_ANNOTATION.match(inner):
                        break
                    if _RE_LINEAGE_TAG.match(inner):
                        break
                    # Non-indented line or a new single-\t declaration ends this block
                    if inner and inner[0] != "\t":
                        break
                    if (
                        _RE_COLUMN_HEADER.match(inner)
                        or _RE_MEASURE_SINGLE.match(inner)
                        or _RE_MEASURE_MULTILINE.match(inner)
                        or _RE_PARTITION.match(inner)
                    ):
                        break
                    expr_lines.append(inner.rstrip())
                    i += 1
                meas["expression"] = "\n".join(expr_lines).strip()
                i, meas = self._collect_measure_properties(lines, i, meas)
                measures.append(meas)
                continue
            i += 1
        return measures

    @staticmethod
    def _blank_measure(table_name: str, raw_name: str) -> Dict[str, Any]:
        return {
            "table_name": table_name,
            "name": _strip_quotes(raw_name),
            "expression": "",
            "format_string": None,
            "display_folder": None,
            "lineage_tag": None,
            "description": None,
            "annotations": {},
        }

    @staticmethod
    def _collect_measure_properties(
        lines: List[str], start: int, meas: Dict[str, Any]
    ) -> Tuple[int, Dict[str, Any]]:
        """Scan post-expression lines for measure property attributes."""
        i = start
        while i < len(lines):
            inner = lines[i]
            # Stop at non-indented lines or new sibling declarations
            if inner and inner[0] != "\t":
                break
            if (
                _RE_COLUMN_HEADER.match(inner)
                or _RE_MEASURE_SINGLE.match(inner)
                or _RE_MEASURE_MULTILINE.match(inner)
                or _RE_PARTITION.match(inner)
            ):
                break
            if m := _RE_FORMAT_STRING.match(inner):
                meas["format_string"] = _strip_quotes(m.group(1).strip())
            elif m := _RE_DISPLAY_FOLDER.match(inner):
                meas["display_folder"] = m.group(1).strip()
            elif m := _RE_LINEAGE_TAG.match(inner):
                if not meas["lineage_tag"]:
                    meas["lineage_tag"] = m.group(1).strip()
            elif m := _RE_ANNOTATION.match(inner):
                key, val = m.group(1).strip(), _parse_annotation_value(m.group(2))
                meas["annotations"][key] = val
                if key == "Description" and not meas["description"]:
                    meas["description"] = val
            i += 1
        return i, meas


# ---------------------------------------------------------------------------
# TMDL relationships parser
# ---------------------------------------------------------------------------


class TmdlRelParser:
    """Parses the ``relationships.tmdl`` file.

    Parameters
    ----------
    path:
        Absolute path to ``relationships.tmdl``.
    """

    def __init__(self, path: Path) -> None:
        self.path = path

    def parse(self) -> List[Dict[str, Any]]:
        """Return a list of raw relationship dicts."""
        try:
            text = self.path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.error("Cannot read relationships file %s: %s", self.path, exc)
            return []

        relationships: List[Dict[str, Any]] = []
        current: Optional[Dict[str, Any]] = None

        for line in text.splitlines():
            if m := _RE_REL_HEADER.match(line):
                if current:
                    relationships.append(current)
                current = {
                    "relationship_id": m.group(1).strip(),
                    "from_table": "",
                    "from_column": "",
                    "to_table": "",
                    "to_column": "",
                    "is_active": True,
                    "cross_filter_behavior": "singleDirection",
                    "from_cardinality": None,
                    "to_cardinality": None,
                    "join_on_date_behavior": None,
                }
                continue
            if current is None:
                continue
            if m := _RE_FROM_COL.match(line):
                current["from_table"] = m.group(1)
                current["from_column"] = m.group(2)
            elif m := _RE_TO_COL.match(line):
                current["to_table"] = m.group(1)
                current["to_column"] = m.group(2)
            elif m := _RE_IS_ACTIVE.match(line):
                current["is_active"] = m.group(1).lower() == "true"
            elif m := _RE_CROSS_FILTER.match(line):
                current["cross_filter_behavior"] = m.group(1).strip()
            elif m := _RE_FROM_CARDINALITY.match(line):
                current["from_cardinality"] = m.group(1).strip()
            elif m := _RE_TO_CARDINALITY.match(line):
                current["to_cardinality"] = m.group(1).strip()
            elif m := _RE_JOIN_DATE.match(line):
                current["join_on_date_behavior"] = m.group(1).strip()

        if current:
            relationships.append(current)

        logger.debug("Parsed %d relationships", len(relationships))
        return relationships


# ---------------------------------------------------------------------------
# PBIR page parser
# ---------------------------------------------------------------------------


class PbirPageParser:
    """Parses a single PBIR report page directory.

    A page directory contains:
    * ``page.json``   — page metadata
    * ``visuals/``    — one sub-directory per visual, each with ``visual.json``

    Parameters
    ----------
    page_dir:
        Path to the page directory (e.g. ``sales.Report/definition/pages/a4150484ffaf9ff89014``).
    """

    def __init__(self, page_dir: Path) -> None:
        self.page_dir = page_dir

    def parse(self) -> Dict[str, Any]:
        """Parse the page and all its visuals; return raw dict."""
        page_json_path = self.page_dir / "page.json"
        if not page_json_path.exists():
            logger.warning("page.json missing in %s", self.page_dir)
            return {}

        try:
            page_data: Dict[str, Any] = json.loads(page_json_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.error("Cannot parse %s: %s", page_json_path, exc)
            return {}

        result: Dict[str, Any] = {
            "page_name": page_data.get("name", self.page_dir.name),
            "display_name": page_data.get("displayName", ""),
            "display_option": page_data.get("displayOption"),
            "height": page_data.get("height"),
            "width": page_data.get("width"),
            "visuals": [],
        }

        visuals_dir = self.page_dir / "visuals"
        if visuals_dir.is_dir():
            for visual_dir in sorted(visuals_dir.iterdir()):
                if not visual_dir.is_dir():
                    continue
                visual_data = self._parse_visual(visual_dir)
                if visual_data:
                    result["visuals"].append(visual_data)

        logger.debug(
            "Parsed page '%s' with %d visuals",
            result["display_name"],
            len(result["visuals"]),
        )
        return result

    def _parse_visual(self, visual_dir: Path) -> Optional[Dict[str, Any]]:
        """Parse a single visual container directory."""
        visual_json = visual_dir / "visual.json"
        if not visual_json.exists():
            logger.debug("No visual.json in %s, skipping", visual_dir)
            return None
        try:
            data: Dict[str, Any] = json.loads(visual_json.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Cannot parse %s: %s", visual_json, exc)
            return None

        visual_block = data.get("visual", {})
        fields = self._extract_fields(visual_block)

        return {
            "visual_name": data.get("name", visual_dir.name),
            "visual_type": visual_block.get("visualType", "unknown"),
            "position": data.get("position", {}),
            "fields": fields,
        }

    @staticmethod
    def _extract_fields(visual_block: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Walk the visual's queryState to extract all bound field references."""
        fields: List[Dict[str, Any]] = []
        query_state = (
            visual_block.get("query", {}).get("queryState", {})
        )
        for _role, role_data in query_state.items():
            for proj in role_data.get("projections", []):
                field = proj.get("field", {})
                query_ref = proj.get("queryRef", "")

                # Column reference
                if "Column" in field:
                    col = field["Column"]
                    entity = col.get("Expression", {}).get("SourceRef", {}).get("Entity", "")
                    prop = col.get("Property", "")
                    fields.append(
                        {
                            "entity": entity,
                            "property_name": prop,
                            "query_ref": query_ref,
                            "field_type": "column",
                        }
                    )
                # Measure reference
                elif "Measure" in field:
                    meas = field["Measure"]
                    entity = meas.get("Expression", {}).get("SourceRef", {}).get("Entity", "")
                    prop = meas.get("Property", "")
                    fields.append(
                        {
                            "entity": entity,
                            "property_name": prop,
                            "query_ref": query_ref,
                            "field_type": "measure",
                        }
                    )
        return fields


# ---------------------------------------------------------------------------
# PBIR pages manifest parser
# ---------------------------------------------------------------------------


class PbirPagesParser:
    """Discovers all report pages from ``pages.json`` and delegates
    each page directory to :class:`PbirPageParser`.

    Parameters
    ----------
    report_dir:
        Root report directory, e.g. ``sales.Report/``.
    """

    def __init__(self, report_dir: Path) -> None:
        self.report_dir = report_dir
        self.pages_json = report_dir / "definition" / "pages" / "pages.json"

    def parse(self) -> List[Dict[str, Any]]:
        """Return a list of raw page dicts in page order."""
        if not self.pages_json.exists():
            logger.error("pages.json not found at %s", self.pages_json)
            return []
        try:
            manifest: Dict[str, Any] = json.loads(self.pages_json.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.error("Cannot parse pages.json: %s", exc)
            return []

        page_order: List[str] = manifest.get("pageOrder", [])
        pages_dir = self.pages_json.parent
        pages: List[Dict[str, Any]] = []

        for page_id in page_order:
            page_dir = pages_dir / page_id
            if not page_dir.is_dir():
                logger.warning("Page directory not found: %s", page_dir)
                continue
            page_data = PbirPageParser(page_dir).parse()
            if page_data:
                pages.append(page_data)

        logger.info("Discovered %d report pages", len(pages))
        return pages
