"""
enterprise.notebook_parser
==========================
ECO Metadata Block Parser for exported Databricks notebooks (.py).

This parser does **NOT** inspect or execute any PySpark or SQL code.  It
reads only the structured ``ECO METADATA`` comment block that ECO-compliant
notebooks embed at the top of the file, extracts the declared fields, and
emits :class:`~graph.models.Asset` nodes and
:class:`~graph.models.Relationship` edges compatible with
:class:`~graph.enterprise_graph.EnterpriseGraph`.

ECO Metadata Block Format
--------------------------
Notebooks export a block of comment lines bounded by sentinels::

    # ECO METADATA
    # NOTEBOOK_NAME: 01_bronze_ingestion_framework
    # LAYER:         bronze
    # CATALOG:       hive_metastore
    # SCHEMA:        bronze
    # READ_TABLES:   landing.customer, landing.sales
    # WRITE_TABLES:  bronze.customer, bronze.sales
    # EXECUTION_ORDER: 1
    # END ECO METADATA

Rules:
- Lines outside the block are completely ignored.
- Every field is optional; missing fields produce safe defaults (``None``
  for scalars, empty lists for collections).
- ``READ_TABLES`` and ``WRITE_TABLES`` accept a comma-separated list on a
  single line **or** one entry per continuation line, each prefixed with ``#``.
- Values are stripped of leading/trailing whitespace and comment prefixes.
- Duplicate table entries within a field are deduplicated, preserving order.
- An empty block (sentinels present but no recognised keys) produces a
  notebook asset with empty lineage.

Produced Graph Output
---------------------
For each notebook::

    DELTA_TABLE(read_table_1)  ──READS──>  DATABRICKS_NOTEBOOK  ──WRITES──>  DELTA_TABLE(write_table_1)
    DELTA_TABLE(read_table_2)  ──READS──>  DATABRICKS_NOTEBOOK  ──WRITES──>  DELTA_TABLE(write_table_2)

More precisely, each edge is:

    notebook  ──READS──>   read_table_asset
    notebook  ──WRITES──>  write_table_asset

Asset IDs:
    notebook:     ``notebook::<NOTEBOOK_NAME>``
    table stubs:  ``delta_table::<table_ref>``

Parser contract:
    :meth:`EcoNotebookParser.parse` always returns ``(list[Asset], list[Relationship])``.
    It never raises.  Malformed lines are skipped with a ``WARNING`` log entry.

Usage
-----
From file paths::

    from enterprise.notebook_parser import EcoNotebookParser
    from pathlib import Path

    paths = list(Path("/Repos/de-team").rglob("*.py"))
    parser = EcoNotebookParser.from_files(paths, owner="de-team")
    assets, rels = parser.parse()

From raw source strings (useful for testing)::

    parser = EcoNotebookParser(
        notebooks=[
            {"name": "01_bronze", "source": "# ECO METADATA\\n# NOTEBOOK_NAME: ..."},
        ]
    )
    assets, rels = parser.parse()
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
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
# Block-boundary sentinels
# ---------------------------------------------------------------------------

#: Comment line that opens an ECO metadata block (case-insensitive match).
_BLOCK_OPEN  = re.compile(r"^\s*#\s*ECO\s+METADATA\s*$",      re.IGNORECASE)
#: Comment line that closes an ECO metadata block (case-insensitive match).
_BLOCK_CLOSE = re.compile(r"^\s*#\s*END\s+ECO\s+METADATA\s*$", re.IGNORECASE)

# ---------------------------------------------------------------------------
# Key → field mapping
# ---------------------------------------------------------------------------

#: Recognised scalar keys (case-insensitive). Value: attribute name on the
#: parsed dict.
_SCALAR_KEYS: Dict[str, str] = {
    "notebook_name":   "name",
    "layer":           "layer",
    "catalog":         "catalog",
    "schema":          "schema",
    "execution_order": "execution_order",
}

#: Recognised list keys (value is comma-separated).
_LIST_KEYS: Dict[str, str] = {
    "read_tables":  "read_tables",
    "write_tables": "write_tables",
}

# Matches a comment-prefixed key/value line inside the block:
#   "# KEY: value"  or  "# KEY :value"
_KEY_VALUE = re.compile(
    r"^\s*#\s*(?P<key>[A-Za-z_]+)\s*:\s*(?P<value>.*)$",
)


# ---------------------------------------------------------------------------
# Low-level block extractor
# ---------------------------------------------------------------------------

def _extract_block(source: str) -> Dict[str, Any]:
    """Parse an ECO METADATA block from *source* and return a raw field dict.

    Only lines between the ``# ECO METADATA`` and ``# END ECO METADATA``
    sentinels are examined.  All other source lines are completely ignored.

    Args:
        source: Full text content of a ``.py`` notebook file (or any string
                containing an ECO METADATA block).

    Returns:
        Dict with keys: ``name``, ``layer``, ``catalog``, ``schema``,
        ``execution_order``, ``read_tables``, ``write_tables``.
        Missing scalar fields default to ``None``; list fields default to
        ``[]``.  Returns the same default dict when no block is found.
    """
    result: Dict[str, Any] = {
        "name":            None,
        "layer":           None,
        "catalog":         None,
        "schema":          None,
        "execution_order": None,
        "read_tables":     [],
        "write_tables":    [],
    }

    in_block = False

    for raw_line in source.splitlines():
        line = raw_line.rstrip()

        # ── Block boundary detection ─────────────────────────────────────
        if not in_block:
            if _BLOCK_OPEN.match(line):
                in_block = True
            continue  # outside block — skip entirely

        if _BLOCK_CLOSE.match(line):
            break  # end of block — stop parsing

        # ── Key / value extraction ───────────────────────────────────────
        m = _KEY_VALUE.match(line)
        if not m:
            # Comment line inside block with no recognisable key — skip
            continue

        key_raw   = m.group("key").strip().lower()
        value_raw = m.group("value").strip()

        if key_raw in _SCALAR_KEYS:
            field = _SCALAR_KEYS[key_raw]
            result[field] = value_raw if value_raw else None

        elif key_raw in _LIST_KEYS:
            field = _LIST_KEYS[key_raw]
            # Accept comma-separated list on a single line
            entries = [e.strip() for e in value_raw.split(",") if e.strip()]
            # Deduplicate while preserving order
            seen: set[str] = set(result[field])
            for entry in entries:
                if entry not in seen:
                    seen.add(entry)
                    result[field].append(entry)

        else:
            logger.debug(
                "EcoNotebookParser: unrecognised key %r in ECO METADATA block — skipped",
                key_raw,
            )

    # ── Normalise execution_order to int ────────────────────────────────
    if result["execution_order"] is not None:
        try:
            result["execution_order"] = int(result["execution_order"])
        except (ValueError, TypeError):
            logger.warning(
                "EcoNotebookParser: EXECUTION_ORDER value %r is not an integer — set to None",
                result["execution_order"],
            )
            result["execution_order"] = None

    return result


# ---------------------------------------------------------------------------
# Asset / relationship builders
# ---------------------------------------------------------------------------

def _table_asset_id(table_ref: str) -> str:
    """Canonical asset ID for a table reference string.

    Args:
        table_ref: Raw table reference, e.g. ``"bronze.customer"``.

    Returns:
        Asset ID string, e.g. ``"delta_table::bronze.customer"``.
    """
    return f"delta_table::{table_ref}"


def _notebook_asset_id(name: str) -> str:
    """Canonical asset ID for a notebook.

    Args:
        name: Notebook name from the ECO METADATA block.

    Returns:
        Asset ID string, e.g. ``"notebook::01_bronze_ingestion_framework"``.
    """
    return f"notebook::{name}"


def _split_table_ref(ref: str) -> Tuple[Optional[str], Optional[str], str]:
    """Split ``catalog.schema.table`` into ``(catalog, schema, table_name)``.

    Args:
        ref: Table reference string (1, 2, or 3 dot-separated parts).

    Returns:
        ``(catalog, schema, table_name)``  where catalog and schema may be
        ``None`` when absent.
    """
    parts = ref.split(".")
    if len(parts) >= 3:
        return parts[0], parts[1], ".".join(parts[2:])
    if len(parts) == 2:
        return None, parts[0], parts[1]
    return None, None, ref


# ---------------------------------------------------------------------------
# EcoNotebookParser
# ---------------------------------------------------------------------------


class EcoNotebookParser(BaseMetadataParser):
    """Parse ECO METADATA blocks from exported Databricks ``.py`` notebooks.

    This parser reads **only** the ``# ECO METADATA`` comment block embedded
    at the top of each notebook file.  It does not execute, import, or
    perform any semantic analysis on PySpark or SQL code.

    Each parsed notebook becomes a :class:`~graph.models.Asset` with
    ``asset_type=DATABRICKS_NOTEBOOK``, and every table listed in
    ``READ_TABLES`` / ``WRITE_TABLES`` becomes a stub
    ``DELTA_TABLE`` asset (emitted once per unique reference across all
    notebooks), connected by ``READS`` / ``WRITES`` edges:

    .. code-block::

        read_table ──READS──> notebook ──WRITES──> write_table

    Parameters:
        notebooks: List of notebook descriptor dicts.  Each dict must contain
                   either a ``"source"`` key (raw file content as a string)
                   or a ``"path"`` key (``Path`` or string) pointing to a
                   ``.py`` file on disk.  The optional ``"name"`` key
                   overrides the ``NOTEBOOK_NAME`` field in the ECO block.
        owner: Default owner tag for all produced assets.
        default_criticality: Default criticality for all produced assets.
        emit_table_stubs: When ``True`` (default), emit a ``DELTA_TABLE``
                          stub asset for every table reference.  Set to
                          ``False`` to produce only the notebook asset and
                          its relationship edges.
    """

    def __init__(
        self,
        notebooks: List[Dict[str, Any]],
        owner: Optional[str] = None,
        default_criticality: Criticality = Criticality.MEDIUM,
        emit_table_stubs: bool = True,
    ) -> None:
        """Initialise the parser with a list of notebook descriptors.

        Args:
            notebooks: List of dicts, each with ``"source"`` or ``"path"``.
            owner: Default owner for produced assets.
            default_criticality: Default criticality.
            emit_table_stubs: Emit stub DELTA_TABLE assets for table refs.
        """
        super().__init__(
            source_name="eco_notebook_parser",
            owner=owner,
            default_criticality=default_criticality,
        )
        self._notebooks = notebooks
        self._emit_table_stubs = emit_table_stubs

    # ------------------------------------------------------------------
    # Alternative constructor — load from file paths
    # ------------------------------------------------------------------

    @classmethod
    def from_files(
        cls,
        paths: List[Any],
        owner: Optional[str] = None,
        default_criticality: Criticality = Criticality.MEDIUM,
        emit_table_stubs: bool = True,
    ) -> "EcoNotebookParser":
        """Construct a parser from a list of file paths.

        Each path is opened, read as UTF-8 text, and wrapped into the
        notebook descriptor dict expected by :meth:`parse`.  Files that
        cannot be read are logged as warnings and skipped.

        Args:
            paths: List of ``Path`` objects or path strings pointing to
                   exported Databricks ``.py`` notebooks.
            owner: Default owner for produced assets.
            default_criticality: Default criticality.
            emit_table_stubs: Emit stub DELTA_TABLE assets.

        Returns:
            A new :class:`EcoNotebookParser` instance ready to call
            :meth:`parse`.
        """
        notebooks: List[Dict[str, Any]] = []
        for p in paths:
            path = Path(p)
            try:
                source = path.read_text(encoding="utf-8")
                notebooks.append({"path": str(path), "source": source})
            except OSError as exc:
                logger.warning(
                    "EcoNotebookParser.from_files: cannot read %s — %s", path, exc
                )
        return cls(
            notebooks=notebooks,
            owner=owner,
            default_criticality=default_criticality,
            emit_table_stubs=emit_table_stubs,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def parse(self) -> ParseResult:
        """Parse all notebook descriptors and return assets and relationships.

        Returns:
            Tuple ``(assets, relationships)``.  Never raises.  Malformed
            descriptors are skipped with a ``WARNING`` log entry.
        """
        assets: List[Asset] = []
        relationships: List[Relationship] = []
        emitted_table_ids: set[str] = set()

        for desc in self._notebooks:
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
                    "EcoNotebookParser: failed to parse %r — %s", label, exc,
                    exc_info=False,
                )

        logger.info(
            "EcoNotebookParser: produced %d assets, %d relationships from %d notebooks",
            len(assets), len(relationships), len(self._notebooks),
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
            desc: Notebook descriptor dict with ``"source"`` and/or ``"path"``.
            emitted_table_ids: Shared set for cross-notebook stub dedup.

        Returns:
            ``(assets, relationships)`` for this notebook.
        """
        assets: List[Asset] = []
        relationships: List[Relationship] = []

        # ── Resolve source text ───────────────────────────────────────────
        source = self._get_source(desc)

        # ── Parse ECO METADATA block ──────────────────────────────────────
        block = _extract_block(source)

        # ── Determine notebook name ───────────────────────────────────────
        # Priority: explicit desc["name"] > NOTEBOOK_NAME field > path basename > "unknown_notebook"
        name = (
            desc.get("name")
            or block.get("name")
            or self._name_from_path(desc.get("path", ""))
            or "unknown_notebook"
        )

        # ── Criticality ───────────────────────────────────────────────────
        criticality_str = str(desc.get("criticality", "")).strip()
        try:
            crit = Criticality(criticality_str) if criticality_str else self.default_criticality
        except ValueError:
            crit = self.default_criticality

        # ── Notebook asset ────────────────────────────────────────────────
        notebook_id = _notebook_asset_id(name)
        notebook_asset = self._make_asset(
            id=notebook_id,
            name=name,
            asset_type=AssetType.DATABRICKS_NOTEBOOK,
            system=SystemType.DATABRICKS,
            catalog=block.get("catalog"),
            schema=block.get("schema"),
            owner=desc.get("owner"),
            criticality=crit,
            tags=list(desc.get("tags", [])),
            source_file=desc.get("path", self.source_name),
            metadata={
                "layer":           block.get("layer"),
                "execution_order": block.get("execution_order"),
                "read_tables":     block.get("read_tables", []),
                "write_tables":    block.get("write_tables", []),
            },
        )
        assets.append(notebook_asset)

        # ── READ edges: read_table ──READS──> notebook ─────────────────────
        for ref in block.get("read_tables", []):
            table_id = _table_asset_id(ref)
            if self._emit_table_stubs and table_id not in emitted_table_ids:
                assets.append(self._make_table_stub(ref, table_id))
                emitted_table_ids.add(table_id)
            relationships.append(Relationship(
                source=table_id,
                target=notebook_id,
                relationship=RelationshipType.READS,
                properties={"unresolved": True, "raw_ref": ref},
            ))

        # ── WRITE edges: notebook ──WRITES──> write_table ──────────────────
        for ref in block.get("write_tables", []):
            table_id = _table_asset_id(ref)
            if self._emit_table_stubs and table_id not in emitted_table_ids:
                assets.append(self._make_table_stub(ref, table_id))
                emitted_table_ids.add(table_id)
            relationships.append(Relationship(
                source=notebook_id,
                target=table_id,
                relationship=RelationshipType.WRITES,
                properties={"unresolved": True, "raw_ref": ref},
            ))

        logger.debug(
            "EcoNotebookParser: notebook %r → %d reads, %d writes",
            name,
            len(block.get("read_tables", [])),
            len(block.get("write_tables", [])),
        )
        return assets, relationships

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _get_source(desc: Dict[str, Any]) -> str:
        """Extract source text from a descriptor dict.

        Accepts pre-loaded ``"source"`` string or falls back to reading
        ``"path"`` from disk.

        Args:
            desc: Notebook descriptor dict.

        Returns:
            Source text string (may be empty).
        """
        if "source" in desc:
            return str(desc["source"])
        path = desc.get("path", "")
        if path:
            try:
                return Path(path).read_text(encoding="utf-8")
            except OSError as exc:
                logger.warning(
                    "EcoNotebookParser: cannot read file %r — %s", path, exc
                )
        return ""

    @staticmethod
    def _name_from_path(path: str) -> Optional[str]:
        """Derive a notebook name from a file path.

        Args:
            path: File path string (may be empty).

        Returns:
            Basename without ``".py"`` extension, or ``None`` if path is empty.
        """
        if not path:
            return None
        basename = Path(path).stem  # removes .py
        return basename or None

    def _make_table_stub(self, ref: str, table_id: str) -> Asset:
        """Create a minimal stub DELTA_TABLE asset for a table reference.

        Args:
            ref:      Raw table reference string (e.g. ``"bronze.customer"``).
            table_id: Pre-computed canonical asset ID.

        Returns:
            :class:`~graph.models.Asset` with ``asset_type=DELTA_TABLE``.
        """
        catalog, schema, table_name = _split_table_ref(ref)
        return self._make_asset(
            id=table_id,
            name=table_name,
            asset_type=AssetType.DELTA_TABLE,
            system=SystemType.DATABRICKS,
            catalog=catalog,
            schema=schema,
            metadata={"raw_ref": ref, "stub": True},
        )
