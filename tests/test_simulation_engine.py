"""
Phase 5 Tests: Dependency Migration Simulator — "What-If?" Analysis
===================================================================
Covers:
  - SimulationResult dataclass fields and to_dict()
  - simulate_migration() in all three modes: recommended / selected / all_safe
  - Projected counts and reduction_percentage accuracy
  - Before / after footprint correctness
  - Blockers and assumptions population
  - Dynamic-import project-level blockers
  - Simulation confidence (HIGH / MEDIUM / LOW)
  - Zero source-file modification (byte-identity test)
  - JSON serialisation round-trip
  - Edge cases: empty deps, no action plan, all-unused, all-essential
"""

import hashlib
import json
import os
import sys
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path

# ---------------------------------------------------------------------------
# Path fixup so tests can import zerodep modules without installation
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))

from zerodep.simulation_engine import SimulationResult, simulate_migration, DEFAULT_ASSUMPTIONS


# ---------------------------------------------------------------------------
# Minimal helpers to build synthetic scan_data dicts
# ---------------------------------------------------------------------------

class _MockImpact:
    def to_dict(self): return {"mock": True}

class _MockRisk:
    def to_dict(self): return {"mock": True}

class _MockItem:
    def __init__(self, pkg, action):
        self.package = pkg
        self.action = action

class _MockActionPlan:
    def __init__(self, items):
        self.items = items

    def to_dict(self):
        return {"items": [{"package": i.package, "action": i.action} for i in self.items]}


def _make_scan(
    declared: list[str] | None = None,
    usage: dict | None = None,
    action_items: list[tuple] | None = None,
    impacts: dict | None = None,
    risks: dict | None = None,
    dynamic_imports: list | None = None,
):
    """Build a minimal scan_data dict for simulate_migration()."""
    declared = declared or []
    usage = usage or {}
    impacts = impacts or {d: _MockImpact() for d in declared}
    risks = risks or {d: _MockRisk() for d in declared}

    items = []
    if action_items:
        for pkg, action in action_items:
            items.append(_MockItem(pkg, action))

    plan = _MockActionPlan(items) if items else _MockActionPlan([])

    return {
        "declared_deps": set(declared),
        "used_deps": {d for d, info in usage.items() if info.get("status") != "UNUSED"},
        "usage_details": usage,
        "impacts": impacts,
        "risks": risks,
        "action_plan": plan,
        "dynamic_imports": dynamic_imports or [],
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestSimulationResultDataclass(unittest.TestCase):
    """Test the SimulationResult dataclass directly."""

    def _make_result(self, **overrides) -> SimulationResult:
        defaults = dict(
            mode="recommended",
            confidence="HIGH",
            current_count=5,
            projected_count=3,
            reduction_count=2,
            reduction_percentage=40.0,
            removed=["pkg_a"],
            migrated=["pkg_b"],
            manual_review=[],
            retained=["pkg_c", "pkg_d", "pkg_e"],
            blockers=[],
            assumptions={"pkg_b": "Can migrate to stdlib"},
            before_footprint=[{"package": "pkg_a", "status": "UNUSED"}],
            after_footprint=[{"package": "pkg_a", "simulated_state": "REMOVE"}],
        )
        defaults.update(overrides)
        return SimulationResult(**defaults)

    def test_dataclass_fields_present(self):
        r = self._make_result()
        self.assertEqual(r.mode, "recommended")
        self.assertEqual(r.confidence, "HIGH")
        self.assertEqual(r.current_count, 5)
        self.assertEqual(r.projected_count, 3)
        self.assertEqual(r.reduction_count, 2)
        self.assertAlmostEqual(r.reduction_percentage, 40.0)
        self.assertIn("pkg_a", r.removed)
        self.assertIn("pkg_b", r.migrated)
        self.assertIn("pkg_c", r.retained)

    def test_to_dict_structure(self):
        r = self._make_result()
        d = r.to_dict()
        self.assertIn("mode", d)
        self.assertIn("confidence", d)
        self.assertIn("current", d)
        self.assertIn("projected", d)
        self.assertIn("reduction", d)
        self.assertIn("removed", d)
        self.assertIn("migrated", d)
        self.assertIn("manual_review", d)
        self.assertIn("retained", d)
        self.assertIn("blockers", d)
        self.assertIn("assumptions", d)
        self.assertIn("before_footprint", d)
        self.assertIn("after_footprint", d)

    def test_to_dict_values(self):
        r = self._make_result()
        d = r.to_dict()
        self.assertEqual(d["current"]["dependency_count"], 5)
        self.assertEqual(d["projected"]["dependency_count"], 3)
        self.assertEqual(d["reduction"]["count"], 2)
        self.assertAlmostEqual(d["reduction"]["percentage"], 40.0)

    def test_to_dict_is_json_serialisable(self):
        r = self._make_result()
        d = r.to_dict()
        # Should not raise
        json_str = json.dumps(d)
        self.assertIsInstance(json_str, str)

    def test_json_round_trip(self):
        r = self._make_result()
        d = r.to_dict()
        decoded = json.loads(json.dumps(d))
        self.assertEqual(decoded["mode"], "recommended")
        self.assertEqual(decoded["current"]["dependency_count"], 5)
        self.assertEqual(decoded["reduction"]["percentage"], 40.0)


class TestSimulateMigrationRecommendedMode(unittest.TestCase):
    """Tests for mode='recommended'."""

    def _make_standard_scan(self):
        declared = ["click", "requests", "colorama"]
        usage = {
            "click": {"status": "REPLACEABLE", "confidence": "HIGH"},
            "requests": {"status": "REPLACEABLE", "confidence": "LOW"},
            "colorama": {"status": "REPLACEABLE", "confidence": "HIGH"},
        }
        actions = [
            ("click", "SAFE MIGRATION CANDIDATE"),
            ("requests", "MANUAL REVIEW"),
            ("colorama", "SAFE MIGRATION CANDIDATE"),
        ]
        return _make_scan(declared=declared, usage=usage, action_items=actions)

    def test_recommended_mode_returns_result(self):
        scan = self._make_standard_scan()
        result = simulate_migration(scan, mode="recommended")
        self.assertIsInstance(result, SimulationResult)

    def test_recommended_mode_field(self):
        scan = self._make_standard_scan()
        result = simulate_migration(scan, mode="recommended")
        self.assertEqual(result.mode, "recommended")

    def test_safe_migration_candidate_goes_to_migrated(self):
        scan = self._make_standard_scan()
        result = simulate_migration(scan, mode="recommended")
        # click: HIGH + SAFE MIGRATION CANDIDATE -> migrated
        self.assertIn("click", result.migrated)
        self.assertNotIn("click", result.removed)
        self.assertNotIn("click", result.manual_review)

    def test_manual_review_dep_goes_to_manual_review(self):
        scan = self._make_standard_scan()
        result = simulate_migration(scan, mode="recommended")
        self.assertIn("requests", result.manual_review)

    def test_manual_review_dep_retained(self):
        scan = self._make_standard_scan()
        result = simulate_migration(scan, mode="recommended")
        # MANUAL REVIEW deps still count as retained (not removed)
        self.assertIn("requests", result.retained)

    def test_projected_count_excludes_migrated_and_removed(self):
        scan = self._make_standard_scan()
        result = simulate_migration(scan, mode="recommended")
        # click + colorama should be migrated; requests in manual_review -> retained
        expected_projected = result.current_count - (len(result.migrated) + len(result.removed))
        self.assertEqual(result.projected_count, expected_projected)

    def test_reduction_count_is_consistent(self):
        scan = self._make_standard_scan()
        result = simulate_migration(scan, mode="recommended")
        self.assertEqual(result.reduction_count, result.current_count - result.projected_count)

    def test_reduction_percentage_math(self):
        scan = self._make_standard_scan()
        result = simulate_migration(scan, mode="recommended")
        if result.current_count > 0:
            expected_pct = round((result.reduction_count / result.current_count) * 100.0, 2)
            self.assertAlmostEqual(result.reduction_percentage, expected_pct, places=2)

    def test_before_footprint_has_all_declared_deps(self):
        scan = self._make_standard_scan()
        result = simulate_migration(scan, mode="recommended")
        before_pkgs = {item["package"] for item in result.before_footprint}
        self.assertEqual(before_pkgs, {"click", "requests", "colorama"})

    def test_after_footprint_has_all_declared_deps(self):
        scan = self._make_standard_scan()
        result = simulate_migration(scan, mode="recommended")
        after_pkgs = {item["package"] for item in result.after_footprint}
        self.assertEqual(after_pkgs, {"click", "requests", "colorama"})

    def test_after_footprint_has_simulated_state_key(self):
        scan = self._make_standard_scan()
        result = simulate_migration(scan, mode="recommended")
        for item in result.after_footprint:
            self.assertIn("simulated_state", item)


class TestSimulateMigrationRemoveUnused(unittest.TestCase):
    """Unused packages should appear as REMOVE in recommended/all_safe mode."""

    def _make_unused_scan(self):
        declared = ["pandas", "click"]
        usage = {
            "pandas": {"status": "UNUSED", "confidence": "HIGH"},
            "click": {"status": "REPLACEABLE", "confidence": "HIGH"},
        }
        actions = [
            ("pandas", "REMOVE"),
            ("click", "SAFE MIGRATION CANDIDATE"),
        ]
        return _make_scan(declared=declared, usage=usage, action_items=actions)

    def test_unused_dep_goes_to_removed(self):
        scan = self._make_unused_scan()
        result = simulate_migration(scan, mode="recommended")
        self.assertIn("pandas", result.removed)

    def test_unused_dep_not_in_migrated(self):
        scan = self._make_unused_scan()
        result = simulate_migration(scan, mode="recommended")
        self.assertNotIn("pandas", result.migrated)

    def test_unused_dep_assumption_is_set(self):
        scan = self._make_unused_scan()
        result = simulate_migration(scan, mode="recommended")
        self.assertIn("pandas", result.assumptions)
        self.assertIn("Unused manifest", result.assumptions["pandas"])

    def test_remove_reduces_projected_count(self):
        scan = self._make_unused_scan()
        result = simulate_migration(scan, mode="recommended")
        self.assertLess(result.projected_count, result.current_count)

    def test_after_state_is_remove_for_unused(self):
        scan = self._make_unused_scan()
        result = simulate_migration(scan, mode="recommended")
        state_map = {item["package"]: item["simulated_state"] for item in result.after_footprint}
        self.assertEqual(state_map["pandas"], "REMOVE")


class TestSimulateMigrationAllSafeMode(unittest.TestCase):
    """Tests for mode='all_safe'."""

    def _make_scan_with_essential(self):
        declared = ["click", "requests", "stdlib_dep"]
        usage = {
            "click": {"status": "REPLACEABLE", "confidence": "HIGH"},
            "requests": {"status": "REPLACEABLE", "confidence": "LOW"},
            "stdlib_dep": {"status": "ESSENTIAL", "confidence": "HIGH"},
        }
        return _make_scan(declared=declared, usage=usage)

    def test_all_safe_migrates_high_conf_replaceable(self):
        scan = self._make_scan_with_essential()
        result = simulate_migration(scan, mode="all_safe")
        self.assertIn("click", result.migrated)

    def test_all_safe_retains_low_conf(self):
        scan = self._make_scan_with_essential()
        result = simulate_migration(scan, mode="all_safe")
        # LOW confidence deps should NOT be migrated in all_safe
        self.assertNotIn("requests", result.migrated)
        self.assertNotIn("requests", result.removed)
        self.assertIn("requests", result.retained)

    def test_all_safe_retains_essential(self):
        scan = self._make_scan_with_essential()
        result = simulate_migration(scan, mode="all_safe")
        self.assertIn("stdlib_dep", result.retained)

    def test_all_safe_mode_field(self):
        scan = self._make_scan_with_essential()
        result = simulate_migration(scan, mode="all_safe")
        self.assertEqual(result.mode, "all_safe")


class TestSimulateMigrationSelectedMode(unittest.TestCase):
    """Tests for mode='selected' with explicit targets."""

    def _make_multi_scan(self):
        declared = ["click", "requests", "colorama", "pandas"]
        usage = {
            "click": {"status": "REPLACEABLE", "confidence": "HIGH"},
            "requests": {"status": "REPLACEABLE", "confidence": "HIGH"},
            "colorama": {"status": "REPLACEABLE", "confidence": "HIGH"},
            "pandas": {"status": "ESSENTIAL", "confidence": "HIGH"},
        }
        actions = [
            ("click", "SAFE MIGRATION CANDIDATE"),
            ("requests", "SAFE MIGRATION CANDIDATE"),
            ("colorama", "SAFE MIGRATION CANDIDATE"),
            ("pandas", "KEEP / NO ACTION"),
        ]
        return _make_scan(declared=declared, usage=usage, action_items=actions)

    def test_selected_mode_only_targets_provided(self):
        scan = self._make_multi_scan()
        result = simulate_migration(scan, mode="selected", targets={"click"})
        # Only 'click' should be migrated; requests/colorama untouched
        self.assertIn("click", result.migrated + result.manual_review + result.removed)
        self.assertIn("requests", result.retained)
        self.assertIn("colorama", result.retained)

    def test_selected_mode_multiple_targets(self):
        scan = self._make_multi_scan()
        result = simulate_migration(scan, mode="selected", targets={"click", "requests"})
        self.assertIn("click", result.migrated + result.removed)
        self.assertIn("requests", result.migrated + result.removed)
        self.assertIn("colorama", result.retained)

    def test_selected_mode_essential_untouched_unless_targeted(self):
        scan = self._make_multi_scan()
        result = simulate_migration(scan, mode="selected", targets={"click"})
        self.assertIn("pandas", result.retained)

    def test_selected_mode_field(self):
        scan = self._make_multi_scan()
        result = simulate_migration(scan, mode="selected", targets={"click"})
        self.assertEqual(result.mode, "selected")

    def test_selected_empty_targets_retains_all(self):
        scan = self._make_multi_scan()
        result = simulate_migration(scan, mode="selected", targets=set())
        # No targets -> all retained
        self.assertEqual(len(result.removed), 0)
        self.assertEqual(len(result.migrated), 0)
        self.assertEqual(result.reduction_count, 0)


class TestSimulationConfidence(unittest.TestCase):
    """Confidence level correctly reflects blocker presence."""

    def test_all_high_conf_gives_high_simulation_confidence(self):
        declared = ["click"]
        usage = {"click": {"status": "REPLACEABLE", "confidence": "HIGH"}}
        actions = [("click", "SAFE MIGRATION CANDIDATE")]
        scan = _make_scan(declared=declared, usage=usage, action_items=actions)
        result = simulate_migration(scan, mode="recommended")
        self.assertEqual(result.confidence, "HIGH")

    def test_manual_review_dep_gives_low_simulation_confidence(self):
        declared = ["requests"]
        usage = {"requests": {"status": "REPLACEABLE", "confidence": "LOW"}}
        actions = [("requests", "MANUAL REVIEW")]
        scan = _make_scan(declared=declared, usage=usage, action_items=actions)
        result = simulate_migration(scan, mode="recommended")
        self.assertEqual(result.confidence, "LOW")

    def test_project_level_dynamic_import_degrades_confidence(self):
        """Unknown-target dynamic imports should degrade simulation confidence to LOW."""
        class _DI:
            target_module = None
            file_path = "main.py"
            line_no = 10

        declared = ["click"]
        usage = {"click": {"status": "REPLACEABLE", "confidence": "HIGH"}}
        actions = [("click", "SAFE MIGRATION CANDIDATE")]
        scan = _make_scan(declared=declared, usage=usage, action_items=actions, dynamic_imports=[_DI()])
        result = simulate_migration(scan, mode="recommended")
        self.assertEqual(result.confidence, "LOW")

    def test_project_level_blocker_in_blockers_list(self):
        """Unknown-target dynamic import adds a PROJECT-level blocker entry."""
        class _DI:
            target_module = None
            file_path = "main.py"
            line_no = 5

        declared = ["click"]
        usage = {"click": {"status": "REPLACEABLE", "confidence": "HIGH"}}
        actions = [("click", "SAFE MIGRATION CANDIDATE")]
        scan = _make_scan(declared=declared, usage=usage, action_items=actions, dynamic_imports=[_DI()])
        result = simulate_migration(scan, mode="recommended")
        project_blockers = [b for b in result.blockers if b["package"] == "PROJECT"]
        self.assertGreater(len(project_blockers), 0)


class TestSimulationEdgeCases(unittest.TestCase):
    """Edge cases: empty project, all-essential, etc."""

    def test_empty_declared_deps(self):
        scan = _make_scan(declared=[], usage={})
        result = simulate_migration(scan, mode="recommended")
        self.assertEqual(result.current_count, 0)
        self.assertEqual(result.projected_count, 0)
        self.assertEqual(result.reduction_count, 0)
        self.assertEqual(result.reduction_percentage, 0.0)
        self.assertEqual(result.removed, [])
        self.assertEqual(result.migrated, [])
        self.assertEqual(result.retained, [])

    def test_all_essential_deps_all_retained(self):
        declared = ["dep_a", "dep_b"]
        usage = {
            "dep_a": {"status": "ESSENTIAL", "confidence": "HIGH"},
            "dep_b": {"status": "ESSENTIAL", "confidence": "HIGH"},
        }
        actions = [
            ("dep_a", "KEEP / NO ACTION"),
            ("dep_b", "KEEP / NO ACTION"),
        ]
        scan = _make_scan(declared=declared, usage=usage, action_items=actions)
        result = simulate_migration(scan, mode="recommended")
        self.assertEqual(len(result.removed), 0)
        self.assertEqual(len(result.migrated), 0)
        self.assertEqual(result.reduction_count, 0)
        self.assertEqual(result.projected_count, result.current_count)

    def test_no_action_plan(self):
        """simulate_migration handles scan_data without an action_plan gracefully."""
        scan = {
            "declared_deps": {"click"},
            "used_deps": {"click"},
            "usage_details": {"click": {"status": "REPLACEABLE", "confidence": "HIGH"}},
            "impacts": {},
            "risks": {},
            "action_plan": None,
            "dynamic_imports": [],
        }
        result = simulate_migration(scan, mode="recommended")
        self.assertIsInstance(result, SimulationResult)

    def test_projected_count_never_negative(self):
        """projected_count is clamped to zero."""
        scan = _make_scan(declared=[], usage={})
        result = simulate_migration(scan, mode="recommended")
        self.assertGreaterEqual(result.projected_count, 0)

    def test_single_dep_100_percent_reduction(self):
        declared = ["click"]
        usage = {"click": {"status": "UNUSED", "confidence": "HIGH"}}
        actions = [("click", "REMOVE")]
        scan = _make_scan(declared=declared, usage=usage, action_items=actions)
        result = simulate_migration(scan, mode="recommended")
        self.assertEqual(result.projected_count, 0)
        self.assertAlmostEqual(result.reduction_percentage, 100.0, places=2)


class TestSimulationNoSourceModification(unittest.TestCase):
    """
    Critical safety test: simulate_migration() MUST NEVER modify source files.
    Verifies byte-identity of every .py file before and after simulation.
    """

    def _hash_file(self, path: Path) -> str:
        h = hashlib.sha256()
        h.update(path.read_bytes())
        return h.hexdigest()

    def test_source_files_byte_identical_after_simulation(self):
        """Run simulate on a temporary project; all .py files must be unchanged."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a small synthetic Python project
            proj = Path(tmpdir)
            (proj / "main.py").write_text("import click\nimport requests\nclick.echo('hello')\n")
            (proj / "utils.py").write_text("import colorama\ncolorama.init()\n")
            (proj / "pyproject.toml").write_text("[tool.poetry.dependencies]\nclick = '*'\n")

            # Capture before-hashes
            py_files = list(proj.rglob("*.py")) + list(proj.rglob("*.toml"))
            before = {p: self._hash_file(p) for p in py_files}

            # Build synthetic scan_data pointing at tmpdir
            declared = ["click", "requests", "colorama"]
            usage = {
                "click": {"status": "REPLACEABLE", "confidence": "HIGH"},
                "requests": {"status": "REPLACEABLE", "confidence": "HIGH"},
                "colorama": {"status": "REPLACEABLE", "confidence": "HIGH"},
            }
            actions = [
                ("click", "SAFE MIGRATION CANDIDATE"),
                ("requests", "SAFE MIGRATION CANDIDATE"),
                ("colorama", "SAFE MIGRATION CANDIDATE"),
            ]
            scan = _make_scan(declared=declared, usage=usage, action_items=actions)
            scan["target_dir"] = str(proj)

            # Run simulation
            simulate_migration(scan, mode="recommended")
            simulate_migration(scan, mode="all_safe")
            simulate_migration(scan, mode="selected", targets={"click", "requests"})

            # Verify after-hashes are identical
            after = {p: self._hash_file(p) for p in py_files}
            for p in py_files:
                self.assertEqual(
                    before[p], after[p],
                    msg=f"simulate_migration() modified source file: {p}"
                )


class TestSimulationAssumptions(unittest.TestCase):
    """Assumptions map is correctly populated per-package."""

    def test_assumptions_keys_match_non_retained(self):
        """Every migrated/removed dep should have an assumption."""
        declared = ["click", "colorama"]
        usage = {
            "click": {"status": "REPLACEABLE", "confidence": "HIGH"},
            "colorama": {"status": "REPLACEABLE", "confidence": "HIGH"},
        }
        actions = [
            ("click", "SAFE MIGRATION CANDIDATE"),
            ("colorama", "SAFE MIGRATION CANDIDATE"),
        ]
        scan = _make_scan(declared=declared, usage=usage, action_items=actions)
        result = simulate_migration(scan, mode="recommended")
        for dep in result.migrated + result.removed:
            self.assertIn(dep, result.assumptions)

    def test_click_default_assumption_text(self):
        """click has a known assumption in DEFAULT_ASSUMPTIONS."""
        self.assertIn("click", DEFAULT_ASSUMPTIONS)
        self.assertIn("argparse", DEFAULT_ASSUMPTIONS["click"])

    def test_requests_default_assumption_text(self):
        """requests assumption mentions urllib."""
        self.assertIn("requests", DEFAULT_ASSUMPTIONS)
        self.assertIn("urllib", DEFAULT_ASSUMPTIONS["requests"])

    def test_unknown_package_gets_generic_assumption(self):
        """Packages not in DEFAULT_ASSUMPTIONS get a generic assumption string."""
        declared = ["some_custom_package"]
        usage = {"some_custom_package": {"status": "UNUSED", "confidence": "HIGH"}}
        actions = [("some_custom_package", "REMOVE")]
        scan = _make_scan(declared=declared, usage=usage, action_items=actions)
        result = simulate_migration(scan, mode="recommended")
        self.assertIn("some_custom_package", result.assumptions)
        # Should not raise; content should be a non-empty string
        self.assertIsInstance(result.assumptions["some_custom_package"], str)
        self.assertGreater(len(result.assumptions["some_custom_package"]), 0)


class TestSimulationJsonExport(unittest.TestCase):
    """Full JSON export round-trip for SimulationResult."""

    def _full_result(self) -> SimulationResult:
        declared = ["click", "requests", "pandas"]
        usage = {
            "click": {"status": "REPLACEABLE", "confidence": "HIGH"},
            "requests": {"status": "REPLACEABLE", "confidence": "LOW"},
            "pandas": {"status": "UNUSED", "confidence": "HIGH"},
        }
        actions = [
            ("click", "SAFE MIGRATION CANDIDATE"),
            ("requests", "MANUAL REVIEW"),
            ("pandas", "REMOVE"),
        ]
        scan = _make_scan(declared=declared, usage=usage, action_items=actions)
        return simulate_migration(scan, mode="recommended")

    def test_to_dict_full_serialisable(self):
        result = self._full_result()
        d = result.to_dict()
        # Full round-trip must not raise
        raw = json.dumps(d, indent=2)
        decoded = json.loads(raw)
        self.assertEqual(decoded["mode"], "recommended")

    def test_json_contains_before_after_footprint(self):
        result = self._full_result()
        d = result.to_dict()
        self.assertIn("before_footprint", d)
        self.assertIn("after_footprint", d)
        self.assertIsInstance(d["before_footprint"], list)
        self.assertIsInstance(d["after_footprint"], list)

    def test_json_simulation_confidence_key_present(self):
        result = self._full_result()
        d = result.to_dict()
        self.assertIn("confidence", d)

    def test_json_blockers_is_list(self):
        result = self._full_result()
        d = result.to_dict()
        self.assertIsInstance(d["blockers"], list)

    def test_json_assumptions_is_dict(self):
        result = self._full_result()
        d = result.to_dict()
        self.assertIsInstance(d["assumptions"], dict)


class TestSimulationWording(unittest.TestCase):
    """Verify simulation never uses forbidden wording ('Successfully migrated')."""

    def test_no_overclaiming_wording_in_assumptions(self):
        declared = ["requests"]
        usage = {"requests": {"status": "REPLACEABLE", "confidence": "HIGH"}}
        actions = [("requests", "SAFE MIGRATION CANDIDATE")]
        scan = _make_scan(declared=declared, usage=usage, action_items=actions)
        result = simulate_migration(scan, mode="recommended")
        for text in result.assumptions.values():
            self.assertNotIn("Successfully migrated", text)
            self.assertNotIn("successfully migrated", text)

    def test_no_overclaiming_wording_in_blockers(self):
        declared = ["requests"]
        usage = {"requests": {"status": "REPLACEABLE", "confidence": "LOW"}}
        actions = [("requests", "MANUAL REVIEW")]
        scan = _make_scan(declared=declared, usage=usage, action_items=actions)
        result = simulate_migration(scan, mode="recommended")
        for blk in result.blockers:
            self.assertNotIn("Successfully migrated", blk.get("reason", ""))


if __name__ == "__main__":
    unittest.main()
