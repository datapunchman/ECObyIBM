"""
enterprise.validator
====================
Graph validation — detect structural issues without raising exceptions.

All checks are non-fatal.  The validator returns a :class:`ValidationReport`
containing categorised warnings.  Callers decide how to react.

Checks
------
1. **Cycles** — any directed cycle in the graph (detected via DFS colour-marking).
2. **Broken references** — relationship endpoints whose IDs do not exist as assets.
3. **Duplicate IDs** — asset IDs that appear more than once (registry deduplicates,
   but if a graph is built manually this can occur).
4. **Dangling assets** — assets with no incoming OR outgoing relationships (orphans).
5. **Disconnected subgraphs** — groups of assets that share no edges with the main
   component (detected via undirected BFS from the most-connected node).

Usage
-----
::

    from enterprise.validator import GraphValidator

    report = GraphValidator(graph).validate()
    if report.has_warnings:
        for w in report.warnings:
            print(w)
"""

from __future__ import annotations

import logging
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Dict, List, Set

from graph.enterprise_graph import EnterpriseGraph

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# ValidationReport
# ---------------------------------------------------------------------------


@dataclass
class ValidationReport:
    """Container for all warnings produced by :class:`GraphValidator`.

    Each warning is a plain string describing the issue and the affected
    asset IDs.  Categories are available as separate lists for structured
    access.
    """

    cycles:               List[str] = field(default_factory=list)
    broken_references:    List[str] = field(default_factory=list)
    duplicate_ids:        List[str] = field(default_factory=list)
    dangling_assets:      List[str] = field(default_factory=list)
    disconnected_subgraphs: List[str] = field(default_factory=list)

    @property
    def warnings(self) -> List[str]:
        """All warnings in a flat list."""
        return (
            self.cycles
            + self.broken_references
            + self.duplicate_ids
            + self.dangling_assets
            + self.disconnected_subgraphs
        )

    @property
    def has_warnings(self) -> bool:
        """``True`` when at least one warning was produced."""
        return bool(self.warnings)

    def summary(self) -> str:
        """Return a one-line summary of all warning counts."""
        return (
            f"cycles={len(self.cycles)}, "
            f"broken_refs={len(self.broken_references)}, "
            f"duplicates={len(self.duplicate_ids)}, "
            f"dangling={len(self.dangling_assets)}, "
            f"disconnected_subgraphs={len(self.disconnected_subgraphs)}"
        )


# ---------------------------------------------------------------------------
# GraphValidator
# ---------------------------------------------------------------------------


class GraphValidator:
    """Validates the structural integrity of an :class:`EnterpriseGraph`.

    All checks run in a single :meth:`validate` call.  Each check is
    implemented as a separate private method for testability.

    Parameters
    ----------
    graph:
        The graph to validate.
    """

    def __init__(self, graph: EnterpriseGraph) -> None:
        self._graph = graph
        # Pre-build adjacency for efficiency
        self._forward: Dict[str, List[str]] = defaultdict(list)
        self._reverse: Dict[str, List[str]] = defaultdict(list)
        for rel in graph.relationships:
            self._forward[rel.source].append(rel.target)
            self._reverse[rel.target].append(rel.source)

    def validate(self) -> ValidationReport:
        """Run all checks and return a :class:`ValidationReport`.

        Complexity: O(V + E) for each check; overall O(V + E).
        """
        report = ValidationReport()

        report.cycles               = self._check_cycles()
        report.broken_references    = self._check_broken_references()
        report.duplicate_ids        = self._check_duplicate_ids()
        report.dangling_assets      = self._check_dangling_assets()
        report.disconnected_subgraphs = self._check_disconnected_subgraphs()

        logger.info("GraphValidator: %s", report.summary())
        return report

    # ------------------------------------------------------------------
    # Check 1 — Cycles (iterative DFS, colour-marking)
    # ------------------------------------------------------------------

    def _check_cycles(self) -> List[str]:
        """Detect directed cycles using iterative DFS with three colours.

        Colours: WHITE (0) = unvisited, GRAY (1) = in-stack, BLACK (2) = done.
        A back-edge (current → GRAY node) signals a cycle.

        Returns
        -------
        list[str]
            One warning string per cycle detected, naming the back-edge.

        Complexity: O(V + E)
        """
        WHITE, GRAY, BLACK = 0, 1, 2
        colour: Dict[str, int] = {aid: WHITE for aid in self._graph.assets}
        warnings: List[str] = []

        for start in list(colour):
            if colour[start] != WHITE:
                continue
            # Iterative DFS: stack entries are (node_id, iterator_of_neighbours)
            stack: deque = deque()
            stack.append((start, iter(self._forward.get(start, []))))
            colour[start] = GRAY

            while stack:
                node, neighbours = stack[-1]
                try:
                    neighbour = next(neighbours)
                    n_colour = colour.get(neighbour, WHITE)
                    if n_colour == GRAY:
                        warnings.append(
                            f"Cycle detected: back-edge {node!r} → {neighbour!r}"
                        )
                        logger.warning("Cycle: %r → %r", node, neighbour)
                    elif n_colour == WHITE:
                        colour[neighbour] = GRAY
                        stack.append((neighbour, iter(self._forward.get(neighbour, []))))
                except StopIteration:
                    colour[node] = BLACK
                    stack.pop()

        return warnings

    # ------------------------------------------------------------------
    # Check 2 — Broken references
    # ------------------------------------------------------------------

    def _check_broken_references(self) -> List[str]:
        """Find relationship endpoints that do not exist as assets.

        Relationships with ``properties["unresolved"] == True`` are expected
        to reference assets from other sources and are excluded from warnings.

        Returns
        -------
        list[str]
            One warning per broken (non-unresolved) endpoint.

        Complexity: O(E)
        """
        asset_ids: Set[str] = set(self._graph.assets)
        warnings: List[str] = []

        for rel in self._graph.relationships:
            if rel.properties.get("unresolved"):
                continue
            if rel.source not in asset_ids:
                warnings.append(
                    f"Broken reference: source {rel.source!r} does not exist "
                    f"(in relationship → {rel.target!r})"
                )
            if rel.target not in asset_ids:
                warnings.append(
                    f"Broken reference: target {rel.target!r} does not exist "
                    f"(in relationship {rel.source!r} →)"
                )

        return warnings

    # ------------------------------------------------------------------
    # Check 3 — Duplicate IDs
    # ------------------------------------------------------------------

    def _check_duplicate_ids(self) -> List[str]:
        """Detect duplicate asset IDs.

        The :class:`~graph.enterprise_graph.EnterpriseGraph` uses a dict so
        duplicate keys are silently overwritten during normal operation.  This
        check surfaces any that were created before being added to the graph.

        In practice the registry deduplicates at ingest time, so this check
        targets manually-constructed graphs.

        Complexity: O(V)  — IDs are keys in a dict so duplicates are already
        collapsed; this check is retained as a documentation reminder and will
        always return empty for registry-built graphs.
        """
        # Since assets is a dict, IDs are already unique by definition.
        # We keep the check here for manual-graph validation and documentation.
        seen: Set[str] = set()
        duplicates: Set[str] = set()
        for aid in self._graph.assets:
            if aid in seen:
                duplicates.add(aid)
            seen.add(aid)

        return [f"Duplicate asset id: {aid!r}" for aid in sorted(duplicates)]

    # ------------------------------------------------------------------
    # Check 4 — Dangling assets (no edges)
    # ------------------------------------------------------------------

    def _check_dangling_assets(self) -> List[str]:
        """Detect assets that have no incoming and no outgoing relationships.

        These "orphan" assets are typically the result of a failed parse,
        a stale manifest, or a mis-configured cross-source reference.

        Returns
        -------
        list[str]
            One warning per asset with zero edges.

        Complexity: O(V + E)
        """
        connected: Set[str] = set()
        for rel in self._graph.relationships:
            connected.add(rel.source)
            connected.add(rel.target)

        warnings: List[str] = []
        for aid, asset in self._graph.assets.items():
            if aid not in connected:
                warnings.append(
                    f"Dangling asset: {aid!r} ({asset.asset_type.value}, "
                    f"{asset.system.value}) has no edges"
                )

        return warnings

    # ------------------------------------------------------------------
    # Check 5 — Disconnected subgraphs
    # ------------------------------------------------------------------

    def _check_disconnected_subgraphs(self) -> List[str]:
        """Detect disconnected components using undirected BFS.

        Treats all edges as undirected for this check.  If there are multiple
        components, each one that is not the largest is reported as a
        disconnected subgraph with the count of assets it contains.

        Returns
        -------
        list[str]
            One warning per non-main component.

        Complexity: O(V + E)
        """
        if not self._graph.assets:
            return []

        # Build undirected adjacency
        undirected: Dict[str, Set[str]] = defaultdict(set)
        for rel in self._graph.relationships:
            undirected[rel.source].add(rel.target)
            undirected[rel.target].add(rel.source)

        unvisited: Set[str] = set(self._graph.assets)
        components: List[Set[str]] = []

        while unvisited:
            start = next(iter(unvisited))
            component: Set[str] = set()
            queue: deque[str] = deque([start])
            while queue:
                node = queue.popleft()
                if node in component:
                    continue
                component.add(node)
                for neighbour in undirected.get(node, set()):
                    if neighbour not in component:
                        queue.append(neighbour)
            # Only mark as visited if in the graph (dangling rel targets may not be)
            unvisited -= {n for n in component if n in unvisited}
            components.append(component)

        if len(components) <= 1:
            return []

        # The largest component is assumed to be the main graph
        main = max(components, key=len)
        warnings: List[str] = []
        for comp in components:
            if comp is main:
                continue
            sample = sorted(comp)[:3]
            warnings.append(
                f"Disconnected subgraph: {len(comp)} asset(s) not connected to "
                f"main graph. Sample IDs: {sample}"
            )

        return warnings
