"""Manifest parser -- reads pyproject.toml, requirements.txt.

Handles:
  - Version specifiers (==, >=, <=, ~=, !=, <, >)
  - Extras (requests[security])
  - Inline comments (requests  # needed for auth)
  - Environment markers (package ; python_version >= "3.10")
  - Blank lines
  - Package-name -> import-symbol normalization
  - dev / test / optional-dependencies classification

Zero third-party runtime dependencies.
"""

import re
import sys
from dataclasses import dataclass, field
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
    "pillow":               "PIL",
    "python-dotenv":        "dotenv",
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
    # Uncertain — keep but mark clearly
    # "msgpack-python": "msgpack",  # upstream renamed to just "msgpack"
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
    """Discover and parse all manifest files in root_path."""
    result = ManifestParseResult()
    seen: Dict[str, ManifestDependency] = {}  # dedup by (name, scope)

    def _add(dep: ManifestDependency) -> None:
        key = f"{dep.name}::{dep.scope}"
        if key not in seen:
            seen[key] = dep

    # 1. pyproject.toml
    for dep in parse_pyproject_toml(root_path / "pyproject.toml"):
        _add(dep)

    # 2. requirements.txt (runtime)
    for dep in parse_requirements_txt(root_path / "requirements.txt", scope="runtime"):
        _add(dep)

    # 3. Dev/test requirements
    for pattern in _DEV_REQ_PATTERNS:
        scope = "test" if "test" in pattern else "dev"
        for dep in parse_requirements_txt(root_path / pattern, scope=scope):
            _add(dep)

    result.dependencies = list(seen.values())
    return result
