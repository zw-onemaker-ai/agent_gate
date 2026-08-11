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


# ── Q2 Phase 0: Config loader tests ──

import tempfile, json as json_mod

def _write_temp_config(data):
    """Helper: write a dict as a temp JSON config file."""
    tf = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json_mod.dump(data, tf)
    tf.close()
    return tf.name

def test_load_legacy_config():
    """Legacy config (project+agents only) should still load."""
    from src.config_loader import load_config
    cfg = {"project": {"name": "test"}, "agents": [
        {"role": "R1", "name": "Test", "role_goal": "Test agent"}
    ]}
    path = _write_temp_config(cfg)
    result = load_config(path)
    assert result["agents"][0]["role"] == "R1"
    os.remove(path)

def test_load_config_missing_agents():
    """Config without agents should raise ValueError."""
    from src.config_loader import load_config
    cfg = {"project": {"name": "test"}}
    path = _write_temp_config(cfg)
    try:
        load_config(path)
        assert False, "Should have raised ValueError"
    except ValueError as e:
        assert "agents" in str(e)
    finally:
        os.remove(path)

def test_load_config_empty_agents():
    """Config with empty agents list should raise."""
    from src.config_loader import load_config
    cfg = {"agents": []}
    path = _write_temp_config(cfg)
    try:
        load_config(path)
        assert False, "Should have raised"
    except ValueError as e:
        assert "empty" in str(e)
    finally:
        os.remove(path)

def test_load_q2_full_config():
    """Full Q2 config with topology, design_notes, context, tools should load."""
    from src.config_loader import load_config
    cfg = {
        "meta": {"project": "test", "version": "1.0"},
        "models": {"default": "qwen2.5:7b", "registry": {
            "qwen2.5:7b": {"provider": "ollama", "model": "qwen2.5:7b"}
        }},
        "agents": [
            {"role": "R1", "name": "A1", "role_goal": "test",
             "declared_tools": ["@web_search"]}
        ],
        "topology": {"stages": [
            {"stage": 1, "agents": ["R1"], "mode": "serial", "max_retries": 3}
        ]},
        "design_notes": {
            "why_these_agents": "Minimal test",
            "gaps_found": [],
            "risks": ["test risk"]
        },
        "context": {"routes": [
            {"from": "R1", "to": "R2", "files": ["output.md"]}
        ]},
        "tools": {"global_whitelist": [
            {"name": "read_file", "risk": "low"}
        ]}
    }
    path = _write_temp_config(cfg)
    result = load_config(path)
    # Defaults should be filled
    stage = result["topology"]["stages"][0]
    assert stage["max_retries"] == 3
    assert stage["on_fail"] is None
    assert result["agents"][0]["declared_tools"] == ["@web_search"]
    os.remove(path)

def test_load_q2_missing_model_provider():
    """Q2 models.registry entry without provider should fail."""
    from src.config_loader import load_config
    cfg = {
        "models": {"default": "x", "registry": {"x": {"model": "y"}}},
        "agents": [{"role": "R1", "name": "A", "role_goal": "test"}]
    }
    path = _write_temp_config(cfg)
    try:
        load_config(path)
        assert False, "Should have raised"
    except ValueError as e:
        assert "provider" in str(e)
    finally:
        os.remove(path)

def test_load_q2_bad_mode():
    """Invalid stage mode should fail."""
    from src.config_loader import load_config
    cfg = {
        "agents": [{"role": "R1", "name": "A", "role_goal": "test"}],
        "topology": {"stages": [{"stage": 1, "agents": ["R1"], "mode": "concurrent"}]}
    }
    path = _write_temp_config(cfg)
    try:
        load_config(path)
        assert False, "Should have raised"
    except ValueError as e:
        assert "mode" in str(e)
    finally:
        os.remove(path)

def test_load_reference_config():
    """q2_reference.json should load without errors."""
    from src.config_loader import load_config
    ref_path = os.path.join(
        os.path.dirname(__file__), "..", "configs", "q2_reference.json")
    result = load_config(ref_path)
    assert len(result["agents"]) == 3
    assert len(result["topology"]["stages"]) == 3
    assert result["design_notes"]["why_these_agents"]


# ── Phase 1: llm_client + context_assembler + contract ──

def test_llm_client_creation():
    """LLMClient should be creatable without errors."""
    from src.llm_client import LLMClient
    c = LLMClient(provider="ollama", model="test")
    assert c.provider == "ollama"
    assert c.model == "test"

def test_llm_response_ok():
    """LLMResponse.ok should work."""
    from src.llm_client import LLMResponse
    r1 = LLMResponse(content="hi", provider="test", model="x")
    assert r1.ok is True
    r2 = LLMResponse(content="", provider="test", model="x", error="fail")
    assert r2.ok is False

def test_contract_creation():
    """Contract should be creatable with all fields."""
    from src.models import Contract
    c = Contract(
        agent_id="R1",
        summary="Built a Todo API",
        output_files=["backend/main.py"],
        endpoints=[{"method": "GET", "path": "/todos"}],
        start_command="uvicorn main:app",
        test_hints=["Test GET /todos returns 200"],
    )
    assert c.agent_id == "R1"
    assert len(c.output_files) == 1
    assert c.start_command == "uvicorn main:app"

def test_contract_parse():
    """parse_contract_from_output should extract CONTRACT_START/END block."""
    from src.context_assembler import parse_contract_from_output
    from src.models import Contract
    output = """Some output text...
CONTRACT_START
summary: Built a Todo API with 4 CRUD endpoints
output_files: backend/main.py, backend/models.py
start_command: uvicorn main:app --port 8000
test_hints: Test GET /todos; Test POST /todos
CONTRACT_END
More text..."""
    c = parse_contract_from_output(output, "R4")
    assert c is not None
    assert c.agent_id == "R4"
    assert "Todo API" in c.summary
    assert "backend/main.py" in c.output_files
    assert "backend/models.py" in c.output_files
    assert "uvicorn" in c.start_command
    assert len(c.test_hints) == 2

def test_contract_parse_missing():
    """Missing CONTRACT block should return None."""
    from src.context_assembler import parse_contract_from_output
    assert parse_contract_from_output("No contract here", "R1") is None

def test_assembler_normal_budget():
    """ContextAssembler with small input should use NORMAL budget."""
    from src.context_assembler import ContextAssembler
    from src.models import Contract
    a = ContextAssembler()
    contract = Contract(
        agent_id="R1",
        summary="Requirements doc",
        output_files=["requirements.md"],
    )
    ctx = a.build(
        agent_id="R2",
        role_goal="Design product spec",
        upstream_contracts=[contract],
        project_background="Test project",
    )
    assert ctx.budget_mode == "CTX_NORMAL"
    assert "R1" in ctx.prompt
    assert "requirements.md" in ctx.prompt
    assert len(ctx.files_available) > 0

def test_assembler_retry_context():
    """build_for_retry should inject failure context."""
    from src.context_assembler import ContextAssembler
    a = ContextAssembler()
    ctx = a.build_for_retry(
        agent_id="R2",
        role_goal="Fix the issue",
        fail_reason="EXIT:1 — test failed",
    )
    assert "RETRY" in ctx.prompt
    assert "EXIT:1" in ctx.prompt

def test_llm_validate_env():
    """validate_env should report missing API keys."""
    from src.llm_client import LLMClient
    registry = {
        "gpt": {"provider": "openai", "model": "gpt-4o-mini"}
    }
    warnings = LLMClient.validate_env(registry)
    # May or may not warn depending on env, but shouldn't crash
    assert isinstance(warnings, list)


# ── Phase 2: Gate + Loopback closed loop ──

def test_classify_failure_backend():
    """Syntax error → BACKEND loopback."""
    from src.validators import classify_failure
    from src.models import ExitCodeFingerprint, LoopbackTarget
    fp = ExitCodeFingerprint(raw_output="EXIT:1", exit_code=1, has_fingerprint=True, count=1)
    target, extra = classify_failure(
        ["SyntaxError: invalid syntax at line 42"],
        "Traceback: NameError: name 'x' is not defined",
        exit_fp=fp,
    )
    assert target == LoopbackTarget.BACKEND
    assert len(extra) == 0


def test_classify_failure_security():
    """Security keywords → SECURITY loopback."""
    from src.validators import classify_failure
    from src.models import ExitCodeFingerprint, LoopbackTarget
    fp = ExitCodeFingerprint(raw_output="EXIT:1", exit_code=1, has_fingerprint=True, count=1)
    target, extra = classify_failure(
        ["Hardcoded API secret found"],
        "XSS vulnerability in template",
        exit_fp=fp,
    )
    assert target == LoopbackTarget.SECURITY


def test_classify_failure_frontend():
    """UI/CSS keywords → FRONTEND loopback."""
    from src.validators import classify_failure
    from src.models import ExitCodeFingerprint, LoopbackTarget
    fp = ExitCodeFingerprint(raw_output="EXIT:1", exit_code=1, has_fingerprint=True, count=1)
    target, extra = classify_failure(
        ["CSS layout broken"],
        "DOM rendering issue on mobile viewport",
        exit_fp=fp,
    )
    assert target == LoopbackTarget.FRONTEND


def test_classify_failure_design():
    """Architecture keywords → DESIGN loopback."""
    from src.validators import classify_failure
    from src.models import ExitCodeFingerprint, LoopbackTarget
    fp = ExitCodeFingerprint(raw_output="EXIT:1", exit_code=1, has_fingerprint=True, count=1)
    target, extra = classify_failure(
        ["Database schema mismatch"],
        "API design incompatible with data model",
        exit_fp=fp,
    )
    assert target == LoopbackTarget.DESIGN


def test_classify_failure_requirements():
    """Requirements keywords → REQUIREMENTS loopback."""
    from src.validators import classify_failure
    from src.models import ExitCodeFingerprint, LoopbackTarget
    fp = ExitCodeFingerprint(raw_output="EXIT:1", exit_code=1, has_fingerprint=True, count=1)
    target, extra = classify_failure(
        ["Missing requirement for authentication"],
        "User story acceptance criteria not met",
        exit_fp=fp,
    )
    assert target == LoopbackTarget.REQUIREMENTS


def test_classify_failure_unclassified():
    """Unclassified errors → NONE + extra reasons."""
    from src.validators import classify_failure
    from src.models import ExitCodeFingerprint, LoopbackTarget
    fp = ExitCodeFingerprint(raw_output="EXIT:1", exit_code=1, has_fingerprint=True, count=1)
    target, extra = classify_failure(
        ["Something bizarre happened with the flux capacitor"],
        "No recognizable pattern here",
        exit_fp=fp,
    )
    assert target == LoopbackTarget.NONE
    assert any("Unclassified" in r for r in extra)


def test_classify_failure_no_failure():
    """No failure → NONE, no extra reasons."""
    from src.validators import classify_failure
    from src.models import ExitCodeFingerprint, LoopbackTarget
    target, extra = classify_failure([], "All good EXIT:0")
    assert target == LoopbackTarget.NONE
    assert len(extra) == 0


def test_cross_verify_contract_pass():
    """Contract claims matching files → PASS."""
    import tempfile, os
    from src.validators import cross_verify_contract
    from src.models import Contract, GateStatus

    tmpdir = tempfile.mkdtemp()
    f = os.path.join(tmpdir, "main.py")
    with open(f, "w") as fh:
        fh.write("print('hello')")

    contract = Contract(
        agent_id="R4",
        summary="Built main.py",
        output_files=["main.py"],
    )
    result = cross_verify_contract(contract, tmpdir)
    assert result.status == GateStatus.PASS
    assert len(result.fail_reasons) == 0

    # Cleanup
    os.remove(f)
    os.rmdir(tmpdir)


def test_cross_verify_contract_fail_missing():
    """Contract claims file that doesn't exist → FAIL."""
    import tempfile, os
    from src.validators import cross_verify_contract
    from src.models import Contract, GateStatus

    tmpdir = tempfile.mkdtemp()
    contract = Contract(
        agent_id="R4",
        summary="Built ghost.py",
        output_files=["ghost.py"],
    )
    result = cross_verify_contract(contract, tmpdir)
    assert result.status == GateStatus.FAIL
    assert any("not found" in r for r in result.fail_reasons)

    os.rmdir(tmpdir)


def test_cross_verify_contract_fail_empty():
    """Contract claims file that's empty → FAIL."""
    import tempfile, os
    from src.validators import cross_verify_contract
    from src.models import Contract, GateStatus

    tmpdir = tempfile.mkdtemp()
    f = os.path.join(tmpdir, "empty.py")
    with open(f, "w") as fh:
        pass  # empty file

    contract = Contract(
        agent_id="R4",
        summary="Built empty.py",
        output_files=["empty.py"],
    )
    result = cross_verify_contract(contract, tmpdir)
    assert result.status == GateStatus.FAIL
    assert any("empty" in r for r in result.fail_reasons)

    os.remove(f)
    os.rmdir(tmpdir)


def test_human_gate_categories():
    """HumanGate should detect category from fail reasons."""
    from src.human_gate import _detect_category, HUMAN_CHECKLIST, FAIL_CATEGORY_MAP

    # Content quality
    assert _detect_category(["Output quality below threshold"]) == "content_quality"
    # Design decision
    assert _detect_category(["Design decision: trade-off between speed and accuracy"]) == "design_decision"
    # Requirements gap
    assert _detect_category(["Missing requirement: no auth specified"]) == "requirements_gap"
    # Security alert
    assert _detect_category(["CVE-2024-1234 detected"]) == "security_alert"
    # Contract broken
    assert _detect_category(["Contract claims file missing"]) == "contract_broken"
    # Default fallback
    assert _detect_category(["Generic error"]) == "crash"

    # All categories should have checklist entries
    for category in {"crash", "content_quality", "design_decision",
                     "requirements_gap", "security_alert", "contract_broken"}:
        assert category in HUMAN_CHECKLIST, "Missing checklist for: {}".format(category)
        assert "question" in HUMAN_CHECKLIST[category]
        assert len(HUMAN_CHECKLIST[category]["options"]) >= 3

    # FAIL_CATEGORY_MAP should cover all categories
    covered = set()
    for _, cat in FAIL_CATEGORY_MAP:
        covered.add(cat)
    assert covered == {"content_quality", "design_decision",
                       "requirements_gap", "security_alert", "contract_broken"}, \
        "FAIL_CATEGORY_MAP coverage: {}".format(covered)


def test_human_gate_prompt():
    """human_gate_prompt should include category info."""
    from src.human_gate import human_gate_prompt
    prompt = human_gate_prompt(
        ["Missing requirement for login"],
        "EXIT:1 no auth",
    )
    assert "HUMAN GATE" in prompt
    assert "REQUIREMENTS GAP" in prompt
    assert "Missing requirement" in prompt


def test_engine_contract_verify_integration():
    """AgentGate with broken contract → gate should fail."""
    import tempfile, os
    from src.engine import AgentGate
    from src.models import Contract, GateStatus

    tmpdir = tempfile.mkdtemp()
    gate = AgentGate(project_name="test-contract", output_dir=tmpdir,
                     model_provider="ollama", model_name="qwen2.5:7b")

    # Register an agent that would produce a file
    gate.register_agent(
        role="R1",
        name="Test Agent",
        role_goal="Write a hello world script",
        output_file="hello.py",
        acceptance_criteria=["File should contain print('hello')"],
    )

    # Verify cross_verify_contract integration via mock
    # (The actual LLM call would fail without ollama, so test contract-only path)
    from src.validators import cross_verify_contract
    c = Contract(agent_id="R1", summary="test",
                 output_files=["nonexistent.py"])
    result = cross_verify_contract(c, tmpdir)
    assert result.status == GateStatus.FAIL
    assert result.loopback_target.value != "NONE"

    os.rmdir(tmpdir)


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
        test_load_legacy_config,
        test_load_config_missing_agents,
        test_load_config_empty_agents,
        test_load_q2_full_config,
        test_load_q2_missing_model_provider,
        test_load_q2_bad_mode,
        test_load_reference_config,
        test_llm_client_creation,
        test_llm_response_ok,
        test_contract_creation,
        test_contract_parse,
        test_contract_parse_missing,
        test_assembler_normal_budget,
        test_assembler_retry_context,
        test_llm_validate_env,
        test_classify_failure_backend,
        test_classify_failure_security,
        test_classify_failure_frontend,
        test_classify_failure_design,
        test_classify_failure_requirements,
        test_classify_failure_unclassified,
        test_classify_failure_no_failure,
        test_cross_verify_contract_pass,
        test_cross_verify_contract_fail_missing,
        test_cross_verify_contract_fail_empty,
        test_human_gate_categories,
        test_human_gate_prompt,
        test_engine_contract_verify_integration,
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
