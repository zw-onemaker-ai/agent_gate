"""Unified LLM client — provider-agnostic abstraction (Phase 1)."""

import json
from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class LLMResponse:
    content: str
    model: str = ""
    provider: str = ""
    tokens_used: int = 0
    error: Optional[str] = None

    @property
    def ok(self):
        return self.error is None


@dataclass
class JudgeResult:
    passed: bool
    reason: str
    criteria_checked: List[str] = field(default_factory=list)


class LLMClient:
    """Unified LLM call abstraction.

    Supports: Ollama (local), OpenAI, LiteLLM (100+ providers).
    Provider + model resolved from config.models.registry.
    """

    def __init__(self, provider="ollama", model="qwen2.5:7b", base_url=None, api_key=None,
                 timeout=300):
        self.provider = provider
        self.model = model
        self.base_url = base_url
        self.api_key = api_key
        self.timeout = timeout  # seconds; CPU-only local inference can be slow

    def call(self, system_prompt, user_prompt="", provider=None, model=None,
             base_url=None, api_key=None):
        # type: (str, str, str, str, str, str) -> LLMResponse
        """Make a single LLM call. Returns LLMResponse.

        Args:
            provider: Override default provider (ollama/openai/litellm).
            model: Override default model name.
            base_url: Override API base URL (for OpenAI-compatible endpoints).
            api_key: Override API key.
        """
        p = provider or self.provider
        m = model or self.model
        url = base_url or self.base_url
        key = api_key or self.api_key

        if p == "ollama":
            return self._call_ollama(m, system_prompt, user_prompt)
        elif p in ("openai", "litellm"):
            return self._call_litellm(m, system_prompt, user_prompt,
                                       base_url=url, api_key=key)
        else:
            return LLMResponse(
                content="", provider=p, model=m,
                error="Unknown provider: {}".format(p))

    # ── Provider implementations ──

    def _call_ollama(self, model, system, user):
        # type: (str, str, str) -> LLMResponse
        import urllib.request
        payload = json.dumps({
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user or "Proceed with your task."},
            ],
            "stream": False,
        }).encode()
        req = urllib.request.Request(
            "http://localhost:11434/api/chat",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read())
                return LLMResponse(
                    content=data.get("message", {}).get("content", ""),
                    model=model, provider="ollama",
                    tokens_used=data.get("eval_count", 0),
                )
        except Exception as e:
            return LLMResponse(
                content="", model=model, provider="ollama",
                error="OLLAMA_ERROR: {}".format(e))

    def _call_litellm(self, model, system, user, base_url=None, api_key=None):
        # type: (str, str, str, str, str) -> LLMResponse
        try:
            from litellm import completion
            kwargs = {
                "model": model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user or "Proceed with your task."},
                ],
            }
            if base_url:
                kwargs["api_base"] = base_url
            if api_key:
                kwargs["api_key"] = api_key
            resp = completion(**kwargs)
            return LLMResponse(
                content=resp.choices[0].message.content,
                model=model, provider="litellm",
                tokens_used=resp.usage.total_tokens if hasattr(resp, 'usage') else 0,
            )
        except ImportError:
            return LLMResponse(
                content="", model=model, provider="litellm",
                error="LITELLM_NOT_INSTALLED: pip install litellm")
        except Exception as e:
            return LLMResponse(
                content="", model=model, provider="litellm",
                error="LITELLM_ERROR: {}".format(e))

    # ── LLM-as-Judge ──

    def judge(self, criteria, output, model=None):
        # type: (list, str, str) -> JudgeResult
        """Evaluate output quality against criteria (LLM-as-Judge).

        Uses a separate lightweight LLM call for evaluation.
        """
        if not criteria:
            return JudgeResult(passed=True, reason="No criteria to check")

        judge_prompt = """You are a quality evaluator. Assess whether the output meets each criterion.

Output to evaluate:
---
{}
---

Criteria:
{}

Respond with JSON only:
{{"passed": true/false, "reason": "brief explanation", "results": [
  {{"criteria": "...", "met": true/false, "note": "..."}}
]}}""".format(output[:4000], "\n".join("- {}".format(c) for c in criteria))

        resp = self.call(
            system_prompt="You are a QA evaluator. Output JSON only.",
            user_prompt=judge_prompt,
            model=model or self.model,
        )

        if not resp.ok:
            return JudgeResult(
                passed=False,
                reason="Judge call failed: {}".format(resp.error),
                criteria_checked=criteria,
            )

        try:
            content = resp.content.strip()
            # Handle markdown code block wrapping
            if content.startswith("```"):
                content = content.split("\n", 1)[1]
                if content.endswith("```"):
                    content = content[:-3]
            data = json.loads(content)
            return JudgeResult(
                passed=data.get("passed", False),
                reason=data.get("reason", "No reason given"),
                criteria_checked=[
                    r.get("criteria", "") for r in data.get("results", [])
                ],
            )
        except (json.JSONDecodeError, KeyError) as e:
            return JudgeResult(
                passed=False,
                reason="Judge response parse error: {}".format(e),
                criteria_checked=criteria,
            )

    # ── Env validation ──

    @staticmethod
    def validate_env(registry):
        # type: (dict) -> list
        """Validate env completeness for configured providers.

        Returns list of missing config warnings (empty = all good).
        """
        warnings = []
        providers_seen = set()
        for alias, entry in registry.items():
            provider = entry.get("provider", "")
            providers_seen.add(provider)

        if "ollama" in providers_seen:
            import os
            host = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
            warnings.append("Ollama host: {} (ensure service is running)".format(host))

        if "openai" in providers_seen:
            import os
            if not os.environ.get("OPENAI_API_KEY"):
                warnings.append("OPENAI_API_KEY not set — OpenAI calls will fail")

        return warnings
