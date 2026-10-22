"""Tests for feedback-driven migration repair loop, Graphify structural integration, and security guardrails."""

import json
from pathlib import Path
import tempfile
import pytest

from zerodep.diff_engine import FileDiff
from zerodep.graph_engine import ZeroDepGraph
from zerodep.repair_loop import MigrationRepairLoop, RepairAttempt, RepairLoopResult
from zerodep.verifier import ProjectVerifier, VerificationResult


def test_graphify_structural_integration():
    """Verify actual Graphify JSON output structure is parsed and integrated into ZeroDepGraph."""
    graphify_data = {
        "nodes": [
            {"id": "service.py", "type": "file", "label": "service.py"},
            {"id": "auth.py", "type": "file", "label": "auth.py"},
            {"id": "requests", "type": "package", "label": "requests"},
            {"id": "login_user", "type": "function", "label": "login_user"},
        ],
        "edges": [
            {"source": "service.py", "target": "auth.py", "type": "imports"},
            {"source": "auth.py", "target": "requests", "type": "calls"},
            {"source": "service.py", "target": "login_user", "type": "contains"},
        ],
    }

    g = ZeroDepGraph()
    g.add_node("main.py")
    g.add_edge("main.py", "service.py")

    assert g.edge_count() == 1
    edges_added = g.integrate_graphify_data(graphify_data)

    assert edges_added == 3
    assert g.edge_count() == 4
    assert g.has_node("requests")
    assert g.has_edge("auth.py", "requests")
    assert g.has_edge("service.py", "auth.py")
    
    # Test BFS reachability across merged graph
    reachable = g.bfs("main.py")
    assert "service.py" in reachable
    assert "auth.py" in reachable
    assert "requests" in reachable


def test_repair_loop_successful_first_try(tmp_path):
    """When the initial patch passes verification, 0 repair attempts are made."""
    test_file = tmp_path / "app.py"
    test_file.write_text("x = 42\n", encoding="utf-8")

    loop = MigrationRepairLoop()
    result = loop.run_repair_loop(
        source_dir=tmp_path,
        package="dummy",
        initial_patches={"app.py": "x = 42\ny = 100\n"},
    )

    assert result.status == "SUCCESS"
    assert result.total_attempts == 0
    assert result.initial_result.status == "PASS"


def test_repair_loop_repairable_failure(tmp_path):
    """Initial patch causes syntax error or missing execution; repair loop fixes it and passes."""
    code_file = tmp_path / "cli.py"
    code_file.write_text("import click\n", encoding="utf-8")

    # Initial broken patch has a syntax error
    broken_patch = "def main():\n    sys.exit(0)\n\ndef broken_func(\n"

    loop = MigrationRepairLoop()

    class MockRepairAI:
        def repair_patch(self, pkg, patches, failure):
            return {
                "success": True,
                "patches": {"cli.py": "import sys\n\ndef main():\n    sys.exit(0)\n"},
                "summary": "Fixed unclosed parenthesis syntax error and added sys import",
            }

    loop.ai_adapter = MockRepairAI()
    result = loop.run_repair_loop(
        source_dir=tmp_path,
        package="click",
        initial_patches={"cli.py": broken_patch},
    )

    assert result.status == "SUCCESS"
    assert result.total_attempts == 1
    assert result.initial_result.status == "FAIL"
    assert result.final_result.status == "PASS"
    assert "import sys" in result.final_patches["cli.py"]


def test_repair_loop_unrepairable_failure(tmp_path):
    """Fundamentally unsupported external feature halts with explicit failure."""
    code_file = tmp_path / "worker.py"
    code_file.write_text("import celery\n", encoding="utf-8")

    # A patch that produces syntax failure
    broken_patch = "def fail(\n"

    loop = MigrationRepairLoop()

    class UnrepairableAI:
        def repair_patch(self, pkg, patches, failure):
            return {
                "success": False,
                "error": "Failure indicates fundamentally unrepairable external dependency feature.",
            }

    loop.ai_adapter = UnrepairableAI()
    result = loop.run_repair_loop(
        source_dir=tmp_path,
        package="celery",
        initial_patches={"worker.py": broken_patch},
    )

    assert result.status in ("FAILED", "MAX_ATTEMPTS_EXCEEDED")
    assert result.final_result.status == "FAIL"


def test_repair_loop_unsafe_path_rejection(tmp_path):
    """Repair loop rejects directory traversal attacks in proposed patches."""
    code_file = tmp_path / "safe.py"
    code_file.write_text("a = 1\n", encoding="utf-8")

    loop = MigrationRepairLoop()

    class MaliciousAIAdapter:
        def repair_patch(self, pkg, patches, failure):
            return {
                "success": True,
                "patches": {"../../etc/passwd": "malicious content"},
                "summary": "Exploit attempt",
            }

    loop.ai_adapter = MaliciousAIAdapter()
    
    # Broken patch to trigger repair
    broken_patch = "def fail_syntax(\n"

    result = loop.run_repair_loop(
        source_dir=tmp_path,
        package="exploit",
        initial_patches={"safe.py": broken_patch},
    )

    assert result.status == "FAILED"
    assert "Security violation" in result.summary


def test_repair_loop_max_attempts_enforced(tmp_path):
    """Repair loop strictly enforces maximum 2 repair attempts."""
    code_file = tmp_path / "broken.py"
    code_file.write_text("x = 1\n", encoding="utf-8")

    loop = MigrationRepairLoop()

    class PerpetualBrokenAdapter:
        def repair_patch(self, pkg, patches, failure):
            return {
                "success": True,
                "patches": {"broken.py": "def still_syntax_error(\n"},
                "summary": "Still broken",
            }

    loop.ai_adapter = PerpetualBrokenAdapter()

    broken_patch = "def initial_syntax_error(\n"
    result = loop.run_repair_loop(
        source_dir=tmp_path,
        package="never_fix",
        initial_patches={"broken.py": broken_patch},
    )

    assert result.status == "MAX_ATTEMPTS_EXCEEDED"
    assert result.total_attempts == 2
    assert len(result.history) == 2


def test_repair_loop_approval_denial(tmp_path):
    """If developer denies approval on revised repair diff, loop immediately halts."""
    code_file = tmp_path / "lib.py"
    code_file.write_text("z = 10\n", encoding="utf-8")

    loop = MigrationRepairLoop()

    class MockRepairAI:
        def repair_patch(self, pkg, patches, failure):
            return {
                "success": True,
                "patches": {"lib.py": "z = 20\n"},
                "summary": "Fix syntax",
            }

    loop.ai_adapter = MockRepairAI()

    def decline_callback(attempt: RepairAttempt) -> bool:
        return False

    broken_patch = "def broken_code(\n"
    result = loop.run_repair_loop(
        source_dir=tmp_path,
        package="test_dep",
        initial_patches={"lib.py": broken_patch},
        approval_callback=decline_callback,
    )

    assert result.status == "APPROVAL_DENIED"
    assert result.total_attempts == 1
    assert "declined" in result.summary.lower()
