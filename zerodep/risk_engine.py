"""Dependency Risk Engine — Calculates explainable risk levels, scores, and recommendations.

Answers: "How much uncertainty, migration difficulty, or maintenance attention does this dependency deserve?"

Dependency Impact (blast radius) != Dependency Risk (uncertainty / difficulty / attention).

Zero third-party dependencies. Standard library only.
"""

from dataclasses import dataclass, field
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
