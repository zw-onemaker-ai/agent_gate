"""Config loader — JSON config → AgentGate pipeline.

Supports:
  - Sequential agents: run one after another
  - Parallel groups: agents in the same group run simultaneously

Config format:
{
  "project": {"name": "...", "description": "..."},
  "agents": [
    {"role": "R1", "name": "...", "role_goal": "...", "output_file": "...",
     "acceptance_criteria": [...], "scenario_type": "..."},
    {"role": "R4", "name": "...", ...},
    {"role": "R5", "name": "...", "group": "backend+frontend", ...},
    ...
  ],
  "parallel_groups": [
    {"group": "backend+frontend", "roles": ["R4", "R5"]}
  ]
}
"""

import json
from pathlib import Path
from .engine import AgentGate


def load_config(config_path):
    # type: (str) -> dict
    """Load and validate a pipeline config file."""
    with open(config_path) as f:
        config = json.load(f)

    # Validate required fields
    if "agents" not in config:
        raise ValueError("Config must have 'agents' list")
    if len(config["agents"]) == 0:
        raise ValueError("Config must have at least one agent")

    for i, agent in enumerate(config["agents"]):
        if "role" not in agent:
            raise ValueError("Agent {} missing 'role'".format(i))
        if "name" not in agent:
            raise ValueError("Agent {} missing 'name'".format(i))
        if "role_goal" not in agent and "prompt_template" not in agent:
            raise ValueError("Agent '{}' needs 'role_goal' or 'prompt_template'".format(
                agent.get("role", "unknown")))

    return config


def build_pipeline(config, provider="ollama", model="qwen2.5:7b", output_dir=None):
    # type: (dict, str, str, str) -> dict
    """Build an AgentGate pipeline from config dict.

    Returns:
        {"gate": AgentGate, "stages": list} — gate instance + pipeline stages.
        stages is None if using default sequential execution.
    """
    project = config.get("project", {})
    name = project.get("name", Path(config.get("_path", "pipeline")).stem)
    if output_dir is None:
        output_dir = "./output/{}".format(name)

    gate = AgentGate(
        project_name=name,
        output_dir=output_dir,
        max_iterations=config.get("max_iterations", 5),
        model_provider=provider,
        model_name=model,
    )

    for agent_cfg in config["agents"]:
        gate.register_agent(
            role=agent_cfg["role"],
            name=agent_cfg["name"],
            prompt_template=agent_cfg.get("prompt_template", ""),
            verify_cmd=agent_cfg.get("verify_cmd", ""),
            output_file=agent_cfg.get("output_file", ""),
            acceptance_criteria=agent_cfg.get("acceptance_criteria"),
            role_goal=agent_cfg.get("role_goal", ""),
            scenario_type=agent_cfg.get("scenario_type", "general"),
        )

    stages = None
    pipeline_cfg = config.get("pipeline", {})
    if pipeline_cfg and "stages" in pipeline_cfg:
        stages = pipeline_cfg["stages"]

    return {"gate": gate, "stages": stages}
