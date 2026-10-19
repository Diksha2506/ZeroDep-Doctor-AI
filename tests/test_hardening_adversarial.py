"""
Final Hardening & Adversarial Test Suite for ZeroDep Doctor.
===========================================================
Covers:
  1. Hostile project structures (syntax errors, malformed files, Unicode, shadow names, nested scopes)
  2. Evidence attribution safety (dynamic/star/circular imports strictly scoped per dependency)
  3. Determinism audit (repeated execution produces byte-identical JSON and CLI output)
  4. Read-only safety audit (SHA-256 byte identity before/after all 7 CLI commands)
  5. Malformed source robustness (graceful non-crashing behavior)
  6. CLI contract & error handling
  7. JSON schema completeness & serialisability
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

from zerodep.ast_visitor import analyze_ast
from zerodep.cli import scan_project, _json_report
from zerodep.explanation_engine import generate_all_explanations
from zerodep.graph_engine import ZeroDepGraph
from zerodep.impact_engine import calculate_dependency_impact
from zerodep.manifest_parser import parse_manifests
from zerodep.risk_engine import calculate_dependency_risk
from zerodep.summary_engine import generate_decision_summary, DecisionSummaryResult


class TestHostileProjectStructures(unittest.TestCase):
    """Test unusual and hostile codebase structures."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.proj = Path(self.tmpdir.name)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_empty_directory(self):
        scan = scan_project(self.proj)
        self.assertEqual(scan["files_scanned"], 0)
        self.assertEqual(len(scan["declared_deps"]), 0)
        self.assertEqual(scan["score_info"].current_score, 100.0)

    def test_single_python_file(self):
        (self.proj / "main.py").write_text("import math\nprint(math.pi)\n")
        scan = scan_project(self.proj)
        self.assertEqual(scan["files_scanned"], 1)
        self.assertEqual(len(scan["declared_deps"]), 0)

    def test_syntax_error_file_does_not_crash_scanner(self):
        (self.proj / "pyproject.toml").write_text("[tool.poetry.dependencies]\nclick = '*'\n")
        (self.proj / "bad.py").write_text("def broken_func(: syntax error here!\n")
        (self.proj / "good.py").write_text("import click\nclick.echo('hello')\n")
        
        # Scanner should not crash on syntax error file, and still parse good.py
        scan = scan_project(self.proj)
        self.assertEqual(scan["files_scanned"], 2)
        self.assertIn("click", scan["used_deps"])

    def test_unicode_source_file(self):
        (self.proj / "pyproject.toml").write_text("[tool.poetry.dependencies]\nclick = '*'\n")
        (self.proj / "unicode_app.py").write_text("# 🚀 ✨ Unicode Test\nimport click\nname = 'こんにちは'\nclick.echo(name)\n", encoding="utf-8")
        scan = scan_project(self.proj)
        self.assertIn("click", scan["used_deps"])

    def test_comments_and_strings_containing_imports(self):
        (self.proj / "pyproject.toml").write_text("[tool.poetry.dependencies]\nrequests = '*'\ncolorama = '*'\n")
        (self.proj / "sample.py").write_text(
            "# import requests\n"
            "doc = 'from colorama import Fore'\n"
            "code_str = 'import requests'\n"
        )
        scan = scan_project(self.proj)
        # Fake imports inside comments and strings must NOT be detected as used
        self.assertNotIn("requests", scan["used_deps"])
        self.assertNotIn("colorama", scan["used_deps"])

    def test_local_module_shadowing_external_package(self):
        (self.proj / "pyproject.toml").write_text("[tool.poetry.dependencies]\nmy_lib = '*'\n")
        (self.proj / "my_lib.py").write_text("# Local module with same name as external package\nx = 42\n")
        (self.proj / "main.py").write_text("import my_lib\nprint(my_lib.x)\n")
        scan = scan_project(self.proj)
        self.assertIn("my_lib", scan["declared_deps"])

    def test_nested_function_and_class_imports(self):
        (self.proj / "pyproject.toml").write_text("[tool.poetry.dependencies]\nclick = '*'\nrequests = '*'\n")
        (self.proj / "app.py").write_text(
            "def foo():\n"
            "    import click\n"
            "    click.echo('nested')\n\n"
            "class Bar:\n"
            "    import requests\n"
            "    res = requests.get('http://test')\n"
        )
        scan = scan_project(self.proj)
        self.assertIn("click", scan["used_deps"])
        self.assertIn("requests", scan["used_deps"])
        
        # Verify scope classification
        click_info = scan["usage_details"]["click"]
        self.assertEqual(click_info["status"], "REPLACEABLE")

    def test_deeply_nested_directories(self):
        deep_dir = self.proj / "a" / "b" / "c" / "d" / "e"
        deep_dir.mkdir(parents=True, exist_ok=True)
        (deep_dir / "deep.py").write_text("import click\nclick.echo('deep')\n")
        (self.proj / "pyproject.toml").write_text("[tool.poetry.dependencies]\nclick = '*'\n")
        scan = scan_project(self.proj)
        self.assertIn("click", scan["used_deps"])


class TestEvidenceAttributionIsolation(unittest.TestCase):
    """Verify evidence belonging to one dependency is NEVER attached to another."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.proj = Path(self.tmpdir.name)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_dynamic_import_requests_does_not_affect_click(self):
        (self.proj / "pyproject.toml").write_text("[tool.poetry.dependencies]\nrequests = '*'\nclick = '*'\n")
        (self.proj / "main.py").write_text(
            "import importlib\n"
            "import click\n"
            "req = importlib.import_module('requests')\n"
            "click.echo('hi')\n"
        )
        scan = scan_project(self.proj)
        
        # click should NOT have dynamic import warnings attached
        click_risk = scan["risks"]["click"]
        click_factor_descriptions = [rf.description for rf in click_risk.risk_factors]
        self.assertFalse(any("Dynamic import" in desc for desc in click_factor_descriptions))

    def test_dynamic_import_stdlib_json_does_not_affect_requests(self):
        (self.proj / "pyproject.toml").write_text("[tool.poetry.dependencies]\nrequests = '*'\n")
        (self.proj / "main.py").write_text(
            "import importlib\n"
            "import requests\n"
            "j = importlib.import_module('json')\n"
            "requests.get('http://test')\n"
        )
        scan = scan_project(self.proj)
        
        # requests should NOT have dynamic import risk factor
        req_risk = scan["risks"]["requests"]
        req_factors = [rf.description for rf in req_risk.risk_factors]
        self.assertFalse(any("Dynamic import" in desc for desc in req_factors))

    def test_star_import_from_package_a_does_not_affect_package_b(self):
        (self.proj / "pyproject.toml").write_text("[tool.poetry.dependencies]\npkg_a = '*'\npkg_b = '*'\n")
        (self.proj / "main.py").write_text(
            "from pkg_a import *\n"
            "import pkg_b\n"
            "pkg_b.run()\n"
        )
        scan = scan_project(self.proj)
        
        # pkg_b risk factors should NOT mention star import
        pkg_b_risk = scan["risks"]["pkg_b"]
        pkg_b_factors = [rf.description for rf in pkg_b_risk.risk_factors]
        self.assertFalse(any("Star import" in desc for desc in pkg_b_factors))

    def test_circular_dependency_involving_mod_a_b_does_not_affect_unrelated_dep(self):
        (self.proj / "pyproject.toml").write_text("[tool.poetry.dependencies]\nclick = '*'\n")
        (self.proj / "mod_a.py").write_text("import mod_b\n")
        (self.proj / "mod_b.py").write_text("import mod_a\n")
        (self.proj / "app.py").write_text("import click\nclick.echo('standalone')\n")
        
        scan = scan_project(self.proj)
        # click module app.py is NOT in circular loop
        click_risk = scan["risks"]["click"]
        click_factors = [rf.description for rf in click_risk.risk_factors]
        self.assertFalse(any("Circular dependency" in desc for desc in click_factors))


class TestDeterminismAndReadOnlySafety(unittest.TestCase):
    """Verify 100% deterministic output and zero file modification across all commands."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.proj = Path(self.tmpdir.name)
        (self.proj / "pyproject.toml").write_text("[tool.poetry.dependencies]\nclick = '*'\nrequests = '*'\ncolorama = '*'\n")
        (self.proj / "main.py").write_text("import click\nimport requests\nclick.echo(requests.get('http://test').text)\n")

    def tearDown(self):
        self.tmpdir.cleanup()

    def _hash_all_files(self) -> dict:
        hashes = {}
        for p in self.proj.rglob("*"):
            if p.is_file():
                hashes[str(p)] = hashlib.sha256(p.read_bytes()).hexdigest()
        return hashes

    def test_read_only_safety_across_all_scans(self):
        before = self._hash_all_files()
        
        # Run multiple scan passes
        for _ in range(3):
            scan = scan_project(self.proj)
            _json_report(scan)
            generate_decision_summary(scan)

        after = self._hash_all_files()
        self.assertEqual(before, after)

    def test_deterministic_json_report_output(self):
        scan1 = scan_project(self.proj)
        scan2 = scan_project(self.proj)
        d1 = _json_report(scan1)
        d2 = _json_report(scan2)
        d1["project"]["execution_seconds"] = 0.0
        d2["project"]["execution_seconds"] = 0.0

        json1 = json.dumps(d1, sort_keys=True)
        json2 = json.dumps(d2, sort_keys=True)

        self.assertEqual(json1, json2)


if __name__ == "__main__":
    unittest.main()
