"""Workspace Config — 设计脑的统一配置入口 (v1.2).

设计脑需要知道三件事才能工作:
  1. 自己用什么LLM来思考 (design_brain_llm)
  2. 手头有哪些平台的API Key (available_platforms)
  3. 偏好: 省钱优先还是质量优先 (preference)

配置方式: 环境变量 + workspace.json (可选覆盖)
"""

import json as json_mod
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class WorkspaceConfig:
    """设计脑工作区配置"""
    # 设计脑自己用的LLM
    brain_provider: str = "ollama"        # ollama | litellm | openai
    brain_model: str = "qwen2.5:7b"       # 模型名
    brain_base_url: str = ""              # API地址 (百炼等)
    brain_api_key: str = ""               # API Key

    # 执行Agent可用的平台
    available_platforms: List[str] = field(default_factory=list)  # ["bailian","ollama","deepseek"]

    # 运行时探测到的模型清单 (L0 discovery, v1.3)
    available_models: List[str] = field(default_factory=list)

    # 偏好
    preference: str = "balanced"          # "cheap" | "balanced" | "quality"
    language: str = "zh"                  # "zh" | "en" — 影响模型选择

    # 项目根目录
    output_dir: str = "./output"


def detect_workspace():
    # type: () -> WorkspaceConfig
    """自动检测当前环境，生成工作区配置。

    检测顺序: 环境变量 → workspace.json → 默认值
    """
    config = WorkspaceConfig()

    # ── 检测设计脑LLM ──
    # 1. 百炼
    if os.environ.get("DASHSCOPE_API_KEY"):
        config.brain_provider = "litellm"
        config.brain_model = "qwen3.7-plus"
        config.brain_base_url = os.environ.get(
            "DASHSCOPE_BASE_URL",
            "https://dashscope.aliyuncs.com/compatible-mode/v1")
        config.brain_api_key = os.environ["DASHSCOPE_API_KEY"]
    # 2. OpenAI
    elif os.environ.get("OPENAI_API_KEY"):
        config.brain_provider = "litellm"
        config.brain_model = "gpt-4o-mini"
        config.brain_api_key = os.environ["OPENAI_API_KEY"]
    # 3. DeepSeek
    elif os.environ.get("DEEPSEEK_API_KEY"):
        config.brain_provider = "litellm"
        config.brain_model = "deepseek-chat"
        config.brain_base_url = "https://api.deepseek.com/v1"
        config.brain_api_key = os.environ["DEEPSEEK_API_KEY"]
    # 4. 默认: 本地Ollama
    else:
        config.brain_provider = "ollama"
        config.brain_model = "qwen2.5:7b"

    # ── 检测可用平台 ──
    platforms = []
    if os.environ.get("DASHSCOPE_API_KEY"):
        platforms.append("bailian")
    if os.environ.get("OPENAI_API_KEY"):
        platforms.append("openai")
    if os.environ.get("DEEPSEEK_API_KEY"):
        platforms.append("deepseek")
    if os.environ.get("GROQ_API_KEY"):
        platforms.append("groq")
    if os.environ.get("ANTHROPIC_API_KEY"):
        platforms.append("anthropic")
    # Ollama 始终可用 (本地)
    platforms.append("ollama")
    config.available_platforms = platforms

    # ── 尝试加载 workspace.json (覆盖) ──
    ws_file = os.environ.get("AGENTGATE_WORKSPACE", "workspace.json")
    model_overridden = False
    if os.path.exists(ws_file):
        try:
            with open(ws_file) as f:
                ws_data = json_mod.load(f)
            brain = ws_data.get("design_brain", {})
            if brain.get("provider"):
                config.brain_provider = brain["provider"]
            if brain.get("model"):
                config.brain_model = brain["model"]
                model_overridden = True
            if brain.get("base_url"):
                config.brain_base_url = brain["base_url"]
            if brain.get("api_key"):
                config.brain_api_key = brain["api_key"]
            if ws_data.get("preference"):
                config.preference = ws_data["preference"]
            if ws_data.get("language"):
                config.language = ws_data["language"]
            if ws_data.get("output_dir"):
                config.output_dir = ws_data["output_dir"]
            # 手动指定的平台覆盖自动检测
            if ws_data.get("platforms"):
                config.available_platforms = ws_data["platforms"]
        except Exception:
            pass

    # ── L0 运行时探测 (v1.3) ──
    # 显式指定模型时跳过；无 key / 无云端地址时跳过（本地 Ollama 无需探测）
    if not model_overridden and config.brain_api_key and config.brain_base_url:
        _discover_brain_model(config)

    return config


def _discover_brain_model(config):
    # type: (WorkspaceConfig) -> None
    """用运行时探测到的真实模型清单挑选设计脑模型。

    失败时静默降级到离线默认值——探测是增强，不是阻塞。
    """
    from .model_discovery import (
        ModelDiscoveryError, fetch_model_catalog, pick_model)

    tier = {
        "quality": "strong",
        "cheap": "cheap",
        "balanced": "balanced",
    }.get(config.preference, "balanced")

    try:
        catalog = fetch_model_catalog(
            config.brain_base_url, config.brain_api_key)
    except ModelDiscoveryError:
        return

    if not catalog:
        return
    config.available_models = catalog
    picked = pick_model(catalog, tier)
    if picked:
        config.brain_model = picked


def show_workspace(config=None):
    # type: (WorkspaceConfig) -> str
    """展示当前工作区配置 (人类可读)"""
    if config is None:
        config = detect_workspace()

    platform_names = {
        "bailian": "阿里百炼", "openai": "OpenAI", "deepseek": "DeepSeek",
        "groq": "Groq", "anthropic": "Anthropic", "ollama": "本地Ollama",
    }

    lines = [
        "",
        "=" * 55,
        "  设计脑工作区配置",
        "=" * 55,
        "",
        "🧠 设计脑LLM:",
        "   {} → {} {}".format(
            config.brain_provider, config.brain_model,
            "(云端)" if config.brain_api_key else "(本地)"),
    ]
    if config.available_models:
        lines.append("   在线模型清单: {} 个 (运行时探测)".format(
            len(config.available_models)))
    lines.extend([
        "",
        "📡 可用平台 ({}个):".format(len(config.available_platforms)),
    ])
    for p in config.available_platforms:
        name = platform_names.get(p, p)
        has_key = bool(os.environ.get(
            {"bailian":"DASHSCOPE_API_KEY","openai":"OPENAI_API_KEY",
             "deepseek":"DEEPSEEK_API_KEY","groq":"GROQ_API_KEY",
             "anthropic":"ANTHROPIC_API_KEY","ollama":""}.get(p, "")
        )) if p != "ollama" else True
        icon = "🔑" if has_key else "⚪"
        lines.append("   {} {} ({})".format(icon, name, p))

    lines.extend([
        "",
        "🎯 偏好: {}".format({"cheap":"省钱优先","balanced":"平衡","quality":"质量优先"}.get(config.preference, config.preference)),
        "🌐 语言: {}".format({"zh":"中文","en":"English"}.get(config.language, config.language)),
        "",
        "💡 提示:",
        "   设置API Key: export DASHSCOPE_API_KEY=\"sk-xxx\"",
        "   切换偏好: 创建 workspace.json → {\"preference\": \"quality\"}",
        "   查看完整配置: python3 -c \"from src.workspace import detect_workspace; print(detect_workspace())\"",
        "",
    ])
    return "\n".join(lines)


def init_workspace(provider=None, model=None, preference=None, output_dir=None):
    # type: (str, str, str, str) -> WorkspaceConfig
    """初始化工作区 — 创建 workspace.json + 打印配置"""
    config = detect_workspace()

    if provider:
        config.brain_provider = provider
    if model:
        config.brain_model = model
    if preference:
        config.preference = preference
    if output_dir:
        config.output_dir = output_dir

    # 写入 workspace.json
    ws_data = {
        "design_brain": {
            "provider": config.brain_provider,
            "model": config.brain_model,
            "base_url": config.brain_base_url,
            "api_key": "${}".format(
                {"litellm": "DASHSCOPE_API_KEY", "openai": "OPENAI_API_KEY",
                 "ollama": ""}.get(config.brain_provider, "")
            ) if config.brain_provider != "ollama" else "",
        },
        "platforms": config.available_platforms,
        "preference": config.preference,
        "language": config.language,
        "output_dir": config.output_dir,
    }
    with open("workspace.json", "w") as f:
        json_mod.dump(ws_data, f, indent=2, ensure_ascii=False)

    print(show_workspace(config))
    return config
