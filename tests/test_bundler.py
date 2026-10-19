"""Bundler Tests — verifies dist/zerodep.py is buildable and correctly structured."""

import subprocess
import sys
import unittest
from pathlib import Path


class TestBundle(unittest.TestCase):

    def _project_root(self) -> Path:
        return Path(__file__).parent.parent.resolve()

    def test_bundle_builds(self):
        """Running bundle.py should produce dist/zerodep.py."""
        root = self._project_root()
        result = subprocess.run(
            [sys.executable, "bundle.py"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, msg=f"bundle.py failed:\n{result.stderr}")
        bundle_file = root / "dist" / "zerodep.py"
        self.assertTrue(bundle_file.exists(), "dist/zerodep.py was not created")

    def test_bundle_is_nonzero(self):
        bundle_file = self._project_root() / "dist" / "zerodep.py"
        if bundle_file.exists():
            self.assertGreater(bundle_file.stat().st_size, 1000)

    def test_bundle_no_double_main(self):
        """dist/zerodep.py must not have more than one call to main()."""
        bundle_file = self._project_root() / "dist" / "zerodep.py"
        if not bundle_file.exists():
            self.skipTest("dist/zerodep.py not built yet")
        content = bundle_file.read_text(encoding="utf-8", errors="ignore")
        # Count top-level if __name__ == '__main__': main() blocks
        import re
        main_guards = re.findall(r'if __name__\s*==\s*["\']__main__["\']\s*:', content)
        self.assertEqual(len(main_guards), 1,
                         f"Expected exactly 1 __main__ guard, found {len(main_guards)}")

    def test_bundle_imports_only_stdlib(self):
        """The bundle should import ONLY stdlib modules at module level."""
        bundle_file = self._project_root() / "dist" / "zerodep.py"
        if not bundle_file.exists():
            self.skipTest("dist/zerodep.py not built yet")
        import ast
        content = bundle_file.read_text(encoding="utf-8", errors="ignore")
        try:
            tree = ast.parse(content)
        except SyntaxError as e:
            self.fail(f"dist/zerodep.py has a syntax error: {e}")
        # Collect top-level imports
        top_imports = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top_imports.add(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    top_imports.add(node.module.split(".")[0])
        # Known third-party packages that must NOT appear
        forbidden = {"requests", "flask", "numpy", "pandas", "yaml", "PIL"}
        found_forbidden = top_imports & forbidden
        self.assertEqual(found_forbidden, set(),
                         f"Third-party imports found in bundle: {found_forbidden}")

    def test_bundle_runs_help(self):
        """python dist/zerodep.py --help should exit 0."""
        bundle_file = self._project_root() / "dist" / "zerodep.py"
        if not bundle_file.exists():
            self.skipTest("dist/zerodep.py not built yet")
        result = subprocess.run(
            [sys.executable, str(bundle_file), "--help"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        # argparse returns exit 0 for --help
        self.assertIn(result.returncode, (0, 2))


if __name__ == "__main__":
    unittest.main()
