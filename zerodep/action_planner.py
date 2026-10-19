"""Dependency Action Planner — Converts impact, risk, explanation, and graph evidence into a prioritized migration plan.

Answers: "What should I actually do about this dependency, in what order, and with what level of human review?"

100% deterministic using Python Standard Library only.
Does NOT modify user source files automatically.
Consumes pre-computed AST, graph, impact, risk, and explanation results without re-scanning.
"""

from dataclasses import dataclass, field
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
