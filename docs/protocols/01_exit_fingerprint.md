# Protocol 1: EXIT_CODE Fingerprint

## Problem

AI agents produce verification results through Bash commands. But there's no way to distinguish between:
- **Real verification**: the command actually ran and produced the output shown
- **Hallucinated verification**: the agent fabricated the output without executing anything

## Solution

Every Bash verification output MUST end with an `EXIT:<code>` fingerprint — the actual exit code of the command, printed by the shell itself (`echo EXIT:$?`). This cannot be forged by an LLM unless it also correctly predicts the exit code of a command it never ran.

## How It Works

```bash
# Instead of:
$ pytest tests/
...test output...

# Do:
$ pytest tests/
...test output...
$ echo EXIT:$?
EXIT:0
```

The framework's `check_exit_fingerprint()` function regex-scans for `EXIT:\d+` patterns. Output without this fingerprint is rejected as "verification may be faked".

## Anti-Patterns

```python
# WRONG: LLM-generated fake verification
verification = "All tests passed! 10/10"

# RIGHT: Real Bash output with fingerprint
verification, exit_code = run_bash("pytest tests/")
# → "...test output...\nEXIT:0"
```

## Integration

```python
from agent_gate.validators import run_bash, check_exit_fingerprint

output, rc = run_bash("python3 -m py_compile main.py")
fp = check_exit_fingerprint(output)
if not fp.has_fingerprint:
    raise QualityGateFailure("Missing EXIT_CODE fingerprint")
```
