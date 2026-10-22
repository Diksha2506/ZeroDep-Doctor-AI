"""Dependency Impact Engine — Calculates explainable impact metrics and classification for dependencies.

Answers: "If this dependency is removed or migrated, how much of my project is affected?"

Impact is interpreted as removal/migration impact (blast radius / effort), NOT universal quality.

Zero third-party dependencies.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple


@dataclass
class DependencyImpact:
    package: str
    level: str  # "LOW" | "MEDIUM" | "HIGH"
    files_affected: int
    modules_affected: int
    transitive_modules_affected: int
    import_locations: int
    call_sites: int
    symbols_count: int
    symbols_used: List[str]
    is_graph_hub: bool
    is_hub_connected: bool
    graph_connectivity: Dict[str, int]
    circular_dependency_involvement: bool
    api_complexity: str  # "LOW" | "MEDIUM" | "HIGH"
    migration_confidence: str  # "HIGH" | "MEDIUM" | "LOW"
    has_dynamic_imports: bool
    has_conditional_imports: bool
    has_star_imports: bool
    estimated_migration_effort: str  # "Trivial" | "Low" | "Moderate" | "High" | "Complex/Manual"
    reasons: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "level": self.level,
            "files_affected": self.files_affected,
            "modules_affected": self.modules_affected,
            "transitive_modules_affected": self.transitive_modules_affected,
            "import_locations": self.import_locations,
            "call_sites": self.call_sites,
            "symbols_count": self.symbols_count,
            "symbols_used": self.symbols_used,
            "is_graph_hub": self.is_graph_hub,
            "is_hub_connected": self.is_hub_connected,
            "graph_connectivity": self.graph_connectivity,
            "circular_dependency_involvement": self.circular_dependency_involvement,
            "api_complexity": self.api_complexity,
            "migration_confidence": self.migration_confidence,
            "has_dynamic_imports": self.has_dynamic_imports,
            "has_conditional_imports": self.has_conditional_imports,
            "has_star_imports": self.has_star_imports,
            "estimated_migration_effort": self.estimated_migration_effort,
            "reasons": self.reasons,
        }


def calculate_dependency_impact(dep_name: str, scan_data: dict) -> DependencyImpact:
    """
    Calculate explainable impact metrics for a declared dependency using existing AST and Graph scan results.
    Never rescans files or executes code.
    """
    usage_details = scan_data.get("usage_details", {})
    dep_info = usage_details.get(dep_name, {})
    graph = scan_data.get("graph")
    ast_results = scan_data.get("ast_results", [])
    
    files_used: List[str] = dep_info.get("files", [])
    files_affected_count = len(files_used)
    import_locations = dep_info.get("imports_count", 0)
    call_sites_count = dep_info.get("occurrences", 0)
    symbols_dict: Dict[str, int] = dep_info.get("symbols", {})
    symbols_used = sorted(symbols_dict.keys())
    symbols_count = len(symbols_used)
    migration_confidence = dep_info.get("confidence", "HIGH")
    status = dep_info.get("status", "UNKNOWN")

    # 1. Modules affected: direct files using this dependency
    direct_modules: Set[str] = set(files_used)
    modules_affected_count = len(direct_modules)

    # 2. Transitive reach: modules in project graph that depend on direct_modules
    transitive_affected: Set[str] = set(direct_modules)
    if graph:
        # Avoid running full BFS for every node on large graphs; find reachable nodes from direct_modules
        for mod in direct_modules:
            reachable = graph.bfs(mod)
            transitive_affected.update(reachable)
    transitive_modules_affected_count = len(transitive_affected)

    # 3. Hub analysis: direct hub vs hub-connected
    is_graph_hub = False
    is_hub_connected = False
    top_hubs: Set[str] = set()
    total_in_degree = 0
    total_out_degree = 0

    if graph:
        hub_tuples = graph.top_hub_nodes(k=5)
        top_hubs = {h[0] for h in hub_tuples if h[1] > 0}
        
        # Check if dependency itself (by import name or package name) is a hub node
        if graph.has_node(dep_name) and graph.in_degree(dep_name) >= 3:
            is_graph_hub = True

        for mod in direct_modules:
            if graph.has_node(mod):
                in_deg = graph.in_degree(mod)
                out_deg = graph.out_degree(mod)
                total_in_degree += in_deg
                total_out_degree += out_deg
                if mod in top_hubs or in_deg >= 3:
                    is_graph_hub = True
                # Check if connected to a top hub
                for nbr in graph.neighbors(mod):
                    if nbr in top_hubs:
                        is_hub_connected = True

    graph_connectivity = {
        "in_degree": total_in_degree,
        "out_degree": total_out_degree,
        "transitive_reach": transitive_modules_affected_count,
    }

    # 4. Circular dependency involvement
    circular_dependency_involvement = False
    if graph:
        cycles = graph.find_cycles()
        for cycle in cycles:
            cycle_set = set(cycle)
            if direct_modules.intersection(cycle_set):
                circular_dependency_involvement = True
                break

    # 5. Dynamic, Conditional, Star imports
    has_dynamic_imports = False
    has_conditional_imports = False
    has_star_imports = False

    # Inspect AST results for direct modules touch
    dep_lower = dep_name.lower()
    for res in ast_results:
        if res.file_path in direct_modules:
            for imp in res.imports:
                mod_base = imp.original_module.split(".")[0].lower()
                if mod_base == dep_lower:
                    if imp.is_conditional or imp.scope_kind == "conditional":
                        has_conditional_imports = True
                    if imp.is_star:
                        has_star_imports = True
            for di in res.dynamic_imports:
                # Only attribute a dynamic import to this dependency when:
                # - the static target explicitly names this dependency (di.target_module == dep_name), OR
                # - the target is unknown (None) AND the import is inside a file that directly uses
                #   this dep — but in that case we still do NOT attribute it to a specific dep
                #   because we cannot be certain.  We therefore only flag it when target is explicit.
                if di.target_module is not None and di.target_module.lower() == dep_lower:
                    has_dynamic_imports = True

    # 6. API Complexity
    # Base heuristic: symbol count + specific complex features (e.g. Session, context managers)
    if symbols_count == 0:
        api_complexity = "LOW"
    elif symbols_count <= 2 and not any("Session" in s for s in symbols_used):
        api_complexity = "LOW"
    elif symbols_count <= 5 and not any("Session" in s for s in symbols_used):
        api_complexity = "MEDIUM"
    else:
        api_complexity = "HIGH"

    # 7. Classification Level & Reasons (Explainable Impact Formula)
    reasons: List[str] = []
    points = 0

    if status == "UNUSED":
        level = "LOW"
        estimated_migration_effort = "Trivial"
        reasons.append("Unused dependency — declared in manifest but 0 AST references")
    else:
        # File & Module scale
        if files_affected_count >= 4:
            points += 3
            reasons.append(f"Used across {files_affected_count} files ({transitive_modules_affected_count} transitive modules)")
        elif files_affected_count > 1:
            points += 2
            reasons.append(f"Used in {files_affected_count} files")
        else:
            points += 1
            reasons.append("Localized to a single file")

        # Call sites
        if call_sites_count >= 15:
            points += 3
            reasons.append(f"High call-site count ({call_sites_count} references)")
        elif call_sites_count >= 5:
            points += 2
            reasons.append(f"Moderate call-site count ({call_sites_count} references)")
        else:
            points += 1
            reasons.append(f"Low call-site count ({call_sites_count} references)")

        # Hub role
        if is_graph_hub:
            points += 3
            reasons.append("Hub dependency — heavily imported by core modules")
        elif is_hub_connected:
            points += 1
            reasons.append("Connected to central dependency graph hub modules")

        # API Complexity & Confidence
        if api_complexity == "HIGH":
            points += 2
            reasons.append("Complex API usage detected (multiple symbols / stateful clients)")
        if migration_confidence == "LOW":
            points += 3
            reasons.append("LOW migration confidence (requires manual non-trivial refactoring)")

        # Risk multipliers
        if circular_dependency_involvement:
            points += 2
            reasons.append("Involved in circular dependency loops")
        if has_dynamic_imports:
            points += 2
            reasons.append("Dynamic import usage detected (cannot be statically resolved)")
        if has_conditional_imports:
            points += 1
            reasons.append("Conditional import guards present")
        if has_star_imports:
            points += 1
            reasons.append("Star imports detected (obscures exact symbol usage)")

        # Classification decision
        if points >= 8 or migration_confidence == "LOW" and points >= 6 or is_graph_hub and points >= 6:
            level = "HIGH"
        elif points >= 4:
            level = "MEDIUM"
        else:
            level = "LOW"

        # Estimated effort mapping
        if level == "LOW":
            estimated_migration_effort = "Low" if call_sites_count > 0 else "Trivial"
        elif level == "MEDIUM":
            estimated_migration_effort = "Moderate"
        else:
            estimated_migration_effort = "Complex/Manual" if migration_confidence == "LOW" else "High"

    return DependencyImpact(
        package=dep_name,
        level=level,
        files_affected=files_affected_count,
        modules_affected=modules_affected_count,
        transitive_modules_affected=transitive_modules_affected_count,
        import_locations=import_locations,
        call_sites=call_sites_count,
        symbols_count=symbols_count,
        symbols_used=symbols_used,
        is_graph_hub=is_graph_hub,
        is_hub_connected=is_hub_connected,
        graph_connectivity=graph_connectivity,
        circular_dependency_involvement=circular_dependency_involvement,
        api_complexity=api_complexity,
        migration_confidence=migration_confidence,
        has_dynamic_imports=has_dynamic_imports,
        has_conditional_imports=has_conditional_imports,
        has_star_imports=has_star_imports,
        estimated_migration_effort=estimated_migration_effort,
        reasons=reasons,
    )
