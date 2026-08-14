"""Model Discovery — L0 bootstrap (v1.3).

Given a provider base_url + api_key, fetch the account's real-time
model catalog via the OpenAI-compatible GET /models endpoint.

This is a pure HTTP call — no LLM involved. It breaks the
chicken-and-egg problem: you can discover which models exist
before choosing one.

Works with any OpenAI-compatible provider:
  - 百炼 DashScope   https://dashscope.aliyuncs.com/compatible-mode/v1
  - 火山方舟         https://ark.cn-beijing.volces.com/api/v3
  - DeepSeek         https://api.deepseek.com/v1
  - Relay gateways   https://<gateway>/v1  (one-api / new-api style)
"""

import json as json_mod
import re
import urllib.request


class ModelDiscoveryError(Exception):
    """Raised when the /models probe fails (network, auth, HTTP status)."""

    def __init__(self, message, status=None):
        # type: (str, int) -> None
        super(ModelDiscoveryError, self).__init__(message)
        self.message = message
        self.status = status


# ── Offline tier templates (the "guess" layer) ──────────────────────────
# Keyword rank per model id. Higher = stronger tier. Templates go stale,
# the live catalog is authoritative — these only provide a first guess.
KEYWORD_RANK = {
    "max": 3, "seed": 3, "reasoner": 3, "opus": 3, "ultra": 3,
    "plus": 2, "pro": 2, "sonnet": 2, "chat": 2, "turbo": 2,
    "flash": 1, "lite": 1, "mini": 1, "haiku": 1, "air": 1,
}

# Model families that are NOT chat models — excluded from picking.
NON_CHAT_KEYWORDS = (
    "embedding", "rerank", "asr", "tts", "image-", "whisper",
    "moderation", "speech", "video-", "transcription",
)


def _is_chat_model(model_id):
    # type: (str) -> bool
    low = model_id.lower()
    for kw in NON_CHAT_KEYWORDS:
        if kw in low:
            return False
    return True


def _score_model(model_id):
    # type: (str) -> float
    """Score a model id: keyword rank + semantic version bonus.

    qwen3.8-max → 3 + 0.038 = 3.038 (beats qwen3.7-plus → 2 + 0.037).
    doubao-seed-1-6-250615 → 3 + 0.0 (no x.y version, rank alone).
    """
    low = model_id.lower()
    rank = 0
    for kw, r in KEYWORD_RANK.items():
        if kw in low:
            rank = max(rank, r)
    version_bonus = 0.0
    m = re.search(r"(\d+)\.(\d+)", model_id)
    if m:
        version_bonus = float("{}.{:02d}".format(int(m.group(1)), int(m.group(2)))) / 1000.0
    return rank + version_bonus


def fetch_model_catalog(base_url, api_key, timeout=10):
    # type: (str, str, int) -> list
    """GET {base_url}/models → list of model ids available to this account.

    Raises ModelDiscoveryError on network/auth/HTTP failure.
    Returns [] when the endpoint answers but exposes no models.
    """
    url = base_url.rstrip("/") + "/models"
    req = urllib.request.Request(url)
    req.add_header("Authorization", "Bearer {}".format(api_key))
    req.add_header("Accept", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = getattr(resp, "status", 200) or 200
            body = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            body = e.read().decode("utf-8", errors="replace")
            try:
                j = json_mod.loads(body)
                msg = j.get("message")
                if not msg and isinstance(j.get("error"), dict):
                    msg = j["error"].get("message")
                detail = str(msg) if msg else body[:200]
            except ValueError:
                detail = body[:200]
        except Exception:
            pass
        suffix = " Provider says: {}".format(detail) if detail else             " Check base_url and api_key."
        raise ModelDiscoveryError(
            "GET {} → HTTP {} ({}).{}".format(
                url, e.code, e.reason, suffix), status=e.code)
    except urllib.error.URLError as e:
        raise ModelDiscoveryError(
            "GET {} → network error: {}".format(url, e.reason))
    except Exception as e:
        raise ModelDiscoveryError("GET {} → {}".format(url, e))

    if status >= 400:
        raise ModelDiscoveryError(
            "GET {} → HTTP {}".format(url, status), status=status)

    try:
        data = json_mod.loads(body)
    except ValueError:
        raise ModelDiscoveryError(
            "GET {} → non-JSON response".format(url))

    ids = []
    if isinstance(data, dict):
        # OpenAI format: {"data": [{"id": ...}, ...]}
        entries = data.get("data")
        if isinstance(entries, list):
            for entry in entries:
                if isinstance(entry, dict) and entry.get("id"):
                    ids.append(entry["id"])
        # Some gateways: {"models": [{"id": ...}, ...]}
        if not ids and isinstance(data.get("models"), list):
            for entry in data["models"]:
                if isinstance(entry, dict) and entry.get("id"):
                    ids.append(entry["id"])
    return ids


def pick_model(catalog, tier="balanced", fallback=None):
    # type: (list, str, str) -> str
    """Pick a chat model from the catalog for a capability tier.

    tier: "strong" | "balanced" | "cheap"
    strong   → highest-score chat model (degrade to any chat model)
    balanced → mid-score chat model (degrade to any chat model)
    cheap    → lowest-score chat model (prefer cheap, degrade to any)
    Returns fallback when catalog has no usable chat model.
    """
    chat_models = [m for m in catalog if _is_chat_model(m)]
    if not chat_models:
        return fallback
    scored = [(m, _score_model(m)) for m in chat_models]
    if tier == "strong":
        best = sorted(scored, key=lambda x: -x[1])
        return best[0][0]
    if tier == "cheap":
        cheapest = sorted(scored, key=lambda x: x[1])
        return cheapest[0][0]
    # balanced: prefer mid-tier (rank 2); degrade gracefully
    mid = [s for s in scored if 1.5 <= s[1] < 3.0]
    if mid:
        return sorted(mid, key=lambda x: -x[1])[0][0]
    return sorted(scored, key=lambda x: -x[1])[0][0]


def check_registry(catalog, registry_names):
    # type: (list, list) -> dict
    """Check which registry model names are still alive in the live catalog.

    Returns {"missing": [...], "suggestions": {name: closest_live_model}}
    """
    missing = [n for n in registry_names if n not in catalog]
    suggestions = {}
    for name in missing:
        family = re.split(r"[\d.\-]", name)[0].lower()  # "qwen-turbo" → "qwen"
        if not family:
            continue
        candidates = [
            m for m in catalog
            if family in m.lower() and _is_chat_model(m)
        ]
        if candidates:
            suggestions[name] = sorted(
                candidates, key=lambda m: -_score_model(m))[0]
    return {"missing": missing, "suggestions": suggestions}
