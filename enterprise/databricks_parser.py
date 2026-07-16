"""
enterprise.databricks_parser
============================
Databricks Notebook Parser — source-code inspection for lineage discovery.

This parser does **NOT** execute notebooks.  It inspects notebook source code
and metadata descriptors to build :class:`~graph.models.Asset` nodes and
:class:`~graph.models.Relationship` edges into an
:class:`~graph.enterprise_graph.EnterpriseGraph`.

Input model
-----------
Each notebook is described by a *descriptor* dict:

.. code-block:: python

    {
        # ── Identity ──────────────────────────────────────────────────────
        "path":             "/Repos/de-team/01_bronze_ingestion_framework",
        "name":             "01_bronze_ingestion_framework",   # optional; derived from path
        "workspace_folder": "/Repos/de-team",                  # optional; derived from path
        "language":         "python",                          # "python" | "sql" | "r" | "scala"
        "owner":            "de-team",                         # optional

        # ── Source code ───────────────────────────────────────────────────
        # Provide either a flat source string or a Jupyter-style cell list.
        "source":  "spark.read.table('bronze.customer') ...",  # flat source string
        "cells": [                                             # Jupyter .ipynb cells
            {"cell_type": "code", "source": "df = spark.read.table('bronze.customer')"},
            {"cell_type": "markdown", "source": "## Notes"},
        ],

        # ── Optional enrichment ───────────────────────────────────────────
        "criticality": "high",
        "tags":        ["bronze", "ingestion"],
    }

At least one of ``source`` or ``cells`` must be present; the parser will fall
back to an empty graph if neither is found.

Lineage extraction
------------------
The following patterns are detected in the concatenated cell source:

READS (notebook consumes data from a table):
    ``spark.read.table("catalog.schema.table")``
    ``spark.table("catalog.schema.table")``
    ``SELECT … FROM catalog.schema.table``
    ``DeltaTable.forName(spark, "catalog.schema.table")``
    ``DeltaTable.forPath(spark, "/path/to/delta")``

WRITES (notebook produces or updates a table):
    ``df.write.saveAsTable("catalog.schema.table")``
    ``df.insertInto("catalog.schema.table")``
    ``df.write.table("catalog.schema.table")``
    ``CREATE TABLE catalog.schema.table``
    ``CREATE OR REPLACE TABLE catalog.schema.table``
    ``MERGE INTO catalog.schema.table``

For every table reference discovered, the parser creates:
    - A **table stub** asset (``AssetType.DELTA_TABLE``, ``system=DATABRICKS``)
      if the reference cannot be resolved to an existing asset.
    - A directed :class:`~graph.models.Relationship`:
        - notebook ──READS──> source_table
        - notebook ──WRITES──> target_table

Execution order
---------------
Notebooks whose ``name`` (or the filename component of ``path``) begins with
a leading numeric prefix such as ``01_``, ``02_``, ``1_``, ``10_`` etc. are
assigned an ``execution_order`` integer stored in ``Asset.metadata``.  The
parser does **not** attempt to infer order from notebooks without such
prefixes — their ``execution_order`` is ``None``.

Fault tolerance
---------------
Every descriptor is processed inside a ``try/except``.  A malformed descriptor
causes a ``WARNING`` log entry and is skipped; it never raises an exception to
the caller.  An empty or missing source string produces a notebook asset with
no lineage edges.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Tuple

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
# Compiled extraction patterns
# ---------------------------------------------------------------------------

# ── READ patterns ──────────────────────────────────────────────────────────

# spark.read.table("catalog.schema.table")  or  spark.read.table('...')
_RE_SPARK_READ_TABLE = re.compile(
    r"""spark\.read\s*(?:\.\w+\([^)]*\))*\s*\.table\s*\(\s*['"](?P<ref>[^'"]+)['"]\s*\)""",
    re.IGNORECASE | re.DOTALL,
)

# spark.table("catalog.schema.table")
_RE_SPARK_TABLE = re.compile(
    r"""spark\.table\s*\(\s*['"](?P<ref>[^'"]+)['"]\s*\)""",
    re.IGNORECASE,
)

# SELECT ... FROM [catalog.][schema.]table  (stops at whitespace / semicolon / EOL)
# Handles: FROM table, FROM schema.table, FROM catalog.schema.table
# Does NOT follow JOINs (those produce separate FROM matches via multi-pass)
_RE_SQL_FROM = re.compile(
    r"""(?:^|[\s;])FROM\s+(?P<ref>[\w][\w.]*[\w])""",
    re.IGNORECASE | re.MULTILINE,
)

# JOIN [INNER|LEFT|RIGHT|FULL] ... ON → also a READ
_RE_SQL_JOIN = re.compile(
    r"""\bJOIN\s+(?P<ref>[\w][\w.]*[\w])""",
    re.IGNORECASE,
)

# DeltaTable.forName(spark, "catalog.schema.table")
_RE_DELTA_FOR_NAME = re.compile(
    r"""DeltaTable\.forName\s*\([^,)]*,\s*['"](?P<ref>[^'"]+)['"]\s*\)""",
    re.IGNORECASE,
)

# DeltaTable.forPath(spark, "/path/to/table")
_RE_DELTA_FOR_PATH = re.compile(
    r"""DeltaTable\.forPath\s*\([^,)]*,\s*['"](?P<ref>[^'"]+)['"]\s*\)""",
    re.IGNORECASE,
)

# ── WRITE patterns ─────────────────────────────────────────────────────────

# df.write.saveAsTable("catalog.schema.table")
_RE_SAVE_AS_TABLE = re.compile(
    r"""\.saveAsTable\s*\(\s*['"](?P<ref>[^'"]+)['"]\s*\)""",
    re.IGNORECASE,
)

# df.insertInto("catalog.schema.table")
_RE_INSERT_INTO = re.compile(
    r"""\.insertInto\s*\(\s*['"](?P<ref>[^'"]+)['"]\s*\)""",
    re.IGNORECASE,
)

# df.write.table("catalog.schema.table")   — note: NOT saveAsTable, NOT .read.table
# Use negative lookbehind for "read" to avoid matching spark.read.table
_RE_WRITE_TABLE = re.compile(
    r"""(?<!read)\.write\s*(?:\.\w+(?:\([^)]*\))?)?\s*\.table\s*\(\s*['"](?P<ref>[^'"]+)['"]\s*\)""",
    re.IGNORECASE,
)

# CREATE [OR REPLACE] TABLE [IF NOT EXISTS] [catalog.][schema.]table
_RE_CREATE_TABLE = re.compile(
    r"""\bCREATE\s+(?:OR\s+REPLACE\s+)?TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(?P<ref>[\w][\w.]*[\w])""",
    re.IGNORECASE,
)

# MERGE INTO [catalog.][schema.]table
_RE_MERGE_INTO = re.compile(
    r"""\bMERGE\s+INTO\s+(?P<ref>[\w][\w.]*[\w])""",
    re.IGNORECASE,
)

# INSERT INTO / INSERT OVERWRITE [catalog.][schema.]table
_RE_SQL_INSERT = re.compile(
    r"""\bINSERT\s+(?:INTO|OVERWRITE)\s+(?:TABLE\s+)?(?P<ref>[\w][\w.]*[\w])""",
    re.IGNORECASE,
)

# MERGE INTO ... USING <source_table> [AS alias] ON ...
# The source table in USING is a READ reference (the notebook reads from it).
_RE_MERGE_USING = re.compile(
    r"""\bUSING\s+(?P<ref>[\w][\w.]*[\w])\s+(?:AS\s+\w+\s+)?ON\b""",
    re.IGNORECASE,
)

# ── Execution-order prefix ─────────────────────────────────────────────────
# Matches: "01_", "1_", "001_", "10_" at the start of the basename
_RE_EXEC_ORDER = re.compile(r"^(?P<order>\d+)_")

# ── SQL keywords to exclude from table references ─────────────────────────
# These words appear after FROM/JOIN in SQL syntax but are not table names.
# Python import keywords are included so that "from pyspark.sql import SparkSession"
# does not match the SQL FROM pattern and produce a spurious "pyspark.sql" read ref.
_SQL_KEYWORDS: frozenset[str] = frozenset({
    "select", "where", "join", "inner", "left", "right", "outer", "full",
    "cross", "on", "group", "order", "having", "limit", "union", "except",
    "intersect", "with", "as", "values", "set", "using", "lateral", "tablesample",
    "unnest", "pivot", "unpivot", "qualify",
    # Python import guard: "from pyspark.sql import" would match _RE_SQL_FROM without this
    "import",
})

# ── Python import-line stripper ────────────────────────────────────────────
# Used to blank out Python import lines before SQL pattern matching so that
# "from pyspark.sql import SparkSession" cannot produce a "pyspark.sql" read ref.
_RE_PYTHON_IMPORT_LINE = re.compile(
    r"^[ \t]*(?:from\s+[\w.]+\s+import\b|import\s+[\w, .]+).*$",
    re.IGNORECASE | re.MULTILINE,
)


# ---------------------------------------------------------------------------
# Helper: extract all table references from a source block
# ---------------------------------------------------------------------------

def _extract_reads(source: str) -> List[str]:
    """Return deduplicated list of table references read in *source*.

    Args:
        source: Concatenated notebook cell source code.

    Returns:
        List of normalised table reference strings, deduplicated,
        preserving first-occurrence order.
    """
    refs: list[str] = []
    seen: set[str] = set()

    def _add(ref: str) -> None:
        ref = ref.strip().strip("`").strip('"').strip("'")
        if ref and ref.lower() not in _SQL_KEYWORDS and ref not in seen:
            seen.add(ref)
            refs.append(ref)

    # Blank out Python import lines before running SQL FROM matching to avoid
    # "from pyspark.sql import SparkSession" → "pyspark.sql" false positive.
    sql_safe = _RE_PYTHON_IMPORT_LINE.sub("", source)

    for m in _RE_SPARK_READ_TABLE.finditer(source):
        _add(m.group("ref"))
    for m in _RE_SPARK_TABLE.finditer(source):
        _add(m.group("ref"))
    for m in _RE_SQL_FROM.finditer(sql_safe):
        _add(m.group("ref"))
    for m in _RE_SQL_JOIN.finditer(sql_safe):
        _add(m.group("ref"))
    for m in _RE_DELTA_FOR_NAME.finditer(source):
        _add(m.group("ref"))
    for m in _RE_DELTA_FOR_PATH.finditer(source):
        _add(m.group("ref"))
    for m in _RE_MERGE_USING.finditer(source):
        _add(m.group("ref"))

    return refs


def _extract_writes(source: str) -> List[str]:
    """Return deduplicated list of table references written in *source*.

    Args:
        source: Concatenated notebook cell source code.

    Returns:
        List of normalised table reference strings, deduplicated,
        preserving first-occurrence order.
    """
    refs: list[str] = []
    seen: set[str] = set()

    def _add(ref: str) -> None:
        ref = ref.strip().strip("`").strip('"').strip("'")
        if ref and ref.lower() not in _SQL_KEYWORDS and ref not in seen:
            seen.add(ref)
            refs.append(ref)

    for m in _RE_SAVE_AS_TABLE.finditer(source):
        _add(m.group("ref"))
    for m in _RE_INSERT_INTO.finditer(source):
        _add(m.group("ref"))
    for m in _RE_WRITE_TABLE.finditer(source):
        _add(m.group("ref"))
    for m in _RE_CREATE_TABLE.finditer(source):
        _add(m.group("ref"))
    for m in _RE_MERGE_INTO.finditer(source):
        _add(m.group("ref"))
    for m in _RE_SQL_INSERT.finditer(source):
        _add(m.group("ref"))

    return refs


def _extract_source(descriptor: Dict[str, Any]) -> str:
    """Concatenate all code cell sources from *descriptor* into one string.

    Handles two input shapes:

    1. ``descriptor["source"]`` — a pre-joined flat string.
    2. ``descriptor["cells"]`` — a Jupyter-style list of cell dicts.

    Markdown / raw cells are excluded from lineage extraction.

    Args:
        descriptor: A single notebook descriptor dict.

    Returns:
        A single string containing all code cell sources, separated by
        newlines.  Returns ``""`` if neither key is present.
    """
    if "source" in descriptor:
        return str(descriptor["source"])

    cells = descriptor.get("cells", [])
    code_parts: list[str] = []
    for cell in cells:
        if not isinstance(cell, dict):
            continue
        cell_type = str(cell.get("cell_type", "code")).lower()
        if cell_type in ("code",):
            src = cell.get("source", "")
            if isinstance(src, list):
                # Jupyter stores cell source as a list of lines
                src = "".join(src)
            code_parts.append(str(src))
    return "\n".join(code_parts)


def _infer_name(descriptor: Dict[str, Any]) -> str:
    """Derive a human-readable notebook name from *descriptor*.

    Preference order:
        1. ``descriptor["name"]`` if non-empty.
        2. Filename component of ``descriptor["path"]``.
        3. ``"unknown_notebook"``.

    Args:
        descriptor: A single notebook descriptor dict.

    Returns:
        A non-empty string notebook name.
    """
    name = descriptor.get("name", "")
    if name:
        return str(name)
    path = descriptor.get("path", "")
    if path:
        # Take last path segment, strip common extensions
        basename = path.rstrip("/").rsplit("/", 1)[-1]
        basename = re.sub(r"\.(py|scala|r|sql|ipynb)$", "", basename, flags=re.IGNORECASE)
        if basename:
            return basename
    return "unknown_notebook"


def _infer_workspace_folder(descriptor: Dict[str, Any]) -> Optional[str]:
    """Derive workspace folder from *descriptor*.

    Args:
        descriptor: A single notebook descriptor dict.

    Returns:
        Workspace folder string, or ``None`` if not determinable.
    """
    folder = descriptor.get("workspace_folder", "")
    if folder:
        return str(folder)
    path = descriptor.get("path", "")
    if path and "/" in path:
        return path.rstrip("/").rsplit("/", 1)[0] or "/"
    return None


def _infer_execution_order(name: str) -> Optional[int]:
    """Parse a leading numeric prefix from *name* to infer execution order.

    Examples:
        ``"01_bronze_ingestion"`` → ``1``
        ``"2_silver_transform"``  → ``2``
        ``"10_gold_publish"``     → ``10``
        ``"adhoc_analysis"``      → ``None``

    Args:
        name: The notebook name (basename only, no directory prefix).

    Returns:
        Integer execution order, or ``None`` if no prefix is found.
    """
    basename = name.rstrip("/").rsplit("/", 1)[-1]
    m = _RE_EXEC_ORDER.match(basename)
    if m:
        try:
            return int(m.group("order"))
        except ValueError:
            return None
    return None


def _table_asset_id(ref: str) -> str:
    """Return a canonical asset ID for a table reference string.

    Args:
        ref: A table reference string such as ``"bronze.customer"`` or
             ``"/delta/bronze/customer"``.

    Returns:
        A stable asset ID string prefixed with ``"delta_table::"``.
    """
    return f"delta_table::{ref}"


def _extract_catalog_schema_refs(reads: List[str], writes: List[str]) -> Tuple[List[str], List[str]]:
    """Extract unique catalog and schema names from a set of table references.

    Args:
        reads:  List of read-table reference strings.
        writes: List of write-table reference strings.

    Returns:
        Tuple of (catalog_refs, schema_refs), each a deduplicated list of
        strings in the format ``"catalog"`` or ``"catalog.schema"``.
    """
    catalogs: set[str] = set()
    schemas: set[str] = set()
    for ref in reads + writes:
        parts = ref.split(".")
        if len(parts) >= 3:
            catalogs.add(parts[0])
            schemas.add(f"{parts[0]}.{parts[1]}")
        elif len(parts) == 2:
            schemas.add(ref.rsplit(".", 1)[0])
    return sorted(catalogs), sorted(schemas)


# ---------------------------------------------------------------------------
# DatabricksNotebookParser
# ---------------------------------------------------------------------------


class DatabricksNotebookParser(BaseMetadataParser):
    """Parse Databricks notebook source code into EnterpriseGraph assets and edges.

    This parser accepts a list of *notebook descriptor* dicts, inspects each
    notebook's source code, extracts read/write table references using
    compiled regex patterns, and emits:

    * One :class:`~graph.models.Asset` per notebook
      (``AssetType.DATABRICKS_NOTEBOOK``, ``system=DATABRICKS``).
    * One stub :class:`~graph.models.Asset` per referenced table
      (``AssetType.DELTA_TABLE``, ``system=DATABRICKS``) when the table is
      not already present in the caller's graph.
    * ``notebook ──READS──> table`` edges for every read reference.
    * ``notebook ──WRITES──> table`` edges for every write reference.

    The parser never executes notebooks and never makes network calls.

    Extraction coverage:

    +---------------------------------+------------------+
    | Source pattern                  | Edge type        |
    +=================================+==================+
    | spark.read.table(...)           | READS            |
    | spark.table(...)                | READS            |
    | SELECT … FROM table             | READS            |
    | JOIN table                      | READS            |
    | DeltaTable.forName(spark, ...)  | READS            |
    | DeltaTable.forPath(spark, ...)  | READS            |
    | df.write.saveAsTable(...)       | WRITES           |
    | df.insertInto(...)              | WRITES           |
    | df.write.table(...)             | WRITES           |
    | CREATE [OR REPLACE] TABLE ...   | WRITES           |
    | MERGE INTO ...                  | WRITES           |
    | INSERT INTO / OVERWRITE ...     | WRITES           |
    +---------------------------------+------------------+

    Parameters:
        descriptors: List of notebook descriptor dicts (see module docstring).
        owner: Default owner for all produced assets; overridden by
               per-descriptor ``owner`` field.
        default_criticality: Default :class:`~graph.models.Criticality` for
                             produced assets.
        emit_table_stubs: When ``True`` (default), emit a stub
                          ``DELTA_TABLE`` asset for every table reference
                          discovered.  Set to ``False`` if the caller manages
                          table assets separately and only wants the notebook
                          nodes and relationship edges.
    """

    def __init__(
        self,
        descriptors: List[Dict[str, Any]],
        owner: Optional[str] = None,
        default_criticality: Criticality = Criticality.MEDIUM,
        emit_table_stubs: bool = True,
    ) -> None:
        """Initialise the parser.

        Args:
            descriptors: List of notebook descriptor dicts.
            owner: Default owner tag.
            default_criticality: Default criticality level.
            emit_table_stubs: Emit stub table assets for every reference.
        """
        super().__init__(
            source_name="databricks_notebook_parser",
            owner=owner,
            default_criticality=default_criticality,
        )
        self._descriptors = descriptors
        self._emit_table_stubs = emit_table_stubs

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def parse(self) -> ParseResult:
        """Parse all notebook descriptors and return assets and relationships.

        Returns:
            Tuple of ``(assets, relationships)`` where ``assets`` is a list of
            :class:`~graph.models.Asset` objects and ``relationships`` is a
            list of :class:`~graph.models.Relationship` objects.  Both lists
            may be empty.  Never raises.
        """
        assets: List[Asset] = []
        relationships: List[Relationship] = []
        # Track table stub IDs already emitted to avoid duplicates across notebooks
        emitted_table_ids: set[str] = set()

        for desc in self._descriptors:
            try:
                nb_assets, nb_rels = self._parse_one(desc, emitted_table_ids)
                assets.extend(nb_assets)
                relationships.extend(nb_rels)
            except Exception as exc:  # pylint: disable=broad-except
                try:
                    label = (
                        desc.get("name") or desc.get("path") or "<unknown>"
                        if isinstance(desc, dict) else repr(desc)
                    )
                except Exception:  # pragma: no cover
                    label = "<unknown>"
                logger.warning(
                    "DatabricksNotebookParser: failed to parse descriptor %r — %s",
                    label,
                    exc,
                    exc_info=False,
                )

        logger.info(
            "DatabricksNotebookParser: produced %d assets, %d relationships from %d descriptors",
            len(assets),
            len(relationships),
            len(self._descriptors),
        )
        return assets, relationships

    # ------------------------------------------------------------------
    # Internal: per-notebook parse
    # ------------------------------------------------------------------

    def _parse_one(
        self,
        desc: Dict[str, Any],
        emitted_table_ids: set[str],
    ) -> ParseResult:
        """Parse a single notebook descriptor.

        Args:
            desc: Notebook descriptor dict.
            emitted_table_ids: Shared set of already-emitted table stub IDs
                               used to deduplicate stubs across notebooks.

        Returns:
            ``(assets, relationships)`` for this notebook only.
        """
        assets: List[Asset] = []
        relationships: List[Relationship] = []

        # ── Identity ──────────────────────────────────────────────────────
        name = _infer_name(desc)
        path = str(desc.get("path", name))
        workspace_folder = _infer_workspace_folder(desc)
        language = str(desc.get("language", "python")).lower()
        execution_order = _infer_execution_order(name)

        # Criticality
        criticality_str = str(desc.get("criticality", "")).strip()
        try:
            crit = Criticality(criticality_str) if criticality_str else self.default_criticality
        except ValueError:
            crit = self.default_criticality

        # ── Source code extraction ─────────────────────────────────────────
        source = _extract_source(desc)

        # ── Lineage extraction ─────────────────────────────────────────────
        reads: List[str] = _extract_reads(source)
        writes: List[str] = _extract_writes(source)

        # Remove write targets from the read list only when they are identical
        # references (a notebook that MERGES INTO a table it also reads is both
        # a reader and a writer — keep both edges).
        # No deduplication between reads and writes is performed here; the graph
        # allows both READS and WRITES edges between the same pair.

        # ── Catalog / schema references ────────────────────────────────────
        catalog_refs, schema_refs = _extract_catalog_schema_refs(reads, writes)

        # ── Notebook asset ─────────────────────────────────────────────────
        notebook_id = f"notebook::{path}"
        notebook_asset = self._make_asset(
            id=notebook_id,
            name=name,
            asset_type=AssetType.DATABRICKS_NOTEBOOK,
            system=SystemType.DATABRICKS,
            owner=desc.get("owner"),
            criticality=crit,
            tags=list(desc.get("tags", [])),
            source_file=path,
            metadata={
                "path":             path,
                "workspace_folder": workspace_folder,
                "language":         language,
                "execution_order":  execution_order,
                "catalog_refs":     catalog_refs,
                "schema_refs":      schema_refs,
                "read_count":       len(reads),
                "write_count":      len(writes),
            },
        )
        assets.append(notebook_asset)

        # ── Table stub assets + READS edges ───────────────────────────────
        for ref in reads:
            table_id = _table_asset_id(ref)
            if self._emit_table_stubs and table_id not in emitted_table_ids:
                table_asset = self._make_table_stub(ref, table_id)
                assets.append(table_asset)
                emitted_table_ids.add(table_id)
            relationships.append(Relationship(
                source=notebook_id,
                target=table_id,
                relationship=RelationshipType.READS,
                properties={"unresolved": True, "raw_ref": ref},
            ))

        # ── Table stub assets + WRITES edges ──────────────────────────────
        for ref in writes:
            table_id = _table_asset_id(ref)
            if self._emit_table_stubs and table_id not in emitted_table_ids:
                table_asset = self._make_table_stub(ref, table_id)
                assets.append(table_asset)
                emitted_table_ids.add(table_id)
            relationships.append(Relationship(
                source=notebook_id,
                target=table_id,
                relationship=RelationshipType.WRITES,
                properties={"unresolved": True, "raw_ref": ref},
            ))

        logger.debug(
            "DatabricksNotebookParser: notebook %r → %d reads, %d writes",
            name, len(reads), len(writes),
        )
        return assets, relationships

    def _make_table_stub(self, ref: str, table_id: str) -> Asset:
        """Create a stub DELTA_TABLE asset for a discovered table reference.

        Args:
            ref:      The raw table reference string (e.g. ``"bronze.customer"``).
            table_id: The canonical asset ID (e.g. ``"delta_table::bronze.customer"``).

        Returns:
            A :class:`~graph.models.Asset` with ``asset_type=DELTA_TABLE``.
        """
        parts = ref.split(".")
        if len(parts) >= 3:
            catalog = parts[0]
            schema = parts[1]
            table_name = ".".join(parts[2:])
        elif len(parts) == 2:
            catalog = None
            schema = parts[0]
            table_name = parts[1]
        else:
            catalog = None
            schema = None
            table_name = ref

        return self._make_asset(
            id=table_id,
            name=table_name,
            asset_type=AssetType.DELTA_TABLE,
            system=SystemType.DATABRICKS,
            catalog=catalog,
            schema=schema,
            metadata={"raw_ref": ref, "stub": True},
        )
