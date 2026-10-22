"""ZeroDep Health Index Scoring Engine (Explainable Current vs Potential Score)."""

from dataclasses import dataclass
from typing import List, Optional, Set

@dataclass
class ScoreBreakdown:
    current_score: float
    potential_score: float
    grade: str
    unused_count: int
    replaceable_count: int
    essential_count: int
    unused_penalty: float
    replaceable_penalty: float
    essential_penalty: float
    stdlib_bonus: float
    undeclared_count: int = 0
    undeclared_penalty: float = 0.0
    audit_status: str = "ASSESSED"  # "ASSESSED" | "GENUINE_ZERO_DEP" | "UNABLE_TO_ASSESS" | "UNDECLARED_DEPS_FOUND"
    status_message: str = ""

def calculate_grade(score: float, audit_status: str = "ASSESSED") -> str:
    if audit_status == "UNABLE_TO_ASSESS":
        return "N/A"
    if score >= 90.0:
        return "A+"
    elif score >= 80.0:
        return "A"
    elif score >= 70.0:
        return "B"
    elif score >= 60.0:
        return "C"
    elif score >= 50.0:
        return "D"
    else:
        return "F"

def calculate_score(
    declared_deps: Set[str],
    used_deps: Set[str],
    replaceable_deps: Set[str],
    total_stdlib_calls: int,
    total_calls: int,
    undeclared_third_party_deps: Optional[Set[str]] = None,
    files_scanned: int = -1,
    manifest_found: bool = True,
) -> ScoreBreakdown:
    undeclared = undeclared_third_party_deps or set()
    undeclared_count = len(undeclared)

    # 1. Edge case: Incomplete audit / no files scanned
    # files_scanned == -1 means "not provided by caller" (legacy callers / unit tests)
    if files_scanned == 0:
        return ScoreBreakdown(
            current_score=0.0,
            potential_score=0.0,
            grade="N/A",
            unused_count=0,
            replaceable_count=0,
            essential_count=0,
            unused_penalty=0.0,
            replaceable_penalty=0.0,
            essential_penalty=0.0,
            stdlib_bonus=0.0,
            undeclared_count=0,
            undeclared_penalty=0.0,
            audit_status="UNABLE_TO_ASSESS",
            status_message="No supported Python source files found in target path.",
        )

    # 2. Edge case: No manifest found, but third-party imports detected in source
    if not manifest_found and not declared_deps and undeclared_count > 0:
        # Penalize for undeclared dependencies
        undeclared_penalty = undeclared_count * 10.0
        raw_current = max(0.0, 100.0 - undeclared_penalty)
        return ScoreBreakdown(
            current_score=round(raw_current, 1),
            potential_score=round(raw_current, 1),
            grade=calculate_grade(raw_current),
            unused_count=0,
            replaceable_count=0,
            essential_count=undeclared_count,
            unused_penalty=0.0,
            replaceable_penalty=0.0,
            essential_penalty=undeclared_penalty,
            stdlib_bonus=0.0,
            undeclared_count=undeclared_count,
            undeclared_penalty=undeclared_penalty,
            audit_status="UNDECLARED_DEPS_FOUND",
            status_message=f"No manifest found; {undeclared_count} unpinned third-party dependencies detected in source.",
        )

    # 3. Edge case: Genuine zero-dependency project (manifest empty or absent, 0 third-party imports, stdlib only)
    if not declared_deps and undeclared_count == 0:
        return ScoreBreakdown(
            current_score=100.0,
            potential_score=100.0,
            grade="A+",
            unused_count=0,
            replaceable_count=0,
            essential_count=0,
            unused_penalty=0.0,
            replaceable_penalty=0.0,
            essential_penalty=0.0,
            stdlib_bonus=10.0,
            undeclared_count=0,
            undeclared_penalty=0.0,
            audit_status="GENUINE_ZERO_DEP",
            status_message="Pure Python standard library project. 0 external dependencies detected.",
        )

    # 4. Standard calculation for declared dependencies
    unused_deps = declared_deps - used_deps
    active_deps = declared_deps & used_deps
    
    rep_active = active_deps & replaceable_deps
    essential_active = active_deps - replaceable_deps

    unused_count = len(unused_deps)
    replaceable_count = len(rep_active)
    essential_count = len(essential_active)

    unused_penalty = unused_count * 15.0
    replaceable_penalty = replaceable_count * 8.0
    essential_penalty = essential_count * 4.0
    undeclared_penalty = undeclared_count * 6.0

    stdlib_ratio = (total_stdlib_calls / total_calls) if total_calls > 0 else 1.0
    stdlib_bonus = round(stdlib_ratio * 10.0, 1)

    raw_current = 100.0 - (unused_penalty + replaceable_penalty + essential_penalty + undeclared_penalty) + stdlib_bonus
    current_score = max(0.0, min(100.0, raw_current))

    raw_potential = 100.0 - essential_penalty + stdlib_bonus
    potential_score = max(0.0, min(100.0, raw_potential))

    return ScoreBreakdown(
        current_score=round(current_score, 1),
        potential_score=round(potential_score, 1),
        grade=calculate_grade(current_score),
        unused_count=unused_count,
        replaceable_count=replaceable_count,
        essential_count=essential_count,
        unused_penalty=unused_penalty,
        replaceable_penalty=replaceable_penalty,
        essential_penalty=essential_penalty,
        stdlib_bonus=stdlib_bonus,
        undeclared_count=undeclared_count,
        undeclared_penalty=undeclared_penalty,
        audit_status="ASSESSED",
        status_message="Project dependencies and health calculated from manifest and AST evidence.",
    )

