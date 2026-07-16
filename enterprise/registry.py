"""
enterprise.registry
===================
Multi-source enterprise graph builder.

The :class:`EnterpriseAssetRegistry` collects output from any number of
:class:`~enterprise.parsers.BaseMetadataParser` implementations and merges them
into a single :class:`~graph.enterprise_graph.EnterpriseGraph`.

After all parsers have been registered and :meth:`build` is called the registry:

1. Calls every parser's ``parse()`` method.
2. Deduplicates assets by ID (first writer wins).
3. Adds all relationships (cross-source relationships with ``unresolved=True``
   are kept so the validator can report dangling references separately from
   intentional forward references).
4. Returns the populated graph.

Usage
-----
::

    from enterprise.registry import EnterpriseAssetRegistry
    from enterprise.parsers import PowerBIParser, SQLParser, NotebookParser, PipelineParser

    registry = EnterpriseAssetRegistry()
    registry.register(PowerBIParser(payload=raw_payload, owner="bi-team"))
    registry.register(SQLParser(descriptors=sql_descs,   owner="dba-team"))
    registry.register(NotebookParser(descriptors=nb_descs))
    registry.register(PipelineParser(descriptors=pl_descs))

    graph = registry.build()
"""

from __future__ import annotations

import logging
from typing import List

from enterprise.parsers import BaseMetadataParser
from graph.enterprise_graph import EnterpriseGraph
from graph.models import Asset, Relationship

logger = logging.getLogger(__name__)


class EnterpriseAssetRegistry:
    """Aggregates multiple parser outputs into one :class:`EnterpriseGraph`.

    Parameters
    ----------
    parsers:
        Optional initial list of :class:`~enterprise.parsers.BaseMetadataParser`
        instances.  Additional parsers can be added later via :meth:`register`.
    """

    def __init__(self, parsers: List[BaseMetadataParser] | None = None) -> None:
        self._parsers: List[BaseMetadataParser] = list(parsers or [])

    def register(self, parser: BaseMetadataParser) -> "EnterpriseAssetRegistry":
        """Add *parser* to the registry.

        Returns ``self`` so calls can be chained::

            registry.register(p1).register(p2).register(p3)
        """
        self._parsers.append(parser)
        return self

    def build(self) -> EnterpriseGraph:
        """Run all registered parsers and return the merged graph.

        Asset deduplication: if two parsers produce an asset with the same ID
        the first one takes precedence (parsers are processed in registration
        order).  A warning is logged for each duplicate.

        Returns
        -------
        EnterpriseGraph
            A fully populated graph containing all assets and relationships
            from every registered parser.
        """
        graph = EnterpriseGraph()
        total_assets = 0
        total_rels = 0
        duplicates = 0

        for parser in self._parsers:
            parser_name = parser.__class__.__name__
            try:
                assets, relationships = parser.parse()
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "EnterpriseAssetRegistry: parser %s raised %s — skipping",
                    parser_name, exc, exc_info=True,
                )
                continue

            for asset in assets:
                if asset.id in graph.assets:
                    logger.warning(
                        "EnterpriseAssetRegistry: duplicate asset id %r "
                        "(from %s) — first writer wins",
                        asset.id, parser_name,
                    )
                    duplicates += 1
                else:
                    graph.add_asset(asset)
                    total_assets += 1

            for rel in relationships:
                graph.add_relationship(rel)
                total_rels += 1

        logger.info(
            "EnterpriseAssetRegistry.build(): %d assets, %d relationships, "
            "%d duplicate IDs skipped, %d parsers",
            total_assets, total_rels, duplicates, len(self._parsers),
        )
        return graph
