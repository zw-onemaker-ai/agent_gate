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
    parser.add_argument("--list", action="store_true",
                        help="List available configs")
    parser.add_argument("--output", "-o", default="",
                        help="Output directory")

    args = parser.parse_args()

    if args.list:
        list_configs()
        return

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
