# Protocol 2: Quality Gate

## Problem

Multi-agent pipelines suffer from error propagation: Agent 2's output depends on Agent 1's output, but Agent 2 never verifies that Agent 1 actually produced what it claims. After 3-4 agent hops, errors accumulate silently.

## Solution

Every agent output passes through a **Quality Gate** before reaching the next agent. The gate checks:

| Check | What | Fail Condition |
|-------|------|---------------|
| **File Existence** | Part A deliverable files exist, are non-empty, and readable | Missing or 0-byte files |
| **EXIT Fingerprint** | Bash verification output contains `EXIT:<code>` | No fingerprint found |
| **Desensitization** | Customer-facing docs contain no internal pipeline terms | "pipeline", "R1-R12", "quality_gate" etc. in output |

Failed gates **halt the pipeline** — the output never reaches the next agent.

## Gate Decision

```
All checks PASS → output proceeds to next agent
Any check FAIL → output blocked → loopback triggered
```

## Implementation

```python
from agent_gate.validators import quality_gate_check

result = quality_gate_check(
    role="R4",
    part_a_files=["backend/main.py", "backend/models.py"],
    verification_output=bash_output_with_exit_fingerprint,
)

if result.status == GateStatus.FAIL:
    print(f"Gate failed: {result.fail_reasons}")
    print(f"Loopback target: {result.loopback_target}")
```
