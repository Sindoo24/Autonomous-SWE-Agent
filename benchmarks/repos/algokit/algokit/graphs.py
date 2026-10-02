"""Graph algorithms on adjacency-list graphs.

A graph is a mapping ``node -> iterable of neighbours``. Weighted graphs map
``node -> iterable of (neighbour, weight)`` pairs. Nodes only need to be hashable; nodes that
appear only as neighbours are treated as having no outgoing edges.
"""

from __future__ import annotations

import heapq
from collections import deque
from itertools import count
from typing import Dict, Hashable, Iterable, List, Mapping, Optional, Tuple

Node = Hashable
Graph = Mapping[Node, Iterable[Node]]
WeightedGraph = Mapping[Node, Iterable[Tuple[Node, float]]]


class CycleError(ValueError):
    """Raised when a topological order is requested for a graph containing a cycle."""


def from_edges(edges: Iterable[Tuple[Node, Node]], directed: bool = True) -> Dict[Node, List[Node]]:
    """Build an adjacency list from ``(u, v)`` pairs. Every endpoint becomes a key."""
    adjacency: Dict[Node, List[Node]] = {}
    for u, v in edges:
        adjacency.setdefault(u, []).append(v)
        adjacency.setdefault(v, [])
        if not directed:
            adjacency[v].append(u)
    return adjacency


def bfs_order(graph: Graph, start: Node) -> List[Node]:
    """Return nodes reachable from ``start`` in breadth-first order."""
    seen = {start}
    order = []
    queue = deque([start])
    while queue:
        node = queue.popleft()
        order.append(node)
        for neighbour in graph.get(node, ()):
            if neighbour not in seen:
                seen.add(neighbour)
                queue.append(neighbour)
    return order


def shortest_hops(graph: Graph, start: Node, goal: Node) -> Optional[List[Node]]:
    """Return a path from ``start`` to ``goal`` with the fewest edges, or ``None``."""
    parents: Dict[Node, Optional[Node]] = {start: None}
    queue = deque([start])
    while queue:
        node = queue.popleft()
        if node == goal:
            return _walk_back(parents, goal)
        for neighbour in graph.get(node, ()):
            if neighbour not in parents:
                parents[neighbour] = node
                queue.append(neighbour)
    return None


def dijkstra(graph: WeightedGraph, source: Node) -> Tuple[Dict[Node, float], Dict[Node, Node]]:
    """Single-source shortest paths for non-negative edge weights.

    Returns ``(distances, predecessors)``. Unreachable nodes are absent from both mappings.
    """
    distances: Dict[Node, float] = {source: 0}
    predecessors: Dict[Node, Node] = {}
    tie = count()  # keeps heap entries comparable when nodes are not orderable
    heap = [(0, next(tie), source)]
    settled = set()
    while heap:
        dist, _, node = heapq.heappop(heap)
        if node in settled:
            continue
        settled.add(node)
        for neighbour, weight in graph.get(node, ()):
            if weight < 0:
                raise ValueError(f"negative edge weight {weight} on {node!r} -> {neighbour!r}")
            candidate = dist + weight
            if neighbour not in distances or candidate < distances[neighbour]:
                distances[neighbour] = candidate
                predecessors[neighbour] = node
                heapq.heappush(heap, (candidate, next(tie), neighbour))
    return distances, predecessors


def shortest_path(graph: WeightedGraph, source: Node, target: Node) -> Tuple[float, List[Node]]:
    """Return ``(cost, path)`` of the cheapest path; raises ``KeyError`` if unreachable."""
    distances, predecessors = dijkstra(graph, source)
    if target not in distances:
        raise KeyError(f"{target!r} is not reachable from {source!r}")
    parents: Dict[Node, Optional[Node]] = {source: None, **predecessors}
    return distances[target], _walk_back(parents, target)


def topological_sort(graph: Graph) -> List[Node]:
    """Return a topological order (Kahn's algorithm).

    Ties are broken by the order in which nodes first appear in ``graph``, so the result is
    deterministic. Raises :class:`CycleError` if the graph has a cycle.
    """
    indegree: Dict[Node, int] = {}
    for node, neighbours in graph.items():
        indegree.setdefault(node, 0)
        for neighbour in neighbours:
            indegree[neighbour] = indegree.get(neighbour, 0) + 1
    ready = deque(node for node, degree in indegree.items() if degree == 0)
    order = []
    while ready:
        node = ready.popleft()
        order.append(node)
        for neighbour in graph.get(node, ()):
            indegree[neighbour] -= 1
            if indegree[neighbour] == 0:
                ready.append(neighbour)
    if len(order) != len(indegree):
        raise CycleError("graph contains a cycle")
    return order


def _walk_back(parents: Mapping[Node, Optional[Node]], node: Node) -> List[Node]:
    path = []
    cursor: Optional[Node] = node
    while cursor is not None:
        path.append(cursor)
        cursor = parents[cursor]
    path.reverse()
    return path
