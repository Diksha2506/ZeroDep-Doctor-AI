"""Unit tests for zerodep.explanation_engine — Phase 3: Dependency Explanation Engine.

Uses Python standard library unittest only.
Verifies explanations correspond directly to underlying evidence without unsupported claims.
"""

import json
import unittest
from pathlib import Path

from zerodep.cli import scan_project
from zerodep.graph_engine import ZeroDepGraph
from zerodep.ast_visitor import ASTAnalysisResult, ImportBinding, CallSite, DynamicImport
from zerodep.impact_engine import calculate_dependency_impact
from zerodep.risk_engine import calculate_dependency_risk
from zerodep.explanation_engine import (
    generate_explanation,
    generate_all_explanations,
    DependencyExplanation,
)


def _make_scan(dep_name: str, status: str, confidence: str, occurrences: int = 0,
               files=None, symbols=None, imports_count: int = 0,
               graph: ZeroDepGraph = None, ast_results=None):
    """Helper to build scan_data dict with pre-computed impact and risk for explanation testing."""
    files = files or []
    symbols = symbols or {}
    ast_results = ast_results or []
    g = graph or ZeroDepGraph()

    dep_info = {
        "status": status,
        "confidence": confidence,
        "files": files,
        "occurrences": occurrences,
        "symbols": symbols,
        "imports_count": imports_count,
    }
    scan_data = {
        "target_dir": "/tmp/test",
        "files_scanned": len(ast_results),
        "loc": 100,
        "duration": 0.01,
        "declared_deps": {dep_name},
        "used_deps": {dep_name} if status not in ("UNUSED", "IMPORTED_BUT_UNUSED") else set(),
        "usage_details": {dep_name: dep_info},
        "graph": g,
        "ast_results": ast_results,
        "impacts": {},
        "risks": {},
    }
    scan_data["impacts"][dep_name] = calculate_dependency_impact(dep_name, scan_data)
    scan_data["risks"][dep_name] = calculate_dependency_risk(dep_name, scan_data)
    return scan_data


class TestExplanationEngine(unittest.TestCase):

    # ── 1. Unused dependency explanation ───────────────────────────────────
    def test_unused_dependency_explanation(self):
        scan = _make_scan("colorama", "UNUSED", "HIGH")
        exp = generate_explanation("colorama", scan)
        self.assertEqual(exp.category, "UNUSED")
        self.assertEqual(exp.recommendation, "REMOVE")
        self.assertEqual(exp.confidence, "HIGH")
        self.assertIn("No active usage detected", exp.purpose)
        self.assertTrue(any("Declared in project manifest" in p for p in exp.why_present))
        self.assertTrue(len(exp.replacement_limitations) > 0)

    # ── 2. Simple replaceable explanation ──────────────────────────────────
    def test_simple_replaceable_explanation(self):
        scan = _make_scan(
            "click", "REPLACEABLE", "HIGH",
            occurrences=2, files=["main.py"], symbols={"click.command": 1, "click.option": 1},
        )
        exp = generate_explanation("click", scan)
        self.assertEqual(exp.category, "SIMPLE REPLACEABLE")
        self.assertEqual(exp.recommendation, "SAFE MIGRATION CANDIDATE")
        self.assertEqual(exp.confidence, "HIGH")
        self.assertIn("click -> argparse", exp.replacement_opportunity)
        self.assertIn("main.py", exp.where_used)

    # ── 3. Complex API explanation ──────────────────────────────────────────
    def test_complex_api_explanation(self):
        scan = _make_scan(
            "requests", "REPLACEABLE", "LOW",
            occurrences=17, files=["a.py", "b.py"],
            symbols={"requests.get": 12, "requests.Session": 5},
        )
        exp = generate_explanation("requests", scan)
        self.assertEqual(exp.category, "COMPLEX REPLACEABLE")
        self.assertEqual(exp.confidence, "LOW")
        self.assertIn("urllib.request", exp.replacement_opportunity)
        self.assertTrue(any("Session" in w for w in exp.why_present))
        self.assertIn("requests.Session (5 calls)", exp.what_is_used)

    # ── 4. Essential dependency explanation ────────────────────────────────
    def test_essential_dependency_explanation(self):
        scan = _make_scan(
            "pandas", "ESSENTIAL", "HIGH",
            occurrences=4, files=["data.py"], symbols={"pandas.DataFrame": 4},
        )
        exp = generate_explanation("pandas", scan)
        self.assertEqual(exp.category, "ESSENTIAL")
        self.assertEqual(exp.recommendation, "KEEP / NO ACTION")
        self.assertEqual(exp.replacement_opportunity, "No direct standard-library replacement")
        self.assertIn("pandas.DataFrame (4 calls)", exp.what_is_used)

    # ── 5. Aliased import explanation ──────────────────────────────────────
    def test_aliased_import_explanation(self):
        scan = _make_scan(
            "requests", "REPLACEABLE", "HIGH",
            occurrences=3, files=["api.py"], symbols={"requests.get": 3},
        )
        exp = generate_explanation("requests", scan)
        self.assertEqual(exp.where_used, ["api.py"])
        self.assertTrue(any("Static evidence indicates active AST references" in p for p in exp.why_present))

    # ── 6. From-import explanation ──────────────────────────────────────────
    def test_from_import_explanation(self):
        scan = _make_scan(
            "requests", "REPLACEABLE", "HIGH",
            occurrences=2, files=["service.py"], symbols={"requests.get": 2},
        )
        exp = generate_explanation("requests", scan)
        self.assertIn("requests.get (2 calls)", exp.what_is_used)

    # ── 7. Multiple symbols ─────────────────────────────────────────────────
    def test_multiple_symbols(self):
        scan = _make_scan(
            "click", "REPLACEABLE", "HIGH",
            occurrences=5, files=["cli.py"],
            symbols={"click.command": 1, "click.option": 3, "click.echo": 1},
        )
        exp = generate_explanation("click", scan)
        self.assertEqual(len(exp.what_is_used), 3)
        self.assertEqual(exp.what_is_used[0], "click.option (3 calls)")

    # ── 8. Dynamic import warning ───────────────────────────────────────────
    def test_dynamic_import_warning(self):
        res = ASTAnalysisResult(
            file_path="app.py",
            imports=[ImportBinding("dyn_pkg", "fn", "fn", 1, False)],
            call_sites=[],
            dynamic_imports=[DynamicImport("app.py", 5, "importlib", target_module="dyn_pkg")],
        )
        scan = _make_scan(
            "dyn_pkg", "ESSENTIAL", "HIGH",
            occurrences=1, files=["app.py"], symbols={"dyn_pkg.fn": 1},
            ast_results=[res],
        )
        exp = generate_explanation("dyn_pkg", scan)
        self.assertTrue(len(exp.warnings) > 0)
        self.assertTrue(any("Dynamic" in w for w in exp.warnings))

    # ── Regression tests for Evidence-Attribution of Dynamic Imports ────────
    def test_dynamic_import_of_a_does_not_affect_b(self):
        """Dynamic import targeting 'requests' must NOT attach warnings to 'click'."""
        res = ASTAnalysisResult(
            file_path="app.py",
            imports=[
                ImportBinding("click", "command", "command", 1, False),
                ImportBinding("requests", "get", "get", 2, False),
            ],
            call_sites=[],
            dynamic_imports=[DynamicImport("app.py", 10, "importlib", target_module="requests")],
        )
        scan = _make_scan(
            "click", "REPLACEABLE", "HIGH",
            occurrences=2, files=["app.py"], symbols={"click.command": 2},
            ast_results=[res],
        )
        scan_req = _make_scan(
            "requests", "REPLACEABLE", "HIGH",
            occurrences=2, files=["app.py"], symbols={"requests.get": 2},
            ast_results=[res],
        )
        impact_click = calculate_dependency_impact("click", scan)
        impact_req = calculate_dependency_impact("requests", scan_req)

        self.assertFalse(impact_click.has_dynamic_imports)
        self.assertTrue(impact_req.has_dynamic_imports)

        exp_click = generate_explanation("click", scan)
        exp_req = generate_explanation("requests", scan_req)

        self.assertFalse(any("Dynamic" in w for w in exp_click.warnings))
        self.assertTrue(any("Dynamic" in w for w in exp_req.warnings))

    def test_unknown_dynamic_import_target_not_attributed_to_individual_deps(self):
        """Dynamic import with variable/unknown target is not falsely attributed to click."""
        res = ASTAnalysisResult(
            file_path="app.py",
            imports=[ImportBinding("click", "command", "command", 1, False)],
            call_sites=[],
            dynamic_imports=[DynamicImport("app.py", 12, "importlib", target_module=None)],
        )
        scan = _make_scan(
            "click", "REPLACEABLE", "HIGH",
            occurrences=2, files=["app.py"], symbols={"click.command": 2},
            ast_results=[res],
        )
        impact = calculate_dependency_impact("click", scan)
        self.assertFalse(impact.has_dynamic_imports)

        exp = generate_explanation("click", scan)
        self.assertFalse(any("Dynamic" in w for w in exp.warnings))

    # ── 9. Conditional import explanation ──────────────────────────────────
    def test_conditional_import_explanation(self):
        res = ASTAnalysisResult(
            file_path="app.py",
            imports=[ImportBinding("cond_pkg", "fn", "fn", 1, False, is_conditional=True)],
            call_sites=[],
        )
        scan = _make_scan(
            "cond_pkg", "ESSENTIAL", "HIGH",
            occurrences=1, files=["app.py"], symbols={"cond_pkg.fn": 1},
            ast_results=[res],
        )
        exp = generate_explanation("cond_pkg", scan)
        self.assertIsNotNone(exp.purpose)

    # ── 10. Star import limitation ──────────────────────────────────────────
    def test_star_import_limitation(self):
        res = ASTAnalysisResult(
            file_path="app.py",
            imports=[ImportBinding("star_pkg", "*", "*", 1, False, is_star=True)],
            call_sites=[],
            star_imports=["star_pkg"],
        )
        scan = _make_scan(
            "star_pkg", "ESSENTIAL", "HIGH",
            occurrences=1, files=["app.py"], symbols={"star_pkg.*": 1},
            ast_results=[res],
        )
        exp = generate_explanation("star_pkg", scan)
        self.assertTrue(any("Star import" in w for w in exp.warnings))

    # ── 11. HIGH confidence explanation ────────────────────────────────────
    def test_high_confidence_explanation(self):
        scan = _make_scan("colorama", "REPLACEABLE", "HIGH", occurrences=1, files=["app.py"], symbols={"colorama.init": 1})
        exp = generate_explanation("colorama", scan)
        self.assertEqual(exp.confidence, "HIGH")

    # ── 12. LOW confidence explanation ─────────────────────────────────────
    def test_low_confidence_explanation(self):
        scan = _make_scan("requests", "REPLACEABLE", "LOW", occurrences=5, files=["app.py"], symbols={"requests.Session": 5})
        exp = generate_explanation("requests", scan)
        self.assertEqual(exp.confidence, "LOW")

    # ── 13. Impact included ─────────────────────────────────────────────────
    def test_impact_included(self):
        scan = _make_scan("tabulate", "REPLACEABLE", "HIGH", occurrences=2, files=["a.py", "b.py"], symbols={"tabulate.tabulate": 2})
        exp = generate_explanation("tabulate", scan)
        self.assertEqual(exp.impact_level, "LOW")
        self.assertIn("2 files affected", exp.impact_summary)

    # ── 14. Risk included ───────────────────────────────────────────────────
    def test_risk_included(self):
        scan = _make_scan("tabulate", "REPLACEABLE", "HIGH", occurrences=2, files=["a.py"], symbols={"tabulate.tabulate": 2})
        exp = generate_explanation("tabulate", scan)
        self.assertEqual(exp.risk_level, "LOW")
        self.assertIsInstance(exp.risk_score, int)

    # ── 15. Recommendation included ────────────────────────────────────────
    def test_recommendation_included(self):
        scan = _make_scan("click", "REPLACEABLE", "HIGH", occurrences=1, files=["a.py"], symbols={"click.command": 1})
        exp = generate_explanation("click", scan)
        self.assertEqual(exp.recommendation, "SAFE MIGRATION CANDIDATE")

    # ── 16. Evidence is deterministic ───────────────────────────────────────
    def test_evidence_is_deterministic(self):
        scan = _make_scan("click", "REPLACEABLE", "HIGH", occurrences=3, files=["a.py"], symbols={"click.command": 2, "click.option": 1})
        exp1 = generate_explanation("click", scan).to_dict()
        exp2 = generate_explanation("click", scan).to_dict()
        self.assertEqual(exp1, exp2)

    # ── 17. JSON serialization ──────────────────────────────────────────────
    def test_json_serialization(self):
        scan = _make_scan("click", "REPLACEABLE", "HIGH", occurrences=1, files=["a.py"], symbols={"click.command": 1})
        exp = generate_explanation("click", scan)
        d = exp.to_dict()
        json_str = json.dumps(d)
        self.assertIn("category", json_str)
        self.assertIn("replacement_opportunity", json_str)

    # ── 18. CLI integration ─────────────────────────────────────────────────
    def test_cli_integration(self):
        demo_path = Path(__file__).parent.parent / "demo"
        if not demo_path.exists():
            self.skipTest("demo directory not found")
        scan = scan_project(demo_path)
        self.assertIn("explanations", scan)
        for dep in scan["declared_deps"]:
            self.assertIn(dep, scan["explanations"])
            self.assertIsInstance(scan["explanations"][dep], DependencyExplanation)

    # ── 19. Empty project ───────────────────────────────────────────────────
    def test_empty_project(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            scan = scan_project(Path(tmpdir))
            self.assertEqual(scan["explanations"], {})

    # ── 20. No dependencies ─────────────────────────────────────────────────
    def test_no_dependencies(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "app.py").write_text("import os\nprint(os.getcwd())\n")
            scan = scan_project(Path(tmpdir))
            self.assertEqual(scan["explanations"], {})

    # ── 21. Unsupported package ─────────────────────────────────────────────
    def test_unsupported_package(self):
        scan = _make_scan("some_obscure_pkg", "ESSENTIAL", "HIGH", occurrences=2, files=["a.py"], symbols={"some_obscure_pkg.run": 2})
        exp = generate_explanation("some_obscure_pkg", scan)
        self.assertEqual(exp.category, "ESSENTIAL")
        self.assertEqual(exp.replacement_opportunity, "No direct standard-library replacement")
        self.assertIn("some_obscure_pkg.run (2 calls)", exp.what_is_used)

    # ── 22. Missing migration recipe ────────────────────────────────────────
    def test_missing_migration_recipe(self):
        scan = _make_scan("custom_framework", "REPLACEABLE", "MEDIUM", occurrences=1, files=["a.py"], symbols={"custom_framework.start": 1})
        exp = generate_explanation("custom_framework", scan)
        self.assertEqual(exp.category, "COMPLEX REPLACEABLE")
        self.assertIsNotNone(exp.purpose)

    # ── 23. Unicode / project path handling ─────────────────────────────────
    def test_unicode_project_path(self):
        scan = _make_scan("click", "REPLACEABLE", "HIGH", occurrences=1, files=["tests/unicode_🐍.py"], symbols={"click.command": 1})
        exp = generate_explanation("click", scan)
        self.assertIn("tests/unicode_🐍.py", exp.where_used)

    # ── 24. Repeated execution produces identical explanation ────────────────
    def test_repeated_execution_produces_identical_explanation(self):
        scan = _make_scan("requests", "REPLACEABLE", "LOW", occurrences=5, files=["a.py", "b.py"], symbols={"requests.get": 3, "requests.Session": 2})
        e1 = generate_explanation("requests", scan).to_dict()
        e2 = generate_explanation("requests", scan).to_dict()
        self.assertEqual(e1, e2)


if __name__ == "__main__":
    unittest.main()
