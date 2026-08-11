"""Validators — Bash verification, EXIT fingerprint, file checks, loopback classification."""

import json
import os
import re
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
                    subprocess.run(["python3", "-m", "py_compile", str(p)],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
                    info["syntax_ok"] = True
                except Exception:
                    info["syntax_ok"] = False
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
    if fc["status"] == GateStatus.FAIL:
        fail_reasons.append("Part A files missing or empty")

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
        combined = " ".join(fail_reasons) + " " + verification_output
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
