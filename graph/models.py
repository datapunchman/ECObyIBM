"""
graph.models
============
Core data models for the Enterprise Metadata Graph.

Backward compatibility
----------------------
All values that existed before this expansion are preserved with identical
string values.  Code that referenced ``AssetType.TABLE``, ``SystemType.POWERBI``,
``RelationshipType.USES``, etc., continues to work without change.

New values are purely additive.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# AssetType
# ---------------------------------------------------------------------------


class AssetType(str, Enum):
    """Every node type that can appear in the Enterprise Metadata Graph.

    Values are grouped by technology layer so the enum reads top-to-bottom
    in data-flow order: database → compute → orchestration → BI.

    Backward-compatible additions are marked with a comment.
    """

    # ── Database / SQL layer ────────────────────────────────────────────────
    DATABASE          = "database"           # ← existing
    DATABASE_TABLE    = "database_table"     # NEW: physical table in a RDBMS
    DATABASE_COLUMN   = "database_column"    # NEW: physical column
    PRIMARY_KEY       = "primary_key"        # NEW: PK constraint node
    FOREIGN_KEY       = "foreign_key"        # NEW: FK constraint / join edge
    TABLE             = "table"              # ← existing (keep; used by PBIP adapter)
    COLUMN            = "column"             # ← existing (keep; used by PBIP adapter)
    VIEW              = "view"               # ← existing
    SQL_VIEW          = "sql_view"           # NEW: explicit SQL view
    MATERIALIZED_VIEW = "materialized_view"  # NEW
    STORED_PROCEDURE  = "stored_procedure"   # ← existing
    SQL_FUNCTION      = "sql_function"       # NEW: scalar / table-valued function
    FUNCTION          = "function"           # ← existing (keep alias)

    # ── Databricks / Spark layer ────────────────────────────────────────────
    DATABRICKS_NOTEBOOK = "databricks_notebook"   # NEW
    NOTEBOOK            = "notebook"              # ← existing (keep alias)
    SPARK_JOB           = "spark_job"             # NEW
    JOB                 = "job"                   # ← existing (keep alias)
    DELTA_TABLE         = "delta_table"           # ← existing
    DELTA_LIVE_TABLE    = "delta_live_table"      # NEW
    UNITY_CATALOG_OBJECT = "unity_catalog_object" # NEW

    # ── Storage / File layer ────────────────────────────────────────────────
    ADLS_FILE        = "adls_file"          # NEW: Azure Data Lake Storage file

    # ── Orchestration / Pipeline layer ──────────────────────────────────────
    PIPELINE         = "pipeline"           # ← existing
    PIPELINE_TASK    = "pipeline_task"      # NEW: individual task within a Databricks workflow
    ADF_PIPELINE     = "adf_pipeline"       # NEW: Azure Data Factory
    FABRIC_PIPELINE  = "fabric_pipeline"    # NEW: Microsoft Fabric
    AIRFLOW_DAG      = "airflow_dag"        # NEW
    DATAFLOW         = "dataflow"           # NEW

    # ── API / Integration layer ─────────────────────────────────────────────
    API       = "api"        # ← existing
    REST_API  = "rest_api"   # NEW: explicit REST endpoint node

    # ── Power BI / BI layer ─────────────────────────────────────────────────
    SEMANTIC_MODEL   = "semantic_model"    # ← existing
    POWERBI_DATASET  = "powerbi_dataset"   # NEW: dataset published to service
    MEASURE          = "measure"           # ← existing
    POWERBI_MEASURE  = "powerbi_measure"   # NEW (explicit alias)
    VISUAL           = "visual"            # ← existing
    POWERBI_VISUAL   = "powerbi_visual"    # NEW (explicit alias)
    REPORT           = "report"            # ← existing
    POWERBI_REPORT   = "powerbi_report"    # NEW (explicit alias)
    DASHBOARD        = "dashboard"         # ← existing
    POWERBI_DASHBOARD = "powerbi_dashboard" # NEW (explicit alias)


# ---------------------------------------------------------------------------
# SystemType
# ---------------------------------------------------------------------------


class SystemType(str, Enum):
    """The technology system that owns or hosts an asset.

    Existing values kept; new values added for finer-grained classification.
    """

    DATABASE   = "database"   # ← existing — generic RDBMS (SQL Server, Postgres …)
    SQL        = "sql"        # ← existing
    DATABRICKS = "databricks" # ← existing
    PIPELINE   = "pipeline"   # ← existing
    POWERBI    = "powerbi"    # ← existing
    API        = "api"        # ← existing

    # New fine-grained system classifications
    DELTA_LAKE      = "delta_lake"       # NEW: Delta Lake / Delta Live Tables
    UNITY_CATALOG   = "unity_catalog"    # NEW: Databricks Unity Catalog
    ADF             = "adf"              # NEW: Azure Data Factory
    FABRIC          = "fabric"           # NEW: Microsoft Fabric
    AIRFLOW         = "airflow"          # NEW: Apache Airflow
    SYNAPSE         = "synapse"          # NEW: Azure Synapse Analytics
    SNOWFLAKE       = "snowflake"        # NEW: Snowflake
    BIGQUERY        = "bigquery"         # NEW: Google BigQuery
    REDSHIFT        = "redshift"         # NEW: Amazon Redshift


# ---------------------------------------------------------------------------
# RelationshipType
# ---------------------------------------------------------------------------


class RelationshipType(str, Enum):
    """Typed directed edges in the Enterprise Metadata Graph.

    Existing values kept; new typed edges added.
    """

    # ← existing
    USES       = "USES"
    READS      = "READS"
    WRITES     = "WRITES"
    FEEDS      = "FEEDS"
    CALLS      = "CALLS"
    REFERENCES = "REFERENCES"
    DISPLAYS   = "DISPLAYS"
    DEPENDS_ON = "DEPENDS_ON"

    # NEW typed edges
    JOINS      = "JOINS"      # JOIN between two tables / views
    GENERATES  = "GENERATES"  # pipeline / job GENERATES a table or artifact
    REFRESHES  = "REFRESHES"  # semantic model REFRESHES from a table
    PUBLISHES  = "PUBLISHES"  # pipeline PUBLISHES to an API / topic
    OWNS       = "OWNS"       # catalog object OWNS a schema / table
    CONTAINS   = "CONTAINS"   # schema CONTAINS a table; notebook CONTAINS a cell
    TRIGGERS   = "TRIGGERS"   # pipeline TRIGGERS a task; task TRIGGERS another task
    INGESTS_TO = "INGESTS_TO" # ADLS file INGESTS_TO a Delta / Bronze table


# ---------------------------------------------------------------------------
# Criticality
# ---------------------------------------------------------------------------


class Criticality(str, Enum):
    """Business criticality rating for an enterprise asset."""

    LOW      = "low"
    MEDIUM   = "medium"
    HIGH     = "high"
    CRITICAL = "critical"


# ---------------------------------------------------------------------------
# Asset
# ---------------------------------------------------------------------------


@dataclass
class Asset:
    """A node in the Enterprise Metadata Graph.

    Required fields
    ---------------
    id          — globally unique identifier, e.g. ``"db_table::dbo::orders"``
    name        — human-readable name, e.g. ``"orders"``
    asset_type  — :class:`AssetType` enum value
    system      — :class:`SystemType` enum value

    Optional enrichment fields (default to safe empty values)
    ----------------------------------------------------------
    catalog      — database / catalog name (e.g. ``"hive_metastore"``)
    schema       — schema / namespace (e.g. ``"dbo"``, ``"public"``)
    owner        — owning team or person (e.g. ``"data-engineering"``)
    criticality  — :class:`Criticality` enum value
    tags         — free-form classification labels
    source_file  — the file this asset was parsed from (for lineage / debugging)
    line_number  — line in *source_file* where the definition starts
    metadata     — arbitrary key/value bag for parser-specific fields
    properties   — ← existing field (kept for backward compatibility)

    Backward compatibility
    ----------------------
    ``properties`` is unchanged so all existing adapter code continues to work.
    New fields default to ``None`` / empty so existing callers do not need to
    supply them.
    """

    # Required (same as before)
    id:         str
    name:       str
    asset_type: AssetType
    system:     SystemType

    # Existing optional field — unchanged
    properties: Dict[str, Any] = field(default_factory=dict)

    # New enrichment fields
    catalog:     Optional[str]         = field(default=None)
    schema:      Optional[str]         = field(default=None)
    owner:       Optional[str]         = field(default=None)
    criticality: Criticality           = field(default=Criticality.MEDIUM)
    tags:        List[str]             = field(default_factory=list)
    source_file: Optional[str]         = field(default=None)
    line_number: Optional[int]         = field(default=None)
    metadata:    Dict[str, Any]        = field(default_factory=dict)

    def fully_qualified_name(self) -> str:
        """Return ``catalog.schema.name`` where available, else just ``name``."""
        parts = [p for p in (self.catalog, self.schema, self.name) if p]
        return ".".join(parts)

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-serialisable representation of this asset."""
        return {
            "id":           self.id,
            "name":         self.name,
            "asset_type":   self.asset_type.value,
            "system":       self.system.value,
            "catalog":      self.catalog,
            "schema":       self.schema,
            "owner":        self.owner,
            "criticality":  self.criticality.value,
            "tags":         self.tags,
            "source_file":  self.source_file,
            "line_number":  self.line_number,
            "metadata":     self.metadata,
            "properties":   self.properties,
        }


# ---------------------------------------------------------------------------
# Relationship
# ---------------------------------------------------------------------------


@dataclass
class Relationship:
    """A directed, typed edge between two :class:`Asset` nodes.

    The ``source`` and ``target`` fields hold asset IDs (not Asset objects)
    so the graph can contain relationships that reference assets not yet loaded
    (dangling references are detected by the validator).

    Existing fields are unchanged.  ``properties`` is kept for backward compat.
    """

    source:       str
    target:       str
    relationship: RelationshipType
    properties:   Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source":       self.source,
            "target":       self.target,
            "relationship": self.relationship.value,
            "properties":   self.properties,
        }
