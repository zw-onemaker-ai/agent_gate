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
