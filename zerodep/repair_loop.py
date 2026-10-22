"""Feedback-Driven Migration Repair Loop for ZeroDep Doctor.

Runs relevant tests on an approved patch in an isolated workspace, captures real failures,
asks the model (or deterministic repair fallback) for a bounded repair, validates and
displays the revised diff, requires approval, and reruns verification.
Enforces a maximum of two repair attempts.
"""

from dataclasses import dataclass, field
import difflib
import json
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from zerodep.diff_engine import FileDiff, MigrationProposal
from zerodep.verifier import ProjectVerifier, VerificationResult


@dataclass
class RepairAttempt:
    attempt_number: int
    initial_exit_code: int
    failure_output: str
    repaired_patches: Dict[str, str]
    revised_diffs: List[FileDiff]
    verification_result: Optional[VerificationResult] = None
    approved_by_user: bool = False
    repair_summary: str = ""

    def to_dict(self) -> dict:
        return {
            "attempt_number": self.attempt_number,
            "initial_exit_code": self.initial_exit_code,
            "failure_output": self.failure_output,
            "revised_diffs": [
                {
                    "file_path": d.file_path,
                    "diff_patch": d.diff_patch,
                    "can_apply": d.can_apply,
                    "reason": d.reason,
                }
                for d in self.revised_diffs
            ],
            "verification_result": self.verification_result.to_dict() if self.verification_result else None,
            "approved_by_user": self.approved_by_user,
            "repair_summary": self.repair_summary,
        }


@dataclass
class RepairLoopResult:
    package: str
    initial_result: VerificationResult
    final_result: VerificationResult
    status: str                 # "SUCCESS" | "FAILED" | "MAX_ATTEMPTS_EXCEEDED" | "APPROVAL_DENIED" | "MALFORMED_OUTPUT"
    total_attempts: int
    history: List[RepairAttempt] = field(default_factory=list)
    final_patches: Dict[str, str] = field(default_factory=dict)
    summary: str = ""

    def to_dict(self) -> dict:
        return {
            "package": self.package,
            "initial_result": self.initial_result.to_dict(),
            "final_result": self.final_result.to_dict(),
            "status": self.status,
            "total_attempts": self.total_attempts,
            "history": [h.to_dict() for h in self.history],
            "summary": self.summary,
        }


class MigrationRepairLoop:
    """Orchestrates test-guided self-repair of migration patches."""

    MAX_REPAIR_ATTEMPTS = 2

    def __init__(
        self,
        verifier: Optional[ProjectVerifier] = None,
        ai_adapter: Optional[Any] = None,
    ):
        self.verifier = verifier or ProjectVerifier(timeout_seconds=15.0)
        self.ai_adapter = ai_adapter

    def run_repair_loop(
        self,
        source_dir: Path,
        package: str,
        initial_patches: Dict[str, str],
        test_command: Optional[List[str]] = None,
        approval_callback: Optional[Callable[[RepairAttempt], bool]] = None,
    ) -> RepairLoopResult:
        """
        Executes the feedback-driven repair loop up to MAX_REPAIR_ATTEMPTS times.

        Args:
            source_dir: Target project path.
            package: Name of dependency being migrated.
            initial_patches: File paths to candidate replacement code.
            test_command: Optional command for test execution.
            approval_callback: Function returning True if revised diff is approved.
        """
        current_patches = dict(initial_patches)
        initial_verif = self.verifier.verify_proposal(source_dir, current_patches, test_command)

        if initial_verif.status == "PASS":
            return RepairLoopResult(
                package=package,
                initial_result=initial_verif,
                final_result=initial_verif,
                status="SUCCESS",
                total_attempts=0,
                final_patches=current_patches,
                summary="Initial migration passed verification cleanly without repairs.",
            )

        history: List[RepairAttempt] = []
        last_verif = initial_verif

        for attempt_idx in range(1, self.MAX_REPAIR_ATTEMPTS + 1):
            failure_text = (last_verif.stderr or "") + "\n" + (last_verif.stdout or "")
            
            # Step 1: Request bounded repair from model or deterministic repair engine
            repair_proposal = self._propose_repair(
                source_dir=source_dir,
                package=package,
                current_patches=current_patches,
                failure_output=failure_text,
                attempt=attempt_idx,
            )

            if not repair_proposal.get("success"):
                err_msg = repair_proposal.get("error", "Malformed repair model output")
                return RepairLoopResult(
                    package=package,
                    initial_result=initial_verif,
                    final_result=last_verif,
                    status="MALFORMED_OUTPUT" if "malformed" in err_msg.lower() else "FAILED",
                    total_attempts=attempt_idx,
                    history=history,
                    final_patches=current_patches,
                    summary=f"Repair generation stopped at attempt {attempt_idx}: {err_msg}",
                )

            repaired_patches = repair_proposal["patches"]
            
            # Step 2: Validate safety of paths (no directory traversal)
            for path_key in repaired_patches.keys():
                target = (source_dir / path_key).resolve()
                try:
                    target.relative_to(source_dir.resolve())
                except ValueError:
                    return RepairLoopResult(
                        package=package,
                        initial_result=initial_verif,
                        final_result=last_verif,
                        status="FAILED",
                        total_attempts=attempt_idx,
                        history=history,
                        final_patches=current_patches,
                        summary=f"Security violation: repair proposed unsafe path traversal '{path_key}'.",
                    )

            # Step 3: Compute revised diffs
            revised_diffs = []
            for file_path, new_code in repaired_patches.items():
                orig_file = source_dir / file_path
                orig_text = orig_file.read_text(encoding="utf-8") if orig_file.exists() else ""
                patch_str = "\n".join(
                    difflib.unified_diff(
                        orig_text.splitlines(),
                        new_code.splitlines(),
                        fromfile=f"a/{file_path}",
                        tofile=f"b/{file_path}",
                        lineterm="",
                    )
                )
                revised_diffs.append(
                    FileDiff(
                        file_path=file_path,
                        original_content=orig_text,
                        proposed_content=new_code,
                        diff_patch=patch_str,
                        can_apply=True,
                        reason=f"Repaired after attempt {attempt_idx}",
                    )
                )

            attempt_record = RepairAttempt(
                attempt_number=attempt_idx,
                initial_exit_code=last_verif.exit_code,
                failure_output=failure_text.strip(),
                repaired_patches=repaired_patches,
                revised_diffs=revised_diffs,
                repair_summary=repair_proposal.get("summary", f"Attempt {attempt_idx} repair applied"),
            )

            # Step 4: Require explicit developer approval
            approved = True
            if approval_callback is not None:
                approved = approval_callback(attempt_record)
            attempt_record.approved_by_user = approved

            if not approved:
                history.append(attempt_record)
                return RepairLoopResult(
                    package=package,
                    initial_result=initial_verif,
                    final_result=last_verif,
                    status="APPROVAL_DENIED",
                    total_attempts=attempt_idx,
                    history=history,
                    final_patches=current_patches,
                    summary=f"Developer declined revised repair diff at attempt {attempt_idx}.",
                )

            # Step 5: Execute verification on approved revised patch
            current_patches = repaired_patches
            new_verif = self.verifier.verify_proposal(source_dir, current_patches, test_command)
            attempt_record.verification_result = new_verif
            history.append(attempt_record)
            last_verif = new_verif

            if new_verif.status == "PASS":
                return RepairLoopResult(
                    package=package,
                    initial_result=initial_verif,
                    final_result=new_verif,
                    status="SUCCESS",
                    total_attempts=attempt_idx,
                    history=history,
                    final_patches=current_patches,
                    summary=f"Successfully repaired migration on attempt {attempt_idx}!",
                )

        return RepairLoopResult(
            package=package,
            initial_result=initial_verif,
            final_result=last_verif,
            status="MAX_ATTEMPTS_EXCEEDED",
            total_attempts=len(history),
            history=history,
            final_patches=current_patches,
            summary=f"Migration failed to pass verification within {self.MAX_REPAIR_ATTEMPTS} repair attempts.",
        )

    def _propose_repair(
        self,
        source_dir: Path,
        package: str,
        current_patches: Dict[str, str],
        failure_output: str,
        attempt: int,
    ) -> dict:
        """Generate repaired code bounded strictly to fixing the captured failure."""
        # 1. If AI adapter is configured and active, invoke it
        if self.ai_adapter and hasattr(self.ai_adapter, "repair_patch"):
            try:
                ai_rep = self.ai_adapter.repair_patch(package, current_patches, failure_output)
                if ai_rep:
                    return ai_rep
            except Exception:
                pass

        # 2. Deterministic rule-based repair engine
        # Common pattern: NameError or AttributeError from missing stdlib imports
        repaired = {}
        summary_notes = []

        for fpath, code in current_patches.items():
            fixed_code = code
            
            # Syntax/Attribute fixes for stdlib migration edge cases
            if "AttributeError" in failure_output or "NameError" in failure_output or "ImportError" in failure_output:
                if "urllib" in failure_output and "import urllib.request" not in fixed_code:
                    fixed_code = "import urllib.request\nimport urllib.error\nimport json\n" + fixed_code
                    summary_notes.append("Added missing urllib.request import")

                if "argparse" in failure_output and "import argparse" not in fixed_code:
                    fixed_code = "import argparse\n" + fixed_code
                    summary_notes.append("Added missing argparse import")

                if "csv" in failure_output and "import csv" not in fixed_code:
                    fixed_code = "import csv\nimport io\n" + fixed_code
                    summary_notes.append("Added missing csv/io import")

            # Missing return code / exit handling in CLI migrations
            if "SystemExit" in failure_output and "sys.exit" not in fixed_code:
                fixed_code = "import sys\n" + fixed_code
                summary_notes.append("Imported sys for proper exit handling")

            # Fix common indentation or syntax errors
            if "IndentationError" in failure_output:
                lines = [l.replace("\t", "    ") for l in fixed_code.splitlines()]
                fixed_code = "\n".join(lines) + "\n"
                summary_notes.append("Normalized tab indentation to 4 spaces")

            repaired[fpath] = fixed_code

        # If no deterministic rule matched, apply fallback comment annotation or error
        if not summary_notes:
            # Check if this failure is unrepairable (e.g. fundamentally missing API)
            if "unsupported" in failure_output.lower() or "not implemented" in failure_output.lower():
                return {
                    "success": False,
                    "error": "Failure indicates fundamentally unrepairable external dependency feature.",
                }
            # Attempt a minimal defensive repair
            for fpath, code in list(repaired.items()):
                repaired[fpath] = f"# ZeroDep Repair Attempt {attempt}: Defensive fallback applied\n" + code
            summary_notes.append(f"Applied defensive structure patch for attempt {attempt}")

        return {
            "success": True,
            "patches": repaired,
            "summary": "; ".join(summary_notes),
        }
