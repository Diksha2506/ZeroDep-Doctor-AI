"""Dependency Migration Simulator — "What If?" Hypothetical Analysis Engine.

Answers: "What would happen to my project's dependency footprint if I removed or migrated selected dependencies?"

100% deterministic using Python Standard Library only.
Pure hypothetical simulation — NEVER modifies user source code, manifests, or project files.
Consumes pre-computed AST, graph, impact, risk, explanation, and action plan results without re-scanning.
"""

from dataclasses import dataclass, field
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
