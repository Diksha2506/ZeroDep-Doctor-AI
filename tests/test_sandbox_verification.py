"""Comprehensive test suite for sandbox verification, approval enforcement, isolation, and edge cases."""
import hashlib
from pathlib import Path
import pytest
import sys

from zerodep.verifier import ProjectVerifier, VerificationResult
from zerodep.server import app
from fastapi.testclient import TestClient

client = TestClient(app)


def _compute_dir_hash(directory: Path) -> dict:
    """Helper to hash all files in directory to ensure immutability."""
    hashes = {}
    for p in sorted(directory.rglob("*")):
        if p.is_file():
            hashes[str(p.relative_to(directory))] = hashlib.sha256(p.read_bytes()).hexdigest()
    return hashes


# ==============================================================================
# Case A — Approval enforcement via API & frontend contract
# ==============================================================================
def test_verify_endpoint_requires_payload():
    """Verify endpoint rejects empty or malformed requests."""
    res = client.post("/api/proposal/verify", json={})
    assert res.status_code == 422


def test_verify_endpoint_requires_valid_project_path(tmp_path):
    """Missing project directory returns 400 Bad Request."""
    res = client.post("/api/proposal/verify", json={
        "project_path": str(tmp_path / "does_not_exist"),
        "package": "requests",
        "file_patches": {"app.py": "print(1)"}
    })
    assert res.status_code == 400
    assert "not found" in res.json()["detail"].lower()


# ==============================================================================
# Case B — Successful verification in sandbox
# ==============================================================================
def test_verify_successful_migration_sandbox(tmp_path):
    """Applying a working migration diff to a temporary copy passes verification."""
    # Create original project with an initial failing file
    src_file = tmp_path / "app.py"
    src_file.write_text("def run():\n    raise NotImplementedError('old code')\n", encoding="utf-8")
    
    test_file = tmp_path / "test_app.py"
    test_file.write_text(
        "import unittest\nfrom app import run\n"
        "class TestApp(unittest.TestCase):\n"
        "    def test_run(self):\n"
        "        self.assertEqual(run(), 42)\n",
        encoding="utf-8"
    )

    verifier = ProjectVerifier(timeout_seconds=10.0)
    
    # 1. Baseline without patch should fail
    baseline = verifier.verify_proposal(
        source_dir=tmp_path,
        file_patches={},
        test_command=[sys.executable, "-m", "unittest", "test_app.py"]
    )
    assert baseline.status == "FAIL"
    assert baseline.exit_code != 0

    # 2. Applying correct patch should PASS
    patch = "def run():\n    return 42\n"
    res = verifier.verify_proposal(
        source_dir=tmp_path,
        file_patches={"app.py": patch},
        test_command=[sys.executable, "-m", "unittest", "test_app.py"]
    )
    assert res.status == "PASS"
    assert res.exit_code == 0
    assert res.diff_applied is True
    assert res.execution_time_seconds > 0


# ==============================================================================
# Case C — Failed verification
# ==============================================================================
def test_verify_failed_migration_sandbox(tmp_path):
    """A patch that causes tests to fail reports FAIL with non-zero exit code and error output."""
    src_file = tmp_path / "app.py"
    src_file.write_text("def run(): return 1\n", encoding="utf-8")
    
    test_file = tmp_path / "test_app.py"
    test_file.write_text(
        "import unittest\nfrom app import run\n"
        "class TestApp(unittest.TestCase):\n"
        "    def test_run(self):\n"
        "        self.assertEqual(run(), 100)\n",
        encoding="utf-8"
    )

    verifier = ProjectVerifier(timeout_seconds=10.0)
    res = verifier.verify_proposal(
        source_dir=tmp_path,
        file_patches={"app.py": "def run(): return 2\n"},
        test_command=[sys.executable, "-m", "unittest", "test_app.py"]
    )
    assert res.status == "FAIL"
    assert res.exit_code != 0
    assert "AssertionError" in res.stderr or "FAIL" in res.stderr or "AssertionError" in res.stdout


# ==============================================================================
# Case D — Timeout handling
# ==============================================================================
def test_verify_timeout_handling(tmp_path):
    """Subprocess exceeding configured timeout reports TIMEOUT and terminates cleanly."""
    src_file = tmp_path / "infinite.py"
    src_file.write_text("import time\ntime.sleep(10)\n", encoding="utf-8")

    verifier = ProjectVerifier(timeout_seconds=1.0)
    res = verifier.verify_proposal(
        source_dir=tmp_path,
        file_patches={},
        test_command=[sys.executable, "infinite.py"]
    )
    assert res.status == "TIMEOUT"
    assert res.exit_code == -1
    assert "timed out" in res.stderr.lower()


# ==============================================================================
# Case E — Original project immutability / genuine isolation
# ==============================================================================
def test_original_project_protection(tmp_path):
    """Original project files are completely preserved and never modified by verification."""
    f1 = tmp_path / "module_a.py"
    f1.write_text("ORIGINAL_CONTENT_A = True\n", encoding="utf-8")
    f2 = tmp_path / "module_b.py"
    f2.write_text("ORIGINAL_CONTENT_B = 12345\n", encoding="utf-8")

    before_hashes = _compute_dir_hash(tmp_path)

    # Run verification with destructive patch attempting to overwrite module_a
    verifier = ProjectVerifier(timeout_seconds=5.0)
    res = verifier.verify_proposal(
        source_dir=tmp_path,
        file_patches={"module_a.py": "COMPLETELY_MODIFIED = 'sandbox_only'\n"}
    )
    assert res.status == "PASS"

    after_hashes = _compute_dir_hash(tmp_path)
    assert before_hashes == after_hashes, "Original directory was modified during sandbox verification!"
    assert f1.read_text(encoding="utf-8") == "ORIGINAL_CONTENT_A = True\n"


# ==============================================================================
# Case F — Path traversal protection
# ==============================================================================
def test_verify_rejects_path_traversal(tmp_path):
    """Patches attempting path traversal outside sandbox are rejected safely with ERROR."""
    verifier = ProjectVerifier(timeout_seconds=5.0)
    res = verifier.verify_proposal(
        source_dir=tmp_path,
        file_patches={"../../outside_file.txt": "evil content"}
    )
    assert res.status == "ERROR"
    assert res.diff_applied is False
    assert "traversal" in res.stderr.lower() or "security" in res.summary.lower()


# ==============================================================================
# Case G — Full HTTP API round-trip test
# ==============================================================================
def test_verify_api_http_round_trip(tmp_path):
    """Test full HTTP POST to /api/proposal/verify returns expected response schema."""
    app_file = tmp_path / "main.py"
    app_file.write_text("print('hello')\n", encoding="utf-8")

    response = client.post(
        "/api/proposal/verify",
        json={
            "project_path": str(tmp_path),
            "package": "requests",
            "file_patches": {"main.py": "print('verified_code')\n"}
        }
    )
    assert response.status_code == 200
    data = response.json()
    assert "status" in data
    assert data["status"] == "PASS"
    assert data["exit_code"] == 0
    assert "command" in data
    assert "execution_time_seconds" in data
    assert "stdout" in data
    assert "stderr" in data
    assert "summary" in data
