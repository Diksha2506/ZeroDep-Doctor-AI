# ZeroDep Doctor — 5-Minute Judge Presentation & Video Script

This document provides a deterministic, step-by-step presentation script designed for a 5-minute competition demo or video recording.

---

## Story Timeline Overview

```text
[0:00 - 0:30]  The Problem: Unaware Dependency Bloat
[0:30 - 1:10]  Step 1: Automated Audit & Health Index (`zerodep analyze`)
[1:10 - 1:50]  Step 2: AST Usage Intelligence & Scope Analysis (`zerodep dependencies`)
[1:50 - 2:30]  Step 3: Honest Stdlib Migration Diffs & Confidence Ratings (`zerodep replacements`)
[2:30 - 3:10]  Step 4: Package Killer — ZeroDep Graph Engine (`zerodep graph`)
[3:10 - 3:45]  Step 5: Prioritized Action Plan (`zerodep plan demo/`)
[3:45 - 4:10]  Step 6: 'What-If?' Simulation — Dependency Migration Simulator (`zerodep simulate`)
[4:10 - 4:40]  Step 7: Executive Decision Summary (`zerodep summary demo/`)
[4:40 - 4:50]  Step 8: Zero-Dependency Verification Stack & Single-File Binary
[4:50 - 5:00]  Conclusion & Hostile Judge Defense Summary
```

---

## 0:00 - 0:30 | Introduction & Problem Statement

### Action
Display a Python project directory (e.g. `demo/`) containing `pyproject.toml` and `main.py`.

### Script
> "Most Python projects suffer from silent dependency creep. Developers add third-party packages for simple tasks like HTTP requests, CLI option parsing, or table formatting — often unaware that Python's standard library already contains equivalent tools. Existing scanners like `pipdeptree` or `deptry` only tell you what is installed or missing. **ZeroDep Doctor** asks a deeper question: **What does your codebase actually use, what can be safely removed, and where can Python's standard library replace external dependencies?** And true to our mission, ZeroDep Doctor itself has **ZERO third-party runtime dependencies**."

---

## 0:30 - 1:20 | Step 1 — Automated Audit & Health Index

### Action
Run CLI command in terminal:
```bash
python dist/zerodep.py analyze demo/
```

### Key Visual Points to Emphasize
- Executive Header & Scanned Stats (3 files, 64 LOC, execution time < 0.01s).
- Health Score: **40.7 / 100 [GRADE: F]** vs Potential Score: **100.0 / 100**.
- Dependency breakdown showing **2 Unused** (-30.0 pts) and **4 Replaceable** (-32.0 pts).

### Script
> "With a single command, `zerodep analyze` scans the entire project statically via AST parsing without executing a single line of code. Notice our **ZeroDep Health Index**: this demo project receives a score of **40.7 / 100 (Grade F)** due to declared dependencies that are either completely unused or easily replaceable by the standard library. If we act on these findings, our score recovers to a perfect **100 / 100**."

---

## 1:20 - 2:00 | Step 2 — AST Usage Intelligence

### Action
Run CLI command in terminal:
```bash
python dist/zerodep.py dependencies demo/
```

### Key Visual Points to Emphasize
- Detailed evidence blocks for each package (`[OK] Direct static import at module level`, `[OK] Multi-symbol API usage`).
- Call-site symbol breakdown (e.g. `requests.get (2)`, `requests.Session (1)`).
- Scope-aware detection (module vs function/class scope).

### Script
> "ZeroDep Doctor goes far beyond regex string matching. By walking Python's Abstract Syntax Tree, it tracks aliased imports like `import requests as req` and `from requests import get`. It identifies exact call sites, symbol counts, and scope boundaries. For example, it sees that `click` has 2 active call sites across `click.command` and `click.option`, whereas `colorama` and `pytz` are declared in `pyproject.toml` but have **zero AST references** across the entire codebase."

---

## 2:00 - 2:45 | Step 3 — Reviewable Migration Diffs & Confidence Model

### Action
Run CLI command in terminal:
```bash
python dist/zerodep.py replacements demo/
```

### Key Visual Points to Emphasize
- Reviewable side-by-side git diff suggestions (`- import click` / `+ import argparse`).
- Explicit confidence ratings: `click` → **HIGH** (`argparse`), `requests` → **LOW** (due to `requests.Session`).

### Script
> "Here is where ZeroDep Doctor turns analysis into action. It generates reviewable standard-library migration diffs. For `click`, it suggests `argparse`. For `networkx`, it suggests our native graph engine. But notice something critical for judge defensibility: for `requests`, the confidence is automatically rated **LOW**. Why? Because our AST visitor detected `requests.Session()` in `main.py`. We know that session state and custom auth wrappers cannot be blindly refactored. ZeroDep Doctor provides honest, reviewable suggestions with explicit technical limitations — never unsafe black-box code modification."

---

## 2:45 - 3:30 | Step 4 — Package Killer: ZeroDep Graph Engine

### Action
Run CLI command in terminal:
```bash
python dist/zerodep.py graph demo/
```

### Key Visual Points to Emphasize
- Circular import detection: `demo/module_a.py -> demo/module_b.py -> demo/module_a.py`.
- Native Graph metrics: Total Nodes, Hub Modules ranking (`requests`, `click`), Connected Components.

### Script
> "Dependency analyzers often depend on heavy external graph libraries like `networkx` to compute module graphs. To adhere to our zero-dependency mandate, we built the **ZeroDep Graph Engine** 100% using standard library primitives — `collections.defaultdict`, `collections.deque`, and `heapq`. It executes 3-color DFS cycle detection, Kahn's topological sort, Dijkstra's shortest paths, and hub-node ranking. As shown here, it instantly flags a circular dependency between `module_a.py` and `module_b.py`."

---

## 3:45 - 4:20 | Step 6 — "What-If?" Dependency Migration Simulator

### Action
Run CLI command in terminal:
```bash
python dist/zerodep.py simulate demo/ --recommended
```

### Key Visual Points to Emphasize
- Before/After footprint table: which deps disappear, which stay.
- Exact projected dependency count and % reduction.
- Migration assumptions per package (explicit, per-package text).
- Blockers list (e.g. dynamic import uncertainty).
- Simulation Confidence rating: HIGH / MEDIUM / LOW.

### Script
> "Phase 5 introduces our **Dependency Migration Simulator** — a pure 'What-If?' analysis. With a single command, it projects the hypothetical dependency footprint if we acted on the Action Planner's recommendations. Notice the Before/After table: `colorama` moves from `[UNUSED]` to `[REMOVE]`, `click` moves from `[REPLACEABLE]` to `[MIGRATE]`. The projected count drops and we see the exact percentage reduction.
>
> Critical point for judges: this simulator is **100% read-only**. It consumes pre-computed AST, risk, impact, and action plan results already in memory — it does not re-scan files, does not write to disk, does not modify `pyproject.toml`, or touch any source file. Every projection uses explicit hypothetical wording: *Projected*, *Hypothetical*, *Potential*. A regression test verifies byte-identity of all source files before and after simulation."

---

## 4:10 - 4:40 | Step 7 — Executive Decision Summary

### Action
Run CLI command in terminal:
```bash
python dist/zerodep.py summary demo/
```

### Key Visual Points to Emphasize
- Consolidated Executive View: Portfolio counts, Migration Readiness status & reasons.
- Ranked Top Priorities with Impact, Risk, Confidence, Effort, and Action.
- Footprint Reduction projection and 3×3 Decision Matrix.
- Aggregated Blockers / Uncertainties and Next Best Action.

### Script
> "Finally, Phase 6 brings everything together in `zerodep summary`. In one clean executive view, project leads and security auditors see portfolio totals, migration readiness (`PARTIALLY READY`), deterministically ranked top priorities, footprint reduction projection, 3×3 decision matrix, remaining blockers, and the single next best action to execute."

---

## 4:40 - 4:50 | Step 8 — Zero-Dependency Proof Stack & Single-File Distribution

### Action
Run CLI commands in terminal:
```bash
# 1. Show zero runtime dependencies in pyproject.toml
type pyproject.toml

# 2. Run single-file standalone distribution from outside the repo
python dist/zerodep.py analyze demo/
```

### Key Visual Points to Emphasize
- Empty `[dependencies]` array in project manifests.
- Standalone single-file bundle `dist/zerodep.py` executing out-of-the-box.

### Script
> "How do we prove ZeroDep Doctor itself has zero runtime dependencies? First, our `pyproject.toml` dependencies array is completely empty. Second, when ZeroDep Doctor audits its own codebase, it scores a **100/100 A+ purity score**. Third, using `bundle.py`, the entire application compiles into a single, standalone ~151 KB file `dist/zerodep.py` that runs out-of-the-box on any standard Python 3.11+ installation with zero `pip install` required."

---

## 4:50 - 5:00 | Conclusion & Technical Summary

### Action
Return to terminal summary view or slides.

### Script
> "To summarize our submission:
> 1. **331 unit tests** covering all 6 analysis phases — from AST visitors and graph algorithms to action planner, simulator, decision summary, and adversarial security tests.
> 2. **Sub-second performance** (scanning 1,000 files in under 8.8 seconds).
> 3. **10 stdlib migration recipes** with usage-aware confidence downgrading.
> 4. **6 analysis phases**: Impact → Risk → Explanations → Action Plan → 'What-If?' Simulation → Decision Summary.
> 5. **100% Python Standard Library** — zero third-party runtime dependencies.
> 
> ZeroDep Doctor helps Python developers trim the fat, eliminate supply chain risk, and unlock the hidden power of the standard library. Thank you!"
