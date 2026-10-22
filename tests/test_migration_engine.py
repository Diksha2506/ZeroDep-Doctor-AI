import unittest
from zerodep.migration_engine import RECIPES, get_recipe

class TestMigrationEngine(unittest.TestCase):
    def test_all_10_recipes_present(self):
        expected_keys = {"requests", "click", "colorama", "tabulate", "networkx", "radon", "python-dotenv", "loguru", "tomli", "pytz"}
        self.assertEqual(set(RECIPES.keys()), expected_keys)

    def test_recipe_metadata(self):
        r = get_recipe("requests")
        self.assertIsNotNone(r)
        self.assertEqual(r.confidence, "HIGH")
        self.assertIn("urllib.request", r.stdlib_module)
        self.assertTrue(r.before_code.startswith("-"))
        self.assertTrue(r.after_code.startswith("+"))

    def test_recipe_case_insensitivity(self):
        r1 = get_recipe("PyTZ")
        r2 = get_recipe("pytz")
        self.assertEqual(r1, r2)

    def test_unknown_recipe(self):
        self.assertIsNone(get_recipe("unknown_pkg_xyz"))

if __name__ == "__main__":
    unittest.main()
