"""Unit tests for zerodep.risk_engine — Phase 2: Dependency Risk Model.

Uses Python standard library unittest only.
Tests validate actual classification logic, not merely field existence.
"""

import json
import unittest
from pathlib import Path

from zerodep.cli import scan_project
from zerodep.graph_engine import ZeroDepGraph
from zerodep.ast_visitor import ASTAnalysisResult, ImportBinding, CallSite, DynamicImport
from zerodep.impact_engine import calculate_dependency_impact
from zerodep.risk_engine import (
    calculate_dependency_risk,
    build_risk_impact_matrix,
    DependencyRisk,
)


def _make_scan(dep_name: str, status: str, confidence: str, occurrences: int = 0,
               files=None, symbols=None, imports_count: int = 0,
               graph: ZeroDepGraph = None, ast_results=None):
    """Helper to build minimal scan_data for risk calculation tests."""
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
        "declared_deps": {dep_name},
        "used_deps": {dep_name} if status not in ("UNUSED", "IMPORTED_BUT_UNUSED") else set(),
        "usage_details": {dep_name: dep_info},
        "graph": g,
        "ast_results": ast_results,
        "impacts": {},
    }
    # Compute impact first so risk engine can read it
    scan_data["impacts"][dep_name] = calculate_dependency_impact(dep_name, scan_data)
    return scan_data


class TestRiskEngine(unittest.TestCase):

    # ── 1. Unused dependency ────────────────────────────────────────────────
    def test_unused_dependency_is_remove(self):
        scan = _make_scan("click", "UNUSED", "HIGH")
        risk = calculate_dependency_risk("click", scan)
        self.assertEqual(risk.recommendation, "REMOVE")
        self.assertIn(risk.level, ("LOW", "MEDIUM"))
        # Score should include unused maintenance penalty
        self.assertGreaterEqual(risk.score, 15)

    # ── 2. Simple high-confidence replacement ───────────────────────────────
    def test_simple_high_confidence_replacement(self):
        scan = _make_scan(
            "colorama", "REPLACEABLE", "HIGH",
            occurrences=2, files=["app.py"], symbols={"colorama.init": 2}, imports_count=1,
        )
        risk = calculate_dependency_risk("colorama", scan)
        self.assertEqual(risk.recommendation, "SAFE MIGRATION CANDIDATE")
        self.assertIn(risk.level, ("LOW", "MEDIUM"))

    def test_replaceable_high_confidence_low_risk_with_project_dynamic_import(self):
        """Ensure REPLACEABLE + HIGH confidence + LOW risk yields SAFE MIGRATION CANDIDATE even if project has dynamic imports."""
        res = ASTAnalysisResult(
            file_path="main.py",
            imports=[ImportBinding("click", "command", "command", 5, False)],
            call_sites=[],
            dynamic_imports=[DynamicImport("main.py", 42, "importlib", target_module=None)],
            star_imports=[],
        )
        scan = _make_scan(
            "click", "REPLACEABLE", "HIGH",
            occurrences=2, files=["main.py"], symbols={"click.command": 1, "click.option": 1},
            imports_count=1, ast_results=[res],
        )
        risk = calculate_dependency_risk("click", scan)
        self.assertEqual(risk.level, "LOW")
        self.assertEqual(risk.recommendation, "SAFE MIGRATION CANDIDATE")

    # ── 3. Complex API ──────────────────────────────────────────────────────
    def test_complex_api_raises_risk_score(self):
        # 4 files pushes the risk score above the HIGH threshold (>=55):
        # LOW confidence (+25) + HIGH api_complexity (+15) + calls>=15 (+10) + files>=4 (+10) = 60
        scan = _make_scan(
            "requests", "REPLACEABLE", "LOW",
            occurrences=17, files=["a.py", "b.py", "c.py", "d.py"],
            symbols={"requests.get": 12, "requests.Session": 5, "requests.post": 3,
                     "requests.put": 2, "requests.delete": 1, "requests.head": 1},
            imports_count=4,
        )
        risk = calculate_dependency_risk("requests", scan)
        self.assertEqual(risk.level, "HIGH")
        self.assertGreaterEqual(risk.score, 55)

    # ── 4. LOW migration confidence ─────────────────────────────────────────
    def test_low_confidence_increases_score(self):
        scan_low = _make_scan("pkg_a", "REPLACEABLE", "LOW",
                              occurrences=5, files=["m.py"], symbols={"pkg_a.fn": 5})
        scan_high = _make_scan("pkg_b", "REPLACEABLE", "HIGH",
                               occurrences=5, files=["m.py"], symbols={"pkg_b.fn": 5})
        risk_low = calculate_dependency_risk("pkg_a", scan_low)
        risk_high = calculate_dependency_risk("pkg_b", scan_high)
        self.assertGreater(risk_low.score, risk_high.score)

    # ── 5. HIGH impact + HIGH risk ──────────────────────────────────────────
    def test_high_impact_high_risk(self):
        scan = _make_scan(
            "requests", "REPLACEABLE", "LOW",
            occurrences=20, files=[f"f{i}.py" for i in range(5)],
            symbols={"requests.Session": 10, "requests.get": 10}, imports_count=5,
        )
        impact = calculate_dependency_impact("requests", scan)
        risk = calculate_dependency_risk("requests", scan)
        self.assertEqual(impact.level, "HIGH")
        self.assertEqual(risk.level, "HIGH")

    # ── 6. HIGH impact + LOW risk ───────────────────────────────────────────
    def test_high_impact_low_risk(self):
        """Many files, hub graph, high call-sites, HIGH confidence, simple API → HIGH impact, LOW risk.

        Impact scoring: files>=4 (+3) + calls>=15 (+3) + is_graph_hub (+3) = 9 pts → HIGH.
        Risk scoring: no LOW confidence, simple API, no danger flags → score stays LOW.
        """
        # Build a hub graph so affected modules are central hubs
        g = ZeroDepGraph()
        for i in range(4):
            g.add_edge(f"dep_{i}.py", "f0.py")   # f0.py has 4 dependents → hub
        for i in range(6):
            g.add_node(f"f{i}.py")
        scan = _make_scan(
            "colorama", "REPLACEABLE", "HIGH",
            occurrences=30, files=[f"f{i}.py" for i in range(6)],
            symbols={"colorama.Fore": 30}, imports_count=6, graph=g,
        )
        impact = calculate_dependency_impact("colorama", scan)
        risk = calculate_dependency_risk("colorama", scan)
        self.assertEqual(impact.level, "HIGH")
        # Risk should be LOW or MEDIUM (not HIGH) because confidence is HIGH & API is simple
        self.assertIn(risk.level, ("LOW", "MEDIUM"))
        # The key assertion: impact and risk differ — they are independent dimensions
        self.assertNotEqual(impact.level, risk.level)

    # ── 7. Small-footprint + HIGH risk (uncertainty-driven) ──────────────────
    def test_low_impact_high_risk(self):
        """1 file, 1 call, HIGH confidence — but dynamic + star + circular + conditional
        drive risk to HIGH despite small direct footprint.

        Key concept: impact flags (dynamic, star, circular) inflate BOTH impact points
        AND risk score. The meaningful test is that HIGH risk can arise from uncertainty
        factors alone, even when direct usage (files/calls) is minimal.

        Risk score: dynamic (+20) + star (+15) + circular (+15) + conditional (+10)
                    + ESSENTIAL no-replacement (+10) = 70 → HIGH risk.
        Direct footprint: files_affected=1, call_sites=1 (small usage breadth).
        """
        g = ZeroDepGraph()
        g.add_edge("mod_a.py", "mod_b.py")
        g.add_edge("mod_b.py", "mod_a.py")

        res = ASTAnalysisResult(
            file_path="mod_a.py",
            imports=[
                ImportBinding("risky_pkg", "x", "x", 1, False,
                              is_star=True, is_conditional=True),
            ],
            call_sites=[],
            dynamic_imports=[DynamicImport("mod_a.py", 5, "importlib", target_module="risky_pkg")],
            star_imports=["risky_pkg"],
        )
        scan = _make_scan(
            "risky_pkg", "ESSENTIAL", "HIGH",   # HIGH confidence, not LOW
            occurrences=1, files=["mod_a.py"], symbols={"risky_pkg.x": 1},
            graph=g, ast_results=[res],
        )
        impact = calculate_dependency_impact("risky_pkg", scan)
        risk = calculate_dependency_risk("risky_pkg", scan)

        # Small direct footprint — only 1 file, 1 call site
        self.assertLessEqual(impact.files_affected, 2)
        self.assertLessEqual(impact.call_sites, 2)
        # HIGH risk is driven purely by uncertainty factors, not usage breadth
        self.assertEqual(risk.level, "HIGH")
        self.assertGreaterEqual(risk.score, 55)
        # Key distinction: risk >> usage ratio (uncertainty dominates)
        self.assertGreater(risk.score, impact.files_affected * 20)


    # ── 8. Dynamic import ───────────────────────────────────────────────────
    def test_dynamic_import_raises_risk_and_is_investigate(self):
        res = ASTAnalysisResult(
            file_path="app.py",
            imports=[ImportBinding("dyn_pkg", "load", "load", 1, False)],
            call_sites=[],
            dynamic_imports=[DynamicImport("app.py", 3, "importlib", target_module="dyn_pkg")],
            star_imports=[],
        )
        scan = _make_scan(
            "dyn_pkg", "ESSENTIAL", "HIGH",
            occurrences=1, files=["app.py"], symbols={"dyn_pkg.load": 1},
            ast_results=[res],
        )
        risk = calculate_dependency_risk("dyn_pkg", scan)
        self.assertTrue(scan["impacts"]["dyn_pkg"].has_dynamic_imports)
        self.assertEqual(risk.recommendation, "INVESTIGATE")
        # Dynamic import contributes +20 to score
        self.assertGreaterEqual(risk.score, 20)

    # ── 9. Conditional import ───────────────────────────────────────────────
    def test_conditional_import_adds_risk(self):
        scan_cond = _make_scan("pkg_cond", "ESSENTIAL", "HIGH",
                               occurrences=2, files=["m.py"], symbols={"pkg_cond.fn": 2})
        scan_cond["impacts"]["pkg_cond"].has_conditional_imports = True
        risk_cond = calculate_dependency_risk("pkg_cond", scan_cond)

        scan_plain = _make_scan("pkg_plain", "ESSENTIAL", "HIGH",
                                occurrences=2, files=["m.py"], symbols={"pkg_plain.fn": 2})
        risk_plain = calculate_dependency_risk("pkg_plain", scan_plain)

        self.assertGreater(risk_cond.score, risk_plain.score)

    # ── 10. Star import ─────────────────────────────────────────────────────
    def test_star_import_triggers_investigate(self):
        res = ASTAnalysisResult(
            file_path="app.py",
            imports=[ImportBinding("star_pkg", "*", "*", 1, False, is_star=True)],
            call_sites=[],
            dynamic_imports=[],
            star_imports=["star_pkg"],
        )
        scan = _make_scan(
            "star_pkg", "ESSENTIAL", "HIGH",
            occurrences=1, files=["app.py"], symbols={"star_pkg.*": 1},
            ast_results=[res],
        )
        risk = calculate_dependency_risk("star_pkg", scan)
        self.assertEqual(risk.recommendation, "INVESTIGATE")
        self.assertGreaterEqual(risk.score, 15)

    # ── 11. Circular dependency ─────────────────────────────────────────────
    def test_circular_dependency_adds_risk(self):
        g = ZeroDepGraph()
        g.add_edge("mod_a.py", "mod_b.py")
        g.add_edge("mod_b.py", "mod_a.py")

        scan = _make_scan("circ_pkg", "ESSENTIAL", "HIGH",
                          occurrences=3, files=["mod_a.py"], symbols={"circ_pkg.fn": 3},
                          graph=g)
        risk = calculate_dependency_risk("circ_pkg", scan)
        self.assertTrue(scan["impacts"]["circ_pkg"].circular_dependency_involvement)
        self.assertGreaterEqual(risk.score, 15)

    # ── 12. No replacement available (ESSENTIAL) ────────────────────────────
    def test_no_replacement_available_keep_no_action(self):
        scan = _make_scan("numpy", "ESSENTIAL", "HIGH",
                          occurrences=5, files=["calc.py"], symbols={"numpy.array": 5})
        risk = calculate_dependency_risk("numpy", scan)
        self.assertEqual(risk.recommendation, "KEEP / NO ACTION")

    # ── 13. Deterministic repeated calculation ──────────────────────────────
    def test_deterministic_results(self):
        scan = _make_scan("pkg", "REPLACEABLE", "HIGH",
                          occurrences=5, files=["m.py"], symbols={"pkg.run": 5})
        r1 = calculate_dependency_risk("pkg", scan).to_dict()
        r2 = calculate_dependency_risk("pkg", scan).to_dict()
        self.assertEqual(r1, r2)

    # ── 14. Risk/impact matrix ──────────────────────────────────────────────
    def test_risk_impact_matrix_structure(self):
        scan_a = _make_scan("pkg_a", "UNUSED", "HIGH")
        scan_b = _make_scan(
            "pkg_b", "REPLACEABLE", "LOW",
            occurrences=20, files=[f"f{i}.py" for i in range(5)],
            symbols={f"pkg_b.fn{i}": 4 for i in range(5)},
        )
        # Build combined impacts/risks dict as scan_project would
        impacts = {
            "pkg_a": calculate_dependency_impact("pkg_a", scan_a),
            "pkg_b": calculate_dependency_impact("pkg_b", scan_b),
        }
        risks = {
            "pkg_a": calculate_dependency_risk("pkg_a", scan_a),
            "pkg_b": calculate_dependency_risk("pkg_b", scan_b),
        }
        matrix = build_risk_impact_matrix(impacts, risks)
        # Matrix must have all 3x3 cells
        for r_lvl in ("LOW", "MEDIUM", "HIGH"):
            for i_lvl in ("LOW", "MEDIUM", "HIGH"):
                self.assertIn(i_lvl, matrix[r_lvl])
        # pkg_a should be in some LOW risk cell (unused = LOW impact, LOW-MEDIUM risk)
        all_placed = [
            dep for row in matrix.values() for cell in row.values() for dep in cell
        ]
        self.assertIn("pkg_a", all_placed)
        self.assertIn("pkg_b", all_placed)

    # ── 15. Recommendation mapping ──────────────────────────────────────────
    def test_recommendation_mapping(self):
        cases = [
            ("UNUSED", "HIGH", "REMOVE"),
            ("IMPORTED_BUT_UNUSED", "HIGH", "REMOVE"),
            ("REPLACEABLE", "HIGH", "SAFE MIGRATION CANDIDATE"),
            ("ESSENTIAL", "HIGH", "KEEP / NO ACTION"),
        ]
        for status, confidence, expected_rec in cases:
            with self.subTest(status=status, confidence=confidence):
                scan = _make_scan(f"pkg_{status}", status, confidence,
                                  occurrences=2, files=["m.py"], symbols={"x": 2})
                risk = calculate_dependency_risk(f"pkg_{status}", scan)
                self.assertEqual(
                    risk.recommendation, expected_rec,
                    f"Expected {expected_rec!r} for status={status!r}, got {risk.recommendation!r}",
                )

    # ── 16. JSON output ─────────────────────────────────────────────────────
    def test_json_output_structure(self):
        scan = _make_scan("pkg", "REPLACEABLE", "HIGH",
                          occurrences=3, files=["m.py"], symbols={"pkg.fn": 3})
        risk = calculate_dependency_risk("pkg", scan)
        d = risk.to_dict()
        for key in ("level", "score", "risk_factors", "warnings", "recommendation", "reasons"):
            self.assertIn(key, d)
        # Must be JSON-serialisable
        json_str = json.dumps(d)
        self.assertIn("level", json_str)
        self.assertIn("recommendation", json_str)

    # ── 17. CLI output integration ──────────────────────────────────────────
    def test_cli_scan_populates_risks(self):
        demo_path = Path(__file__).parent.parent / "demo"
        if not demo_path.exists():
            self.skipTest("demo directory not found")
        scan = scan_project(demo_path)
        self.assertIn("risks", scan)
        for dep in scan["declared_deps"]:
            self.assertIn(dep, scan["risks"])
            risk = scan["risks"][dep]
            self.assertIsInstance(risk, DependencyRisk)
            self.assertIn(risk.level, ("LOW", "MEDIUM", "HIGH"))
            self.assertIn(risk.recommendation,
                          ("REMOVE", "SAFE MIGRATION CANDIDATE", "REVIEW MIGRATION",
                           "MANUAL REVIEW", "KEEP / NO ACTION", "INVESTIGATE"))

    # ── 18. Empty project ───────────────────────────────────────────────────
    def test_empty_project(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            scan = scan_project(Path(tmpdir))
            self.assertEqual(scan["risks"], {})

    # ── 19. No dependencies ─────────────────────────────────────────────────
    def test_no_dependencies(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "app.py").write_text("import os\nprint(os.getcwd())\n")
            scan = scan_project(Path(tmpdir))
            self.assertEqual(scan["risks"], {})

    # ── 20. Multiple dependencies ───────────────────────────────────────────
    def test_multiple_dependencies_all_computed(self):
        demo_path = Path(__file__).parent.parent / "demo"
        if not demo_path.exists():
            self.skipTest("demo directory not found")
        scan = scan_project(demo_path)
        self.assertGreaterEqual(len(scan["risks"]), 1)
        # Every declared dep must have a risk entry
        for dep in scan["declared_deps"]:
            self.assertIn(dep, scan["risks"])
        # risk dict must be JSON-serialisable
        risk_json = {k: v.to_dict() for k, v in scan["risks"].items()}
        self.assertIsInstance(json.dumps(risk_json), str)


if __name__ == "__main__":
    unittest.main()
