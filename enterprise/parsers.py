"""
enterprise.parsers
==================
Parser interfaces and concrete implementations for every technology source.

Design
------
All parsers implement the :class:`BaseMetadataParser` ABC.  The only contract
is ``parse() -> list[Asset]``.  Callers never depend on which concrete parser
produced an asset — all parsers return the same :class:`~graph.models.Asset`
and :class:`~graph.models.Relationship` types so the
:class:`~enterprise.registry.EnterpriseAssetRegistry` can merge any combination
of sources into a single :class:`~graph.enterprise_graph.EnterpriseGraph`
without special-casing any technology.

Provided implementations
------------------------
:class:`PowerBIParser`
    Wraps the existing ``MetadataAdapter`` so Power BI assets continue to flow
    through the same pipeline.  Produces ``system=POWERBI`` assets.

:class:`SQLParser`
    Ingests SQL DDL or information-schema rows describing physical tables, views,
    stored procedures, and functions.  Produces ``system=DATABASE`` or
    ``system=SQL`` assets.

:class:`NotebookParser`
    Ingests Databricks notebook descriptors (JSON / YAML manifest).  Produces
    ``system=DATABRICKS`` assets.

:class:`PipelineParser`
    Ingests pipeline descriptors (ADF JSON, Airflow DAG manifest, Fabric YAML).
    Produces ``system=ADF``, ``system=AIRFLOW``, or ``system=FABRIC`` assets.

Extension pattern
-----------------
To add a new source (e.g. Snowflake), subclass :class:`BaseMetadataParser`,
implement ``parse()``, and register the parser with
:class:`~enterprise.registry.EnterpriseAssetRegistry`.  No other code needs
to change.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Tuple

from graph.enterprise_graph import EnterpriseGraph
from graph.models import (
    Asset,
    AssetType,
    Criticality,
    Relationship,
    RelationshipType,
    SystemType,
)

logger = logging.getLogger(__name__)

# Type alias for what every parser returns
ParseResult = Tuple[List[Asset], List[Relationship]]


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------


class BaseMetadataParser(ABC):
    """Abstract base class for all enterprise metadata parsers.

    Every parser must implement :meth:`parse`, which returns a tuple of
    ``(assets, relationships)``.  Both lists may be empty — the registry
    will silently skip empty parsers.

    Parsers are stateless where possible; all configuration is passed to the
    constructor so parsers can be unit-tested without file I/O.

    Parameters
    ----------
    source_name:
        A short label used in log messages and asset ``source_file`` fields
        to identify which parser produced a given asset.
    owner:
        Default owner tag applied to all assets produced by this parser when
        the asset manifest does not supply an explicit owner.
    default_criticality:
        Default criticality applied to produced assets.
    """

    def __init__(
        self,
        source_name: str = "unknown",
        owner: Optional[str] = None,
        default_criticality: Criticality = Criticality.MEDIUM,
    ) -> None:
        self.source_name = source_name
        self.owner = owner
        self.default_criticality = default_criticality

    @abstractmethod
    def parse(self) -> ParseResult:
        """Parse the source and return ``(assets, relationships)``.

        Returns
        -------
        tuple[list[Asset], list[Relationship]]
            All assets and relationships discovered in the source.
            Relationships may reference assets produced by *other* parsers
            (cross-source lineage); the registry resolves them after all
            parsers have run.
        """

    def _make_asset(
        self,
        *,
        id: str,
        name: str,
        asset_type: AssetType,
        system: SystemType,
        catalog: Optional[str] = None,
        schema: Optional[str] = None,
        owner: Optional[str] = None,
        criticality: Optional[Criticality] = None,
        tags: Optional[List[str]] = None,
        source_file: Optional[str] = None,
        line_number: Optional[int] = None,
        metadata: Optional[Dict[str, Any]] = None,
        properties: Optional[Dict[str, Any]] = None,
    ) -> Asset:
        """Convenience factory that fills default fields from parser config."""
        return Asset(
            id=id,
            name=name,
            asset_type=asset_type,
            system=system,
            catalog=catalog,
            schema=schema,
            owner=owner or self.owner,
            criticality=criticality or self.default_criticality,
            tags=tags or [],
            source_file=source_file or self.source_name,
            line_number=line_number,
            metadata=metadata or {},
            properties=properties or {},
        )


# ---------------------------------------------------------------------------
# PowerBIParser
# ---------------------------------------------------------------------------


class PowerBIParser(BaseMetadataParser):
    """Parses a Power BI / PBIP semantic-model payload into graph assets.

    This parser is a thin wrapper around the existing
    :class:`~graph.adapter.MetadataAdapter`.  It accepts the same
    ``MetadataPayload | dict`` input the adapter already handles and returns
    the adapter-produced graph assets as flat lists, integrating seamlessly
    with the multi-source registry.

    Parameters
    ----------
    payload:
        Either a :class:`~metadata.models.MetadataPayload` instance or the
        raw JSON dict from ``GET /metadata``.
    owner:
        Optional default owner for all Power BI assets.
    """

    def __init__(
        self,
        payload: Any,
        owner: Optional[str] = None,
        default_criticality: Criticality = Criticality.MEDIUM,
    ) -> None:
        super().__init__(
            source_name="powerbi",
            owner=owner,
            default_criticality=default_criticality,
        )
        self._payload = payload

    def parse(self) -> ParseResult:
        """Delegate to MetadataAdapter, then extract assets and relationships."""
        from graph.adapter import MetadataAdapter  # lazy — avoids circular import

        graph: EnterpriseGraph = MetadataAdapter.to_enterprise_graph(self._payload)

        assets: List[Asset] = list(graph.assets.values())
        relationships: List[Relationship] = list(graph.relationships)

        # Enrich assets with owner / criticality from parser config
        for asset in assets:
            if not asset.owner and self.owner:
                asset.owner = self.owner
            if asset.criticality == Criticality.MEDIUM and self.default_criticality != Criticality.MEDIUM:
                asset.criticality = self.default_criticality
            if not asset.source_file:
                asset.source_file = self.source_name

        logger.info(
            "PowerBIParser: produced %d assets, %d relationships",
            len(assets),
            len(relationships),
        )
        return assets, relationships


# ---------------------------------------------------------------------------
# SQLParser
# ---------------------------------------------------------------------------


class SQLParser(BaseMetadataParser):
    """Parses SQL data-source descriptors into physical database assets.

    Accepts a list of descriptor dicts, each describing one SQL artifact
    (table, view, stored procedure, or function).  This mirrors what a
    real implementation would receive from an information-schema query or
    a DDL scanner.

    Each descriptor dict supports the following keys:

    .. code-block:: python

        {
            "type":        "table" | "view" | "materialized_view"
                           | "stored_procedure" | "sql_function",
            "catalog":     "AdventureWorks",          # optional
            "schema":      "dbo",
            "name":        "Orders",
            "columns":     [{"name": "OrderID", "type": "int", "is_pk": True}, ...],
            "reads":       ["dbo.Customers"],          # refs for relationship edges
            "owner":       "dba-team",                # overrides parser default
            "criticality": "high",                    # overrides parser default
            "tags":        ["gold", "pii"],
            "source_file": "schema/dbo.Orders.sql",
        }

    Parameters
    ----------
    descriptors:
        List of SQL artifact descriptor dicts (see above).
    system:
        System classification for all produced assets (default ``DATABASE``).
    owner:
        Default owner for produced assets.
    """

    _TYPE_MAP: Dict[str, AssetType] = {
        "table":             AssetType.DATABASE_TABLE,
        "view":              AssetType.SQL_VIEW,
        "materialized_view": AssetType.MATERIALIZED_VIEW,
        "stored_procedure":  AssetType.STORED_PROCEDURE,
        "sql_function":      AssetType.SQL_FUNCTION,
        "function":          AssetType.SQL_FUNCTION,
    }

    def __init__(
        self,
        descriptors: List[Dict[str, Any]],
        system: SystemType = SystemType.DATABASE,
        owner: Optional[str] = None,
        default_criticality: Criticality = Criticality.MEDIUM,
    ) -> None:
        super().__init__(
            source_name="sql",
            owner=owner,
            default_criticality=default_criticality,
        )
        self._descriptors = descriptors
        self._system = system

    def parse(self) -> ParseResult:
        assets: List[Asset] = []
        relationships: List[Relationship] = []

        for desc in self._descriptors:
            art_type_str = desc.get("type", "table").lower()
            asset_type = self._TYPE_MAP.get(art_type_str, AssetType.DATABASE_TABLE)
            catalog = desc.get("catalog")
            schema = desc.get("schema", "dbo")
            name = desc.get("name", "unknown")
            fqn_parts = [p for p in (catalog, schema, name) if p]
            asset_id = f"sql::{'.'.join(fqn_parts)}"

            criticality_str = desc.get("criticality", "")
            try:
                crit = Criticality(criticality_str) if criticality_str else self.default_criticality
            except ValueError:
                crit = self.default_criticality

            asset = self._make_asset(
                id=asset_id,
                name=name,
                asset_type=asset_type,
                system=self._system,
                catalog=catalog,
                schema=schema,
                owner=desc.get("owner"),
                criticality=crit,
                tags=desc.get("tags", []),
                source_file=desc.get("source_file", self.source_name),
                metadata={"raw_type": art_type_str},
            )
            assets.append(asset)

            # Column nodes
            for col in desc.get("columns", []):
                col_name = col.get("name", "unknown")
                col_type = AssetType.PRIMARY_KEY if col.get("is_pk") else AssetType.DATABASE_COLUMN
                col_id = f"sql::{'.'.join(fqn_parts)}.{col_name}"
                col_asset = self._make_asset(
                    id=col_id,
                    name=col_name,
                    asset_type=col_type,
                    system=self._system,
                    catalog=catalog,
                    schema=schema,
                    metadata={"data_type": col.get("type", "unknown"), "is_pk": col.get("is_pk", False)},
                )
                assets.append(col_asset)
                relationships.append(Relationship(
                    source=col_id,
                    target=asset_id,
                    relationship=RelationshipType.CONTAINS,
                ))

            # Lineage edges: this view/proc READS from referenced objects
            for ref in desc.get("reads", []):
                ref_id = f"sql::{ref}"
                relationships.append(Relationship(
                    source=asset_id,
                    target=ref_id,
                    relationship=RelationshipType.READS,
                    properties={"unresolved": True},
                ))

        logger.info(
            "SQLParser: produced %d assets, %d relationships",
            len(assets),
            len(relationships),
        )
        return assets, relationships


# ---------------------------------------------------------------------------
# NotebookParser
# ---------------------------------------------------------------------------


class NotebookParser(BaseMetadataParser):
    """Parses Databricks notebook descriptors into graph assets.

    Each descriptor dict supports:

    .. code-block:: python

        {
            "name":        "etl_revenue_transform",
            "path":        "/Repos/de-team/etl_revenue_transform",
            "cluster":     "job-cluster-001",
            "reads":       ["sql::dbo.Orders", "sql::dbo.Customers"],
            "writes":      ["delta::gold.revenue_daily"],
            "owner":       "de-team",
            "criticality": "high",
            "tags":        ["gold", "etl"],
        }
    """

    def __init__(
        self,
        descriptors: List[Dict[str, Any]],
        owner: Optional[str] = None,
        default_criticality: Criticality = Criticality.MEDIUM,
    ) -> None:
        super().__init__(
            source_name="databricks",
            owner=owner,
            default_criticality=default_criticality,
        )
        self._descriptors = descriptors

    def parse(self) -> ParseResult:
        assets: List[Asset] = []
        relationships: List[Relationship] = []

        for desc in self._descriptors:
            name = desc.get("name", "unknown_notebook")
            path = desc.get("path", name)
            asset_id = f"notebook::{path}"

            criticality_str = desc.get("criticality", "")
            try:
                crit = Criticality(criticality_str) if criticality_str else self.default_criticality
            except ValueError:
                crit = self.default_criticality

            asset = self._make_asset(
                id=asset_id,
                name=name,
                asset_type=AssetType.DATABRICKS_NOTEBOOK,
                system=SystemType.DATABRICKS,
                owner=desc.get("owner"),
                criticality=crit,
                tags=desc.get("tags", []),
                source_file=desc.get("path", self.source_name),
                metadata={
                    "cluster": desc.get("cluster"),
                    "path": path,
                },
            )
            assets.append(asset)

            for ref in desc.get("reads", []):
                relationships.append(Relationship(
                    source=asset_id,
                    target=ref,
                    relationship=RelationshipType.READS,
                    properties={"unresolved": True},
                ))
            for ref in desc.get("writes", []):
                relationships.append(Relationship(
                    source=asset_id,
                    target=ref,
                    relationship=RelationshipType.WRITES,
                    properties={"unresolved": True},
                ))

        logger.info(
            "NotebookParser: produced %d assets, %d relationships",
            len(assets),
            len(relationships),
        )
        return assets, relationships


# ---------------------------------------------------------------------------
# PipelineParser
# ---------------------------------------------------------------------------


class PipelineParser(BaseMetadataParser):
    """Parses orchestration pipeline descriptors (ADF / Fabric / Airflow).

    Each descriptor dict supports:

    .. code-block:: python

        {
            "name":      "pl_load_revenue",
            "type":      "adf" | "airflow" | "fabric",
            "reads":     ["sql::dbo.Orders"],
            "writes":    ["delta::gold.revenue_daily"],
            "calls":     ["notebook::/Repos/de-team/etl_revenue_transform"],
            "publishes": ["api::revenue-events-topic"],
            "owner":     "orchestration-team",
            "criticality": "critical",
            "tags":      ["ingestion", "scheduled"],
        }
    """

    _PIPELINE_TYPE_MAP: Dict[str, Tuple[AssetType, SystemType]] = {
        "adf":     (AssetType.ADF_PIPELINE,    SystemType.ADF),
        "airflow": (AssetType.AIRFLOW_DAG,      SystemType.AIRFLOW),
        "fabric":  (AssetType.FABRIC_PIPELINE,  SystemType.FABRIC),
        "generic": (AssetType.PIPELINE,         SystemType.PIPELINE),
    }

    def __init__(
        self,
        descriptors: List[Dict[str, Any]],
        owner: Optional[str] = None,
        default_criticality: Criticality = Criticality.MEDIUM,
    ) -> None:
        super().__init__(
            source_name="pipeline",
            owner=owner,
            default_criticality=default_criticality,
        )
        self._descriptors = descriptors

    def parse(self) -> ParseResult:
        assets: List[Asset] = []
        relationships: List[Relationship] = []

        for desc in self._descriptors:
            name = desc.get("name", "unknown_pipeline")
            pipeline_type_str = desc.get("type", "generic").lower()
            asset_type, system = self._PIPELINE_TYPE_MAP.get(
                pipeline_type_str,
                (AssetType.PIPELINE, SystemType.PIPELINE),
            )
            asset_id = f"pipeline::{name}"

            criticality_str = desc.get("criticality", "")
            try:
                crit = Criticality(criticality_str) if criticality_str else self.default_criticality
            except ValueError:
                crit = self.default_criticality

            asset = self._make_asset(
                id=asset_id,
                name=name,
                asset_type=asset_type,
                system=system,
                owner=desc.get("owner"),
                criticality=crit,
                tags=desc.get("tags", []),
                metadata={"pipeline_type": pipeline_type_str},
            )
            assets.append(asset)

            for ref in desc.get("reads", []):
                relationships.append(Relationship(source=asset_id, target=ref,
                    relationship=RelationshipType.READS, properties={"unresolved": True}))
            for ref in desc.get("writes", []):
                relationships.append(Relationship(source=asset_id, target=ref,
                    relationship=RelationshipType.WRITES, properties={"unresolved": True}))
            for ref in desc.get("calls", []):
                relationships.append(Relationship(source=asset_id, target=ref,
                    relationship=RelationshipType.CALLS, properties={"unresolved": True}))
            for ref in desc.get("publishes", []):
                relationships.append(Relationship(source=asset_id, target=ref,
                    relationship=RelationshipType.PUBLISHES, properties={"unresolved": True}))

        logger.info(
            "PipelineParser: produced %d assets, %d relationships",
            len(assets),
            len(relationships),
        )
        return assets, relationships
