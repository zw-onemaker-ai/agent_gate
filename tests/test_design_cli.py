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
    monkeypatch.setattr("src.design_cli.fetch_model_catalog",
                        lambda base_url, api_key, timeout=10: ["qwen3.7-plus"])
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("builtins.input", lambda prompt="": "n")
    rc = run_design("Build a Todo API", auto_confirm=False, expand=False)
    assert rc == 1
    saved = json_mod.load(open(str(tmp_path / "output/test_designed/design_config.json")))
    assert "providers" in saved
    assert saved["providers"]["litellm"]["api_key"] == "${DASHSCOPE_API_KEY}"
    raw = open(str(tmp_path / "output/test_designed/design_config.json")).read()
    assert "sk-local" not in raw  # raw key never persisted


STALE_CONFIG = {
    "meta": {"project": "stale_design"},
    "project": {"name": "stale_design", "description": "t"},
    "providers": {"dashscope": {"type": "api",
                                "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1"}},
    "models": {"default": "qwen-turbo",
               "registry": {"qwen-turbo": {"provider": "dashscope", "tier": "cheap"}}},
    "agents": [
        {"role": "R1", "name": "Req", "role_goal": "analyze",
         "model": "qwen-turbo", "output_file": "requirements.md"},
    ],
    "topology": {"stages": [{"agents": ["R1"]}]},
}


def test_align_replaces_stale_knowledge_with_live_catalog(monkeypatch, tmp_path):
    _ws_env(monkeypatch, tmp_path)
    live_catalog = ["qwen3.6-flash", "qwen3.7-plus", "qwen3.8-max"]
    monkeypatch.setattr("src.model_discovery.fetch_model_catalog",
                        lambda base_url, api_key, timeout=10: live_catalog)
    monkeypatch.setattr("src.design_cli.fetch_model_catalog",
                        lambda base_url, api_key, timeout=10: live_catalog)
    monkeypatch.setattr("src.design_cli.LLMClient",
                        lambda **kw: _FakeClient([json_mod.dumps(STALE_CONFIG)]))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("builtins.input", lambda prompt="": "n")
    rc = run_design("Build a Todo API", auto_confirm=False, expand=False)
    assert rc == 1
    saved = json_mod.load(open(str(tmp_path / "output/stale_design/design_config.json")))
    # base_url 对齐到工作区 (token-plan), 键名统一为 brain_provider (litellm)
    assert saved["providers"]["litellm"]["base_url"] == "http://x/v1"
    assert saved["providers"]["litellm"]["api_key"] == "${DASHSCOPE_API_KEY}"
    # registry 重建为实时档位, 旧名 qwen-turbo 消失
    reg = saved["models"]["registry"]
    assert "qwen-turbo" not in reg
    assert "qwen3.7-plus" in reg and reg["qwen3.7-plus"]["model"] == "qwen3.7-plus"
    # default 校正
    assert saved["models"]["default"] == "qwen3.7-plus"
    # agent 过期 model 字段被清理
    assert "model" not in saved["agents"][0]
    # raw key 不落盘
    raw = open(str(tmp_path / "output/stale_design/design_config.json")).read()
    assert "sk-local" not in raw


RAW_DESIGN_SCHEMA = {
    "meta": {"project": "raw_schema_design"},
    "project": {"name": "raw_schema_design", "description": "t"},
    "providers": {"dashscope": {"type": "api",
                                "base_url": "https://old.example.com/v1"}},
    "models": {"default": "qwen-turbo",
               "registry": {"qwen-turbo": {"provider": "dashscope",
                                           "model": "qwen-turbo"}}},
    "agents": [
        {"role": "R1", "name": "需求分析", "role_goal": "analyze",
         "output_file": "requirements.md"},
        {"role": "R4", "name": "后端开发", "role_goal": "implement",
         "output_file": "app.py"},
    ],
    "topology": {"stages": [
        {"stage": 1, "agents": ["R1"], "mode": "serial"},
        {"stage": 2, "agents": ["R4"], "mode": "serial", "on_fail": "R1"},
    ]},
    "context": {"routes": [
        {"agent": "R4", "requires": ["requirements.md", "app.py"]},
    ]},
    "design_notes": {
        "why_these_agents": "需求先行, 后端实现",
        "topology_strategy": "串行保证依赖顺序",
        "model_strategy": "按任务复杂度分配模型",
    },
}


def test_design_raw_schema_normalized_to_engine_schema(monkeypatch, tmp_path):
    """设计脑原生 schema 偏差 (dashscope键/routes格式/design_notes字段)
    必须被校正层归一化为引擎可加载格式 (v1.3.3+)。"""
    _ws_env(monkeypatch, tmp_path)
    live_catalog = ["qwen3.7-plus"]
    accept_map = {"R1": ["3+ user stories"], "R4": ["endpoint returns 200"]}
    monkeypatch.setattr("src.model_discovery.fetch_model_catalog",
                        lambda base_url, api_key, timeout=10: live_catalog)
    monkeypatch.setattr("src.design_cli.fetch_model_catalog",
                        lambda base_url, api_key, timeout=10: live_catalog)
    monkeypatch.setattr(
        "src.design_cli.LLMClient",
        lambda **kw: _FakeClient([json_mod.dumps(RAW_DESIGN_SCHEMA),
                                  json_mod.dumps(accept_map)]))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("builtins.input", lambda prompt="": "n")
    rc = run_design("raw schema", auto_confirm=False, expand=False)
    assert rc == 1  # HumanGate 处中止
    saved = json_mod.load(
        open(str(tmp_path / "output/raw_schema_design/design_config.json")))
    # providers 键名统一为 brain_provider (litellm)
    assert "litellm" in saved["providers"]
    assert "dashscope" not in saved["providers"]
    assert saved["providers"]["litellm"]["base_url"] == "http://x/v1"
    # registry provider 与 providers 键一致 (否则 build_pipeline 查不到 → base_url 静默丢失)
    for entry in saved["models"]["registry"].values():
        assert entry["provider"] in saved["providers"]
    # routes → 引擎 from/to 格式
    routes = saved["context"]["routes"]
    assert all("from" in r and "to" in r for r in routes)
    assert {"from": "R1", "to": "R4", "files": ["requirements.md"]} in routes
    # 自引用文件 (app.py 是 R4 自己产的) 不产生路由
    assert not any(r["files"] == ["app.py"] for r in routes)
    # design_notes 未知字段折入 why_these_agents
    dn = saved["design_notes"]
    assert "topology_strategy" not in dn and "model_strategy" not in dn
    assert "topology_strategy" in dn.get("why_these_agents", "")
    assert "model_strategy" in dn.get("why_these_agents", "")
    # 验收锚定缺口 → 设计脑补全回环
    assert saved["agents"][0]["acceptance_criteria"] == ["3+ user stories"]
    assert saved["agents"][1]["acceptance_criteria"] == ["endpoint returns 200"]
    # 引擎 load_config 可直接加载 (无校验错误)
    from src.config_loader import load_config
    load_config(str(tmp_path / "output/raw_schema_design/design_config.json"))


def test_acceptance_missing_blocks_execution(monkeypatch, tmp_path, capsys):
    """验收锚定缺失且设计脑补全失败 → 拒绝执行 (即使 --yes), 不静默降级 (v1.3.3+)。"""
    _ws_env(monkeypatch, tmp_path)
    live_catalog = ["qwen3.7-plus"]
    monkeypatch.setattr("src.model_discovery.fetch_model_catalog",
                        lambda base_url, api_key, timeout=10: live_catalog)
    monkeypatch.setattr("src.design_cli.fetch_model_catalog",
                        lambda base_url, api_key, timeout=10: live_catalog)
    # 第二次 LLM 调用 (补全回环) 返回非法 JSON → 补全失败
    monkeypatch.setattr(
        "src.design_cli.LLMClient",
        lambda **kw: _FakeClient([json_mod.dumps(RAW_DESIGN_SCHEMA), "not json"]))
    monkeypatch.chdir(tmp_path)
    rc = run_design("raw schema", auto_confirm=True, expand=False)
    out = capsys.readouterr().out
    assert rc == 1  # 拦截: 拒绝执行
    assert "验收锚定缺失" in out
    assert "R1" in out and "R4" in out  # 缺失角色被点名


def test_design_prompt_mandates_acceptance_criteria():
    """设计脑 prompt 必须在输出格式示例中展示 acceptance_criteria,
    且规则8 强制每个 agent 带 2-5 条 (v1.3.3+)。"""
    from src.orchestrator_agent import ORCHESTRATOR_SYSTEM
    assert '"acceptance_criteria"' in ORCHESTRATOR_SYSTEM
    assert "MUST include 2-5 acceptance_criteria" in ORCHESTRATOR_SYSTEM


def test_acceptance_fill_prompt_carries_project_context():
    """补全回环提示词必须携带项目描述 + agent role_goal —
    回归: 缺失时设计脑会跑偏到别的项目域 (2026-08-14 活体实测)。"""
    from src.design_cli import _fill_missing_acceptance
    config = {"agents": [
        {"role": "R4", "name": "后端开发",
         "role_goal": "Implement FastAPI CRUD for Todo API",
         "output_file": "app.py"},
    ]}
    seen = []

    def spy_call_llm(prompt, user_prompt=""):
        seen.append(prompt)
        return '{"R4": ["health endpoint returns 200"]}'

    cfg, missing = _fill_missing_acceptance(
        config, spy_call_llm, description="Build a Todo API backend")
    assert missing == []
    prompt = seen[0]
    assert "Build a Todo API backend" in prompt  # 项目描述
    assert "FastAPI CRUD" in prompt              # agent role_goal
    assert cfg["agents"][0]["acceptance_criteria"] == ["health endpoint returns 200"]
