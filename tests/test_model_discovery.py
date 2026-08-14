"""Tests for Model Discovery — L0 bootstrap (v1.3).

All HTTP is mocked: these tests run without any real API key.
"""
import json as json_mod
import urllib.error

import pytest

from src.model_discovery import (
    ModelDiscoveryError,
    check_registry,
    fetch_model_catalog,
    pick_model,
)


class _FakeResponse(object):
    def __init__(self, payload, status=200):
        self.payload = payload
        self.status = status

    def read(self):
        return json_mod.dumps(self.payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _catalog(ids):
    return {"data": [{"id": i, "object": "model"} for i in ids]}


def test_fetch_parses_openai_format(monkeypatch):
    captured = {}

    def fake_open(req, timeout=None):
        captured["auth"] = req.get_header("Authorization")
        captured["url"] = req.full_url
        return _FakeResponse(_catalog(["qwen3.7-flash", "qwen3.8-max"]))

    monkeypatch.setattr("src.model_discovery.urllib.request.urlopen", fake_open)
    models = fetch_model_catalog("https://example.com/v1", "sk-test")
    assert models == ["qwen3.7-flash", "qwen3.8-max"]
    assert captured["auth"] == "Bearer sk-test"
    assert captured["url"] == "https://example.com/v1/models"


def test_fetch_http_401_raises(monkeypatch):
    def fake_open(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {}, None)

    monkeypatch.setattr("src.model_discovery.urllib.request.urlopen", fake_open)
    with pytest.raises(ModelDiscoveryError) as exc:
        fetch_model_catalog("https://example.com/v1", "bad-key")
    assert exc.value.status == 401


def test_fetch_network_error_raises(monkeypatch):
    def fake_open(req, timeout=None):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr("src.model_discovery.urllib.request.urlopen", fake_open)
    with pytest.raises(ModelDiscoveryError):
        fetch_model_catalog("https://example.com/v1", "sk-test")


def test_pick_strong_prefers_max_over_flash():
    catalog = ["qwen3.7-flash", "qwen3.8-max", "qwen3.7-plus"]
    assert pick_model(catalog, "strong") == "qwen3.8-max"


def test_pick_cheap_prefers_flash():
    catalog = ["qwen3.7-flash", "qwen3.8-max", "qwen3.7-plus"]
    assert pick_model(catalog, "cheap") == "qwen3.7-flash"


def test_pick_balanced_prefers_mid_tier():
    catalog = ["qwen3.7-flash", "qwen3.8-max", "qwen3.7-plus"]
    assert pick_model(catalog, "balanced") == "qwen3.7-plus"


def test_pick_version_bonus_beats_older_generation():
    # same keyword "max": newer generation wins
    catalog = ["qwen-max", "qwen3.8-max"]
    assert pick_model(catalog, "strong") == "qwen3.8-max"


def test_pick_excludes_non_chat_models():
    catalog = ["qwen3-rerank", "qwen-image-3.0-pro", "text-embedding-v4",
               "qwen3.7-flash"]
    assert pick_model(catalog, "strong") == "qwen3.7-flash"


def test_pick_degrades_when_tier_absent():
    # only cheap models live in this account
    catalog = ["qwen3.7-flash"]
    assert pick_model(catalog, "strong") == "qwen3.7-flash"


def test_pick_empty_catalog_returns_fallback():
    assert pick_model([], "strong", fallback="offline-default") == "offline-default"


def test_check_registry_missing_with_suggestions():
    catalog = ["qwen3.7-flash", "qwen3.7-plus", "qwen3.8-max"]
    result = check_registry(catalog, ["qwen-turbo", "qwen3.7-plus"])
    assert result["missing"] == ["qwen-turbo"]
    # suggestion family "qwen" → strongest live chat model
    assert result["suggestions"]["qwen-turbo"] == "qwen3.8-max"


def test_check_registry_all_alive():
    catalog = ["qwen3.7-flash", "qwen3.7-plus"]
    result = check_registry(catalog, ["qwen3.7-flash"])
    assert result == {"missing": [], "suggestions": {}}


# ── Workspace integration (design brain bootstrap) ──────────────────────

def _write_ws(tmp_path, design_brain):
    import json as json_mod
    ws_file = tmp_path / "workspace.json"
    ws_file.write_text(json_mod.dumps(
        {"design_brain": design_brain, "preference": "quality"}))
    return str(ws_file)


def test_workspace_discovers_brain_model(monkeypatch, tmp_path):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-local")
    monkeypatch.setenv("AGENTGATE_WORKSPACE", _write_ws(
        tmp_path, {"provider": "litellm",
                   "base_url": "http://127.0.0.1:18765/v1"}))
    monkeypatch.setattr(
        "src.model_discovery.fetch_model_catalog",
        lambda base_url, api_key, timeout=10: ["qwen3.7-flash", "qwen3.8-max"])
    from src.workspace import detect_workspace
    cfg = detect_workspace()
    assert cfg.brain_model == "qwen3.8-max"  # quality preference → strong
    assert cfg.available_models == ["qwen3.7-flash", "qwen3.8-max"]


def test_workspace_explicit_model_wins(monkeypatch, tmp_path):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-local")
    monkeypatch.setenv("AGENTGATE_WORKSPACE", _write_ws(
        tmp_path, {"provider": "litellm", "model": "my-custom-model",
                   "base_url": "http://127.0.0.1:18765/v1"}))
    monkeypatch.setattr(
        "src.model_discovery.fetch_model_catalog",
        lambda base_url, api_key, timeout=10: ["qwen3.8-max"])
    from src.workspace import detect_workspace
    cfg = detect_workspace()
    assert cfg.brain_model == "my-custom-model"


def test_workspace_falls_back_when_probe_fails(monkeypatch, tmp_path):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-local")
    monkeypatch.setenv("AGENTGATE_WORKSPACE", _write_ws(
        tmp_path, {"provider": "litellm",
                   "base_url": "http://127.0.0.1:19999/v1"}))
    import src.model_discovery

    def boom(base_url, api_key, timeout=10):
        raise src.model_discovery.ModelDiscoveryError("down")
    monkeypatch.setattr("src.model_discovery.fetch_model_catalog", boom)
    from src.workspace import detect_workspace
    cfg = detect_workspace()
    assert cfg.brain_model == "qwen3.7-plus"  # offline fallback
    assert cfg.available_models == []


def test_fetch_401_surfaces_provider_error_body(monkeypatch):
    import io

    def fake_open(req, timeout=None):
        raise urllib.error.HTTPError(
            req.full_url, 401, "Unauthorized", {},
            io.BytesIO(b'{"code":"InvalidApiKey","message":"API key is invalid"}'))

    monkeypatch.setattr("src.model_discovery.urllib.request.urlopen", fake_open)
    with pytest.raises(ModelDiscoveryError) as exc:
        fetch_model_catalog("https://example.com/v1", "wrong-key")
    assert exc.value.status == 401
    assert "API key is invalid" in str(exc.value)


def test_cheap_tier_prefers_known_family_over_unknown():
    # glm-5.2 has no tier keyword (score ~0) — low score must not mean cheap
    catalog = ["glm-5.2", "qwen3.6-flash"]
    assert pick_model(catalog, "cheap") == "qwen3.6-flash"


def test_workspace_respects_base_url_env_override(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-local")
    monkeypatch.setenv("DASHSCOPE_BASE_URL", "https://token-plan.example.com/compatible-mode/v1")
    monkeypatch.setenv("AGENTGATE_WORKSPACE", "/tmp/nonexistent_ws.json")
    captured = {}

    def fake_fetch(base_url, api_key, timeout=10):
        captured["base_url"] = base_url
        return ["qwen3.8-max"]

    monkeypatch.setattr("src.model_discovery.fetch_model_catalog", fake_fetch)
    from src.workspace import detect_workspace
    cfg = detect_workspace()
    assert cfg.brain_base_url == "https://token-plan.example.com/compatible-mode/v1"
    assert captured["base_url"] == cfg.brain_base_url


def test_check_models_401_hint(monkeypatch, capsys):
    import src.model_discovery
    import run as run_mod

    def fake_fetch(base_url, api_key, timeout=10):
        raise src.model_discovery.ModelDiscoveryError("HTTP 401", status=401)

    monkeypatch.setattr("src.model_discovery.fetch_model_catalog", fake_fetch)
    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-fake")
    rc = run_mod.run_check_models(config_path=None)
    out = capsys.readouterr().out
    assert rc == 1
    assert "DASHSCOPE_BASE_URL" in out  # actionable hint shown


def test_check_models_validates_resolved_names_not_aliases(monkeypatch, capsys, tmp_path):
    import json as json_mod
    import run as run_mod

    cfg = {
        "project": {"name": "t", "description": "t"},
        "providers": {"relay": {"base_url": "http://x/v1", "api_key": "${RELAY_KEY}"}},
        "models": {"default": "real-flash-0731", "registry": {
            "cheap-alias": {"provider": "relay", "model": "real-flash-0731"},
        }},
        "agents": [{"role": "R1", "name": "T", "role_goal": "t", "output_file": "o.md"}],
    }
    cfg_file = tmp_path / "cfg.json"
    cfg_file.write_text(json_mod.dumps(cfg))
    monkeypatch.setenv("RELAY_KEY", "sk-x")
    monkeypatch.setattr(
        "src.model_discovery.fetch_model_catalog",
        lambda base_url, api_key, timeout=10: ["real-flash-0731", "real-pro"])
    rc = run_mod.run_check_models(config_path=str(cfg_file))
    out = capsys.readouterr().out
    assert rc == 0
    assert "alive" in out  # alias is local-only, resolved name is what matters
