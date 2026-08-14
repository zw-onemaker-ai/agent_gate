"""Tests for Design Brain CLI wiring (v1.3.3). All LLM calls mocked."""
import json as json_mod
from types import SimpleNamespace

import pytest

from src.design_cli import run_design


class _FakeLLMResult(object):
    def __init__(self, content="", error=None):
        self.content = content
        self.error = error


class _FakeClient(object):
    def __init__(self, outputs):
        # outputs: list of contents returned per call
        self.outputs = list(outputs)
        self.calls = []

    def call(self, system_prompt="", user_prompt=""):
        self.calls.append(user_prompt)
        if self.outputs:
            content = self.outputs.pop(0)
        else:
            content = "{}"
        return _FakeLLMResult(content=content)


GOOD_CONFIG = {
    "meta": {"project": "test_designed"},
    "project": {"name": "test_designed", "description": "t"},
    "models": {"default": "qwen3.7-plus",
               "registry": {"qwen3.7-plus": {"provider": "litellm",
                                             "model": "qwen3.7-plus"}}},
    "providers": {"litellm": {"base_url": "http://x/v1",
                              "api_key": "${DASHSCOPE_API_KEY}"}},
    "agents": [
        {"role": "R1", "name": "Req", "role_goal": "analyze",
         "output_file": "requirements.md",
         "acceptance_criteria": ["3 user stories"]},
    ],
    "topology": {"stages": [{"stage": 1, "agents": ["R1"], "mode": "serial"}]},
}


def _ws_env(monkeypatch, tmp_path):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-local")
    monkeypatch.setenv("DASHSCOPE_BASE_URL", "http://x/v1")
    monkeypatch.setenv("AGENTGATE_WORKSPACE", str(tmp_path / "no_ws.json"))
    monkeypatch.setattr(
        "src.model_discovery.fetch_model_catalog",
        lambda base_url, api_key, timeout=10: ["qwen3.7-plus"])


def test_design_generates_config_and_saves(monkeypatch, tmp_path, capsys):
    _ws_env(monkeypatch, tmp_path)
    monkeypatch.setattr("src.design_cli.LLMClient",
                        lambda **kw: _FakeClient([json_mod.dumps(GOOD_CONFIG)]))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("builtins.input", lambda prompt="": "n")  # HumanGate: abort
    rc = run_design("Build a Todo API", auto_confirm=False, expand=False)
    out = capsys.readouterr().out
    assert rc == 1  # aborted at HumanGate by design
    assert "Pipeline plan" in out
    cfg = json_mod.load(open(str(tmp_path / "output/test_designed/design_config.json")))
    assert cfg["agents"][0]["role"] == "R1"


def test_design_llm_failure_falls_back_to_template(monkeypatch, tmp_path, capsys):
    _ws_env(monkeypatch, tmp_path)
    # LLM returns garbage → template fallback inside generate_pipeline_config
    monkeypatch.setattr("src.design_cli.LLMClient",
                        lambda **kw: _FakeClient(["not json at all"]))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("builtins.input", lambda prompt="": "n")
    rc = run_design("Build a web API", auto_confirm=False, expand=False)
    out = capsys.readouterr().out
    assert rc == 1
    assert "Pipeline plan" in out  # template config still produced a plan


def test_design_auto_confirm_runs_pipeline(monkeypatch, tmp_path, capsys):
    _ws_env(monkeypatch, tmp_path)
    monkeypatch.setattr("src.design_cli.LLMClient",
                        lambda **kw: _FakeClient([json_mod.dumps(GOOD_CONFIG)]))
    monkeypatch.chdir(tmp_path)

    class _FakeGate(object):
        def run_pipeline(self, initial_context="", stages=None):
            return {"ok": True}

        def summary(self):
            return "fake summary"

    monkeypatch.setattr("src.design_cli.build_pipeline",
                        lambda config, provider="", model="", output_dir=None: {
                            "gate": _FakeGate(), "stages": None})
    rc = run_design("Build a Todo API", auto_confirm=True, expand=False)
    out = capsys.readouterr().out
    assert rc == 0
    assert "fake summary" in out


def test_design_injects_provider_without_persisting_raw_key(monkeypatch, tmp_path):
    _ws_env(monkeypatch, tmp_path)
    bare_config = {k: v for k, v in GOOD_CONFIG.items() if k != "providers"}
    monkeypatch.setattr("src.design_cli.LLMClient",
                        lambda **kw: _FakeClient([json_mod.dumps(bare_config)]))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("builtins.input", lambda prompt="": "n")
    rc = run_design("Build a Todo API", auto_confirm=False, expand=False)
    assert rc == 1
    saved = json_mod.load(open(str(tmp_path / "output/test_designed/design_config.json")))
    assert "providers" in saved
    assert saved["providers"]["litellm"]["api_key"] == "${DASHSCOPE_API_KEY}"
    raw = open(str(tmp_path / "output/test_designed/design_config.json")).read()
    assert "sk-local" not in raw  # raw key never persisted
