"""Diagnostic: reproduce the failure for each test path."""
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from zerodep.cli import scan_project
from zerodep.manifest_parser import parse_manifests

TEST_PATHS = [
    ("demo (relative)", Path("demo")),
    ("spaceguard (absolute)", Path(r"C:\Users\diksh\OneDrive\Desktop\My_Project\spaceguard")),
    ("OOPs Game (space in name)", Path(r"C:\Users\diksh\OneDrive\Desktop\My_Project\OOPs Game\Game 2 - Copy")),
    ("lightweight-container (optional)", Path(r"C:\Users\diksh\OneDrive\Desktop\My_Project\Mini Docker Using AI\lightweight-container")),
    # Mini_Project path known not to exist
]

for label, raw_path in TEST_PATHS:
    print("=" * 70)
    print(f"PATH: {label}")
    print(f"  raw:      {raw_path}")

    # Is it absolute? Resolve relative to CWD
    if raw_path.is_absolute():
        resolved = raw_path.resolve()
    else:
        resolved = (Path.cwd() / raw_path).resolve()

    print(f"  resolved: {resolved}")
    print(f"  exists:   {resolved.exists()}")
    print(f"  is_dir:   {resolved.is_dir() if resolved.exists() else 'N/A'}")

    if not resolved.exists() or not resolved.is_dir():
        print("  [SKIP] Path does not exist or is not a directory")
        print()
        continue

    # File discovery
    py_files = []
    parse_errors = []
    for pf in resolved.rglob("*.py"):
        # Check skip logic
        parts = set(pf.relative_to(resolved).parts)
        skip_dirs = {".git", "__pycache__", "venv", ".venv", "env", ".env",
                     "build", "dist", "node_modules", ".tox", ".pytest_cache",
                     "site-packages", "eggs"}
        if any(p in skip_dirs or p.startswith(".") or p.endswith(".egg-info") for p in parts):
            continue
        py_files.append(pf)
        # Try to parse
        try:
            content = pf.read_text(encoding="utf-8", errors="ignore")
            import ast
            ast.parse(content)
        except SyntaxError as e:
            parse_errors.append(f"{pf.relative_to(resolved)}: SyntaxError at line {e.lineno}")
        except Exception as e:
            parse_errors.append(f"{pf.relative_to(resolved)}: {type(e).__name__}: {e}")

    print(f"  py_files_discovered: {len(py_files)}")
    if parse_errors:
        print(f"  parse_errors ({len(parse_errors)}):")
        for e in parse_errors[:5]:
            print(f"    {e}")
    else:
        print(f"  parse_errors: 0")

    # Manifest detection
    mr = parse_manifests(resolved)
    print(f"  manifest_files_found: {mr.manifest_files_found}")
    print(f"  declared_deps ({len(mr.dependencies)}): {[d.name for d in mr.dependencies[:10]]}")
    if mr.errors:
        print(f"  manifest_errors: {mr.errors}")

    # Full scan
    try:
        result = scan_project(resolved)
        si = result["score_info"]
        print(f"  files_scanned: {result['files_scanned']}")
        print(f"  loc: {result['loc']}")
        print(f"  declared_deps: {sorted(result['declared_deps'])}")
        print(f"  used_deps: {sorted(result['used_deps'])}")
        unused = result['declared_deps'] - result['used_deps']
        print(f"  unused_deps: {sorted(unused)}")
        print(f"  score: {si.current_score}  grade: {si.grade}  status: {si.audit_status}")
    except Exception:
        print(f"  SCAN ERROR:")
        traceback.print_exc()

    print()
