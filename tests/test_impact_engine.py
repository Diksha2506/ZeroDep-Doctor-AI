"""Unit tests for zerodep.impact_engine — using standard unittest framework."""

import json
from pathlib import Path
import unittest

from zerodep.cli import scan_project
from zerodep.impact_engine import calculate_dependency_impact, DependencyImpact
from zerodep.graph_engine import ZeroDepGraph
from zerodep.ast_visitor import ASTAnalysisResult, ImportBinding, CallSite, DynamicImport


class TestImpactEngine(unittest.TestCase):

    def test_unused_dependency_low_impact(self):
        scan_data = {
            "declared_deps": {"click"},
            "used_deps": set(),
            "usage_details": {
                "click": {
                    "status": "UNUSED",
                    "confidence": "HIGH",
                    "files": [],
                    "occurrences": 0,
                    "symbols": {},
                    "imports_count": 0,
                }
            },
            "graph": ZeroDepGraph(),
            "ast_results": [],
        }
        impact = calculate_dependency_impact("click", scan_data)
        self.assertEqual(impact.level, "LOW")
        self.assertEqual(impact.files_affected, 0)
        self.assertEqual(impact.call_sites, 0)
        self.assertEqual(impact.estimated_migration_effort, "Trivial")

    def test_single_file_dependency_low_impact(self):
        g = ZeroDepGraph()
        g.add_node("app.py")
        scan_data = {
            "declared_deps": {"colorama"},
            "used_deps": {"colorama"},
            "usage_details": {
                "colorama": {
                    "status": "REPLACEABLE",
                    "confidence": "HIGH",
                    "files": ["app.py"],
                    "occurrences": 2,
                    "symbols": {"colorama.init": 1, "colorama.Fore": 1},
                    "imports_count": 1,
                }
            },
            "graph": g,
            "ast_results": [],
        }
        impact = calculate_dependency_impact("colorama", scan_data)
        self.assertEqual(impact.level, "LOW")
        self.assertEqual(impact.files_affected, 1)
        self.assertEqual(impact.modules_affected, 1)
        self.assertEqual(impact.call_sites, 2)

    def test_multi_file_high_call_site_impact(self):
        g = ZeroDepGraph()
        for i in range(5):
            g.add_node(f"file_{i}.py")
        
        scan_data = {
            "declared_deps": {"requests"},
            "used_deps": {"requests"},
            "usage_details": {
                "requests": {
                    "status": "REPLACEABLE",
                    "confidence": "LOW",
                    "files": [f"file_{i}.py" for i in range(5)],
                    "occurrences": 20,
                    "symbols": {"requests.get": 15, "requests.Session": 5},
                    "imports_count": 5,
                }
            },
            "graph": g,
            "ast_results": [],
        }
        impact = calculate_dependency_impact("requests", scan_data)
        self.assertEqual(impact.level, "HIGH")
        self.assertEqual(impact.files_affected, 5)
        self.assertEqual(impact.call_sites, 20)
        self.assertEqual(impact.api_complexity, "HIGH")
        self.assertEqual(impact.migration_confidence, "LOW")

    def test_hub_dependency(self):
        g = ZeroDepGraph()
        # Make core.py a hub module with 4 dependents
        for i in range(4):
            g.add_edge(f"mod_{i}.py", "core.py")

        scan_data = {
            "declared_deps": {"core_pkg"},
            "used_deps": {"core_pkg"},
            "usage_details": {
                "core_pkg": {
                    "status": "ESSENTIAL",
                    "confidence": "HIGH",
                    "files": ["core.py"],
                    "occurrences": 10,
                    "symbols": {"core_pkg.fn": 10},
                    "imports_count": 1,
                }
            },
            "graph": g,
            "ast_results": [],
        }
        impact = calculate_dependency_impact("core_pkg", scan_data)
        self.assertTrue(impact.is_graph_hub)
        self.assertIn("Hub dependency", "".join(impact.reasons))

    def test_hub_connected_dependency(self):
        g = ZeroDepGraph()
        # hub_mod.py is a hub
        for i in range(4):
            g.add_edge(f"client_{i}.py", "hub_mod.py")
        # client.py connects to hub_mod.py
        g.add_edge("client.py", "hub_mod.py")

        scan_data = {
            "declared_deps": {"sub_pkg"},
            "used_deps": {"sub_pkg"},
            "usage_details": {
                "sub_pkg": {
                    "status": "ESSENTIAL",
                    "confidence": "HIGH",
                    "files": ["client.py"],
                    "occurrences": 3,
                    "symbols": {"sub_pkg.run": 3},
                    "imports_count": 1,
                }
            },
            "graph": g,
            "ast_results": [],
        }
        impact = calculate_dependency_impact("sub_pkg", scan_data)
        self.assertTrue(impact.is_hub_connected)

    def test_circular_dependency_involvement(self):
        g = ZeroDepGraph()
        # Create cycle between mod_a.py and mod_b.py
        g.add_edge("mod_a.py", "mod_b.py")
        g.add_edge("mod_b.py", "mod_a.py")

        scan_data = {
            "declared_deps": {"cyc_pkg"},
            "used_deps": {"cyc_pkg"},
            "usage_details": {
                "cyc_pkg": {
                    "status": "ESSENTIAL",
                    "confidence": "HIGH",
                    "files": ["mod_a.py"],
                    "occurrences": 3,
                    "symbols": {"cyc_pkg.fn": 3},
                    "imports_count": 1,
                }
            },
            "graph": g,
            "ast_results": [],
        }
        impact = calculate_dependency_impact("cyc_pkg", scan_data)
        self.assertTrue(impact.circular_dependency_involvement)
        self.assertIn("circular dependency", "".join(impact.reasons).lower())

    def test_dynamic_conditional_star_imports(self):
        res = ASTAnalysisResult(
            file_path="app.py",
            imports=[
                ImportBinding("dynamic_pkg", "x", "x", 1, False, is_conditional=True, is_star=True)
            ],
            call_sites=[],
            dynamic_imports=[DynamicImport("app.py", 2, "importlib", target_module="dynamic_pkg")],
            star_imports=["dynamic_pkg"],
        )

        scan_data = {
            "declared_deps": {"dynamic_pkg"},
            "used_deps": {"dynamic_pkg"},
            "usage_details": {
                "dynamic_pkg": {
                    "status": "ESSENTIAL",
                    "confidence": "MEDIUM",
                    "files": ["app.py"],
                    "occurrences": 1,
                    "symbols": {"dynamic_pkg.x": 1},
                    "imports_count": 1,
                }
            },
            "graph": ZeroDepGraph(),
            "ast_results": [res],
        }
        impact = calculate_dependency_impact("dynamic_pkg", scan_data)
        self.assertTrue(impact.has_dynamic_imports)
        self.assertTrue(impact.has_conditional_imports)
        self.assertTrue(impact.has_star_imports)

    def test_deterministic_results(self):
        g = ZeroDepGraph()
        g.add_node("main.py")
        scan_data = {
            "declared_deps": {"pkg"},
            "used_deps": {"pkg"},
            "usage_details": {
                "pkg": {
                    "status": "ESSENTIAL",
                    "confidence": "HIGH",
                    "files": ["main.py"],
                    "occurrences": 5,
                    "symbols": {"pkg.run": 5},
                    "imports_count": 1,
                }
            },
            "graph": g,
            "ast_results": [],
        }
        res1 = calculate_dependency_impact("pkg", scan_data).to_dict()
        res2 = calculate_dependency_impact("pkg", scan_data).to_dict()
        self.assertEqual(res1, res2)

    def test_cli_and_json_integration_test_fixture(self):
        demo_path = Path(__file__).parent.parent / "demo"
        if not demo_path.exists():
            return
        scan = scan_project(demo_path)
        self.assertIn("impacts", scan)
        self.assertIn("requests", scan["impacts"])
        
        req_impact = scan["impacts"]["requests"]
        self.assertIsInstance(req_impact, DependencyImpact)
        self.assertIn(req_impact.level, ("LOW", "MEDIUM", "HIGH"))
        
        # Test JSON conversion integration
        json_str = json.dumps(scan["impacts"]["requests"].to_dict())
        self.assertIn("files_affected", json_str)
        self.assertIn("level", json_str)


if __name__ == "__main__":
    unittest.main()
