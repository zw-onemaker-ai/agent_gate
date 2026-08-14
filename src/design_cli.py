"""Design Brain CLI glue (v1.3.3).

Wires the design brain to the command line:

  python3 run.py --design "Build a Todo API" [--yes] [--no-expand]

Flow (boot sequence steps ③-⑥):
  ③ Design brain first call  → detect_workspace + LLMClient
  ④ Generate config          → generate_pipeline_config (+ prompt expansion)
  ⑤ HumanGate                → print plan, confirm y/N (--yes skips)
  ⑥ Execution brain          → build_pipeline + run_pipeline
"""

import json as json_mod
import os
import sys

from .config_loader import build_pipeline, load_config
from .llm_client import LLMClient
from .model_discovery import check_registry, fetch_model_catalog, pick_model
from .orchestrator_agent import expand_prompts, generate_pipeline_config
from .workspace import detect_workspace


def _make_brain_caller(client):
    # type: (object) -> callable
    """Adapt LLMClient.call (returns LLMResult) → call_llm_fn (returns str)."""

    def call_llm(system_prompt, user_prompt):
        resp = client.call(system_prompt=system_prompt, user_prompt=user_prompt)
        if getattr(resp, "error", None):
            raise RuntimeError(resp.error)
        content = getattr(resp, "content", "")
        if not content:
            raise RuntimeError("empty response from model")
        return content

    return call_llm


def _align_config_with_live_catalog(config, ws):
    # type: (dict, object) -> dict
    """校正设计脑的离线知识 — 运行时清单是权威。

    1. providers.base_url 以工作区为准 (计划专属域名等)
    2. providers 缺 api_key → 注入环境变量占位符 (不落盘 raw key)
    3. models.registry 用实时清单重建 (strong/balanced/cheap 档位实选)
    4. agent.model 若不在实时清单 → 移除 (回落到 models.default)
    """
    if not ws.brain_api_key or not ws.brain_base_url:
        return config

    try:
        catalog = fetch_model_catalog(ws.brain_base_url, ws.brain_api_key)
    except Exception:
        return config
    if not catalog:
        return config

    # 1+2: providers 对齐
    providers = config.get("providers") or {}
    if providers:
        for pname, pcfg in providers.items():
            pcfg["base_url"] = ws.brain_base_url
            if not pcfg.get("api_key"):
                pcfg["api_key"] = "${DASHSCOPE_API_KEY}"
        config["providers"] = providers
    else:
        config["providers"] = {
            ws.brain_provider: {
                "base_url": ws.brain_base_url,
                "api_key": "${DASHSCOPE_API_KEY}",
            }
        }

    # 3: registry 用实时清单档位重建
    tiers = {}
    for tier in ("strong", "balanced", "cheap"):
        picked = pick_model(catalog, tier)
        if picked:
            tiers[tier] = picked
    if tiers:
        models = config.get("models") or {}
        models["registry"] = {
            m: {"provider": ws.brain_provider, "model": m}
            for m in sorted(set(tiers.values()))
        }
        if models.get("default") not in catalog:
            models["default"] = tiers.get("balanced") or ws.brain_model
        config["models"] = models

    # 4: agent.model 过期字段清理
    for agent in config.get("agents", []):
        if agent.get("model") and agent["model"] not in catalog:
            agent.pop("model", None)
    return config


def run_design(description, auto_confirm=False, expand=True):
    # type: (str, bool, bool) -> int
    """Design brain → HumanGate → execution brain. Exit codes: 0 ok, 1 abort."""
    ws = detect_workspace()

    # ── ③ Design brain first call setup ──
    client = None
    if ws.brain_api_key and ws.brain_base_url:
        client = LLMClient(
            provider=ws.brain_provider,
            model=ws.brain_model,
            base_url=ws.brain_base_url,
            api_key=ws.brain_api_key,
        )
    elif ws.brain_provider == "ollama":
        client = LLMClient(provider="ollama", model=ws.brain_model)

    if client is None:
        print("No LLM available for the design brain.")
        print("Set an API key first, e.g.: export DASHSCOPE_API_KEY=\"sk-...\"")
        print("(Without a key, use --config with a hand-written pipeline config.)")
        return 1

    brain_model = "{}/{}".format(ws.brain_provider, ws.brain_model)
    print("Design Brain : {}".format(brain_model))
    print("Designing pipeline for: {}".format(description[:80]))
    print()

    call_llm = _make_brain_caller(client)

    # ── ④ Generate config ──
    config = generate_pipeline_config(description, call_llm_fn=call_llm, use_llm=True)
    if expand:
        try:
            expand_prompts(config, call_llm)
        except Exception as e:
            print("  (prompt expansion skipped: {})".format(e))

    config = _align_config_with_live_catalog(config, ws)

    # Validate + persist
    meta = config.get("meta", {})
    project = config.get("project", {})
    name = (
        meta.get("project") or
        project.get("name") or
        "designed_pipeline"
    )
    output_dir = "./output/{}".format(name)
    if not os.path.isdir(output_dir):
        os.makedirs(output_dir)
    cfg_path = os.path.join(output_dir, "design_config.json")
    with open(cfg_path, "w") as f:
        json_mod.dump(config, f, indent=2, ensure_ascii=False)

    # ── ⑤ HumanGate ──
    print("── Pipeline plan ──")
    print("  Project : {}".format(name))
    print("  Agents  : {}".format(len(config.get("agents", []))))
    for a in config.get("agents", []):
        print("    {}  {} → {}".format(
            a.get("role", "?"), a.get("name", ""), a.get("output_file", "")))
    topology = config.get("topology", {}).get("stages")
    if topology:
        plan = []
        for s in topology:
            marker = "⇉" if s.get("mode") == "parallel" else "→"
            plan.append(marker + " " + "+".join(s.get("agents", [])))
        print("  Topology: " + " ".join(plan))
    print("  Config saved: {}".format(cfg_path))
    print()

    if not auto_confirm:
        try:
            answer = input("Confirm and run the pipeline? [y/N]: ").strip().lower()
        except EOFError:
            answer = "n"
        if answer not in ("y", "yes"):
            print("Aborted. Config saved; run later with:")
            print("  python3 run.py --config {}".format(cfg_path))
            return 1

    # ── ⑥ Execution brain ──
    print("Running pipeline...")
    result = build_pipeline(
        config,
        provider=ws.brain_provider,
        model=ws.brain_model,
        output_dir=output_dir,
    )
    gate = result["gate"]
    stages = result.get("stages")
    state = gate.run_pipeline(initial_context=description, stages=stages)
    print()
    print(gate.summary())
    return 0
