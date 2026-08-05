"""Tests for AgentGate core validators."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.validators import (
    check_exit_fingerprint, run_bash, quality_gate_check,
    bash_verify_files, check_context_budget,
)
from src.models import GateStatus, LoopbackTarget


def test_exit_fingerprint_valid():
    output = "Tests passed!\nEXIT:0\n"
    fp = check_exit_fingerprint(output)
    assert fp.has_fingerprint is True
    assert fp.exit_code == 0
    assert fp.count == 1


def test_exit_fingerprint_missing():
    output = "Tests passed!\nAll good."
    fp = check_exit_fingerprint(output)
    assert fp.has_fingerprint is False


def test_exit_fingerprint_multiple():
    output = "EXIT:0\nStep 2 done\nEXIT:0\n"
    fp = check_exit_fingerprint(output)
    assert fp.count == 2


def test_run_bash_success():
    output, rc = run_bash("echo hello")
    assert "hello" in output
    assert "EXIT:0" in output
    assert rc == 0


def test_run_bash_failure():
    # Use a failing command (not exit, which terminates the shell before echo runs)
    output, rc = run_bash("python3 -c 'import sys; sys.exit(42)'")
    assert "EXIT:42" in output


def test_quality_gate_pass():
    # Create a test file
    test_file = "/tmp/agentgate_test_file.txt"
    with open(test_file, "w") as f:
        f.write("test content")
    
    result = quality_gate_check(
        role="R1",
        part_a_files=[test_file],
        verification_output="File OK\nEXIT:0\n",
    )
    assert result.status == GateStatus.PASS
    assert len(result.fail_reasons) == 0
    assert result.exit_fingerprint.has_fingerprint
    
    os.remove(test_file)


def test_quality_gate_missing_file():
    result = quality_gate_check(
        role="R1",
        part_a_files=["/tmp/nonexistent_file_xyz.txt"],
        verification_output="EXIT:0\n",
    )
    assert result.status == GateStatus.FAIL
    assert any("missing" in r.lower() or "empty" in r.lower() for r in result.fail_reasons)


def test_quality_gate_no_fingerprint():
    test_file = "/tmp/agentgate_test2.txt"
    with open(test_file, "w") as f:
        f.write("test")
    
    result = quality_gate_check(
        role="R1",
        part_a_files=[test_file],
        verification_output="All good, no fingerprint here.",
    )
    assert result.status == GateStatus.FAIL
    assert any("fingerprint" in r.lower() for r in result.fail_reasons)
    
    os.remove(test_file)


def test_context_budget():
    assert check_context_budget(1000) == "CTX_NORMAL"
    assert check_context_budget(9000) == "CTX_WARNING"
    assert check_context_budget(16000) == "CTX_CRITICAL"


def test_oriented_loopback():
    """Oriented loopback: errors route to the right target."""
    tf = "/tmp/agentgate_lb.txt"
    with open(tf, "w") as f:
        f.write("test")

    # Syntax error -> Backend
    r1 = quality_gate_check("R2", [tf], "SyntaxError: invalid syntax\nEXIT:1\n")
    assert r1.loopback_target == LoopbackTarget.BACKEND

    # Security -> Security
    r2 = quality_gate_check("R6", [tf], "XSS vulnerability found\nEXIT:1\n")
    assert r2.loopback_target == LoopbackTarget.SECURITY

    # UI/CSS -> Frontend
    r3 = quality_gate_check("R5", [tf], "CSS layout broken on mobile\nEXIT:1\n")
    assert r3.loopback_target == LoopbackTarget.FRONTEND

    os.remove(tf)


def test_unclassified_loopback():
    """Unclassified errors → NONE (escalate to human, not guess R1)."""
    tf = "/tmp/agentgate_unknown.txt"
    with open(tf, "w") as f:
        f.write("test")
    result = quality_gate_check(
        "R2", [tf],
        "Something weird happened but no recognizable pattern\nEXIT:1\n"
    )
    # Should be NONE (escalate), not REQUIREMENTS (blind guess)
    assert result.loopback_target == LoopbackTarget.NONE, \
        "Unclassified errors should escalate, got: {}".format(result.loopback_target)
    assert any("HUMAN_GATE" in r or "Unclassified" in r for r in result.fail_reasons)
    os.remove(tf)


if __name__ == "__main__":
    import traceback
    tests = [
        test_exit_fingerprint_valid,
        test_exit_fingerprint_missing,
        test_exit_fingerprint_multiple,
        test_run_bash_success,
        test_run_bash_failure,
        test_quality_gate_pass,
        test_quality_gate_missing_file,
        test_quality_gate_no_fingerprint,
        test_context_budget,
        test_oriented_loopback,
        test_unclassified_loopback,
    ]
    passed = 0
    failed = 0
    for test in tests:
        try:
            test()
            print("  PASS: {}".format(test.__name__))
            passed += 1
        except Exception as e:
            print("  FAIL: {} — {}".format(test.__name__, e))
            failed += 1
    
    print("\n{} passed, {} failed".format(passed, failed))
    if failed > 0:
        sys.exit(1)
