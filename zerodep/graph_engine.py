"""ZeroDep Graph Engine — directed dependency graph using Python stdlib only.

Provides:
  - add_node / add_edge
  - BFS / iterative DFS traversal
  - 3-color iterative cycle detection (no recursion limit risk)
  - Kahn's topological sort
  - Dijkstra shortest path (heapq)
  - in_degree / out_degree
  - Hub node detection
  - Connected components (weakly connected)

Zero third-party runtime dependencies.
"""

from collections import defaultdict, deque
import heapq
import sys
from typing import Dict, List, Optional, Set, Tuple


class ZeroDepGraph:

    def __init__(self) -> None:
        self._adj: Dict[str, Dict[str, dict]] = defaultdict(dict)
        self._in_degree: Dict[str, int] = defaultdict(int)

    # ──────────────────────────────────────────
    # Mutators
    # ──────────────────────────────────────────

    def add_node(self, node: str, **attrs) -> None:
        if node not in self._adj:
            self._adj[node] = {}
        if node not in self._in_degree:
            self._in_degree[node] = 0

    def add_edge(self, u: str, v: str, weight: float = 1.0) -> None:
        self.add_node(u)
        self.add_node(v)
        if v not in self._adj[u]:
            self._adj[u][v] = {"weight": weight}
            self._in_degree[v] += 1

    # ──────────────────────────────────────────
    # Accessors
    # ──────────────────────────────────────────

    def nodes(self) -> List[str]:
        return list(self._adj.keys())

    def neighbors(self, node: str) -> List[str]:
        return list(self._adj.get(node, {}).keys())

    def in_degree(self, node: str) -> int:
        return self._in_degree.get(node, 0)

    def out_degree(self, node: str) -> int:
        return len(self._adj.get(node, {}))

    def has_node(self, node: str) -> bool:
        return node in self._adj

    def has_edge(self, u: str, v: str) -> bool:
        return v in self._adj.get(u, {})

    def edge_count(self) -> int:
        return sum(len(v) for v in self._adj.values())

    # ──────────────────────────────────────────
    # Traversals
    # ──────────────────────────────────────────

    def bfs(self, start: str) -> List[str]:
        if start not in self._adj:
            return []
        visited: Set[str] = {start}
        queue: deque = deque([start])
        order: List[str] = []
        while queue:
            curr = queue.popleft()
            order.append(curr)
            for nxt in self._adj[curr]:
                if nxt not in visited:
                    visited.add(nxt)
                    queue.append(nxt)
        return order

    def dfs(self, start: str) -> List[str]:
        """Iterative DFS — no recursion limit risk."""
        if start not in self._adj:
            return []
        visited: Set[str] = set()
        order: List[str] = []
        stack = [start]
        while stack:
            u = stack.pop()
            if u in visited:
                continue
            visited.add(u)
            order.append(u)
            # Push in reverse order to preserve natural traversal order
            for v in reversed(list(self._adj[u])):
                if v not in visited:
                    stack.append(v)
        return order

    # ──────────────────────────────────────────
    # Cycle detection — iterative 3-color DFS
    # ──────────────────────────────────────────

    def find_cycles(self) -> List[List[str]]:
        """
        Iterative 3-colour cycle detection.

        Returns a list of cycles; each cycle is a list of node names forming
        a closed path. Duplicates across different DFS roots are excluded.
        Handles graphs with >1000 nodes safely (no sys.setrecursionlimit).
        """
        WHITE, GRAY, BLACK = 0, 1, 2
        color: Dict[str, int] = {u: WHITE for u in self._adj}
        cycles: List[List[str]] = []
        seen_cycle_keys: Set[frozenset] = set()

        for start in list(self._adj):
            if color[start] != WHITE:
                continue

            # Stack items: (node, iterator_over_neighbors, current_path)
            # We track path as a list and path_set as a set for O(1) lookup
            path: List[str] = []
            path_set: Set[str] = set()
            # Each frame: (node, neighbor_iterator)
            iter_stack: List[Tuple[str, "iterator"]] = []  # type: ignore[valid-type]

            color[start] = GRAY
            path.append(start)
            path_set.add(start)
            iter_stack.append((start, iter(self._adj[start])))

            while iter_stack:
                u, nbrs = iter_stack[-1]
                try:
                    v = next(nbrs)
                except StopIteration:
                    # Done with u — backtrack
                    color[u] = BLACK
                    path.pop()
                    path_set.discard(u)
                    iter_stack.pop()
                    continue

                if color[v] == GRAY and v in path_set:
                    # Found a back edge → cycle
                    cycle_start_idx = path.index(v)
                    cycle = path[cycle_start_idx:] + [v]
                    key = frozenset(cycle)
                    if key not in seen_cycle_keys:
                        seen_cycle_keys.add(key)
                        cycles.append(cycle)
                elif color[v] == WHITE:
                    color[v] = GRAY
                    path.append(v)
                    path_set.add(v)
                    iter_stack.append((v, iter(self._adj[v])))

        return cycles

    # ──────────────────────────────────────────
    # Topological sort — Kahn's algorithm
    # ──────────────────────────────────────────

    def topological_sort(self) -> Optional[List[str]]:
        """
        Kahn's algorithm. Returns ordered list or None if cycles exist.
        Stable: nodes with equal in-degree are processed in lexical order.
        """
        in_deg: Dict[str, int] = {u: self._in_degree[u] for u in self._adj}
        # Min-heap for stable ordering
        heap: List[str] = sorted(u for u in in_deg if in_deg[u] == 0)
        heapq.heapify(heap)
        result: List[str] = []

        while heap:
            u = heapq.heappop(heap)
            result.append(u)
            for v in sorted(self._adj[u]):
                in_deg[v] -= 1
                if in_deg[v] == 0:
                    heapq.heappush(heap, v)

        return result if len(result) == len(self._adj) else None

    # ──────────────────────────────────────────
    # Shortest path — Dijkstra
    # ──────────────────────────────────────────

    def shortest_path(
        self, start: str, target: str
    ) -> Optional[Tuple[float, List[str]]]:
        """Dijkstra's algorithm via heapq. Returns (distance, path) or None."""
        if start not in self._adj or target not in self._adj:
            return None

        dist: Dict[str, float] = {u: float("inf") for u in self._adj}
        dist[start] = 0.0
        prev: Dict[str, Optional[str]] = {u: None for u in self._adj}
        pq: List[Tuple[float, str]] = [(0.0, start)]

        while pq:
            d, u = heapq.heappop(pq)
            if d > dist[u]:
                continue
            if u == target:
                # Reconstruct path
                path: List[str] = []
                node: Optional[str] = target
                while node is not None:
                    path.append(node)
                    node = prev[node]
                path.reverse()
                return (dist[target], path)
            for v, attrs in self._adj[u].items():
                w = attrs.get("weight", 1.0)
                nd = d + w
                if nd < dist[v]:
                    dist[v] = nd
                    prev[v] = u
                    heapq.heappush(pq, (nd, v))

        return None

    # ──────────────────────────────────────────
    # Connected components (weakly connected)
    # ──────────────────────────────────────────

    def weakly_connected_components(self) -> List[List[str]]:
        """
        Union-Find based weakly-connected component detection.
        Treats all edges as undirected.
        """
        parent: Dict[str, str] = {u: u for u in self._adj}

        def find(x: str) -> str:
            while parent[x] != x:
                parent[x] = parent[parent[x]]  # path compression
                x = parent[x]
            return x

        def union(a: str, b: str) -> None:
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[ra] = rb

        for u in self._adj:
            for v in self._adj[u]:
                union(u, v)

        component_map: Dict[str, List[str]] = defaultdict(list)
        for node in self._adj:
            component_map[find(node)].append(node)

        return [sorted(members) for members in component_map.values()]

    # ──────────────────────────────────────────
    # Hub detection
    # ──────────────────────────────────────────

    def top_hub_nodes(self, k: int = 5) -> List[Tuple[str, int]]:
        """Return the k nodes with highest in-degree."""
        return sorted(
            ((n, self._in_degree[n]) for n in self._adj),
            key=lambda x: -x[1],
        )[:k]

    def max_dependency_depth(self) -> int:
        """Calculate the longest path depth across the DAG."""
        topo = self.topological_sort()
        if not topo:
            return -1  # Cycle present
        depth: Dict[str, int] = {u: 1 for u in self._adj}
        for u in topo:
            for v in self._adj[u]:
                if depth[u] + 1 > depth[v]:
                    depth[v] = depth[u] + 1
        return max(depth.values()) if depth else 0
