"""
Phase 6 Tests: Dependency Decision Summary Engine (`zerodep/summary_engine.py`)
================================================================================
Covers all 20 requirement areas:
  1. Empty project
  2. No dependencies
  3. All-essential project
  4. All-unused project
  5. Mixed dependency project
  6. High-impact/high-risk dependency
  7. Low-impact/low-risk dependency
  8. Dynamic import blocker
  9. Star import uncertainty
  10. Circular dependency blocker
  11. Deterministic ranking
  12. Deterministic repeated output
  13. Correct 3x3 decision matrix
  14. Migration readiness classification
  15. JSON schema
  16. CLI integration
  17. Existing Phase 5 simulation integration
  18. No source modification
  19. Standard-library-only implementation
  20. Bundle integration
"""

import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

# Path fixup
REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))

from zerodep.summary_engine import (
    DecisionSummaryResult,
    TopPriorityItem,
    generate_decision_summary,
)
from zerodep.simulation_engine import simulate_migration


# Helpers to construct synthetic scan_data
class _MockImpact:
    def __init__(self, level="LOW"):
        self.level = level

    def to_dict(self):
        return {"level": self.level}


class _MockRisk:
    def __init__(self, level="LOW", rec="KEEP / NO ACTION"):
        self.level = level
        self.recommendation = rec

    def to_dict(self):
        return {"level": self.level, "recommendation": self.recommendation}


class _MockActionItem:
    def __init__(self, pkg, prio, action, effort="Low", blockers=None):
        self.package = pkg
        self.priority = prio
        self.action = action
        self.effort = effort
        self.blocking_factors = blockers or []

    def to_dict(self):
        return {
            "dependency": self.package,
            "priority": self.priority,
            "action": self.action,
            "effort": self.effort,
            "blocking_factors": self.blocking_factors,
        }


class _MockActionPlan:
    def __init__(self, project_prio="P3", items=None, next_action=""):
        self.project_priority = project_prio
        self.items = items or []
        self.next_best_action = next_action

    def to_dict(self):
        return {
            "project_priority": self.project_priority,
            "items": [i.to_dict() for i in self.items],
            "next_best_action": self.next_best_action,
        }


class _MockDynamicImport:
    def __init__(self, file_path="main.py", line_no=10, target=None):
        self.file_path = file_path
        self.line_no = line_no
        self.target_module = target


class _MockGraph:
    def __init__(self, cycles=None):
        self._cycles = cycles or []

    def find_cycles(self):
        return self._cycles


def _make_scan(
    declared=None,
    usage=None,
    impacts=None,
    risks=None,
    action_plan=None,
    dynamic_imports=None,
    star_imports=None,
    graph=None,
    include_simulation=True,
):
    declared = set(declared or [])
    usage = usage or {}
    impacts = impacts or {d: _MockImpact() for d in declared}
    risks = risks or {d: _MockRisk() for d in declared}
    
    scan = {
        "target_dir": "/mock/project",
        "files_scanned": 5,
        "loc": 150,
        "duration": 0.01,
        "declared_deps": declared,
        "used_deps": {d for d, info in usage.items() if info.get("status") != "UNUSED"},
        "usage_details": usage,
        "impacts": impacts,
        "risks": risks,
        "action_plan": action_plan or _MockActionPlan(),
        "dynamic_imports": dynamic_imports or [],
        "star_imports": star_imports or [],
        "graph": graph or _MockGraph(),
    }
    if include_simulation:
        scan["simulation"] = simulate_migration(scan, mode="recommended")
    return scan


class TestSummaryEngine(unittest.TestCase):

    def test_1_empty_project(self):
        scan = _make_scan(declared=[])
        summary = generate_decision_summary(scan)
        self.assertEqual(summary.total_dependencies, 0)
        self.assertEqual(summary.migration_readiness, "READY")
        self.assertEqual(summary.current_dependency_count, 0)
        self.assertEqual(summary.projected_dependency_count, 0)

    def test_2_no_dependencies(self):
        scan = _make_scan(declared=[], usage={})
        summary = generate_decision_summary(scan)
        self.assertEqual(summary.unused_count, 0)
        self.assertEqual(summary.replaceable_count, 0)
        self.assertEqual(summary.essential_count, 0)

    def test_3_all_essential_project(self):
        declared = ["dep1", "dep2"]
        usage = {
            "dep1": {"status": "ESSENTIAL", "confidence": "HIGH"},
            "dep2": {"status": "ESSENTIAL", "confidence": "HIGH"},
        }
        actions = _MockActionPlan(project_prio="P3", items=[
            _MockActionItem("dep1", "P3", "KEEP / NO ACTION"),
            _MockActionItem("dep2", "P3", "KEEP / NO ACTION"),
        ])
        scan = _make_scan(declared=declared, usage=usage, action_plan=actions)
        summary = generate_decision_summary(scan)
        self.assertEqual(summary.essential_count, 2)
        self.assertEqual(summary.replaceable_count, 0)
        self.assertEqual(summary.unused_count, 0)
        self.assertEqual(summary.potential_reduction_count, 0)

    def test_4_all_unused_project(self):
        declared = ["unused1", "unused2"]
        usage = {
            "unused1": {"status": "UNUSED", "confidence": "HIGH"},
            "unused2": {"status": "UNUSED", "confidence": "HIGH"},
        }
        actions = _MockActionPlan(project_prio="P1", items=[
            _MockActionItem("unused1", "P1", "REMOVE"),
            _MockActionItem("unused2", "P1", "REMOVE"),
        ])
        scan = _make_scan(declared=declared, usage=usage, action_plan=actions)
        summary = generate_decision_summary(scan)
        self.assertEqual(summary.unused_count, 2)
        self.assertEqual(summary.potential_reduction_count, 2)
        self.assertEqual(summary.potential_reduction_percentage, 100.0)

    def test_5_mixed_dependency_project(self):
        declared = ["click", "pandas", "colorama"]
        usage = {
            "click": {"status": "REPLACEABLE", "confidence": "HIGH"},
            "pandas": {"status": "ESSENTIAL", "confidence": "HIGH"},
            "colorama": {"status": "UNUSED", "confidence": "HIGH"},
        }
        actions = _MockActionPlan(project_prio="P1", items=[
            _MockActionItem("colorama", "P1", "REMOVE"),
            _MockActionItem("click", "P2", "SAFE MIGRATION CANDIDATE"),
            _MockActionItem("pandas", "P3", "KEEP / NO ACTION"),
        ])
        scan = _make_scan(declared=declared, usage=usage, action_plan=actions)
        summary = generate_decision_summary(scan)
        self.assertEqual(summary.total_dependencies, 3)
        self.assertEqual(summary.unused_count, 1)
        self.assertEqual(summary.replaceable_count, 1)
        self.assertEqual(summary.essential_count, 1)

    def test_6_high_impact_high_risk_dependency(self):
        declared = ["complex_pkg"]
        usage = {"complex_pkg": {"status": "REPLACEABLE", "confidence": "LOW"}}
        impacts = {"complex_pkg": _MockImpact(level="HIGH")}
        risks = {"complex_pkg": _MockRisk(level="HIGH", rec="MANUAL REVIEW")}
        actions = _MockActionPlan(project_prio="P0", items=[
            _MockActionItem("complex_pkg", "P0", "MANUAL REVIEW", effort="Complex/Manual")
        ])
        scan = _make_scan(declared=declared, usage=usage, impacts=impacts, risks=risks, action_plan=actions)
        summary = generate_decision_summary(scan)
        self.assertEqual(summary.high_impact_count, 1)
        self.assertEqual(summary.high_risk_count, 1)
        self.assertEqual(summary.manual_review_count, 1)
        self.assertEqual(summary.top_priority_dependencies[0].package, "complex_pkg")
        self.assertEqual(summary.top_priority_dependencies[0].effort, "Complex/Manual")

    def test_7_low_impact_low_risk_dependency(self):
        declared = ["simple_pkg"]
        usage = {"simple_pkg": {"status": "REPLACEABLE", "confidence": "HIGH"}}
        impacts = {"simple_pkg": _MockImpact(level="LOW")}
        risks = {"simple_pkg": _MockRisk(level="LOW", rec="SAFE MIGRATION CANDIDATE")}
        actions = _MockActionPlan(project_prio="P2", items=[
            _MockActionItem("simple_pkg", "P2", "SAFE MIGRATION CANDIDATE", effort="Trivial")
        ])
        scan = _make_scan(declared=declared, usage=usage, impacts=impacts, risks=risks, action_plan=actions)
        summary = generate_decision_summary(scan)
        self.assertEqual(summary.high_impact_count, 0)
        self.assertEqual(summary.high_risk_count, 0)
        self.assertEqual(summary.safe_migration_count, 1)

    def test_8_dynamic_import_blocker(self):
        scan = _make_scan(
            declared=["click"],
            usage={"click": {"status": "REPLACEABLE", "confidence": "HIGH"}},
            dynamic_imports=[_MockDynamicImport(file_path="app.py", line_no=42, target=None)]
        )
        summary = generate_decision_summary(scan)
        self.assertTrue(any("Dynamic import" in b for b in summary.blockers))
        self.assertIn(summary.migration_readiness, ["PARTIALLY READY", "BLOCKED"])

    def test_9_star_import_uncertainty(self):
        scan = _make_scan(
            declared=["requests"],
            usage={"requests": {"status": "REPLACEABLE", "confidence": "MEDIUM"}},
            star_imports=["helpers"]
        )
        summary = generate_decision_summary(scan)
        self.assertTrue(any("Star import" in b for b in summary.blockers))

    def test_10_circular_dependency_blocker(self):
        scan = _make_scan(
            declared=["dep1"],
            usage={"dep1": {"status": "ESSENTIAL"}},
            graph=_MockGraph(cycles=[["mod_a", "mod_b", "mod_a"]])
        )
        summary = generate_decision_summary(scan)
        self.assertTrue(any("Circular dependency" in b for b in summary.blockers))
        self.assertEqual(summary.migration_readiness, "BLOCKED")

    def test_11_deterministic_ranking(self):
        declared = ["pkg_c", "pkg_a", "pkg_b"]
        usage = {
            "pkg_c": {"status": "REPLACEABLE", "occurrences": 10},
            "pkg_a": {"status": "REPLACEABLE", "occurrences": 5},
            "pkg_b": {"status": "REPLACEABLE", "occurrences": 5},
        }
        actions = _MockActionPlan(project_prio="P0", items=[
            _MockActionItem("pkg_c", "P0", "MANUAL REVIEW"),
            _MockActionItem("pkg_a", "P1", "REMOVE"),
            _MockActionItem("pkg_b", "P1", "REMOVE"),
        ])
        scan = _make_scan(declared=declared, usage=usage, action_plan=actions)
        summary = generate_decision_summary(scan)
        top_names = [t.package for t in summary.top_priority_dependencies]
        self.assertEqual(top_names[0], "pkg_c")
        # pkg_a and pkg_b tied on P1 and 5 occurrences -> alphabetical
        self.assertEqual(top_names[1], "pkg_a")
        self.assertEqual(top_names[2], "pkg_b")

    def test_12_deterministic_repeated_output(self):
        scan = _make_scan(
            declared=["click", "requests", "colorama"],
            usage={
                "click": {"status": "REPLACEABLE", "confidence": "HIGH"},
                "requests": {"status": "REPLACEABLE", "confidence": "LOW"},
                "colorama": {"status": "UNUSED", "confidence": "HIGH"},
            }
        )
        res1 = generate_decision_summary(scan).to_dict()
        res2 = generate_decision_summary(scan).to_dict()
        self.assertEqual(json.dumps(res1, sort_keys=True), json.dumps(res2, sort_keys=True))

    def test_13_correct_3x3_decision_matrix(self):
        scan = _make_scan(declared=["click"])
        summary = generate_decision_summary(scan)
        matrix = summary.decision_matrix
        self.assertIn("LOW IMPACT", matrix)
        self.assertIn("MEDIUM IMPACT", matrix)
        self.assertIn("HIGH IMPACT", matrix)
        self.assertEqual(matrix["LOW IMPACT"]["LOW RISK"], "REMOVE")
        self.assertEqual(matrix["MEDIUM IMPACT"]["LOW RISK"], "MIGRATE")
        self.assertEqual(matrix["HIGH IMPACT"]["HIGH RISK"], "BLOCK/MANUAL")

    def test_14_migration_readiness_classification(self):
        # READY case
        scan_ready = _make_scan(
            declared=["click"],
            usage={"click": {"status": "REPLACEABLE", "confidence": "HIGH"}},
            action_plan=_MockActionPlan(project_prio="P2", items=[_MockActionItem("click", "P2", "SAFE MIGRATION CANDIDATE")])
        )
        self.assertEqual(generate_decision_summary(scan_ready).migration_readiness, "READY")

        # BLOCKED case
        scan_blocked = _make_scan(
            declared=["click"],
            graph=_MockGraph(cycles=[["a", "b", "a"]])
        )
        self.assertEqual(generate_decision_summary(scan_blocked).migration_readiness, "BLOCKED")

    def test_15_json_schema(self):
        scan = _make_scan(declared=["click"])
        summary_dict = generate_decision_summary(scan).to_dict()
        required_keys = [
            "total_dependencies", "unused_count", "replaceable_count", "essential_count",
            "high_risk_count", "high_impact_count", "safe_migration_count", "manual_review_count",
            "project_priority", "current_dependency_count", "projected_dependency_count",
            "potential_reduction_count", "potential_reduction_percentage", "migration_readiness",
            "top_priority_dependencies", "top_risk_dependencies", "top_impact_dependencies",
            "blockers", "recommended_next_action", "decision_matrix", "category_counts"
        ]
        for key in required_keys:
            self.assertIn(key, summary_dict)

    def test_16_cli_integration(self):
        from zerodep.cli import scan_project
        # Scan fixtures case_b_medium
        fixture_path = REPO_ROOT / "fixtures" / "case_b_medium"
        if fixture_path.exists():
            scan = scan_project(fixture_path)
            self.assertIn("summary", scan)
            self.assertIsInstance(scan["summary"], DecisionSummaryResult)

    def test_17_existing_phase_5_simulation_integration(self):
        actions = _MockActionPlan(items=[
            _MockActionItem("click", "P2", "SAFE MIGRATION CANDIDATE"),
            _MockActionItem("colorama", "P1", "REMOVE"),
        ])
        scan = _make_scan(
            declared=["click", "colorama"],
            usage={
                "click": {"status": "REPLACEABLE", "confidence": "HIGH"},
                "colorama": {"status": "UNUSED", "confidence": "HIGH"},
            },
            action_plan=actions,
            include_simulation=True
        )
        summary = generate_decision_summary(scan)
        self.assertEqual(summary.current_dependency_count, 2)
        self.assertEqual(summary.projected_dependency_count, 0)
        self.assertEqual(summary.potential_reduction_count, 2)

    def test_18_no_source_modification(self):
        """Verifies byte-identity of target files before and after summary generation."""
        with tempfile.TemporaryDirectory() as tmpdir:
            proj = Path(tmpdir)
            pfile = proj / "main.py"
            pfile.write_text("import click\nclick.echo('hi')\n")

            hash_before = hashlib.sha256(pfile.read_bytes()).hexdigest()
            scan = _make_scan(declared=["click"])
            scan["target_dir"] = str(proj)

            generate_decision_summary(scan)
            hash_after = hashlib.sha256(pfile.read_bytes()).hexdigest()
            self.assertEqual(hash_before, hash_after)

    def test_19_standard_library_only_implementation(self):
        import zerodep.summary_engine as se
        for attr_name in dir(se):
            attr = getattr(se, attr_name)
            if hasattr(attr, "__module__") and attr.__module__:
                mod = attr.__module__.split(".")[0]
                self.assertTrue(
                    mod in sys.stdlib_module_names or mod in ("zerodep", "typing", "dataclasses"),
                    f"Third-party module found: {mod}"
                )

    def test_20_bundle_integration(self):
        bundle_path = REPO_ROOT / "dist" / "zerodep.py"
        self.assertTrue(bundle_path.exists())
        content = bundle_path.read_text(encoding="utf-8")
        self.assertIn("class DecisionSummaryResult", content)
        self.assertIn("def generate_decision_summary", content)


if __name__ == "__main__":
    unittest.main()
