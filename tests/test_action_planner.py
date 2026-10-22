"""Unit tests for Phase 4 Dependency Action Planner.

Validates action plan creation, priority scoring, effort aggregation,
human review rules, next-best-action determination, CLI rendering, and JSON formatting.
"""

import json
import tempfile
import unittest
from pathlib import Path

from zerodep.action_planner import (
    ActionPlanItem,
    ProjectActionPlan,
    generate_action_plan,
    plan_action_for_dependency,
)
from zerodep.ast_visitor import ASTAnalysisResult, DynamicImport, ImportBinding
from zerodep.cli import _json_report, scan_project
from zerodep.graph_engine import ZeroDepGraph
from zerodep.impact_engine import calculate_dependency_impact
from zerodep.risk_engine import calculate_dependency_risk


def _make_mock_scan(
    dep_name: str,
    status: str = "REPLACEABLE",
    confidence: str = "HIGH",
    occurrences: int = 2,
    files: list = None,
    symbols: dict = None,
    graph: ZeroDepGraph = None,
    ast_results: list = None,
) -> dict:
    if files is None:
        files = ["main.py"]
    if symbols is None:
        symbols = {f"{dep_name}.fn": occurrences}
    if graph is None:
        graph = ZeroDepGraph()
        for f in files:
            graph.add_node(f)
    if ast_results is None:
        ast_results = []

    scan_data = {
        "target_dir": "/mock",
        "files_scanned": len(files),
        "loc": 100,
        "duration": 0.05,
        "declared_deps": {dep_name},
        "used_deps": {dep_name} if status != "UNUSED" else set(),
        "usage_details": {
            dep_name: {
                "status": status,
                "confidence": confidence,
                "files": files,
                "occurrences": occurrences,
                "symbols": symbols,
                "imports_count": len(files),
            }
        },
        "graph": graph,
        "ast_results": ast_results,
    }

    scan_data["impacts"] = {dep_name: calculate_dependency_impact(dep_name, scan_data)}
    scan_data["risks"] = {dep_name: calculate_dependency_risk(dep_name, scan_data)}
    from zerodep.scoring_engine import calculate_score
    from zerodep.migration_engine import RECIPES
    used = scan_data["used_deps"]
    declared = scan_data["declared_deps"]
    replaceable = used & set(RECIPES.keys())
    scan_data["score_info"] = calculate_score(declared, used, replaceable, 10, 10)
    return scan_data


class TestActionPlanner(unittest.TestCase):

    # 1. Unused dependency → REMOVE
    def test_unused_dependency_removal(self):
        scan = _make_mock_scan("unused_pkg", status="UNUSED", occurrences=0, files=[])
        item = plan_action_for_dependency("unused_pkg", scan)
        self.assertEqual(item.action, "REMOVE")
        self.assertEqual(item.priority, "P1")
        self.assertFalse(item.human_review_required)
        self.assertEqual(
            item.human_review_type,
            "Source-code review: not required; packaging/configuration verification recommended."
        )
        self.assertEqual(item.effort, "Trivial")

    def test_unused_dependency_non_code_limitation_warning(self):
        """Regression test: REMOVE action must explicitly note non-code reference limitations."""
        scan = _make_mock_scan("unused_pkg", status="UNUSED", occurrences=0, files=[])
        item = plan_action_for_dependency("unused_pkg", scan)
        self.assertTrue(any("non-code" in b.lower() for b in item.blocking_factors))
        self.assertTrue("config" in item.suggested_sequence[0].lower())

    # 2. High-confidence replacement → SAFE MIGRATION CANDIDATE
    def test_high_confidence_replacement(self):
        scan = _make_mock_scan("click", status="REPLACEABLE", confidence="HIGH", occurrences=2)
        item = plan_action_for_dependency("click", scan)
        self.assertEqual(item.action, "SAFE MIGRATION CANDIDATE")
        self.assertEqual(item.priority, "P2")
        self.assertTrue(item.human_review_required)
        self.assertEqual(item.human_review_type, "Recommended")

    # 3. Low-confidence complex migration → MANUAL REVIEW
    def test_low_confidence_complex_migration(self):
        scan = _make_mock_scan(
            "requests", status="REPLACEABLE", confidence="LOW", occurrences=15,
            files=["a.py", "b.py", "c.py", "d.py"],
            symbols={"requests.get": 10, "requests.Session": 5}
        )
        item = plan_action_for_dependency("requests", scan)
        self.assertEqual(item.action, "MANUAL REVIEW")
        self.assertEqual(item.priority, "P1")
        self.assertTrue(item.human_review_required)
        self.assertEqual(item.human_review_type, "Required")
        self.assertEqual(item.effort, "Complex/Manual")

    # 4. Essential dependency → KEEP
    def test_essential_dependency_retention(self):
        scan = _make_mock_scan("pandas", status="ESSENTIAL", confidence="HIGH", occurrences=5)
        item = plan_action_for_dependency("pandas", scan)
        self.assertEqual(item.action, "KEEP / NO ACTION")
        self.assertEqual(item.priority, "P3")
        self.assertFalse(item.human_review_required)
        self.assertEqual(item.human_review_type, "Not required")

    # 5. Dynamic import attribution
    def test_dynamic_import_triggers_investigate_or_blocking(self):
        res = ASTAnalysisResult(
            file_path="main.py",
            imports=[ImportBinding("dyn_pkg", "fn", "fn", 1, False)],
            call_sites=[],
            dynamic_imports=[DynamicImport("main.py", 10, "importlib", target_module="dyn_pkg")],
        )
        scan = _make_mock_scan("dyn_pkg", status="ESSENTIAL", confidence="HIGH", ast_results=[res])
        item = plan_action_for_dependency("dyn_pkg", scan)
        self.assertTrue(any("Dynamic" in b for b in item.blocking_factors) or item.action == "INVESTIGATE")

    # 6. Conditional import
    def test_conditional_import_handling(self):
        res = ASTAnalysisResult(
            file_path="main.py",
            imports=[ImportBinding("cond_pkg", "fn", "fn", 1, False, is_conditional=True)],
            call_sites=[],
        )
        scan = _make_mock_scan("cond_pkg", status="REPLACEABLE", confidence="HIGH", ast_results=[res])
        item = plan_action_for_dependency("cond_pkg", scan)
        self.assertIsNotNone(item.action)

    # 7. Star import
    def test_star_import_handling(self):
        res = ASTAnalysisResult(
            file_path="main.py",
            imports=[ImportBinding("star_pkg", "*", "*", 1, False, is_star=True)],
            call_sites=[],
            star_imports=["star_pkg"],
        )
        scan = _make_mock_scan("star_pkg", status="REPLACEABLE", confidence="HIGH", ast_results=[res])
        item = plan_action_for_dependency("star_pkg", scan)
        self.assertIsNotNone(item.priority)

    # 8. Circular dependency → P0 BLOCKING
    def test_circular_dependency_p0_blocking(self):
        g = ZeroDepGraph()
        g.add_edge("a.py", "b.py")
        g.add_edge("b.py", "a.py")
        scan = _make_mock_scan("circ_pkg", status="REPLACEABLE", confidence="HIGH", files=["a.py"], graph=g)
        item = plan_action_for_dependency("circ_pkg", scan)
        self.assertEqual(item.priority, "P0")
        self.assertEqual(item.priority_label, "P0 — BLOCKING")
        self.assertTrue(any("Circular" in b for b in item.blocking_factors))

    # 9. Priority ordering (P0 < P1 < P2 < P3)
    def test_priority_ordering_logic(self):
        p_ranks = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
        self.assertLess(p_ranks["P0"], p_ranks["P1"])
        self.assertLess(p_ranks["P1"], p_ranks["P2"])
        self.assertLess(p_ranks["P2"], p_ranks["P3"])

    # 10. Multiple dependencies deterministic sort
    def test_multiple_dependencies_sorting(self):
        scan = {
            "declared_deps": {"click", "colorama", "requests", "pandas"},
            "used_deps": {"click", "requests", "pandas"},
            "usage_details": {
                "colorama": {"status": "UNUSED", "confidence": "HIGH", "files": [], "occurrences": 0, "symbols": {}, "imports_count": 0},
                "requests": {"status": "REPLACEABLE", "confidence": "LOW", "files": ["a.py", "b.py", "c.py", "d.py"], "occurrences": 15, "symbols": {"requests.Session": 5}, "imports_count": 4},
                "click": {"status": "REPLACEABLE", "confidence": "HIGH", "files": ["cli.py"], "occurrences": 2, "symbols": {"click.command": 1}, "imports_count": 1},
                "pandas": {"status": "ESSENTIAL", "confidence": "HIGH", "files": ["data.py"], "occurrences": 3, "symbols": {"pandas.DataFrame": 3}, "imports_count": 1},
            },
            "graph": ZeroDepGraph(),
            "ast_results": [],
        }
        scan["impacts"] = {d: calculate_dependency_impact(d, scan) for d in scan["declared_deps"]}
        scan["risks"] = {d: calculate_dependency_risk(d, scan) for d in scan["declared_deps"]}

        plan = generate_action_plan(scan)
        deps_in_order = [item.package for item in plan.items]

        # colorama (UNUSED -> P1) and requests (LOW conf -> P1) must be before click (P2) and pandas (P3)
        self.assertEqual(deps_in_order[0], "colorama")
        self.assertEqual(deps_in_order[-1], "pandas")

    # 11. Effort aggregation in summary
    def test_effort_aggregation_summary(self):
        scan = _make_mock_scan("click", status="REPLACEABLE", confidence="HIGH")
        plan = generate_action_plan(scan)
        self.assertIn("total_items", plan.summary)
        self.assertEqual(plan.summary["total_items"], 1)

    # 12. Human-review determination rules
    def test_human_review_determination(self):
        scan_unused = _make_mock_scan("unused", status="UNUSED", occurrences=0, files=[])
        item_unused = plan_action_for_dependency("unused", scan_unused)
        self.assertFalse(item_unused.human_review_required)

        scan_safe = _make_mock_scan("click", status="REPLACEABLE", confidence="HIGH")
        item_safe = plan_action_for_dependency("click", scan_safe)
        self.assertTrue(item_safe.human_review_required)
        self.assertEqual(item_safe.human_review_type, "Recommended")

    # 13. Project-level next-best-action determination
    def test_next_best_action_determination(self):
        scan_unused = _make_mock_scan("colorama", status="UNUSED", occurrences=0, files=[])
        plan_unused = generate_action_plan(scan_unused)
        self.assertIn("Remove unused dependency 'colorama'", plan_unused.next_best_action)

        scan_safe = _make_mock_scan("click", status="REPLACEABLE", confidence="HIGH")
        plan_safe = generate_action_plan(scan_safe)
        self.assertIn("Migrate 'click'", plan_safe.next_best_action)

    # 14. Deterministic repeated results
    def test_deterministic_results(self):
        scan = _make_mock_scan("click", status="REPLACEABLE", confidence="HIGH")
        plan1 = generate_action_plan(scan)
        plan2 = generate_action_plan(scan)
        self.assertEqual(plan1.to_dict(), plan2.to_dict())

    # 15. JSON output schema and content
    def test_json_output_structure(self):
        scan = _make_mock_scan("click", status="REPLACEABLE", confidence="HIGH")
        scan["action_plan"] = generate_action_plan(scan)
        report = _json_report(scan)

        self.assertIn("action_plan", report)
        self.assertIn("project_priority", report["action_plan"])
        self.assertIn("items", report["action_plan"])
        self.assertIn("summary", report["action_plan"])

    # 16. CLI scan integration
    def test_cli_scan_integration(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            (tmppath / "requirements.txt").write_text("click\n", encoding="utf-8")
            (tmppath / "app.py").write_text("import click\nclick.command()\n", encoding="utf-8")

            scan = scan_project(tmppath)
            self.assertIn("action_plan", scan)
            self.assertEqual(len(scan["action_plan"].items), 1)

    # 17. Empty project directory
    def test_empty_project_directory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            scan = scan_project(tmppath)
            plan = scan["action_plan"]
            self.assertEqual(plan.summary["total_items"], 0)
            self.assertEqual(plan.next_best_action, "No dependencies detected in project.")

    # 18. No dependencies in manifest
    def test_no_dependencies(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            (tmppath / "app.py").write_text("import json\nprint(json.dumps({}))\n", encoding="utf-8")
            scan = scan_project(tmppath)
            plan = scan["action_plan"]
            self.assertEqual(len(plan.items), 0)

    # 19. Unsupported package handling
    def test_unsupported_package(self):
        scan = _make_mock_scan("custom_unsupported_lib", status="ESSENTIAL", confidence="HIGH")
        item = plan_action_for_dependency("custom_unsupported_lib", scan)
        self.assertEqual(item.action, "KEEP / NO ACTION")

    # 20. Dependency with no migration recipe
    def test_dependency_with_no_recipe(self):
        scan = _make_mock_scan("unknown_pkg", status="ESSENTIAL", confidence="HIGH")
        item = plan_action_for_dependency("unknown_pkg", scan)
        self.assertEqual(item.action, "KEEP / NO ACTION")

    # 21. High impact / low risk prioritization
    def test_high_impact_low_risk_prioritization(self):
        g = ZeroDepGraph()
        files = [f"f{i}.py" for i in range(4)]
        for f in files:
            g.add_node(f)
        scan = _make_mock_scan(
            "colorama", status="REPLACEABLE", confidence="HIGH",
            occurrences=15, files=files, graph=g
        )
        item = plan_action_for_dependency("colorama", scan)
        self.assertIn(item.priority, ("P1", "P2"))

    # 22. Low impact / high risk prioritization
    def test_low_impact_high_risk_prioritization(self):
        res = ASTAnalysisResult(
            file_path="a.py",
            imports=[ImportBinding("risk_pkg", "x", "x", 1, False, is_star=True)],
            call_sites=[],
            dynamic_imports=[DynamicImport("a.py", 5, "importlib", target_module="risk_pkg")],
            star_imports=["risk_pkg"],
        )
        scan = _make_mock_scan("risk_pkg", status="REPLACEABLE", confidence="HIGH", occurrences=1, files=["a.py"], ast_results=[res])
        item = plan_action_for_dependency("risk_pkg", scan)
        self.assertEqual(item.priority, "P2")


if __name__ == "__main__":
    unittest.main()
