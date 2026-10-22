# ZeroDep Doctor — Technical Defense (30 Seconds)

## Core Technical Summary

> **"What did we actually build?"**

---

## 30-Second Script

> "ZeroDep Doctor is a 100% Python Standard Library static analysis engine built on seven core components:
>
> 1. **AST Visitor (`ast.NodeVisitor`):** Tracks aliased imports, from-imports, scope nesting, and exact function call sites without executing user source code.
> 2. **ZeroDep Graph Engine:** Replaces runtime NetworkX dependencies using `collections.defaultdict`, `deque`, and `heapq` for BFS, 3-color DFS cycle detection, Kahn's topological sort, and Dijkstra's algorithm.
> 3. **Usage-Aware Migration Engine:** Generates reviewable stdlib replacement diffs with dynamic confidence downgrading (e.g. `requests.Session` usage downgrades confidence from HIGH to LOW).
> 4. **Dependency Impact Analysis (Phase 1):** Calculates per-dependency blast radius — files affected, transitive module reach, hub role, circular involvement — and estimates migration effort (Trivial / Moderate / Broad / Complex).
> 5. **Dependency Risk Model (Phase 2):** Evidence-based 0–100 risk score per dependency, aggregating 13 distinct risk factors (dynamic imports, star imports, LOW confidence, stateful API, circular involvement, file spread, call volume) into a transparent recommendation (REMOVE / SAFE MIGRATION / MANUAL REVIEW / INVESTIGATE / KEEP).
> 6. **Dependency Explanation Engine (Phase 3):** Deterministic, evidence-backed narrative generation answering *Why is this dependency here, what is it doing, can I replace it, and what should I do next?* without LLMs or external APIs.
> 7. **Dependency Action Planner (Phase 4):** Converts evidence into a prioritized migration sequence (P0–P3) with step-by-step instructions, human review rules, effort aggregation, and next best action recommendation (`zerodep plan`).
> 8. **Dependency Migration Simulator — "What-If?" Analysis (Phase 5):** Pure hypothetical, read-only projection of dependency footprint change. Consumes pre-computed scan results in-memory, projects dependency count and exact percentage reduction. (`zerodep simulate`)
> 9. **Dependency Decision Summary (Phase 6):** Executive-level unified decision view consolidating portfolio counts, migration readiness, deterministic top priorities, What-If projection, 3×3 decision matrix, blockers, and next best action in one view (`zerodep summary`).
> 10. **Single-File Bundler:** Compiles the entire tool into a byte-identical ~151.8 KB standalone executable `dist/zerodep.py` verified with 331 unit tests."

---

## Key Differentiators

| Ordinary Dep Tool | ZeroDep Doctor |
| :--- | :--- |
| Binary "used/unused" | Tracks exact symbols, call sites, files, occurrences |
| Just reports deps | Generates reviewable stdlib migration diffs |
| Single score | Impact score + Risk score + 3×3 matrix + Explanation + Action Plan + Simulation |
| AI / API dependencies | 100% deterministic static evidence (0 LLM calls, 0 network APIs) |
| Runtime deps required | 0 runtime dependencies — pure stdlib |
| Opaque results | Every classification has an evidence trail |
| Risk = vulnerability | Risk = static analysis uncertainty + migration complexity |
| "Migration" = code change | Our simulator is purely hypothetical / read-only (byte-identity verified) |

---

## Technical Q&A Highlights

> **Judge Question:** *"Isn't this Explanation Engine just using an AI/LLM API under the hood?"*
> **Answer:** "No. Every explanation string is generated deterministically by pure Python logic inspecting pre-computed AST call sites, symbol counts, migration recipes, and graph topology. There are zero network calls, zero API keys, and zero LLM dependencies."

---

> **Judge Question:** *"Does `zerodep simulate` actually change my code?"*
> **Answer:** "No. The simulator is 100% read-only. It consumes pre-computed scan results that are already in memory from the AST analysis pass — no files are re-read, no writes are made to disk. A dedicated regression test (`test_source_files_byte_identical_after_simulation`) verifies SHA-256 byte-identity of all project files before and after simulation runs in all three modes."

---

## Security Guarantee

> **Analyzed Python source is NEVER executed.** All analysis uses `ast.parse()` + `ast.NodeVisitor` for pure compile-time structural extraction only.
