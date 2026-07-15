"""
graph.adapter
=============
Converts a :class:`metadata.models.MetadataPayload` into an
:class:`graph.enterprise_graph.EnterpriseGraph`.

The adapter is the only coupling point between the metadata layer and the
graph layer.  It must not modify the payload it receives.

Mapping rules
-------------
Tables     → Asset(asset_type=TABLE,  system=POWERBI)
Columns    → Asset(asset_type=COLUMN, system=POWERBI)
           → Relationship(COLUMN → TABLE, type=DEPENDS_ON)
Measures   → Asset(asset_type=MEASURE, system=POWERBI)
           → Relationship(MEASURE → TABLE, type=DEPENDS_ON)  for each referenced table
           → Relationship(MEASURE → COLUMN, type=USES)       for each referenced column
           → Relationship(MEASURE → MEASURE, type=DEPENDS_ON) for each referenced measure
Reports    → Asset(asset_type=REPORT, system=POWERBI)
           → Relationship(REPORT → MEASURE, type=DISPLAYS)   for each used measure
           → Relationship(REPORT → TABLE,   type=USES)       for each used table
Model rels → Relationship(TABLE → TABLE,    type=REFERENCES) preserving from/to direction
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from graph.enterprise_graph import EnterpriseGraph
from graph.models import Asset, AssetType, Relationship, RelationshipType, SystemType

if TYPE_CHECKING:
    from metadata.models import MetadataPayload

logger = logging.getLogger(__name__)


class MetadataAdapter:
    """Converts a :class:`~metadata.models.MetadataPayload` into an
    :class:`~graph.enterprise_graph.EnterpriseGraph`.

    All methods are static — the class is a namespace, not a stateful object.
    """

    @staticmethod
    def to_enterprise_graph(metadata: "MetadataPayload") -> EnterpriseGraph:
        """Build and return an :class:`EnterpriseGraph` from *metadata*.

        Parameters
        ----------
        metadata:
            A fully populated ``MetadataPayload`` (as returned by
            ``MetadataEngine.load()``).

        Returns
        -------
        EnterpriseGraph
            A new graph instance populated with assets and relationships
            derived from the payload.  The payload itself is not modified.
        """
        graph = EnterpriseGraph()

        # ------------------------------------------------------------------
        # Tables
        # ------------------------------------------------------------------
        for table in metadata.tables:
            asset_id = f"table::{table.name}"
            graph.add_asset(Asset(
                id=asset_id,
                name=table.name,
                asset_type=AssetType.TABLE,
                system=SystemType.POWERBI,
                properties={
                    "source_type": table.source_type,
                    "is_hidden": table.is_hidden,
                    "is_date_table": table.is_date_table,
                    "description": table.description,
                },
            ))

        # ------------------------------------------------------------------
        # Columns — each column belongs to its parent table
        # ------------------------------------------------------------------
        for column in metadata.columns:
            asset_id = f"column::{column.table_name}::{column.name}"
            graph.add_asset(Asset(
                id=asset_id,
                name=column.name,
                asset_type=AssetType.COLUMN,
                system=SystemType.POWERBI,
                properties={
                    "table_name": column.table_name,
                    "data_type": column.data_type,
                    "is_hidden": column.is_hidden,
                    "is_key": column.is_key,
                },
            ))
            # COLUMN --DEPENDS_ON--> TABLE
            graph.add_relationship(Relationship(
                source=asset_id,
                target=f"table::{column.table_name}",
                relationship=RelationshipType.DEPENDS_ON,
            ))

        # ------------------------------------------------------------------
        # Measures
        # ------------------------------------------------------------------
        for measure in metadata.measures:
            asset_id = f"measure::{measure.name}"
            graph.add_asset(Asset(
                id=asset_id,
                name=measure.name,
                asset_type=AssetType.MEASURE,
                system=SystemType.POWERBI,
                properties={
                    "table_name": measure.table_name,
                    "display_folder": measure.display_folder,
                    "description": measure.description,
                },
            ))
            # MEASURE --DEPENDS_ON--> referenced tables
            for ref_table in measure.referenced_tables:
                graph.add_relationship(Relationship(
                    source=asset_id,
                    target=f"table::{ref_table}",
                    relationship=RelationshipType.DEPENDS_ON,
                ))
            # MEASURE --USES--> referenced columns
            for ref_col in measure.referenced_columns:
                # ref_col is qualified "TableName[ColumnName]"
                parts = ref_col.rstrip("]").split("[", 1)
                if len(parts) == 2:
                    col_id = f"column::{parts[0]}::{parts[1]}"
                    graph.add_relationship(Relationship(
                        source=asset_id,
                        target=col_id,
                        relationship=RelationshipType.USES,
                    ))
            # MEASURE --DEPENDS_ON--> referenced measures
            for ref_measure in measure.referenced_measures:
                graph.add_relationship(Relationship(
                    source=asset_id,
                    target=f"measure::{ref_measure}",
                    relationship=RelationshipType.DEPENDS_ON,
                ))

        # ------------------------------------------------------------------
        # Model relationships (table-to-table cardinality links)
        # ------------------------------------------------------------------
        for rel in metadata.relationships:
            graph.add_relationship(Relationship(
                source=f"table::{rel.from_table}",
                target=f"table::{rel.to_table}",
                relationship=RelationshipType.REFERENCES,
                properties={
                    "relationship_id": rel.relationship_id,
                    "from_column": rel.from_column,
                    "to_column": rel.to_column,
                    "is_active": rel.is_active,
                },
            ))

        # ------------------------------------------------------------------
        # Report pages
        # ------------------------------------------------------------------
        for report in metadata.reports:
            asset_id = f"report::{report.page_name}"
            graph.add_asset(Asset(
                id=asset_id,
                name=report.display_name,
                asset_type=AssetType.REPORT,
                system=SystemType.POWERBI,
                properties={
                    "page_name": report.page_name,
                    "display_name": report.display_name,
                },
            ))
            # REPORT --DISPLAYS--> used measures
            for measure_name in report.used_measures:
                graph.add_relationship(Relationship(
                    source=asset_id,
                    target=f"measure::{measure_name}",
                    relationship=RelationshipType.DISPLAYS,
                ))
            # REPORT --USES--> used tables
            for table_name in report.used_tables:
                graph.add_relationship(Relationship(
                    source=asset_id,
                    target=f"table::{table_name}",
                    relationship=RelationshipType.USES,
                ))

        logger.info(
            "MetadataAdapter: built EnterpriseGraph with %d assets and %d relationships",
            len(graph.assets),
            len(graph.relationships),
        )
        return graph
