"""Single-file distribution packager for ZeroDep Doctor (+5 Bonus Target).

Strips per-module __main__ guards to prevent double execution in the bundle,
and strips redundant stdlib import lines already covered by the preamble.
All embedded module docstrings are stripped to avoid Unicode/syntax leakage.
"""

from pathlib import Path
import re

# Modules to embed in order (dependencies first)
MODULE_ORDER = [
    "manifest_parser.py",
    "ast_visitor.py",
    "graph_engine.py",
    "migration_engine.py",
    "scoring_engine.py",
    "cli_formatter.py",
    "impact_engine.py",
    "risk_engine.py",
    "explanation_engine.py",
    "action_planner.py",
    "simulation_engine.py",
    "summary_engine.py",
    "cli.py",
]

# Top-level preamble imports that we can strip from embedded modules
PREAMBLE_IMPORTS = {
    "import sys", "import os", "import ast", "import re",
    "import argparse", "import json", "import time", "import shutil",
    "import pathlib", "import heapq",
    "from collections import defaultdict, deque",
    "from dataclasses import dataclass, field",
    "from typing import Dict, List, Set, Tuple, Optional",
}

# Pattern to remove zerodep-relative imports (handled by embedding)
ZERODEP_IMPORT_RE = re.compile(r"^from zerodep\.\w+ import .+$")

# Pattern to strip inline per-module if __name__ == "__main__" blocks
MAIN_GUARD_RE = re.compile(r'^\s*if __name__\s*==\s*["\']__main__["\']\s*:\s*$')


def _strip_top_docstring(lines):
    """Remove a leading triple-quoted docstring from a list of lines."""
    if not lines:
        return lines
    first = lines[0].strip()
    # Single-line triple-quoted docstring: """..."""
    if first.startswith('"""') and first.endswith('"""') and len(first) > 6:
        return lines[1:]
    # Multi-line triple-quoted docstring
    if first.startswith('"""'):
        end = 1
        while end < len(lines):
            if '"""' in lines[end]:
                return lines[end + 1:]
            end += 1
    return lines


def strip_module(content: str, mod_name: str) -> str:
    """Remove headers, redundant imports, and per-module __main__ guards."""
    lines = content.splitlines()
    # Strip leading module docstring (may be multi-line)
    lines = _strip_top_docstring(lines)

    out = []
    skip_main_block = False
    main_indent = None

    for line in lines:
        # Strip relative zerodep package imports
        if ZERODEP_IMPORT_RE.match(line):
            continue

        # Strip preamble stdlib imports already in bundle header
        stripped = line.strip()
        if stripped in PREAMBLE_IMPORTS:
            continue

        # Detect and skip the per-module `if __name__ == "__main__":` block
        if MAIN_GUARD_RE.match(line):
            skip_main_block = True
            main_indent = len(line) - len(line.lstrip())
            continue

        if skip_main_block:
            current_indent = len(line) - len(line.lstrip()) if line.strip() else main_indent + 1
            if line.strip() == "" or current_indent > main_indent:
                continue  # still inside the guarded block
            else:
                skip_main_block = False  # block ended

        out.append(line)

    return "\n".join(out)


def bundle():
    zerodep_dir = Path(__file__).parent / "zerodep"
    output_file = Path(__file__).parent / "dist" / "zerodep.py"
    output_file.parent.mkdir(parents=True, exist_ok=True)

    sections = [
        "#!/usr/bin/env python3",
        '"""ZeroDep Doctor — Single-File Executable Distribution (Pure Stdlib)."""',
        "# Zero third-party runtime dependencies. Python 3.11+ required.",
        "",
        "import sys, os, ast, re, argparse, json, time, shutil, pathlib, heapq",
        "from collections import defaultdict, deque",
        "from dataclasses import dataclass, field",
        "from typing import Dict, List, Set, Tuple, Optional",
        "",
        "if sys.version_info < (3, 11):",
        "    print('ZeroDep Doctor requires Python 3.11+', file=sys.stderr)",
        "    sys.exit(1)",
        "",
        "# === EMBEDDED MODULES ===",
    ]

    for mod_name in MODULE_ORDER:
        mod_path = zerodep_dir / mod_name
        if not mod_path.exists():
            print(f"  WARNING: Missing module {mod_name}")
            continue
        raw = mod_path.read_text(encoding="utf-8")
        cleaned = strip_module(raw, mod_name)
        sections.append(f"\n# --- {mod_name} ---")
        sections.append(cleaned)

    sections.append("\n# === ENTRY POINT ===")
    sections.append("if __name__ == '__main__':\n    main()")

    output_file.write_text("\n".join(sections), encoding="utf-8")
    size_kb = output_file.stat().st_size / 1024
    print(f"Bundle OK -> {output_file}  ({size_kb:.1f} KB)")


if __name__ == "__main__":
    bundle()
