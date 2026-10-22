"""AST Visitor Tests — covers all import styles and call-site resolution."""

import unittest
from zerodep.ast_visitor import analyze_ast, ASTAnalysisResult


class TestBasicImports(unittest.TestCase):

    def test_standard_import(self):
        code = "import requests\nrequests.get('https://example.com')"
        res = analyze_ast("test.py", code)
        self.assertFalse(res.has_syntax_error)
        self.assertEqual(len(res.imports), 1)
        self.assertEqual(res.imports[0].original_module, "requests")
        self.assertEqual(len(res.call_sites), 1)
        self.assertEqual(res.call_sites[0].resolved_symbol, "requests.get")

    def test_aliased_import(self):
        code = "import requests as req\nreq.get('https://example.com')"
        res = analyze_ast("test.py", code)
        self.assertEqual(res.imports[0].alias, "req")
        self.assertEqual(len(res.call_sites), 1)
        self.assertEqual(res.call_sites[0].resolved_symbol, "requests.get")

    def test_from_import_symbol_call(self):
        """from x import y → calling y() should resolve to x.y"""
        code = "from requests import get\nget('https://example.com')"
        res = analyze_ast("test.py", code)
        self.assertFalse(res.has_syntax_error)
        self.assertEqual(len(res.imports), 1)
        self.assertEqual(res.imports[0].original_module, "requests")
        self.assertEqual(res.imports[0].symbol_name, "get")
        # call site should resolve
        self.assertEqual(len(res.call_sites), 1)
        self.assertEqual(res.call_sites[0].original_module, "requests")
        self.assertEqual(res.call_sites[0].resolved_symbol, "requests.get")

    def test_from_import_alias_call(self):
        """from requests import get as http_get → http_get() resolves"""
        code = "from requests import get as http_get\nhttp_get('https://example.com')"
        res = analyze_ast("test.py", code)
        self.assertEqual(len(res.call_sites), 1)
        self.assertEqual(res.call_sites[0].original_module, "requests")
        self.assertEqual(res.call_sites[0].resolved_symbol, "requests.get")

    def test_stdlib_not_in_call_sites(self):
        """stdlib imports must NOT appear in call_sites"""
        code = "import os\nos.path.join('/tmp', 'x')"
        res = analyze_ast("test.py", code)
        self.assertEqual(len(res.call_sites), 0)

    def test_from_stdlib_not_in_call_sites(self):
        code = "from pathlib import Path\nPath('.')"
        res = analyze_ast("test.py", code)
        self.assertEqual(len(res.call_sites), 0)


class TestRelativeImports(unittest.TestCase):

    def test_relative_import_flagged(self):
        code = "from .utils import helper"
        res = analyze_ast("package/module.py", code)
        self.assertFalse(res.has_syntax_error)
        self.assertTrue(res.imports[0].is_relative)
        self.assertFalse(res.imports[0].is_stdlib)

    def test_relative_import_from_parent(self):
        code = "from ..core import Config"
        res = analyze_ast("package/sub/module.py", code)
        self.assertTrue(res.imports[0].is_relative)


class TestStarImports(unittest.TestCase):

    def test_star_import_detected(self):
        code = "from requests import *"
        res = analyze_ast("test.py", code)
        self.assertIn("requests", res.star_imports)
        self.assertTrue(res.imports[0].is_star)

    def test_star_import_stdlib_not_flagged_as_star_dep(self):
        """Star import of stdlib still appears in star_imports but not call_sites."""
        code = "from os.path import *"
        res = analyze_ast("test.py", code)
        self.assertIn("os.path", res.star_imports)
        self.assertEqual(len(res.call_sites), 0)


class TestConditionalImports(unittest.TestCase):

    def test_try_except_import_flagged(self):
        code = (
            "try:\n"
            "    import ujson as json\n"
            "except ImportError:\n"
            "    import json\n"
        )
        res = analyze_ast("test.py", code)
        self.assertFalse(res.has_syntax_error)
        # ujson import should be flagged as conditional
        ujson_bindings = [i for i in res.imports if i.original_module == "ujson"]
        self.assertEqual(len(ujson_bindings), 1)
        self.assertTrue(ujson_bindings[0].is_conditional)

    def test_regular_import_not_flagged_as_conditional(self):
        code = "import requests"
        res = analyze_ast("test.py", code)
        self.assertFalse(res.imports[0].is_conditional)


class TestDynamicImports(unittest.TestCase):

    def test_importlib_import_module(self):
        code = "import importlib\nmod = importlib.import_module('json')"
        res = analyze_ast("test.py", code)
        self.assertGreater(len(res.dynamic_imports), 0)
        self.assertTrue(any(d.kind == "importlib" for d in res.dynamic_imports))

    def test_dunder_import(self):
        code = "mod = __import__('os')"
        res = analyze_ast("test.py", code)
        self.assertGreater(len(res.dynamic_imports), 0)
        self.assertTrue(any(d.kind == "__import__" for d in res.dynamic_imports))


class TestEdgeCases(unittest.TestCase):

    def test_syntax_error_handled(self):
        code = "def broken_func("
        res = analyze_ast("test.py", code)
        self.assertTrue(res.has_syntax_error)
        self.assertIn("SyntaxError", res.error_message)

    def test_empty_file(self):
        res = analyze_ast("empty.py", "")
        self.assertFalse(res.has_syntax_error)
        self.assertEqual(len(res.imports), 0)

    def test_multiline_import(self):
        code = "from os.path import (\n    join,\n    exists,\n    dirname,\n)"
        res = analyze_ast("test.py", code)
        self.assertFalse(res.has_syntax_error)
        self.assertEqual(len(res.imports), 3)
        self.assertTrue(all(i.is_stdlib for i in res.imports))

    def test_deep_attribute_call(self):
        """pkg.sub.func() should resolve to pkg"""
        code = "import numpy as np\nnp.random.normal(0, 1)"
        res = analyze_ast("test.py", code)
        self.assertEqual(len(res.call_sites), 1)
        self.assertEqual(res.call_sites[0].original_module, "numpy")

    def test_multiple_from_imports(self):
        code = "from flask import Flask, request, jsonify\napp = Flask(__name__)"
        res = analyze_ast("test.py", code)
        # 3 symbols imported from flask
        self.assertEqual(len(res.imports), 3)
        self.assertTrue(all(i.original_module == "flask" for i in res.imports))
        # Flask() is a call site
        self.assertEqual(len(res.call_sites), 1)
        self.assertEqual(res.call_sites[0].original_module, "flask")

    def test_no_false_positives_for_stdlib(self):
        """No third-party call sites should appear for pure stdlib code."""
        code = (
            "import json, sys, re\n"
            "from pathlib import Path\n"
            "from collections import defaultdict\n"
            "json.dumps({})\n"
            "sys.exit(0)\n"
            "re.compile(r'.')\n"
            "Path('.')\n"
            "defaultdict(list)\n"
        )
        res = analyze_ast("test.py", code)
        self.assertEqual(len(res.call_sites), 0)

    def test_nested_function_import(self):
        """Imports inside functions should still be tracked."""
        code = (
            "def fetch():\n"
            "    import requests\n"
            "    return requests.get('http://example.com')\n"
        )
        res = analyze_ast("test.py", code)
        self.assertEqual(len(res.imports), 1)
        self.assertEqual(res.imports[0].original_module, "requests")


if __name__ == "__main__":
    unittest.main()
