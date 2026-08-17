"""Validators — Bash verification, EXIT fingerprint, file checks, loopback classification."""

import json
import os
import re
import shlex
import subprocess
from pathlib import Path
from typing import Optional, List, Tuple

from .models import (
    ExitCodeFingerprint, GateStatus, QualityGateResult, LoopbackTarget,
    Contract,
)


def bash_verify_files(file_paths, check_syntax=True):
    # type: (List[str], bool) -> dict
    """Verify files exist + non-empty + readable + syntax check."""
    result = {"status": GateStatus.PASS, "files": {}}
    for fp in file_paths:
        p = Path(fp)
        info = {"exists": p.exists(), "non_empty": False, "readable": False, "syntax_ok": None}
        if p.exists():
            info["non_empty"] = p.stat().st_size > 0
            try:
                with open(fp) as f:
                    f.read(1)
                info["readable"] = True
            except Exception:
                pass
            if check_syntax and p.suffix == ".py":
                try:
                    r = subprocess.run(["python3", "-m", "py_compile", str(p)],
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
                    # v1.3.4 fix: returncode was ignored — a failing py_compile
                    # was recorded as syntax_ok=True (E2E root cause amplifier)
                    info["syntax_ok"] = (r.returncode == 0)
                except Exception:
                    info["syntax_ok"] = False
                if info["syntax_ok"] is False:
                    result["status"] = GateStatus.FAIL
        result["files"][fp] = info
        if not info["exists"] or not info["non_empty"]:
            result["status"] = GateStatus.FAIL
    return result


def check_exit_fingerprint(output):
    # type: (str) -> ExitCodeFingerprint
    """Extract EXIT_CODE fingerprint. No EXIT:? -> verification is FAKE."""
    return ExitCodeFingerprint.from_output(output)


def run_bash(cmd, timeout=30):
    # type: (str, int) -> Tuple[str, int]
    """Run bash command, auto-append EXIT_CODE fingerprint."""
    try:
        r = subprocess.run(
            ["bash", "-c", cmd + "; echo EXIT:$?"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=timeout
        )
        return r.stdout + r.stderr, r.returncode
    except subprocess.TimeoutExpired:
        return "TIMEOUT\nEXIT:124", 124
    except Exception as e:
        return "ERROR: {}\nEXIT:1".format(e), 1


def quality_gate_check(role, part_a_files, verification_output, check_desensitize=False):
    # type: (str, List[str], str, bool) -> QualityGateResult
    """Full quality gate protocol: file check + EXIT fingerprint + desensitization."""
    checks = []
    fail_reasons = []
    exit_fp = check_exit_fingerprint(verification_output)

    fc = bash_verify_files(part_a_files)
    checks.append({"name": "file_existence", "status": fc["status"].value, "detail": fc["files"]})
    missing = [
        fp for fp, info in fc["files"].items()
        if not info["exists"] or not info["non_empty"]
    ]
    if missing:
        fail_reasons.append(
            "Part A files missing or empty: {}".format(", ".join(missing)))
    # v1.3.4 ①: syntax failures must fail the gate (previously silent)
    syntax_bad = [
        fp for fp, info in fc["files"].items()
        if info.get("syntax_ok") is False
    ]
    if syntax_bad:
        fail_reasons.append(
            "Python syntax error in {}".format(", ".join(syntax_bad)))

    checks.append({
        "name": "exit_fingerprint",
        "status": "PASS" if exit_fp.has_fingerprint else "FAIL",
        "detail": {"count": exit_fp.count, "exit_code": exit_fp.exit_code},
    })
    if not exit_fp.has_fingerprint:
        fail_reasons.append("EXIT_CODE fingerprint absent")
    elif exit_fp.exit_code != 0:
        fail_reasons.append("Verification command failed (EXIT:{})".format(exit_fp.exit_code))

    if check_desensitize:
        forbidden = r"(管线|pipeline|R\d|Bash验证|CTO审查|quality_gate|orchestrator|agent_core)"
        leaked = False
        for fp in part_a_files:
            if Path(fp).exists():
                content = Path(fp).read_text(encoding="utf-8", errors="ignore")
                if re.search(forbidden, content):
                    leaked = True
                    fail_reasons.append("Internal terms leaked: {}".format(fp))
        checks.append({"name": "desensitization", "status": "FAIL" if leaked else "PASS"})

    status = GateStatus.FAIL if fail_reasons else GateStatus.PASS

    # Oriented loopback classification (delegated to standalone function)
    loopback, extra_reasons = classify_failure(fail_reasons, verification_output, exit_fp)
    fail_reasons.extend(extra_reasons)

    # If NONE but gate failed, escalate to human
    if loopback == LoopbackTarget.NONE and status == GateStatus.FAIL:
        # v1.3.4 ③: never embed raw verification output (contains the verify
        # command itself) into the failure context the retrying agent will see
        combined = " ".join(fail_reasons) + " " + sanitize_loopback_context(
            verification_output, max_len=200)
        fail_reasons.append(
            "[HUMAN_GATE] No automatic loopback target matched. "
            "Error context: {}".format(combined[:200])
        )

    return QualityGateResult(
        status=status, checks=checks, exit_fingerprint=exit_fp,
        fail_reasons=fail_reasons, loopback_target=loopback,
    )


def validate_json_file(filepath):
    # type: (str) -> bool
    """Check file is valid JSON."""
    try:
        with open(filepath) as f:
            json.load(f)
        return True
    except Exception:
        return False


def check_context_budget(total_bytes):
    # type: (int) -> str
    if total_bytes <= 8192:
        return "CTX_NORMAL"
    elif total_bytes <= 15360:
        return "CTX_WARNING"
    return "CTX_CRITICAL"


# ── Phase 2: Oriented loopback classification ──

# Error pattern → loopback target mapping (order matters: first match wins)
LOOPBACK_PATTERNS = [
    # Code-level errors → BACKEND
    (r"(syntax\s*error|traceback|NameError|TypeError|ValueError|"
     r"AttributeError|ImportError|IndentationError|bug\b|crash|"
     r"compile\s*fail|undefined\s+variable|not\s+defined)",
     LoopbackTarget.BACKEND),
    # Security issues → SECURITY
    (r"(security|xss\b|injection|csrf\b|owasp|vulnerability|"
     r"hardcoded\s*(secret|token|password|key)|cve-\d)",
     LoopbackTarget.SECURITY),
    # Frontend/UI issues → FRONTEND
    (r"(html|css\b|ui\b|layout|frontend|dom\b|responsive|"
     r"viewport|styling|component\s*render)",
     LoopbackTarget.FRONTEND),
    # Architecture/design issues → DESIGN
    (r"(design|architecture|schema\b|model\b|database|"
     r"api\s*design|data\s*model|interface\s*contract)",
     LoopbackTarget.DESIGN),
    # Requirements issues → REQUIREMENTS
    (r"(requirement|specification|acceptance\s*criteria|"
     r"user\s*story|scope|missing\s*requirement)",
     LoopbackTarget.REQUIREMENTS),
    # Generic content-verification failure → SELF: the agent's own artifact
    # failed its own acceptance checks — regenerate it.
    # (Specific errors — syntax/traceback/security — match earlier patterns.)
    (r"(verification\s*(command\s*)?failed|acceptance\s*check\s*failed|"
     r"criteria\s*not\s*met|check\s*failed)",
     LoopbackTarget.SELF),
]


def classify_failure(fail_reasons, verification_output, exit_fp=None):
    # type: (List[str], str, Optional[ExitCodeFingerprint]) -> Tuple[LoopbackTarget, List[str]]
    """Classify a gate failure to determine the correct loopback target.

    Phase 2: Extracted from quality_gate_check into standalone function
    for reuse by engine, human_gate, and pipeline doctor.

    Returns:
        (LoopbackTarget, extra_fail_reasons) — extra reasons added for
        unclassified or edge cases.
    """
    extra_reasons = []
    combined = " ".join(fail_reasons) + " " + verification_output

    # Only classify if there's an actual failure
    has_failure = bool(fail_reasons)
    if exit_fp and exit_fp.exit_code != 0:
        has_failure = True

    if not has_failure:
        return LoopbackTarget.NONE, extra_reasons

    for pattern, target in LOOPBACK_PATTERNS:
        if re.search(pattern, combined, re.I):
            return target, extra_reasons

    # Unclassified — escalate to human
    extra_reasons.append(
        "Unclassified failure: unable to determine loopback target from "
        "error patterns. Manual review needed."
    )
    return LoopbackTarget.NONE, extra_reasons


# ── Phase 2: Contract cross-verification ──

def cross_verify_contract(contract, output_dir, check_endpoints=True):
    # type: (Contract, str, bool) -> QualityGateResult
    """Cross-verify a Contract's claims against actual files/endpoints.

    Step 0.6 of the anti-hallucination protocol: if an agent claims it
    produced certain files or API endpoints, verify those claims are real.

    Returns a QualityGateResult — PASS if all claims verified, FAIL otherwise.
    """
    checks = []
    fail_reasons = []
    od = Path(output_dir)

    # Verify claimed files exist
    if contract.output_files:
        for f in contract.output_files:
            fp = od / f
            exists = fp.exists()
            non_empty = exists and fp.stat().st_size > 0
            checks.append({
                "name": "contract_file:{}".format(f),
                "status": "PASS" if (exists and non_empty) else "FAIL",
            })
            if not exists:
                fail_reasons.append(
                    "Contract claims file '{}' — not found in {}".format(f, output_dir))
            elif not non_empty:
                fail_reasons.append(
                    "Contract claims file '{}' — exists but is empty".format(f))

    # Verify claimed endpoints are reachable (basic check)
    if check_endpoints and contract.endpoints:
        for ep in contract.endpoints:
            method = ep.get("method", "GET")
            path = ep.get("path", "/")
            checks.append({
                "name": "contract_endpoint:{} {}".format(method, path),
                "status": "SKIPPED",  # Can't actually curl without running service
                "detail": "Endpoint declared — verify manually after deployment",
            })

    status = GateStatus.FAIL if fail_reasons else GateStatus.PASS
    return QualityGateResult(
        status=status,
        checks=checks,
        fail_reasons=fail_reasons,
        loopback_target=LoopbackTarget.NONE if status == GateStatus.PASS else LoopbackTarget.BACKEND,
    )


# ── v1.3.4 ①: Output sanitization ──

_CODE_BLOCK_RE = re.compile(r"```[a-zA-Z0-9_+#.-]*\n(.*?)```", re.S)

_PART_HEADER_RE = re.compile(
    r"^#{0,6}\s*(part\s*[ab]|part\s*[一二])\s*[:：]?.*$", re.I)

_PY_SIGNAL_RE = re.compile(
    r"^(import\s+|from\s+[\w.]+\s+import\s+|def\s+|async\s+def\s+|class\s+|"
    r"if\s+__name__\s*==\s*[\"']__main__[\"']|@[\w.]+|#!|"
    r"\"\"\"|'''|#\s|[A-Za-z_][A-Za-z0-9_]*\s*=\s*)")

# Artifacts where the fenced code block IS the deliverable (vs .md docs where
# inner code fences are legitimate content and must not be extracted)
_CODE_FENCE_EXTRACT = {
    ".py", ".js", ".ts", ".jsx", ".sh", ".go", ".rs", ".java",
    ".css", ".html", ".json", ".yaml", ".yml", ".sql", ".sol",
}


def sanitize_agent_output(raw, output_file):
    # type: (str, str) -> str
    """v1.3.4 ①: strip narrative / markdown fences / Part labels from raw
    LLM output before it is written as the artifact file.

    E2E failure mode: narrative text landed at the top of app.py and broke
    py_compile → pytest cascade → unjust loopback spiral.

    The CONTRACT block (Part B) is intentionally removed from the artifact —
    the engine parses it from the raw output separately.
    """
    text = (raw or "").replace("﻿", "").strip()
    if not text:
        return ""

    ext = Path(output_file).suffix.lower()

    # 1. Fence handling
    if ext in _CODE_FENCE_EXTRACT:
        # Prefer the longest fenced block — that is the real artifact
        blocks = _CODE_BLOCK_RE.findall(text)
        if blocks:
            text = max(blocks, key=len).strip()
        elif text.startswith("```"):
            inner = text.split("\n", 1)[-1]
            if inner.rstrip().endswith("```"):
                inner = inner.rstrip()[:-3]
            text = inner.strip()
    elif text.startswith("```") and text.rstrip().endswith("```"):
        # Docs: only strip if the WHOLE text is one outer fence
        inner = text.split("\n", 1)[-1]
        if inner.rstrip().endswith("```"):
            inner = inner.rstrip()[:-3]
        text = inner.strip()

    # 2. Tool-call stubs are not artifacts (live E2E: qwen emitted a 48-byte
    # "<tool_call>..." stub as its architecture.md) — treat as empty so the
    # engine retries the agent instead of writing a useless file
    if text.lstrip().startswith("<tool_call"):
        return ""

    # 3. Remove Part B contract block
    text = re.sub(r"CONTRACT_START.*?CONTRACT_END", "", text, flags=re.S)

    # 3. Remove Part A / Part B section headers
    lines = [
        l for l in text.split("\n")
        if not _PART_HEADER_RE.match(l.strip())
    ]
    text = "\n".join(lines).strip()

    # 4. Code files: drop leading narrative before the first code-signal line
    if ext == ".py":
        text = _strip_python_narrative(text)

    return text.strip()


def _strip_python_narrative(text):
    # type: (str) -> str
    """Drop narrative lines that precede the first line that looks like code."""
    lines = text.split("\n")
    for i, line in enumerate(lines):
        if _PY_SIGNAL_RE.match(line.strip()):
            return "\n".join(lines[i:])
    return text


# ── v1.3.4 ②: verify_cmd strengthening ──

def strengthen_verify_cmd(verify_cmd, file_path):
    # type: (str, str) -> str
    """v1.3.4 ②: code files must carry real syntax validation — a gate must
    never pass a code artifact on a grep-only command.

    Prepends py_compile / node --check for code artifacts. Commands that
    already contain a syntax check or a python run are left untouched.
    """
    if not verify_cmd or not file_path:
        return verify_cmd
    ext = Path(file_path).suffix.lower()
    cmd = verify_cmd.strip()
    if ext == ".py":
        syntax = "python3 -m py_compile {}".format(shlex.quote(str(file_path)))
        if "py_compile" not in cmd and not re.search(r"\bpython3?\s+\S+\.py", cmd):
            cmd = syntax + " && " + cmd
    elif ext in (".js", ".mjs", ".ts", ".jsx"):
        syntax = "node --check {}".format(shlex.quote(str(file_path)))
        if "--check" not in cmd:
            cmd = syntax + " && " + cmd
    return cmd


# ── v1.3.4 ③: Loopback context sanitization ──

# Commands are lowercase by convention; case-sensitive so human-readable
# reasons like "Python syntax error in ..." survive.
_COMMAND_LINE_RE = re.compile(
    r"^(python3?|pytest|curl|grep|bash\b|npm|node|pip3?|echo|wc\s|ls\s|"
    r"test\s|cat\s|find\s|cd\s|mkdir|cp\s|mv\s|rm\s|chmod|git\s|export\s)")

_TRACEBACK_FILE_RE = re.compile(r"^File \"[^\"]+\", line \d+")


def sanitize_loopback_context(text, max_len=600):
    # type: (str, int) -> str
    """v1.3.4 ③: strip verification-command traces from loopback context.

    E2E failure mode: the failure context carried the verify command text, and
    the retrying model copied those commands into its output (test_app.py
    "tool hallucination"). Only human-readable failure reasons and contract
    summaries should survive into the retry prompt.
    """
    if not text:
        return text
    cleaned = []
    for line in text.split("\n"):
        s = line.strip()
        if not s:
            continue
        if s.startswith(("$", ">", ">>>")):
            continue
        if s.startswith("Traceback"):
            continue
        if _TRACEBACK_FILE_RE.match(s):
            continue
        if _COMMAND_LINE_RE.match(s):
            continue
        # Drop fingerprint noise but keep the human-readable reason
        s = re.sub(r"\bEXIT:\d+\b", "", s).replace("()", "").strip()
        if not s:
            continue
        cleaned.append(s)
    out = "\n".join(cleaned).strip()
    if len(out) > max_len:
        out = out[:max_len] + "…"
    return out
