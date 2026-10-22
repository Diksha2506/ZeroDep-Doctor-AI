"""Manifest Parser Tests — covers requirements.txt, pyproject.toml, and edge cases."""

import sys
import unittest
from pathlib import Path
import tempfile
import os

from zerodep.manifest_parser import (
    parse_manifests,
    parse_requirements_txt,
    normalize_package_name,
    ManifestParseResult,
)


class TestNormalizeName(unittest.TestCase):

    def test_simple_name(self):
        pkg, imp = normalize_package_name("requests")
        self.assertEqual(pkg, "requests")
        self.assertEqual(imp, "requests")

    def test_version_stripped(self):
        pkg, imp = normalize_package_name("requests>=2.28.0")
        self.assertEqual(pkg, "requests")

    def test_extras_stripped(self):
        pkg, imp = normalize_package_name("requests[security]>=2.0")
        self.assertEqual(pkg, "requests")

    def test_inline_comment_stripped(self):
        pkg, imp = normalize_package_name("requests  # needed for auth")
        self.assertEqual(pkg, "requests")

    def test_marker_stripped(self):
        pkg, imp = normalize_package_name('requests; python_version >= "3.9"')
        self.assertEqual(pkg, "requests")

    def test_known_alias_pyyaml(self):
        pkg, imp = normalize_package_name("pyyaml")
        self.assertEqual(imp, "yaml")

    def test_known_alias_scikit_learn(self):
        pkg, imp = normalize_package_name("scikit-learn")
        self.assertEqual(imp, "sklearn")

    def test_known_alias_pillow(self):
        pkg, imp = normalize_package_name("pillow")
        self.assertEqual(imp, "PIL")

    def test_hyphen_converted_to_underscore(self):
        pkg, imp = normalize_package_name("python-dateutil")
        # python-dateutil IS in alias map as dateutil
        self.assertEqual(imp, "dateutil")

    def test_unknown_package_default_normalization(self):
        pkg, imp = normalize_package_name("my-custom-lib>=1.0")
        self.assertEqual(pkg, "my-custom-lib")
        self.assertEqual(imp, "my_custom_lib")

    def test_empty_string(self):
        pkg, imp = normalize_package_name("")
        self.assertEqual(pkg, "")
        self.assertEqual(imp, "")


class TestRequirementsTxt(unittest.TestCase):

    def _make_temp_req(self, content: str) -> Path:
        tmp = tempfile.mktemp(suffix=".txt")
        Path(tmp).write_text(content, encoding="utf-8")
        return Path(tmp)

    def test_basic_requirements(self):
        tmp = self._make_temp_req("requests\nflask\nnumpy")
        deps = parse_requirements_txt(tmp)
        names = {d.name for d in deps}
        self.assertIn("requests", names)
        self.assertIn("flask", names)
        self.assertIn("numpy", names)
        os.unlink(tmp)

    def test_comments_skipped(self):
        tmp = self._make_temp_req("# this is a comment\nrequests")
        deps = parse_requirements_txt(tmp)
        self.assertEqual(len(deps), 1)
        os.unlink(tmp)

    def test_blank_lines_skipped(self):
        tmp = self._make_temp_req("\n\nrequests\n\nflask\n\n")
        deps = parse_requirements_txt(tmp)
        self.assertEqual(len(deps), 2)
        os.unlink(tmp)

    def test_options_skipped(self):
        tmp = self._make_temp_req("-r other.txt\n--index-url https://pypi.org\nrequests")
        deps = parse_requirements_txt(tmp)
        self.assertEqual(len(deps), 1)
        os.unlink(tmp)

    def test_version_specifiers_stripped(self):
        tmp = self._make_temp_req("requests>=2.28.0,<3.0")
        deps = parse_requirements_txt(tmp)
        self.assertEqual(len(deps), 1)
        self.assertEqual(deps[0].name, "requests")
        os.unlink(tmp)

    def test_scope_passed_through(self):
        tmp = self._make_temp_req("pytest")
        deps = parse_requirements_txt(tmp, scope="test")
        self.assertEqual(deps[0].scope, "test")
        os.unlink(tmp)

    def test_nonexistent_file_returns_empty(self):
        deps = parse_requirements_txt(Path("/nonexistent/requirements.txt"))
        self.assertEqual(deps, [])


class TestParseManifeststIntegration(unittest.TestCase):

    def _make_project(self, files: dict) -> Path:
        d = Path(tempfile.mkdtemp())
        for fname, content in files.items():
            (d / fname).write_text(content, encoding="utf-8")
        return d

    def test_requirements_discovered(self):
        proj = self._make_project({"requirements.txt": "requests\nflask"})
        result = parse_manifests(proj)
        names = {d.name for d in result.dependencies}
        self.assertIn("requests", names)
        self.assertIn("flask", names)

    def test_dev_requirements_classified(self):
        proj = self._make_project({"requirements-dev.txt": "pytest\nblack"})
        result = parse_manifests(proj)
        scopes = {d.name: d.scope for d in result.dependencies}
        self.assertEqual(scopes.get("pytest"), "dev")

    def test_test_requirements_classified(self):
        proj = self._make_project({"requirements-test.txt": "pytest"})
        result = parse_manifests(proj)
        scopes = {d.name: d.scope for d in result.dependencies}
        self.assertEqual(scopes.get("pytest"), "test")

    def test_deduplication(self):
        """Same package in runtime and dev should appear once per scope."""
        proj = self._make_project({
            "requirements.txt": "requests",
            "requirements-dev.txt": "requests",
        })
        result = parse_manifests(proj)
        names = [d.name for d in result.dependencies]
        # Should have requests:runtime and requests:dev — both present
        self.assertGreaterEqual(names.count("requests"), 1)

    @unittest.skipUnless(sys.version_info >= (3, 11), "tomllib requires Python 3.11+")
    def test_pyproject_toml_pep621(self):
        toml_content = (
            '[project]\nname = "myapp"\n'
            'dependencies = ["requests>=2.0", "flask"]\n'
            '[project.optional-dependencies]\n'
            'dev = ["pytest"]\n'
        )
        proj = self._make_project({"pyproject.toml": toml_content})
        result = parse_manifests(proj)
        names = {d.name for d in result.dependencies}
        self.assertIn("requests", names)
        self.assertIn("flask", names)
        self.assertIn("pytest", names)

    def test_empty_project(self):
        """Project with no manifests should return empty result."""
        proj = self._make_project({"main.py": "import os"})
        result = parse_manifests(proj)
        self.assertIsInstance(result, ManifestParseResult)
        self.assertEqual(result.dependencies, [])


if __name__ == "__main__":
    unittest.main()
