#!/usr/bin/env python3
"""AgentGate — Single entry point.

Usage:
  # From config file (recommended):
  python3 run.py --config configs/code_gen.json "Build a Todo API"
  python3 run.py --config configs/content_writing.json "Topic: AI and DevOps"

  # With different model:
  python3 run.py --config configs/code_gen.json --provider openai --model gpt-4o-mini "..."

  # Dry-run (mock, no LLM):
  python3 run.py --config configs/code_gen.json --mock "..."

  # List available configs:
  python3 run.py --list
"""

import sys
import os
import argparse
from pathlib import Path

# Ensure src is importable
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.config_loader import load_config, build_pipeline


def list_configs():
    """List all available pipeline configs."""
    configs_dir = Path(__file__).parent / "configs"
    if not configs_dir.exists():
        print("No configs directory found.")
        return
    for f in sorted(configs_dir.glob("*.json")):
        try:
            cfg = load_config(str(f))
            proj = cfg.get("project", {})
            agents = len(cfg.get("agents", []))
            print("  {} — {} ({} agents)".format(
                f.stem, proj.get("description", "No description"), agents))
        except Exception as e:
            print("  {} — [ERROR: {}]".format(f.stem, e))



def _resolve_env_value(value):
    # type: (str) -> str
    """Resolve ${ENV_VAR} placeholders from the environment."""
    if value and value.startswith("${") and value.endswith("}"):
        return os.environ.get(value[2:-1], "")
    return value


def run_check_models(config_path=None):
    # type: (str) -> int
    """Probe a provider's live model catalog and check the config registry.

    Standalone (no --config): probes Bailian with DASHSCOPE_API_KEY if set.
    With --config: probes the first provider entry whose key resolves.
    Exit codes: 0 = probe succeeded, 1 = no key / probe failed.
    """
    from src.model_discovery import (
        ModelDiscoveryError, check_registry, fetch_model_catalog, pick_model)

    base_url = ""
    api_key = ""
    registry_names = []
    provider_name = ""

    if config_path:
        cfg = load_config(config_path)
        providers_cfg = cfg.get("providers", {})
        registry = (cfg.get("models") or {}).get("registry", {})
        registry_names = list(registry.keys())
        for pname, pcfg in providers_cfg.items():
            key = _resolve_env_value(pcfg.get("api_key", ""))
            if key:
                provider_name = pname
                base_url = pcfg.get("base_url", "")
                api_key = key
                break
        if not base_url:
            expected = [pcfg.get("api_key", "") for pcfg in providers_cfg.values()]
            print("No provider key found in environment.")
            print("Expected env vars: {}".format(", ".join(expected)))
            return 1
    else:
        key = os.environ.get("DASHSCOPE_API_KEY", "")
        if not key:
            print("No API key found. Set one first, e.g.:")
            print("  export DASHSCOPE_API_KEY=\"sk-...\"   # Bailian")
            print("  export ARK_API_KEY=\"...\"            # Volcano")
            return 1
        provider_name = "bailian"
        base_url = os.environ.get(
            "DASHSCOPE_BASE_URL",
            "https://dashscope.aliyuncs.com/compatible-mode/v1")
        api_key = key

    print("Model Catalog Check")
    print("  Provider : {}".format(provider_name))
    print("  Base URL : {}".format(base_url))
    try:
        catalog = fetch_model_catalog(base_url, api_key)
    except ModelDiscoveryError as e:
        print("  ❌ {}".format(e.message))
        if e.status == 401:
            print("  💡 Key 被端点拒绝。若你的 key 来自百炼控制台的「按量付费计划」，")
            print("     该 key 只认计划专属域名（token-plan.cn-beijing.maas.aliyuncs.com）。")
            print("     解决: export DASHSCOPE_BASE_URL=\"https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1\"")
            print("     或用 --config 指向 providers.base_url 为该域名的配置。")
        return 1

    chat_catalog = [m for m in catalog]
    print("  Live models: {}".format(len(chat_catalog)))
    for m in sorted(chat_catalog):
        print("    - {}".format(m))

    if registry_names:
        # 比对「解析后的模型名」(registry alias → model 字段)，
        # 本地别名只用于展示——API 实际发送的是 model 值。
        resolved_names = []
        name_by_resolved = {}
        for alias in registry_names:
            entry = registry[alias]
            real = entry.get("model", alias) if isinstance(entry, dict) else alias
            resolved_names.append(real)
            name_by_resolved[real] = alias
        result = check_registry(catalog, resolved_names)
        if result["missing"]:
            print("  Registry check:")
            for name in result["missing"]:
                alias = name_by_resolved.get(name, name)
                hint = result["suggestions"].get(name)
                suffix = " (suggest: {})".format(hint) if hint else ""
                label = alias if alias != name else name
                print("    - {} → ❌ NOT in live catalog{}".format(label, suffix))
        else:
            print("  Registry check: all {} names alive ✓".format(len(registry_names)))

    print("  Tier picks:")
    for tier in ("strong", "balanced", "cheap"):
        picked = pick_model(catalog, tier)
        print("    {:8} → {}".format(tier, picked or "(no chat model)"))
    return 0


def main():
    parser = argparse.ArgumentParser(
        description="AgentGate — AI Agent Quality Framework")
    parser.add_argument("idea", nargs="?", default="",
                        help="Initial context/idea for the pipeline")
    parser.add_argument("--config", "-c", default="",
                        help="Pipeline config file (JSON)")
    parser.add_argument("--provider", default="ollama",
                        help="LLM provider (ollama, openai, litellm)")
    parser.add_argument("--model", default="qwen2.5:7b",
                        help="Model name")
    parser.add_argument("--mock", action="store_true",
                        help="Dry-run without LLM")
    parser.add_argument("--check-models", action="store_true",
                        help="Probe provider /models endpoint and check registry names")
    parser.add_argument("--list", action="store_true",
                        help="List available configs")
    parser.add_argument("--output", "-o", default="",
                        help="Output directory")

    args = parser.parse_args()

    if args.list:
        list_configs()
        return

    if args.check_models:
        sys.exit(run_check_models(config_path=args.config or None))

    if not args.config:
        print("Usage: python run.py --config <config.json> [idea]")
        print("       python run.py --list")
        print("\nExamples:")
        print("  python run.py --config configs/code_gen.json 'Build a Todo API'")
        print("  python run.py --config configs/content_writing.json --mock 'AI in DevOps'")
        sys.exit(1)

    # Load config
    config_path = Path(args.config)
    if not config_path.exists():
        print("Config not found: {}".format(args.config))
        sys.exit(1)

    config = load_config(str(config_path))
    config["_path"] = str(config_path)

    proj = config.get("project", {})
    print("Pipeline: {}".format(proj.get("name", config_path.stem)))
    print("Description: {}".format(proj.get("description", "N/A")))
    print("Agents: {}".format(len(config.get("agents", []))))
    print("Model: {} / {}".format(args.provider, args.model))
    print()

    # Build pipeline
    output_dir = args.output or None
    result = build_pipeline(config, provider=args.provider, model=args.model,
                            output_dir=output_dir)
    gate = result["gate"]
    stages = result["stages"]

    if args.mock:
        print("[MOCK MODE] Pipeline config loaded. Agents registered:")
        for role, agent in gate._agents.items():
            criteria_count = len(agent.get("acceptance_criteria", []))
            has_prompt = bool(agent.get("prompt_template"))
            has_role_goal = bool(agent.get("role_goal"))
            generator = "prompt_template" if has_prompt else "role_goal (auto-gen)" if has_role_goal else "MISSING"
            print("  {}: {} → {}  [prompt: {}] [criteria: {}]".format(
                role, agent["name"], agent["output_file"], generator, criteria_count))
        if stages:
            print("\n  Pipeline plan:")
            for s in stages:
                marker = "⇉" if s.get("parallel") else "→"
                print("    {} {}".format(marker, " + ".join(s["roles"])))
        print()
        print("To run with LLM, remove --mock flag.")
        return

    # Run pipeline
    idea = args.idea or proj.get("description", "")
    state = gate.run_pipeline(initial_context=idea, stages=stages)
    print("\n" + gate.summary())


if __name__ == "__main__":
    main()
