# Protocol 3: Oriented Loopback

## Problem

Traditional agent pipelines use "fail → restart from beginning" (loopback to R1). This is wasteful:
- A CSS bug doesn't require redoing requirements analysis
- A SQL injection doesn't require redesigning the product spec
- Each full restart burns time, tokens, and context budget

## Solution

**Oriented Loopback**: failures are classified by type, and the pipeline loops back to the relevant agent — not always R1.

## Error Classification

| Error Pattern | Loopback Target | Examples |
|--------------|:--------------:|---------|
| Syntax error, bug, crash, traceback | R4 (Backend) | `NameError`, `KeyError`, import failures |
| Security vulnerability, XSS, injection | R6 (Security) | SQL injection, missing auth, CORS leak |
| UI/CSS/layout issues | R5 (Frontend) | Style breakage, responsive failure |
| Architecture, schema, model design | R2 (Design) | Wrong data model, API contract mismatch |
| Everything else | R1 (Requirements) | Unclear spec, scope creep |

## Decision Logic

```python
if "syntax" in error or "traceback" in error:
    loopback = LoopbackTarget.BACKEND  # → R4
elif "xss" in error or "injection" in error:
    loopback = LoopbackTarget.SECURITY  # → R6
elif "css" in error or "layout" in error:
    loopback = LoopbackTarget.FRONTEND  # → R5
elif "architecture" in error:
    loopback = LoopbackTarget.DESIGN    # → R2
else:
    loopback = LoopbackTarget.REQUIREMENTS  # → R1
```

## Loopback Limits

- After 2 loopbacks → warning logged
- After 3+ loopbacks → Pipeline Doctor triggered (manual intervention needed)
- Max 5 total loopbacks per pipeline run
