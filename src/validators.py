"""Validators — Bash verification, EXIT fingerprint, file checks."""

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Optional, List, Tuple

from .models import ExitCodeFingerprint, GateStatus, QualityGateResult, LoopbackTarget


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

    # Oriented loopback: check both fail_reasons AND verification output
    loopback = LoopbackTarget.NONE
    combined = " ".join(fail_reasons) + " " + verification_output
    if status == GateStatus.FAIL or exit_fp.exit_code != 0:
        if re.search(r"(syntax|error|bug|crash|traceback)", combined, re.I):
            loopback = LoopbackTarget.BACKEND
        elif re.search(r"(security|xss|injection|csrf)", combined, re.I):
            loopback = LoopbackTarget.SECURITY
        elif re.search(r"(html|css|ui|layout|frontend)", combined, re.I):
            loopback = LoopbackTarget.FRONTEND
        elif re.search(r"(design|architecture|schema|model)", combined, re.I):
            loopback = LoopbackTarget.DESIGN
        else:
            # Unclassified failure — escalate, don't guess
            loopback = LoopbackTarget.NONE
            fail_reasons.append(
                "Unclassified failure: unable to determine loopback target. "
                "Reviewing agent should analyze the error and suggest target."
            )

    # If NONE but gate failed, escalate to human
    if loopback == LoopbackTarget.NONE and status == GateStatus.FAIL:
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
