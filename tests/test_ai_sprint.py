"""Unit and integration tests for ZeroDep Doctor AI additions:
- Open-weight AI Adapter (availability check, evidence grounding, deterministic fallback)
- Unified Diff Engine (proposal generation, pattern transformations, low-confidence flagging)
- Isolated Verifier (sandboxing, timeout handling, path traversal protection, compileall/test execution)
- FastAPI Server Endpoints (health, analyze, explain, proposal diff, verify)
"""

import sys
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from zerodep.ai_adapter import ZeroDepAIAdapter, AIExplanationResponse
from zerodep.diff_engine import generate_migration_proposal, _transform_code
from zerodep.verifier import ProjectVerifier
from zerodep.server import app
from zerodep.cli import scan_project


@pytest.fixture
def repo_root():
    return Path(__file__).parent.parent


@pytest.fixture
def demo_scan(repo_root):
    return scan_project(repo_root / "demo")


# ── AI Adapter Tests ──────────────────────────────────────────

def test_ai_adapter_fallback_when_offline(demo_scan):
    """When Ollama is offline or unreachable, fallback deterministically without error."""
    adapter = ZeroDepAIAdapter(endpoint="http://127.0.0.1:59999", model="nonexistent-model")
    avail = adapter.check_availability()
    assert avail["available"] is False

    # Check that explain_dependency returns a valid structured AIExplanationResponse
    res = adapter.explain_dependency("tabulate", demo_scan)
    assert isinstance(res, AIExplanationResponse)
    assert res.is_fallback is True
    assert res.package == "tabulate"
    assert res.confidence == "HIGH"
    assert len(res.benefits) > 0
    assert len(res.verification_checklist) > 0
    # Must use real AST evidence, not fabricated lines
    assert any("call site" in ev.lower() or "import" in ev.lower() for ev in res.evidence_summary)


def test_ai_adapter_unused_package_grounding(demo_scan):
    """AI adapter correctly identifies unused dependency without inventing usages."""
    adapter = ZeroDepAIAdapter(endpoint="http://127.0.0.1:59999")
    res = adapter.explain_dependency("colorama", demo_scan)
    assert res.package == "colorama"
    assert "no call sites" in res.why_used.lower() or "manifest" in res.why_used.lower()


# ── Diff Engine Tests ─────────────────────────────────────────

def test_diff_engine_generates_reviewable_patch(repo_root, demo_scan):
    """Generates unified diff preview without modifying original files."""
    demo_dir = repo_root / "demo"
    orig_main = (demo_dir / "main.py").read_text(encoding="utf-8")

    proposal = generate_migration_proposal(demo_dir, "tabulate", demo_scan)
    assert proposal.package == "tabulate"
    assert proposal.confidence == "HIGH"
    assert len(proposal.file_diffs) > 0
    assert "--- a/main.py" in proposal.file_diffs[0].diff_patch
    assert "+++ b/main.py" in proposal.file_diffs[0].diff_patch

    # Verify original file was NOT modified by proposal generation
    after_main = (demo_dir / "main.py").read_text(encoding="utf-8")
    assert orig_main == after_main


def test_diff_engine_low_confidence_safeguard(repo_root, demo_scan):
    """Complex requests usage (Session) results in low confidence and can_auto_apply=False."""
    demo_dir = repo_root / "demo"
    proposal = generate_migration_proposal(demo_dir, "requests", demo_scan)
    assert proposal.package == "requests"
    assert proposal.confidence == "LOW"
    assert proposal.can_auto_apply is False
    assert any("low confidence" in d.reason.lower() for d in proposal.file_diffs)


# ── Project Verifier Tests ────────────────────────────────────

def test_verifier_isolated_execution(repo_root):
    """Verifier runs in a temporary directory and preserves original source."""
    demo_dir = repo_root / "demo"
    verifier = ProjectVerifier(timeout_seconds=10.0)

    # Supply a clean patch
    patches = {
        "main.py": "# Migrated test\nprint('Verification test!')\n"
    }

    result = verifier.verify_proposal(
        source_dir=demo_dir,
        file_patches=patches,
        test_command=[sys.executable, "-c", "print('PASS_TEST')"]
    )

    assert result.status == "PASS"
    assert result.exit_code == 0
    assert "PASS_TEST" in result.stdout
    # Original file is preserved
    assert (demo_dir / "main.py").exists()
    assert "requests" in (demo_dir / "main.py").read_text(encoding="utf-8")


def test_verifier_path_traversal_protection(repo_root):
    """Verifier rejects attempts to write outside sandbox workspace."""
    verifier = ProjectVerifier()
    patches = {
        "../../evil.py": "print('malicious')"
    }
    result = verifier.verify_proposal(
        source_dir=repo_root / "demo",
        file_patches=patches,
    )
    assert result.status == "ERROR"
    assert "path traversal" in result.stderr.lower() or "traversal" in result.summary.lower()


# ── Server API Integration Tests ──────────────────────────────

def test_api_health_endpoint():
    client = TestClient(app)
    resp = client.get("/api/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "healthy"
    assert "ai" in data
    assert "sample_fixtures" in data


def test_api_analyze_and_explain(repo_root):
    client = TestClient(app)
    # Analyze
    analyze_resp = client.post("/api/analyze", json={"project_path": "demo"})
    assert analyze_resp.status_code == 200
    adata = analyze_resp.json()
    assert adata["files_scanned"] >= 1
    assert "score" in adata
    assert len(adata["dependencies"]) > 0

    # Explain
    explain_resp = client.post("/api/explain", json={"project_path": "demo", "package": "click"})
    assert explain_resp.status_code == 200
    edata = explain_resp.json()
    assert edata["package"] == "click"
    assert "confidence" in edata
    assert len(edata["benefits"]) > 0

    # Proposal Diff
    diff_resp = client.post("/api/proposal/diff", json={"project_path": "demo", "package": "click"})
    assert diff_resp.status_code == 200
    pdata = diff_resp.json()
    assert pdata["package"] == "click"
    assert len(pdata["file_diffs"]) > 0

    # Verify
    verify_resp = client.post(
        "/api/proposal/verify",
        json={
            "project_path": "demo",
            "package": "click",
            "file_patches": {"main.py": "# patched\n"},
            "test_command": [sys.executable, "-c", "import sys; sys.exit(0)"],
        },
    )
    assert verify_resp.status_code == 200
    vdata = verify_resp.json()
    assert vdata["status"] == "PASS"
    assert vdata["exit_code"] == 0
