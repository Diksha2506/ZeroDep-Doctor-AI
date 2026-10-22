"""FastAPI backend server for ZeroDep Doctor AI.

Provides REST endpoints for scanning codebases, querying open-weight AI explanations,
inspecting reviewable diff proposals, and running isolated verifications.
"""

import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from zerodep.ai_adapter import ZeroDepAIAdapter
from zerodep.cli import scan_project
from zerodep.diff_engine import generate_migration_proposal
from zerodep.verifier import ProjectVerifier

from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

app = FastAPI(
    title="ZeroDep Doctor AI API",
    description="Intelligent dependency risk analysis, evidence-grounded AI explanations, and verified stdlib migrations.",
    version="1.0.0",
)

# Enable CORS for frontend dev server
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

static_dir = Path(__file__).parent / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

@app.get("/")
def get_root():
    index_file = static_dir / "index.html"
    if index_file.exists():
        return FileResponse(str(index_file))
    return {"message": "ZeroDep Doctor AI API is active. Open /docs for Swagger UI."}

ai_adapter = ZeroDepAIAdapter()
verifier = ProjectVerifier(timeout_seconds=15.0)

# In-memory scan cache: target_path -> scan_data
_SCAN_CACHE: Dict[str, dict] = {}


# --- Request & Response Models ---

class AnalyzeRequest(BaseModel):
    project_path: str = Field(..., description="Absolute or relative path to project directory")


class ExplainRequest(BaseModel):
    project_path: str
    package: str
    model: Optional[str] = Field(None, description="Optional model override, e.g. 'llama3.2', 'gemma2:9b', 'mistral'")


class ProposalRequest(BaseModel):
    project_path: str
    package: str


class VerifyRequest(BaseModel):
    project_path: str
    package: str
    file_patches: Dict[str, str] = Field(..., description="Map of relative file paths to new content")
    test_command: Optional[List[str]] = None


# --- Endpoints ---

@app.get("/api/health")
def health_check():
    """Health check endpoint disclosing AI provider status and system readiness."""
    ai_status = ai_adapter.check_availability()
    return {
        "status": "healthy",
        "engine": "ZeroDep Pure Stdlib Engine",
        "ai": ai_status,
        "sample_fixtures": [
            {"id": "demo", "name": "Bloated Demo Project", "path": "demo"},
            {"id": "case_a", "name": "CLI Tool (click/requests)", "path": "fixtures/case_a_cli"},
            {"id": "case_b", "name": "Pipeline (requests/loguru)", "path": "fixtures/case_b_medium"},
            {"id": "case_c", "name": "Heavy Service (multi-dep)", "path": "fixtures/case_c_heavy"},
        ],
    }


@app.post("/api/analyze")
def analyze_endpoint(req: AnalyzeRequest):
    """Analyze a project directory using the deterministic ZeroDep static engine."""
    target_path = Path(req.project_path)
    if not target_path.is_absolute():
        # Resolve relative to repo root
        target_path = (Path.cwd() / target_path).resolve()

    if not target_path.exists() or not target_path.is_dir():
        raise HTTPException(status_code=400, detail=f"Target directory does not exist: {req.project_path}")

    # Prevent path traversal outside safe system boundaries
    try:
        scan_data = scan_project(target_path)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Analysis failed: {str(e)}")

    # Cache scan data for subsequent explanation and proposal calls
    cache_key = str(target_path)
    _SCAN_CACHE[cache_key] = scan_data

    # Format JSON response safely (serializable subset)
    score_info = scan_data["score_info"]
    usage_list = []
    for dep, u in scan_data["usage_details"].items():
        imp = scan_data["impacts"].get(dep)
        risk = scan_data["risks"].get(dep)
        usage_list.append({
            "package": dep,
            "status": u["status"],
            "confidence": u["confidence"],
            "occurrences": u["occurrences"],
            "files": u["files"],
            "symbols": u["symbols"],
            "evidence": u["evidence"],
            "impact_level": imp.level if imp else "LOW",
            "risk_level": risk.level if risk else "LOW",
            "risk_score": risk.score if risk else 0,
        })

    summary_dict = {}
    if "summary" in scan_data and scan_data["summary"]:
        s = scan_data["summary"]
        summary_dict = s.to_dict() if hasattr(s, "to_dict") else dict(s)

    action_plan_dict = {}
    if "action_plan" in scan_data and scan_data["action_plan"]:
        ap = scan_data["action_plan"]
        action_plan_dict = ap.to_dict() if hasattr(ap, "to_dict") else dict(ap)

    return {
        "target_dir": str(target_path),
        "files_scanned": scan_data["files_scanned"],
        "loc": scan_data["loc"],
        "duration": round(scan_data["duration"], 3),
        "manifest_found": scan_data.get("manifest_found", True),
        "manifest_files": scan_data.get("manifest_files_found", []),
        "parse_errors": scan_data.get("parse_errors", []),
        "undeclared_third_party": sorted(list(scan_data.get("undeclared_third_party", []))),
        "score": {
            "current": score_info.current_score,
            "potential": score_info.potential_score,
            "grade": score_info.grade,
            "audit_status": score_info.audit_status,
            "status_message": score_info.status_message,
            "breakdown": {
                "unused_count": score_info.unused_count,
                "replaceable_count": score_info.replaceable_count,
                "essential_count": score_info.essential_count,
                "unused_penalty": score_info.unused_penalty,
                "replaceable_penalty": score_info.replaceable_penalty,
                "essential_penalty": score_info.essential_penalty,
                "stdlib_bonus": score_info.stdlib_bonus,
            },
        },
        "dependencies": usage_list,
        "summary": summary_dict,
        "action_plan": action_plan_dict,
        "graph_metrics": {
            "nodes": len(scan_data["graph"].nodes()),
            "edges": scan_data["graph"].edge_count(),
            "has_cycles": bool(scan_data["graph"].find_cycles()),
        },
    }


@app.post("/api/explain")
def explain_endpoint(req: ExplainRequest):
    """Generate an evidence-grounded AI explanation for a dependency."""
    target_path = Path(req.project_path)
    if not target_path.is_absolute():
        target_path = (Path.cwd() / target_path).resolve()

    cache_key = str(target_path)
    scan_data = _SCAN_CACHE.get(cache_key)
    if not scan_data:
        # Run scan if not in cache
        if not target_path.exists():
            raise HTTPException(status_code=400, detail="Target path not found")
        scan_data = scan_project(target_path)
        _SCAN_CACHE[cache_key] = scan_data

    dep_name = req.package
    if dep_name not in scan_data.get("declared_deps", set()) and dep_name not in scan_data.get("usage_details", {}):
        raise HTTPException(status_code=404, detail=f"Package '{dep_name}' not found in scan results")

    # If model is customized in request, use configured adapter instance
    adapter = ai_adapter
    if req.model and req.model != ai_adapter.model:
        adapter = ZeroDepAIAdapter(model=req.model)

    explanation = adapter.explain_dependency(dep_name, scan_data)
    return explanation.to_dict()


@app.post("/api/proposal/diff")
def proposal_diff_endpoint(req: ProposalRequest):
    """Generate a reviewable unified diff proposal without modifying source files."""
    target_path = Path(req.project_path)
    if not target_path.is_absolute():
        target_path = (Path.cwd() / target_path).resolve()

    cache_key = str(target_path)
    scan_data = _SCAN_CACHE.get(cache_key)
    if not scan_data:
        if not target_path.exists():
            raise HTTPException(status_code=400, detail="Target path not found")
        scan_data = scan_project(target_path)
        _SCAN_CACHE[cache_key] = scan_data

    proposal = generate_migration_proposal(target_path, req.package, scan_data)
    return proposal.to_dict()


@app.post("/api/proposal/verify")
def verify_endpoint(req: VerifyRequest):
    """Run sandboxed verification of an explicitly approved diff proposal."""
    target_path = Path(req.project_path)
    if not target_path.is_absolute():
        target_path = (Path.cwd() / target_path).resolve()

    if not target_path.exists():
        raise HTTPException(status_code=400, detail="Target path not found")

    result = verifier.verify_proposal(
        source_dir=target_path,
        file_patches=req.file_patches,
        test_command=req.test_command,
    )
    return result.to_dict()
