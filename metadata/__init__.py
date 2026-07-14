"""
metadata
========
Metadata Engine for the Enterprise Change Orchestrator.

Public API
----------
    from metadata import MetadataEngine

    engine = MetadataEngine(semantic_model_path="sales.SemanticModel", report_path="sales.Report")
    payload = engine.load()
"""

from metadata.loader import MetadataEngine
from metadata.models import (
    TableMetadata,
    ColumnMetadata,
    MeasureMetadata,
    RelationshipMetadata,
    ReportMetadata,
    DependencyMetadata,
    MetadataPayload,
)

__all__ = [
    "MetadataEngine",
    "TableMetadata",
    "ColumnMetadata",
    "MeasureMetadata",
    "RelationshipMetadata",
    "ReportMetadata",
    "DependencyMetadata",
    "MetadataPayload",
]
