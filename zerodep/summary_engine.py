"""Dependency Decision Summary Engine — Executive-level unified decision view.

Turns all previous analysis (Impact, Risk, Explanation, Action Plan, What-If Simulation, Graph)
into a single concise executive decision summary.

100% deterministic using Python Standard Library only.
Does NOT rescan files or execute target code.
"""

from dataclasses import dataclass, field
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
