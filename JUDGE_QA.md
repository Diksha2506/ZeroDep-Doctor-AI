# Judge Defense & Technical Q&A

### Q1: Why not use `deptry` or `pip-extra-deps`?
*A:* Existing tools only flag missing or undeclared dependencies. ZeroDep Doctor performs full static AST analysis: call-site resolution, aliased-symbol tracking (`import requests as req`), scope-level awareness (module vs function vs class), circular module graph analysis, and generates human-reviewable standard-library migration diffs with usage-aware confidence ratings.

---

### Q2: Why not use `networkx` for graph processing?
*A:* The hackathon rule is zero third-party runtime dependencies. We built a native **ZeroDep Graph Engine** using `collections.defaultdict`, `collections.deque`, and `heapq` that implements BFS, DFS, 3-color cycle detection, Kahn's topological sort, Dijkstra's shortest path, dependency depth calculation, weakly-connected components (Union-Find), and hub-node ranking.

---

### Q3: Why are migration diffs labeled as "suggestions" rather than auto-applied?
*A:* Black-box refactoring of network logic (e.g. `requests.Session`, custom auth) can silently break production code. ZeroDep Doctor generates clearly labeled "REVIEWABLE DIFF SUGGESTION (NOT AUTOMATICALLY APPLIED)" blocks with explicit confidence ratings and technical limitations — making it defensible and correct rather than overconfident.

---

### Q4: How does ZeroDep Doctor detect complex vs simple API usage?
*A:* The migration engine dynamically inspects the detected symbol dictionary per dependency. For `requests`, seeing `requests.Session`, `requests.auth`, or `requests.adapters` downgrades the recipe confidence from HIGH to LOW and updates the `why_replaceable` text to say "Manual migration to urllib required." Simple `requests.get`-only usage stays HIGH.

---

### Q5: How are dynamic imports handled?
*A:* The AST visitor inspects `Call` nodes for `importlib.import_module` and `__import__`. These are flagged with file/line metadata in the `dynamic_imports` array and surfaced in both the CLI `[ANALYSIS WARNINGS]` section and JSON output. They are never misclassified as unused.

---

### Q6: How are conditional imports handled?
*A:* The `visit_Try` visitor sets `_in_conditional_import = True` when a `try/except (ImportError, ModuleNotFoundError)` guard is detected. The `visit_If` visitor does the same for `if`-guarded imports. Imports inside these guards get `is_conditional=True` and `scope_kind="conditional"`, reducing call-site confidence to MEDIUM.

---

### Q7: How are aliased imports and from-imports resolved?
*A:* The visitor maintains a `_symbols` dictionary mapping local names to `(original_module, full_symbol, confidence, scope_kind)`. `import requests as req` registers `req → requests`, and `from requests import get` registers `get → requests.get`. All call-site resolution goes through this table.

---

### Q8: How do you prove ZeroDep Doctor itself has zero runtime dependencies?
*A:* Five-point proof stack:
1. `requirements.txt` has 0 runtime packages.
2. `pyproject.toml` has empty `[dependencies]`.
3. `python dist/zerodep.py analyze .` self-audits with **100/100 A+ score**.
4. Operates identically in a pristine `python -m venv` with empty `pip freeze`.
5. Single-file `python dist/zerodep.py analyze ./demo` runs out-of-the-box.

---

### Q9: What are the known limitations?
*A:*
1. **Dynamic imports** (runtime string lookups) cannot be statically resolved — flagged, not misclassified.
2. **Star imports** (`from pkg import *`) prevent exact symbol attribution — confidence downgraded to MEDIUM.
3. **Complex external APIs** (requests.Session, SQLAlchemy ORM, etc.) get LOW confidence, not HIGH.
4. **Requires Python 3.11+** for `tomllib`; gracefully degrades to setup.cfg/requirements.txt parsing on older versions.

---

### Q10: What is the performance profile?
*A:* Measured benchmark across synthetic and real-world repositories:

| Files | Execution Time |
| :--- | :--- |
| 10 | 0.055s |
| 100 | 0.294s |
| 1,000 | 4.06s |

> Day 1 baseline at 1,000 files was 14.75s. Final state is **3.6x faster** despite richer analysis (confidence evidence, scope tracking, graph depth metrics).

---

### Q11: What was AI's role in this project?
---

### Q12: Why doesn't ZeroDep Doctor automatically modify my code?
*A:* Because the analyzer separates evidence from code modification. It provides a deterministic, prioritized migration plan (`zerodep plan`) while leaving final source changes and test verification strictly under developer control. Automated code rewrites without human review can introduce subtle runtime bugs, especially in complex network logic, stateful client objects, or environment-dependent handlers. ZeroDep Doctor prioritizes safety, transparency, and developer control.

---

### Q13: Does the `zerodep simulate` command actually migrate the project?
*A:* **No. Absolutely not.** `zerodep simulate` is a **100% read-only hypothetical analysis**. It consumes the pre-computed AST, impact, risk, and action plan results already in memory — it does not re-scan files, does not write to disk, does not modify `pyproject.toml`, `requirements.txt`, or any source file. The simulation is a mathematical projection of dependency footprint change only. All output uses explicit hypothetical wording: *Projected*, *Hypothetical*, *Potential*, *Simulation*. A regression test (`test_source_files_byte_identical_after_simulation`) verifies byte-identity of all project files before and after simulation.

---

### Q14: How do you know the projected dependency count is correct?
*A:* The math is exact and verifiable:
- `projected_count = current_count - (len(removed) + len(migrated))`
- `reduction_count = current_count - projected_count`
- `reduction_percentage = round((reduction_count / current_count) * 100.0, 2)`

`MANUAL REVIEW` dependencies are counted as **retained** (not removed) because they require human action before any removal — this is the conservative, correct accounting. The formula is tested explicitly in `test_projected_count_excludes_migrated_and_removed` and `test_reduction_percentage_math`.

---

### Q15: What modes does the simulator support and when should I use each?
*A:*
| Mode | Flag | When to Use |
| :--- | :--- | :--- |
| **recommended** | `--recommended` | Follow the Action Planner's prioritized recommendations. Best default. |
| **all_safe** | `--all-safe` | Only simulate changes the engine is 100% confident about (HIGH-confidence REPLACEABLE + REMOVE). Minimal risk. |
| **selected** | `--targets pkg1 pkg2` | Manually specify which packages to simulate removing/migrating. Useful for focused what-if analysis. |

---

### Q16: How does the simulator handle `requests.Session` and other complex APIs?
*A:* The simulation inherits the Action Planner's action for each package. `requests` with a detected `requests.Session` usage gets `LOW` migration confidence and `MANUAL REVIEW` action from the Risk Engine. In the simulator, a `MANUAL REVIEW` package is counted as **retained** (not removed from the projected count) and added to the `manual_review` list with an explicit blocker explaining the complex API surface. The simulation confidence degrades to `LOW`. This is consistent across: Risk Engine → Explanation Engine → Action Planner → Simulator → CLI → JSON.

---

### Q17: What does `zerodep summary` provide beyond `zerodep analyze`?
*A:* While `zerodep analyze` provides a detailed granular audit report, `zerodep summary` synthesizes all six analysis phases into an **executive decision view**. It consolidates portfolio metrics, migration readiness (`READY`, `PARTIALLY READY`, `REVIEW REQUIRED`, `BLOCKED`) with explainable bullet points, deterministically ranked top priorities, What-If reduction metrics, a 3×3 decision matrix, aggregated blockers, and the single recommended next best action.

---

### Q18: How does the 3×3 Decision Matrix in `zerodep summary` work?
*A:* The decision matrix maps orthogonal Impact (LOW / MEDIUM / HIGH) and Risk (LOW / MEDIUM / HIGH) levels directly into recommended action labels:
- LOW Impact + LOW Risk → **REMOVE** / **MIGRATE**
- LOW Impact + HIGH Risk → **INVESTIGATE**
- MEDIUM Impact + HIGH Risk → **MANUAL**
- HIGH Impact + LOW Risk → **PLAN**
- HIGH Impact + HIGH Risk → **BLOCK/MANUAL**

The matrix values are derived 100% deterministically from pre-computed risk, impact, and action plan data — ensuring complete consistency across CLI, JSON report, and summary views without introducing conflicting recommendation systems.
