"""Migration Engine Edge Case Tests — aliases, from-imports, conditions, complex APIs."""

import unittest
from zerodep.ast_visitor import analyze_ast
from zerodep.migration_engine import get_recipe


class TestMigrationAliasedImport(unittest.TestCase):

    def test_aliased_requests_call_site_resolved(self):
        code = "import requests as req\nreq.get('http://x.com')"
        res = analyze_ast("test.py", code)
        self.assertEqual(len(res.call_sites), 1)
        self.assertEqual(res.call_sites[0].original_module, "requests")
        self.assertEqual(res.call_sites[0].resolved_symbol, "requests.get")

    def test_from_import_get_resolved(self):
        code = "from requests import get\nget('http://x.com')"
        res = analyze_ast("test.py", code)
        self.assertEqual(len(res.call_sites), 1)
        self.assertEqual(res.call_sites[0].original_module, "requests")
        self.assertEqual(res.call_sites[0].resolved_symbol, "requests.get")

    def test_from_import_aliased_resolved(self):
        code = "from requests import get as fetch\nfetch('http://x.com')"
        res = analyze_ast("test.py", code)
        self.assertEqual(len(res.call_sites), 1)
        self.assertEqual(res.call_sites[0].original_module, "requests")

    def test_conditional_import_flagged(self):
        code = "try:\n    import requests\nexcept ImportError:\n    requests = None"
        res = analyze_ast("test.py", code)
        imp = res.imports[0]
        self.assertTrue(imp.is_conditional)
        self.assertEqual(imp.scope_kind, "conditional")

    def test_dynamic_import_flagged(self):
        code = "import importlib\nmod = importlib.import_module('requests')"
        res = analyze_ast("test.py", code)
        self.assertGreaterEqual(len(res.dynamic_imports), 1)
        self.assertEqual(res.dynamic_imports[0].kind, "importlib")

    def test_star_import_detected(self):
        code = "from requests import *"
        res = analyze_ast("test.py", code)
        self.assertIn("requests", res.star_imports)

    def test_simple_requests_get_high_confidence(self):
        recipe = get_recipe("requests", {"requests.get": 3})
        self.assertIsNotNone(recipe)
        self.assertEqual(recipe.confidence, "HIGH")

    def test_complex_session_downgrades_confidence(self):
        recipe = get_recipe("requests", {"requests.Session": 1})
        self.assertIsNotNone(recipe)
        self.assertEqual(recipe.confidence, "LOW")
        self.assertIn("Session", recipe.why_replaceable)

    def test_requests_auth_downgrades_confidence(self):
        recipe = get_recipe("requests", {"requests.auth": 1})
        self.assertIsNotNone(recipe)
        self.assertEqual(recipe.confidence, "LOW")

    def test_click_recipe_high_confidence(self):
        recipe = get_recipe("click")
        self.assertIsNotNone(recipe)
        self.assertEqual(recipe.confidence, "HIGH")
        self.assertIn("argparse", recipe.stdlib_module)

    def test_pytz_recipe_high_confidence(self):
        recipe = get_recipe("pytz")
        self.assertIsNotNone(recipe)
        self.assertEqual(recipe.confidence, "HIGH")
        self.assertIn("zoneinfo", recipe.stdlib_module)

    def test_unknown_package_no_recipe(self):
        recipe = get_recipe("numpy")
        self.assertIsNone(recipe)

    def test_limitations_not_empty_in_recipes(self):
        from zerodep.migration_engine import RECIPES
        for name, recipe in RECIPES.items():
            self.assertGreater(len(recipe.limitations), 5, f"Empty limitations for {name}")

    def test_no_misleading_auto_apply_claim(self):
        """Migration diffs must be labeled as suggestions, not auto-applied."""
        from zerodep.migration_engine import RECIPES
        for name, recipe in RECIPES.items():
            # Ensure we don't claim automated refactoring
            self.assertNotIn("automatically applied", recipe.why_replaceable.lower())


class TestMigrationDiffFormat(unittest.TestCase):

    def test_before_code_has_minus_prefix(self):
        recipe = get_recipe("requests")
        self.assertIn("-", recipe.before_code)

    def test_after_code_has_plus_prefix(self):
        recipe = get_recipe("requests")
        self.assertIn("+", recipe.after_code)

    def test_before_after_not_empty(self):
        from zerodep.migration_engine import RECIPES
        for name, recipe in RECIPES.items():
            self.assertGreater(len(recipe.before_code), 0, f"Empty before_code for {name}")
            self.assertGreater(len(recipe.after_code), 0, f"Empty after_code for {name}")


if __name__ == "__main__":
    unittest.main()
