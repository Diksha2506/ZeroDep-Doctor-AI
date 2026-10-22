# Audit Findings & Sprint Implementation Checklist

## 1. Audit Findings (First 15 Minutes)
- **Git Status**: Clean working tree on `main`, commit `7999db0` ("Initial commit: ZeroDep dependency risk analyzer").
- **Baseline Test Suite**:
  - `pytest` direct invocation collected 220 items with 5 errors due to `PYTHONPATH` not including cwd (`ModuleNotFoundError: No module named 'zerodep'`).
  - `python -m pytest` executes cleanly: **331 passed in 5.81s (100% pass rate)**.
- **Engine Capabilities Present**:
  - Parsing manifests (`pyproject.toml`, `requirements.txt`).
  - AST analysis tracking symbols, call sites, aliased imports, conditional imports, scope-nesting.
  - Graph engine with cycle detection, topological sort, Dijkstra, hubs.
  - Risk engine (0-100 score) & Impact engine (blast radius, transitive impact).
  - Explanation engine generating deterministic reasons.
  - Action planner and simulation engine ("What-If?").
  - 10 migration recipes in `zerodep/migration_engine.py`.
- **Gaps Identified**:
  - **No AI Model Adapter**: Completely absent. Need an open-weight local AI adapter (Ollama HTTP `/api/generate` with model like `llama3.2` or `mistral`, with configurable endpoint and deterministic fallback).
  - **No Migration Application & Verification Workflow**: Recipes only provide static code snippets; no diff generator on actual target source files, no approval gate, and no controlled isolated verification workflow.
  - **No Backend**: No HTTP/REST API exists. Need FastAPI backend with endpoints for project scan, AI explanation, diff preview, verified execution in a temp isolated directory.
  - **No Web UI / Frontend**: Only CLI exists. Need a React + TypeScript + Vite dashboard with full interactive workflow.

## 2. Implementation Checklist
- [x] Phase 1: Audit before coding & establish baseline (331 tests passing).
- [ ] Phase 2A/B: Implement `zerodep/ai_adapter.py` supporting Ollama / open-weight models, environment config, structured JSON schema, prompt grounding with real AST evidence, and deterministic fallback.
- [ ] Phase 2C/D: Implement `zerodep/diff_engine.py` and `zerodep/verifier.py` for reviewable diff generation and isolated test verification with subprocess timeouts.
- [ ] Phase 3 Backend: Implement `zerodep/server.py` FastAPI app with endpoints:
  - `GET /api/health`
  - `POST /api/analyze`
  - `POST /api/explain` (AI evidence grounded)
  - `POST /api/proposal/diff`
  - `POST /api/proposal/verify`
- [ ] Phase 3 Frontend: Create React + TypeScript + Vite app in `frontend/` with:
  - Project target selector / sample picker
  - Scan execution & Health Index / Scorecard
  - Dependency table with status, confidence, risk & impact
  - Dependency detail drawer with code evidence
  - AI Explanation panel with model badge & reasoning
  - Migration proposal diff viewer with approval button
  - Verification console showing subprocess PASS/FAIL & exit codes
- [ ] Phase 4: Unit & integration tests for AI adapter, diff engine, verifier, and API endpoints. Ensure full suite passes without regression.
- [ ] Phase 5: Submission readiness — updated README.md, demo script, and final report.
