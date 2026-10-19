"""ZeroDep Health Index Scoring Engine (Explainable Current vs Potential Score)."""

from dataclasses import dataclass
from typing import List, Set

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

def calculate_grade(score: float) -> str:
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
    total_calls: int
) -> ScoreBreakdown:
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

    stdlib_ratio = (total_stdlib_calls / total_calls) if total_calls > 0 else 1.0
    stdlib_bonus = round(stdlib_ratio * 10.0, 1)

    raw_current = 100.0 - (unused_penalty + replaceable_penalty + essential_penalty) + stdlib_bonus
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
        stdlib_bonus=stdlib_bonus
    )
