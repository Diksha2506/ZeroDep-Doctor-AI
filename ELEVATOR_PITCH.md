# ZeroDep Doctor — Elevator Pitch (60 Seconds)

## Narrative Timeline

```text
[0:00 - 0:10] The Problem: Unaware Dependency Creep
[0:10 - 0:25] The Core Insight: AST-level Usage Analysis
[0:25 - 0:40] The Solution & Key Differentiators
[0:40 - 0:55] Zero-Dependency Craft & Technical Proof
[0:55 - 1:00] Closing Impact Statement
```

---

## 60-Second Script

> **"Modern Python applications accumulate dependencies silently.** Developers add third-party packages for simple HTTP calls, CLI flags, or data tables — often unaware that Python's standard library already contains equivalent tools.
> 
> Existing scanners like `pipdeptree` or `deptry` only tell you what is installed or missing. **ZeroDep Doctor** asks a deeper question: **What does your codebase actually use, what can be safely removed, and where can Python's standard library replace external dependencies?**
> 
> By walking Python's Abstract Syntax Tree statically without executing code, ZeroDep Doctor tracks aliased symbols, function scope boundaries, and call sites. It generates human-reviewable stdlib migration diffs with usage-aware confidence ratings. It even replaces heavy external graph libraries like NetworkX with a native graph engine built entirely from standard library primitives.
> 
> True to our mission, **ZeroDep Doctor itself has ZERO third-party runtime dependencies**. It compiles into a single 72 KB standalone executable with a byte-identical reproducible build.
> 
> ZeroDep Doctor helps developers trim supply chain bloat and unlock the hidden power of Python's standard library."
