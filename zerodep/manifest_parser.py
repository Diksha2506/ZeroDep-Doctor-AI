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

