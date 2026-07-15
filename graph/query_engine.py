"""
graph.query_engine
==================
Enterprise Impact Search Engine — graph traversal utilities built on top of
:class:`~graph.enterprise_graph.EnterpriseGraph`.

All traversals use iterative BFS (no recursion).  An adjacency index is built
once at construction time so that neighbour lookups are O(1) rather than O(R)
(where R = total relationship count).

Public API
----------
::

    from graph.query_engine import EnterpriseQueryEngine

    query  = EnterpriseQueryEngine(graph)

    # All downstream assets (BFS, deduplicated)
    assets = query.find_downstream("column::sales_dashboard::Revenue")

    # All upstream assets (BFS, deduplicated)
    assets = query.find_upstream("report::page_exec")

    # Full impact grouped by enterprise system
    impact = query.find_full_impact("column::sales_dashboard::Revenue")
    # → {"source": "...", "database": [], "sql": [], "powerbi": [...], ...}

    # All root-to-leaf dependency paths
    paths  = query.find_dependency_paths("column::sales_dashboard::Revenue")
    # → [["column::...", "measure::...", "report::..."], ...]

Complexity
----------
Let V = |assets|, E = |relationships|.

* Construction (index build)  : O(E)
* find_downstream             : O(V + E)
* find_upstream               : O(V + E)
* find_full_impact            : O(V + E)   (one BFS pass)
* find_dependency_paths       : O(V * P)   where P = number of distinct paths
                                (worst-case exponential for dense graphs,
                                 typical enterprise graphs are sparse DAGs)
"""

from __future__ import annotations

import logging
from collections import defaultdict, deque
from typing import Dict, List, Set

from graph.enterprise_graph import EnterpriseGraph
from graph.models import Asset, SystemType

logger = logging.getLogger(__name__)


class EnterpriseQueryEngine:
    """Graph traversal engine for impact analysis over an
    :class:`~graph.enterprise_graph.EnterpriseGraph`.

    An internal adjacency index is built once in the constructor so every
    subsequent query is O(V + E) rather than O(V * R).

    Parameters
    ----------
    graph:
        A fully populated :class:`~graph.enterprise_graph.EnterpriseGraph`
        (e.g. as returned by ``MetadataEngine.get_enterprise_graph()``).
    """

    def __init__(self, graph: EnterpriseGraph) -> None:
        self._graph = graph

        # Forward adjacency: source_id → [target_id, ...]
        self._forward: Dict[str, List[str]] = defaultdict(list)
        # Reverse adjacency: target_id → [source_id, ...]
        self._reverse: Dict[str, List[str]] = defaultdict(list)

        for rel in graph.relationships:
            self._forward[rel.source].append(rel.target)
            self._reverse[rel.target].append(rel.source)

        logger.debug(
            "EnterpriseQueryEngine ready — %d assets, %d relationships",
            len(graph.assets),
            len(graph.relationships),
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def find_downstream(self, asset_id: str) -> List[Asset]:
        """Return every asset reachable *downstream* from *asset_id*.

        Traversal follows the directed edges in their natural direction
        (source → target).  BFS order is preserved.  The source asset
        itself is not included in the result.

        Parameters
        ----------
        asset_id:
            The ID of the starting asset (e.g. ``"column::sales_dashboard::Revenue"``).

        Returns
        -------
        list[Asset]
            Deduplicated list of downstream :class:`~graph.models.Asset` objects
            in BFS order.  Assets whose IDs are not present in the graph are
            silently skipped.

        Complexity: O(V + E)
        """
        visited_ids = self._bfs(asset_id, self._forward)
        return self._resolve(visited_ids)

    def find_upstream(self, asset_id: str) -> List[Asset]:
        """Return every asset reachable *upstream* from *asset_id*.

        Traversal follows edges in reverse (target → source).  BFS order
        is preserved.  The source asset itself is not included.

        Parameters
        ----------
        asset_id:
            The ID of the starting asset.

        Returns
        -------
        list[Asset]
            Deduplicated list of upstream :class:`~graph.models.Asset` objects
            in BFS order.

        Complexity: O(V + E)
        """
        visited_ids = self._bfs(asset_id, self._reverse)
        return self._resolve(visited_ids)

    def find_full_impact(self, asset_id: str) -> Dict[str, object]:
        """Return the complete downstream impact grouped by enterprise system.

        Performs a single BFS pass over the forward graph starting from
        *asset_id*.  The result asset lists are ordered BFS-first within
        each system bucket.

        Parameters
        ----------
        asset_id:
            The ID of the asset whose downstream impact is being assessed.

        Returns
        -------
        dict with the structure::

            {
                "source":      "<asset_id>",
                "database":    [<Asset>, ...],
                "sql":         [<Asset>, ...],
                "databricks":  [<Asset>, ...],
                "pipeline":    [<Asset>, ...],
                "powerbi":     [<Asset>, ...],
                "api":         [<Asset>, ...]
            }

        Every :class:`~graph.models.SystemType` value is always present as a
        key even when its list is empty, so callers can index without key-
        existence checks.

        Complexity: O(V + E)
        """
        downstream = self.find_downstream(asset_id)

        # Initialise a bucket for every SystemType so the result is always
        # complete regardless of which systems are actually present.
        buckets: Dict[str, List[Asset]] = {s.value: [] for s in SystemType}
        for asset in downstream:
            buckets[asset.system.value].append(asset)

        result: Dict[str, object] = {"source": asset_id}
        result.update(buckets)
        return result

    def find_dependency_paths(self, asset_id: str) -> List[List[str]]:
        """Return all root-to-leaf dependency paths starting from *asset_id*.

        Each path is a list of asset IDs beginning with *asset_id* and
        ending at a leaf (an asset with no outgoing edges in the forward
        graph).  Cycles are broken by refusing to revisit an ID that already
        appears in the current path.

        This iterative DFS uses an explicit stack of ``(current_id, path)``
        tuples, avoiding recursion and Python's call-stack limit.

        Parameters
        ----------
        asset_id:
            The ID of the starting asset.

        Returns
        -------
        list[list[str]]
            All distinct root-to-leaf paths as lists of asset ID strings.
            Returns ``[[asset_id]]`` when the asset has no downstream
            neighbours (i.e. it is already a leaf).

        Complexity: O(V * P) where P is the number of distinct paths.
        For typical sparse enterprise DAGs this is effectively O(V + E).
        """
        if asset_id not in self._graph.assets and not self._forward.get(asset_id):
            logger.debug("find_dependency_paths: asset '%s' not found", asset_id)
            return []

        completed_paths: List[List[str]] = []
        # Stack entries: (current_node_id, path_so_far)
        stack: deque[tuple[str, List[str]]] = deque()
        stack.append((asset_id, [asset_id]))

        while stack:
            current_id, path = stack.pop()
            neighbours = self._forward.get(current_id, [])

            if not neighbours:
                # Leaf node — record this complete path
                completed_paths.append(path)
                continue

            for neighbour_id in neighbours:
                # Cycle guard: do not revisit any node already in this path
                if neighbour_id in path:
                    logger.debug(
                        "Cycle detected: '%s' already in path — skipping",
                        neighbour_id,
                    )
                    completed_paths.append(path)  # treat loop-back as a leaf
                    continue
                stack.append((neighbour_id, path + [neighbour_id]))

        return completed_paths

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _bfs(self, start_id: str, adjacency: Dict[str, List[str]]) -> List[str]:
        """Iterative BFS over *adjacency* starting from *start_id*.

        Returns an ordered, deduplicated list of visited IDs, **excluding**
        *start_id* itself.

        Parameters
        ----------
        start_id:
            ID of the starting node.
        adjacency:
            Either ``self._forward`` or ``self._reverse``.

        Returns
        -------
        list[str]
            BFS-ordered IDs of visited nodes (start node excluded).
        """
        visited: Set[str] = {start_id}
        queue: deque[str] = deque([start_id])
        ordered: List[str] = []

        while queue:
            current = queue.popleft()
            for neighbour in adjacency.get(current, []):
                if neighbour not in visited:
                    visited.add(neighbour)
                    queue.append(neighbour)
                    ordered.append(neighbour)

        return ordered

    def _resolve(self, asset_ids: List[str]) -> List[Asset]:
        """Resolve a list of asset IDs to :class:`~graph.models.Asset` objects.

        IDs that are not present in the graph (e.g. dangling references) are
        silently skipped with a debug-level log message.
        """
        assets: List[Asset] = []
        for aid in asset_ids:
            asset = self._graph.assets.get(aid)
            if asset is not None:
                assets.append(asset)
            else:
                logger.debug("_resolve: asset id '%s' not found in graph", aid)
        return assets
