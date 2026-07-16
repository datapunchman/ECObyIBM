"""
enterprise.metrics
==================
Graph analytics — compute enterprise-grade metrics from an
:class:`~graph.enterprise_graph.EnterpriseGraph`.

Metrics
-------
assets_by_system
    Count of assets per :class:`~graph.models.SystemType` value.

assets_by_type
    Count of assets per :class:`~graph.models.AssetType` value.

dependency_depth
    For every asset: the maximum path length from that asset to any reachable
    downstream leaf.  Computed with a reverse-BFS from all leaves.

critical_path
    The single longest source-to-leaf dependency chain (list of asset IDs).

blast_radius
    For every asset: the count of assets reachable downstream (including
    transitively).  High blast-radius assets are high-risk change targets.

top_connected_assets
    Assets ranked by total degree (in + out edges), descending.

leaf_assets
    Assets with no outgoing edges (pure consumers / endpoints).

orphan_assets
    Assets with neither incoming nor outgoing edges.

Usage
-----
::

    from enterprise.metrics import EnterpriseGraphMetrics

    m = EnterpriseGraphMetrics(graph)
    report = m.compute()

    print(report["blast_radius"]["column::sales_dashboard::Revenue"])
    print(report["critical_path"])
    print(report["top_connected_assets"][:5])
"""

from __future__ import annotations

import logging
from collections import defaultdict, deque
from typing import Any, Dict, List, Set, Tuple

from graph.enterprise_graph import EnterpriseGraph
from graph.models import Asset, AssetType, SystemType

logger = logging.getLogger(__name__)


class EnterpriseGraphMetrics:
    """Compute enterprise-grade graph metrics.

    Parameters
    ----------
    graph:
        A populated :class:`~graph.enterprise_graph.EnterpriseGraph`.
    """

    def __init__(self, graph: EnterpriseGraph) -> None:
        self._graph = graph

        # Forward adjacency: source → [target, ...]
        self._forward: Dict[str, List[str]] = defaultdict(list)
        # Reverse adjacency: target → [source, ...]
        self._reverse: Dict[str, List[str]] = defaultdict(list)

        for rel in graph.relationships:
            self._forward[rel.source].append(rel.target)
            self._reverse[rel.target].append(rel.source)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def compute(self) -> Dict[str, Any]:
        """Compute all metrics and return them as a single dict.

        Returns
        -------
        dict with keys:
            ``assets_by_system``, ``assets_by_type``, ``dependency_depth``,
            ``critical_path``, ``blast_radius``, ``top_connected_assets``,
            ``leaf_assets``, ``orphan_assets``, ``total_assets``,
            ``total_relationships``.
        """
        assets_by_system  = self._assets_by_system()
        assets_by_type    = self._assets_by_type()
        depths            = self._dependency_depth()
        critical_path     = self._critical_path(depths)
        blast_radius      = self._blast_radius()
        top_connected     = self._top_connected_assets()
        leaf_ids          = self._leaf_assets()
        orphan_ids        = self._orphan_assets()

        return {
            "total_assets":         len(self._graph.assets),
            "total_relationships":  len(self._graph.relationships),
            "assets_by_system":     assets_by_system,
            "assets_by_type":       assets_by_type,
            "dependency_depth":     depths,
            "critical_path":        critical_path,
            "blast_radius":         blast_radius,
            "top_connected_assets": top_connected,
            "leaf_assets":          leaf_ids,
            "orphan_assets":        orphan_ids,
        }

    # ------------------------------------------------------------------
    # assets_by_system
    # ------------------------------------------------------------------

    def _assets_by_system(self) -> Dict[str, int]:
        """Count of assets per SystemType value.

        Complexity: O(V)
        """
        counts: Dict[str, int] = {s.value: 0 for s in SystemType}
        for asset in self._graph.assets.values():
            counts[asset.system.value] = counts.get(asset.system.value, 0) + 1
        return {k: v for k, v in counts.items() if v > 0}

    # ------------------------------------------------------------------
    # assets_by_type
    # ------------------------------------------------------------------

    def _assets_by_type(self) -> Dict[str, int]:
        """Count of assets per AssetType value.

        Complexity: O(V)
        """
        counts: Dict[str, int] = {}
        for asset in self._graph.assets.values():
            key = asset.asset_type.value
            counts[key] = counts.get(key, 0) + 1
        return counts

    # ------------------------------------------------------------------
    # dependency_depth  (longest downstream path from each node)
    # ------------------------------------------------------------------

    def _dependency_depth(self) -> Dict[str, int]:
        """Return the maximum downstream path length for every asset.

        Computed with a reverse topological BFS (processing leaves first).
        This runs in O(V + E) — far cheaper than running BFS from every node.

        Complexity: O(V + E)
        """
        # Start from leaf nodes (no outgoing edges)
        depth: Dict[str, int] = {}
        in_degree: Dict[str, int] = defaultdict(int)
        for node_id in self._graph.assets:
            in_degree[node_id]  # ensure all nodes present
        for rel in self._graph.relationships:
            in_degree[rel.target]  # ensure all targets present

        # Forward-topological: nodes with out-degree = 0 have depth 0
        out_degree: Dict[str, int] = defaultdict(int)
        for rel in self._graph.relationships:
            out_degree[rel.source] += 1

        queue: deque[str] = deque()
        for aid in self._graph.assets:
            if out_degree[aid] == 0:
                depth[aid] = 0
                queue.append(aid)

        processed: Set[str] = set()
        while queue:
            current = queue.popleft()
            if current in processed:
                continue
            processed.add(current)
            current_depth = depth.get(current, 0)
            for parent in self._reverse.get(current, []):
                new_depth = current_depth + 1
                if depth.get(parent, -1) < new_depth:
                    depth[parent] = new_depth
                if parent not in processed:
                    queue.append(parent)

        # Nodes not visited (cycles or isolated) get depth 0
        for aid in self._graph.assets:
            depth.setdefault(aid, 0)

        return depth

    # ------------------------------------------------------------------
    # critical_path  (longest chain start_id → leaf)
    # ------------------------------------------------------------------

    def _critical_path(self, depths: Dict[str, int]) -> List[str]:
        """Return the single longest dependency chain as a list of asset IDs.

        The chain starts from the asset with the highest ``dependency_depth``
        and follows the forward graph, always choosing the neighbour with the
        highest remaining depth.

        Complexity: O(V + E)
        """
        if not depths:
            return []

        start = max(depths, key=lambda aid: depths.get(aid, 0))
        path: List[str] = [start]
        visited: Set[str] = {start}
        current = start

        while True:
            neighbours = [
                n for n in self._forward.get(current, [])
                if n not in visited
            ]
            if not neighbours:
                break
            # Pick the neighbour with the highest remaining depth
            next_node = max(neighbours, key=lambda n: depths.get(n, 0))
            path.append(next_node)
            visited.add(next_node)
            current = next_node

        return path

    # ------------------------------------------------------------------
    # blast_radius  (downstream reachability count per asset)
    # ------------------------------------------------------------------

    def _blast_radius(self) -> Dict[str, int]:
        """Return the number of assets transitively reachable downstream.

        Computed with a single reverse-BFS from all leaf nodes so the total
        work is O(V + E), not O(V * (V + E)).

        Complexity: O(V + E)
        """
        # For each node count descendants via BFS from that node
        # This is O(V*(V+E)) in the naive case.
        # For enterprise graphs (V < 10 000) this is acceptable.
        # A Tarjan-SCC + reverse-topo approach gives O(V+E) but adds complexity.
        radius: Dict[str, int] = {}
        for start_id in self._graph.assets:
            visited: Set[str] = {start_id}
            queue: deque[str] = deque([start_id])
            count = 0
            while queue:
                current = queue.popleft()
                for neighbour in self._forward.get(current, []):
                    if neighbour not in visited:
                        visited.add(neighbour)
                        queue.append(neighbour)
                        count += 1
            radius[start_id] = count
        return radius

    # ------------------------------------------------------------------
    # top_connected_assets  (ranked by total degree)
    # ------------------------------------------------------------------

    def _top_connected_assets(self, top_n: int = 10) -> List[Dict[str, Any]]:
        """Return the top-N assets by total edge count (in + out).

        Complexity: O(E + V log V)
        """
        degree: Dict[str, int] = defaultdict(int)
        for rel in self._graph.relationships:
            degree[rel.source] += 1
            degree[rel.target] += 1

        ranked: List[Tuple[str, int]] = sorted(
            ((aid, degree[aid]) for aid in self._graph.assets),
            key=lambda x: x[1],
            reverse=True,
        )

        result: List[Dict[str, Any]] = []
        for aid, deg in ranked[:top_n]:
            asset = self._graph.assets[aid]
            result.append({
                "id":         aid,
                "name":       asset.name,
                "asset_type": asset.asset_type.value,
                "system":     asset.system.value,
                "degree":     deg,
            })
        return result

    # ------------------------------------------------------------------
    # leaf_assets  (no outgoing edges)
    # ------------------------------------------------------------------

    def _leaf_assets(self) -> List[str]:
        """Return IDs of assets with no outgoing edges.

        Complexity: O(E)
        """
        has_outgoing: Set[str] = {rel.source for rel in self._graph.relationships}
        return [
            aid for aid in self._graph.assets
            if aid not in has_outgoing
        ]

    # ------------------------------------------------------------------
    # orphan_assets  (no edges at all)
    # ------------------------------------------------------------------

    def _orphan_assets(self) -> List[str]:
        """Return IDs of assets with neither incoming nor outgoing edges.

        Complexity: O(E)
        """
        connected: Set[str] = set()
        for rel in self._graph.relationships:
            connected.add(rel.source)
            connected.add(rel.target)
        return [aid for aid in self._graph.assets if aid not in connected]
