"""Dependency Explanation Engine — Generates concise, evidence-backed explanations.

Answers: "Why is this dependency in my project, what exactly is it doing,
can I replace it, and what should I do next?"

100% deterministic using Python Standard Library only.
Consumes pre-computed AST, graph, recipe, impact, and risk evidence without re-scanning.
Never executes code. Never uses external AI services.
"""

from dataclasses import dataclass, field
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
