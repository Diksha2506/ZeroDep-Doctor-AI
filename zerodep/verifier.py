"""Verification engine for ZeroDep Doctor.

Executes controlled, isolated verification in a temporary workspace copy of the target project.
Never modifies original project files. Enforces timeouts and captures stdout, stderr, and exit codes.
"""

import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional


@dataclass
class VerificationResult:
    status: str             # "PASS" | "FAIL" | "TIMEOUT" | "NOT RUN" | "ERROR"
    exit_code: int
    command: str
    stdout: str
    stderr: str
    execution_time_seconds: float
    workspace_path: str
    diff_applied: bool
    summary: str

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "exit_code": self.exit_code,
            "command": self.command,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "execution_time_seconds": round(self.execution_time_seconds, 2),
            "workspace_path": self.workspace_path,
            "diff_applied": self.diff_applied,
            "summary": self.summary,
        }


class ProjectVerifier:
    """Manages sandboxed verification of proposed migrations in temporary copies."""

    def __init__(self, timeout_seconds: float = 15.0):
        self.timeout_seconds = timeout_seconds

    def verify_proposal(
        self,
        source_dir: Path,
        file_patches: Dict[str, str],
        test_command: Optional[List[str]] = None,
    ) -> VerificationResult:
        """Run verification in an isolated temporary clone of source_dir.
        
        Args:
            source_dir: Original project folder (preserved untouched).
            file_patches: Dictionary of relative_file_path -> new_content to apply.
            test_command: Command to execute (e.g., ["python", "-m", "pytest"] or ["python", "-m", "unittest"]).
        """
        if not source_dir.exists():
            return VerificationResult(
                status="ERROR",
                exit_code=-1,
                command="",
                stdout="",
                stderr=f"Source directory does not exist: {source_dir}",
                execution_time_seconds=0.0,
                workspace_path="",
                diff_applied=False,
                summary="Source directory not found",
            )

        temp_workspace = Path(tempfile.mkdtemp(prefix="zerodep_verify_"))
        try:
            # 1. Safely clone project directory (excluding venvs, .git, etc.)
            def _ignore_patterns(path, names):
                ignored = {".git", ".tox", ".pytest_cache", "__pycache__", "venv", ".venv", "env", "node_modules"}
                return [n for n in names if n in ignored]

            shutil.copytree(source_dir, temp_workspace / "project", ignore=_ignore_patterns)
            sandbox_dir = temp_workspace / "project"

            # 2. Apply proposed patches
            diff_applied = True
            for rel_path, new_content in file_patches.items():
                target_file = sandbox_dir / rel_path
                # Safety check against path traversal
                try:
                    target_file.resolve().relative_to(sandbox_dir.resolve())
                except ValueError:
                    diff_applied = False
                    return VerificationResult(
                        status="ERROR",
                        exit_code=-1,
                        command="",
                        stdout="",
                        stderr=f"Unsafe path traversal attempt rejected: {rel_path}",
                        execution_time_seconds=0.0,
                        workspace_path=str(sandbox_dir),
                        diff_applied=False,
                        summary="Path traversal security violation",
                    )
                target_file.parent.mkdir(parents=True, exist_ok=True)
                target_file.write_text(new_content, encoding="utf-8")

            # 3. Determine safe verification command
            if not test_command:
                # Look for tests in project
                if (sandbox_dir / "tests").exists() or any(sandbox_dir.glob("test_*.py")):
                    test_command = [sys.executable, "-m", "unittest", "discover", "-s", str(sandbox_dir)]
                else:
                    # Syntax & compile check on all python files
                    test_command = [sys.executable, "-m", "compileall", "-q", str(sandbox_dir)]

            # 4. Execute test command with timeout
            import time
            start_t = time.perf_counter()
            env = os.environ.copy()
            env["PYTHONPATH"] = str(sandbox_dir) + (os.pathsep + env.get("PYTHONPATH", ""))

            proc = subprocess.run(
                test_command,
                cwd=str(sandbox_dir),
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                env=env,
            )
            duration = time.perf_counter() - start_t

            status = "PASS" if proc.returncode == 0 else "FAIL"
            summary = (
                f"Verification command '{' '.join(test_command)}' exited with code {proc.returncode} ({status})."
            )

            return VerificationResult(
                status=status,
                exit_code=proc.returncode,
                command=" ".join(test_command),
                stdout=proc.stdout,
                stderr=proc.stderr,
                execution_time_seconds=duration,
                workspace_path=str(sandbox_dir),
                diff_applied=diff_applied,
                summary=summary,
            )

        except subprocess.TimeoutExpired as te:
            return VerificationResult(
                status="TIMEOUT",
                exit_code=-1,
                command=" ".join(test_command or []),
                stdout=te.stdout or "",
                stderr=f"Command timed out after {self.timeout_seconds}s.",
                execution_time_seconds=self.timeout_seconds,
                workspace_path=str(temp_workspace),
                diff_applied=True,
                summary="Execution timed out during verification",
            )
        except Exception as e:
            return VerificationResult(
                status="ERROR",
                exit_code=-1,
                command=" ".join(test_command or []),
                stdout="",
                stderr=str(e),
                execution_time_seconds=0.0,
                workspace_path=str(temp_workspace),
                diff_applied=False,
                summary=f"Verification runner error: {e}",
            )
        finally:
            # 5. Clean up temporary directory
            shutil.rmtree(temp_workspace, ignore_errors=True)
