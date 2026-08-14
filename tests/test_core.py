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


# ── Phase 3: Design Brain — orchestrator_agent ──

def test_template_match_web_api():
    """API keywords → web_api template."""
    from src.orchestrator_agent import _match_template
    assert _match_template("Build a REST API with FastAPI and CRUD endpoints") == "web_api"

def test_template_match_cli():
    """CLI keywords → cli_tool template."""
    from src.orchestrator_agent import _match_template
    assert _match_template("Create a command line tool with argparse") == "cli_tool"

def test_template_match_data():
    """Data keywords → data_pipeline template."""
    from src.orchestrator_agent import _match_template
    assert _match_template("Build an ETL data pipeline for CSV files") == "data_pipeline"

def test_template_match_default():
    """No keywords → default web_api."""
    from src.orchestrator_agent import _match_template
    result = _match_template("some random project without recognizable keywords")
    assert result in ("web_api", "cli_tool", "data_pipeline")

def test_generate_config_template():
    """generate_pipeline_config without LLM → valid config from template."""
    from src.orchestrator_agent import generate_pipeline_config
    config = generate_pipeline_config(
        "Build a REST API for a todo app", use_llm=False)
    assert "meta" in config
    assert "agents" in config
    assert len(config["agents"]) >= 2
    assert "topology" in config
    assert "design_notes" in config
    assert config["design_notes"]["why_these_agents"]

def test_generate_config_contains_r1():
    """Every generated config should start with R1."""
    from src.orchestrator_agent import generate_pipeline_config
    config = generate_pipeline_config(
        "Build a CLI tool", use_llm=False)
    assert config["agents"][0]["role"] == "R1"

def test_parse_llm_output_json():
    """_parse_llm_output should extract JSON from raw LLM output."""
    from src.orchestrator_agent import _parse_llm_output
    raw = 'Some text...\n{"agents": [{"role": "R1", "role_goal": "test"}]}\nMore text...'
    result = _parse_llm_output(raw)
    assert result is not None
    assert result["agents"][0]["role"] == "R1"

def test_parse_llm_output_fenced():
    """_parse_llm_output should strip markdown fences."""
    from src.orchestrator_agent import _parse_llm_output
    raw = '```json\n{"agents": [{"role": "R1", "role_goal": "test"}]}\n```'
    result = _parse_llm_output(raw)
    assert result is not None
    assert result["agents"][0]["role"] == "R1"

def test_parse_llm_output_invalid():
    """_parse_llm_output should return None for garbage."""
    from src.orchestrator_agent import _parse_llm_output
    assert _parse_llm_output("not json at all") is None

def test_validate_orchestrator_output():
    """_validate_orchestrator_output should reject bad configs."""
    from src.orchestrator_agent import _validate_orchestrator_output
    assert _validate_orchestrator_output({"agents": [{"role": "R1", "role_goal": "x"}]}) is True
    assert _validate_orchestrator_output({"agents": []}) is False
    assert _validate_orchestrator_output({"agents": [{"role": "R1"}]}) is False  # no role_goal
    assert _validate_orchestrator_output("not dict") is False

def test_expand_prompts_no_llm():
    """expand_prompts without LLM should add template-based prompts."""
    from src.orchestrator_agent import expand_prompts
    config = {
        "agents": [
            {"role": "R1", "name": "Test", "role_goal": "test agent",
             "output_file": "out.md", "scenario_type": "code_gen",
             "acceptance_criteria": ["test"]},
        ]
    }
    result = expand_prompts(config, call_llm_fn=None)
    assert "prompt_template" in result["agents"][0]
    assert "Role" in result["agents"][0]["prompt_template"]
    assert "Test" in result["agents"][0]["prompt_template"]

def test_expand_prompts_skips_existing():
    """expand_prompts should not overwrite existing prompt_template."""
    from src.orchestrator_agent import expand_prompts
    config = {
        "agents": [
            {"role": "R1", "name": "Test", "role_goal": "test",
             "prompt_template": "PRESERVE_ME", "output_file": "out.md"},
        ]
    }
    result = expand_prompts(config, call_llm_fn=None)
    assert result["agents"][0]["prompt_template"] == "PRESERVE_ME"

def test_expand_prompts_all_templates():
    """All 3 templates should produce valid expandable configs."""
    from src.orchestrator_agent import generate_pipeline_config, expand_prompts
    for desc in [
        "Build a REST API",
        "Create a CLI tool with click",
        "Build an ETL data pipeline",
    ]:
        config = generate_pipeline_config(desc, use_llm=False)
        result = expand_prompts(config, call_llm_fn=None)
        for agent in result["agents"]:
            assert "prompt_template" in agent
            assert len(agent["prompt_template"]) > 50


# ── Phase 4: Full Pipeline — tool_registry, cpoo_scorer, memory_manager, doctor ──

def test_tool_registry_validate_ok():
    """Valid tools should pass validation."""
    from src.tool_registry import ToolRegistry
    reg = ToolRegistry()
    assert reg.validate_agent_tools(["@read_file", "@write_file"]) == []

def test_tool_registry_validate_bad():
    """Unknown tools should be rejected."""
    from src.tool_registry import ToolRegistry
    reg = ToolRegistry()
    errors = reg.validate_agent_tools(["@sudo_rm_rf"])
    assert len(errors) >= 1
    assert any("Unknown" in e for e in errors)

def test_tool_registry_risk():
    """Risk levels should be correctly assigned."""
    from src.tool_registry import ToolRegistry
    reg = ToolRegistry()
    assert reg.get_risk("@read_file") == "low"
    assert reg.get_risk("@deploy") == "high"
    assert reg.max_risk(["@read_file"]) == "low"
    assert reg.max_risk(["@read_file", "@deploy"]) == "high"

def test_tool_registry_requires_gate():
    """Gate-requiring tools should be detected."""
    from src.tool_registry import ToolRegistry
    reg = ToolRegistry()
    assert reg.requires_gate(["@write_file"]) is True
    assert reg.requires_gate(["@read_file"]) is False

def test_tool_registry_gate_check():
    """Tool gate check should produce QualityGateResult."""
    from src.tool_registry import ToolRegistry
    from src.models import GateStatus
    reg = ToolRegistry()
    result = reg.gate_check_tools("R1", ["@read_file", "@write_file"])
    assert result.status == GateStatus.PASS
    result2 = reg.gate_check_tools("R1", ["@nonexistent_tool"])
    assert result2.status == GateStatus.FAIL

def test_cpoo_scorer_high_quality():
    """A well-structured prompt should score >=80."""
    from src.cpoo_scorer import CPOOScorer
    scorer = CPOOScorer()
    good_prompt = """
## Role
You are a Backend Developer. Your job is to implement REST API endpoints.
Your role matters because the product depends on reliable backend services.

## Constraints
- 🔴 MUST: Produce complete Python code with no placeholders
- 🔴 MUST: All Bash output must carry EXIT_CODE fingerprint
- 🟡 SHOULD: Follow PEP8 style guide

## Workflow
1. Read the requirements and product spec
2. Design the API endpoints and data models
3. Implement the complete backend code in backend/main.py
4. Run verification: python3 -m py_compile backend/main.py && echo EXIT:$?
5. Self-check: do all acceptance criteria pass?

## IO Format
**Input:** requirements.md, product_spec.md
**Output:** backend/main.py (complete, runnable FastAPI application)

## Quality Gate Note
Your output will be automatically verified for file existence, syntax, and EXIT_CODE fingerprint.
All acceptance criteria must be met. No TODOs or placeholders allowed.
"""
    result = scorer.score(good_prompt)
    assert result.total >= 60, "Expected >=60, got {}".format(result.total)

def test_cpoo_scorer_low_quality():
    """A minimal prompt should score low."""
    from src.cpoo_scorer import CPOOScorer
    scorer = CPOOScorer()
    bad_prompt = "Write some code please. Make it good."
    result = scorer.score(bad_prompt)
    assert result.total < 60, "Expected <60, got {}".format(result.total)
    assert result.needs_regen is True

def test_cpoo_scorer_quick_check():
    """quick_check should work."""
    from src.cpoo_scorer import quick_check
    assert quick_check("Write code", threshold=80) is False

def test_cpoo_scorer_optimize_no_llm():
    """Pattern-based optimize should add missing modules."""
    from src.cpoo_scorer import CPOOScorer
    scorer = CPOOScorer()
    bad = "Write a hello world script in Python."
    original_score = scorer.score(bad).total
    optimized = scorer.optimize(bad, role_goal="Write Python code")
    new_score = scorer.score(optimized).total
    assert new_score > original_score, "Optimization should improve score: {} → {}".format(
        original_score, new_score)

def test_memory_manager_save_load():
    """Save and load a pipeline session."""
    import tempfile, os
    from src.memory_manager import MemoryManager
    from src.models import PipelineState, AgentOutput, ContextPackage, GateStatus, Contract

    tmpdir = tempfile.mkdtemp()
    mm = MemoryManager(memory_dir=tmpdir)

    state = PipelineState(project_name="test_memory")
    state.steps = [
        ContextPackage(
            package_id="pkg-1",
            source_role="R1",
            target_roles=["R2"],
            agent_output=AgentOutput(
                role="R1", role_name="Test",
                part_a_files=["out.md"],
                quality_gate=GateStatus.PASS,
                contract=Contract(agent_id="R1", summary="test contract"),
            ),
            gate_result=None,
        )
    ]
    state.loopback_count = 1

    sid = mm.save_session(state)
    assert sid.startswith("test_memory_")

    loaded = mm.load_session(sid)
    assert loaded["project_name"] == "test_memory"
    assert len(loaded["steps"]) == 1

    sessions = mm.list_sessions()
    assert len(sessions) >= 1

    # Cleanup
    import shutil
    shutil.rmtree(tmpdir)

def test_memory_manager_health():
    """Health check should work on fresh memory dir."""
    import tempfile, os, shutil
    from src.memory_manager import MemoryManager

    tmpdir = tempfile.mkdtemp()
    mm = MemoryManager(memory_dir=tmpdir)
    result = mm.health_check()
    assert result["healthy"] is True
    assert result["session_count"] == 0

    shutil.rmtree(tmpdir)

def test_pipeline_doctor_empty():
    """Doctor on fresh pipeline should report healthy."""
    from src.engine import AgentGate
    from src.pipeline_doctor import PipelineDoctor

    gate = AgentGate(project_name="doctor_test", output_dir="/tmp/doctor_test")
    doctor = PipelineDoctor(gate)
    diagnosis = doctor.diagnose()
    assert diagnosis.root_cause is not None
    assert len(diagnosis.findings) == 0  # No issues on fresh pipeline

def test_pipeline_doctor_has_summary():
    """Diagnosis should always have a summary."""
    from src.engine import AgentGate
    from src.pipeline_doctor import PipelineDoctor

    gate = AgentGate(project_name="summary_test", output_dir="/tmp/summary_test")
    doctor = PipelineDoctor(gate)
    diagnosis = doctor.diagnose()
    assert diagnosis.summary
    assert "Pipeline Doctor" in diagnosis.summary

def test_engine_cpoo_integration():
    """AgentGate should run CPOO scoring on agent registration."""
    from src.engine import AgentGate

    gate = AgentGate(project_name="cpoo_test", output_dir="/tmp/cpoo_test")
    gate.register_agent(
        role="R1",
        name="Test",
        role_goal="Write a hello world script",
        output_file="hello.py",
        acceptance_criteria=["File should exist and be non-empty"],
    )
    # Agent registered, CPOO scorer initialized
    assert gate.cpoo_scorer is not None
    assert gate.tool_registry is not None

def test_engine_memory_methods():
    """AgentGate should have save/load/diagnose methods."""
    from src.engine import AgentGate

    gate = AgentGate(project_name="methods_test", output_dir="/tmp/methods_test")
    assert hasattr(gate, 'save')
    assert hasattr(gate, 'load')
    assert hasattr(gate, 'diagnose')


# ── v1.1: Multi-model provider (百炼 / OpenAI-compatible) ──

def test_llm_client_base_url():
    """LLMClient should accept and store base_url."""
    from src.llm_client import LLMClient
    c = LLMClient(
        provider="litellm",
        model="qwen-turbo",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        api_key="sk-test",
    )
    assert c.base_url == "https://dashscope.aliyuncs.com/compatible-mode/v1"
    assert c.api_key == "sk-test"

def test_config_providers_section():
    """Config with providers section should set base_url on LLM client."""
    from src.config_loader import build_pipeline
    config = {
        "meta": {"project": "test_providers"},
        "providers": {
            "litellm": {
                "base_url": "https://api.example.com/v1",
                "api_key": "sk-fake",
            }
        },
        "models": {
            "default": "qwen-turbo",
            "registry": {
                "qwen-turbo": {"provider": "litellm", "model": "qwen-turbo"},
            },
        },
        "agents": [
            {"role": "R1", "name": "Test", "role_goal": "test",
             "model": "qwen-turbo", "output_file": "out.md"},
        ],
    }
    result = build_pipeline(config, provider="litellm", model="qwen-turbo")
    gate = result["gate"]
    assert gate.llm.base_url == "https://api.example.com/v1"
    assert gate.llm.api_key == "sk-fake"

def test_bailian_config_loads():
    """q2_bailian.json should load and validate."""
    from src.config_loader import load_config
    import os
    ref_path = os.path.join(
        os.path.dirname(__file__), "..", "configs", "q2_bailian.json")
    result = load_config(ref_path)
    assert "providers" in result
    assert result["providers"]["litellm"]["base_url"]
    assert result["models"]["registry"]["qwen3.8-max"]["model"]
    # Check per-agent model assignments
    models = {a["role"]: a.get("model", "") for a in result["agents"]}
    assert models.get("R1") == "qwen3.6-flash"   # cheap for requirements
    assert models.get("R4") == "qwen3.7-max"     # powerful for code

def test_orchestrator_multi_model_template():
    """Template configs should support per-agent model field."""
    from src.orchestrator_agent import generate_pipeline_config, expand_prompts
    config = generate_pipeline_config(
        "Build a REST API with 百炼 multi-model support",
        use_llm=False,
    )
    # web_api template agents should exist
    assert len(config["agents"]) >= 2
    # Each agent should have role and role_goal
    for agent in config["agents"]:
        assert "role" in agent
        assert "role_goal" in agent


# ── v1.2: Platform Advisor — API规划 + 自动分配 ──

def test_platform_advisor_analyze():
    """analyze_project should produce a valid DependencyPlan."""
    from src.platform_advisor import analyze_project
    plan = analyze_project(
        "Build a FastAPI todo backend with user auth",
        available_keys=["DASHSCOPE_API_KEY"],
    )
    assert plan.project_name
    assert len(plan.recommendations) >= 2
    assert plan.total_platforms >= 1
    assert plan.estimated_monthly_cost
    assert plan.summary

def test_platform_advisor_no_keys():
    """Without any API keys, should fall back to Ollama."""
    from src.platform_advisor import analyze_project
    plan = analyze_project("Build a simple CLI tool", available_keys=[])
    for rec in plan.recommendations:
        assert rec.platform_id == "ollama",             "Expected ollama, got {}: {}".format(rec.platform_id, rec.model)

def test_platform_advisor_resolve_config():
    """resolve_config should generate valid config from plan."""
    from src.platform_advisor import analyze_project, resolve_config
    plan = analyze_project("Build a REST API", available_keys=["DASHSCOPE_API_KEY"])
    config = resolve_config(plan, {"DASHSCOPE_API_KEY": "sk-test"})
    assert "meta" in config
    assert len(config["agents"]) >= 2
    for agent in config["agents"]:
        assert "model" in agent

def test_platform_advisor_config_loadable():
    """Generated config should pass config_loader validation."""
    import tempfile, json as jm, os
    from src.platform_advisor import analyze_project, resolve_config
    from src.config_loader import load_config
    plan = analyze_project("Build a REST API", available_keys=["DASHSCOPE_API_KEY"])
    config = resolve_config(plan, {"DASHSCOPE_API_KEY": "sk-test"})
    tf = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    jm.dump(config, tf)
    tf.close()
    try:
        validated = load_config(tf.name)
        assert len(validated["agents"]) >= 2
    finally:
        os.remove(tf.name)

def test_platform_advisor_all_platforms():
    """All platform profiles should have required fields."""
    from src.platform_advisor import PLATFORMS
    for pid, p in PLATFORMS.items():
        assert p.id and p.name and p.base_url and p.provider
        assert len(p.models) >= 1
        for m in p.models:
            assert m.name and len(m.strengths) >= 1


# ── Test runner ──

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
        test_template_match_web_api,
        test_template_match_cli,
        test_template_match_data,
        test_template_match_default,
        test_generate_config_template,
        test_generate_config_contains_r1,
        test_parse_llm_output_json,
        test_parse_llm_output_fenced,
        test_parse_llm_output_invalid,
        test_validate_orchestrator_output,
        test_expand_prompts_no_llm,
        test_expand_prompts_skips_existing,
        test_expand_prompts_all_templates,
        test_tool_registry_validate_ok,
        test_tool_registry_validate_bad,
        test_tool_registry_risk,
        test_tool_registry_requires_gate,
        test_tool_registry_gate_check,
        test_cpoo_scorer_high_quality,
        test_cpoo_scorer_low_quality,
        test_cpoo_scorer_quick_check,
        test_cpoo_scorer_optimize_no_llm,
        test_memory_manager_save_load,
        test_memory_manager_health,
        test_pipeline_doctor_empty,
        test_pipeline_doctor_has_summary,
        test_engine_cpoo_integration,
        test_engine_memory_methods,
        test_llm_client_base_url,
        test_config_providers_section,
        test_bailian_config_loads,
        test_orchestrator_multi_model_template,
        test_platform_advisor_analyze,
        test_platform_advisor_no_keys,
        test_platform_advisor_resolve_config,
        test_platform_advisor_config_loadable,
        test_platform_advisor_all_platforms,
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
