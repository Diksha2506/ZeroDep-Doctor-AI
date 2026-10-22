# ZeroDep Doctor

> **The zero-dependency static analyzer that audits, graphs, and recommends standard-library migrations for Python codebases.**

ZeroDep Doctor itself has **ZERO third-party runtime dependencies**. Built 100% using the Python Standard Library (`ast`, `urllib`, `collections`, `heapq`, `tomllib`, `argparse`, `pathlib`).

---

## Technical Architecture

```text
                           Python Target Codebase
                                     │
                                     ▼
                          Manifest Parser (pyproject.toml / requirements.txt)
                                     │
                                     ▼
                          AST Visitor (ast.NodeVisitor)
                                     │
              ┌──────────────────────┼──────────────────────┐
              ▼                      ▼                      ▼
     Dependency Metadata    Usage & Evidence Model   Import Dependency Graph
    (declared / scope)      (aliased symbols/sites)  (ZeroDep Graph Engine)
              │                      │                      │
              └──────────────────────┼──────────────────────┘
                                     ▼
                          Migration Recipe Engine (10 Recipes)
                                     │
                                     ▼
                          Health Scoring Engine (0-100 Score)
                                     │
              ┌──────────────────────┴──────────────────────┐
              ▼                                             ▼
     ANSI Terminal Formatter                        Machine-Readable JSON Report
```

---

## Why Zero Dependency?

Because the constraint is the product. Building ZeroDep Doctor with **zero third-party runtime packages** forced us to engineer parsing, AST call-site tracking, graph algorithms, evidence models, CLI formatting, bundling, and testing using only Python's standard library.

The result is not merely another dependency tool — it is an empirical demonstration of how much engineering value can be extracted directly from Python itself.

---

## Key Features

- **Dependency Usage Intelligence:** Goes beyond "is it imported?" — tracks exact call sites, resolved symbols, file locations, and occurrence counts per dependency.
- **Explainable Confidence Model:** Rates every dependency classification as HIGH / MEDIUM / LOW / UNKNOWN with a transparent evidence trail.
- **Import Scope Analysis:** Distinguishes module-level, function-nested, class-nested, conditional (try/except), and dynamic (importlib) imports.
- **Focused NetworkX-Inspired Graph Engine:** Native graph engine supporting BFS, DFS, 3-color cycle detection, Kahn's topological sort, Dijkstra shortest paths, dependency depth, hub nodes, and weakly-connected components using standard library primitives.
- **Usage-Aware Migration Suggestions:** 10 reviewable stdlib migration diff suggestions with dynamic confidence downgrading for complex API usage (e.g. `requests.Session` downgrades from HIGH to LOW).
- **Explainable Health Index:** Current Score vs Potential Score (0–100) with per-category recovery breakdown.
- **Dependency Impact Analysis (Phase 1):** Calculates removal/migration blast radius per dependency — files affected, call sites, transitive module reach, hub role, circular dependency involvement, and estimated migration effort.
- **Dependency Risk Model (Phase 2):** Evidence-based risk scoring (0–100) for each dependency with transparent contributing factors, warnings, and recommended actions (REMOVE / SAFE MIGRATION CANDIDATE / REVIEW MIGRATION / MANUAL REVIEW / KEEP / INVESTIGATE).
- **Dependency Explanation Engine (Phase 3):** Deterministic narrative generator providing clear, evidence-backed answers for why a dependency exists, what it does, and how to address it.
- **Dependency Action Planner (Phase 4):** Converts evidence into a prioritized migration sequence (P0–P3) with step-by-step instructions, human review rules, effort aggregation, and next best action recommendation (`zerodep plan`).
- **Dependency Migration Simulator — "What-If?" Analysis (Phase 5):** Pure hypothetical simulation of dependency removal and stdlib migration scenarios. Projects dependency count, calculates exact mathematical reduction percentage, lists per-package migration assumptions, and surfaces blockers — **without modifying any source file, manifest, or project filesystem**. (`zerodep simulate`)
- **Dependency Decision Summary (Phase 6):** Executive-level unified decision view consolidating portfolio counts, migration readiness, deterministic top priorities, What-If projection, 3×3 decision matrix, blockers, and next best action in one view (`zerodep summary`).
- **Risk vs Impact Matrix:** 3×3 matrix placing each dependency by its independent risk and impact levels — demonstrating they are orthogonal dimensions.
- **Machine-Readable JSON:** Deterministic JSON output with full evidence arrays, migration suggestions, impact, risk, explanations, action plan, simulation projection, graph metrics, and score breakdown.
- **Non-TTY / CI Safe:** ANSI colors disabled automatically when output is piped, redirected, or `NO_COLOR` is set.
- **Single-File Standalone Python Distribution:** `bundle.py` compiles the tool into `dist/zerodep.py` — single-file, zero dependencies.

---

## Impact vs Risk: Two Independent Dimensions

ZeroDep Doctor separates two orthogonal concerns:

| Dimension | Question | Example (HIGH) |
| :--- | :--- | :--- |
| **Impact** | *"If I remove/migrate this dep, how large is the blast radius?"* | `requests` used in 10 files with 50 call sites |
| **Risk** | *"How much uncertainty or refactoring difficulty does this dep introduce?"* | `requests` with Session API, LOW migration confidence |

**A dependency can be HIGH Impact + LOW Risk:** e.g. `colorama` used in many files but with a HIGH-confidence, trivial stdlib migration.

**A dependency can be LOW Impact + HIGH Risk:** e.g. a single-file dependency with dynamic imports, star imports, and circular graph involvement — small blast radius, high uncertainty.

### Risk Score Evidence Model (0–100)

| Risk Factor | Score Contribution | Rationale |
| :--- | :--- | :--- |
| Unused declaration | +15 | Manifest clutter with no observable code value |
| Import-but-unused | +20 | Dead import statement adds confusion |
| LOW migration confidence | +25 | Complex manual refactoring required |
| MEDIUM migration confidence | +10 | Non-trivial migration path |
| Complex/stateful API (HIGH complexity) | +15 | Stateful clients (Session, context manager) hard to replace |
| Moderate API complexity | +5 | Multi-symbol but replaceable API |
| Dynamic import detected | +20 | Static analysis uncertainty — cannot be verified statically |
| Star import detected | +15 | Symbol origin obscured — usage may be underreported |
| Conditional import detected | +10 | Environment-dependent behavior adds branching risk |
| Circular dependency involvement | +15 | Structural refactoring creates cascading changes |
| High call-site volume (≥15) | +10 | Widespread invocation increases refactoring surface |
| Widespread file touch (≥4 files) | +10 | Broad migration scope |
| No stdlib replacement (ESSENTIAL) | +10 | Long-term third-party dependency risk |

**Risk Level Thresholds:**
- `LOW`: Score < 30
- `MEDIUM`: 30 ≤ Score < 55
- `HIGH`: Score ≥ 55

### Recommendation Engine

| Condition | Recommendation |
| :--- | :--- |
| Status = UNUSED or IMPORTED_BUT_UNUSED | **REMOVE** |
| REPLACEABLE + HIGH confidence + LOW/MEDIUM risk | **SAFE MIGRATION CANDIDATE** |
| Dynamic imports or star imports detected (non-SAFE_MIGRATION) | **INVESTIGATE** |
| REPLACEABLE + MEDIUM confidence or MEDIUM risk | **REVIEW MIGRATION** |
| REPLACEABLE + LOW confidence or HIGH risk | **MANUAL REVIEW** |
| ESSENTIAL + LOW/MEDIUM risk + no circular | **KEEP / NO ACTION** |

> **This is NOT a vulnerability scanner.** Risk scores reflect static analysis uncertainty and migration complexity only. ZeroDep Doctor makes no claims about package security, CVEs, or supply-chain risk.

---

## Installation & Setup

ZeroDep Doctor requires Python 3.11+ and **no third-party packages**.

```bash
git clone https://github.com/your-username/zerodep-doctor.git
cd zerodep-doctor
```

---

## CLI Usage

```bash
# Full audit report
python dist/zerodep.py analyze ./my_project

# Dependency analysis with confidence model
python dist/zerodep.py dependencies ./my_project

# Dependency graph + cycle detection
python dist/zerodep.py graph ./my_project

# Migration suggestions only
python dist/zerodep.py replacements ./my_project

# Machine-readable JSON report (no ANSI)
python dist/zerodep.py report ./my_project

# Any command + --json flag
python dist/zerodep.py analyze ./my_project --json

# Prioritized migration action plan
python dist/zerodep.py plan ./my_project

# Executive decision summary
python dist/zerodep.py summary ./my_project

# 'What-If?' simulation — follow Action Planner recommendations
python dist/zerodep.py simulate ./my_project --recommended

# 'What-If?' simulation — only safe (high-confidence) changes
python dist/zerodep.py simulate ./my_project --all-safe

# 'What-If?' simulation — specific targets
python dist/zerodep.py simulate ./my_project --targets requests colorama
```

> **Safety guarantee**: `zerodep simulate` is a **purely hypothetical, read-only analysis**.
> It NEVER modifies source files, `pyproject.toml`, `requirements.txt`, or any project file.
> All projections use wording: *Projected*, *Hypothetical*, *Potential*, *Simulation*.

---

## Demo Project

The `demo/` directory demonstrates all key capabilities:

| Feature | Demo File |
| :--- | :--- |
| Simple `requests.get()` | `main.py` |
| Aliased import `requests as req` | `main.py` |
| From-import `from requests import get` | `main.py` |
| Complex `requests.Session()` (LOW confidence) | `main.py` |
| Conditional `try/except ImportError` | `main.py` |
| Dynamic `importlib.import_module` | `main.py` |
| Circular dependency `module_a ↔ module_b` | `module_a.py`, `module_b.py` |
| Unused declared deps (colorama, pytz) | `pyproject.toml` |

---

## AI Sprint Innovation: Evidence-Grounded Dependency Doctor

ZeroDep Doctor AI connects deterministic AST evidence to open-weight models (served locally via Ollama) and an interactive web dashboard:

1. **Deterministic Static Engine (Core)**: 100% Python Standard Library. Discovers AST call sites, scopes, graph cycles, blast radius impact, and risk scores.
2. **Open-Weight AI Model Integration**: Optional local LLM adapter (`zerodep.ai_adapter.ZeroDepAIAdapter`) querying Ollama (`llama3.2`, `mistral`, `deepseek-r1`) via HTTP.
   - **Strict Evidence Grounding**: The model receives real symbol counts, call site references, and manifest data.
   - **No Hallucinations**: Zero fabricated paths or CVEs.
   - **Deterministic Fallback**: If Ollama or the model is offline, gracefully falls back to the deterministic rules engine without failing.
3. **Reviewable Migration Diff Engine**: Proposes unified diff patches for supported stdlib recipes (e.g. `tabulate`, `click`, `colorama`, `networkx`, `requests`). Enforces explicit user approval and rejects low-confidence auto-application.
4. **Isolated Sandboxed Verifier**: Safely tests proposed migrations by cloning the project into a temporary isolated directory, applying the patch, and executing test suites or syntax verification with timeouts. Never executes uploaded project code during static audit.
5. **FastAPI & Web Dashboard**: Live modern UI with scorecard metrics, dependency classification table, AST evidence inspector, AI reasoning panel, diff viewer, and sandbox verification output.

---

## Getting Started

### 1. Install & Run CLI
```bash
# Clone repository
git clone https://github.com/Diksha2506/Zerodep-Intelligent_dependency_analyzer.git
cd Zerodep-Intelligent_dependency_analyzer

# Run pure CLI static audit (Zero external dependencies)
python -m zerodep.cli analyze demo
python -m zerodep.cli plan demo
python -m zerodep.cli summary demo
```

### 2. Launch FastAPI Server & Web Dashboard
```bash
# Start backend server on port 8000
python -m uvicorn zerodep.server:app --port 8000

# Open in browser:
# http://localhost:8000
```

### 3. (Optional) Run with Local Open-Weight LLM (Ollama)
```bash
# In another terminal, start Ollama and pull llama3.2
ollama serve
ollama pull llama3.2

# ZeroDep Doctor automatically connects to http://localhost:11434
# If Ollama is not running, ZeroDep Doctor seamlessly uses its built-in
# deterministic evidence engine fallback.
```

---

## Verification & Testing

```bash
# Run full test suite (339 tests passing)
python -m pytest

# Run AI Sprint tests specifically
python -m pytest tests/test_ai_sprint.py

# Build single-file standalone distribution
python bundle.py
```

---

## Performance Benchmarks

Measured across synthetic Python repositories:

| Repository Size | Files Scanned | Lines of Code | Execution Time |
| :--- | :--- | :--- | :--- |
| **10 Files** | 10 | 70 | **0.055s** |
| **100 Files** | 100 | 700 | **0.294s** |
| **1,000 Files** | 1,000 | 7,000 | **4.06s** |

---

## Known Limitations & Security Guardrails

1. **Non-destructive Static Analysis**: Scanning never executes target code.
2. **Reviewable Diff Workflows**: Migration patches are generated as unified diff previews. Low-confidence cases (such as stateful `requests.Session` usage) are flagged and blocked from auto-application.
3. **Sandbox Isolation**: Verifications run exclusively in temporary workspaces with directory-traversal bounds checking (`relative_to` enforcement).
4. **AI Hallucination Containment**: The AI prompt is strictly constrained to AST output, symbol counts, and known limitations.
5. **No CVE Marketing**: ZeroDep Doctor is a static code usage and standard-library migration engine, not a CVE/vulnerability database scanner.

---

## License

MIT License — Hackathon Raptors 2026 Submission.

