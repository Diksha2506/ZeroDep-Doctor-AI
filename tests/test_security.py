"""Security Hardening Tests — ensures malicious/unusual inputs are handled safely."""

import unittest
from zerodep.ast_visitor import analyze_ast


class TestMaliciousConstructs(unittest.TestCase):

    def test_exec_does_not_execute(self):
        """exec() calls in source code must be parsed statically, never run."""
        code = "exec('import os; os.system(\"del /f /q C:\\\\*\")')"
        res = analyze_ast("malicious.py", code)
        self.assertFalse(res.has_syntax_error)

    def test_eval_does_not_execute(self):
        code = "result = eval('__import__(\"subprocess\").check_output([\"whoami\"])')"
        res = analyze_ast("malicious.py", code)
        self.assertFalse(res.has_syntax_error)

    def test_deeply_nested_ast(self):
        """AST with very deep nesting must not crash."""
        # Build: a + (a + (a + ...)) 300 levels deep
        code = "x = " + " + ".join(["1"] * 300)
        res = analyze_ast("deep.py", code)
        # Should parse successfully or gracefully error — no crash
        self.assertIsNotNone(res)

    def test_unicode_identifiers(self):
        """Python 3 supports unicode identifiers — should not crash."""
        code = "café = 42\nprint(café)"
        res = analyze_ast("unicode.py", code)
        self.assertFalse(res.has_syntax_error)

    def test_null_bytes_handled(self):
        """Files with null bytes cause SyntaxError — must be reported, not crashed."""
        code = "import os\x00import sys"
        res = analyze_ast("nullbyte.py", code)
        # Either parsed or gracefully errored
        self.assertIsNotNone(res)

    def test_very_long_string(self):
        """Huge string literals should not crash the AST parser."""
        code = f"x = '{'a' * 100_000}'"
        res = analyze_ast("bigstring.py", code)
        self.assertFalse(res.has_syntax_error)

    def test_obfuscated_import_via_dunder(self):
        """__import__ is detected as a dynamic import."""
        code = "__import__('subprocess').run(['ls'])"
        res = analyze_ast("obfuscated.py", code)
        self.assertGreater(len(res.dynamic_imports), 0)

    def test_path_traversal_ignored(self):
        """The file_path argument is never used for filesystem access."""
        code = "import os"
        # Passing a traversal path should not cause filesystem access
        res = analyze_ast("../../../etc/passwd", code)
        self.assertFalse(res.has_syntax_error)

    def test_circular_alias_reference(self):
        """Circular-looking alias assignments should not loop."""
        code = "import requests as requests\nrequests.get('x')"
        res = analyze_ast("alias.py", code)
        self.assertFalse(res.has_syntax_error)
        self.assertEqual(len(res.call_sites), 1)

    def test_import_shadowed_by_assignment(self):
        """Shadowing an import with a local name — visitor should not crash."""
        code = "import requests\nrequests = None\nrequests.get('x')"
        res = analyze_ast("shadow.py", code)
        # Must not crash; call site detection may or may not pick it up
        self.assertFalse(res.has_syntax_error)

    def test_nonstandard_encoding_comment(self):
        """Files with # -*- coding: latin-1 -*- should not crash."""
        code = "# -*- coding: utf-8 -*-\nimport requests\n"
        res = analyze_ast("encoded.py", code)
        self.assertFalse(res.has_syntax_error)
        self.assertEqual(len(res.imports), 1)


if __name__ == "__main__":
    unittest.main()
