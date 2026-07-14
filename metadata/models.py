"""
metadata.models
===============
Pydantic data models for every metadata artifact produced by the
Metadata Engine.  All models are immutable by default (frozen=True)
so they can be safely passed across async contexts without defensive
copying.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class DependencyType(str, Enum):
    """Categories of inferred dependency edges in the dependency graph."""

    MEASURE_USES_COLUMN = "measure_uses_column"
    MEASURE_USES_MEASURE = "measure_uses_measure"
    MEASURE_USES_TABLE = "measure_uses_table"
    COLUMN_BELONGS_TO_TABLE = "column_belongs_to_table"
    RELATIONSHIP_LINKS_TABLES = "relationship_links_tables"
    REPORT_USES_MEASURE = "report_uses_measure"
    REPORT_USES_COLUMN = "report_uses_column"
    REPORT_USES_TABLE = "report_uses_table"


class ColumnDataType(str, Enum):
    """Normalised column data types extracted from TMDL."""

    STRING = "string"
    INT64 = "int64"
    DOUBLE = "double"
    DATETIME = "dateTime"
    BOOLEAN = "boolean"
    DECIMAL = "decimal"
    UNKNOWN = "unknown"


class CrossFilterBehavior(str, Enum):
    SINGLE = "singleDirection"
    BOTH = "bothDirections"


# ---------------------------------------------------------------------------
# Core metadata models
# ---------------------------------------------------------------------------


class TableMetadata(BaseModel):
    """Represents a single table in the Power BI semantic model."""

    model_config = {"frozen": True}

    name: str = Field(..., description="Table name as defined in TMDL.")
    lineage_tag: Optional[str] = Field(None, description="TMDL lineageTag GUID.")
    source_type: str = Field(
        "unknown",
        description="Partition mode, e.g. 'import', 'directQuery', 'calculated'.",
    )
    is_hidden: bool = Field(False, description="True when the table is hidden from report view.")
    is_date_table: bool = Field(False, description="True when the table is a date/calendar table.")
    display_folder: Optional[str] = Field(None)
    description: Optional[str] = Field(None)
    annotations: Dict[str, str] = Field(default_factory=dict)


class ColumnMetadata(BaseModel):
    """Represents a single column within a table."""

    model_config = {"frozen": True}

    table_name: str = Field(..., description="Parent table name.")
    name: str = Field(..., description="Column name.")
    data_type: str = Field(ColumnDataType.UNKNOWN, description="Normalised data type.")
    lineage_tag: Optional[str] = Field(None)
    is_hidden: bool = Field(False)
    is_key: bool = Field(False, description="True when used as a relationship key.")
    summarize_by: Optional[str] = Field(None)
    display_folder: Optional[str] = Field(None)
    data_category: Optional[str] = Field(
        None, description="Geographic or temporal category annotation."
    )
    sort_by_column: Optional[str] = Field(None)
    description: Optional[str] = Field(None)
    annotations: Dict[str, str] = Field(default_factory=dict)

    @property
    def qualified_name(self) -> str:
        """Return ``table_name[column_name]`` qualified identifier."""
        return f"{self.table_name}[{self.name}]"


class MeasureMetadata(BaseModel):
    """Represents a DAX measure."""

    model_config = {"frozen": True}

    table_name: str = Field(..., description="Host table (usually '_Measures').")
    name: str = Field(..., description="Measure name.")
    expression: str = Field(..., description="Raw DAX expression.")
    format_string: Optional[str] = Field(None)
    display_folder: Optional[str] = Field(None)
    lineage_tag: Optional[str] = Field(None)
    description: Optional[str] = Field(None)
    annotations: Dict[str, str] = Field(default_factory=dict)
    # Populated by DependencyBuilder — not parsed directly from TMDL.
    referenced_tables: List[str] = Field(
        default_factory=list,
        description="Table names inferred from DAX expression.",
    )
    referenced_columns: List[str] = Field(
        default_factory=list,
        description="Qualified column names inferred from DAX expression.",
    )
    referenced_measures: List[str] = Field(
        default_factory=list,
        description="Measure names inferred from DAX expression.",
    )

    @property
    def qualified_name(self) -> str:
        return f"[{self.name}]"


class RelationshipMetadata(BaseModel):
    """Represents a relationship between two table columns."""

    model_config = {"frozen": True}

    relationship_id: str = Field(..., description="TMDL relationship GUID or name.")
    from_table: str = Field(..., description="Many-side table name.")
    from_column: str = Field(..., description="Many-side column name.")
    to_table: str = Field(..., description="One-side table name.")
    to_column: str = Field(..., description="One-side column name.")
    is_active: bool = Field(True)
    cross_filter_behavior: str = Field(CrossFilterBehavior.SINGLE)
    from_cardinality: Optional[str] = Field(None)
    to_cardinality: Optional[str] = Field(None)
    join_on_date_behavior: Optional[str] = Field(None)


class VisualFieldRef(BaseModel):
    """A single field (column or measure) bound to a report visual."""

    model_config = {"frozen": True}

    entity: str = Field(..., description="Table or measure host entity name.")
    property_name: str = Field(..., description="Column or measure name.")
    query_ref: Optional[str] = Field(None)
    field_type: str = Field("unknown", description="'column' or 'measure'.")


class VisualMetadata(BaseModel):
    """A single visual container on a report page."""

    model_config = {"frozen": True}

    visual_name: str = Field(..., description="Visual container name / ID.")
    visual_type: str = Field(..., description="Power BI visual type identifier.")
    fields: List[VisualFieldRef] = Field(default_factory=list)
    position: Dict[str, Any] = Field(default_factory=dict)


class ReportMetadata(BaseModel):
    """Represents a single report page."""

    model_config = {"frozen": True}

    page_name: str = Field(..., description="Internal page name / ID.")
    display_name: str = Field(..., description="Human-readable tab label.")
    display_option: Optional[str] = Field(None)
    height: Optional[float] = Field(None)
    width: Optional[float] = Field(None)
    visuals: List[VisualMetadata] = Field(default_factory=list)
    # Aggregated field refs across all visuals — populated by DependencyBuilder.
    used_measures: List[str] = Field(default_factory=list)
    used_columns: List[str] = Field(default_factory=list)
    used_tables: List[str] = Field(default_factory=list)


class DependencyMetadata(BaseModel):
    """A directed dependency edge in the artifact graph."""

    model_config = {"frozen": True}

    source_type: str = Field(
        ...,
        description="Artifact type of the source node: 'measure', 'column', 'table', 'report'.",
    )
    source_name: str = Field(..., description="Qualified name of the source artifact.")
    target_type: str = Field(..., description="Artifact type of the target node.")
    target_name: str = Field(..., description="Qualified name of the target artifact.")
    dependency_type: DependencyType = Field(..., description="Semantic category of the edge.")
    metadata: Dict[str, Any] = Field(
        default_factory=dict,
        description="Optional bag of additional context (e.g. relationship_id).",
    )


# ---------------------------------------------------------------------------
# Top-level payload
# ---------------------------------------------------------------------------


class MetadataPayload(BaseModel):
    """Complete metadata snapshot returned by MetadataEngine.load()."""

    tables: List[TableMetadata] = Field(default_factory=list)
    columns: List[ColumnMetadata] = Field(default_factory=list)
    measures: List[MeasureMetadata] = Field(default_factory=list)
    relationships: List[RelationshipMetadata] = Field(default_factory=list)
    reports: List[ReportMetadata] = Field(default_factory=list)
    dependencies: List[DependencyMetadata] = Field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Serialise to a plain dict suitable for JSON responses."""
        return self.model_dump()
