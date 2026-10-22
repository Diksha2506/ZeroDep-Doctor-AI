"""Final audit trace — demo project vs spaceguard vs empty directory."""
import tempfile
from pathlib import Path

from zerodep.cli import scan_project


def trace_audit(label: str, path: Path) -> None:
    print("=" * 65)
    print(f"AUDIT: {label}")
    print("=" * 65)
    sg = scan_project(path)
    si = sg["score_info"]
    unused = sg["declared_deps"] - sg["used_deps"]
    print(f"Resolved path :     {path.resolve()}")
    print(f"Python files  :     {sg['files_scanned']}")
    print(f"Lines of code :     {sg['loc']}")
    print(f"Declared ({len(sg['declared_deps'])})  :  {sorted(sg['declared_deps'])}")
    print(f"Used     ({len(sg['used_deps'])})  :  {sorted(sg['used_deps'])}")
    print(f"Unused   ({len(unused)})  :  {sorted(unused)}")
    print()
    print("Score calculation:")
    penalty_total = si.unused_penalty + si.replaceable_penalty + si.essential_penalty + si.undeclared_penalty
    raw = 100.0 - penalty_total + si.stdlib_bonus
    print(f"  unused_count    = {si.unused_count:>3}  x 15 pts  => -{si.unused_penalty}")
    print(f"  replaceable     = {si.replaceable_count:>3}  x  8 pts  => -{si.replaceable_penalty}")
    print(f"  essential       = {si.essential_count:>3}  x  4 pts  => -{si.essential_penalty}")
    print(f"  undeclared      = {si.undeclared_count:>3}  x  6 pts  => -{si.undeclared_penalty}")
    print(f"  stdlib_bonus    =                    +{si.stdlib_bonus}")
    print(f"  raw             = 100 - {penalty_total} + {si.stdlib_bonus} = {raw:.1f}")
    print(f"  SCORE (clamped) = {si.current_score}  GRADE={si.grade}")
    print(f"  POTENTIAL       = {si.potential_score}")
    print(f"  audit_status    = {si.audit_status}")
    print(f"  status_msg      = {si.status_message}")
    print()


trace_audit("demo (Bloated Demo Project)", Path("demo"))
trace_audit("spaceguard (My_Project)", Path("C:/Users/diksh/OneDrive/Desktop/My_Project/spaceguard"))

# Empty directory guard
print("=" * 65)
print("AUDIT: Empty directory (must NOT receive perfect score)")
print("=" * 65)
with tempfile.TemporaryDirectory() as tmp:
    empty = scan_project(Path(tmp))
    si = empty["score_info"]
    print(f"files_scanned   : {empty['files_scanned']}")
    print(f"score           : {si.current_score}")
    print(f"grade           : {si.grade}")
    print(f"audit_status    : {si.audit_status}")
    print(f"status_message  : {si.status_message}")
    assert si.current_score == 0.0, f"Empty dir should score 0.0, got {si.current_score}"
    assert si.audit_status == "UNABLE_TO_ASSESS", f"Expected UNABLE_TO_ASSESS, got {si.audit_status}"
    print("[PASS] Empty project correctly blocked from receiving misleading perfect score")
