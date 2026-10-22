"""Diff generation and reviewable patch engine for ZeroDep migrations.

Generates unified diffs comparing original source files against proposed stdlib replacements,
without modifying any project file until explicitly approved.
"""

import difflib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from zerodep.migration_engine import RECIPES, get_recipe


@dataclass
class FileDiff:
    file_path: str
    original_content: str
    proposed_content: str
    diff_patch: str
    can_apply: bool
    reason: str


@dataclass
class MigrationProposal:
    package: str
    confidence: str
    can_auto_apply: bool
    summary: str
    limitations: str
    file_diffs: List[FileDiff]
    recipe_before: str
    recipe_after: str

    def to_dict(self) -> dict:
        return {
            "package": self.package,
            "confidence": self.confidence,
            "can_auto_apply": self.can_auto_apply,
            "summary": self.summary,
            "limitations": self.limitations,
            "recipe_before": self.recipe_before,
            "recipe_after": self.recipe_after,
            "file_diffs": [
                {
                    "file_path": d.file_path,
                    "proposed_content": d.proposed_content,
                    "diff_patch": d.diff_patch,
                    "can_apply": d.can_apply,
                    "reason": d.reason,
                }
                for d in self.file_diffs
            ],
        }


def generate_migration_proposal(
    target_dir: Path,
    dep_name: str,
    scan_data: dict,
) -> MigrationProposal:
    """Generate a reviewable unified diff proposal for a target dependency.
    
    Rejects unsupported, low-confidence, or non-deterministic recipes instead of inventing code.
    """
    usage_info = scan_data.get("usage_details", {}).get(dep_name, {})
    status = usage_info.get("status", "UNKNOWN")
    confidence = usage_info.get("confidence", "UNKNOWN")
    files = usage_info.get("files", [])
    symbols = usage_info.get("symbols", {})

    recipe = get_recipe(dep_name, symbols)
    if not recipe:
        return MigrationProposal(
            package=dep_name,
            confidence="UNKNOWN",
            can_auto_apply=False,
            summary=f"No stdlib migration recipe available for '{dep_name}'.",
            limitations="Package requires custom manual refactoring or is an essential external library.",
            file_diffs=[],
            recipe_before="",
            recipe_after="",
        )

    # For LOW confidence or complex API usage, generate preview-only diff with warning
    is_safe = (confidence == "HIGH") and (status == "REPLACEABLE")

    file_diffs: List[FileDiff] = []
    for rel_path in files:
        file_path = target_dir / rel_path
        if not file_path.exists() or not file_path.is_file():
            continue

        orig_code = file_path.read_text(encoding="utf-8", errors="replace")
        new_code, applied, reason = _transform_code(dep_name, orig_code, symbols, confidence)

        diff = "".join(
            difflib.unified_diff(
                orig_code.splitlines(keepends=True),
                new_code.splitlines(keepends=True),
                fromfile=f"a/{rel_path}",
                tofile=f"b/{rel_path}",
            )
        )

        file_diffs.append(
            FileDiff(
                file_path=rel_path,
                original_content=orig_code,
                proposed_content=new_code,
                diff_patch=diff,
                can_apply=applied and is_safe,
                reason=reason,
            )
        )

    can_auto_apply = is_safe and any(d.can_apply for d in file_diffs)
    summary = recipe.why_replaceable
    if not can_auto_apply and confidence == "LOW":
        summary += " [NOTICE: Low confidence due to complex API usage — review diff carefully before applying.]"

    return MigrationProposal(
        package=dep_name,
        confidence=confidence,
        can_auto_apply=can_auto_apply,
        summary=summary,
        limitations=recipe.limitations,
        file_diffs=file_diffs,
        recipe_before=recipe.before_code,
        recipe_after=recipe.after_code,
    )


def _transform_code(
    dep_name: str,
    code: str,
    symbols: Dict[str, int],
    confidence: str,
) -> Tuple[str, bool, str]:
    """Pattern-based stdlib codemod for supported recipes."""
    new_code = code

    if dep_name == "tabulate":
        # Check if tabulate is simply imported
        if "from tabulate import tabulate" in new_code or "import tabulate" in new_code:
            # Replace import with helper or comment
            new_code = re.sub(
                r"^(\s*)from tabulate import tabulate",
                r"\1# ZeroDep: tabulate replaced with stdlib format\n\1# Use string formatting: f'{val:<10}'",
                new_code,
                flags=re.MULTILINE,
            )
            new_code = re.sub(
                r"^(\s*)import tabulate",
                r"\1# ZeroDep: tabulate removed",
                new_code,
                flags=re.MULTILINE,
            )
            return new_code, True, "Migrated tabulate import to stdlib formatting comments"

    elif dep_name == "colorama":
        if "import colorama" in new_code or "from colorama import" in new_code:
            new_code = re.sub(
                r"^(\s*)(import colorama.*|from colorama import.*)$",
                r"\1# ZeroDep: colorama replaced with native ANSI\n\1RED, RESET = '\033[31m', '\033[0m'",
                new_code,
                flags=re.MULTILINE,
            )
            new_code = re.sub(r"colorama\.init\(\)", "# colorama.init() (native ANSI)", new_code)
            return new_code, True, "Replaced colorama import with native ANSI constants"

    elif dep_name == "requests":
        if confidence == "LOW":
            # Don't auto-patch complex Session or Auth usage — generate illustrative diff comment
            new_code = "# ZeroDep Migration Notice: requests has complex Session or Auth usages.\n" + new_code
            return new_code, False, "Low confidence: complex requests API usage requires manual migration."
        else:
            # Simple GET replacement illustration
            if "import requests as req" in new_code:
                new_code = new_code.replace("import requests as req", "import urllib.request as req_net\nimport json")
                new_code = re.sub(
                    r"req\.get\((.*?)\)\.json\(\)",
                    r"json.loads(req_net.urlopen(req_net.Request(\1)).read().decode('utf-8'))",
                    new_code,
                )
                return new_code, True, "Replaced simple req.get().json() with urllib.request + json"

    elif dep_name == "click":
        # Suggest argparse migration
        new_code = re.sub(
            r"^(\s*)import click",
            r"\1import argparse  # ZeroDep: migrated from click",
            new_code,
            flags=re.MULTILINE,
        )
        return new_code, True, "Replaced click import with argparse template"

    elif dep_name == "networkx":
        new_code = re.sub(
            r"^(\s*)import networkx as nx",
            r"\1from zerodep.graph_engine import ZeroDepGraph as nx  # ZeroDep Graph drop-in",
            new_code,
            flags=re.MULTILINE,
        )
        return new_code, True, "Replaced networkx with ZeroDep Graph Engine"

    return new_code, False, f"Automatic patch generator for '{dep_name}' not available; review recipe."
