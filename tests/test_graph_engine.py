"""Graph Engine Tests — covers all graph algorithms."""

import unittest
from zerodep.graph_engine import ZeroDepGraph


class TestBasicOperations(unittest.TestCase):

    def test_add_node(self):
        g = ZeroDepGraph()
        g.add_node("a")
        self.assertIn("a", g.nodes())

    def test_add_edge_creates_both_nodes(self):
        g = ZeroDepGraph()
        g.add_edge("a", "b")
        self.assertIn("a", g.nodes())
        self.assertIn("b", g.nodes())

    def test_in_degree(self):
        g = ZeroDepGraph()
        g.add_edge("a", "b")
        g.add_edge("c", "b")
        self.assertEqual(g.in_degree("b"), 2)
        self.assertEqual(g.in_degree("a"), 0)

    def test_out_degree(self):
        g = ZeroDepGraph()
        g.add_edge("a", "b")
        g.add_edge("a", "c")
        self.assertEqual(g.out_degree("a"), 2)

    def test_no_duplicate_edges(self):
        g = ZeroDepGraph()
        g.add_edge("a", "b")
        g.add_edge("a", "b")
        self.assertEqual(g.in_degree("b"), 1)

    def test_has_edge(self):
        g = ZeroDepGraph()
        g.add_edge("x", "y")
        self.assertTrue(g.has_edge("x", "y"))
        self.assertFalse(g.has_edge("y", "x"))

    def test_edge_count(self):
        g = ZeroDepGraph()
        g.add_edge("a", "b")
        g.add_edge("b", "c")
        self.assertEqual(g.edge_count(), 2)


class TestBFS(unittest.TestCase):

    def test_bfs_simple(self):
        g = ZeroDepGraph()
        g.add_edge("a", "b")
        g.add_edge("a", "c")
        g.add_edge("b", "d")
        order = g.bfs("a")
        # a should be first, d last
        self.assertEqual(order[0], "a")
        self.assertIn("d", order)

    def test_bfs_disconnected_node(self):
        g = ZeroDepGraph()
        g.add_edge("a", "b")
        g.add_node("z")
        order = g.bfs("a")
        self.assertNotIn("z", order)

    def test_bfs_missing_start(self):
        g = ZeroDepGraph()
        self.assertEqual(g.bfs("nonexistent"), [])


class TestDFS(unittest.TestCase):

    def test_dfs_visits_all_reachable(self):
        g = ZeroDepGraph()
        g.add_edge("a", "b")
        g.add_edge("b", "c")
        g.add_edge("a", "d")
        order = g.dfs("a")
        self.assertEqual(set(order), {"a", "b", "c", "d"})

    def test_dfs_no_repeated_visits(self):
        g = ZeroDepGraph()
        g.add_edge("a", "b")
        g.add_edge("a", "c")
        g.add_edge("b", "c")
        order = g.dfs("a")
        self.assertEqual(len(order), len(set(order)))


class TestCycleDetection(unittest.TestCase):

    def test_no_cycles_in_dag(self):
        g = ZeroDepGraph()
        g.add_edge("a", "b")
        g.add_edge("b", "c")
        self.assertEqual(g.find_cycles(), [])

    def test_simple_cycle(self):
        g = ZeroDepGraph()
        g.add_edge("a", "b")
        g.add_edge("b", "a")
        cycles = g.find_cycles()
        self.assertGreater(len(cycles), 0)

    def test_triangle_cycle(self):
        g = ZeroDepGraph()
        g.add_edge("a", "b")
        g.add_edge("b", "c")
        g.add_edge("c", "a")
        cycles = g.find_cycles()
        self.assertGreater(len(cycles), 0)

    def test_self_loop(self):
        g = ZeroDepGraph()
        g.add_edge("a", "a")
        cycles = g.find_cycles()
        self.assertGreater(len(cycles), 0)

    def test_multiple_independent_cycles(self):
        g = ZeroDepGraph()
        # Cycle 1: a<->b
        g.add_edge("a", "b")
        g.add_edge("b", "a")
        # Cycle 2: c<->d
        g.add_edge("c", "d")
        g.add_edge("d", "c")
        cycles = g.find_cycles()
        self.assertGreaterEqual(len(cycles), 2)

    def test_large_graph_no_stack_overflow(self):
        """Iterative detection must handle 2000 nodes without RecursionError."""
        g = ZeroDepGraph()
        for i in range(1999):
            g.add_edge(f"node{i}", f"node{i+1}")
        # No cycle
        cycles = g.find_cycles()
        self.assertEqual(cycles, [])


class TestTopologicalSort(unittest.TestCase):

    def test_topo_sort_dag(self):
        g = ZeroDepGraph()
        g.add_edge("a", "b")
        g.add_edge("a", "c")
        g.add_edge("b", "d")
        g.add_edge("c", "d")
        order = g.topological_sort()
        self.assertIsNotNone(order)
        self.assertLess(order.index("a"), order.index("b"))
        self.assertLess(order.index("b"), order.index("d"))

    def test_topo_sort_returns_none_for_cycle(self):
        g = ZeroDepGraph()
        g.add_edge("a", "b")
        g.add_edge("b", "a")
        self.assertIsNone(g.topological_sort())


class TestShortestPath(unittest.TestCase):

    def test_direct_path(self):
        g = ZeroDepGraph()
        g.add_edge("a", "b")
        result = g.shortest_path("a", "b")
        self.assertIsNotNone(result)
        dist, path = result
        self.assertEqual(path, ["a", "b"])
        self.assertEqual(dist, 1.0)

    def test_no_path(self):
        g = ZeroDepGraph()
        g.add_edge("a", "b")
        g.add_node("c")
        self.assertIsNone(g.shortest_path("a", "c"))

    def test_multi_hop_path(self):
        g = ZeroDepGraph()
        g.add_edge("a", "b")
        g.add_edge("b", "c")
        g.add_edge("a", "c", weight=5.0)
        result = g.shortest_path("a", "c")
        self.assertIsNotNone(result)
        dist, path = result
        # Shortest should be a->b->c (2.0) not a->c (5.0)
        self.assertAlmostEqual(dist, 2.0)
        self.assertEqual(path, ["a", "b", "c"])

    def test_missing_node_returns_none(self):
        g = ZeroDepGraph()
        self.assertIsNone(g.shortest_path("x", "y"))


class TestConnectedComponents(unittest.TestCase):

    def test_single_component(self):
        g = ZeroDepGraph()
        g.add_edge("a", "b")
        g.add_edge("b", "c")
        comps = g.weakly_connected_components()
        self.assertEqual(len(comps), 1)
        self.assertEqual(set(comps[0]), {"a", "b", "c"})

    def test_disconnected_components(self):
        g = ZeroDepGraph()
        g.add_edge("a", "b")
        g.add_edge("c", "d")
        comps = g.weakly_connected_components()
        self.assertEqual(len(comps), 2)

    def test_isolated_node(self):
        g = ZeroDepGraph()
        g.add_node("x")
        comps = g.weakly_connected_components()
        self.assertEqual(len(comps), 1)
        self.assertEqual(comps[0], ["x"])


class TestHubDetection(unittest.TestCase):

    def test_top_hubs(self):
        g = ZeroDepGraph()
        g.add_edge("a", "hub")
        g.add_edge("b", "hub")
        g.add_edge("c", "hub")
        g.add_edge("a", "other")
        hubs = g.top_hub_nodes(1)
        self.assertEqual(hubs[0][0], "hub")
        self.assertEqual(hubs[0][1], 3)


if __name__ == "__main__":
    unittest.main()
