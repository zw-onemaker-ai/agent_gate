"""v1.3.4 regression tests — the five E2E fixes (2026-08-17).

Root cause chain of the Todo E2E first run:
  output not sanitized → app.py syntax error → pytest cascade →
  unjust R7 loopback → polluted loopback context → 5-loopback cap.

Each test targets one defect from the v1.3.4 fix list:
  ① output sanitization (narrative/fences/Part labels + py_compile hard gate)
  ② verify_cmd strengthening (code files, no pure-grep gates)
  ③ loopback context sanitization (no verify-command traces)
  ④ meta.description → initial_context fallback
  ⑤ CPOO 60-80 dead-zone (stuck score) fix
"""
import os
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from src.models import GateStatus, LoopbackTarget
from src.validators import (
    bash_verify_files,
    quality_gate_check,
    sanitize_agent_output,
    sanitize_loopback_context,
    strengthen_verify_cmd,
)
from src.cpoo_scorer import CPOOScorer
from src.engine import AgentGate


# ── ① Output sanitization ──

def test_sanitize_extracts_fenced_python():
    raw = ("Sure, here is your backend code:\n\n"
           "```python\nimport os\n\ndef main():\n    return os.getcwd()\n```\n\n"
           "Hope that helps!")
    clean = sanitize_agent_output(raw, "backend/main.py")
    assert clean.startswith("import os")
    assert "Sure, here is" not in clean
    assert "```" not in clean


def test_sanitize_strips_narrative_before_code():
    raw = ("I'll implement the API now.\n"
           "The file will contain FastAPI routes.\n"
           "import os\nfrom fastapi import FastAPI\n\napp = FastAPI()\n")
    clean = sanitize_agent_output(raw, "backend/main.py")
    assert clean.startswith("import os")
    assert "I'll implement" not in clean
    assert "FastAPI routes." not in clean


def test_sanitize_strips_part_labels_and_contract():
    raw = ("### Part A (deliverable)\nimport os\n"
           "CONTRACT_START\nsummary: wrote backend\noutput_files: backend/main.py\n"
           "CONTRACT_END\n")
    clean = sanitize_agent_output(raw, "backend/main.py")
    assert "Part A" not in clean
    assert "CONTRACT" not in clean
    assert clean.strip() == "import os"


def test_sanitize_docs_keep_inner_code_fences():
    raw = ("# README\n\nUsage example:\n\n```python\nprint('hi')\n```\n")
    clean = sanitize_agent_output(raw, "README.md")
    assert "print('hi')" in clean  # inner fence content survives
    assert "# README" in clean


def test_sanitize_outer_fence_on_md():
    raw = "```markdown\n# README\n\nContent here.\n```"
    clean = sanitize_agent_output(raw, "README.md")
    assert clean.startswith("# README")
    assert "```" not in clean


def test_verify_files_catches_syntax_error():
    # v1.3.4 regression: py_compile returncode was ignored — this used to
    # record syntax_ok=True and PASS the file
    bad = "output_bad_syntax.py"
    with open(bad, "w") as f:
        f.write("def broken(:\n    pass\n")
    try:
        result = bash_verify_files([bad])
        info = result["files"][bad]
        assert info["syntax_ok"] is False
        assert result["status"] == GateStatus.FAIL
    finally:
        os.remove(bad)


def test_gate_fails_on_syntax_error_and_routes_backend():
    bad = "output_gate_syntax.py"
    with open(bad, "w") as f:
        f.write("def broken(:\n")
    try:
        result = quality_gate_check("R4", [bad], "EXIT:0")
        assert result.status == GateStatus.FAIL
        assert any("syntax error" in r.lower() for r in result.fail_reasons)
        assert result.loopback_target == LoopbackTarget.BACKEND
    finally:
        os.remove(bad)


# ── ② verify_cmd strengthening ──

def test_strengthen_prepends_pycompile_for_pure_grep():
    cmd = strengthen_verify_cmd("grep -c 'def main' out/app.py", "out/app.py")
    assert cmd.startswith("python3 -m py_compile")


def test_strengthen_keeps_existing_syntax_check():
    cmd = "python3 -m py_compile out/app.py && grep -c main out/app.py"
    assert strengthen_verify_cmd(cmd, "out/app.py") == cmd


def test_strengthen_skips_non_code_files():
    cmd = "grep -c 'user story' out/requirements.md"
    assert strengthen_verify_cmd(cmd, "out/requirements.md") == cmd


# ── ③ Loopback context sanitization ──

def test_loopback_context_strips_command_traces():
    polluted = (
        "[LOOPBACK] FAIL: Python syntax error in out/app.py\n"
        "[CONTEXT] Upstream: wrote backend\n"
        "python3 -m py_compile 'out/app.py'\n"
        "$ pytest tests/\n"
        "Traceback (most recent call last):\n"
        '  File "out/app.py", line 3\n'
        "SyntaxError: invalid syntax\n"
        "Verification command failed (EXIT:1)"
    )
    clean = sanitize_loopback_context(polluted)
    assert "python3 -m py_compile" not in clean
    assert "pytest" not in clean
    assert "Traceback" not in clean
    assert 'File "out/app.py"' not in clean
    assert "EXIT:1" not in clean
    # Human-readable reasons survive
    assert "[LOOPBACK] FAIL: Python syntax error" in clean
    assert "Verification command failed" in clean


def test_loopback_context_keeps_plain_reasons():
    text = "Missing acceptance criteria for agent R2"
    assert sanitize_loopback_context(text) == text


# ── ④ meta.description fallback ──

def test_run_pipeline_falls_back_to_project_description(tmp_path):
    gate = AgentGate(project_name="todo", output_dir=str(tmp_path))
    gate.project_description = "Build a Todo API backend"
    gate.register_agent(
        role="R1", name="req", prompt_template="do it", verify_cmd="echo OK",
        output_file="r.md")

    seen = []

    def fake_single(role, context):
        seen.append(context)
        return SimpleNamespace(quality_gate=GateStatus.PASS, context_card="")

    gate._run_single_agent = fake_single
    gate.run_pipeline(initial_context="", stages=[{"roles": ["R1"], "parallel": False}])
    assert seen and seen[0] == "Build a Todo API backend"


# ── ⑤ CPOO dead-zone fix ──

def test_cpoo_pattern_fix_converges_stuck_score():
    # v1.3.4 regression: a prompt scoring in the 60-80 band never got fixed —
    # the score stayed at 66/67 across 3 rounds
    scorer = CPOOScorer(call_llm_fn=None)
    weak = "You are an expert engineer. Your role is to deliver the output."
    before = scorer.score(weak).total
    fixed = scorer.pattern_fix(weak)
    after = scorer.score(fixed).total
    assert before < 80, "test prompt should start below threshold"
    assert after > before, "pattern fix must improve the score"
    assert after >= 80, "pattern fix should converge to PASS (got {})".format(after)


def test_cpoo_io_template_matches_patterns():
    # v1.3.4 regression: the old IO template scored 0 points against its own
    # module patterns ("Input:" instead of "Input format:")
    scorer = CPOOScorer(call_llm_fn=None)
    fixed = scorer.pattern_fix("weak prompt")
    io_modules = [m for m in scorer.score(fixed).modules if m.name == "IO Format"]
    assert io_modules and io_modules[0].score == 20


# ── Engine integration: sanitize before write ──

def test_engine_sanitizes_output_before_write(tmp_path, monkeypatch):
    gate = AgentGate(project_name="todo", output_dir=str(tmp_path))
    monkeypatch.setattr(gate.cpoo_scorer, "call_llm_fn", None)  # no LLM rewrite
    gate.register_agent(
        role="R4", name="backend",
        prompt_template="You are an expert engineer.\n"
                        "- 🔴 MUST: complete output\n- 🟡 SHOULD: clean\n"
                        "## Workflow\n1. read input\n2. produce output\n"
                        "3. self-verify and record EXIT_CODE\n"
                        "## IO Format\n**Input format:** context\n"
                        "**Output format:** deliverable to output file\n"
                        "**Output file path:** specified\n"
                        "## Quality\nquality gate checked; self-check; "
                        "complete with no TODO",
        verify_cmd="echo OK", output_file="backend/main.py")

    raw = ("Here is the FastAPI backend:\n\n"
           "```python\nimport os\n\ndef main():\n    return 'ok'\n```\n")
    monkeypatch.setattr(
        gate.llm, "call",
        lambda system_prompt=None, user_prompt=None: SimpleNamespace(
            ok=True, content=raw, error=None))

    out = gate.run_step("R4", context="Build a Todo API")
    written = (tmp_path / "backend" / "main.py").read_text()
    assert written.startswith("import os")
    assert "Here is the FastAPI" not in written
    assert out.quality_gate == GateStatus.PASS


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
