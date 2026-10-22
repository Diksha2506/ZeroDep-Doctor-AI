"""Main CLI interface, project scanner, and report runner for ZeroDep Doctor.

Commands:
  analyze      — full audit report
  dependencies — dependency analysis only
  graph        — dependency graph + cycle detection
  replacements — migration suggestions only
  report       — JSON machine-readable output

Zero third-party runtime dependencies.
"""

import argparse
import json
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from zerodep.manifest_parser import ManifestDependency, parse_manifests
from zerodep.ast_visitor import ASTAnalysisResult, analyze_ast
from zerodep.graph_engine import ZeroDepGraph
from zerodep.migration_engine import RECIPES, get_recipe
from zerodep.scoring_engine import calculate_score
from zerodep.cli_formatter import ANSIFormatter
from zerodep.impact_engine import calculate_dependency_impact
from zerodep.risk_engine import calculate_dependency_risk, build_risk_impact_matrix
from zerodep.explanation_engine import generate_all_explanations

try:
    from zerodep.action_planner import generate_action_plan
except ImportError:
    try:
        from action_planner import generate_action_plan  # type: ignore
    except ImportError:
        generate_action_plan = globals().get("generate_action_plan")  # type: ignore

try:
    from zerodep.simulation_engine import simulate_migration
except ImportError:
    try:
        from simulation_engine import simulate_migration  # type: ignore
    except ImportError:
        simulate_migration = globals().get("simulate_migration")  # type: ignore

try:
    from zerodep.summary_engine import generate_decision_summary
except ImportError:
    try:
        from summary_engine import generate_decision_summary  # type: ignore
    except ImportError:
        generate_decision_summary = globals().get("generate_decision_summary")  # type: ignore

# ──────────────────────────────────────────────
# Directories to skip during scanning
# ──────────────────────────────────────────────
_SKIP_DIRS: frozenset = frozenset({
    ".git", ".hg", ".svn", ".tox", ".mypy_cache", ".pytest_cache",
    ".ruff_cache", "__pycache__", "venv", "env", ".venv", ".env",
    "build", "dist", "node_modules", ".idea", ".vscode", "site-packages",
    "eggs", "*.egg-info",
})


def _should_skip(path: Path, root: Path) -> bool:
    """Return True if any path component indicates a directory to ignore."""
    try:
        parts = path.relative_to(root).parts
    except ValueError:
        return True
    return any(
        p in _SKIP_DIRS or p.startswith(".") or p.endswith(".egg-info")
        for p in parts
    )


# ──────────────────────────────────────────────
# Core scanner
# ──────────────────────────────────────────────

def scan_project(target_dir: Path) -> dict:
    """
    Scan a Python project directory and return a rich analysis result dict.
    Never executes any analyzed code.
    """
    start_time = time.perf_counter()
    manifest_result = parse_manifests(target_dir)

    ast_results: List[ASTAnalysisResult] = []
    total_loc = 0
    total_stdlib_calls = 0
    total_calls = 0
    graph = ZeroDepGraph()

    # ── File discovery + AST analysis ──────────────────────
    parse_errors: List[str] = []
    for py_file in target_dir.rglob("*.py"):
        if _should_skip(py_file, target_dir):
            continue
        # Safety: skip symlinks that escape the project root
        try:
            resolved_file = py_file.resolve()
            resolved_file.relative_to(target_dir.resolve())
        except (ValueError, OSError):
            continue

        try:
            raw_bytes = py_file.read_bytes()
            # Strip UTF-8 BOM (U+FEFF) if present — prevents SyntaxError
            if raw_bytes.startswith(b"\xef\xbb\xbf"):
                raw_bytes = raw_bytes[3:]
            content = raw_bytes.decode("utf-8", errors="ignore")
        except (OSError, PermissionError):
            continue

        total_loc += content.count("\n") + 1
        rel_path = str(py_file.relative_to(target_dir))
        res = analyze_ast(rel_path, content)
        if res.has_syntax_error:
            parse_errors.append(f"{rel_path}: {res.error_message}")
        ast_results.append(res)

    # Pre-index file stems and imports/call sites in O(N)
    imports_by_mod: Dict[str, List[Tuple[str, ImportBinding]]] = defaultdict(list)
    call_sites_by_mod: Dict[str, List[CallSite]] = defaultdict(list)
    stem_to_path: Dict[str, str] = {}

    for res in ast_results:
        stem_to_path[Path(res.file_path).stem] = res.file_path
        for imp in res.imports:
            mod_base = imp.original_module.split(".")[0].lower()
            imports_by_mod[mod_base].append((res.file_path, imp))
            if imp.is_stdlib:
                total_stdlib_calls += 1
            total_calls += 1
        for cs in res.call_sites:
            mod_base = cs.original_module.split(".")[0].lower()
            call_sites_by_mod[mod_base].append(cs)

    # Build graph edges in O(N) using stem index
    for res in ast_results:
        for imp in res.imports:
            mod_base = imp.original_module
            if mod_base in stem_to_path:
                graph.add_edge(res.file_path, stem_to_path[mod_base])
            else:
                graph.add_edge(res.file_path, mod_base)

    # ── Match used packages to declared dependencies ────────
    decl_by_import: Dict[str, ManifestDependency] = {}
    decl_by_pypi: Dict[str, ManifestDependency] = {}
    for dep in manifest_result.dependencies:
        decl_by_import[dep.import_name.lower()] = dep
        decl_by_pypi[dep.name.lower()] = dep

    runtime_declared: Set[str] = {
        d.name for d in manifest_result.dependencies if d.scope == "runtime"
    }
    all_declared: Set[str] = {d.name for d in manifest_result.dependencies}
    manifest_found: bool = bool(manifest_result.manifest_files_found)

    used_deps: Set[str] = set()
    # Imports that don't match any declared dependency (potential undeclared third-party)
    unresolved_imports: Set[str] = set()
    for res in ast_results:
        for imp in res.imports:
            if imp.is_stdlib or imp.is_relative:
                continue
            mod_base = imp.original_module.split(".")[0].lower()
            if mod_base in decl_by_import:
                used_deps.add(decl_by_import[mod_base].name)
            elif mod_base in decl_by_pypi:
                used_deps.add(decl_by_pypi[mod_base].name)
            else:
                # Not in stdlib, not in declared deps — could be undeclared third-party
                # or a local module. We record for scoring but cannot be certain.
                unresolved_imports.add(mod_base)

    replaceable_set: Set[str] = set(RECIPES.keys())

    # Build detailed per-dependency usage dictionary in O(D)
    dep_usage_details: Dict[str, dict] = {}
    for dep in sorted(all_declared):
        dep_imports = []
        dep_call_sites = []

        match_import_names = set()
        for d in manifest_result.dependencies:
            if d.name.lower() == dep.lower():
                match_import_names.add(d.import_name.lower())
                match_import_names.add(d.name.lower())

        for name in match_import_names:
            dep_imports.extend(imports_by_mod.get(name, []))
            dep_call_sites.extend(call_sites_by_mod.get(name, []))

        files_used = sorted({cs.file_path for cs in dep_call_sites} | {path for path, _ in dep_imports})
        symbol_counts: Dict[str, int] = {}
        for cs in dep_call_sites:
            symbol_counts[cs.resolved_symbol] = symbol_counts.get(cs.resolved_symbol, 0) + 1

        status = "UNUSED"
        if dep in used_deps:
            status = "REPLACEABLE" if dep in replaceable_set else "ESSENTIAL"
        elif dep_imports:
            status = "IMPORTED_BUT_UNUSED"

        # Compute explainable confidence & evidence
        evidence = []
        if dep_imports:
            has_direct = any(imp.scope_kind == "module" and not imp.is_conditional for _, imp in dep_imports)
            has_cond = any(imp.is_conditional or imp.scope_kind == "conditional" for _, imp in dep_imports)
            has_nested = any(imp.scope_kind in ("function", "class") for _, imp in dep_imports)
            has_star = any(imp.is_star for _, imp in dep_imports)
            
            if has_direct:
                evidence.append("[OK] Direct static import at module level")
            if has_cond:
                evidence.append("[!] Conditional / try-except import guard")
            if has_nested:
                evidence.append("[!] Scope-nested import inside function or class")
            if has_star:
                evidence.append("[!] Star import (from pkg import *) detected")

        if dep_call_sites:
            evidence.append(f"[OK] {len(dep_call_sites)} static call site reference(s)")
            symbols_list = list(symbol_counts.keys())
            if len(symbols_list) == 1:
                evidence.append(f"[OK] Isolated symbol usage ({symbols_list[0]})")
            else:
                evidence.append(f"[OK] Multi-symbol API usage ({len(symbols_list)} distinct functions)")
        else:
            evidence.append("[X] 0 call site references detected in codebase AST")

        # Confidence rating
        if status in ("UNUSED", "IMPORTED_BUT_UNUSED"):
            confidence = "HIGH" if not any(imp.is_star for _, imp in dep_imports) else "MEDIUM"
        elif any(imp.is_conditional or imp.is_star for _, imp in dep_imports):
            confidence = "MEDIUM"
        elif any(cs.confidence_label == "LOW" for cs in dep_call_sites):
            confidence = "LOW"
        elif dep_call_sites:
            confidence = "HIGH"
        else:
            confidence = "UNKNOWN"

        # For REPLACEABLE deps, let the migration recipe override confidence
        # (e.g. requests.Session => LOW even though static call sites are HIGH)
        if status == "REPLACEABLE":
            recipe = get_recipe(dep, symbol_counts)
            if recipe:
                confidence = recipe.confidence

        dep_usage_details[dep] = {
            "status": status,
            "confidence": confidence,
            "evidence": evidence,
            "files": files_used,
            "occurrences": len(dep_call_sites),
            "symbols": symbol_counts,
            "imports_count": len(dep_imports),
        }

    # Narrow unresolved imports to likely third-party (not local module names)
    # by filtering out names that appear as file stems or top-level package dirs in this project
    local_stems: Set[str] = set()
    for r in ast_results:
        p = Path(r.file_path)
        local_stems.add(p.stem.lower())
        for part in p.parts[:-1]:
            local_stems.add(part.lower())
    undeclared_third_party: Set[str] = {
        m for m in unresolved_imports if m not in local_stems
    }

    score_info = calculate_score(
        declared_deps=runtime_declared if runtime_declared else all_declared,
        used_deps=used_deps,
        replaceable_deps=replaceable_set,
        total_stdlib_calls=total_stdlib_calls,
        total_calls=total_calls,
        undeclared_third_party_deps=undeclared_third_party,
        files_scanned=len(ast_results),
        manifest_found=manifest_found,
    )

    duration = time.perf_counter() - start_time

    scan_data = {
        "target_dir": str(target_dir),
        "files_scanned": len(ast_results),
        "loc": total_loc,
        "duration": duration,
        "manifest_dependencies": manifest_result.dependencies,
        "manifest_files_found": manifest_result.manifest_files_found,
        "manifest_found": manifest_found,
        "declared_deps": runtime_declared if runtime_declared else all_declared,
        "used_deps": used_deps,
        "unresolved_imports": unresolved_imports,
        "undeclared_third_party": undeclared_third_party,
        "parse_errors": parse_errors,
        "usage_details": dep_usage_details,
        "ast_results": ast_results,
        "score_info": score_info,
        "graph": graph,
        "dynamic_imports": [
            di for res in ast_results for di in res.dynamic_imports
        ],
        "star_imports": [
            mod for res in ast_results for mod in res.star_imports
        ],
    }

    # Compute Dependency Impact Analysis per declared dependency
    impacts = {}
    for dep in sorted(scan_data["declared_deps"]):
        impacts[dep] = calculate_dependency_impact(dep, scan_data)
    scan_data["impacts"] = impacts

    # Compute Dependency Risk Model per declared dependency
    risks = {}
    for dep in sorted(scan_data["declared_deps"]):
        risks[dep] = calculate_dependency_risk(dep, scan_data)
    scan_data["risks"] = risks

    # Generate Dependency Explanations (Phase 3)
    scan_data["explanations"] = generate_all_explanations(scan_data)

    # Generate Dependency Action Plan (Phase 4)
    if generate_action_plan:
        scan_data["action_plan"] = generate_action_plan(scan_data)

    # Generate Dependency Migration Simulation (Phase 5)
    if simulate_migration:
        scan_data["simulation"] = simulate_migration(scan_data, mode="recommended")

    # Generate Dependency Decision Summary (Phase 6)
    if generate_decision_summary:
        scan_data["summary"] = generate_decision_summary(scan_data)

    return scan_data


# ──────────────────────────────────────────────
# Formatters
# ──────────────────────────────────────────────

def _print_analyze(scan: dict, fmt: ANSIFormatter) -> None:
    print(fmt.header("ZERODEP DOCTOR - AUDIT REPORT"))
    print(f"Target Path:    {scan['target_dir']}")
    print(f"Files Scanned:  {scan['files_scanned']} | Lines of Code: {scan['loc']}")
    print(f"Execution Time: {scan['duration']:.2f}s (Pure Stdlib Engine)")

    _print_score(scan, fmt)
    _print_dependencies(scan, fmt)
    _print_risk_matrix(scan, fmt)
    _print_action_plan(scan, fmt)
    _print_warnings(scan, fmt)
    _print_migrations(scan, fmt)
    _print_graph(scan, fmt)
    print("=" * 80)


def _print_score(scan: dict, fmt: ANSIFormatter) -> None:
    s = scan["score_info"]
    print(fmt.subheader("ZERODEP HEALTH INDEX"))
    print(f" Current Score:   {fmt.bold(str(s.current_score))} / 100  [GRADE: {fmt.green(s.grade)}]")
    print(f" Potential Score: {fmt.bold(str(s.potential_score))} / 100 (if all suggestions applied)")
    print("\n Dependency Breakdown:")
    print(f"   Unused:        {s.unused_count:<3} (-{s.unused_penalty:.1f} pts penalty -> Action: Remove from manifest)")
    print(f"   Replaceable:   {s.replaceable_count:<3} (-{s.replaceable_penalty:.1f} pts penalty -> Action: Migrate to stdlib)")
    print(f"   Essential:     {s.essential_count:<3} (-{s.essential_penalty:.1f} pts penalty -> Action: Retain external pkg)")
    print(f"   Stdlib Bonus:  +{s.stdlib_bonus:.1f} pts (ratio of stdlib calls in project AST)")
    print("\n Potential Improvement Opportunities:")
    print(f"   Remove unused:       +{s.unused_penalty:.1f} pts potential score recovery")
    print(f"   Migrate replaceable: +{s.replaceable_penalty:.1f} pts potential score recovery")


def _print_dependencies(scan: dict, fmt: ANSIFormatter, verbose: bool = False) -> None:
    print(fmt.subheader("DEPENDENCY ANALYSIS & CONFIDENCE MODEL"))
    details = scan.get("usage_details", {})
    explanations = scan.get("explanations", {})
    if not scan["declared_deps"]:
        print("  No runtime dependencies declared in manifests.")
        return

    for dep in sorted(scan["declared_deps"]):
        info = details.get(dep, {})
        status = info.get("status", "UNKNOWN")
        conf = info.get("confidence", "HIGH")
        conf_color = fmt.green if conf == "HIGH" else (fmt.yellow if conf == "MEDIUM" else fmt.red)
        
        exp = explanations.get(dep)

        if status == "UNUSED":
            print(f"\n {fmt.red('[UNUSED]')}            {fmt.bold(dep)}  [Confidence: {conf_color(conf)}]")
            print(f"   Purpose:      {exp.purpose if exp else 'No active usage detected'}")
            print("   Status:       Declared in manifest, 0 AST references (safe to remove)")
        elif status == "IMPORTED_BUT_UNUSED":
            print(f"\n {fmt.yellow('[IMPORTED_UNUSED]')}   {fmt.bold(dep)}  [Confidence: {conf_color(conf)}]")
            print(f"   Purpose:      {exp.purpose if exp else 'Import statement present but 0 call sites'}")
            print("   Status:       Import statement present, but 0 active function/class call sites")
            print(f"   Files:        {', '.join(info.get('files', []))}")
        elif status == "REPLACEABLE":
            recipe = get_recipe(dep, info.get("symbols"))
            suggested = recipe.stdlib_module if recipe else "Standard Library"
            eff_conf = recipe.confidence if recipe else conf
            eff_color = fmt.green if eff_conf == "HIGH" else (fmt.yellow if eff_conf == "MEDIUM" else fmt.red)
            print(f"\n {fmt.yellow('[REPLACEABLE]')}      {fmt.bold(dep)}  [Confidence: {eff_color(eff_conf)}]")
            print(f"   Purpose:      {exp.purpose if exp else 'Active AST call sites'}")
            print(f"   Status:       Active AST call sites -> Migrate to {fmt.cyan(suggested)}")
            if recipe:
                print(f"   Reason:       {recipe.why_replaceable}")
            print(f"   Files:        {', '.join(info.get('files', []))}")
            print(f"   Occurrences:  {info.get('occurrences', 0)}")
            if info.get("symbols"):
                sym_str = ", ".join(f"{s} ({c})" for s, c in info["symbols"].items())
                print(f"   Symbols Used: {sym_str}")
        else: # ESSENTIAL
            print(f"\n {fmt.green('[ESSENTIAL]')}        {fmt.bold(dep)}  [Confidence: {conf_color(conf)}]")
            print(f"   Purpose:      {exp.purpose if exp else 'Necessary external dependency'}")
            print("   Status:       Necessary external dependency (no stdlib equivalent)")
            print(f"   Files:        {', '.join(info.get('files', []))}")
            print(f"   Occurrences:  {info.get('occurrences', 0)}")
            if info.get("symbols"):
                sym_str = ", ".join(f"{s} ({c})" for s, c in info["symbols"].items())
                print(f"   Symbols Used: {sym_str}")

        if info.get("evidence"):
            print("   Evidence:")
            for item in info["evidence"]:
                print(f"     {item}")

        # Display Impact Analysis
        impacts = scan.get("impacts", {})
        if dep in impacts:
            imp = impacts[dep]
            imp_color = fmt.green if imp.level == "LOW" else (fmt.yellow if imp.level == "MEDIUM" else fmt.red)
            print(f"   Impact Level: {imp_color(imp.level)} (Effort: {imp.estimated_migration_effort})")

        # Display Risk Analysis & Explanation Summary
        risks = scan.get("risks", {})
        if dep in risks:
            risk = risks[dep]
            risk_color = fmt.green if risk.level == "LOW" else (fmt.yellow if risk.level == "MEDIUM" else fmt.red)
            rec_color = fmt.green if risk.recommendation in ("REMOVE", "SAFE MIGRATION CANDIDATE") else (
                fmt.yellow if risk.recommendation in ("REVIEW MIGRATION", "KEEP / NO ACTION") else fmt.red
            )
            print(f"   Risk Level:   {risk_color(risk.level)} (Score: {risk.score}/100)")
            print(f"   Recommended:  {rec_color(risk.recommendation)}")
            if risk.risk_factors:
                print("   Risk Factors:")
                for rf in risk.risk_factors:
                    sev_color = fmt.red if rf.severity == "HIGH" else (fmt.yellow if rf.severity == "MED" else fmt.green)
                    print(f"     [{sev_color(rf.severity)}] {rf.description}")
            if risk.warnings:
                for w in risk.warnings:
                    print(f"   {fmt.yellow('[WARN]')} {w}")

        if exp and (verbose or exp.reasons):
            print("   Why:")
            for r in exp.reasons:
                print(f"     • {r}")
            if verbose and exp.replacement_limitations:
                print("   Limitations:")
                for lim in exp.replacement_limitations:
                    print(f"     ! {lim}")


def _print_risk_matrix(scan: dict, fmt: ANSIFormatter) -> None:
    """Print the 3x3 Risk vs Impact matrix populated with actual dependencies."""
    impacts = scan.get("impacts", {})
    risks = scan.get("risks", {})
    if not impacts or not risks:
        return

    matrix = build_risk_impact_matrix(impacts, risks)
    print(fmt.subheader("DEPENDENCY RISK vs IMPACT MATRIX"))
    levels = ["LOW", "MEDIUM", "HIGH"]
    col_w = 22
    # Header
    print(f"  {'':20}  {'IMPACT':<{col_w}}  {'IMPACT':<{col_w}}  {'IMPACT':<{col_w}}")
    print(f"  {'':20}  {'LOW':<{col_w}}  {'MEDIUM':<{col_w}}  {'HIGH':<{col_w}}")
    print(f"  {'-'*20}  {'-'*col_w}  {'-'*col_w}  {'-'*col_w}")
    for r_lvl in levels:
        r_color = fmt.green if r_lvl == "LOW" else (fmt.yellow if r_lvl == "MEDIUM" else fmt.red)
        row_label = f"Risk {r_lvl}"
        cells = []
        for i_lvl in levels:
            names = matrix[r_lvl][i_lvl]
            cell_str = ", ".join(names) if names else "(none)"
            if len(cell_str) > col_w:
                cell_str = cell_str[:col_w - 3] + "..."
            cells.append(f"{cell_str:<{col_w}}")
        print(f"  {r_color(row_label):<28}  {'  '.join(cells)}")
    print()


def _print_warnings(scan: dict, fmt: ANSIFormatter) -> None:
    dynamic = scan.get("dynamic_imports", [])
    stars = scan.get("star_imports", [])
    if not dynamic and not stars:
        return
    print(fmt.subheader("ANALYSIS WARNINGS"))
    if dynamic:
        print(f" {fmt.yellow('[DYNAMIC IMPORTS]')} {len(dynamic)} dynamic import(s) detected.")
        print("   These cannot be resolved statically. Manual audit required.")
        for di in dynamic[:5]:
            print(f"     {di.file_path}:{di.line_no}  ({di.kind})")
    if stars:
        unique_stars = sorted(set(stars))
        print(f" {fmt.yellow('[STAR IMPORTS]')} from {', '.join(unique_stars)} import *")
        print("   Star imports hide exact symbol usage. Dependency usage may be underreported.")


def _print_action_plan(scan: dict, fmt: ANSIFormatter) -> None:
    action_plan = scan.get("action_plan")
    if not action_plan:
        return

    print(fmt.subheader("DEPENDENCY ACTION PLAN  [PRIORITIZED MIGRATION SEQUENCE]"))
    items = action_plan.items
    if not items:
        print("  No dependencies detected in project.")
        print()
        return

    # Group items by priority label
    by_priority: Dict[str, list] = defaultdict(list)
    for item in items:
        by_priority[item.priority_label].append(item)

    for p_label in ["P0 — BLOCKING", "P1 — HIGH", "P2 — MEDIUM", "P3 — LOW"]:
        group = by_priority.get(p_label, [])
        p_color = fmt.red if "P0" in p_label or "P1" in p_label else (fmt.yellow if "P2" in p_label else fmt.green)
        print(f" {p_color(p_label)}")
        if not group:
            print("   (none)")
        else:
            for idx, item in enumerate(group, 1):
                act_color = fmt.red if item.action in ("REMOVE", "MANUAL REVIEW") else (
                    fmt.yellow if item.action in ("REVIEW MIGRATION", "INVESTIGATE") else fmt.green
                )
                print(f"   {idx}. {fmt.bold(item.package)}  [{act_color(item.action)}]")
                print(f"      Reason: {item.reason}")
                print(f"      Effort: {item.effort} | Human Review: {item.human_review_type}")
                if item.blocking_factors:
                    print(f"      Blockers: {', '.join(item.blocking_factors)}")

    print(f"\n {fmt.bold('NEXT BEST ACTION:')}")
    print(f"   {fmt.green(action_plan.next_best_action)}")
    print()


def _print_simulation(sim, fmt: ANSIFormatter) -> None:
    if not sim:
        return
    print(fmt.subheader("DEPENDENCY MIGRATION SIMULATION  ['WHAT-IF' HYPOTHETICAL ANALYSIS]"))
    print(f" Mode:                   {fmt.bold(sim.mode.upper())}")
    conf_color = fmt.green if sim.confidence == "HIGH" else (fmt.yellow if sim.confidence == "MEDIUM" else fmt.red)
    print(f" Simulation Confidence:  {conf_color(sim.confidence)}")
    print(f" Current Dependencies:   {sim.current_count}")
    print(f" Projected Dependencies: {fmt.bold(str(sim.projected_count))}")
    print(f" Potential Reduction:    {fmt.green(f'{sim.reduction_count} / {sim.current_count} ({sim.reduction_percentage:.1f}%)')}")
    print()

    print(fmt.subheader("BEFORE / AFTER FOOTPRINT"))
    print(f"  {'BEFORE':<25}  {'AFTER (SIMULATED)':<25}")
    print(f"  {'-'*25}  {'-'*25}")

    after_map = {item["package"]: item["simulated_state"] for item in sim.after_footprint}
    for item in sim.before_footprint:
        pkg = item["package"]
        st = item["status"]
        after_st = after_map.get(pkg, "UNCHANGED")

        before_str = f"{pkg} [{st}]"
        after_color = fmt.green if after_st in ("REMOVE", "MIGRATE") else (fmt.yellow if after_st in ("MANUAL REVIEW", "INVESTIGATE") else fmt.bold)
        after_str = f"{pkg} [{after_st}]"
        print(f"  {before_str:<25}  {after_color(after_str):<25}")
    print()

    if sim.assumptions:
        print(fmt.bold("  MIGRATION ASSUMPTIONS (HYPOTHETICAL):"))
        for pkg, asm in sim.assumptions.items():
            print(f"    • {fmt.bold(pkg)}: {asm}")
        print()

    if sim.blockers:
        print(fmt.bold(fmt.yellow("  REMAINING BLOCKERS / UNCERTAINTIES:")))
        for blk in sim.blockers:
            print(f"    ! {blk['package']}: {blk['reason']}")
        print()


def _print_summary(summary, fmt: ANSIFormatter) -> None:
    if not summary:
        return
    print(fmt.subheader("DEPENDENCY PORTFOLIO"))
    print(f"Total Dependencies:       {summary.total_dependencies}")
    print(f"Unused:                   {summary.unused_count}")
    print(f"Replaceable:              {summary.replaceable_count}")
    print(f"Essential:                {summary.essential_count}")
    print(f"High Risk:                {summary.high_risk_count}")
    print(f"High Impact:              {summary.high_impact_count}")
    print()

    print(fmt.subheader("MIGRATION READINESS"))
    readiness_color = fmt.green if summary.migration_readiness == "READY" else (
        fmt.yellow if summary.migration_readiness in ("PARTIALLY READY", "REVIEW REQUIRED") else fmt.red
    )
    print(f"Status: {readiness_color(summary.migration_readiness)}")
    print()
    if summary.readiness_reasons:
        print("Reasons:")
        for r in summary.readiness_reasons:
            print(f"  • {r}")
    print()

    print(fmt.subheader("TOP PRIORITIES"))
    if not summary.top_priority_dependencies:
        print("  (none)")
    else:
        for idx, item in enumerate(summary.top_priority_dependencies, 1):
            act_color = fmt.red if item.action in ("REMOVE", "MANUAL REVIEW") else (
                fmt.yellow if item.action in ("REVIEW MIGRATION", "INVESTIGATE") else fmt.green
            )
            print(f"{idx}. {fmt.bold(item.package)}")
            print(f"   Impact: {item.impact}")
            print(f"   Risk: {item.risk}")
            print(f"   Confidence: {item.confidence}")
            print(f"   Effort: {item.effort}")
            print(f"   Action: {act_color(item.action)}")
            print()

    print(fmt.subheader("WHAT-IF SUMMARY"))
    print(f"Current Dependencies:     {summary.current_dependency_count}")
    print(f"Projected Dependencies:   {fmt.bold(str(summary.projected_dependency_count))}")
    print(f"Potential Reduction:      {fmt.green(f'{summary.potential_reduction_count} / {summary.current_dependency_count} ({summary.potential_reduction_percentage:.2f}%)')}")
    print()

    print(fmt.subheader("DECISION MATRIX"))
    print("                     LOW RISK        MEDIUM RISK       HIGH RISK")
    matrix = summary.decision_matrix
    for imp_lvl in ["LOW IMPACT", "MEDIUM IMPACT", "HIGH IMPACT"]:
        row = matrix.get(imp_lvl, {})
        low_act = row.get("LOW RISK", "")
        med_act = row.get("MEDIUM RISK", "")
        high_act = row.get("HIGH RISK", "")
        print(f"{imp_lvl:<21}{low_act:<16}{med_act:<18}{high_act}")
    print()

    if summary.blockers:
        print(fmt.subheader("BLOCKERS / UNCERTAINTIES"))
        for blk in summary.blockers:
            print(fmt.yellow(f"! {blk}"))
        print()

    print(fmt.subheader("NEXT BEST ACTION"))
    print(fmt.green(summary.recommended_next_action))
    print()


def _print_migrations(scan: dict, fmt: ANSIFormatter) -> None:
    print(fmt.subheader("SUGGESTED MIGRATION DIFFS  [REVIEWABLE SUGGESTIONS]"))
    found = False
    details = scan.get("usage_details", {})
    for dep in sorted(scan["used_deps"]):
        info = details.get(dep, {})
        recipe = get_recipe(dep, info.get("symbols"))
        if recipe:
            found = True
            conf_color = fmt.green if recipe.confidence == "HIGH" else (fmt.yellow if recipe.confidence == "MEDIUM" else fmt.red)
            print(f"\n  Package:      {fmt.bold(recipe.package_name)}")
            print(f"  Target Stdlib:{fmt.cyan(recipe.stdlib_module)}")
            print(f"  Confidence:   {conf_color(recipe.confidence)}")
            print(f"  Reason:       {recipe.why_replaceable}")
            print(f"  Limitations:  {recipe.limitations}")
            print("\n  REVIEWABLE DIFF SUGGESTION (NOT AUTOMATICALLY APPLIED):")
            print(fmt.red(recipe.before_code))
            print(fmt.green(recipe.after_code))
            print()
    if not found:
        print("  No replaceable package call sites detected.")


def _print_graph(scan: dict, fmt: ANSIFormatter) -> None:
    g: ZeroDepGraph = scan["graph"]
    print(fmt.subheader("GRAPH ANALYSIS  (ZeroDep Graph Engine — NetworkX replacement)"))
    cycles = g.find_cycles()
    if cycles:
        print(f" {fmt.red('[CIRCULAR IMPORTS DETECTED]')} {len(cycles)} cycle(s)")
        for cyc in cycles[:5]:
            formatted_cycle = " -> ".join(cyc)
            print(f"   Circular Dependency:\n     {formatted_cycle}")
    else:
        print(f" {fmt.green('[OK]')} Circular Imports: NONE DETECTED")

    topo = g.topological_sort()
    print(f" Topological Sort:        {'OK — valid DAG' if topo else fmt.yellow('N/A — cycles present')}")
    print(f" Total Module Graph Nodes:{len(g.nodes())}")
    
    depth = g.max_dependency_depth()
    if depth > 0:
        print(f" Max Dependency Depth:    {depth}")
    
    comps = g.weakly_connected_components()
    print(f" Connected Components:   {len(comps)}")

    hubs = g.top_hub_nodes(3)
    if hubs and any(c > 0 for _, c in hubs):
        print(" Most Connected Hub Modules:")
        for node, count in hubs:
            if count > 0:
                print(f"   {node:<25} ({count} dependents)")


def _json_report(scan: dict) -> dict:
    """Build a deterministic, schema-compliant JSON report."""
    s = scan["score_info"]
    g: ZeroDepGraph = scan["graph"]
    details = scan.get("usage_details", {})
    unused = scan["declared_deps"] - scan["used_deps"]

    migrations = []
    for dep in sorted(scan["used_deps"]):
        info = details.get(dep, {})
        recipe = get_recipe(dep, info.get("symbols"))
        if recipe:
            migrations.append({
                "package": recipe.package_name,
                "stdlib_module": recipe.stdlib_module,
                "confidence": recipe.confidence,
                "reason": recipe.why_replaceable,
                "limitations": recipe.limitations,
                "before_code": recipe.before_code,
                "after_code": recipe.after_code,
            })

    usage_list = []
    for dep in sorted(scan["declared_deps"]):
        info = details.get(dep, {})
        usage_list.append({
            "package": dep,
            "status": info.get("status", "UNKNOWN"),
            "confidence": info.get("confidence", "UNKNOWN"),
            "occurrences": info.get("occurrences", 0),
            "files": info.get("files", []),
            "symbols": info.get("symbols", {}),
            "evidence": info.get("evidence", []),
        })

    impact_dict = {}
    impacts = scan.get("impacts", {})
    for dep, imp in impacts.items():
        impact_dict[dep] = imp.to_dict()

    risk_dict = {}
    risks = scan.get("risks", {})
    for dep, risk in risks.items():
        risk_dict[dep] = risk.to_dict()

    explanation_dict = {}
    explanations = scan.get("explanations", {})
    for dep, exp in explanations.items():
        explanation_dict[dep] = exp.to_dict()

    return {
        "zerodep_doctor": "1.0",
        "project": {
            "path": scan["target_dir"],
            "files_scanned": scan["files_scanned"],
            "lines_of_code": scan["loc"],
            "execution_seconds": round(scan["duration"], 4),
        },
        "dependencies": {
            "declared": sorted(scan["declared_deps"]),
            "used": sorted(scan["used_deps"]),
            "unused": sorted(unused),
            "replaceable": sorted(scan["used_deps"] & set(RECIPES.keys())),
        },
        "impact": impact_dict,
        "risk": risk_dict,
        "explanations": explanation_dict,
        "action_plan": scan["action_plan"].to_dict() if scan.get("action_plan") else {},
        "simulation": scan["simulation"].to_dict() if scan.get("simulation") else {},
        "summary": scan["summary"].to_dict() if scan.get("summary") else {},
        "usage": usage_list,
        "migrations": migrations,
        "graph": {
            "nodes": len(g.nodes()),
            "edges": g.edge_count(),
            "cycles_count": len(g.find_cycles()),
            "max_dependency_depth": g.max_dependency_depth(),
            "connected_components": len(g.weakly_connected_components()),
            "top_hubs": [{"module": m, "dependents": c} for m, c in g.top_hub_nodes(5) if c > 0],
        },
        "score": {
            "current": s.current_score,
            "potential": s.potential_score,
            "grade": s.grade,
            "breakdown": {
                "unused_penalty": s.unused_penalty,
                "replaceable_penalty": s.replaceable_penalty,
                "essential_penalty": s.essential_penalty,
                "stdlib_bonus": s.stdlib_bonus,
            },
            "potential_improvements": {
                "remove_unused_bonus": s.unused_penalty,
                "migrate_replaceable_bonus": s.replaceable_penalty,
            },
        },
        "warnings": {
            "dynamic_imports": [
                {
                    "file": di.file_path,
                    "line": di.line_no,
                    "kind": di.kind,
                    "target_module": di.target_module,
                }
                for di in scan.get("dynamic_imports", [])
            ],
            "star_imports": sorted(set(scan.get("star_imports", []))),
        },
    }


# ──────────────────────────────────────────────
# CLI entry point
# ──────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        prog="zerodep",
        description="ZeroDep Doctor — zero-dependency static analyzer & stdlib migration assistant",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Commands:\n"
            "  analyze      Full audit report\n"
            "  dependencies Dependency analysis only\n"
            "  graph        Dependency graph + cycle detection\n"
            "  replacements Migration suggestions only\n"
            "  plan         Prioritized action plan\n"
            "  simulate     'What-If' dependency migration simulator\n"
            "  summary      Executive dependency decision summary\n"
            "  report       Machine-readable JSON output\n\n"
            "Example:\n"
            "  zerodep analyze  ./my_project\n"
            "  zerodep plan     ./my_project\n"
            "  zerodep summary  ./my_project\n"
        ),
    )
    parser.add_argument(
        "command",
        choices=["analyze", "dependencies", "graph", "replacements", "plan", "simulate", "summary", "report"],
    )
    parser.add_argument("path", nargs="?", default=".", help="Target project path")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument("--verbose", "-v", action="store_true", help="Show detailed explanation evidence & limitations")
    parser.add_argument("--recommended", action="store_true", help="Simulate recommended Action Planner steps")
    parser.add_argument("--all-safe", action="store_true", help="Simulate all safe removals and high-confidence replacements")
    parser.add_argument("--targets", nargs="*", help="List specific dependencies to simulate removing/migrating")

    args = parser.parse_args()
    target_path = Path(args.path).resolve()

    if not target_path.exists():
        print(f"Error: path not found: {target_path}", file=sys.stderr)
        sys.exit(1)
    if not target_path.is_dir():
        print(f"Error: expected a directory, got: {target_path}", file=sys.stderr)
        sys.exit(1)

    scan = scan_project(target_path)
    fmt = ANSIFormatter()

    cmd = args.command

    # JSON mode (any command + --json, or report command)
    if args.json or cmd == "report":
        print(json.dumps(_json_report(scan), indent=2, sort_keys=False))
        sys.exit(0)

    if cmd == "analyze":
        _print_analyze(scan, fmt)
    elif cmd == "dependencies":
        _print_dependencies(scan, fmt, verbose=args.verbose)
    elif cmd == "graph":
        _print_graph(scan, fmt)
    elif cmd == "replacements":
        _print_migrations(scan, fmt)
    elif cmd == "plan":
        print(fmt.header("ZERODEP DOCTOR — MIGRATION ACTION PLAN"))
        _print_action_plan(scan, fmt)
    elif cmd == "simulate":
        print(fmt.header("ZERODEP DOCTOR — DEPENDENCY MIGRATION SIMULATOR"))
        mode = "recommended"
        targets = None
        if args.all_safe:
            mode = "all_safe"
        elif args.targets:
            mode = "selected"
            targets = set(args.targets)

        if simulate_migration:
            sim = simulate_migration(scan, mode=mode, targets=targets)
            _print_simulation(sim, fmt)
    elif cmd == "summary":
        print(fmt.header("ZERODEP DOCTOR — DECISION SUMMARY"))
        if generate_decision_summary:
            summary = scan.get("summary") or generate_decision_summary(scan)
            _print_summary(summary, fmt)

    sys.exit(0)


if __name__ == "__main__":
    main()
