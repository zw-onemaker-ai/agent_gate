"""Tests for the OpenAI-compatible HTTP transport (v1.3.3)."""
import json as json_mod

from src.llm_client import LLMClient


class _FakeHTTPResp(object):
    def __init__(self, payload, status=200):
        self.payload = payload
        self.status = status

    def read(self):
        return json_mod.dumps(self.payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_openai_compatible_call_hits_chat_completions(monkeypatch):
    captured = {}

    def fake_open(req, timeout=None):
        captured["url"] = req.full_url
        captured["auth"] = req.get_header("Authorization")
        captured["body"] = json_mod.loads(req.data.decode("utf-8"))
        return _FakeHTTPResp({
            "choices": [{"message": {"content": "pong"}}],
            "usage": {"total_tokens": 7},
        })

    monkeypatch.setattr("urllib.request.urlopen", fake_open)
    client = LLMClient(provider="litellm", model="qwen3.7-plus",
                       base_url="https://token-plan.example.com/compatible-mode/v1",
                       api_key="sk-x")
    r = client.call(system_prompt="sys", user_prompt="ping")
    assert r.ok
    assert r.content == "pong"
    assert r.tokens_used == 7
    assert captured["url"] == "https://token-plan.example.com/compatible-mode/v1/chat/completions"
    assert captured["auth"] == "Bearer sk-x"
    assert captured["body"]["model"] == "qwen3.7-plus"


def test_openai_compatible_call_http_error_surfaces_body(monkeypatch):
    import urllib.error
    import io

    def fake_open(req, timeout=None):
        raise urllib.error.HTTPError(
            req.full_url, 400, "Bad Request", {},
            io.BytesIO(b'{"error":{"message":"model not found"}}'))

    monkeypatch.setattr("urllib.request.urlopen", fake_open)
    client = LLMClient(provider="litellm", model="nope",
                       base_url="http://x/v1", api_key="sk-x")
    r = client.call(system_prompt="s", user_prompt="u")
    assert not r.ok
    assert "model not found" in r.error
    assert "HTTP 400" in r.error
