---
name: zerodep-doctor
description: >-
  Audits Python projects for unused and replaceable dependencies using AST analysis, graphs blast-radius impact,
  calculates risk scores, explains findings using open-weight AI (or deterministic fallback), proposes reviewable
  stdlib migration diffs with developer approval, and verifies changes in sandboxed workspaces.
---

# ZeroDep Doctor — Agent Skill

This skill allows an AI agent to audit a Python project, explain dependency usage and trade-offs using real AST evidence, generate reviewable standard-library replacement diffs, require explicit developer approval, and verify migrations in an isolated sandbox.

## When to Use This Skill

Activate this skill when:
- The user asks to audit, analyze, or clean up Python dependencies in a repository.
- The user asks whether a third-party dependency (e.g. `requests`, `tabulate`, `click`, `colorama`, `networkx`, `python-dotenv`, `loguru`) can be replaced with Python standard library.
- The user requests a reviewable migration diff to eliminate external dependencies.
- The user asks to verify that a proposed dependency migration passes tests without modifying the original project files.

---

## Complete Workflow

### Step 1: Project Audit & Evidence Gathering
Run the ZeroDep deterministic static analyzer on the target project path. Never guess or fabricate dependency findings.

```bash
# JSON output containing complete AST evidence, call sites, risk, and impact
python -m zerodep.cli analyze <target_dir> --json
```

Or via Python API:
```python
from pathlib import Path
from zerodep.cli import scan_project

scan_data = scan_project(Path("<target_dir>"))
# scan_data contains: usage_details, score_info, risks, impacts, explanations, graph
```

Inspect the returned findings:
- `usage_details[dep]["status"]`: `UNUSED` | `REPLACEABLE` | `ESSENTIAL`
- `usage_details[dep]["evidence"]`: Static call site counts, scope level, import types
- `risks[dep].level` & `risks[dep].score`: 0–100 risk score based on uncertainty and API complexity
- `impacts[dep].level`: Blast radius (files affected, transitive modules, graph hubs)

---

### Step 2: Evidence-Grounded AI Explanation
Use `zerodep.ai_adapter.ZeroDepAIAdapter` to synthesize an explanation grounded strictly in the AST findings.

```python
from zerodep.ai_adapter import ZeroDepAIAdapter

adapter = ZeroDepAIAdapter()  # Configurable via ZERODEP_OLLAMA_MODEL (e.g. llama3.2, gemma2:9b)
explanation = adapter.explain_dependency("<dep_name>", scan_data)

print("Provider:", explanation.provider)  # "ollama" or "fallback"
print("Why Used:", explanation.why_used)
print("Benefits:", explanation.benefits)
print("Trade-offs:", explanation.trade_offs)
print("Risks:", explanation.risks)
print("Checklist:", explanation.verification_checklist)
```

**Guardrail**: If Ollama or the local model is offline, the adapter returns `is_fallback: True` using the built-in deterministic rules engine. **Clearly disclose whether reasoning was produced by the model or the fallback.**

---

### Step 3: Migration Recommendation & Decision
Consult the 3x3 Risk vs Impact decision matrix before proceeding:
- **UNUSED dependencies**: Recommend removing from manifest (`pyproject.toml` or `requirements.txt`).
- **HIGH-confidence REPLACEABLE dependencies** (`tabulate`, `click`, `colorama`, `networkx`, `dotenv`): Propose standard library migration.
- **LOW-confidence or Complex dependencies** (e.g. `requests` with `Session` or custom transport): Mark as `MANUAL REVIEW` / `INVESTIGATE`. Reject automatic patching.

---

### Step 4: Generate Reviewable Unified Diff
Generate a unified diff preview using the existing diff engine. **Do not modify source files directly.**

```python
from zerodep.diff_engine import generate_migration_proposal

proposal = generate_migration_proposal(Path("<target_dir>"), "<dep_name>", scan_data)
# proposal.file_diffs contains diff_patch and can_apply flag
# proposal.can_auto_apply is False for low-confidence or unsupported APIs
```

Present the unified diff to the developer for inspection:
```diff
--- a/main.py
+++ b/main.py
@@ -3,4 +3,4 @@
-import tabulate
+# ZeroDep: tabulate removed
```

---

### Step 5: Explicit Developer Approval Gate (Mandatory)
**CRITICAL**: You MUST NOT apply the patch or run verification without explicit developer consent.
Ask the developer:
> *"I have generated a reviewable migration diff to replace `<dep_name>` with `<stdlib_module>`. Would you like me to test and verify this change in an isolated sandbox?"*

If the user rejects or the proposal is low-confidence, halt and keep the project unchanged.

---

### Step 6: Isolated Sandboxed Verification
When approved, execute verification in a temporary workspace. Original project files are never touched.

```python
from zerodep.verifier import ProjectVerifier

verifier = ProjectVerifier(timeout_seconds=15.0)
file_patches = {
    diff.file_path: diff.proposed_content
    for diff in proposal.file_diffs
    if diff.can_apply
}

result = verifier.verify_proposal(
    source_dir=Path("<target_dir>"),
    file_patches=file_patches,
    test_command=None  # Automatically uses pytest/unittest or compileall
)

# result.status is "PASS", "FAIL", or "ERROR"
# result.exit_code, result.stdout, result.stderr
```

---

### Step 7: Report Verification Outcome
Report the genuine result to the developer:
1. **Verification Status**: PASS (Exit 0) or FAIL (Exit code).
2. **Subprocess Output**: Include stdout and stderr logs.
3. **Disclosure of Limitations**: State whether verification was dynamic test execution or static compilation.

---

## References & Examples
- [CLI Reference Guide](file:///c:/Users/diksh/OneDrive/Desktop/Hackthon/Zero-Dependancy/README.md)
- [Stdlib Migration Recipes](file:///c:/Users/diksh/OneDrive/Desktop/Hackthon/Zero-Dependancy/STDLIB.md)
- [Automated Demo Script](file:///c:/Users/diksh/OneDrive/Desktop/Hackthon/Zero-Dependancy/scripts/demo_ai_sprint.py)
