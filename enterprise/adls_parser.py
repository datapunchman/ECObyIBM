"""
enterprise.adls_parser
======================
Azure Data Lake Storage (ADLS) Metadata Parser.

This parser reads ``metadata/adls/adls_inventory.csv`` — a structured inventory
of files stored in ADLS containers — and converts every row into an
:class:`~graph.models.Asset` node of type ``ADLS_FILE`` plus an
``ADLS_FILE ──INGESTS_TO──> DELTA_TABLE`` lineage edge pointing at the
corresponding Bronze Delta table.

The parser does **not** read, execute, or inspect any notebook or SQL source.
It is purely driven by the CSV manifest.

CSV Schema
----------
Expected columns (case-insensitive, extra columns are silently ignored):

    FileName     — file name including extension (e.g. ``DimCustomer.parquet``)
    Container    — ADLS container name (e.g. ``landing``)
    Folder       — path within the container (e.g. ``/landing``)
    Format       — file format (e.g. ``parquet``, ``csv``, ``delta``)
    Description  — human-readable description (optional)
    BronzeTable  — fully-qualified target Delta table (e.g. ``bronze.dim_customer``)
                   When present and non-empty an ``INGESTS_TO`` edge is emitted.

Graph Model
-----------
For each CSV row::

    ADLS_FILE(FileName)  ──INGESTS_TO──>  DELTA_TABLE(BronzeTable)

Asset IDs:
    ADLS file:     ``adls_file::<Container>/<Folder>/<FileName>``
    Bronze table:  ``delta_table::<BronzeTable>``  (stub, ``unresolved=True``)

Fault Tolerance
---------------
- Missing CSV file            → returns ``([], [])`` with a WARNING log.
- Malformed CSV               → returns ``([], [])`` with a WARNING log.
- Row with empty FileName     → row skipped with a WARNING log.
- Missing ``BronzeTable``     → ADLS_FILE asset created, no INGESTS_TO edge.
- Duplicate rows              → both assets emitted (unique by asset ID);
                               if two rows produce the same asset ID the
                               second is skipped with a WARNING log.
- Missing required columns    → ``([], [])`` with WARNING; parser is
                               lenient when ``FileName`` is present even if
                               other columns are absent (defaults to empty string).
"""

from __future__ import annotations

import csv
import io
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

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
# SystemType for ADLS
# ---------------------------------------------------------------------------
# ADLS files belong to the Azure / storage layer.  We use the closest
# existing system type.  If a fine-grained ADLS system type is added to
# SystemType in a future iteration, only this constant needs updating.
_ADLS_SYSTEM: SystemType = SystemType.DATABASE  # represents the Azure storage tier

# ---------------------------------------------------------------------------
# Column-name normalisation
# ---------------------------------------------------------------------------
# Maps lower-cased column headers to the canonical field name used internally.
_COL_MAP: Dict[str, str] = {
    "filename":    "filename",
    "file_name":   "filename",
    "container":   "container",
    "folder":      "folder",
    "format":      "format",
    "description": "description",
    "bronzetable": "bronze_table",
    "bronze_table": "bronze_table",
    "target":      "bronze_table",
    "target_table": "bronze_table",
}


# ---------------------------------------------------------------------------
# ID helpers
# ---------------------------------------------------------------------------

def _adls_asset_id(container: str, folder: str, filename: str) -> str:
    """Return the canonical asset ID for an ADLS file.

    Builds ``adls_file::<container>/<folder>/<filename>`` with duplicate
    slashes collapsed.

    Args:
        container: ADLS container name.
        folder:    Path within the container (leading/trailing slashes stripped).
        filename:  File name including extension.

    Returns:
        Canonical asset ID string.
    """
    folder_clean = folder.strip("/")
    parts = [p for p in [container, folder_clean, filename] if p]
    return f"adls_file::{'/'.join(parts)}"


def _delta_table_id(bronze_table: str) -> str:
    """Return the canonical asset ID for a Bronze Delta table stub.

    Args:
        bronze_table: Fully-qualified table ref (e.g. ``bronze.dim_customer``).

    Returns:
        Asset ID string, e.g. ``"delta_table::bronze.dim_customer"``.
    """
    return f"delta_table::{bronze_table}"


# ---------------------------------------------------------------------------
# CSV loading helper
# ---------------------------------------------------------------------------

def _load_csv(source: Union[str, Path]) -> Optional[List[Dict[str, str]]]:
    """Read and parse the ADLS inventory CSV.

    Accepts either a file path or a raw CSV string (for unit testing without
    disk I/O).  Column headers are normalised to lower-case; rows with all
    empty values are skipped.

    Args:
        source: ``Path`` to the CSV file, or a raw CSV string.

    Returns:
        List of row dicts with normalised (lower-cased) keys, or ``None`` on
        any unrecoverable error.
    """
    if isinstance(source, Path):
        try:
            text = source.read_text(encoding="utf-8-sig")  # strip BOM if present
        except OSError as exc:
            logger.warning("ADLSMetadataParser: cannot read %s — %s", source, exc)
            return None
    else:
        text = str(source)

    try:
        reader = csv.DictReader(io.StringIO(text))
        rows: List[Dict[str, str]] = []
        for row in reader:
            # Normalise header keys
            normalised: Dict[str, str] = {
                k.strip().lower(): v.strip() if v else ""
                for k, v in row.items()
                if k is not None
            }
            # Skip entirely blank rows
            if not any(normalised.values()):
                continue
            rows.append(normalised)
        return rows
    except csv.Error as exc:
        logger.warning("ADLSMetadataParser: malformed CSV — %s", exc)
        return None


def _map_row(raw_row: Dict[str, str]) -> Dict[str, str]:
    """Re-map raw column names to canonical field names.

    Args:
        raw_row: Dict with normalised (lower-cased) keys as returned by
                 :func:`_load_csv`.

    Returns:
        Dict with canonical field names: ``filename``, ``container``,
        ``folder``, ``format``, ``description``, ``bronze_table``.
    """
    mapped: Dict[str, str] = {
        "filename":     "",
        "container":    "",
        "folder":       "",
        "format":       "",
        "description":  "",
        "bronze_table": "",
    }
    for raw_key, value in raw_row.items():
        canonical = _COL_MAP.get(raw_key)
        if canonical and value:
            mapped[canonical] = value
    return mapped


# ---------------------------------------------------------------------------
# ADLSMetadataParser
# ---------------------------------------------------------------------------


class ADLSMetadataParser(BaseMetadataParser):
    """Parse an ADLS file inventory CSV into graph assets and lineage edges.

    Reads ``metadata/adls/adls_inventory.csv`` (or any CSV / string provided
    to the constructor) and emits:

    * One :class:`~graph.models.Asset` with ``asset_type=ADLS_FILE`` for
      every row that has a non-empty ``FileName``.
    * One stub :class:`~graph.models.Asset` with ``asset_type=DELTA_TABLE``
      for every unique ``BronzeTable`` reference (deduplicated across rows).
    * ``ADLS_FILE ──INGESTS_TO──> DELTA_TABLE`` edges.

    Parameters:
        source: Path to the CSV file or a raw CSV string.  Defaults to the
                canonical ``metadata/adls/adls_inventory.csv`` path relative
                to the current working directory when ``None`` is passed.
        owner: Default owner tag for all produced assets.
        default_criticality: Default :class:`~graph.models.Criticality`.
        emit_table_stubs: When ``True`` (default), emit a ``DELTA_TABLE``
                          stub asset for every unique ``BronzeTable``
                          reference.  Set ``False`` if callers manage Bronze
                          table assets separately.
    """

    #: Default CSV path relative to the workspace root.
    DEFAULT_CSV_PATH = Path("metadata") / "adls" / "adls_inventory.csv"

    def __init__(
        self,
        source: Union[str, Path, None] = None,
        owner: Optional[str] = None,
        default_criticality: Criticality = Criticality.MEDIUM,
        emit_table_stubs: bool = True,
    ) -> None:
        """Initialise the parser.

        Args:
            source: Path to CSV or raw CSV string.  ``None`` uses the
                    default ``metadata/adls/adls_inventory.csv`` path.
            owner: Default owner for produced assets.
            default_criticality: Default criticality.
            emit_table_stubs: Emit stub DELTA_TABLE assets for bronze refs.
        """
        super().__init__(
            source_name="adls_metadata_parser",
            owner=owner,
            default_criticality=default_criticality,
        )
        self._source: Union[str, Path] = (
            source if source is not None else self.DEFAULT_CSV_PATH
        )
        self._emit_table_stubs = emit_table_stubs

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def parse(self) -> ParseResult:
        """Parse the ADLS inventory CSV and return assets and relationships.

        Returns:
            Tuple ``(assets, relationships)``.  Never raises.  Rows that
            cannot be parsed are skipped with a WARNING log entry.
        """
        rows = _load_csv(self._source)
        if rows is None:
            return [], []

        if not rows:
            logger.info(
                "ADLSMetadataParser: CSV is empty — no assets produced"
            )
            return [], []

        assets: List[Asset] = []
        relationships: List[Relationship] = []
        seen_asset_ids: set[str] = set()
        seen_table_ids: set[str] = set()

        for idx, raw_row in enumerate(rows):
            try:
                row = _map_row(raw_row)

                filename = row["filename"].strip()
                if not filename:
                    logger.warning(
                        "ADLSMetadataParser: row %d has empty FileName — skipped",
                        idx + 1,
                    )
                    continue

                container   = row["container"].strip()
                folder      = row["folder"].strip()
                fmt         = row["format"].strip()
                description = row["description"].strip()
                bronze_table = row["bronze_table"].strip()

                # ── ADLS_FILE asset ───────────────────────────────────────
                asset_id = _adls_asset_id(container, folder, filename)

                if asset_id in seen_asset_ids:
                    logger.warning(
                        "ADLSMetadataParser: duplicate asset ID %r (row %d) — skipped",
                        asset_id, idx + 1,
                    )
                    continue
                seen_asset_ids.add(asset_id)

                adls_asset = self._make_asset(
                    id=asset_id,
                    name=filename,
                    asset_type=AssetType.ADLS_FILE,
                    system=_ADLS_SYSTEM,
                    metadata={
                        "container":   container or None,
                        "folder":      folder or None,
                        "format":      fmt or None,
                        "description": description or None,
                        "bronze_table": bronze_table or None,
                    },
                )
                assets.append(adls_asset)

                # ── Bronze table stub + INGESTS_TO edge ───────────────────
                if bronze_table:
                    table_id = _delta_table_id(bronze_table)

                    if self._emit_table_stubs and table_id not in seen_table_ids:
                        tbl_parts = bronze_table.split(".")
                        if len(tbl_parts) >= 2:
                            tbl_schema = tbl_parts[-2]
                            tbl_name   = tbl_parts[-1]
                        else:
                            tbl_schema = None
                            tbl_name   = bronze_table

                        table_stub = self._make_asset(
                            id=table_id,
                            name=tbl_name,
                            asset_type=AssetType.DELTA_TABLE,
                            system=SystemType.DATABRICKS,
                            schema=tbl_schema,
                            metadata={
                                "raw_ref": bronze_table,
                                "stub":    True,
                                "layer":   "bronze",
                            },
                        )
                        assets.append(table_stub)
                        seen_table_ids.add(table_id)

                    relationships.append(Relationship(
                        source=asset_id,
                        target=table_id,
                        relationship=RelationshipType.INGESTS_TO,
                        properties={"unresolved": True, "raw_ref": bronze_table},
                    ))

            except Exception as exc:  # pylint: disable=broad-except
                logger.warning(
                    "ADLSMetadataParser: unexpected error on row %d — %s",
                    idx + 1, exc,
                    exc_info=False,
                )

        logger.info(
            "ADLSMetadataParser: produced %d assets (%d ADLS files, %d table stubs), "
            "%d relationships",
            len(assets),
            len([a for a in assets if a.asset_type == AssetType.ADLS_FILE]),
            len([a for a in assets if a.asset_type == AssetType.DELTA_TABLE]),
            len(relationships),
        )
        return assets, relationships
