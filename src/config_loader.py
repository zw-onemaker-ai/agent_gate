"""Config loader — JSON config → AgentGate pipeline.

Supports:
  - Sequential agents: run one after another
  - Parallel groups: agents in the same group run simultaneously
  - Q2 Phase 0 fields: topology.max_retries/on_fail, design_notes,
    declared_tools, context.routes, models.registry, meta.version

Config format (Q2 target):
{
  "meta": {"project": "...", "version": "1.0"},
  "models": {"default": "qwen2.5:7b", "registry": {...}},
  "agents": [
    {"role": "R1", "name": "...", "role_goal": "...", "output_file": "...",
     "acceptance_criteria": [...], "declared_tools": ["@web_fetch"], ...}
  ],
  "topology": {
    "stages": [
      {"stage": 1, "agents": ["R1"], "mode": "serial", "max_retries": 3}
    ]
  },
  "design_notes": {"why_these_agents": "...", "gaps_found": [], "risks": []},
  "context": {"routes": [...]}
}
"""

import json
from pathlib import Path
from .engine import AgentGate


# ── Schema validation ──

REQUIRED_TOP_KEYS = ["agents"]
Q2_OPTIONAL_KEYS = ["meta", "models", "topology", "design_notes", "context", "tools"]

REQUIRED_AGENT_KEYS = ["role", "name"]
OPTIONAL_AGENT_KEYS = [
    "role_goal", "prompt_template", "verify_cmd", "output_file",
    "acceptance_criteria", "scenario_type", "model",          # v0 fields
    "declared_tools", "prompt_file",                           # Q2 fields
]

VALID_MODES = ("serial", "parallel")


def load_config(config_path):
    # type: (str) -> dict
    """Load and validate a pipeline config file.

    Validates required fields. Q2 optional fields (topology, design_notes, etc.)
    are checked for format but not required — old configs remain compatible.
    """
    with open(config_path) as f:
        config = json.load(f)

    errors = []

    # ── Top-level required ──
    for key in REQUIRED_TOP_KEYS:
        if key not in config:
            errors.append("Config missing required key: '{}'".format(key))

    if "agents" in config:
        if len(config["agents"]) == 0:
            errors.append("Config 'agents' list is empty")
        else:
            for i, agent in enumerate(config["agents"]):
                errors.extend(_validate_agent(agent, i))

    # ── Q2: meta ──
    meta = config.get("meta", {})
    if meta and "version" not in meta:
        meta["version"] = "1.0"  # default

    # ── Q2: models ──
    models = config.get("models", {})
    if models:
        if "default" not in models:
            errors.append("Q2 'models' section missing 'default' key")
        registry = models.get("registry", {})
        for alias, entry in registry.items():
            if "provider" not in entry or "model" not in entry:
                errors.append(
                    "Q2 models.registry.{} missing 'provider' or 'model'".format(alias))

    # ── Q2: topology ──
    topology = config.get("topology", {})
    stages = topology.get("stages", [])
    if stages:
        for s in stages:
            if "stage" not in s:
                errors.append("Q2 topology.stages entry missing 'stage' number")
            if "agents" not in s or not s["agents"]:
                errors.append("Q2 topology.stages[{}] missing 'agents'".format(
                    s.get("stage", "?")))
            mode = s.get("mode", "serial")
            if mode not in VALID_MODES:
                errors.append(
                    "Q2 topology.stages[{}].mode '{}' invalid (must be {})".format(
                        s.get("stage", "?"), mode, "/".join(VALID_MODES)))
            # Defaults for Q2 fields
            if "max_retries" not in s:
                s["max_retries"] = 3
            if "on_fail" not in s:
                s["on_fail"] = None

    # ── Q2: design_notes ──
    design = config.get("design_notes", {})
    if design:
        valid_fields = {"why_these_agents", "gaps_found", "risks"}
        extra = set(design.keys()) - valid_fields
        if extra:
            errors.append(
                "Q2 design_notes has unknown fields: {}".format(list(extra)))

    # ── Q2: context.routes ──
    ctx = config.get("context", {})
    routes = ctx.get("routes", [])
    if routes:
        for r in routes:
            if "from" not in r or "to" not in r:
                errors.append("Q2 context.routes entry missing 'from' or 'to'")

    # ── Q2: tools ──
    tools = config.get("tools", {})
    if tools:
        whitelist = tools.get("global_whitelist", [])
        for t in whitelist:
            if "name" not in t or "risk" not in t:
                errors.append("Q2 tools.global_whitelist entry missing 'name' or 'risk'")

    if errors:
        raise ValueError(
            "Config validation failed ({} errors):\n  - {}".format(
                len(errors), "\n  - ".join(errors)))

    return config


def _validate_agent(agent, idx):
    # type: (dict, int) -> list
    """Validate a single agent entry. Returns list of error strings."""
    errors = []
    for key in REQUIRED_AGENT_KEYS:
        if key not in agent:
            errors.append("Agent #{} missing '{}'".format(idx + 1, key))
    if "role_goal" not in agent and "prompt_template" not in agent:
        errors.append(
            "Agent '{}' needs 'role_goal' or 'prompt_template'".format(
                agent.get("role", "agent #{}".format(idx + 1))))
    # Q2: declared_tools format check
    tools = agent.get("declared_tools", [])
    if tools:
        for t in tools:
            if not t.startswith("@"):
                errors.append(
                    "Agent '{}' declared_tool '{}' should start with @".format(
                        agent.get("role", "?"), t))
    return errors


def build_pipeline(config, provider="ollama", model="qwen2.5:7b", output_dir=None):
    # type: (dict, str, str, str) -> dict
    """Build an AgentGate pipeline from config dict.

    Returns:
        {"gate": AgentGate, "stages": list} — gate instance + pipeline stages.
        stages is None if using default sequential execution.

    Q2 Phase 0: Reads topology from config.topology.stages (new format)
    or config.pipeline.stages (legacy format).
    v1.1: Reads providers section for base_url + api_key per provider.
    """
    # ── Project name ──
    meta = config.get("meta", {})
    project = config.get("project", {})
    name = (
        meta.get("project") or
        project.get("name") or
        Path(config.get("_path", "pipeline")).stem
    )
    if output_dir is None:
        output_dir = "./output/{}".format(name)

    # ── Model selection (Q2: config.models overrides CLI args) ──
    models_cfg = config.get("models", {})
    default_model = models_cfg.get("default", model)
    registry = models_cfg.get("registry", {})
    if default_model in registry:
        entry = registry[default_model]
        provider = entry.get("provider", provider)
        model = entry.get("model", model)

    # ── v1.1: Provider config (base_url, api_key) ──
    providers_cfg = config.get("providers", {})
    provider_cfg = providers_cfg.get(provider, {})
    gate_base_url = provider_cfg.get("base_url") or config.get("api_base")
    gate_api_key = provider_cfg.get("api_key") or config.get("api_key")

    # Resolve env vars in api_key (e.g., "${DASHSCOPE_API_KEY}")
    import os as _os
    if gate_api_key and gate_api_key.startswith("${") and gate_api_key.endswith("}"):
        gate_api_key = _os.environ.get(gate_api_key[2:-1], gate_api_key)

    gate = AgentGate(
        project_name=name,
        output_dir=output_dir,
        max_iterations=config.get("max_iterations", 5),
        model_provider=provider,
        model_name=model,
    )
    # Inject provider config into gate's LLM client
    if gate_base_url:
        gate.llm.base_url = gate_base_url
    if gate_api_key:
        gate.llm.api_key = gate_api_key

    for agent_cfg in config["agents"]:
        # Agent-level model override (Q2)
        agent_model_alias = agent_cfg.get("model", default_model)
        if agent_model_alias in registry:
            entry = registry[agent_model_alias]
            agent_provider = entry.get("provider", provider)
            agent_model_name = entry.get("model", model)
        else:
            agent_provider = provider
            agent_model_name = model

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

    # ── Stages: Q2 topology.stages first, fallback to legacy pipeline.stages ──
    stages = None
    topology = config.get("topology", {})
    if topology and "stages" in topology:
        # Q2 format: topology.stages → convert to internal format
        stages = []
        for s in topology["stages"]:
            stages.append({
                "roles": s["agents"],
                "parallel": s.get("mode", "serial") == "parallel",
                "max_retries": s.get("max_retries", 3),
                "on_fail": s.get("on_fail"),
            })
    else:
        # Legacy format
        pipeline_cfg = config.get("pipeline", {})
        if pipeline_cfg and "stages" in pipeline_cfg:
            stages = pipeline_cfg["stages"]

    return {"gate": gate, "stages": stages}
