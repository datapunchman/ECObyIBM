"""
enterprise.sql_parser
=====================
SQL DDL Metadata Parser.

This parser reads ``.sql`` files from the ``metadata/sql/`` directory (or any
directory / raw string supplied to the constructor) and converts every DDL
statement into an :class:`~graph.models.Asset` node plus dependency
:class:`~graph.models.Relationship` edges.

Supported DDL statements
------------------------
``CREATE [OR REPLACE] TABLE [schema.]name (…)``
    → :class:`~graph.models.AssetType.DATABASE_TABLE`

``CREATE [OR REPLACE] VIEW [schema.]name AS SELECT … FROM … [JOIN …]``
    → :class:`~graph.models.AssetType.SQL_VIEW`
    + ``SQL_VIEW ──READS──> DATABASE_TABLE`` edges for every referenced table.

``CREATE [OR REPLACE] PROCEDURE [schema.]name … AS … SELECT … FROM … [JOIN …]``
    → :class:`~graph.models.AssetType.STORED_PROCEDURE`
    + ``STORED_PROCEDURE ──READS──> DATABASE_TABLE`` edges.

``CREATE [OR REPLACE] FUNCTION [schema.]name … RETURNS … AS … SELECT … FROM …``
    → :class:`~graph.models.AssetType.SQL_FUNCTION`
    + ``SQL_FUNCTION ──READS──> DATABASE_TABLE`` edges.

Asset IDs
---------
For a two-part name ``schema.object``::

    sql::schema.object              e.g. ``sql::dbo.dim_customer``

For a bare name (no schema)::

    sql::object                     e.g. ``sql::orders``

Fault tolerance
---------------
- Missing SQL directory     → ``([], [])`` with a WARNING log.
- Missing individual file   → file skipped with a WARNING log.
- Malformed DDL statement   → statement skipped with a WARNING log.
- Duplicate asset IDs       → second occurrence skipped with a WARNING log.
- Empty files               → silently returns no assets from that file.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple, Union

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
# Constants
# ---------------------------------------------------------------------------

_SQL_SYSTEM: SystemType = SystemType.DATABASE

#: Default SQL files loaded when no explicit source is provided.
_DEFAULT_SQL_FILES: Tuple[str, ...] = (
    "tables.sql",
    "views.sql",
    "procedures.sql",
    "functions.sql",
)

# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------

# Strip single-line (--) and block (/* */) SQL comments
_RE_LINE_COMMENT  = re.compile(r"--[^\n]*")
_RE_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)

# Matches: CREATE [OR REPLACE] TABLE [schema.]name
_RE_TABLE = re.compile(
    r"\bCREATE\s+(?:OR\s+REPLACE\s+)?TABLE\s+"
    r"(?:IF\s+NOT\s+EXISTS\s+)?"
    r"(?P<name>[\w\.\[\]`\"]+)",
    re.IGNORECASE,
)

# Matches: CREATE [OR REPLACE] VIEW [schema.]name AS
_RE_VIEW = re.compile(
    r"\bCREATE\s+(?:OR\s+REPLACE\s+)?VIEW\s+"
    r"(?P<name>[\w\.\[\]`\"]+)\s+AS\b",
    re.IGNORECASE,
)

# Matches: CREATE [OR REPLACE] PROCEDURE [schema.]name
_RE_PROCEDURE = re.compile(
    r"\bCREATE\s+(?:OR\s+REPLACE\s+)?PROCEDURE\s+"
    r"(?P<name>[\w\.\[\]`\"]+)",
    re.IGNORECASE,
)

# Matches: CREATE [OR REPLACE] FUNCTION [schema.]name
_RE_FUNCTION = re.compile(
    r"\bCREATE\s+(?:OR\s+REPLACE\s+)?FUNCTION\s+"
    r"(?P<name>[\w\.\[\]`\"]+)",
    re.IGNORECASE,
)

# Matches FROM / JOIN table references inside a SELECT body.
# Captures optional alias; handles schema.table and bare table names.
# NOTE: The alias group uses [^\s,\n\r]+ (no whitespace/comma/newline) and
# is optional so that "FROM dbo.a\nJOIN …" does NOT consume "JOIN" as an alias.
_RE_TABLE_REF = re.compile(
    r"(?:FROM|JOIN)\s+(?P<ref>[\w\.\[\]`\"]+)"
    r"(?:[ \t]+(?:AS[ \t]+)?[A-Za-z_]\w*)?",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _strip_comments(sql: str) -> str:
    """Remove SQL line and block comments from *sql*.

    Args:
        sql: Raw SQL text.

    Returns:
        SQL with comments replaced by whitespace.
    """
    sql = _RE_BLOCK_COMMENT.sub(" ", sql)
    sql = _RE_LINE_COMMENT.sub(" ", sql)
    return sql


def _normalise_name(raw: str) -> str:
    """Strip quoting characters from a SQL identifier.

    Removes ``[``, ``]``, backtick, and double-quote characters so that
    ``[dbo].[dim_customer]`` becomes ``dbo.dim_customer``.

    Args:
        raw: Raw identifier as parsed from the SQL text.

    Returns:
        Clean, lower-cased identifier string.
    """
    return re.sub(r'[\[\]`"]', "", raw).lower().strip()


def _asset_id(name: str) -> str:
    """Return the canonical asset ID for a SQL object.

    Args:
        name: Normalised (lower-cased, unquoted) object name.

    Returns:
        Asset ID string, e.g. ``"sql::dbo.dim_customer"``.
    """
    return f"sql::{name}"


def _schema_and_name(normalised: str) -> Tuple[Optional[str], str]:
    """Split a normalised ``schema.object`` or bare ``object`` name.

    Args:
        normalised: Normalised object name.

    Returns:
        ``(schema, object_name)`` — schema is ``None`` for bare names.
    """
    parts = normalised.split(".")
    if len(parts) >= 2:
        return ".".join(parts[:-1]), parts[-1]
    return None, normalised


def _extract_table_refs(body: str, known_ids: Set[str]) -> List[str]:
    """Extract table asset IDs referenced in a SELECT body.

    Scans all ``FROM`` / ``JOIN`` clauses and returns IDs of tables that
    exist in *known_ids* (i.e. tables already parsed from ``tables.sql``).

    Sub-queries that alias a bare column name like ``FROM (SELECT …) sub``
    are safely ignored — ``_normalise_name`` on a sub-query fragment returns
    a garbage string that will never match a known ID.

    Args:
        body: SQL text containing SELECT statements.
        known_ids: Set of asset IDs already accumulated from TABLE definitions.

    Returns:
        Deduplicated list of asset IDs for referenced tables.
    """
    refs: List[str] = []
    seen: Set[str] = set()
    for m in _RE_TABLE_REF.finditer(body):
        raw_ref = _normalise_name(m.group("ref"))
        aid = _asset_id(raw_ref)
        if aid in known_ids and aid not in seen:
            refs.append(aid)
            seen.add(aid)
    return refs


# ---------------------------------------------------------------------------
# DDL block splitting
# ---------------------------------------------------------------------------

# Matches a GO batch separator on its own line (case-insensitive, T-SQL)
_RE_GO_SEPARATOR = re.compile(r"^\s*GO\s*$", re.IGNORECASE | re.MULTILINE)


def _split_statements(sql: str) -> List[str]:
    """Split a SQL script into individual DDL statements.

    Splitting strategy:

    1. Strip comments.
    2. If the script contains ``GO`` batch separators (T-SQL style), split on
       those — each batch is one logical statement and inner ``;`` are kept.
    3. Otherwise use a BEGIN/END-depth-aware semicolon splitter so that
       ``CREATE PROCEDURE … BEGIN … ; … END`` is treated as one unit and
       the ``;`` inside the body does not fragment it.

    Args:
        sql: Full SQL script text (may still contain comments).

    Returns:
        List of statement strings, stripped of surrounding whitespace.
        Empty fragments are discarded.
    """
    cleaned = _strip_comments(sql)

    # ── GO-based splitting (T-SQL batch separator) ────────────────────────
    if _RE_GO_SEPARATOR.search(cleaned):
        parts = _RE_GO_SEPARATOR.split(cleaned)
        return [p.strip() for p in parts if p.strip()]

    # ── BEGIN/END-depth-aware semicolon splitter ──────────────────────────
    # Track nesting depth so that ";" inside BEGIN…END is NOT a statement
    # boundary.
    statements: List[str] = []
    depth = 0
    current_chars: List[str] = []

    token_re = re.compile(r"\bBEGIN\b|\bEND\b|;", re.IGNORECASE)
    pos = 0
    for m in token_re.finditer(cleaned):
        tok = m.group().upper()
        # Accumulate everything up to and including this token
        current_chars.append(cleaned[pos: m.end()])
        pos = m.end()

        if tok == "BEGIN":
            depth += 1
        elif tok == "END":
            if depth > 0:
                depth -= 1
        elif tok == ";":
            if depth == 0:
                # Statement boundary — flush
                stmt = "".join(current_chars).rstrip(";").strip()
                if stmt:
                    statements.append(stmt)
                current_chars = []

    # Flush any trailing text (no closing semicolon)
    remaining = cleaned[pos:].strip()
    tail = ("".join(current_chars) + remaining).strip()
    if tail:
        statements.append(tail)

    return statements


# ---------------------------------------------------------------------------
# Per-file parsing
# ---------------------------------------------------------------------------

def _parse_sql_text(
    sql: str,
    source_file: str,
    known_table_ids: Set[str],
    owner: Optional[str] = None,
    criticality: Optional[Criticality] = None,
) -> Tuple[List[Asset], List[Relationship]]:
    """Parse a single SQL file's text and return assets + relationships.

    Each ``CREATE`` statement is processed in isolation.  Unrecognised
    statements (INSERT, ALTER, DROP, …) are silently skipped.

    Args:
        sql: Full SQL text of the file (may include comments).
        source_file: Human-readable label for log messages / ``source_file``
                     field on produced assets.
        known_table_ids: Set of TABLE asset IDs already accumulated; updated
                         in-place as new TABLE assets are discovered so that
                         later VIEW/PROCEDURE/FUNCTION statements in the
                         *same* file can reference them.
        owner: Default owner for produced assets.
        criticality: Default criticality for produced assets.

    Returns:
        ``(assets, relationships)`` produced by this file.
    """
    _criticality = criticality or Criticality.MEDIUM

    assets: List[Asset] = []
    relationships: List[Relationship] = []
    seen_ids: Set[str] = set()

    statements = _split_statements(sql)

    for stmt in statements:
        try:
            # ── TABLE ───────────────────────────────────────────────────────
            m = _RE_TABLE.search(stmt)
            if m:
                raw_name  = _normalise_name(m.group("name"))
                schema, obj_name = _schema_and_name(raw_name)
                aid = _asset_id(raw_name)

                if aid in seen_ids:
                    logger.warning(
                        "SQLMetadataParser: duplicate TABLE %r in %s — skipped",
                        raw_name, source_file,
                    )
                    continue
                seen_ids.add(aid)
                known_table_ids.add(aid)

                assets.append(Asset(
                    id=aid,
                    name=obj_name,
                    asset_type=AssetType.DATABASE_TABLE,
                    system=_SQL_SYSTEM,
                    schema=schema,
                    owner=owner,
                    criticality=_criticality,
                    source_file=source_file,
                    metadata={"sql_type": "table"},
                ))
                continue

            # ── VIEW ────────────────────────────────────────────────────────
            m = _RE_VIEW.search(stmt)
            if m:
                raw_name  = _normalise_name(m.group("name"))
                schema, obj_name = _schema_and_name(raw_name)
                aid = _asset_id(raw_name)

                if aid in seen_ids:
                    logger.warning(
                        "SQLMetadataParser: duplicate VIEW %r in %s — skipped",
                        raw_name, source_file,
                    )
                    continue
                seen_ids.add(aid)

                assets.append(Asset(
                    id=aid,
                    name=obj_name,
                    asset_type=AssetType.SQL_VIEW,
                    system=_SQL_SYSTEM,
                    schema=schema,
                    owner=owner,
                    criticality=_criticality,
                    source_file=source_file,
                    metadata={"sql_type": "view"},
                ))

                # Lineage edges: TABLE ──FEEDS──> VIEW
                # Direction: source TABLE → downstream VIEW so that BFS from a
                # changed table reaches the view (and anything beyond it).
                body_start = m.end()
                body = stmt[body_start:]
                for tbl_id in _extract_table_refs(body, known_table_ids):
                    relationships.append(Relationship(
                        source=tbl_id,
                        target=aid,
                        relationship=RelationshipType.FEEDS,
                        properties={"via": "view_definition"},
                    ))
                continue

            # ── PROCEDURE ───────────────────────────────────────────────────
            m = _RE_PROCEDURE.search(stmt)
            if m:
                raw_name  = _normalise_name(m.group("name"))
                schema, obj_name = _schema_and_name(raw_name)
                aid = _asset_id(raw_name)

                if aid in seen_ids:
                    logger.warning(
                        "SQLMetadataParser: duplicate PROCEDURE %r in %s — skipped",
                        raw_name, source_file,
                    )
                    continue
                seen_ids.add(aid)

                assets.append(Asset(
                    id=aid,
                    name=obj_name,
                    asset_type=AssetType.STORED_PROCEDURE,
                    system=_SQL_SYSTEM,
                    schema=schema,
                    owner=owner,
                    criticality=_criticality,
                    source_file=source_file,
                    metadata={"sql_type": "procedure"},
                ))

                # Lineage edges: TABLE ──FEEDS──> PROCEDURE
                body_start = m.end()
                body = stmt[body_start:]
                for tbl_id in _extract_table_refs(body, known_table_ids):
                    relationships.append(Relationship(
                        source=tbl_id,
                        target=aid,
                        relationship=RelationshipType.FEEDS,
                        properties={"via": "procedure_body"},
                    ))
                continue

            # ── FUNCTION ────────────────────────────────────────────────────
            m = _RE_FUNCTION.search(stmt)
            if m:
                raw_name  = _normalise_name(m.group("name"))
                schema, obj_name = _schema_and_name(raw_name)
                aid = _asset_id(raw_name)

                if aid in seen_ids:
                    logger.warning(
                        "SQLMetadataParser: duplicate FUNCTION %r in %s — skipped",
                        raw_name, source_file,
                    )
                    continue
                seen_ids.add(aid)

                assets.append(Asset(
                    id=aid,
                    name=obj_name,
                    asset_type=AssetType.SQL_FUNCTION,
                    system=_SQL_SYSTEM,
                    schema=schema,
                    owner=owner,
                    criticality=_criticality,
                    source_file=source_file,
                    metadata={"sql_type": "function"},
                ))

                # Lineage edges: TABLE ──FEEDS──> FUNCTION
                body_start = m.end()
                body = stmt[body_start:]
                for tbl_id in _extract_table_refs(body, known_table_ids):
                    relationships.append(Relationship(
                        source=tbl_id,
                        target=aid,
                        relationship=RelationshipType.FEEDS,
                        properties={"via": "function_body"},
                    ))
                continue

            # Any other statement (INSERT, ALTER, DROP, …) — silently skip

        except Exception as exc:  # pylint: disable=broad-except
            logger.warning(
                "SQLMetadataParser: error parsing statement in %s — %s",
                source_file, exc,
                exc_info=False,
            )

    return assets, relationships


# ---------------------------------------------------------------------------
# SQLMetadataParser
# ---------------------------------------------------------------------------


class SQLMetadataParser(BaseMetadataParser):
    """Parse SQL DDL files into graph assets and lineage edges.

    Reads ``.sql`` files from the ``metadata/sql/`` directory (or any
    directory / raw SQL string supplied to the constructor) and emits:

    * :class:`~graph.models.AssetType.DATABASE_TABLE` for every ``CREATE TABLE``.
    * :class:`~graph.models.AssetType.SQL_VIEW` for every ``CREATE VIEW``.
    * :class:`~graph.models.AssetType.STORED_PROCEDURE` for every ``CREATE PROCEDURE``.
    * :class:`~graph.models.AssetType.SQL_FUNCTION` for every ``CREATE FUNCTION``.
    * ``READS`` edges from views / procedures / functions to their source tables.

    Parameters:
        source: One of:

            * ``None`` (default) — reads all four canonical files from
              ``metadata/sql/``.
            * A :class:`~pathlib.Path` pointing to a *directory* — reads every
              ``*.sql`` file in that directory (sorted alphabetically so that
              ``tables.sql`` loads before ``views.sql``).
            * A :class:`~pathlib.Path` pointing to a *single file*.
            * A raw SQL ``str`` — parsed directly (used in unit tests).

        owner: Default owner for all produced assets.
        default_criticality: Default :class:`~graph.models.Criticality`.
    """

    #: Default SQL directory relative to the workspace root.
    DEFAULT_SQL_DIR: Path = Path("metadata") / "sql"

    def __init__(
        self,
        source: Union[str, Path, None] = None,
        owner: Optional[str] = None,
        default_criticality: Criticality = Criticality.MEDIUM,
    ) -> None:
        super().__init__(
            source_name="sql_metadata_parser",
            owner=owner,
            default_criticality=default_criticality,
        )
        self._source: Union[str, Path, None] = source

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def parse(self) -> ParseResult:
        """Parse SQL DDL sources and return assets and relationships.

        Returns:
            Tuple ``(assets, relationships)``.  Never raises.
        """
        texts = self._load_sources()
        if not texts:
            return [], []

        all_assets: List[Asset] = []
        all_relationships: List[Relationship] = []
        # Cross-file table ID registry — populated as tables.sql is parsed
        # so that views.sql, procedures.sql, functions.sql can reference them.
        known_table_ids: Set[str] = set()

        for label, sql_text in texts:
            try:
                assets, rels = _parse_sql_text(
                    sql_text, label, known_table_ids,
                    owner=self.owner,
                    criticality=self.default_criticality,
                )
                all_assets.extend(assets)
                all_relationships.extend(rels)
            except Exception as exc:  # pylint: disable=broad-except
                logger.warning(
                    "SQLMetadataParser: unhandled error in %s — %s", label, exc
                )

        logger.info(
            "SQLMetadataParser: produced %d assets, %d relationships",
            len(all_assets),
            len(all_relationships),
        )
        return all_assets, all_relationships

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _load_sources(self) -> List[Tuple[str, str]]:
        """Resolve the source parameter to a list of ``(label, sql_text)`` pairs.

        Returns:
            List of ``(label, sql_text)`` in load order, or ``[]`` on error.
        """
        src = self._source

        # ── Raw SQL string (unit tests) ──────────────────────────────────
        if isinstance(src, str):
            return [("inline_sql", src)]

        # ── Default: read from DEFAULT_SQL_DIR ───────────────────────────
        if src is None:
            sql_dir = self.DEFAULT_SQL_DIR
            if not sql_dir.exists():
                logger.warning(
                    "SQLMetadataParser: default SQL directory %s not found", sql_dir
                )
                return []
            return self._load_dir(sql_dir)

        path = Path(src)

        # ── Directory ────────────────────────────────────────────────────
        if path.is_dir():
            return self._load_dir(path)

        # ── Single file ──────────────────────────────────────────────────
        if path.is_file():
            text = self._read_file(path)
            if text is None:
                return []
            return [(path.name, text)]

        logger.warning(
            "SQLMetadataParser: path %s does not exist", path
        )
        return []

    def _load_dir(self, directory: Path) -> List[Tuple[str, str]]:
        """Load all ``*.sql`` files from *directory*, sorted alphabetically.

        Files are sorted so that ``tables.sql`` always loads first (so table
        asset IDs are in ``known_table_ids`` before views/procedures/functions
        are parsed).  The canonical order is defined by ``_DEFAULT_SQL_FILES``;
        any additional ``*.sql`` files are appended in alphabetical order.

        Args:
            directory: Directory containing ``.sql`` files.

        Returns:
            List of ``(filename, sql_text)`` pairs in dependency-safe order.
        """
        all_sql = list(directory.glob("*.sql"))

        # Canonical files in dependency-safe order: tables → views → procs → functions
        _CANONICAL_ORDER = {name: i for i, name in enumerate(_DEFAULT_SQL_FILES)}

        def _sort_key(p: Path) -> Tuple[int, str]:
            return (_CANONICAL_ORDER.get(p.name, len(_DEFAULT_SQL_FILES)), p.name)

        sql_files = sorted(all_sql, key=_sort_key)
        if not sql_files:
            logger.info(
                "SQLMetadataParser: no *.sql files found in %s", directory
            )
            return []
        results: List[Tuple[str, str]] = []
        for fp in sql_files:
            text = self._read_file(fp)
            if text is not None:
                results.append((fp.name, text))
        return results

    @staticmethod
    def _read_file(path: Path) -> Optional[str]:
        """Read a SQL file and return its text, or ``None`` on error.

        Args:
            path: Path to the ``.sql`` file.

        Returns:
            File text, or ``None`` if the file cannot be read.
        """
        try:
            return path.read_text(encoding="utf-8-sig")
        except OSError as exc:
            logger.warning(
                "SQLMetadataParser: cannot read %s — %s", path, exc
            )
            return None
