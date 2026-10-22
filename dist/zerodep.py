#!/usr/bin/env python3
"""ZeroDep Doctor — Single-File Executable Distribution (Pure Stdlib)."""
# Zero third-party runtime dependencies. Python 3.11+ required.

import sys, os, ast, re, argparse, json, time, shutil, pathlib, heapq
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Dict, List, Set, Tuple, Optional

if sys.version_info < (3, 11):
    print('ZeroDep Doctor requires Python 3.11+', file=sys.stderr)
    sys.exit(1)

# === EMBEDDED MODULES ===

# --- manifest_parser.py ---

from pathlib import Path
from typing import Dict, List, Optional, Tuple

if sys.version_info >= (3, 11):
    import tomllib  # type: ignore[import]
else:
    tomllib = None  # type: ignore[assignment]


# ──────────────────────────────────────────────────────────────────────────
# Known PyPI name → Python import symbol mappings.
# Entries here override the default normalization (lower + replace("-","_")).
# Add entries only when they are well-established; document uncertain ones.
# ──────────────────────────────────────────────────────────────────────────
PACKAGE_ALIAS_MAP: Dict[str, str] = {
    # Confirmed stable mappings
    "scikit-learn":         "sklearn",
    "pyyaml":               "yaml",
    "yaml":                 "yaml",
    "pillow":               "PIL",
    "python-dotenv":        "dotenv",
    "dotenv":               "dotenv",
    "beautifulsoup4":       "bs4",
    "opencv-python":        "cv2",
    "opencv-python-headless": "cv2",
    "typing-extensions":    "typing_extensions",
    "protobuf":             "google.protobuf",
    "discord.py":           "discord",
    "pypdf2":               "PyPDF2",
    "python-dateutil":      "dateutil",
    "python-jose":          "jose",
    "mysqlclient":          "MySQLdb",
    "psycopg2-binary":      "psycopg2",
    "psycopg2":             "psycopg2",
    "pyarrow":              "pyarrow",
    "google-cloud-storage": "google.cloud.storage",
    "google-auth":          "google.auth",
    "sqlalchemy":           "sqlalchemy",
    "pydantic":             "pydantic",
    "attrs":                "attr",
    "exceptiongroup":       "exceptiongroup",
    "py":                   "py",
    "setuptools":           "setuptools",
    "wheel":                "wheel",
    "tomli":                "tomli",
    "tomli-w":              "tomli_w",
    "flask-cors":           "flask_cors",
    "python-multipart":     "multipart",
    "torchvision":          "torchvision",
    "ultralytics":          "ultralytics",
    "poliastro":            "poliastro",
    "sgp4":                 "sgp4",
    "astropy":              "astropy",
}

# Dependency group names that indicate dev/test scope
_DEV_GROUPS = frozenset({"dev", "devel", "development", "test", "tests", "testing", "lint", "qa", "ci", "docs"})

# Regex to split a requirement line into (package_spec, marker)
_MARKER_SPLIT_RE = re.compile(r";")
# Regex to strip version specifiers and extras from package name
_NAME_STRIP_RE = re.compile(r"[=<>!~\[\s;]")


@dataclass
class ManifestDependency:
    name: str               # Normalized PyPI name (lowercase, dashes preserved)
    import_name: str        # Best-guess Python import symbol
    source_file: str        # Which manifest file this came from
    raw_spec: str           # Original line e.g. "requests[security]>=2.0"
    scope: str = "runtime"  # "runtime" | "optional" | "dev" | "test"


@dataclass
class ManifestParseResult:
    dependencies: List[ManifestDependency] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    manifest_files_found: List[str] = field(default_factory=list)



# ──────────────────────────────────────────────────────────────────────────
# Normalization helpers
# ──────────────────────────────────────────────────────────────────────────

def _strip_inline_comment(line: str) -> str:
    """Remove trailing # comments from a requirement line."""
    # Avoid stripping URLs like http://... which don't have spaces before #
    idx = line.find(" #")
    if idx == -1:
        idx = line.find("\t#")
    return line[:idx].strip() if idx != -1 else line.strip()


def normalize_package_name(raw: str) -> Tuple[str, str]:
    """
    Returns (canonical_pkg_name, import_symbol).

    canonical_pkg_name: PyPI name as given, stripped of version/extras/markers.
    import_symbol: best-guess Python import name.
    """
    # Strip inline comment
    raw = _strip_inline_comment(raw)
    # Strip marker (everything after first ';')
    spec = _MARKER_SPLIT_RE.split(raw, 1)[0].strip()
    # Strip version and extras
    name_part = _NAME_STRIP_RE.split(spec, 1)[0].strip()
    if not name_part:
        return "", ""

    # Lookup in alias map (case-insensitive key)
    lookup_key = name_part.lower()
    if lookup_key in PACKAGE_ALIAS_MAP:
        import_name = PACKAGE_ALIAS_MAP[lookup_key]
    else:
        # Default: lowercase, replace hyphens with underscores
        import_name = lookup_key.replace("-", "_")

    return name_part, import_name


def _scope_from_group(group_name: str) -> str:
    g = group_name.lower()
    if any(tok in g for tok in _DEV_GROUPS):
        return "dev" if "dev" in g or "lint" in g or "ci" in g else "test"
    return "optional"


# ──────────────────────────────────────────────────────────────────────────
# Parsers
# ──────────────────────────────────────────────────────────────────────────

def parse_requirements_txt(
    file_path: Path,
    scope: str = "runtime",
) -> List[ManifestDependency]:
    """Parse a requirements*.txt file."""
    deps: List[ManifestDependency] = []
    if not file_path.exists():
        return deps

    try:
        text = file_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return deps

    for raw_line in text.splitlines():
        line = raw_line.strip()
        # Skip blank lines, comments, options (-r, --index-url, etc.)
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        pkg_name, import_name = normalize_package_name(line)
        if not pkg_name:
            continue
        deps.append(ManifestDependency(
            name=pkg_name.lower(),
            import_name=import_name,
            source_file=file_path.name,
            raw_spec=line,
            scope=scope,
        ))
    return deps


def parse_pyproject_toml(file_path: Path) -> List[ManifestDependency]:
    """Parse pyproject.toml — supports PEP 621, Poetry, and Flit layouts."""
    if not file_path.exists() or tomllib is None:
        return []

    try:
        with open(file_path, "rb") as fh:
            data = tomllib.load(fh)
    except Exception:  # noqa: BLE001
        return []

    deps: List[ManifestDependency] = []

    # ── PEP 621 [project.dependencies] ──────────────────────
    project = data.get("project", {})
    for raw in project.get("dependencies", []):
        pkg, imp = normalize_package_name(str(raw))
        if pkg:
            deps.append(ManifestDependency(
                name=pkg.lower(), import_name=imp,
                source_file=file_path.name, raw_spec=raw, scope="runtime",
            ))

    # ── PEP 621 [project.optional-dependencies] ─────────────
    for group, group_deps in project.get("optional-dependencies", {}).items():
        scope = _scope_from_group(group)
        for raw in group_deps:
            pkg, imp = normalize_package_name(str(raw))
            if pkg:
                deps.append(ManifestDependency(
                    name=pkg.lower(), import_name=imp,
                    source_file=file_path.name, raw_spec=raw, scope=scope,
                ))

    # ── Poetry [tool.poetry.dependencies] ───────────────────
    poetry = data.get("tool", {}).get("poetry", {})
    for raw_name, _ver in poetry.get("dependencies", {}).items():
        if raw_name.lower() == "python":
            continue
        pkg, imp = normalize_package_name(raw_name)
        if pkg:
            deps.append(ManifestDependency(
                name=pkg.lower(), import_name=imp,
                source_file=file_path.name, raw_spec=raw_name, scope="runtime",
            ))
    for group_name, group_data in poetry.get("group", {}).items():
        scope = _scope_from_group(group_name)
        for raw_name in group_data.get("dependencies", {}).keys():
            pkg, imp = normalize_package_name(raw_name)
            if pkg:
                deps.append(ManifestDependency(
                    name=pkg.lower(), import_name=imp,
                    source_file=file_path.name, raw_spec=raw_name, scope=scope,
                ))

    return deps


# ──────────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────────

_DEV_REQ_PATTERNS = ("requirements-dev.txt", "requirements-test.txt",
                     "requirements_dev.txt", "requirements_test.txt",
                     "dev-requirements.txt", "test-requirements.txt")


def parse_manifests(root_path: Path) -> ManifestParseResult:
    """Discover and parse all manifest files in root_path and immediate project subfolders."""
    result = ManifestParseResult()
    seen: Dict[str, ManifestDependency] = {}  # dedup by (name, scope)
    manifest_files_found: List[str] = []

    def _add(dep: ManifestDependency, rel_path: str) -> None:
        key = f"{dep.name}::{dep.scope}"
        if key not in seen:
            seen[key] = dep
        if rel_path not in manifest_files_found:
            manifest_files_found.append(rel_path)

    # Search candidates in root and subdirectories (excluding virtual environments, git, caches)
    skip_dirs = {".git", ".tox", ".pytest_cache", "venv", ".venv", "env", "node_modules", "site-packages", "__pycache__"}

    def _should_skip_dir(p: Path) -> bool:
        return any(part in skip_dirs for part in p.parts)

    _SUPPORTED_MANIFESTS = ("pyproject.toml", "requirements.txt", "setup.py", "setup.cfg", "Pipfile") + _DEV_REQ_PATTERNS

    candidate_files = []
    # 1. Direct root candidates
    for name in _SUPPORTED_MANIFESTS:
        target = root_path / name
        if target.is_file():
            candidate_files.append(target)

    # 2. Subdirectory candidates (e.g. backend/requirements.txt, src/requirements.txt)
    for p in root_path.glob("*/*"):
        if p.is_file() and not _should_skip_dir(p.parent):
            if p.name in _SUPPORTED_MANIFESTS:
                if p not in candidate_files:
                    candidate_files.append(p)

    # 3. Parse discovered manifests
    for mf in candidate_files:
        rel = str(mf.relative_to(root_path))
        if mf.name == "pyproject.toml":
            parsed = parse_pyproject_toml(mf)
            if parsed:
                for dep in parsed:
                    _add(dep, rel)
            else:
                if rel not in manifest_files_found:
                    manifest_files_found.append(rel)
        elif mf.name.endswith(".txt"):
            scope = "test" if "test" in mf.name else ("dev" if "dev" in mf.name else "runtime")
            parsed = parse_requirements_txt(mf, scope=scope)
            if parsed:
                for dep in parsed:
                    _add(dep, rel)
            else:
                if rel not in manifest_files_found:
                    manifest_files_found.append(rel)
        elif mf.name == "setup.py":
            # Simple static extraction of install_requires
            if rel not in manifest_files_found:
                manifest_files_found.append(rel)
            try:
                content = mf.read_text(encoding="utf-8", errors="ignore")
                match = re.search(r"install_requires\s*=\s*\[(.*?)\]", content, re.DOTALL)
                if match:
                    items = re.findall(r"['\"]([^'\"]+)['\"]", match.group(1))
                    for item in items:
                        pkg, imp = normalize_package_name(item)
                        if pkg:
                            dep = ManifestDependency(
                                name=pkg.lower(),
                                import_name=imp,
                                source_file=mf.name,
                                raw_spec=item,
                                scope="runtime",
                            )
                            _add(dep, rel)
            except Exception:
                pass
        elif mf.name == "setup.cfg":
            if rel not in manifest_files_found:
                manifest_files_found.append(rel)
            try:
                import configparser
                cfg = configparser.ConfigParser()
                cfg.read(str(mf), encoding="utf-8")
                raw_install = cfg.get("options", "install_requires", fallback="")
                for line in raw_install.splitlines():
                    raw = line.strip()
                    if raw and not raw.startswith("#"):
                        pkg, imp = normalize_package_name(raw)
                        if pkg:
                            dep = ManifestDependency(
                                name=pkg.lower(),
                                import_name=imp,
                                source_file=mf.name,
                                raw_spec=raw,
                                scope="runtime",
                            )
                            _add(dep, rel)
            except Exception:
                pass
        elif mf.name == "Pipfile":
            if rel not in manifest_files_found:
                manifest_files_found.append(rel)
            try:
                content = mf.read_text(encoding="utf-8", errors="ignore")
                in_packages = False
                in_dev_packages = False
                for line in content.splitlines():
                    line_clean = line.strip()
                    if line_clean.startswith("[packages]"):
                        in_packages = True
                        in_dev_packages = False
                        continue
                    elif line_clean.startswith("[dev-packages]"):
                        in_packages = False
                        in_dev_packages = True
                        continue
                    elif line_clean.startswith("["):
                        in_packages = False
                        in_dev_packages = False
                        continue
                    if (in_packages or in_dev_packages) and "=" in line_clean and not line_clean.startswith("#"):
                        pkg_raw = line_clean.split("=")[0].strip().strip('"').strip("'")
                        if pkg_raw:
                            pkg, imp = normalize_package_name(pkg_raw)
                            if pkg:
                                dep = ManifestDependency(
                                    name=pkg.lower(),
                                    import_name=imp,
                                    source_file=mf.name,
                                    raw_spec=line_clean,
                                    scope="dev" if in_dev_packages else "runtime",
                                )
                                _add(dep, rel)
            except Exception:
                pass

    result.dependencies = list(seen.values())
    result.manifest_files_found = manifest_files_found
    return result


# --- ast_visitor.py ---

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

# --- graph_engine.py ---

from typing import Dict, List, Optional, Set, Tuple


class ZeroDepGraph:

    def __init__(self) -> None:
        self._adj: Dict[str, Dict[str, dict]] = defaultdict(dict)
        self._in_degree: Dict[str, int] = defaultdict(int)

    # ──────────────────────────────────────────
    # Mutators
    # ──────────────────────────────────────────

    def add_node(self, node: str, **attrs) -> None:
        if node not in self._adj:
            self._adj[node] = {}
        if node not in self._in_degree:
            self._in_degree[node] = 0

    def add_edge(self, u: str, v: str, weight: float = 1.0) -> None:
        self.add_node(u)
        self.add_node(v)
        if v not in self._adj[u]:
            self._adj[u][v] = {"weight": weight}
            self._in_degree[v] += 1

    # ──────────────────────────────────────────
    # Accessors
    # ──────────────────────────────────────────

    def nodes(self) -> List[str]:
        return list(self._adj.keys())

    def neighbors(self, node: str) -> List[str]:
        return list(self._adj.get(node, {}).keys())

    def in_degree(self, node: str) -> int:
        return self._in_degree.get(node, 0)

    def out_degree(self, node: str) -> int:
        return len(self._adj.get(node, {}))

    def has_node(self, node: str) -> bool:
        return node in self._adj

    def has_edge(self, u: str, v: str) -> bool:
        return v in self._adj.get(u, {})

    def edge_count(self) -> int:
        return sum(len(v) for v in self._adj.values())

    @property
    def num_edges(self) -> int:
        return self.edge_count()

    # ──────────────────────────────────────────
    # Traversals
    # ──────────────────────────────────────────

    def bfs(self, start: str) -> List[str]:
        if start not in self._adj:
            return []
        visited: Set[str] = {start}
        queue: deque = deque([start])
        order: List[str] = []
        while queue:
            curr = queue.popleft()
            order.append(curr)
            for nxt in self._adj[curr]:
                if nxt not in visited:
                    visited.add(nxt)
                    queue.append(nxt)
        return order

    def dfs(self, start: str) -> List[str]:
        """Iterative DFS — no recursion limit risk."""
        if start not in self._adj:
            return []
        visited: Set[str] = set()
        order: List[str] = []
        stack = [start]
        while stack:
            u = stack.pop()
            if u in visited:
                continue
            visited.add(u)
            order.append(u)
            # Push in reverse order to preserve natural traversal order
            for v in reversed(list(self._adj[u])):
                if v not in visited:
                    stack.append(v)
        return order

    # ──────────────────────────────────────────
    # Cycle detection — iterative 3-color DFS
    # ──────────────────────────────────────────

    def find_cycles(self) -> List[List[str]]:
        """
        Iterative 3-colour cycle detection.

        Returns a list of cycles; each cycle is a list of node names forming
        a closed path. Duplicates across different DFS roots are excluded.
        Handles graphs with >1000 nodes safely (no sys.setrecursionlimit).
        """
        WHITE, GRAY, BLACK = 0, 1, 2
        color: Dict[str, int] = {u: WHITE for u in self._adj}
        cycles: List[List[str]] = []
        seen_cycle_keys: Set[frozenset] = set()

        for start in list(self._adj):
            if color[start] != WHITE:
                continue

            # Stack items: (node, iterator_over_neighbors, current_path)
            # We track path as a list and path_set as a set for O(1) lookup
            path: List[str] = []
            path_set: Set[str] = set()
            # Each frame: (node, neighbor_iterator)
            iter_stack: List[Tuple[str, "iterator"]] = []  # type: ignore[valid-type]

            color[start] = GRAY
            path.append(start)
            path_set.add(start)
            iter_stack.append((start, iter(self._adj[start])))

            while iter_stack:
                u, nbrs = iter_stack[-1]
                try:
                    v = next(nbrs)
                except StopIteration:
                    # Done with u — backtrack
                    color[u] = BLACK
                    path.pop()
                    path_set.discard(u)
                    iter_stack.pop()
                    continue

                if color[v] == GRAY and v in path_set:
                    # Found a back edge → cycle
                    cycle_start_idx = path.index(v)
                    cycle = path[cycle_start_idx:] + [v]
                    key = frozenset(cycle)
                    if key not in seen_cycle_keys:
                        seen_cycle_keys.add(key)
                        cycles.append(cycle)
                elif color[v] == WHITE:
                    color[v] = GRAY
                    path.append(v)
                    path_set.add(v)
                    iter_stack.append((v, iter(self._adj[v])))

        return cycles

    # ──────────────────────────────────────────
    # Topological sort — Kahn's algorithm
    # ──────────────────────────────────────────

    def topological_sort(self) -> Optional[List[str]]:
        """
        Kahn's algorithm. Returns ordered list or None if cycles exist.
        Stable: nodes with equal in-degree are processed in lexical order.
        """
        in_deg: Dict[str, int] = {u: self._in_degree[u] for u in self._adj}
        # Min-heap for stable ordering
        heap: List[str] = sorted(u for u in in_deg if in_deg[u] == 0)
        heapq.heapify(heap)
        result: List[str] = []

        while heap:
            u = heapq.heappop(heap)
            result.append(u)
            for v in sorted(self._adj[u]):
                in_deg[v] -= 1
                if in_deg[v] == 0:
                    heapq.heappush(heap, v)

        return result if len(result) == len(self._adj) else None

    # ──────────────────────────────────────────
    # Shortest path — Dijkstra
    # ──────────────────────────────────────────

    def shortest_path(
        self, start: str, target: str
    ) -> Optional[Tuple[float, List[str]]]:
        """Dijkstra's algorithm via heapq. Returns (distance, path) or None."""
        if start not in self._adj or target not in self._adj:
            return None

        dist: Dict[str, float] = {u: float("inf") for u in self._adj}
        dist[start] = 0.0
        prev: Dict[str, Optional[str]] = {u: None for u in self._adj}
        pq: List[Tuple[float, str]] = [(0.0, start)]

        while pq:
            d, u = heapq.heappop(pq)
            if d > dist[u]:
                continue
            if u == target:
                # Reconstruct path
                path: List[str] = []
                node: Optional[str] = target
                while node is not None:
                    path.append(node)
                    node = prev[node]
                path.reverse()
                return (dist[target], path)
            for v, attrs in self._adj[u].items():
                w = attrs.get("weight", 1.0)
                nd = d + w
                if nd < dist[v]:
                    dist[v] = nd
                    prev[v] = u
                    heapq.heappush(pq, (nd, v))

        return None

    # ──────────────────────────────────────────
    # Connected components (weakly connected)
    # ──────────────────────────────────────────

    def weakly_connected_components(self) -> List[List[str]]:
        """
        Union-Find based weakly-connected component detection.
        Treats all edges as undirected.
        """
        parent: Dict[str, str] = {u: u for u in self._adj}

        def find(x: str) -> str:
            while parent[x] != x:
                parent[x] = parent[parent[x]]  # path compression
                x = parent[x]
            return x

        def union(a: str, b: str) -> None:
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[ra] = rb

        for u in self._adj:
            for v in self._adj[u]:
                union(u, v)

        component_map: Dict[str, List[str]] = defaultdict(list)
        for node in self._adj:
            component_map[find(node)].append(node)

        return [sorted(members) for members in component_map.values()]

    # ──────────────────────────────────────────
    # Hub detection
    # ──────────────────────────────────────────

    def top_hub_nodes(self, k: int = 5) -> List[Tuple[str, int]]:
        """Return the k nodes with highest in-degree."""
        return sorted(
            ((n, self._in_degree[n]) for n in self._adj),
            key=lambda x: -x[1],
        )[:k]

    def max_dependency_depth(self) -> int:
        """Calculate the longest path depth across the DAG."""
        topo = self.topological_sort()
        if not topo:
            return -1  # Cycle present
        depth: Dict[str, int] = {u: 1 for u in self._adj}
        for u in topo:
            for v in self._adj[u]:
                if depth[u] + 1 > depth[v]:
                    depth[v] = depth[u] + 1
        return max(depth.values()) if depth else 0

    # ──────────────────────────────────────────
    # Graphify structural integration
    # ──────────────────────────────────────────

    @classmethod
    def from_graphify_data(cls, data: dict) -> "ZeroDepGraph":
        """
        Build or enhance a ZeroDepGraph using actual Graphify JSON structure.
        
        Graphify outputs a structured JSON representing nodes (entities such as files,
        modules, classes, functions, external packages) and directed edges (calls,
        imports, contains, depends_on).
        """
        g = cls()
        nodes = data.get("nodes", [])
        if isinstance(nodes, list):
            for n in nodes:
                if isinstance(n, dict):
                    node_id = str(n.get("id") or n.get("name") or "")
                    if node_id:
                        g.add_node(node_id, **n)
                elif isinstance(n, str):
                    g.add_node(n)
        elif isinstance(nodes, dict):
            for k, v in nodes.items():
                attrs = v if isinstance(v, dict) else {}
                g.add_node(str(k), **attrs)

        edges = data.get("edges", []) or data.get("links", [])
        if isinstance(edges, list):
            for e in edges:
                if isinstance(e, dict):
                    u = str(e.get("source") or e.get("from") or e.get("u") or "")
                    v = str(e.get("target") or e.get("to") or e.get("v") or "")
                    weight = float(e.get("weight", 1.0))
                    if u and v:
                        g.add_edge(u, v, weight=weight)
                elif isinstance(e, (list, tuple)) and len(e) >= 2:
                    g.add_edge(str(e[0]), str(e[1]))

        return g

    def integrate_graphify_data(self, data: dict) -> int:
        """
        Integrate Graphify entities and relationships into this graph.
        Returns the number of new edges added.
        """
        initial_edges = self.edge_count()
        sub = self.from_graphify_data(data)
        for u in sub.nodes():
            self.add_node(u)
            for v in sub.neighbors(u):
                self.add_edge(u, v)
        return self.edge_count() - initial_edges



# --- migration_engine.py ---

from dataclasses import dataclass
from typing import Dict, List, Optional


@dataclass
class MigrationRecipe:
    package_name: str
    target_symbol: str
    stdlib_module: str
    confidence: str  # HIGH, MEDIUM, LOW
    why_replaceable: str
    limitations: str
    before_code: str
    after_code: str

# 10 High-Impact Stdlib Migration Recipes with complete metadata & usage awareness
RECIPES: Dict[str, MigrationRecipe] = {
    "requests": MigrationRecipe(
        package_name="requests",
        target_symbol="requests.get",
        stdlib_module="urllib.request",
        confidence="HIGH",
        why_replaceable="Simple HTTP GET request detected. Standard library urllib.request handles basic HTTP queries cleanly.",
        limitations="Complex OAuth sessions, requests.Session(), custom transport adapters, or persistent cookie jars require manual migration.",
        before_code="- import requests\n- res = requests.get('https://api.example.com/data', timeout=5).json()",
        after_code="+ import urllib.request\n+ import json\n+ req = urllib.request.Request('https://api.example.com/data')\n+ with urllib.request.urlopen(req, timeout=5) as response:\n+     res = json.loads(response.read().decode('utf-8'))"
    ),
    "click": MigrationRecipe(
        package_name="click",
        target_symbol="click.command",
        stdlib_module="argparse",
        confidence="HIGH",
        why_replaceable="CLI option parsing detected. Standard library argparse provides robust flag parsing and help formatting.",
        limitations="Complex nested subcommand trees and interactive prompts require additional argparse subparser boilerplate.",
        before_code="- import click\n- @click.command()\n- @click.option('--name', prompt='Your name')\n- def hello(name):\n-     click.echo(f'Hello {name}')",
        after_code="+ import argparse\n+ parser = argparse.ArgumentParser()\n+ parser.add_argument('--name', required=True)\n+ args = parser.parse_args()\n+ print(f'Hello {args.name}')"
    ),
    "colorama": MigrationRecipe(
        package_name="colorama",
        target_symbol="colorama.Fore",
        stdlib_module="ANSI Escape Codes",
        confidence="HIGH",
        why_replaceable="Terminal text styling detected. Standard ANSI escape codes work natively across modern terminals.",
        limitations="Legacy Windows CMD without ANSI support requires manual os.system('') or ctypes initialization.",
        before_code="- from colorama import Fore, Style\n- print(Fore.RED + 'Error' + Style.RESET_ALL)",
        after_code="+ RED = '\\033[31m'\n+ RESET = '\\033[0m'\n+ print(f'{RED}Error{RESET}')"
    ),
    "tabulate": MigrationRecipe(
        package_name="tabulate",
        target_symbol="tabulate.tabulate",
        stdlib_module="string.format",
        confidence="HIGH",
        why_replaceable="Basic table formatting detected. Python string column specifiers provide clean alignment without third-party deps.",
        limitations="Auto-wrapping complex multi-line cell content requires textwrap.wrap logic.",
        before_code="- from tabulate import tabulate\n- print(tabulate([['Alice', 24], ['Bob', 19]], headers=['Name', 'Age']))",
        after_code="+ rows = [['Alice', 24], ['Bob', 19]]\n+ print(f'{\"Name\":<10} {\"Age\":<5}')\n+ for name, age in rows:\n+     print(f'{name:<10} {age:<5}')"
    ),
    "networkx": MigrationRecipe(
        package_name="networkx",
        target_symbol="networkx.Graph",
        stdlib_module="ZeroDep Graph Engine (collections / heapq)",
        confidence="HIGH",
        why_replaceable="Graph data structures and path algorithms detected. ZeroDep's native Graph engine handles BFS, DFS, cycles, and Dijkstra.",
        limitations="Advanced graph theory algorithms (e.g. max-flow, spectral centrality) are not implemented.",
        before_code="- import networkx as nx\n- G = nx.Graph()\n- G.add_edge('A', 'B')",
        after_code="+ from zerodep.graph_engine import ZeroDepGraph\n+ G = ZeroDepGraph()\n+ G.add_edge('A', 'B')"
    ),
    "radon": MigrationRecipe(
        package_name="radon",
        target_symbol="radon.complexity",
        stdlib_module="ast",
        confidence="MEDIUM",
        why_replaceable="Cyclomatic complexity calculation detected. Standard ast NodeVisitor counts decision branches directly.",
        limitations="Does not compute advanced Radon Maintainability Index (MI) metrics out-of-the-box.",
        before_code="- from radon.complexity import cc_visit\n- results = cc_visit(code)",
        after_code="+ import ast\n+ tree = ast.parse(code)\n+ complexity = sum(1 for node in ast.walk(tree) if isinstance(node, (ast.If, ast.For, ast.While)))"
    ),
    "python-dotenv": MigrationRecipe(
        package_name="python-dotenv",
        target_symbol="dotenv.load_dotenv",
        stdlib_module="os / pathlib",
        confidence="HIGH",
        why_replaceable=".env environment file parsing detected. Standard library pathlib and string splitting handle key=value loading natively.",
        limitations="Does not support variable expansion ($VAR) or multiline strings without regex enhancement.",
        before_code="- from dotenv import load_dotenv\n- load_dotenv()",
        after_code="+ import os, pathlib\n+ env_path = pathlib.Path('.env')\n+ if env_path.exists():\n+     for line in env_path.read_text().splitlines():\n+         if line and not line.startswith('#') and '=' in line:\n+             k, v = line.split('=', 1)\n+             os.environ[k.strip()] = v.strip()"
    ),
    "loguru": MigrationRecipe(
        package_name="loguru",
        target_symbol="loguru.logger",
        stdlib_module="logging",
        confidence="HIGH",
        why_replaceable="Basic logger invocations detected. Standard library logging provides thread-safe formatting and file handlers.",
        limitations="Loguru's automatic context binding and colorized defaults require manual logging.basicConfig setup.",
        before_code="- from loguru import logger\n- logger.info('Started')",
        after_code="+ import logging\n+ logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')\n+ logging.info('Started')"
    ),
    "tomli": MigrationRecipe(
        package_name="tomli",
        target_symbol="tomli.loads",
        stdlib_module="tomllib",
        confidence="HIGH",
        why_replaceable="TOML parsing detected. Python 3.11+ includes built-in tomllib with identical API.",
        limitations="tomllib is read-only in stdlib (cannot write TOML).",
        before_code="- import tomli\n- config = tomli.loads(toml_str)",
        after_code="+ import tomllib\n+ config = tomllib.loads(toml_str)"
    ),
    "pytz": MigrationRecipe(
        package_name="pytz",
        target_symbol="pytz.timezone",
        stdlib_module="zoneinfo",
        confidence="HIGH",
        why_replaceable="Timezone lookup detected. Python 3.9+ standard library zoneinfo provides modern IANA timezone support.",
        limitations="Windows systems without system timezone databases may require tzdata if offline.",
        before_code="- import pytz\n- tz = pytz.timezone('UTC')",
        after_code="+ from zoneinfo import ZoneInfo\n+ tz = ZoneInfo('UTC')"
    ),
}

def get_recipe(package_name: str, used_symbols: Optional[Dict[str, int]] = None) -> Optional[MigrationRecipe]:
    recipe = RECIPES.get(package_name.lower())
    if not recipe:
        return None
    
    # Dynamic confidence downgrading for complex API usage
    if used_symbols and package_name.lower() == "requests":
        complex_calls = {"requests.Session", "requests.adapters", "requests.auth", "requests.cookies"}
        if any(sym in complex_calls for sym in used_symbols):
            return MigrationRecipe(
                package_name=recipe.package_name,
                target_symbol=recipe.target_symbol,
                stdlib_module=recipe.stdlib_module,
                confidence="LOW",
                why_replaceable="Complex requests usage (Session/Auth/Adapters) detected. Manual migration to urllib required.",
                limitations="Session persistence, transport adapters, or custom auth classes cannot be auto-diffed simply.",
                before_code=recipe.before_code,
                after_code=recipe.after_code,
            )
    return recipe

# --- scoring_engine.py ---

from dataclasses import dataclass
from typing import List, Optional, Set

@dataclass
class ScoreBreakdown:
    current_score: float
    potential_score: float
    grade: str
    unused_count: int
    replaceable_count: int
    essential_count: int
    unused_penalty: float
    replaceable_penalty: float
    essential_penalty: float
    stdlib_bonus: float
    undeclared_count: int = 0
    undeclared_penalty: float = 0.0
    audit_status: str = "ASSESSED"  # "ASSESSED" | "GENUINE_ZERO_DEP" | "UNABLE_TO_ASSESS" | "UNDECLARED_DEPS_FOUND"
    status_message: str = ""

def calculate_grade(score: float, audit_status: str = "ASSESSED") -> str:
    if audit_status == "UNABLE_TO_ASSESS":
        return "N/A"
    if score >= 90.0:
        return "A+"
    elif score >= 80.0:
        return "A"
    elif score >= 70.0:
        return "B"
    elif score >= 60.0:
        return "C"
    elif score >= 50.0:
        return "D"
    else:
        return "F"

def calculate_score(
    declared_deps: Set[str],
    used_deps: Set[str],
    replaceable_deps: Set[str],
    total_stdlib_calls: int,
    total_calls: int,
    undeclared_third_party_deps: Optional[Set[str]] = None,
    files_scanned: int = -1,
    manifest_found: bool = True,
) -> ScoreBreakdown:
    undeclared = undeclared_third_party_deps or set()
    undeclared_count = len(undeclared)

    # 1. Edge case: Incomplete audit / no files scanned
    # files_scanned == -1 means "not provided by caller" (legacy callers / unit tests)
    if files_scanned == 0:
        return ScoreBreakdown(
            current_score=0.0,
            potential_score=0.0,
            grade="N/A",
            unused_count=0,
            replaceable_count=0,
            essential_count=0,
            unused_penalty=0.0,
            replaceable_penalty=0.0,
            essential_penalty=0.0,
            stdlib_bonus=0.0,
            undeclared_count=0,
            undeclared_penalty=0.0,
            audit_status="UNABLE_TO_ASSESS",
            status_message="No supported Python source files found in target path.",
        )

    # 2. Edge case: No manifest found, but third-party imports detected in source
    if not manifest_found and not declared_deps and undeclared_count > 0:
        # Penalize for undeclared dependencies
        undeclared_penalty = undeclared_count * 10.0
        raw_current = max(0.0, 100.0 - undeclared_penalty)
        return ScoreBreakdown(
            current_score=round(raw_current, 1),
            potential_score=round(raw_current, 1),
            grade=calculate_grade(raw_current),
            unused_count=0,
            replaceable_count=0,
            essential_count=undeclared_count,
            unused_penalty=0.0,
            replaceable_penalty=0.0,
            essential_penalty=undeclared_penalty,
            stdlib_bonus=0.0,
            undeclared_count=undeclared_count,
            undeclared_penalty=undeclared_penalty,
            audit_status="UNDECLARED_DEPS_FOUND",
            status_message=f"No manifest found; {undeclared_count} unpinned third-party dependencies detected in source.",
        )

    # 3. Edge case: Genuine zero-dependency project (manifest empty or absent, 0 third-party imports, stdlib only)
    if not declared_deps and undeclared_count == 0:
        return ScoreBreakdown(
            current_score=100.0,
            potential_score=100.0,
            grade="A+",
            unused_count=0,
            replaceable_count=0,
            essential_count=0,
            unused_penalty=0.0,
            replaceable_penalty=0.0,
            essential_penalty=0.0,
            stdlib_bonus=10.0,
            undeclared_count=0,
            undeclared_penalty=0.0,
            audit_status="GENUINE_ZERO_DEP",
            status_message="Pure Python standard library project. 0 external dependencies detected.",
        )

    # 4. Standard calculation for declared dependencies
    unused_deps = declared_deps - used_deps
    active_deps = declared_deps & used_deps
    
    rep_active = active_deps & replaceable_deps
    essential_active = active_deps - replaceable_deps

    unused_count = len(unused_deps)
    replaceable_count = len(rep_active)
    essential_count = len(essential_active)

    unused_penalty = unused_count * 15.0
    replaceable_penalty = replaceable_count * 8.0
    essential_penalty = essential_count * 4.0
    undeclared_penalty = undeclared_count * 6.0

    stdlib_ratio = (total_stdlib_calls / total_calls) if total_calls > 0 else 1.0
    stdlib_bonus = round(stdlib_ratio * 10.0, 1)

    raw_current = 100.0 - (unused_penalty + replaceable_penalty + essential_penalty + undeclared_penalty) + stdlib_bonus
    current_score = max(0.0, min(100.0, raw_current))

    raw_potential = 100.0 - essential_penalty + stdlib_bonus
    potential_score = max(0.0, min(100.0, raw_potential))

    return ScoreBreakdown(
        current_score=round(current_score, 1),
        potential_score=round(potential_score, 1),
        grade=calculate_grade(current_score),
        unused_count=unused_count,
        replaceable_count=replaceable_count,
        essential_count=essential_count,
        unused_penalty=unused_penalty,
        replaceable_penalty=replaceable_penalty,
        essential_penalty=essential_penalty,
        stdlib_bonus=stdlib_bonus,
        undeclared_count=undeclared_count,
        undeclared_penalty=undeclared_penalty,
        audit_status="ASSESSED",
        status_message="Project dependencies and health calculated from manifest and AST evidence.",
    )


# --- cli_formatter.py ---



class ANSIFormatter:
    """ANSI color formatter that auto-detects TTY and disables in non-interactive contexts."""

    def __init__(self, force_color: bool = False, force_plain: bool = False):
        if force_plain:
            self.use_color = False
        elif force_color:
            self.use_color = True
        else:
            self.use_color = self._supports_color()

    def _supports_color(self) -> bool:
        """Return True only when stdout is an interactive TTY that supports ANSI."""
        # Respect NO_COLOR convention: https://no-color.org/
        if os.environ.get("NO_COLOR"):
            return False
        # Not a real TTY (piped/redirected)
        if not hasattr(sys.stdout, "isatty") or not sys.stdout.isatty():
            return False
        # Windows: only support color when a compatible ANSI terminal is present
        if os.name == "nt":
            return (
                os.environ.get("TERM") in ("xterm", "xterm-256color")
                or os.environ.get("ANSICON") is not None
                or os.environ.get("WT_SESSION") is not None  # Windows Terminal
            )
        return True

    def bold(self, text: str) -> str:
        return f"\033[1m{text}\033[0m" if self.use_color else text

    def red(self, text: str) -> str:
        return f"\033[31m{text}\033[0m" if self.use_color else text

    def green(self, text: str) -> str:
        return f"\033[32m{text}\033[0m" if self.use_color else text

    def yellow(self, text: str) -> str:
        return f"\033[33m{text}\033[0m" if self.use_color else text

    def cyan(self, text: str) -> str:
        return f"\033[36m{text}\033[0m" if self.use_color else text

    def header(self, title: str) -> str:
        term_width = min(shutil.get_terminal_size((80, 20)).columns, 80)
        line = "=" * term_width
        centered = title.center(term_width)
        return f"{self.cyan(line)}\n{self.bold(centered)}\n{self.cyan(line)}"

    def subheader(self, title: str) -> str:
        term_width = min(shutil.get_terminal_size((80, 20)).columns, 80)
        line = "-" * term_width
        return f"\n{self.cyan(line)}\n[{self.bold(title)}]\n{self.cyan(line)}"

# --- impact_engine.py ---

from typing import Dict, List, Optional, Set, Tuple


@dataclass
class DependencyImpact:
    package: str
    level: str  # "LOW" | "MEDIUM" | "HIGH"
    files_affected: int
    modules_affected: int
    transitive_modules_affected: int
    import_locations: int
    call_sites: int
    symbols_count: int
    symbols_used: List[str]
    is_graph_hub: bool
    is_hub_connected: bool
    graph_connectivity: Dict[str, int]
    circular_dependency_involvement: bool
    api_complexity: str  # "LOW" | "MEDIUM" | "HIGH"
    migration_confidence: str  # "HIGH" | "MEDIUM" | "LOW"
    has_dynamic_imports: bool
    has_conditional_imports: bool
    has_star_imports: bool
    estimated_migration_effort: str  # "Trivial" | "Low" | "Moderate" | "High" | "Complex/Manual"
    reasons: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "level": self.level,
            "files_affected": self.files_affected,
            "modules_affected": self.modules_affected,
            "transitive_modules_affected": self.transitive_modules_affected,
            "import_locations": self.import_locations,
            "call_sites": self.call_sites,
            "symbols_count": self.symbols_count,
            "symbols_used": self.symbols_used,
            "is_graph_hub": self.is_graph_hub,
            "is_hub_connected": self.is_hub_connected,
            "graph_connectivity": self.graph_connectivity,
            "circular_dependency_involvement": self.circular_dependency_involvement,
            "api_complexity": self.api_complexity,
            "migration_confidence": self.migration_confidence,
            "has_dynamic_imports": self.has_dynamic_imports,
            "has_conditional_imports": self.has_conditional_imports,
            "has_star_imports": self.has_star_imports,
            "estimated_migration_effort": self.estimated_migration_effort,
            "reasons": self.reasons,
        }


def calculate_dependency_impact(dep_name: str, scan_data: dict) -> DependencyImpact:
    """
    Calculate explainable impact metrics for a declared dependency using existing AST and Graph scan results.
    Never rescans files or executes code.
    """
    usage_details = scan_data.get("usage_details", {})
    dep_info = usage_details.get(dep_name, {})
    graph = scan_data.get("graph")
    ast_results = scan_data.get("ast_results", [])
    
    files_used: List[str] = dep_info.get("files", [])
    files_affected_count = len(files_used)
    import_locations = dep_info.get("imports_count", 0)
    call_sites_count = dep_info.get("occurrences", 0)
    symbols_dict: Dict[str, int] = dep_info.get("symbols", {})
    symbols_used = sorted(symbols_dict.keys())
    symbols_count = len(symbols_used)
    migration_confidence = dep_info.get("confidence", "HIGH")
    status = dep_info.get("status", "UNKNOWN")

    # 1. Modules affected: direct files using this dependency
    direct_modules: Set[str] = set(files_used)
    modules_affected_count = len(direct_modules)

    # 2. Transitive reach: modules in project graph that depend on direct_modules
    transitive_affected: Set[str] = set(direct_modules)
    if graph:
        # Avoid running full BFS for every node on large graphs; find reachable nodes from direct_modules
        for mod in direct_modules:
            reachable = graph.bfs(mod)
            transitive_affected.update(reachable)
    transitive_modules_affected_count = len(transitive_affected)

    # 3. Hub analysis: direct hub vs hub-connected
    is_graph_hub = False
    is_hub_connected = False
    top_hubs: Set[str] = set()
    total_in_degree = 0
    total_out_degree = 0

    if graph:
        hub_tuples = graph.top_hub_nodes(k=5)
        top_hubs = {h[0] for h in hub_tuples if h[1] > 0}
        
        # Check if dependency itself (by import name or package name) is a hub node
        if graph.has_node(dep_name) and graph.in_degree(dep_name) >= 3:
            is_graph_hub = True

        for mod in direct_modules:
            if graph.has_node(mod):
                in_deg = graph.in_degree(mod)
                out_deg = graph.out_degree(mod)
                total_in_degree += in_deg
                total_out_degree += out_deg
                if mod in top_hubs or in_deg >= 3:
                    is_graph_hub = True
                # Check if connected to a top hub
                for nbr in graph.neighbors(mod):
                    if nbr in top_hubs:
                        is_hub_connected = True

    graph_connectivity = {
        "in_degree": total_in_degree,
        "out_degree": total_out_degree,
        "transitive_reach": transitive_modules_affected_count,
    }

    # 4. Circular dependency involvement
    circular_dependency_involvement = False
    if graph:
        cycles = graph.find_cycles()
        for cycle in cycles:
            cycle_set = set(cycle)
            if direct_modules.intersection(cycle_set):
                circular_dependency_involvement = True
                break

    # 5. Dynamic, Conditional, Star imports
    has_dynamic_imports = False
    has_conditional_imports = False
    has_star_imports = False

    # Inspect AST results for direct modules touch
    dep_lower = dep_name.lower()
    for res in ast_results:
        if res.file_path in direct_modules:
            for imp in res.imports:
                mod_base = imp.original_module.split(".")[0].lower()
                if mod_base == dep_lower:
                    if imp.is_conditional or imp.scope_kind == "conditional":
                        has_conditional_imports = True
                    if imp.is_star:
                        has_star_imports = True
            for di in res.dynamic_imports:
                # Only attribute a dynamic import to this dependency when:
                # - the static target explicitly names this dependency (di.target_module == dep_name), OR
                # - the target is unknown (None) AND the import is inside a file that directly uses
                #   this dep — but in that case we still do NOT attribute it to a specific dep
                #   because we cannot be certain.  We therefore only flag it when target is explicit.
                if di.target_module is not None and di.target_module.lower() == dep_lower:
                    has_dynamic_imports = True

    # 6. API Complexity
    # Base heuristic: symbol count + specific complex features (e.g. Session, context managers)
    if symbols_count == 0:
        api_complexity = "LOW"
    elif symbols_count <= 2 and not any("Session" in s for s in symbols_used):
        api_complexity = "LOW"
    elif symbols_count <= 5 and not any("Session" in s for s in symbols_used):
        api_complexity = "MEDIUM"
    else:
        api_complexity = "HIGH"

    # 7. Classification Level & Reasons (Explainable Impact Formula)
    reasons: List[str] = []
    points = 0

    if status == "UNUSED":
        level = "LOW"
        estimated_migration_effort = "Trivial"
        reasons.append("Unused dependency — declared in manifest but 0 AST references")
    else:
        # File & Module scale
        if files_affected_count >= 4:
            points += 3
            reasons.append(f"Used across {files_affected_count} files ({transitive_modules_affected_count} transitive modules)")
        elif files_affected_count > 1:
            points += 2
            reasons.append(f"Used in {files_affected_count} files")
        else:
            points += 1
            reasons.append("Localized to a single file")

        # Call sites
        if call_sites_count >= 15:
            points += 3
            reasons.append(f"High call-site count ({call_sites_count} references)")
        elif call_sites_count >= 5:
            points += 2
            reasons.append(f"Moderate call-site count ({call_sites_count} references)")
        else:
            points += 1
            reasons.append(f"Low call-site count ({call_sites_count} references)")

        # Hub role
        if is_graph_hub:
            points += 3
            reasons.append("Hub dependency — heavily imported by core modules")
        elif is_hub_connected:
            points += 1
            reasons.append("Connected to central dependency graph hub modules")

        # API Complexity & Confidence
        if api_complexity == "HIGH":
            points += 2
            reasons.append("Complex API usage detected (multiple symbols / stateful clients)")
        if migration_confidence == "LOW":
            points += 3
            reasons.append("LOW migration confidence (requires manual non-trivial refactoring)")

        # Risk multipliers
        if circular_dependency_involvement:
            points += 2
            reasons.append("Involved in circular dependency loops")
        if has_dynamic_imports:
            points += 2
            reasons.append("Dynamic import usage detected (cannot be statically resolved)")
        if has_conditional_imports:
            points += 1
            reasons.append("Conditional import guards present")
        if has_star_imports:
            points += 1
            reasons.append("Star imports detected (obscures exact symbol usage)")

        # Classification decision
        if points >= 8 or migration_confidence == "LOW" and points >= 6 or is_graph_hub and points >= 6:
            level = "HIGH"
        elif points >= 4:
            level = "MEDIUM"
        else:
            level = "LOW"

        # Estimated effort mapping
        if level == "LOW":
            estimated_migration_effort = "Low" if call_sites_count > 0 else "Trivial"
        elif level == "MEDIUM":
            estimated_migration_effort = "Moderate"
        else:
            estimated_migration_effort = "Complex/Manual" if migration_confidence == "LOW" else "High"

    return DependencyImpact(
        package=dep_name,
        level=level,
        files_affected=files_affected_count,
        modules_affected=modules_affected_count,
        transitive_modules_affected=transitive_modules_affected_count,
        import_locations=import_locations,
        call_sites=call_sites_count,
        symbols_count=symbols_count,
        symbols_used=symbols_used,
        is_graph_hub=is_graph_hub,
        is_hub_connected=is_hub_connected,
        graph_connectivity=graph_connectivity,
        circular_dependency_involvement=circular_dependency_involvement,
        api_complexity=api_complexity,
        migration_confidence=migration_confidence,
        has_dynamic_imports=has_dynamic_imports,
        has_conditional_imports=has_conditional_imports,
        has_star_imports=has_star_imports,
        estimated_migration_effort=estimated_migration_effort,
        reasons=reasons,
    )

# --- risk_engine.py ---

from typing import Dict, List, Optional, Set, Tuple


@dataclass
class RiskFactor:
    severity: str  # "HIGH" | "MED" | "LOW"
    description: str


@dataclass
class DependencyRisk:
    package: str
    level: str  # "LOW" | "MEDIUM" | "HIGH"
    score: int  # 0 to 100
    risk_factors: List[RiskFactor] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    recommendation: str = "KEEP / NO ACTION"
    reasons: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "level": self.level,
            "score": self.score,
            "risk_factors": [
                {"severity": rf.severity, "description": rf.description}
                for rf in self.risk_factors
            ],
            "warnings": self.warnings,
            "recommendation": self.recommendation,
            "reasons": self.reasons,
        }


def calculate_dependency_risk(
    dep_name: str,
    scan_data: dict,
    impact_data: Optional[dict] = None,
) -> DependencyRisk:
    """
    Calculate deterministic, evidence-backed risk metrics and recommendations for a dependency.
    Never executes code or rescans files.
    """
    usage_details = scan_data.get("usage_details", {})
    dep_info = usage_details.get(dep_name, {})
    ast_results = scan_data.get("ast_results", [])
    
    # Sourced from Phase 1 impact data if available, or scan_data
    impacts = scan_data.get("impacts", {})
    imp = impact_data or (impacts.get(dep_name).to_dict() if dep_name in impacts else {})

    status = dep_info.get("status", "UNKNOWN")
    confidence = dep_info.get("confidence", "HIGH")
    files_used = dep_info.get("files", [])
    call_sites_count = dep_info.get("occurrences", 0)
    symbols_dict = dep_info.get("symbols", {})
    symbols_used = sorted(symbols_dict.keys())
    
    impact_level = imp.get("level", "LOW")
    api_complexity = imp.get("api_complexity", "LOW")
    has_dynamic = imp.get("has_dynamic_imports", False)
    has_conditional = imp.get("has_conditional_imports", False)
    has_star = imp.get("has_star_imports", False)
    circular = imp.get("circular_dependency_involvement", False)

    score = 0
    risk_factors: List[RiskFactor] = []
    warnings: List[str] = []
    reasons: List[str] = []

    # 1. Unused status (Maintenance debt)
    if status == "UNUSED":
        score += 15
        risk_factors.append(RiskFactor("MED", "Unused manifest declaration (manifest debt)"))
        reasons.append("Unused manifest declaration provides no value to codebase")
    elif status == "IMPORTED_BUT_UNUSED":
        score += 20
        risk_factors.append(RiskFactor("MED", "Import statement present but 0 call sites"))
        reasons.append("Imported module without call sites adds dead imports")

    # 2. Migration Confidence & API Complexity
    if confidence == "LOW":
        score += 25
        risk_factors.append(RiskFactor("HIGH", "LOW migration confidence (complex refactoring required)"))
        reasons.append("LOW migration confidence indicates non-trivial migration obstacles")
    elif confidence == "MEDIUM":
        score += 10
        risk_factors.append(RiskFactor("MED", "MEDIUM migration confidence"))

    if api_complexity == "HIGH":
        score += 15
        risk_factors.append(RiskFactor("HIGH", "Complex API / stateful client usage detected"))
        reasons.append("Stateful API (e.g. Session or context manager) complicates migration")
    elif api_complexity == "MEDIUM":
        score += 5
        risk_factors.append(RiskFactor("MED", "Moderate API surface complexity"))

    # 3. Dynamic / Star / Conditional Imports (Uncertainty factors)
    if has_dynamic:
        score += 20
        risk_factors.append(RiskFactor("HIGH", "Dynamic import usage detected"))
        warnings.append("Dynamic resolution cannot be fully verified statically")
        reasons.append("Dynamic imports hide exact call sites from static analysis")

    if has_star:
        score += 15
        risk_factors.append(RiskFactor("MED", "Star import (from pkg import *) detected"))
        warnings.append("Star imports obscure exact symbol usage")
        reasons.append("Star imports obscure symbol origins")

    if has_conditional:
        score += 10
        risk_factors.append(RiskFactor("MED", "Conditional import guard detected"))
        warnings.append("Conditional imports introduce environment-dependent behavior")
        reasons.append("Conditional imports indicate environment branching")

    # 4. Circular Dependency & Graph Position
    if circular:
        score += 15
        risk_factors.append(RiskFactor("HIGH", "Involved in circular dependency loop"))
        reasons.append("Circular dependency involvement increases refactoring risk")

    # 5. Usage Volume Risk
    if call_sites_count >= 15:
        score += 10
        risk_factors.append(RiskFactor("MED", f"High call-site volume ({call_sites_count} references)"))
    if len(files_used) >= 4:
        score += 10
        risk_factors.append(RiskFactor("MED", f"Widespread file touch ({len(files_used)} files)"))

    # 6. Absence of Stdlib Replacement
    if status == "ESSENTIAL":
        score += 10
        risk_factors.append(RiskFactor("LOW", "No stdlib replacement available (essential dependency)"))
        reasons.append("Third-party package retained as essential dependency")
    elif status == "REPLACEABLE" and confidence == "HIGH":
        risk_factors.append(RiskFactor("LOW", "High-confidence stdlib replacement available"))
        reasons.append("High-confidence stdlib alternative reduces long-term risk")

    # Bound score between 0 and 100
    score = max(0, min(100, score))

    # Level classification
    if score >= 55:
        level = "HIGH"
    elif score >= 25:
        level = "MEDIUM"
    else:
        level = "LOW"

    # Recommendation Mapping Engine
    if status in ("UNUSED", "IMPORTED_BUT_UNUSED"):
        recommendation = "REMOVE"
    elif status == "REPLACEABLE" and confidence == "HIGH" and level in ("LOW", "MEDIUM"):
        recommendation = "SAFE MIGRATION CANDIDATE"
    elif has_dynamic or has_star:
        recommendation = "INVESTIGATE"
    elif status == "REPLACEABLE":
        if confidence == "LOW":
            recommendation = "MANUAL REVIEW"
        elif confidence == "MEDIUM" or level == "MEDIUM":
            recommendation = "REVIEW MIGRATION"
        else:
            recommendation = "SAFE MIGRATION CANDIDATE"
    else:  # ESSENTIAL
        if level == "HIGH" or circular:
            recommendation = "INVESTIGATE"
        else:
            recommendation = "KEEP / NO ACTION"

    return DependencyRisk(
        package=dep_name,
        level=level,
        score=score,
        risk_factors=risk_factors,
        warnings=warnings,
        recommendation=recommendation,
        reasons=reasons,
    )


def build_risk_impact_matrix(impacts: dict, risks: dict) -> Dict[str, Dict[str, List[str]]]:
    """
    Construct a 3x3 Risk vs Impact matrix mapping dependencies.
    Matrix structure: matrix[risk_level][impact_level] = [dep_names]
    """
    levels = ["LOW", "MEDIUM", "HIGH"]
    matrix: Dict[str, Dict[str, List[str]]] = {
        r_lvl: {i_lvl: [] for i_lvl in levels} for r_lvl in levels
    }

    for dep_name in sorted(impacts.keys()):
        imp_lvl = impacts[dep_name].level if hasattr(impacts[dep_name], "level") else impacts[dep_name].get("level", "LOW")
        risk_obj = risks.get(dep_name)
        risk_lvl = risk_obj.level if hasattr(risk_obj, "level") else risk_obj.get("level", "LOW")
        
        if risk_lvl in matrix and imp_lvl in matrix[risk_lvl]:
            matrix[risk_lvl][imp_lvl].append(dep_name)

    return matrix

# --- explanation_engine.py ---

from typing import Dict, List, Optional

try:
    from zerodep.migration_engine import get_recipe
except ImportError:
    try:
        from migration_engine import get_recipe  # type: ignore
    except ImportError:
        get_recipe = globals().get("get_recipe")  # type: ignore


@dataclass
class DependencyExplanation:
    package: str
    category: str                   # "UNUSED" | "SIMPLE REPLACEABLE" | "COMPLEX REPLACEABLE" | "ESSENTIAL"
    purpose: str
    where_used: List[str]
    what_is_used: List[str]          # Symbol breakdown with call counts
    why_present: List[str]           # Evidence-backed presence reasons
    replacement_opportunity: str     # Target stdlib module or "No direct stdlib replacement"
    replacement_limitations: List[str]
    confidence: str                  # "HIGH" | "MEDIUM" | "LOW"
    impact_level: str                # "LOW" | "MEDIUM" | "HIGH"
    impact_summary: str              # e.g., "1 file affected, 2 call sites"
    risk_level: str                  # "LOW" | "MEDIUM" | "HIGH"
    risk_score: int                  # 0–100
    recommendation: str              # "REMOVE" | "SAFE MIGRATION CANDIDATE" | ...
    reasons: List[str]               # Combined explainable evidence bullet points
    warnings: List[str]

    def to_dict(self) -> dict:
        return {
            "package": self.package,
            "category": self.category,
            "purpose": self.purpose,
            "where_used": self.where_used,
            "what_is_used": self.what_is_used,
            "why_present": self.why_present,
            "replacement_opportunity": self.replacement_opportunity,
            "replacement_limitations": self.replacement_limitations,
            "confidence": self.confidence,
            "impact_level": self.impact_level,
            "impact_summary": self.impact_summary,
            "risk_level": self.risk_level,
            "risk_score": self.risk_score,
            "recommendation": self.recommendation,
            "reasons": self.reasons,
            "warnings": self.warnings,
        }


# Known stdlib domain/purpose mapping based strictly on package identity & detected symbols
_PACKAGE_PURPOSES = {
    "click": "CLI command and option parsing",
    "colorama": "Terminal ANSI text coloring and styling",
    "tabulate": "Pretty-printing tabular data as formatted text",
    "networkx": "Graph data structures and network algorithms",
    "requests": "HTTP network requests and web API communications",
    "pytz": "Timezone definitions and date/time localization",
    "pandas": "DataFrame manipulation and tabular data analysis",
    "numpy": "Numerical array operations and mathematical computing",
    "radon": "Code complexity and maintainability metrics",
    "python-dotenv": "Loading environment variables from .env files",
    "loguru": "Structured logging and exception tracking",
    "tomli": "TOML configuration file parsing",
}


def _infer_purpose(dep_name: str, symbols: Dict[str, int], recipe: Optional[object]) -> str:
    dep_lower = dep_name.lower()
    if dep_lower in _PACKAGE_PURPOSES:
        return _PACKAGE_PURPOSES[dep_lower]
    if recipe and hasattr(recipe, "why_replaceable") and recipe.why_replaceable:
        return recipe.why_replaceable
    if symbols:
        sym_names = ", ".join(sorted(symbols.keys())[:3])
        return f"Detected API usage involving symbols: {sym_names}"
    return f"Third-party package '{dep_name}' declared in project manifests"


def generate_explanation(dep_name: str, scan_data: dict) -> DependencyExplanation:
    """Generate a deterministic, evidence-backed explanation for a single dependency."""
    usage_details = scan_data.get("usage_details", {})
    info = usage_details.get(dep_name, {})
    
    impacts = scan_data.get("impacts", {})
    risks = scan_data.get("risks", {})
    
    imp = impacts.get(dep_name)
    risk = risks.get(dep_name)

    status = info.get("status", "UNKNOWN")
    confidence = info.get("confidence", "HIGH")
    files = info.get("files", [])
    symbols = info.get("symbols", {})

    recipe_fn = get_recipe or globals().get("get_recipe")
    recipe = recipe_fn(dep_name, symbols) if recipe_fn else None

    # 1. Purpose
    purpose = _infer_purpose(dep_name, symbols, recipe) if status not in ("UNUSED", "IMPORTED_BUT_UNUSED") else "No active usage detected in codebase AST"

    # 2. Category
    if status in ("UNUSED", "IMPORTED_BUT_UNUSED"):
        category = "UNUSED"
    elif status == "REPLACEABLE":
        if recipe and recipe.confidence == "HIGH":
            category = "SIMPLE REPLACEABLE"
        else:
            category = "COMPLEX REPLACEABLE"
    else:
        category = "ESSENTIAL"

    # 3. What is used & Why present (Evidence-backed statements only)
    what_is_used = []
    why_present = []

    if status in ("UNUSED", "IMPORTED_BUT_UNUSED"):
        why_present.append("Declared in project manifest, but 0 active AST call sites detected")
    else:
        for sym, count in sorted(symbols.items(), key=lambda x: (-x[1], x[0])):
            what_is_used.append(f"{sym} ({count} call{'s' if count != 1 else ''})")
        
        why_present.append(f"Static evidence indicates active AST references across {len(files)} file{'s' if len(files) != 1 else ''}")
        if any("Session" in s for s in symbols):
            why_present.append("Detected stateful client session usage (e.g. Session object)")
        if any("command" in s or "option" in s for s in symbols):
            why_present.append("Detected CLI command line parsing decorators/functions")
        if any("Graph" in s for s in symbols):
            why_present.append("Detected graph object instantiation")

    # 4. Replacement & Limitations
    replacement_limitations = []
    if recipe:
        eff_conf = recipe.confidence
        replacement_opportunity = f"{recipe.package_name} -> {recipe.stdlib_module}"
        if recipe.limitations:
            replacement_limitations.append(recipe.limitations)
    else:
        eff_conf = confidence
        replacement_opportunity = "No direct standard-library replacement"

    if status in ("UNUSED", "IMPORTED_BUT_UNUSED"):
        replacement_limitations.append("Static analysis cannot detect runtime dynamic references not present in source code")

    # 5. Impact & Risk metrics
    imp_lvl = imp.level if imp else "LOW"
    imp_files = imp.files_affected if imp else len(files)
    imp_calls = imp.call_sites if imp else occurrences
    impact_summary = f"{imp_files} file{'s' if imp_files != 1 else ''} affected, {imp_calls} call site{'s' if imp_calls != 1 else ''}"

    risk_lvl = risk.level if risk else "LOW"
    risk_sc = risk.score if risk else 0
    rec = risk.recommendation if risk else ("REMOVE" if category == "UNUSED" else "KEEP / NO ACTION")

    # 6. Combined Reasons & Warnings
    reasons = []
    if category == "UNUSED":
        reasons.append("Unused manifest declaration adds unnecessary maintenance overhead")
    elif category == "SIMPLE REPLACEABLE":
        reasons.append("High-confidence standard library replacement available")
        reasons.append("Low migration risk with straightforward refactoring pattern")
    elif category == "COMPLEX REPLACEABLE":
        reasons.append("Complex API usage surface requires careful manual refactoring")
    else:
        reasons.append("Necessary external package providing specialized functionality without stdlib equivalent")

    warnings = []
    if risk and risk.warnings:
        warnings.extend(risk.warnings)

    return DependencyExplanation(
        package=dep_name,
        category=category,
        purpose=purpose,
        where_used=files,
        what_is_used=what_is_used,
        why_present=why_present,
        replacement_opportunity=replacement_opportunity,
        replacement_limitations=replacement_limitations,
        confidence=eff_conf,
        impact_level=imp_lvl,
        impact_summary=impact_summary,
        risk_level=risk_lvl,
        risk_score=risk_sc,
        recommendation=rec,
        reasons=reasons,
        warnings=warnings,
    )


def generate_all_explanations(scan_data: dict) -> Dict[str, DependencyExplanation]:
    """Generate explanations for all declared dependencies in a project scan."""
    explanations = {}
    declared = scan_data.get("declared_deps", set())
    for dep in sorted(declared):
        explanations[dep] = generate_explanation(dep, scan_data)
    return explanations

# --- action_planner.py ---

from typing import Dict, List, Optional, Tuple

try:
    from zerodep.explanation_engine import generate_explanation
    from zerodep.migration_engine import get_recipe
except ImportError:
    try:
        from explanation_engine import generate_explanation  # type: ignore
        from migration_engine import get_recipe  # type: ignore
    except ImportError:
        generate_explanation = globals().get("generate_explanation")  # type: ignore
        get_recipe = globals().get("get_recipe")  # type: ignore


@dataclass
class ActionPlanItem:
    package: str
    priority: str                # "P0" | "P1" | "P2" | "P3"
    priority_label: str          # "P0 — BLOCKING" | "P1 — HIGH" | "P2 — MEDIUM" | "P3 — LOW"
    action: str                  # "REMOVE" | "SAFE MIGRATION CANDIDATE" | "REVIEW MIGRATION" | "MANUAL REVIEW" | "KEEP / NO ACTION" | "INVESTIGATE"
    reason: str                  # Main summary reason
    effort: str                  # "Trivial" | "Low" | "Moderate" | "High" | "Complex/Manual"
    expected_benefit: str        # e.g., "Eliminate manifest debt and shrink bundle footprint"
    blocking_factors: List[str]  # e.g., ["Circular import dependency loop", "Low migration confidence"]
    human_review_required: bool  # True | False
    human_review_type: str       # "Not required" | "Recommended" | "Required"
    suggested_sequence: List[str] # Ordered step-by-step instructions
    why: List[str]               # Bullet points explaining why this priority/action was chosen

    def to_dict(self) -> dict:
        return {
            "dependency": self.package,
            "priority": self.priority,
            "priority_label": self.priority_label,
            "action": self.action,
            "reason": self.reason,
            "effort": self.effort,
            "expected_benefit": self.expected_benefit,
            "blocking_factors": self.blocking_factors,
            "human_review_required": self.human_review_required,
            "human_review_type": self.human_review_type,
            "suggested_sequence": self.suggested_sequence,
            "why": self.why,
        }


@dataclass
class ProjectActionPlan:
    project_priority: str                  # Highest priority ("P0", "P1", "P2", "P3")
    items: List[ActionPlanItem]
    summary: Dict[str, int]                # total_items, trivial, low, moderate, high, complex_manual
    candidates_for_stdlib: int
    recommended_retention: int
    next_best_action: str

    def to_dict(self) -> dict:
        return {
            "project_priority": self.project_priority,
            "items": [item.to_dict() for item in self.items],
            "summary": self.summary,
            "candidates_for_stdlib": self.candidates_for_stdlib,
            "recommended_retention": self.recommended_retention,
            "next_best_action": self.next_best_action,
        }


def _determine_priority(
    action: str,
    impact_level: str,
    risk_level: str,
    confidence: str,
    effort: str,
    circular: bool,
    has_dynamic: bool,
) -> Tuple[str, str]:
    """
    Determines priority P0, P1, P2, P3 based on combined evidence.

    Formula:
    - P0 — BLOCKING: Circular dependency loops or structural graph blockers requiring resolution first.
    - P1 — HIGH:
        - UNUSED dependency (action == REMOVE): High cleanup value, trivial effort.
        - HIGH impact + LOW confidence / HIGH risk (manual review required for large blast radius).
        - HIGH risk + REPLACEABLE.
    - P2 — MEDIUM:
        - SAFE MIGRATION CANDIDATE (HIGH confidence replaceable).
        - MEDIUM impact/risk replaceable candidates.
        - Dynamic/Conditional/Star import uncertainty (INVESTIGATE).
    - P3 — LOW:
        - LOW impact + LOW risk replaceable candidates.
        - ESSENTIAL dependencies (KEEP / NO ACTION — retention candidates).
    """
    if circular:
        return "P0", "P0 — BLOCKING"

    if action == "REMOVE":
        return "P1", "P1 — HIGH"

    if action in ("MANUAL REVIEW", "REVIEW MIGRATION") and (impact_level == "HIGH" or risk_level == "HIGH"):
        return "P1", "P1 — HIGH"

    if action == "SAFE MIGRATION CANDIDATE":
        if impact_level == "HIGH":
            return "P1", "P1 — HIGH"
        return "P2", "P2 — MEDIUM"

    if action == "INVESTIGATE" or has_dynamic:
        return "P2", "P2 — MEDIUM"

    if action in ("REVIEW MIGRATION", "MANUAL REVIEW"):
        return "P2", "P2 — MEDIUM"

    if action == "KEEP / NO ACTION":
        return "P3", "P3 — LOW"

    # Default fallback by impact/risk
    if impact_level == "LOW" and risk_level == "LOW":
        return "P3", "P3 — LOW"

    return "P2", "P2 — MEDIUM"


def _generate_suggested_sequence(dep_name: str, action: str, recipe_target: Optional[str]) -> List[str]:
    """Generates deterministic step-by-step migration sequence."""
    if action == "REMOVE":
        return [
            f"Verify non-code references for {dep_name} (config files, plugins, entry points, build scripts).",
            f"Remove {dep_name} from project manifest (requirements.txt / pyproject.toml).",
            "Re-run ZeroDep Doctor to verify clean manifest state.",
        ]
    elif action == "SAFE MIGRATION CANDIDATE":
        target = recipe_target or "stdlib equivalent"
        return [
            f"Replace {dep_name} calls with Python standard library `{target}`.",
            "Run project unit tests to confirm behavioral equivalence.",
            f"Remove {dep_name} from project manifest.",
            "Re-run ZeroDep Doctor to confirm zero remaining references.",
        ]
    elif action in ("REVIEW MIGRATION", "MANUAL REVIEW"):
        target = recipe_target or "stdlib equivalent"
        return [
            f"Audit complex {dep_name} usage sites and API state dependencies.",
            f"Draft manual wrapper using `{target}`.",
            "Run full regression test suite.",
            f"Remove {dep_name} from manifest.",
            "Re-run ZeroDep Doctor.",
        ]
    elif action == "INVESTIGATE":
        return [
            f"Locate dynamic/star import references associated with {dep_name}.",
            "Replace runtime dynamic imports with explicit static imports where possible.",
            "Re-run ZeroDep Doctor analysis.",
        ]
    else:  # KEEP / NO ACTION
        return [
            f"Retain {dep_name} as an essential project dependency.",
            "Periodically monitor stdlib releases for new built-in alternatives.",
        ]


def plan_action_for_dependency(dep_name: str, scan_data: dict) -> ActionPlanItem:
    """Creates a detailed ActionPlanItem for a single dependency."""
    exp = generate_explanation(dep_name, scan_data)
    impact = scan_data.get("impacts", {}).get(dep_name)
    risk = scan_data.get("risk", {}).get(dep_name)
    recipe = get_recipe(dep_name)
    recipe_target = None
    if recipe:
        if hasattr(recipe, "stdlib_module"):
            recipe_target = recipe.stdlib_module
        elif isinstance(recipe, dict):
            recipe_target = recipe.get("stdlib_module") or recipe.get("target_stdlib")

    action = exp.recommendation
    impact_level = exp.impact_level
    risk_level = exp.risk_level
    confidence = exp.confidence
    effort = impact.estimated_migration_effort if impact else "Low"
    circular = impact.circular_dependency_involvement if impact else False
    has_dynamic = impact.has_dynamic_imports if impact else False

    # Priority determination
    priority, priority_label = _determine_priority(
        action, impact_level, risk_level, confidence, effort, circular, has_dynamic
    )

    # Human review rules
    if action == "REMOVE":
        human_review_required = False
        human_review_type = "Source-code review: not required; packaging/configuration verification recommended."
    elif action == "SAFE MIGRATION CANDIDATE":
        human_review_required = True
        human_review_type = "Recommended"
    elif action == "KEEP / NO ACTION":
        human_review_required = False
        human_review_type = "Not required"
    else:
        human_review_required = True
        human_review_type = "Required"

    # Expected benefit
    if action == "REMOVE":
        expected_benefit = "Eliminate manifest debt and shrink dependency footprint with zero code edits"
    elif action in ("SAFE MIGRATION CANDIDATE", "REVIEW MIGRATION", "MANUAL REVIEW"):
        target = recipe_target or "stdlib"
        expected_benefit = f"Eliminate third-party dependency by migrating to built-in `{target}`"
    elif action == "INVESTIGATE":
        expected_benefit = "Improve static analysis visibility and reduce runtime dynamic resolution risks"
    else:
        expected_benefit = "Retain necessary domain package providing specialized functionality"

    # Blocking factors
    blocking_factors = []
    if action == "REMOVE":
        blocking_factors.append("Static AST analysis cannot verify non-code references (config, plugins, entry points)")
    if circular:
        blocking_factors.append("Circular import dependency loop detected in project graph")
    if has_dynamic:
        blocking_factors.append("Dynamic import usage obscures static call sites")
    if confidence == "LOW":
        blocking_factors.append("LOW migration confidence requires manual refactoring of complex API surface")
    if exp.replacement_limitations:
        blocking_factors.extend(exp.replacement_limitations[:2])

    # Why bullet points
    why = list(exp.why_present)
    if exp.reasons:
        why.extend(exp.reasons[:2])

    # Main summary reason
    if action == "REMOVE":
        reason = "Declared in manifest but zero AST references detected (manifest debt)"
    elif recipe and recipe_target:
        reason = f"{confidence} confidence `{recipe_target}` replacement available ({exp.purpose})"
    else:
        reason = exp.purpose

    suggested_sequence = _generate_suggested_sequence(dep_name, action, recipe_target)

    return ActionPlanItem(
        package=dep_name,
        priority=priority,
        priority_label=priority_label,
        action=action,
        reason=reason,
        effort=effort,
        expected_benefit=expected_benefit,
        blocking_factors=blocking_factors,
        human_review_required=human_review_required,
        human_review_type=human_review_type,
        suggested_sequence=suggested_sequence,
        why=why,
    )


def generate_action_plan(scan_data: dict) -> ProjectActionPlan:
    """
    Generates a deterministic ProjectActionPlan across all declared dependencies.
    Sorts items deterministically by priority (P0 -> P1 -> P2 -> P3) and effort.
    """
    declared = sorted(scan_data.get("declared_deps", []))
    items: List[ActionPlanItem] = []

    for dep in declared:
        items.append(plan_action_for_dependency(dep, scan_data))

    # Priority Rank Map
    p_rank = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
    action_rank = {
        "REMOVE": 0,
        "SAFE MIGRATION CANDIDATE": 1,
        "REVIEW MIGRATION": 2,
        "MANUAL REVIEW": 3,
        "INVESTIGATE": 4,
        "KEEP / NO ACTION": 5,
    }

    # Deterministic sorting: priority (P0..P3) -> action rank -> package name
    items.sort(key=lambda x: (p_rank.get(x.priority, 9), action_rank.get(x.action, 9), x.package))

    # Calculate overall project priority
    highest_priority = "P3"
    for item in items:
        if p_rank.get(item.priority, 9) < p_rank.get(highest_priority, 9):
            highest_priority = item.priority

    # Effort aggregation
    summary = {
        "total_items": len(items),
        "trivial": sum(1 for i in items if i.effort == "Trivial"),
        "low": sum(1 for i in items if i.effort == "Low"),
        "moderate": sum(1 for i in items if i.effort == "Moderate"),
        "high": sum(1 for i in items if i.effort == "High"),
        "complex_manual": sum(1 for i in items if i.effort == "Complex/Manual"),
    }

    candidates_for_stdlib = sum(
        1 for i in items if i.action in ("SAFE MIGRATION CANDIDATE", "REVIEW MIGRATION", "MANUAL REVIEW")
    )
    recommended_retention = sum(1 for i in items if i.action == "KEEP / NO ACTION")

    # Next Best Action
    unused_items = [i for i in items if i.action == "REMOVE"]
    safe_items = [i for i in items if i.action == "SAFE MIGRATION CANDIDATE"]
    review_items = [i for i in items if i.action in ("REVIEW MIGRATION", "MANUAL REVIEW")]

    if unused_items:
        next_best_action = f"Remove unused dependency '{unused_items[0].package}' from manifest."
    elif safe_items:
        next_best_action = f"Migrate '{safe_items[0].package}' to standard library equivalent."
    elif review_items:
        next_best_action = f"Review manual migration strategy for '{review_items[0].package}'."
    elif items:
        next_best_action = "No immediate migration actions required. Core dependencies are optimized."
    else:
        next_best_action = "No dependencies detected in project."

    return ProjectActionPlan(
        project_priority=highest_priority,
        items=items,
        summary=summary,
        candidates_for_stdlib=candidates_for_stdlib,
        recommended_retention=recommended_retention,
        next_best_action=next_best_action,
    )

# --- simulation_engine.py ---

from typing import Dict, List, Optional, Set, Tuple

try:
    from zerodep.migration_engine import get_recipe
except ImportError:
    try:
        from migration_engine import get_recipe  # type: ignore
    except ImportError:
        get_recipe = globals().get("get_recipe")  # type: ignore


# Default migration assumptions per recipe package
DEFAULT_ASSUMPTIONS: Dict[str, str] = {
    "click": "CLI command/option usage can be migrated to stdlib `argparse`.",
    "requests": "Simple HTTP GET requests can use `urllib.request`; Session/Auth behavior requires manual implementation.",
    "colorama": "Terminal text formatting can use native ANSI escape codes.",
    "tabulate": "Basic table formatting can use standard string column format specifiers.",
    "networkx": "Supported dependency-graph operations can use the native ZeroDep Graph Engine.",
    "pytz": "Timezone handling can use standard library `zoneinfo` (Python 3.9+).",
    "python-dotenv": "Environment variable loading can use `os.environ` and standard file parsing.",
    "loguru": "Logging calls can use standard library `logging` module.",
    "tomli": "TOML parsing can use standard library `tomllib` (Python 3.11+).",
    "radon": "Code metric calculation can use standard library `ast` node inspection.",
}


@dataclass
class SimulationResult:
    mode: str                                    # "recommended" | "selected" | "all_safe"
    confidence: str                              # "HIGH" | "MEDIUM" | "LOW"
    current_count: int                           # Total declared dependencies before simulation
    projected_count: int                         # Total dependencies remaining after simulation
    reduction_count: int                         # current_count - projected_count
    reduction_percentage: float                  # Percentage reduction (0.0 to 100.0)
    removed: List[str]                           # Packages simulated for removal
    migrated: List[str]                          # Packages simulated for stdlib migration
    manual_review: List[str]                     # Packages requiring manual migration / refactoring
    retained: List[str]                          # Packages retained (essential or unchanged)
    blockers: List[Dict[str, str]]               # List of {"package": dep, "reason": reason}
    assumptions: Dict[str, str]                  # Map of package -> assumption_text
    before_footprint: List[Dict[str, str]]       # List of {"package": dep, "status": status}
    after_footprint: List[Dict[str, str]]        # List of {"package": dep, "simulated_state": state}

    def to_dict(self) -> dict:
        return {
            "mode": self.mode,
            "confidence": self.confidence,
            "current": {
                "dependency_count": self.current_count,
            },
            "projected": {
                "dependency_count": self.projected_count,
            },
            "reduction": {
                "count": self.reduction_count,
                "percentage": self.reduction_percentage,
            },
            "removed": self.removed,
            "migrated": self.migrated,
            "manual_review": self.manual_review,
            "retained": self.retained,
            "blockers": self.blockers,
            "assumptions": self.assumptions,
            "before_footprint": self.before_footprint,
            "after_footprint": self.after_footprint,
        }


def _get_package_assumption(dep_name: str) -> str:
    if dep_name in DEFAULT_ASSUMPTIONS:
        return DEFAULT_ASSUMPTIONS[dep_name]
    recipe = get_recipe(dep_name)
    if recipe:
        target = recipe.stdlib_module if hasattr(recipe, "stdlib_module") else recipe.get("stdlib_module", "stdlib")
        return f"Package usage can be migrated to Python standard library `{target}`."
    return "Hypothetical migration assumes standard library equivalent replaces third-party package API."


def simulate_migration(
    scan_data: dict,
    mode: str = "recommended",
    targets: Optional[Set[str]] = None,
) -> SimulationResult:
    """
    Executes a pure static "What-If" dependency footprint simulation.
    Consumes pre-computed scan_data without re-scanning or modifying target files.
    """
    declared = sorted(scan_data.get("declared_deps", []))
    usage_details = scan_data.get("usage_details", {})
    impacts = scan_data.get("impacts", {})
    risks = scan_data.get("risks", {})
    action_plan = scan_data.get("action_plan")

    action_map = {}
    if action_plan and hasattr(action_plan, "items"):
        for item in action_plan.items:
            action_map[item.package] = item.action

    current_count = len(declared)

    if targets:
        # Handle case-insensitive and duplicate inputs safely
        target_set = {t.lower() for t in targets}
    else:
        target_set = set()

    removed: List[str] = []
    migrated: List[str] = []
    manual_review: List[str] = []
    retained: List[str] = []
    blockers: List[Dict[str, str]] = []
    assumptions: Dict[str, str] = {}
    before_footprint: List[Dict[str, str]] = []
    after_footprint: List[Dict[str, str]] = []

    has_low_conf = False
    has_med_conf = False

    for dep in declared:
        dep_lower = dep.lower()
        info = usage_details.get(dep, {})
        status = info.get("status", "UNKNOWN")
        conf = info.get("confidence", "HIGH")
        imp = impacts.get(dep)
        risk = risks.get(dep)
        rec_action = action_map.get(dep, "KEEP / NO ACTION")

        before_footprint.append({
            "package": dep,
            "status": status,
        })

        # Determine if this dependency is targeted for change in simulation mode
        should_process = False
        if mode == "recommended":
            should_process = rec_action in ("REMOVE", "SAFE MIGRATION CANDIDATE", "REVIEW MIGRATION", "MANUAL REVIEW")
        elif mode in ("selected", "custom"):
            should_process = dep_lower in target_set
        elif mode == "all_safe":
            should_process = status == "UNUSED" or (status == "REPLACEABLE" and conf == "HIGH")

        if should_process:
            if status == "UNUSED" or rec_action == "REMOVE":
                removed.append(dep)
                after_footprint.append({"package": dep, "simulated_state": "REMOVE"})
                assumptions[dep] = "Unused manifest declaration removed; no source code changes required."
            elif status == "REPLACEABLE" and conf == "HIGH" and rec_action == "SAFE MIGRATION CANDIDATE":
                migrated.append(dep)
                after_footprint.append({"package": dep, "simulated_state": "MIGRATE"})
                assumptions[dep] = _get_package_assumption(dep)
            elif rec_action in ("MANUAL REVIEW", "REVIEW MIGRATION") or conf == "LOW":
                manual_review.append(dep)
                retained.append(dep)
                after_footprint.append({"package": dep, "simulated_state": "MANUAL REVIEW"})
                assumptions[dep] = _get_package_assumption(dep) + " Potential migration requires manual refactoring."
                blockers.append({
                    "package": dep,
                    "reason": f"LOW migration confidence / complex API surface ({dep} requires manual refactoring)",
                })
                has_low_conf = True
            elif rec_action == "INVESTIGATE":
                retained.append(dep)
                after_footprint.append({"package": dep, "simulated_state": "INVESTIGATE"})
                blockers.append({
                    "package": dep,
                    "reason": f"Dynamic or star import uncertainty obscures static call sites for {dep}",
                })
                has_low_conf = True
            else:
                migrated.append(dep)
                after_footprint.append({"package": dep, "simulated_state": "MIGRATE"})
                assumptions[dep] = _get_package_assumption(dep)
                if conf == "MEDIUM":
                    has_med_conf = True
        else:
            # Dependency remains untouched / retained in simulation
            retained.append(dep)
            state_label = "KEEP" if status == "ESSENTIAL" or rec_action == "KEEP / NO ACTION" else "UNCHANGED"
            after_footprint.append({"package": dep, "simulated_state": state_label})

    # Project-level blockers check (e.g. dynamic imports or circular loops)
    dynamic_imports = scan_data.get("dynamic_imports", [])
    if dynamic_imports:
        for di in dynamic_imports:
            if di.target_module is None:
                blockers.append({
                    "package": "PROJECT",
                    "reason": f"Dynamic import at {di.file_path}:{di.line_no} with unknown target module",
                })
                has_low_conf = True

    # Calculate exact mathematical reduction
    # Projected count = current - (removed + migrated)
    projected_count = current_count - (len(removed) + len(migrated))
    if projected_count < 0:
        projected_count = 0

    reduction_count = current_count - projected_count
    if current_count > 0:
        reduction_percentage = round((reduction_count / current_count) * 100.0, 2)
    else:
        reduction_percentage = 0.0

    # Calculate simulation confidence
    if has_low_conf:
        sim_confidence = "LOW"
    elif has_med_conf:
        sim_confidence = "MEDIUM"
    else:
        sim_confidence = "HIGH"

    return SimulationResult(
        mode=mode,
        confidence=sim_confidence,
        current_count=current_count,
        projected_count=projected_count,
        reduction_count=reduction_count,
        reduction_percentage=reduction_percentage,
        removed=removed,
        migrated=migrated,
        manual_review=manual_review,
        retained=retained,
        blockers=blockers,
        assumptions=assumptions,
        before_footprint=before_footprint,
        after_footprint=after_footprint,
    )

# --- summary_engine.py ---

from typing import Dict, List, Optional, Set, Tuple


@dataclass
class TopPriorityItem:
    package: str
    impact: str         # "LOW" | "MEDIUM" | "HIGH"
    risk: str           # "LOW" | "MEDIUM" | "HIGH"
    confidence: str     # "HIGH" | "MEDIUM" | "LOW"
    effort: str         # "Trivial" | "Low" | "Moderate" | "High" | "Complex/Manual"
    action: str         # Action plan recommendation label

    def to_dict(self) -> dict:
        return {
            "package": self.package,
            "impact": self.impact,
            "risk": self.risk,
            "confidence": self.confidence,
            "effort": self.effort,
            "action": self.action,
        }


@dataclass
class DecisionSummaryResult:
    total_dependencies: int
    unused_count: int
    replaceable_count: int
    essential_count: int
    high_risk_count: int
    high_impact_count: int
    safe_migration_count: int
    manual_review_count: int
    project_priority: str
    current_dependency_count: int
    projected_dependency_count: int
    potential_reduction_count: int
    potential_reduction_percentage: float
    migration_readiness: str                       # "READY" | "PARTIALLY READY" | "REVIEW REQUIRED" | "BLOCKED"
    readiness_reasons: List[str]
    top_priority_dependencies: List[TopPriorityItem]
    top_risk_dependencies: List[str]
    top_impact_dependencies: List[str]
    blockers: List[str]
    recommended_next_action: str
    decision_matrix: Dict[str, Dict[str, str]]    # 3x3 matrix mapping row (Impact) -> col (Risk) -> Action label
    category_counts: Dict[str, int]               # Status & risk level breakdowns

    def to_dict(self) -> dict:
        return {
            "total_dependencies": self.total_dependencies,
            "unused_count": self.unused_count,
            "replaceable_count": self.replaceable_count,
            "essential_count": self.essential_count,
            "high_risk_count": self.high_risk_count,
            "high_impact_count": self.high_impact_count,
            "safe_migration_count": self.safe_migration_count,
            "manual_review_count": self.manual_review_count,
            "project_priority": self.project_priority,
            "current_dependency_count": self.current_dependency_count,
            "projected_dependency_count": self.projected_dependency_count,
            "potential_reduction_count": self.potential_reduction_count,
            "potential_reduction_percentage": self.potential_reduction_percentage,
            "migration_readiness": {
                "status": self.migration_readiness,
                "reasons": self.readiness_reasons,
            },
            "top_priority_dependencies": [t.to_dict() for t in self.top_priority_dependencies],
            "top_risk_dependencies": self.top_risk_dependencies,
            "top_impact_dependencies": self.top_impact_dependencies,
            "blockers": self.blockers,
            "recommended_next_action": self.recommended_next_action,
            "decision_matrix": self.decision_matrix,
            "category_counts": self.category_counts,
        }


def _get_standard_matrix() -> Dict[str, Dict[str, str]]:
    return {
        "LOW IMPACT": {
            "LOW RISK": "REMOVE",
            "MEDIUM RISK": "REVIEW",
            "HIGH RISK": "INVESTIGATE",
        },
        "MEDIUM IMPACT": {
            "LOW RISK": "MIGRATE",
            "MEDIUM RISK": "REVIEW",
            "HIGH RISK": "MANUAL",
        },
        "HIGH IMPACT": {
            "LOW RISK": "PLAN",
            "MEDIUM RISK": "CAREFUL MIGRATION",
            "HIGH RISK": "BLOCK/MANUAL",
        },
    }


def generate_decision_summary(scan_data: dict) -> DecisionSummaryResult:
    """Generates an executive-level decision summary from pre-computed scan_data."""
    declared = sorted(scan_data.get("declared_deps", []))
    total_deps = len(declared)
    usage_details = scan_data.get("usage_details", {})
    impacts = scan_data.get("impacts", {})
    risks = scan_data.get("risks", {})
    action_plan = scan_data.get("action_plan")
    simulation = scan_data.get("simulation")
    dynamic_imports = scan_data.get("dynamic_imports", [])
    star_imports = scan_data.get("star_imports", [])
    graph = scan_data.get("graph")

    unused_count = 0
    replaceable_count = 0
    essential_count = 0
    high_risk_count = 0
    high_impact_count = 0
    safe_migration_count = 0
    manual_review_count = 0

    top_risk_deps = []
    top_impact_deps = []

    # Helper maps
    action_map = {}
    effort_map = {}
    if action_plan and hasattr(action_plan, "items"):
        for item in action_plan.items:
            action_map[item.package] = item.action
            effort_map[item.package] = item.effort

    for dep in declared:
        info = usage_details.get(dep, {})
        status = info.get("status", "UNKNOWN")
        conf = info.get("confidence", "HIGH")
        
        imp = impacts.get(dep)
        risk = risks.get(dep)

        imp_level = imp.level if imp else "LOW"
        risk_level = risk.level if risk else "LOW"
        rec_action = action_map.get(dep, risk.recommendation if risk else "KEEP / NO ACTION")

        if status == "UNUSED":
            unused_count += 1
        elif status == "REPLACEABLE":
            replaceable_count += 1
        elif status == "ESSENTIAL":
            essential_count += 1

        if risk_level == "HIGH":
            high_risk_count += 1
            top_risk_deps.append(dep)
        if imp_level == "HIGH":
            high_impact_count += 1
            top_impact_deps.append(dep)

        if rec_action in ("SAFE MIGRATION CANDIDATE", "MIGRATE"):
            safe_migration_count += 1
        elif rec_action in ("MANUAL REVIEW", "MANUAL"):
            manual_review_count += 1

    # Project Priority
    project_priority = "P3"
    if action_plan and hasattr(action_plan, "project_priority"):
        project_priority = action_plan.project_priority

    # Simulation metrics
    if simulation:
        current_dep_count = simulation.current_count
        projected_dep_count = simulation.projected_count
        potential_reduction_count = simulation.reduction_count
        potential_reduction_pct = simulation.reduction_percentage
    else:
        current_dep_count = total_deps
        projected_dep_count = total_deps - (unused_count + safe_migration_count)
        if projected_dep_count < 0:
            projected_dep_count = 0
        potential_reduction_count = current_dep_count - projected_dep_count
        potential_reduction_pct = round((potential_reduction_count / current_dep_count * 100.0), 2) if current_dep_count > 0 else 0.0

    # Aggregated Blockers & Uncertainties
    blockers: List[str] = []
    
    # Check dynamic imports
    for di in dynamic_imports:
        target = di.target_module or "unknown target"
        blockers.append(f"Dynamic import at {di.file_path}:{di.line_no} ({target})")

    # Check star imports
    for si in star_imports:
        blockers.append(f"Star import detected in module '{si}' reduces symbol certainty")

    # Check graph circular dependencies
    if graph and hasattr(graph, "find_cycles"):
        cycles = graph.find_cycles()
        if cycles:
            blockers.append(f"Circular dependency detected involving {len(cycles)} loop(s)")

    # Check package-level blockers from action plan or risks
    if action_plan and hasattr(action_plan, "items"):
        for item in action_plan.items:
            for bf in item.blocking_factors:
                msg = f"{item.package}: {bf}"
                if msg not in blockers:
                    blockers.append(msg)

    # Migration Readiness
    readiness_reasons: List[str] = []
    if safe_migration_count > 0:
        readiness_reasons.append(f"{safe_migration_count} high-confidence migration candidate(s) detected")
    if unused_count > 0:
        readiness_reasons.append(f"{unused_count} unused dependencies can be removed after manifest verification")
    if manual_review_count > 0:
        readiness_reasons.append(f"{manual_review_count} complex dependency/dependencies require manual review")
    if high_risk_count > 0:
        readiness_reasons.append(f"{high_risk_count} high-risk dependency/dependencies identified")
    if any("Dynamic import" in b for b in blockers):
        readiness_reasons.append("Dynamic imports introduce static analysis uncertainty")
    if any("Circular dependency" in b for b in blockers):
        readiness_reasons.append("Circular module dependencies present architectural risk")

    if total_deps == 0:
        migration_readiness = "READY"
        readiness_reasons = ["Project has zero declared runtime dependencies."]
    elif project_priority == "P0" or any("Circular dependency" in b for b in blockers):
        migration_readiness = "BLOCKED"
    elif manual_review_count > 0 or high_risk_count > 0 or any("Dynamic import" in b for b in blockers):
        migration_readiness = "PARTIALLY READY"
    elif safe_migration_count > 0 or unused_count > 0:
        migration_readiness = "READY"
    else:
        migration_readiness = "REVIEW REQUIRED"

    if not readiness_reasons:
        readiness_reasons = ["No immediate migration blockers identified."]

    # Deterministic Top Priority Items
    # Sort order:
    # 1. Action priority (P0 < P1 < P2 < P3)
    # 2. Impact (HIGH < MEDIUM < LOW)
    # 3. Risk (HIGH < MEDIUM < LOW)
    # 4. Confidence (LOW < MEDIUM < HIGH)
    # 5. Call-site count (descending)
    # 6. Package name (alphabetical)
    prio_order = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
    lvl_order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    conf_order = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}

    priority_candidates = []
    for dep in declared:
        info = usage_details.get(dep, {})
        imp = impacts.get(dep)
        risk = risks.get(dep)
        rec_action = action_map.get(dep, risk.recommendation if risk else "KEEP / NO ACTION")
        eff = effort_map.get(dep, "Low")

        imp_lbl = imp.level if imp else "LOW"
        risk_lbl = risk.level if risk else "LOW"
        conf_lbl = info.get("confidence", "HIGH")
        occ = info.get("occurrences", 0)

        # Priority from action plan or fallback
        item_prio = "P3"
        if action_plan and hasattr(action_plan, "items"):
            for api in action_plan.items:
                if api.package == dep:
                    item_prio = api.priority
                    break

        priority_candidates.append({
            "package": dep,
            "impact": imp_lbl,
            "risk": risk_lbl,
            "confidence": conf_lbl,
            "effort": eff,
            "action": rec_action,
            "prio_val": prio_order.get(item_prio, 3),
            "imp_val": lvl_order.get(imp_lbl, 2),
            "risk_val": lvl_order.get(risk_lbl, 2),
            "conf_val": conf_order.get(conf_lbl, 2),
            "occ_val": occ,
        })

    priority_candidates.sort(
        key=lambda x: (
            x["prio_val"],
            x["imp_val"],
            x["risk_val"],
            x["conf_val"],
            -x["occ_val"],
            x["package"].lower(),
        )
    )

    top_priority_deps = [
        TopPriorityItem(
            package=c["package"],
            impact=c["impact"],
            risk=c["risk"],
            confidence=c["confidence"],
            effort=c["effort"],
            action=c["action"],
        )
        for c in priority_candidates[:5]
    ]

    # Recommended next action
    if action_plan and hasattr(action_plan, "next_best_action") and action_plan.next_best_action:
        next_action = action_plan.next_best_action
    elif unused_count > 0:
        next_action = "Remove unused declared dependencies after verifying packaging/configuration references."
    elif safe_migration_count > 0:
        next_action = "Begin standard library migration for high-confidence replaceable dependencies."
    else:
        next_action = "Maintain current architecture; no immediate migration required."

    category_counts = {
        "total": total_deps,
        "unused": unused_count,
        "replaceable": replaceable_count,
        "essential": essential_count,
        "high_risk": high_risk_count,
        "medium_risk": sum(1 for d in declared if risks.get(d) and risks[d].level == "MEDIUM"),
        "low_risk": sum(1 for d in declared if risks.get(d) and risks[d].level == "LOW"),
        "high_impact": high_impact_count,
        "medium_impact": sum(1 for d in declared if impacts.get(d) and impacts[d].level == "MEDIUM"),
        "low_impact": sum(1 for d in declared if impacts.get(d) and impacts[d].level == "LOW"),
    }

    return DecisionSummaryResult(
        total_dependencies=total_deps,
        unused_count=unused_count,
        replaceable_count=replaceable_count,
        essential_count=essential_count,
        high_risk_count=high_risk_count,
        high_impact_count=high_impact_count,
        safe_migration_count=safe_migration_count,
        manual_review_count=manual_review_count,
        project_priority=project_priority,
        current_dependency_count=current_dep_count,
        projected_dependency_count=projected_dep_count,
        potential_reduction_count=potential_reduction_count,
        potential_reduction_percentage=potential_reduction_pct,
        migration_readiness=migration_readiness,
        readiness_reasons=readiness_reasons,
        top_priority_dependencies=top_priority_deps,
        top_risk_dependencies=top_risk_deps,
        top_impact_dependencies=top_impact_deps,
        blockers=blockers,
        recommended_next_action=next_action,
        decision_matrix=_get_standard_matrix(),
        category_counts=category_counts,
    )

# --- cli.py ---

from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple


try:
    from zerodep.action_planner import generate_action_plan
except ImportError:
    try:
        from action_planner import generate_action_plan  # type: ignore
    except ImportError:
        generate_action_plan = globals().get("generate_action_plan")  # type: ignore

try:
    from zerodep.simulation_engine import simulate_migration
except ImportError:
    try:
        from simulation_engine import simulate_migration  # type: ignore
    except ImportError:
        simulate_migration = globals().get("simulate_migration")  # type: ignore

try:
    from zerodep.summary_engine import generate_decision_summary
except ImportError:
    try:
        from summary_engine import generate_decision_summary  # type: ignore
    except ImportError:
        generate_decision_summary = globals().get("generate_decision_summary")  # type: ignore

# ──────────────────────────────────────────────
# Directories to skip during scanning
# ──────────────────────────────────────────────
_SKIP_DIRS: frozenset = frozenset({
    ".git", ".hg", ".svn", ".tox", ".mypy_cache", ".pytest_cache",
    ".ruff_cache", "__pycache__", "venv", "env", ".venv", ".env",
    "build", "dist", "node_modules", ".idea", ".vscode", "site-packages",
    "eggs", "*.egg-info",
})


def _should_skip(path: Path, root: Path) -> bool:
    """Return True if any path component indicates a directory to ignore."""
    try:
        parts = path.relative_to(root).parts
    except ValueError:
        return True
    return any(
        p in _SKIP_DIRS or p.startswith(".") or p.endswith(".egg-info")
        for p in parts
    )


# ──────────────────────────────────────────────
# Core scanner
# ──────────────────────────────────────────────

def scan_project(target_dir: Path) -> dict:
    """
    Scan a Python project directory and return a rich analysis result dict.
    Never executes any analyzed code.
    """
    start_time = time.perf_counter()
    manifest_result = parse_manifests(target_dir)

    ast_results: List[ASTAnalysisResult] = []
    total_loc = 0
    total_stdlib_calls = 0
    total_calls = 0
    graph = ZeroDepGraph()

    # ── File discovery + AST analysis ──────────────────────
    parse_errors: List[str] = []
    for py_file in target_dir.rglob("*.py"):
        if _should_skip(py_file, target_dir):
            continue
        # Safety: skip symlinks that escape the project root
        try:
            resolved_file = py_file.resolve()
            resolved_file.relative_to(target_dir.resolve())
        except (ValueError, OSError):
            continue

        try:
            raw_bytes = py_file.read_bytes()
            # Strip UTF-8 BOM (U+FEFF) if present — prevents SyntaxError
            if raw_bytes.startswith(b"\xef\xbb\xbf"):
                raw_bytes = raw_bytes[3:]
            content = raw_bytes.decode("utf-8", errors="ignore")
        except (OSError, PermissionError):
            continue

        total_loc += content.count("\n") + 1
        rel_path = str(py_file.relative_to(target_dir))
        res = analyze_ast(rel_path, content)
        if res.has_syntax_error:
            parse_errors.append(f"{rel_path}: {res.error_message}")
        ast_results.append(res)

    # Pre-index file stems and imports/call sites in O(N)
    imports_by_mod: Dict[str, List[Tuple[str, ImportBinding]]] = defaultdict(list)
    call_sites_by_mod: Dict[str, List[CallSite]] = defaultdict(list)
    stem_to_path: Dict[str, str] = {}

    for res in ast_results:
        stem_to_path[Path(res.file_path).stem] = res.file_path
        for imp in res.imports:
            mod_base = imp.original_module.split(".")[0].lower()
            imports_by_mod[mod_base].append((res.file_path, imp))
            if imp.is_stdlib:
                total_stdlib_calls += 1
            total_calls += 1
        for cs in res.call_sites:
            mod_base = cs.original_module.split(".")[0].lower()
            call_sites_by_mod[mod_base].append(cs)

    # Build graph edges in O(N) using stem index
    for res in ast_results:
        for imp in res.imports:
            mod_base = imp.original_module
            if mod_base in stem_to_path:
                graph.add_edge(res.file_path, stem_to_path[mod_base])
            else:
                graph.add_edge(res.file_path, mod_base)

    # ── Check for Graphify structural output (graph.json) ──────
    graphify_candidates = [
        target_dir / "graphify-out" / "graph.json",
        target_dir / "graphify.json",
        target_dir / ".graphify" / "graph.json",
    ]
    graphify_detected = False
    graphify_edges_added = 0
    for g_path in graphify_candidates:
        if g_path.exists():
            try:
                with open(g_path, "r", encoding="utf-8") as gf:
                    g_data = json.load(gf)
                graphify_edges_added = graph.integrate_graphify_data(g_data)
                graphify_detected = True
                break
            except Exception:
                pass

    # ── Match used packages to declared dependencies ────────
    decl_by_import: Dict[str, ManifestDependency] = {}
    decl_by_pypi: Dict[str, ManifestDependency] = {}
    for dep in manifest_result.dependencies:
        decl_by_import[dep.import_name.lower()] = dep
        decl_by_pypi[dep.name.lower()] = dep

    runtime_declared: Set[str] = {
        d.name for d in manifest_result.dependencies if d.scope == "runtime"
    }
    all_declared: Set[str] = {d.name for d in manifest_result.dependencies}
    manifest_found: bool = bool(manifest_result.manifest_files_found)

    used_deps: Set[str] = set()
    # Imports that don't match any declared dependency (potential undeclared third-party)
    unresolved_imports: Set[str] = set()
    for res in ast_results:
        for imp in res.imports:
            if imp.is_stdlib or imp.is_relative:
                continue
            mod_base = imp.original_module.split(".")[0].lower()
            if mod_base in decl_by_import:
                used_deps.add(decl_by_import[mod_base].name)
            elif mod_base in decl_by_pypi:
                used_deps.add(decl_by_pypi[mod_base].name)
            else:
                # Not in stdlib, not in declared deps — could be undeclared third-party
                # or a local module. We record for scoring but cannot be certain.
                unresolved_imports.add(mod_base)

    replaceable_set: Set[str] = set(RECIPES.keys())

    # Build detailed per-dependency usage dictionary in O(D)
    dep_usage_details: Dict[str, dict] = {}
    for dep in sorted(all_declared):
        dep_imports = []
        dep_call_sites = []

        match_import_names = set()
        for d in manifest_result.dependencies:
            if d.name.lower() == dep.lower():
                match_import_names.add(d.import_name.lower())
                match_import_names.add(d.name.lower())

        for name in match_import_names:
            dep_imports.extend(imports_by_mod.get(name, []))
            dep_call_sites.extend(call_sites_by_mod.get(name, []))

        files_used = sorted({cs.file_path for cs in dep_call_sites} | {path for path, _ in dep_imports})
        symbol_counts: Dict[str, int] = {}
        for cs in dep_call_sites:
            symbol_counts[cs.resolved_symbol] = symbol_counts.get(cs.resolved_symbol, 0) + 1

        status = "UNUSED"
        if dep in used_deps:
            status = "REPLACEABLE" if dep in replaceable_set else "ESSENTIAL"
        elif dep_imports:
            status = "IMPORTED_BUT_UNUSED"

        # Compute explainable confidence & evidence
        evidence = []
        if dep_imports:
            has_direct = any(imp.scope_kind == "module" and not imp.is_conditional for _, imp in dep_imports)
            has_cond = any(imp.is_conditional or imp.scope_kind == "conditional" for _, imp in dep_imports)
            has_nested = any(imp.scope_kind in ("function", "class") for _, imp in dep_imports)
            has_star = any(imp.is_star for _, imp in dep_imports)
            
            if has_direct:
                evidence.append("[OK] Direct static import at module level")
            if has_cond:
                evidence.append("[!] Conditional / try-except import guard")
            if has_nested:
                evidence.append("[!] Scope-nested import inside function or class")
            if has_star:
                evidence.append("[!] Star import (from pkg import *) detected")

        if dep_call_sites:
            evidence.append(f"[OK] {len(dep_call_sites)} static call site reference(s)")
            symbols_list = list(symbol_counts.keys())
            if len(symbols_list) == 1:
                evidence.append(f"[OK] Isolated symbol usage ({symbols_list[0]})")
            else:
                evidence.append(f"[OK] Multi-symbol API usage ({len(symbols_list)} distinct functions)")
        else:
            evidence.append("[X] 0 call site references detected in codebase AST")

        # Known CLI runner / runtime entrypoint packages that might not be imported directly in source
        _CLI_RUNNERS = {"uvicorn", "gunicorn", "celery", "waitress", "daphne", "hypercorn"}
        if status in ("UNUSED", "IMPORTED_BUT_UNUSED") and dep.lower() in _CLI_RUNNERS:
            evidence.append("[!] Notice: Package is often invoked as a CLI runner/service, not directly imported in code")
            confidence = "MEDIUM"

        # Confidence rating
        if status in ("UNUSED", "IMPORTED_BUT_UNUSED"):
            confidence = "HIGH" if not any(imp.is_star for _, imp in dep_imports) else "MEDIUM"
        elif any(imp.is_conditional or imp.is_star for _, imp in dep_imports):
            confidence = "MEDIUM"
        elif any(cs.confidence_label == "LOW" for cs in dep_call_sites):
            confidence = "LOW"
        elif dep_call_sites:
            confidence = "HIGH"
        else:
            confidence = "UNKNOWN"

        # For REPLACEABLE deps, let the migration recipe override confidence
        # (e.g. requests.Session => LOW even though static call sites are HIGH)
        if status == "REPLACEABLE":
            recipe = get_recipe(dep, symbol_counts)
            if recipe:
                confidence = recipe.confidence

        dep_usage_details[dep] = {
            "status": status,
            "confidence": confidence,
            "evidence": evidence,
            "files": files_used,
            "occurrences": len(dep_call_sites),
            "symbols": symbol_counts,
            "imports_count": len(dep_imports),
        }

    # Narrow unresolved imports to likely third-party (not local module names)
    # by filtering out names that appear as file stems or top-level package dirs in this project
    local_stems: Set[str] = set()
    for r in ast_results:
        p = Path(r.file_path)
        local_stems.add(p.stem.lower())
        for part in p.parts[:-1]:
            local_stems.add(part.lower())
    undeclared_third_party: Set[str] = {
        m for m in unresolved_imports if m not in local_stems
    }

    score_info = calculate_score(
        declared_deps=runtime_declared if runtime_declared else all_declared,
        used_deps=used_deps,
        replaceable_deps=replaceable_set,
        total_stdlib_calls=total_stdlib_calls,
        total_calls=total_calls,
        undeclared_third_party_deps=undeclared_third_party,
        files_scanned=len(ast_results),
        manifest_found=manifest_found,
    )

    duration = time.perf_counter() - start_time

    scan_data = {
        "target_dir": str(target_dir),
        "files_scanned": len(ast_results),
        "loc": total_loc,
        "duration": duration,
        "manifest_dependencies": manifest_result.dependencies,
        "manifest_files_found": manifest_result.manifest_files_found,
        "manifest_found": manifest_found,
        "declared_deps": runtime_declared if runtime_declared else all_declared,
        "used_deps": used_deps,
        "unresolved_imports": unresolved_imports,
        "undeclared_third_party": undeclared_third_party,
        "parse_errors": parse_errors,
        "usage_details": dep_usage_details,
        "ast_results": ast_results,
        "score_info": score_info,
        "graph": graph,
        "graphify_detected": graphify_detected,
        "graphify_edges_added": graphify_edges_added,
        "dynamic_imports": [
            di for res in ast_results for di in res.dynamic_imports
        ],
        "star_imports": [
            mod for res in ast_results for mod in res.star_imports
        ],
    }

    # Compute Dependency Impact Analysis per declared dependency
    impacts = {}
    for dep in sorted(scan_data["declared_deps"]):
        impacts[dep] = calculate_dependency_impact(dep, scan_data)
    scan_data["impacts"] = impacts

    # Compute Dependency Risk Model per declared dependency
    risks = {}
    for dep in sorted(scan_data["declared_deps"]):
        risks[dep] = calculate_dependency_risk(dep, scan_data)
    scan_data["risks"] = risks

    # Generate Dependency Explanations (Phase 3)
    scan_data["explanations"] = generate_all_explanations(scan_data)

    # Generate Dependency Action Plan (Phase 4)
    if generate_action_plan:
        scan_data["action_plan"] = generate_action_plan(scan_data)

    # Generate Dependency Migration Simulation (Phase 5)
    if simulate_migration:
        scan_data["simulation"] = simulate_migration(scan_data, mode="recommended")

    # Generate Dependency Decision Summary (Phase 6)
    if generate_decision_summary:
        scan_data["summary"] = generate_decision_summary(scan_data)

    return scan_data


# ──────────────────────────────────────────────
# Formatters
# ──────────────────────────────────────────────

def _print_analyze(scan: dict, fmt: ANSIFormatter) -> None:
    print(fmt.header("ZERODEP DOCTOR - AUDIT REPORT"))
    print(f"Target Path:    {scan['target_dir']}")
    print(f"Files Scanned:  {scan['files_scanned']} | Lines of Code: {scan['loc']}")
    print(f"Execution Time: {scan['duration']:.2f}s (Pure Stdlib Engine)")

    _print_score(scan, fmt)
    _print_dependencies(scan, fmt)
    _print_risk_matrix(scan, fmt)
    _print_action_plan(scan, fmt)
    _print_warnings(scan, fmt)
    _print_migrations(scan, fmt)
    _print_graph(scan, fmt)
    print("=" * 80)


def _print_score(scan: dict, fmt: ANSIFormatter) -> None:
    s = scan["score_info"]
    print(fmt.subheader("ZERODEP HEALTH INDEX"))
    print(f" Current Score:   {fmt.bold(str(s.current_score))} / 100  [GRADE: {fmt.green(s.grade)}]")
    print(f" Potential Score: {fmt.bold(str(s.potential_score))} / 100 (if all suggestions applied)")
    print("\n Dependency Breakdown:")
    print(f"   Unused:        {s.unused_count:<3} (-{s.unused_penalty:.1f} pts penalty -> Action: Remove from manifest)")
    print(f"   Replaceable:   {s.replaceable_count:<3} (-{s.replaceable_penalty:.1f} pts penalty -> Action: Migrate to stdlib)")
    print(f"   Essential:     {s.essential_count:<3} (-{s.essential_penalty:.1f} pts penalty -> Action: Retain external pkg)")
    print(f"   Stdlib Bonus:  +{s.stdlib_bonus:.1f} pts (ratio of stdlib calls in project AST)")
    print("\n Potential Improvement Opportunities:")
    print(f"   Remove unused:       +{s.unused_penalty:.1f} pts potential score recovery")
    print(f"   Migrate replaceable: +{s.replaceable_penalty:.1f} pts potential score recovery")


def _print_dependencies(scan: dict, fmt: ANSIFormatter, verbose: bool = False) -> None:
    print(fmt.subheader("DEPENDENCY ANALYSIS & CONFIDENCE MODEL"))
    details = scan.get("usage_details", {})
    explanations = scan.get("explanations", {})
    if not scan["declared_deps"]:
        print("  No runtime dependencies declared in manifests.")
        return

    for dep in sorted(scan["declared_deps"]):
        info = details.get(dep, {})
        status = info.get("status", "UNKNOWN")
        conf = info.get("confidence", "HIGH")
        conf_color = fmt.green if conf == "HIGH" else (fmt.yellow if conf == "MEDIUM" else fmt.red)
        
        exp = explanations.get(dep)

        if status == "UNUSED":
            print(f"\n {fmt.red('[UNUSED]')}            {fmt.bold(dep)}  [Confidence: {conf_color(conf)}]")
            print(f"   Purpose:      {exp.purpose if exp else 'No active usage detected'}")
            print("   Status:       Declared in manifest, 0 AST references (safe to remove)")
        elif status == "IMPORTED_BUT_UNUSED":
            print(f"\n {fmt.yellow('[IMPORTED_UNUSED]')}   {fmt.bold(dep)}  [Confidence: {conf_color(conf)}]")
            print(f"   Purpose:      {exp.purpose if exp else 'Import statement present but 0 call sites'}")
            print("   Status:       Import statement present, but 0 active function/class call sites")
            print(f"   Files:        {', '.join(info.get('files', []))}")
        elif status == "REPLACEABLE":
            recipe = get_recipe(dep, info.get("symbols"))
            suggested = recipe.stdlib_module if recipe else "Standard Library"
            eff_conf = recipe.confidence if recipe else conf
            eff_color = fmt.green if eff_conf == "HIGH" else (fmt.yellow if eff_conf == "MEDIUM" else fmt.red)
            print(f"\n {fmt.yellow('[REPLACEABLE]')}      {fmt.bold(dep)}  [Confidence: {eff_color(eff_conf)}]")
            print(f"   Purpose:      {exp.purpose if exp else 'Active AST call sites'}")
            print(f"   Status:       Active AST call sites -> Migrate to {fmt.cyan(suggested)}")
            if recipe:
                print(f"   Reason:       {recipe.why_replaceable}")
            print(f"   Files:        {', '.join(info.get('files', []))}")
            print(f"   Occurrences:  {info.get('occurrences', 0)}")
            if info.get("symbols"):
                sym_str = ", ".join(f"{s} ({c})" for s, c in info["symbols"].items())
                print(f"   Symbols Used: {sym_str}")
        else: # ESSENTIAL
            print(f"\n {fmt.green('[ESSENTIAL]')}        {fmt.bold(dep)}  [Confidence: {conf_color(conf)}]")
            print(f"   Purpose:      {exp.purpose if exp else 'Necessary external dependency'}")
            print("   Status:       Necessary external dependency (no stdlib equivalent)")
            print(f"   Files:        {', '.join(info.get('files', []))}")
            print(f"   Occurrences:  {info.get('occurrences', 0)}")
            if info.get("symbols"):
                sym_str = ", ".join(f"{s} ({c})" for s, c in info["symbols"].items())
                print(f"   Symbols Used: {sym_str}")

        if info.get("evidence"):
            print("   Evidence:")
            for item in info["evidence"]:
                print(f"     {item}")

        # Display Impact Analysis
        impacts = scan.get("impacts", {})
        if dep in impacts:
            imp = impacts[dep]
            imp_color = fmt.green if imp.level == "LOW" else (fmt.yellow if imp.level == "MEDIUM" else fmt.red)
            print(f"   Impact Level: {imp_color(imp.level)} (Effort: {imp.estimated_migration_effort})")

        # Display Risk Analysis & Explanation Summary
        risks = scan.get("risks", {})
        if dep in risks:
            risk = risks[dep]
            risk_color = fmt.green if risk.level == "LOW" else (fmt.yellow if risk.level == "MEDIUM" else fmt.red)
            rec_color = fmt.green if risk.recommendation in ("REMOVE", "SAFE MIGRATION CANDIDATE") else (
                fmt.yellow if risk.recommendation in ("REVIEW MIGRATION", "KEEP / NO ACTION") else fmt.red
            )
            print(f"   Risk Level:   {risk_color(risk.level)} (Score: {risk.score}/100)")
            print(f"   Recommended:  {rec_color(risk.recommendation)}")
            if risk.risk_factors:
                print("   Risk Factors:")
                for rf in risk.risk_factors:
                    sev_color = fmt.red if rf.severity == "HIGH" else (fmt.yellow if rf.severity == "MED" else fmt.green)
                    print(f"     [{sev_color(rf.severity)}] {rf.description}")
            if risk.warnings:
                for w in risk.warnings:
                    print(f"   {fmt.yellow('[WARN]')} {w}")

        if exp and (verbose or exp.reasons):
            print("   Why:")
            for r in exp.reasons:
                print(f"     • {r}")
            if verbose and exp.replacement_limitations:
                print("   Limitations:")
                for lim in exp.replacement_limitations:
                    print(f"     ! {lim}")


def _print_risk_matrix(scan: dict, fmt: ANSIFormatter) -> None:
    """Print the 3x3 Risk vs Impact matrix populated with actual dependencies."""
    impacts = scan.get("impacts", {})
    risks = scan.get("risks", {})
    if not impacts or not risks:
        return

    matrix = build_risk_impact_matrix(impacts, risks)
    print(fmt.subheader("DEPENDENCY RISK vs IMPACT MATRIX"))
    levels = ["LOW", "MEDIUM", "HIGH"]
    col_w = 22
    # Header
    print(f"  {'':20}  {'IMPACT':<{col_w}}  {'IMPACT':<{col_w}}  {'IMPACT':<{col_w}}")
    print(f"  {'':20}  {'LOW':<{col_w}}  {'MEDIUM':<{col_w}}  {'HIGH':<{col_w}}")
    print(f"  {'-'*20}  {'-'*col_w}  {'-'*col_w}  {'-'*col_w}")
    for r_lvl in levels:
        r_color = fmt.green if r_lvl == "LOW" else (fmt.yellow if r_lvl == "MEDIUM" else fmt.red)
        row_label = f"Risk {r_lvl}"
        cells = []
        for i_lvl in levels:
            names = matrix[r_lvl][i_lvl]
            cell_str = ", ".join(names) if names else "(none)"
            if len(cell_str) > col_w:
                cell_str = cell_str[:col_w - 3] + "..."
            cells.append(f"{cell_str:<{col_w}}")
        print(f"  {r_color(row_label):<28}  {'  '.join(cells)}")
    print()


def _print_warnings(scan: dict, fmt: ANSIFormatter) -> None:
    dynamic = scan.get("dynamic_imports", [])
    stars = scan.get("star_imports", [])
    if not dynamic and not stars:
        return
    print(fmt.subheader("ANALYSIS WARNINGS"))
    if dynamic:
        print(f" {fmt.yellow('[DYNAMIC IMPORTS]')} {len(dynamic)} dynamic import(s) detected.")
        print("   These cannot be resolved statically. Manual audit required.")
        for di in dynamic[:5]:
            print(f"     {di.file_path}:{di.line_no}  ({di.kind})")
    if stars:
        unique_stars = sorted(set(stars))
        print(f" {fmt.yellow('[STAR IMPORTS]')} from {', '.join(unique_stars)} import *")
        print("   Star imports hide exact symbol usage. Dependency usage may be underreported.")


def _print_action_plan(scan: dict, fmt: ANSIFormatter) -> None:
    action_plan = scan.get("action_plan")
    if not action_plan:
        return

    print(fmt.subheader("DEPENDENCY ACTION PLAN  [PRIORITIZED MIGRATION SEQUENCE]"))
    items = action_plan.items
    if not items:
        print("  No dependencies detected in project.")
        print()
        return

    # Group items by priority label
    by_priority: Dict[str, list] = defaultdict(list)
    for item in items:
        by_priority[item.priority_label].append(item)

    for p_label in ["P0 — BLOCKING", "P1 — HIGH", "P2 — MEDIUM", "P3 — LOW"]:
        group = by_priority.get(p_label, [])
        p_color = fmt.red if "P0" in p_label or "P1" in p_label else (fmt.yellow if "P2" in p_label else fmt.green)
        print(f" {p_color(p_label)}")
        if not group:
            print("   (none)")
        else:
            for idx, item in enumerate(group, 1):
                act_color = fmt.red if item.action in ("REMOVE", "MANUAL REVIEW") else (
                    fmt.yellow if item.action in ("REVIEW MIGRATION", "INVESTIGATE") else fmt.green
                )
                print(f"   {idx}. {fmt.bold(item.package)}  [{act_color(item.action)}]")
                print(f"      Reason: {item.reason}")
                print(f"      Effort: {item.effort} | Human Review: {item.human_review_type}")
                if item.blocking_factors:
                    print(f"      Blockers: {', '.join(item.blocking_factors)}")

    print(f"\n {fmt.bold('NEXT BEST ACTION:')}")
    print(f"   {fmt.green(action_plan.next_best_action)}")
    print()


def _print_simulation(sim, fmt: ANSIFormatter) -> None:
    if not sim:
        return
    print(fmt.subheader("DEPENDENCY MIGRATION SIMULATION  ['WHAT-IF' HYPOTHETICAL ANALYSIS]"))
    print(f" Mode:                   {fmt.bold(sim.mode.upper())}")
    conf_color = fmt.green if sim.confidence == "HIGH" else (fmt.yellow if sim.confidence == "MEDIUM" else fmt.red)
    print(f" Simulation Confidence:  {conf_color(sim.confidence)}")
    print(f" Current Dependencies:   {sim.current_count}")
    print(f" Projected Dependencies: {fmt.bold(str(sim.projected_count))}")
    print(f" Potential Reduction:    {fmt.green(f'{sim.reduction_count} / {sim.current_count} ({sim.reduction_percentage:.1f}%)')}")
    print()

    print(fmt.subheader("BEFORE / AFTER FOOTPRINT"))
    print(f"  {'BEFORE':<25}  {'AFTER (SIMULATED)':<25}")
    print(f"  {'-'*25}  {'-'*25}")

    after_map = {item["package"]: item["simulated_state"] for item in sim.after_footprint}
    for item in sim.before_footprint:
        pkg = item["package"]
        st = item["status"]
        after_st = after_map.get(pkg, "UNCHANGED")

        before_str = f"{pkg} [{st}]"
        after_color = fmt.green if after_st in ("REMOVE", "MIGRATE") else (fmt.yellow if after_st in ("MANUAL REVIEW", "INVESTIGATE") else fmt.bold)
        after_str = f"{pkg} [{after_st}]"
        print(f"  {before_str:<25}  {after_color(after_str):<25}")
    print()

    if sim.assumptions:
        print(fmt.bold("  MIGRATION ASSUMPTIONS (HYPOTHETICAL):"))
        for pkg, asm in sim.assumptions.items():
            print(f"    • {fmt.bold(pkg)}: {asm}")
        print()

    if sim.blockers:
        print(fmt.bold(fmt.yellow("  REMAINING BLOCKERS / UNCERTAINTIES:")))
        for blk in sim.blockers:
            print(f"    ! {blk['package']}: {blk['reason']}")
        print()


def _print_summary(summary, fmt: ANSIFormatter) -> None:
    if not summary:
        return
    print(fmt.subheader("DEPENDENCY PORTFOLIO"))
    print(f"Total Dependencies:       {summary.total_dependencies}")
    print(f"Unused:                   {summary.unused_count}")
    print(f"Replaceable:              {summary.replaceable_count}")
    print(f"Essential:                {summary.essential_count}")
    print(f"High Risk:                {summary.high_risk_count}")
    print(f"High Impact:              {summary.high_impact_count}")
    print()

    print(fmt.subheader("MIGRATION READINESS"))
    readiness_color = fmt.green if summary.migration_readiness == "READY" else (
        fmt.yellow if summary.migration_readiness in ("PARTIALLY READY", "REVIEW REQUIRED") else fmt.red
    )
    print(f"Status: {readiness_color(summary.migration_readiness)}")
    print()
    if summary.readiness_reasons:
        print("Reasons:")
        for r in summary.readiness_reasons:
            print(f"  • {r}")
    print()

    print(fmt.subheader("TOP PRIORITIES"))
    if not summary.top_priority_dependencies:
        print("  (none)")
    else:
        for idx, item in enumerate(summary.top_priority_dependencies, 1):
            act_color = fmt.red if item.action in ("REMOVE", "MANUAL REVIEW") else (
                fmt.yellow if item.action in ("REVIEW MIGRATION", "INVESTIGATE") else fmt.green
            )
            print(f"{idx}. {fmt.bold(item.package)}")
            print(f"   Impact: {item.impact}")
            print(f"   Risk: {item.risk}")
            print(f"   Confidence: {item.confidence}")
            print(f"   Effort: {item.effort}")
            print(f"   Action: {act_color(item.action)}")
            print()

    print(fmt.subheader("WHAT-IF SUMMARY"))
    print(f"Current Dependencies:     {summary.current_dependency_count}")
    print(f"Projected Dependencies:   {fmt.bold(str(summary.projected_dependency_count))}")
    print(f"Potential Reduction:      {fmt.green(f'{summary.potential_reduction_count} / {summary.current_dependency_count} ({summary.potential_reduction_percentage:.2f}%)')}")
    print()

    print(fmt.subheader("DECISION MATRIX"))
    print("                     LOW RISK        MEDIUM RISK       HIGH RISK")
    matrix = summary.decision_matrix
    for imp_lvl in ["LOW IMPACT", "MEDIUM IMPACT", "HIGH IMPACT"]:
        row = matrix.get(imp_lvl, {})
        low_act = row.get("LOW RISK", "")
        med_act = row.get("MEDIUM RISK", "")
        high_act = row.get("HIGH RISK", "")
        print(f"{imp_lvl:<21}{low_act:<16}{med_act:<18}{high_act}")
    print()

    if summary.blockers:
        print(fmt.subheader("BLOCKERS / UNCERTAINTIES"))
        for blk in summary.blockers:
            print(fmt.yellow(f"! {blk}"))
        print()

    print(fmt.subheader("NEXT BEST ACTION"))
    print(fmt.green(summary.recommended_next_action))
    print()


def _print_migrations(scan: dict, fmt: ANSIFormatter) -> None:
    print(fmt.subheader("SUGGESTED MIGRATION DIFFS  [REVIEWABLE SUGGESTIONS]"))
    found = False
    details = scan.get("usage_details", {})
    for dep in sorted(scan["used_deps"]):
        info = details.get(dep, {})
        recipe = get_recipe(dep, info.get("symbols"))
        if recipe:
            found = True
            conf_color = fmt.green if recipe.confidence == "HIGH" else (fmt.yellow if recipe.confidence == "MEDIUM" else fmt.red)
            print(f"\n  Package:      {fmt.bold(recipe.package_name)}")
            print(f"  Target Stdlib:{fmt.cyan(recipe.stdlib_module)}")
            print(f"  Confidence:   {conf_color(recipe.confidence)}")
            print(f"  Reason:       {recipe.why_replaceable}")
            print(f"  Limitations:  {recipe.limitations}")
            print("\n  REVIEWABLE DIFF SUGGESTION (NOT AUTOMATICALLY APPLIED):")
            print(fmt.red(recipe.before_code))
            print(fmt.green(recipe.after_code))
            print()
    if not found:
        print("  No replaceable package call sites detected.")


def _print_graph(scan: dict, fmt: ANSIFormatter) -> None:
    g: ZeroDepGraph = scan["graph"]
    print(fmt.subheader("GRAPH ANALYSIS  (ZeroDep Graph Engine — NetworkX replacement)"))
    cycles = g.find_cycles()
    if cycles:
        print(f" {fmt.red('[CIRCULAR IMPORTS DETECTED]')} {len(cycles)} cycle(s)")
        for cyc in cycles[:5]:
            formatted_cycle = " -> ".join(cyc)
            print(f"   Circular Dependency:\n     {formatted_cycle}")
    else:
        print(f" {fmt.green('[OK]')} Circular Imports: NONE DETECTED")

    topo = g.topological_sort()
    print(f" Topological Sort:        {'OK — valid DAG' if topo else fmt.yellow('N/A — cycles present')}")
    print(f" Total Module Graph Nodes:{len(g.nodes())}")
    
    depth = g.max_dependency_depth()
    if depth > 0:
        print(f" Max Dependency Depth:    {depth}")
    
    comps = g.weakly_connected_components()
    print(f" Connected Components:   {len(comps)}")

    hubs = g.top_hub_nodes(3)
    if hubs and any(c > 0 for _, c in hubs):
        print(" Most Connected Hub Modules:")
        for node, count in hubs:
            if count > 0:
                print(f"   {node:<25} ({count} dependents)")


def _json_report(scan: dict) -> dict:
    """Build a deterministic, schema-compliant JSON report."""
    s = scan["score_info"]
    g: ZeroDepGraph = scan["graph"]
    details = scan.get("usage_details", {})
    unused = scan["declared_deps"] - scan["used_deps"]

    migrations = []
    for dep in sorted(scan["used_deps"]):
        info = details.get(dep, {})
        recipe = get_recipe(dep, info.get("symbols"))
        if recipe:
            migrations.append({
                "package": recipe.package_name,
                "stdlib_module": recipe.stdlib_module,
                "confidence": recipe.confidence,
                "reason": recipe.why_replaceable,
                "limitations": recipe.limitations,
                "before_code": recipe.before_code,
                "after_code": recipe.after_code,
            })

    usage_list = []
    for dep in sorted(scan["declared_deps"]):
        info = details.get(dep, {})
        usage_list.append({
            "package": dep,
            "status": info.get("status", "UNKNOWN"),
            "confidence": info.get("confidence", "UNKNOWN"),
            "occurrences": info.get("occurrences", 0),
            "files": info.get("files", []),
            "symbols": info.get("symbols", {}),
            "evidence": info.get("evidence", []),
        })

    impact_dict = {}
    impacts = scan.get("impacts", {})
    for dep, imp in impacts.items():
        impact_dict[dep] = imp.to_dict()

    risk_dict = {}
    risks = scan.get("risks", {})
    for dep, risk in risks.items():
        risk_dict[dep] = risk.to_dict()

    explanation_dict = {}
    explanations = scan.get("explanations", {})
    for dep, exp in explanations.items():
        explanation_dict[dep] = exp.to_dict()

    return {
        "zerodep_doctor": "1.0",
        "project": {
            "path": scan["target_dir"],
            "files_scanned": scan["files_scanned"],
            "lines_of_code": scan["loc"],
            "execution_seconds": round(scan["duration"], 4),
        },
        "dependencies": {
            "declared": sorted(scan["declared_deps"]),
            "used": sorted(scan["used_deps"]),
            "unused": sorted(unused),
            "replaceable": sorted(scan["used_deps"] & set(RECIPES.keys())),
        },
        "impact": impact_dict,
        "risk": risk_dict,
        "explanations": explanation_dict,
        "action_plan": scan["action_plan"].to_dict() if scan.get("action_plan") else {},
        "simulation": scan["simulation"].to_dict() if scan.get("simulation") else {},
        "summary": scan["summary"].to_dict() if scan.get("summary") else {},
        "usage": usage_list,
        "migrations": migrations,
        "graph": {
            "nodes": len(g.nodes()),
            "edges": g.edge_count(),
            "cycles_count": len(g.find_cycles()),
            "max_dependency_depth": g.max_dependency_depth(),
            "connected_components": len(g.weakly_connected_components()),
            "top_hubs": [{"module": m, "dependents": c} for m, c in g.top_hub_nodes(5) if c > 0],
        },
        "score": {
            "current": s.current_score,
            "potential": s.potential_score,
            "grade": s.grade,
            "breakdown": {
                "unused_penalty": s.unused_penalty,
                "replaceable_penalty": s.replaceable_penalty,
                "essential_penalty": s.essential_penalty,
                "stdlib_bonus": s.stdlib_bonus,
            },
            "potential_improvements": {
                "remove_unused_bonus": s.unused_penalty,
                "migrate_replaceable_bonus": s.replaceable_penalty,
            },
        },
        "warnings": {
            "dynamic_imports": [
                {
                    "file": di.file_path,
                    "line": di.line_no,
                    "kind": di.kind,
                    "target_module": di.target_module,
                }
                for di in scan.get("dynamic_imports", [])
            ],
            "star_imports": sorted(set(scan.get("star_imports", []))),
        },
    }


# ──────────────────────────────────────────────
# CLI entry point
# ──────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        prog="zerodep",
        description="ZeroDep Doctor — zero-dependency static analyzer & stdlib migration assistant",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Commands:\n"
            "  analyze      Full audit report\n"
            "  dependencies Dependency analysis only\n"
            "  graph        Dependency graph + cycle detection\n"
            "  replacements Migration suggestions only\n"
            "  plan         Prioritized action plan\n"
            "  simulate     'What-If' dependency migration simulator\n"
            "  summary      Executive dependency decision summary\n"
            "  report       Machine-readable JSON output\n\n"
            "Example:\n"
            "  zerodep analyze  ./my_project\n"
            "  zerodep plan     ./my_project\n"
            "  zerodep summary  ./my_project\n"
        ),
    )
    parser.add_argument(
        "command",
        choices=["analyze", "dependencies", "graph", "replacements", "plan", "simulate", "summary", "report"],
    )
    parser.add_argument("path", nargs="?", default=".", help="Target project path")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument("--verbose", "-v", action="store_true", help="Show detailed explanation evidence & limitations")
    parser.add_argument("--recommended", action="store_true", help="Simulate recommended Action Planner steps")
    parser.add_argument("--all-safe", action="store_true", help="Simulate all safe removals and high-confidence replacements")
    parser.add_argument("--targets", nargs="*", help="List specific dependencies to simulate removing/migrating")

    args = parser.parse_args()
    target_path = Path(args.path).resolve()

    if not target_path.exists():
        print(f"Error: path not found: {target_path}", file=sys.stderr)
        sys.exit(1)
    if not target_path.is_dir():
        print(f"Error: expected a directory, got: {target_path}", file=sys.stderr)
        sys.exit(1)

    scan = scan_project(target_path)
    fmt = ANSIFormatter()

    cmd = args.command

    # JSON mode (any command + --json, or report command)
    if args.json or cmd == "report":
        print(json.dumps(_json_report(scan), indent=2, sort_keys=False))
        sys.exit(0)

    if cmd == "analyze":
        _print_analyze(scan, fmt)
    elif cmd == "dependencies":
        _print_dependencies(scan, fmt, verbose=args.verbose)
    elif cmd == "graph":
        _print_graph(scan, fmt)
    elif cmd == "replacements":
        _print_migrations(scan, fmt)
    elif cmd == "plan":
        print(fmt.header("ZERODEP DOCTOR — MIGRATION ACTION PLAN"))
        _print_action_plan(scan, fmt)
    elif cmd == "simulate":
        print(fmt.header("ZERODEP DOCTOR — DEPENDENCY MIGRATION SIMULATOR"))
        mode = "recommended"
        targets = None
        if args.all_safe:
            mode = "all_safe"
        elif args.targets:
            mode = "selected"
            targets = set(args.targets)

        if simulate_migration:
            sim = simulate_migration(scan, mode=mode, targets=targets)
            _print_simulation(sim, fmt)
    elif cmd == "summary":
        print(fmt.header("ZERODEP DOCTOR — DECISION SUMMARY"))
        if generate_decision_summary:
            summary = scan.get("summary") or generate_decision_summary(scan)
            _print_summary(summary, fmt)

    sys.exit(0)



# === ENTRY POINT ===
if __name__ == '__main__':
    main()