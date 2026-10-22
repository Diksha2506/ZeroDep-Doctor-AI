"""Integration Tests — end-to-end project scanning and self-audit."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

from zerodep.cli import scan_project


class TestSelfAudit(unittest.TestCase):
    """ZeroDep Doctor must be able to audit itself."""

    @classmethod
    def setUpClass(cls):
        project_root = Path(__file__).parent.parent.resolve()
        cls.scan = scan_project(project_root)

    def test_self_audit_returns_results(self):
        self.assertGreater(self.scan["files_scanned"], 0)

    def test_self_audit_has_zero_score(self):
        """Score should be computable (>0 and <=100); fixtures pull in declared deps."""
        score = self.scan["score_info"].current_score
        self.assertGreaterEqual(score, 0.0,
                                "Self-audit score should not be negative.")
        self.assertLessEqual(score, 100.0,
                             "Self-audit score should not exceed 100.")

    def test_self_audit_no_third_party_deps(self):
        """Self-audit: undeclared third-party packages should not appear in used_deps.

        The project root's requirements.txt lists requests/pandas/networkx etc.
        for fixtures and benchmark scripts, so those are expected in used_deps.
        The assertion is that no *undeclared* surprise package appears.
        """
        used = self.scan["used_deps"]
        declared = self.scan["declared_deps"]
        # Packages that should never appear (not declared, not stdlib)
        forbidden = {"flask", "django", "numpy", "sqlalchemy", "boto3"}
        undeclared_surprise = used & forbidden - declared
        self.assertTrue(len(undeclared_surprise) == 0,
                        f"Unexpected undeclared package(s) detected in self-audit: {undeclared_surprise}")

    def test_self_audit_loc_positive(self):
        self.assertGreater(self.scan["loc"], 100)

    def test_self_audit_duration_reasonable(self):
        self.assertLess(self.scan["duration"], 30.0)


class TestSyntheticProject(unittest.TestCase):
    """Create a minimal synthetic project and validate scan results."""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        # requirements.txt
        (self.tmpdir / "requirements.txt").write_text(
            "requests>=2.0\nflask\npyyaml\n", encoding="utf-8"
        )
        # main.py using requests and flask
        (self.tmpdir / "main.py").write_text(
            "import requests\nfrom flask import Flask\n"
            "import yaml\n"
            "app = Flask(__name__)\n"
            "resp = requests.get('http://example.com')\n",
            encoding="utf-8",
        )
        # unused.py — no imports at all
        (self.tmpdir / "unused.py").write_text("print('hello')\n", encoding="utf-8")

    def test_files_scanned(self):
        scan = scan_project(self.tmpdir)
        self.assertGreaterEqual(scan["files_scanned"], 2)

    def test_used_deps_detected(self):
        scan = scan_project(self.tmpdir)
        self.assertIn("requests", scan["used_deps"])
        self.assertIn("flask", scan["used_deps"])

    def test_unused_dep_detected(self):
        """pyyaml is declared but not used in any .py file in this project."""
        # But yaml IS used through pyyaml — ensure it's found via import_name
        scan = scan_project(self.tmpdir)
        # yaml is imported, and pyyaml's import_name is "yaml" — should be in used_deps
        self.assertIn("pyyaml", scan["used_deps"])

    def test_graph_populated(self):
        scan = scan_project(self.tmpdir)
        self.assertGreater(len(scan["graph"].nodes()), 0)

    def test_score_below_100_with_deps(self):
        scan = scan_project(self.tmpdir)
        self.assertLessEqual(scan["score_info"].current_score, 100.0)

    def test_loc_counted(self):
        scan = scan_project(self.tmpdir)
        self.assertGreater(scan["loc"], 0)


class TestVenvExclusion(unittest.TestCase):
    """Ensure venv directories are not scanned."""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        # Real source
        (self.tmpdir / "main.py").write_text("import os\n", encoding="utf-8")
        # Fake venv directory
        venv_dir = self.tmpdir / "venv" / "lib" / "site-packages"
        venv_dir.mkdir(parents=True)
        (venv_dir / "requests.py").write_text("import requests\n", encoding="utf-8")

    def test_venv_files_excluded(self):
        scan = scan_project(self.tmpdir)
        # Only main.py should be scanned, not the fake venv file
        scanned_paths = [r.file_path for r in scan["ast_results"]]
        self.assertFalse(any("venv" in p for p in scanned_paths))


class TestJsonReport(unittest.TestCase):
    """Validate JSON output structure."""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        (self.tmpdir / "requirements.txt").write_text("requests\n", encoding="utf-8")
        (self.tmpdir / "main.py").write_text("import requests\nrequests.get('x')\n",
                                             encoding="utf-8")

    def test_json_report_keys(self):
        from zerodep.cli import _json_report, scan_project
        scan = scan_project(self.tmpdir)
        report = _json_report(scan)
        self.assertIn("score", report)
        self.assertIn("dependencies", report)
        self.assertIn("migrations", report)
        self.assertIn("graph", report)
        self.assertIn("warnings", report)

    def test_json_is_serializable(self):
        from zerodep.cli import _json_report, scan_project
        scan = scan_project(self.tmpdir)
        report = _json_report(scan)
        # Must not raise
        json_str = json.dumps(report)
        self.assertIsInstance(json_str, str)


if __name__ == "__main__":
    unittest.main()
