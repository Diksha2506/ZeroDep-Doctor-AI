"""AST Visitor — static import and call-site analysis using Python standard library.

Tracks:
  - import x / import x as y
  - from x import y / from x import y as z
  - relative imports (flagged, not resolved)
  - conditional imports (try/except ImportError)
  - star imports (flagged as low-confidence)
  - dynamic imports via importlib / __import__
  - call site attribution through alias tables
  - nested-scope (function/class body) imports

Zero third-party dependencies.
"""

import ast
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# ──────────────────────────────────────────────
# Stdlib module set
# ──────────────────────────────────────────────
if hasattr(sys, "stdlib_module_names"):
    STDLIB_MODULES: frozenset = sys.stdlib_module_names  # type: ignore[attr-defined]
else:
    STDLIB_MODULES = frozenset({
        "_thread", "abc", "aifc", "argparse", "array", "ast", "asynchat",
        "asyncio", "asyncore", "atexit", "audioop", "base64", "bdb",
        "binascii", "binhex", "bisect", "builtins", "bz2", "calendar",
        "cgi", "cgitb", "chunk", "cmath", "cmd", "code", "codecs",
        "codeop", "colorsys", "compileall", "concurrent", "configparser",
        "contextlib", "contextvars", "copy", "copyreg", "csv", "ctypes",
        "curses", "dataclasses", "datetime", "dbm", "decimal", "difflib",
        "dis", "doctest", "email", "encodings", "enum", "errno",
        "faulthandler", "fcntl", "filecmp", "fileinput", "fnmatch",
        "fractions", "ftplib", "functools", "gc", "getopt", "getpass",
        "gettext", "glob", "grp", "gzip", "hashlib", "heapq", "hmac",
        "html", "http", "idlelib", "imaplib", "importlib", "inspect",
        "io", "ipaddress", "itertools", "json", "keyword", "lib2to3",
        "linecache", "locale", "logging", "lzma", "mailbox", "math",
        "mimetypes", "mmap", "modulefinder", "multiprocessing", "netrc",
        "nis", "nntplib", "numbers", "operator", "optparse", "os",
        "pathlib", "pdb", "pickle", "pickletools", "pipes", "pkgutil",
        "platform", "plistlib", "poplib", "posix", "posixpath", "pprint",
        "profile", "pstats", "pty", "pwd", "py_compile", "pyclbr",
        "pydoc", "queue", "quopri", "random", "re", "readline",
        "reprlib", "resource", "rlcompleter", "runpy", "sched",
        "secrets", "select", "selectors", "shelve", "shlex", "shutil",
        "signal", "site", "smtpd", "smtplib", "sndhdr", "socket",
        "socketserver", "spwd", "sqlite3", "sre_compile", "sre_constants",
        "sre_parse", "ssl", "stat", "statistics", "string", "stringprep",
        "struct", "subprocess", "sunau", "symtable", "sys", "sysconfig",
        "syslog", "tabnanny", "tarfile", "telnetlib", "tempfile",
        "termios", "test", "textwrap", "threading", "time", "timeit",
        "tkinter", "token", "tokenize", "tomllib", "trace", "traceback",
        "tracemalloc", "tty", "turtle", "turtledemo", "types", "typing",
        "unicodedata", "unittest", "urllib", "uu", "uuid", "venv",
        "warnings", "wave", "weakref", "webbrowser", "wsgiref",
        "xdrlib", "xml", "xmlrpc", "zipapp", "zipfile", "zipimport",
        "zlib", "zoneinfo",
    })


# ──────────────────────────────────────────────
# Data classes
# ──────────────────────────────────────────────

@dataclass
class ImportBinding:
    original_module: str        # The resolved top-level package (e.g. "requests")
    symbol_name: str            # The specific name imported (e.g. "get")
    alias: str                  # The local name in scope (e.g. "req")
    line_no: int
    is_stdlib: bool
    is_relative: bool = False   # True for "from .utils import x"
    is_conditional: bool = False  # True if inside try/except or if statement
    is_star: bool = False       # True for "from pkg import *"
    scope_kind: str = "module"  # "module" | "function" | "class" | "conditional"

@dataclass
class CallSite:
    file_path: str
    line_no: int
    func_name: str              # As written in source (e.g. "req.get")
    resolved_symbol: str        # Fully resolved (e.g. "requests.get")
    original_module: str        # Top-level package (e.g. "requests")
    confidence: float = 1.0     # 1.0=High, 0.7=Medium (star/scope), 0.3=Low (dynamic)
    confidence_label: str = "HIGH" # "HIGH" | "MEDIUM" | "LOW" | "UNKNOWN"
    scope_kind: str = "module"

@dataclass
class DynamicImport:
    file_path: str
    line_no: int
    kind: str                   # "importlib", "__import__", or "other"
    target_module: Optional[str] = None  # Statically resolved target, if a string literal was passed

@dataclass
class ASTAnalysisResult:
    file_path: str
    imports: List[ImportBinding] = field(default_factory=list)
    call_sites: List[CallSite] = field(default_factory=list)
    dynamic_imports: List[DynamicImport] = field(default_factory=list)
    star_imports: List[str] = field(default_factory=list)  # modules with star imports
    has_syntax_error: bool = False
    error_message: str = ""


# ──────────────────────────────────────────────
# Visitor
# ──────────────────────────────────────────────

class SymbolTableVisitor(ast.NodeVisitor):
    """Single-pass visitor that builds a local symbol table per scope level."""

    def __init__(self, file_path: str):
        self.file_path = file_path
        self.imports: List[ImportBinding] = []
        self.call_sites: List[CallSite] = []
        self.dynamic_imports: List[DynamicImport] = []
        self.star_imports: List[str] = []

        # alias -> (original_module, full_symbol, confidence_modifier, scope_kind)
        self._symbols: Dict[str, Tuple[str, str, float, str]] = {}
        self._in_conditional_import: bool = False
        self._scope_stack: List[str] = ["module"]

    # ── Helpers ──────────────────────────────

    def _is_stdlib(self, module: str) -> bool:
        return module.split(".")[0] in STDLIB_MODULES

    def _current_scope(self) -> str:
        if self._in_conditional_import:
            return "conditional"
        return self._scope_stack[-1] if self._scope_stack else "module"

    def _register(self, alias: str, orig_module: str, symbol: str,
                  conf: float = 1.0, scope_kind: str = "module") -> None:
        """Register an alias -> (module, symbol, confidence, scope_kind) mapping."""
        self._symbols[alias] = (orig_module, symbol, conf, scope_kind)

    # ── Import nodes ─────────────────────────

    def visit_Import(self, node: ast.Import) -> None:
        scope = self._current_scope()
        is_cond = self._in_conditional_import or scope == "conditional"
        for alias in node.names:
            mod = alias.name
            bound_as = alias.asname or alias.name
            is_stdlib = self._is_stdlib(mod)
            binding = ImportBinding(
                original_module=mod,
                symbol_name=mod,
                alias=bound_as,
                line_no=node.lineno,
                is_stdlib=is_stdlib,
                is_conditional=is_cond,
                scope_kind=scope,
            )
            self.imports.append(binding)
            conf = 0.7 if (is_cond or scope in ("function", "class")) else 1.0
            self._register(bound_as, mod, mod, conf, scope)
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        scope = self._current_scope()
        is_cond = self._in_conditional_import or scope == "conditional"

        # Relative imports: module may be None ("from . import x")
        if node.level and node.level > 0:
            for alias in node.names:
                bound_as = alias.asname or alias.name
                binding = ImportBinding(
                    original_module=f"{'.' * node.level}{node.module or ''}",
                    symbol_name=alias.name,
                    alias=bound_as,
                    line_no=node.lineno,
                    is_stdlib=False,
                    is_relative=True,
                    is_conditional=is_cond,
                    scope_kind=scope,
                )
                self.imports.append(binding)
            self.generic_visit(node)
            return

        if not node.module:
            self.generic_visit(node)
            return

        mod = node.module
        is_stdlib = self._is_stdlib(mod)

        for alias in node.names:
            if alias.name == "*":
                self.star_imports.append(mod)
                binding = ImportBinding(
                    original_module=mod,
                    symbol_name="*",
                    alias="*",
                    line_no=node.lineno,
                    is_stdlib=is_stdlib,
                    is_star=True,
                    is_conditional=is_cond,
                    scope_kind=scope,
                )
                self.imports.append(binding)
                continue

            bound_as = alias.asname or alias.name
            full_symbol = f"{mod}.{alias.name}"
            binding = ImportBinding(
                original_module=mod,
                symbol_name=alias.name,
                alias=bound_as,
                line_no=node.lineno,
                is_stdlib=is_stdlib,
                is_conditional=is_cond,
                scope_kind=scope,
            )
            self.imports.append(binding)
            conf = 0.7 if (is_cond or scope in ("function", "class")) else 1.0
            self._register(bound_as, mod, full_symbol, conf, scope)

        self.generic_visit(node)

    # ── Try/except & If for conditional imports ───

    def visit_Try(self, node: ast.Try) -> None:
        """Detect try/except ImportError patterns."""
        is_import_guard = any(
            isinstance(h.type, ast.Name)
            and h.type.id in ("ImportError", "ModuleNotFoundError")
            for h in node.handlers
            if h.type is not None
        )
        prev = self._in_conditional_import
        if is_import_guard:
            self._in_conditional_import = True
        self.generic_visit(node)
        self._in_conditional_import = prev

    def visit_If(self, node: ast.If) -> None:
        """Flag imports inside if-statements as conditional."""
        prev = self._in_conditional_import
        self._in_conditional_import = True
        self.generic_visit(node)
        self._in_conditional_import = prev

    # ── Function / class bodies (nested scope) ───

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._scope_stack.append("function")
        self.generic_visit(node)
        self._scope_stack.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._scope_stack.append("class")
        self.generic_visit(node)
        self._scope_stack.pop()

    # ── Call nodes ───────────────────────────

    def _resolve_name(self, name: str) -> Optional[Tuple[str, str, float, str]]:
        """Look up alias -> (module, symbol, confidence, scope_kind)."""
        return self._symbols.get(name)

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func

        # ── Simple name call: get(...) ─────────────────────
        if isinstance(func, ast.Name):
            name = func.id
            # Dynamic import detection
            if name == "__import__":
                # Try to extract static string literal target
                target = None
                if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                    target = node.args[0].value.split(".")[0]
                self.dynamic_imports.append(DynamicImport(
                    file_path=self.file_path,
                    line_no=node.lineno,
                    kind="__import__",
                    target_module=target,
                ))
            resolved = self._resolve_name(name)
            if resolved:
                orig_mod, sym, conf, scope_kind = resolved
                if not self._is_stdlib(orig_mod):
                    conf_label = "HIGH" if conf >= 1.0 else ("MEDIUM" if conf >= 0.7 else "LOW")
                    self.call_sites.append(CallSite(
                        file_path=self.file_path,
                        line_no=node.lineno,
                        func_name=name,
                        resolved_symbol=sym,
                        original_module=orig_mod,
                        confidence=conf,
                        confidence_label=conf_label,
                        scope_kind=scope_kind,
                    ))

        # ── Attribute call: req.get(...) ───────────────────
        elif isinstance(func, ast.Attribute):
            chain, base = self._flatten_attr(func)
            if chain and base:
                func_name = ".".join(chain)
                resolved = self._resolve_name(base)
                if resolved:
                    orig_mod, _, conf, scope_kind = resolved
                    attr_tail = ".".join(chain[1:])
                    sym = f"{orig_mod}.{attr_tail}" if attr_tail else orig_mod
                    if func_name in ("importlib.import_module", "importlib.util.find_spec"):
                        # Try to extract static string literal target
                        target = None
                        if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                            target = node.args[0].value.split(".")[0]
                        self.dynamic_imports.append(DynamicImport(
                            file_path=self.file_path,
                            line_no=node.lineno,
                            kind="importlib",
                            target_module=target,
                        ))
                    elif not self._is_stdlib(orig_mod):
                        conf_label = "HIGH" if conf >= 1.0 else ("MEDIUM" if conf >= 0.7 else "LOW")
                        self.call_sites.append(CallSite(
                            file_path=self.file_path,
                            line_no=node.lineno,
                            func_name=func_name,
                            resolved_symbol=sym,
                            original_module=orig_mod,
                            confidence=conf,
                            confidence_label=conf_label,
                            scope_kind=scope_kind,
                        ))

        self.generic_visit(node)

    @staticmethod
    def _flatten_attr(node: ast.Attribute) -> Tuple[List[str], str]:
        """Walk a.b.c(...) -> (["a", "b", "c"], "a")."""
        chain: List[str] = []
        curr = node
        while isinstance(curr, ast.Attribute):
            chain.append(curr.attr)
            curr = curr.value
        if isinstance(curr, ast.Name):
            chain.append(curr.id)
            chain.reverse()
            return chain, chain[0]
        return [], ""


# ──────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────

MAX_FILE_SIZE_BYTES = 5 * 1024 * 1024  # 5 MB guard

def analyze_ast(file_path: str, code_content: str) -> ASTAnalysisResult:
    """Parse and analyze a single Python source file. Never executes code."""
    result = ASTAnalysisResult(file_path=file_path)

    if len(code_content.encode("utf-8", errors="ignore")) > MAX_FILE_SIZE_BYTES:
        result.has_syntax_error = True
        result.error_message = "File exceeds 5 MB size limit — skipped."
        return result

    try:
        tree = ast.parse(code_content, filename=file_path)
    except SyntaxError as exc:
        result.has_syntax_error = True
        result.error_message = f"SyntaxError line {exc.lineno}: {exc.msg}"
        return result
    except Exception as exc:  # noqa: BLE001
        result.has_syntax_error = True
        result.error_message = str(exc)
        return result

    visitor = SymbolTableVisitor(file_path=file_path)
    visitor.visit(tree)

    result.imports = visitor.imports
    result.call_sites = visitor.call_sites
    result.dynamic_imports = visitor.dynamic_imports
    result.star_imports = visitor.star_imports
    return result
