"""Open-weight AI Adapter for ZeroDep Doctor.

Connects to locally-served open-weight LLMs (e.g., Ollama running llama3.2, mistral, deepseek-r1)
via HTTP REST API. If the model or endpoint is unavailable, gracefully falls back to the
deterministic explanation engine without inventing facts.
"""

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class AIExplanationResponse:
    provider: str               # "ollama" | "fallback"
    model: str                  # e.g., "llama3.2:latest" or "deterministic-engine"
    is_fallback: bool
    package: str
    why_used: str
    evidence_summary: List[str]
    confidence: str
    benefits: List[str]
    trade_offs: List[str]
    risks: List[str]
    verification_checklist: List[str]
    raw_response: Optional[str] = None
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "provider": self.provider,
            "model": self.model,
            "is_fallback": self.is_fallback,
            "package": self.package,
            "why_used": self.why_used,
            "evidence_summary": self.evidence_summary,
            "confidence": self.confidence,
            "benefits": self.benefits,
            "trade_offs": self.trade_offs,
            "risks": self.risks,
            "verification_checklist": self.verification_checklist,
            "raw_response": self.raw_response,
            "error": self.error,
        }


class ZeroDepAIAdapter:
    """Grounds LLM reasoning in deterministic ZeroDep static analysis evidence."""

    def __init__(
        self,
        endpoint: Optional[str] = None,
        model: Optional[str] = None,
        timeout_seconds: float = 8.0,
    ):
        self.endpoint = (
            endpoint
            or os.environ.get("ZERODEP_OLLAMA_ENDPOINT")
            or "http://localhost:11434"
        ).rstrip("/")
        self.model = model or os.environ.get("ZERODEP_OLLAMA_MODEL") or "llama3.2"
        self.timeout_seconds = timeout_seconds

    def check_availability(self) -> Dict[str, Any]:
        """Check if local Ollama server is responsive and which models are installed."""
        import socket
        from urllib.parse import urlparse

        parsed = urlparse(self.endpoint)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or 11434

        # Fast socket probe (0.2s) to avoid hanging if daemon is offline
        try:
            with socket.create_connection((host, port), timeout=0.2):
                pass
        except Exception:
            return {
                "available": False,
                "endpoint": self.endpoint,
                "configured_model": self.model,
                "error": f"Ollama daemon not listening on {host}:{port}",
                "model_ready": False,
            }

        url = f"{self.endpoint}/api/tags"
        req = urllib.request.Request(url, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=1.0) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read().decode("utf-8"))
                    models = [m.get("name") for m in data.get("models", [])]
                    has_target = any(self.model in m for m in models)
                    return {
                        "available": True,
                        "endpoint": self.endpoint,
                        "configured_model": self.model,
                        "models_installed": models,
                        "model_ready": has_target,
                    }
        except Exception as e:
            return {
                "available": False,
                "endpoint": self.endpoint,
                "configured_model": self.model,
                "error": str(e),
                "model_ready": False,
            }
        return {
            "available": False,
            "endpoint": self.endpoint,
            "configured_model": self.model,
            "model_ready": False,
        }


    def explain_dependency(
        self,
        dep_name: str,
        scan_data: dict,
    ) -> AIExplanationResponse:
        """Produce an evidence-grounded explanation for a dependency.
        
        Uses local open-weight model if accessible, else falls back deterministically.
        """
        usage_info = scan_data.get("usage_details", {}).get(dep_name, {})
        impact_info = scan_data.get("impacts", {}).get(dep_name)
        risk_info = scan_data.get("risks", {}).get(dep_name)
        explanations = scan_data.get("explanations", {})
        det_exp = explanations.get(dep_name)

        # Baseline ground truth from engine
        status = usage_info.get("status", "UNKNOWN")
        confidence = usage_info.get("confidence", "UNKNOWN")
        files = usage_info.get("files", [])
        symbols = usage_info.get("symbols", {})
        evidence_list = usage_info.get("evidence", [])

        # Try Ollama if reachable
        avail = self.check_availability()
        if avail.get("available") and avail.get("model_ready"):
            try:
                return self._call_ollama(
                    dep_name=dep_name,
                    status=status,
                    confidence=confidence,
                    files=files,
                    symbols=symbols,
                    evidence_list=evidence_list,
                    det_exp=det_exp,
                    risk_info=risk_info,
                    impact_info=impact_info,
                )
            except Exception as ex:
                # Network or generation timeout -> fall through to deterministic fallback
                fallback = self._build_deterministic_fallback(
                    dep_name, status, confidence, files, symbols, evidence_list, det_exp, risk_info, impact_info
                )
                fallback.error = f"Ollama connection/generation error: {ex}"
                return fallback

        # Deterministic fallback
        err_msg = avail.get("error") if not avail.get("available") else f"Model '{self.model}' not pulled in Ollama."
        fallback = self._build_deterministic_fallback(
            dep_name, status, confidence, files, symbols, evidence_list, det_exp, risk_info, impact_info
        )
        fallback.error = err_msg
        return fallback

    def _call_ollama(
        self,
        dep_name: str,
        status: str,
        confidence: str,
        files: List[str],
        symbols: Dict[str, int],
        evidence_list: List[str],
        det_exp: Any,
        risk_info: Any,
        impact_info: Any,
    ) -> AIExplanationResponse:
        """Query Ollama with a strict JSON-enforced system and evidence prompt."""
        prompt = f"""You are ZeroDep Doctor AI, an expert code analysis assistant.
Analyze the following dependency based STRICTLY on the AST and manifest evidence provided below.
DO NOT fabricate file paths, call sites, or external vulnerabilities.

DEPENDENCY: {dep_name}
CLASSIFICATION: {status}
ENGINE CONFIDENCE: {confidence}
DETECTED FILES: {files}
AST SYMBOLS USED: {json.dumps(symbols)}
STATIC EVIDENCE: {json.dumps(evidence_list)}
RISK LEVEL: {getattr(risk_info, 'risk_level', 'UNKNOWN') if risk_info else 'UNKNOWN'} (Score: {getattr(risk_info, 'risk_score', 0) if risk_info else 0})
IMPACT LEVEL: {getattr(impact_info, 'impact_level', 'UNKNOWN') if impact_info else 'UNKNOWN'}
RECOMMENDED ACTION: {getattr(det_exp, 'recommendation', 'REVIEW') if det_exp else 'REVIEW'}
REPLACEMENT: {getattr(det_exp, 'replacement_opportunity', 'None') if det_exp else 'None'}
KNOWN LIMITATIONS: {getattr(det_exp, 'replacement_limitations', []) if det_exp else []}

Respond ONLY with a valid JSON object matching this schema:
{{
  "why_used": "1-2 sentences on why this dependency is used based on the symbols",
  "evidence_summary": ["bullet 1", "bullet 2"],
  "benefits": ["benefit 1 of removing or migrating"],
  "trade_offs": ["trade-off or migration consideration"],
  "risks": ["concrete technical risks if migrated"],
  "verification_checklist": ["step 1 to verify before approving"]
}}
"""
        req_data = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "format": "json",
            "options": {
                "temperature": 0.1,
            },
        }

        url = f"{self.endpoint}/api/generate"
        req = urllib.request.Request(
            url,
            data=json.dumps(req_data).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            raw_text = data.get("response", "{}")
            try:
                parsed = json.loads(raw_text)
            except Exception as parse_err:
                fallback = self._build_deterministic_fallback(
                    dep_name, status, confidence, files, symbols, evidence_list, det_exp, risk_info, impact_info
                )
                fallback.error = f"Model returned unparseable output: {parse_err}"
                fallback.raw_response = raw_text
                return fallback

            return AIExplanationResponse(
                provider="ollama",
                model=self.model,
                is_fallback=False,
                package=dep_name,
                why_used=parsed.get("why_used", f"Used for {dep_name} functionality."),
                evidence_summary=parsed.get("evidence_summary", evidence_list),
                confidence=confidence,
                benefits=parsed.get("benefits", ["Reduces external supply-chain surface"]),
                trade_offs=parsed.get("trade_offs", ["Requires refactoring call sites to stdlib"]),
                risks=parsed.get("risks", ["API behavioral edge cases"]),
                verification_checklist=parsed.get("verification_checklist", ["Run test suite after migration"]),
                raw_response=raw_text,
            )

    def _build_deterministic_fallback(
        self,
        dep_name: str,
        status: str,
        confidence: str,
        files: List[str],
        symbols: Dict[str, int],
        evidence_list: List[str],
        det_exp: Any,
        risk_info: Any,
        impact_info: Any,
    ) -> AIExplanationResponse:
        """Deterministic grounding when local open-weight model is unavailable."""
        purpose = getattr(det_exp, "purpose", f"Third-party library {dep_name}") if det_exp else f"Third-party library {dep_name}"
        why_used = f"Package '{dep_name}' provides {purpose}."
        if symbols:
            why_used += f" Call sites invoke: {', '.join(sorted(symbols.keys()))}."
        elif status == "UNUSED":
            why_used = f"Declared in manifest but no call sites or import statements were detected across project AST."

        benefits = []
        if status in ("UNUSED", "IMPORTED_BUT_UNUSED"):
            benefits.append(f"Immediate zero-risk removal from manifest eliminates unused supply-chain exposure.")
        else:
            opp = getattr(det_exp, "replacement_opportunity", "stdlib") if det_exp else "stdlib"
            benefits.append(f"Standard-library migration to {opp} eliminates third-party dependency.")
            benefits.append("Reduces installation footprint and wheel building overhead.")

        trade_offs = []
        limits = getattr(det_exp, "replacement_limitations", []) if det_exp else []
        if limits:
            for lim in limits:
                trade_offs.append(f"Limitation: {lim}")
        else:
            trade_offs.append("Stdlib APIs may require more verbose boilerplate than third-party helpers.")

        risks = []
        risk_factors = getattr(risk_info, "factors", []) if risk_info else []
        if risk_factors:
            for rf in risk_factors:
                risks.append(f"{rf.name} (+{rf.score_contribution} pts): {rf.description}")
        else:
            risks.append("Ensure automated test suite covers all modified code paths before deployment.")

        checklist = [
            f"Review AST evidence in files: {', '.join(files) if files else 'manifest only'}",
            "Verify all unit tests pass before applying code changes",
            "Run post-migration isolated verification tests",
        ]

        return AIExplanationResponse(
            provider="fallback",
            model="deterministic-rules-engine",
            is_fallback=True,
            package=dep_name,
            why_used=why_used,
            evidence_summary=evidence_list or ["No static call sites detected"],
            confidence=confidence,
            benefits=benefits,
            trade_offs=trade_offs,
            risks=risks,
            verification_checklist=checklist,
        )
