"""Expanded Day 2 Tests — targeting 135+ tests across confidence, scope, graph, json, and CLI features."""

import json
import tempfile
import unittest
from pathlib import Path

from zerodep.ast_visitor import analyze_ast, SymbolTableVisitor
from zerodep.cli import scan_project, _json_report
from zerodep.graph_engine import ZeroDepGraph
from zerodep.migration_engine import get_recipe
from zerodep.scoring_engine import calculate_score


class TestImportScopeAndConfidence(unittest.TestCase):

    def test_module_level_import_high_confidence(self):
        code = "import requests\nrequests.get('x')"
        res = analyze_ast("test.py", code)
        self.assertEqual(res.imports[0].scope_kind, "module")
        self.assertEqual(res.call_sites[0].confidence_label, "HIGH")

    def test_function_level_nested_import_medium_confidence(self):
        code = "def foo():\n    import requests\n    requests.get('x')"
        res = analyze_ast("test.py", code)
        self.assertEqual(res.imports[0].scope_kind, "function")
        self.assertEqual(res.call_sites[0].confidence_label, "MEDIUM")

    def test_class_level_nested_import(self):
        code = "class Bar:\n    import requests\n    requests.get('x')"
        res = analyze_ast("test.py", code)
        self.assertEqual(res.imports[0].scope_kind, "class")

    def test_conditional_if_import(self):
        code = "if True:\n    import requests\n    requests.get('x')"
        res = analyze_ast("test.py", code)
        self.assertTrue(res.imports[0].is_conditional)

    def test_try_except_import_guard(self):
        code = "try:\n    import ujson\nexcept ImportError:\n    import json"
        res = analyze_ast("test.py", code)
        ujson_imp = [i for i in res.imports if i.original_module == "ujson"][0]
        self.assertTrue(ujson_imp.is_conditional)


class TestMigrationEngineConfidence(unittest.TestCase):

    def test_simple_requests_high_confidence(self):
        recipe = get_recipe("requests", {"requests.get": 1})
        self.assertIsNotNone(recipe)
        self.assertEqual(recipe.confidence, "HIGH")

    def test_complex_requests_session_low_confidence(self):
        recipe = get_recipe("requests", {"requests.Session": 1, "requests.get": 1})
        self.assertIsNotNone(recipe)
        self.assertEqual(recipe.confidence, "LOW")
        self.assertIn("Manual migration to urllib required", recipe.why_replaceable)


class TestGraphEngineEnhancedMetrics(unittest.TestCase):

    def test_max_dependency_depth_dag(self):
        g = ZeroDepGraph()
        g.add_edge("a", "b")
        g.add_edge("b", "c")
        g.add_edge("c", "d")
        self.assertEqual(g.max_dependency_depth(), 4)

    def test_max_dependency_depth_cycle_returns_minus_one(self):
        g = ZeroDepGraph()
        g.add_edge("a", "b")
        g.add_edge("b", "a")
        self.assertEqual(g.max_dependency_depth(), -1)

    def test_top_hub_nodes_sorted(self):
        g = ZeroDepGraph()
        g.add_edge("a", "hub")
        g.add_edge("b", "hub")
        g.add_edge("c", "other")
        hubs = g.top_hub_nodes(2)
        self.assertEqual(hubs[0], ("hub", 2))
        self.assertEqual(hubs[1], ("other", 1))


class TestJSONReportSchema(unittest.TestCase):

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        (self.tmpdir / "requirements.txt").write_text("requests\nclick\n", encoding="utf-8")
        (self.tmpdir / "main.py").write_text(
            "import requests\nrequests.get('x')\n", encoding="utf-8"
        )

    def test_json_report_structure(self):
        scan = scan_project(self.tmpdir)
        rep = _json_report(scan)
        self.assertIn("usage", rep)
        self.assertIn("graph", rep)
        self.assertIn("warnings", rep)
        self.assertIn("edges", rep["graph"])
        self.assertIn("max_dependency_depth", rep["graph"])
        self.assertIn("connected_components", rep["graph"])
        self.assertIn("top_hubs", rep["graph"])
        self.assertIn("potential_improvements", rep["score"])

    def test_json_usage_entry_evidence(self):
        scan = scan_project(self.tmpdir)
        rep = _json_report(scan)
        req_usage = [u for u in rep["usage"] if u["package"] == "requests"][0]
        self.assertEqual(req_usage["status"], "REPLACEABLE")
        self.assertEqual(req_usage["confidence"], "HIGH")
        self.assertGreater(len(req_usage["evidence"]), 0)


class TestDemoIntegration(unittest.TestCase):

    def test_demo_directory_scan(self):
        demo_dir = Path(__file__).parent.parent / "demo"
        scan = scan_project(demo_dir)
        self.assertIn("requests", scan["used_deps"])
        self.assertIn("click", scan["used_deps"])
        self.assertIn("networkx", scan["used_deps"])
        self.assertIn("colorama", scan["declared_deps"] - scan["used_deps"])
        self.assertIn("pytz", scan["declared_deps"] - scan["used_deps"])
        # Circular import between module_a and module_b
        cycles = scan["graph"].find_cycles()
        self.assertGreaterEqual(len(cycles), 1)


if __name__ == "__main__":
    unittest.main()
