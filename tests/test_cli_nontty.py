"""Comprehensive CLI & Non-TTY Integration Tests."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class TestCLIIntegration(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.project_root = Path(__file__).parent.parent.resolve()
        cls.dist_cli = cls.project_root / "dist" / "zerodep.py"
        cls.demo_dir = cls.project_root / "demo"

    def _run_cli(self, args, cwd=None, env=None):
        cmd = [sys.executable, str(self.dist_cli)] + args
        return subprocess.run(
            cmd,
            cwd=cwd or self.project_root,
            capture_output=True,
            text=True,
            env=env or os.environ.copy(),
            timeout=30,
        )

    def test_cli_help_exit_code(self):
        res = self._run_cli(["--help"])
        self.assertEqual(res.returncode, 0)
        self.assertIn("ZeroDep Doctor", res.stdout)

    def test_cli_analyze_command(self):
        res = self._run_cli(["analyze", str(self.demo_dir)])
        self.assertEqual(res.returncode, 0)
        self.assertIn("ZERODEP DOCTOR - AUDIT REPORT", res.stdout)

    def test_cli_dependencies_command(self):
        res = self._run_cli(["dependencies", str(self.demo_dir)])
        self.assertEqual(res.returncode, 0)
        self.assertIn("DEPENDENCY ANALYSIS & CONFIDENCE MODEL", res.stdout)

    def test_cli_graph_command(self):
        res = self._run_cli(["graph", str(self.demo_dir)])
        self.assertEqual(res.returncode, 0)
        self.assertIn("GRAPH ANALYSIS", res.stdout)

    def test_cli_replacements_command(self):
        res = self._run_cli(["replacements", str(self.demo_dir)])
        self.assertEqual(res.returncode, 0)
        self.assertIn("SUGGESTED MIGRATION DIFFS", res.stdout)

    def test_cli_invalid_path_error(self):
        res = self._run_cli(["analyze", "/nonexistent/path/for/zerodep/test"])
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("Error:", res.stderr)

    def test_cli_invalid_command_error(self):
        res = self._run_cli(["invalidcmd"])
        self.assertNotEqual(res.returncode, 0)

    def test_json_command_valid_json(self):
        res = self._run_cli(["report", str(self.demo_dir)])
        self.assertEqual(res.returncode, 0)
        data = json.loads(res.stdout)
        self.assertEqual(data["zerodep_doctor"], "1.0")

    def test_json_flag_valid_json(self):
        res = self._run_cli(["analyze", str(self.demo_dir), "--json"])
        self.assertEqual(res.returncode, 0)
        data = json.loads(res.stdout)
        self.assertIn("dependencies", data)

    def test_json_output_has_no_ansi_codes(self):
        res = self._run_cli(["report", str(self.demo_dir)])
        self.assertEqual(res.returncode, 0)
        self.assertNotIn("\033[", res.stdout)
        self.assertNotIn("\x1b[", res.stdout)

    def test_non_tty_pipe_no_ansi(self):
        """When output is captured via pipe, verify json report contains zero escape sequences."""
        res = self._run_cli(["report", str(self.demo_dir)])
        # Must be valid json without corruption
        data = json.loads(res.stdout)
        self.assertIsInstance(data, dict)


if __name__ == "__main__":
    unittest.main()
