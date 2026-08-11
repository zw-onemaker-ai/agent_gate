"""Orchestrator Agent — Design Brain v1.0 (Phase 3).

Takes a natural language project description and generates a complete
pipeline config.json — agents, topology, context routes, design_notes.

Two-brain architecture:
  Design Brain (LLM) → config.json → Execution Brain (AgentGate engine)

The orchestrator decomposes requirements into:
  1. Agent decomposition: which roles are needed, what each does
  2. Topology design: serial/parallel stages, loopback targets
  3. Context routing: which upstream files each agent needs
  4. Risk assessment: gaps and failure modes identified upfront
"""

import json as json_mod
from .prompt_gen import generate_agent_prompt


# ── Orchestrator system prompt ──

ORCHESTRATOR_SYSTEM = """You are an AI Pipeline Architect — the "Design Brain" of AgentGate.

Your job: given a project description in natural language, design a complete
agent pipeline configuration in JSON format.

## Role Library (standard agent roles)
| Role | Name        | Typical Responsibility             | Model Tier |
|------|------------|-----------------------------------|------------|
| R1   | 需求分析    | Requirements analysis, user stories| cheap      |
| R2   | 产品设计    | Product spec, MVP scope           | cheap      |
| R3   | 技术架构    | System architecture, data modeling | cheap      |
| R4   | 后端开发    | Backend implementation (API, DB)   | powerful   |
| R5   | 前端开发    | Frontend implementation (UI)       | powerful   |
| R6   | 安全审计    | Security audit, vulnerability scan | balanced   |
| R7   | 测试验证    | Integration tests, E2E testing     | balanced   |
| R8   | DevOps      | Deployment, CI/CD                  | balanced   |
| R9   | 文档撰写    | Documentation, README              | cheap      |
| R10  | 代码审查    | Code review, quality analysis      | cheap      |

## Multi-Model Strategy (cost-aware)
When a provider offers multiple models (e.g., 百炼: qwen-turbo/plus/max/coder-plus):
- R1,R2,R3,R9,R10 → cheapest model (requirements/design/docs are light tasks)
- R4,R5 → most powerful code model (code generation needs quality)
- R6,R7,R8 → balanced model (security/testing/devops need reliability)
Add "model" field to each agent entry specifying which model to use.

## Scenario Types
- "code_gen": write code files
- "content_writing": write documents/specs
- "data_analysis": analyze data, produce reports
- "devops": infrastructure, deployment

## Output Format
You MUST output ONLY valid JSON — no markdown fences, no explanations.

{
  "meta": {"project": "...", "version": "1.0", "description": "..."},
  "providers": {},
  "models": {"default": "...", "registry": {}},
  "agents": [
    {
      "role": "R1",
      "name": "...",
      "model": "qwen-turbo",
      "role_goal": "1-2 sentences describing what this agent does",
      ...
    }
  ],
  "topology": {...},
  "context": {"routes": [...]},
  "design_notes": {...}
}

## Design Rules
1. ALWAYS start with R1 (requirements) unless project explicitly has requirements
2. Each agent MUST have a unique role_goal — no duplicates
3. Topology stages: serial for dependent agents, parallel for independent ones
4. on_fail: specify which agent to loopback to if this stage fails
5. Context routes: for each agent R[N], list which upstream files it needs
6. MAX 5 agents unless the project genuinely needs more
7. Every agent's output_file must be unique
8. Acceptance criteria must be verifiable (file exists, contains X, etc.)
9. When provider has multiple models, assign cheap models to R1-R3,R9-R10 and powerful models to R4-R5
"""

ORCHESTRATOR_USER = """Design a pipeline config for the following project:

{description}

Generate ONLY the JSON config — no explanation."""


# ── Template-based fallback ──

FALLBACK_TEMPLATES = {
    "web_api": {
        "agents": [
            {"role": "R1", "name": "需求分析", "role_goal": "分析需求产出用户故事和验收标准",
             "output_file": "requirements.md",
             "acceptance_criteria": ["含3+用户故事", "每个故事有验收标准"],
             "scenario_type": "code_gen", "declared_tools": ["@write_file"]},
            {"role": "R2", "name": "产品设计", "role_goal": "产出产品规格和MVP范围",
             "output_file": "product_spec.md",
             "acceptance_criteria": ["含功能清单和优先级", "MVP范围明确"],
             "scenario_type": "code_gen", "declared_tools": ["@write_file", "@read_file"]},
            {"role": "R4", "name": "后端开发", "role_goal": "实现API后端CRUD和健康检查",
             "output_file": "backend/main.py",
             "acceptance_criteria": ["API端点可访问", "健康检查返回200"],
             "scenario_type": "code_gen", "declared_tools": ["@write_file", "@run_bash"]},
        ],
        "stages": [
            {"stage": 1, "agents": ["R1"], "mode": "serial", "max_retries": 3},
            {"stage": 2, "agents": ["R2"], "mode": "serial", "max_retries": 3, "on_fail": "R1"},
            {"stage": 3, "agents": ["R4"], "mode": "serial", "max_retries": 3, "on_fail": "R2"},
        ],
        "routes": [
            {"from": "R1", "to": "R2", "files": ["requirements.md"]},
            {"from": "R2", "to": "R4", "files": ["product_spec.md", "requirements.md"]},
        ],
        "design_notes": {
            "why_these_agents": "标准3-agent串行管线：需求→设计→开发",
            "gaps_found": ["缺少前端(R5)和测试(R7)"],
            "risks": ["后端可能缺数据库配置"],
        },
    },
    "cli_tool": {
        "agents": [
            {"role": "R1", "name": "需求分析", "role_goal": "分析CLI工具需求定义命令和参数",
             "output_file": "requirements.md",
             "acceptance_criteria": ["含CLI命令列表", "每个命令含参数说明"],
             "scenario_type": "code_gen", "declared_tools": ["@write_file"]},
            {"role": "R4", "name": "CLI开发", "role_goal": "实现CLI工具核心逻辑",
             "output_file": "cli/main.py",
             "acceptance_criteria": ["--help可执行", "核心命令可用"],
             "scenario_type": "code_gen", "declared_tools": ["@write_file", "@run_bash"]},
            {"role": "R9", "name": "文档撰写", "role_goal": "撰写README和用户文档",
             "output_file": "README.md",
             "acceptance_criteria": ["含安装说明", "含使用示例"],
             "scenario_type": "content_writing", "declared_tools": ["@write_file", "@read_file"]},
        ],
        "stages": [
            {"stage": 1, "agents": ["R1"], "mode": "serial", "max_retries": 3},
            {"stage": 2, "agents": ["R4", "R9"], "mode": "parallel", "max_retries": 3, "on_fail": "R1"},
        ],
        "routes": [
            {"from": "R1", "to": "R4", "files": ["requirements.md"]},
            {"from": "R1", "to": "R9", "files": ["requirements.md"]},
        ],
        "design_notes": {
            "why_these_agents": "R4+R9并行加速：开发写代码的同时文档撰写README",
            "gaps_found": ["缺少测试(R7)"],
            "risks": ["并行阶段R4和R9可能对需求理解不一致"],
        },
    },
    "data_pipeline": {
        "agents": [
            {"role": "R1", "name": "需求分析", "role_goal": "定义数据处理需求和输出格式",
             "output_file": "requirements.md",
             "acceptance_criteria": ["含数据Schema", "含输出格式说明"],
             "scenario_type": "code_gen", "declared_tools": ["@write_file"]},
            {"role": "R3", "name": "数据架构", "role_goal": "设计数据处理管线和Schema",
             "output_file": "data_schema.md",
             "acceptance_criteria": ["Schema定义完整", "含数据流图"],
             "scenario_type": "code_gen", "declared_tools": ["@write_file", "@read_file"]},
            {"role": "R4", "name": "数据处理", "role_goal": "实现ETL/数据处理脚本",
             "output_file": "pipeline/transform.py",
             "acceptance_criteria": ["脚本可运行", "输出符合Schema"],
             "scenario_type": "code_gen", "declared_tools": ["@write_file", "@run_bash"]},
        ],
        "stages": [
            {"stage": 1, "agents": ["R1"], "mode": "serial", "max_retries": 3},
            {"stage": 2, "agents": ["R3"], "mode": "serial", "max_retries": 3, "on_fail": "R1"},
            {"stage": 3, "agents": ["R4"], "mode": "serial", "max_retries": 3, "on_fail": "R3"},
        ],
        "routes": [
            {"from": "R1", "to": "R3", "files": ["requirements.md"]},
            {"from": "R3", "to": "R4", "files": ["data_schema.md", "requirements.md"]},
        ],
        "design_notes": {
            "why_these_agents": "数据处理三件套：需求→Schema→ETL实现",
            "gaps_found": ["缺少数据质量验证"],
            "risks": ["Schema变更导致R4输出不兼容"],
        },
    },
}

# ── Keyword → template matching ──

_TEMPLATE_KEYWORDS = [
    (r"\b(api|rest|crud|endpoint|backend|fastapi|flask|express|server)\b", "web_api"),
    (r"\b(cli|command.line|terminal|bash.script|argparse|click)\b", "cli_tool"),
    (r"\b(data|etl|pipeline|analytics|transform|csv|json.*schema)\b", "data_pipeline"),
]


def _match_template(description):
    # type: (str) -> str
    """Match a project description to the best fallback template."""
    import re
    desc_lower = description.lower()
    scores = {}
    for pattern, tpl_name in _TEMPLATE_KEYWORDS:
        matches = len(re.findall(pattern, desc_lower))
        if matches > 0:
            scores[tpl_name] = scores.get(tpl_name, 0) + matches
    if scores:
        return max(scores, key=scores.get)
    return "web_api"  # default


def _build_config_from_template(template_name, description):
    # type: (str, str) -> dict
    """Build a complete config dict from a named template."""
    import re
    tpl = FALLBACK_TEMPLATES[template_name]

    # Derive project name from description (first 4 words, sanitized)
    words = re.findall(r'[a-zA-Z0-9_一-鿿]+', description)
    proj_name = "_".join(words[:4]) if words else "agent_pipeline"

    return {
        "meta": {
            "project": proj_name,
            "version": "1.0",
            "description": description[:200],
        },
        "models": {
            "default": "qwen2.5:7b",
            "registry": {
                "qwen2.5:7b": {"provider": "ollama", "model": "qwen2.5:7b"},
            },
        },
        "agents": tpl["agents"],
        "topology": {"stages": tpl["stages"]},
        "context": {"routes": tpl["routes"]},
        "design_notes": tpl["design_notes"],
    }


# ── Main API ──

def generate_pipeline_config(description, call_llm_fn=None, use_llm=True):
    # type: (str, callable, bool) -> dict
    """Design Brain: generate a complete pipeline config from a project description.

    Args:
        description: Natural language project description (Chinese or English).
        call_llm_fn: Function(system_prompt, user_prompt) -> str. If None, uses template.
        use_llm: If False, always use template fallback even if call_llm_fn is available.

    Returns:
        Valid config dict ready for config_loader.build_pipeline().
    """
    if call_llm_fn and use_llm:
        try:
            raw = call_llm_fn(
                ORCHESTRATOR_SYSTEM,
                ORCHESTRATOR_USER.format(description=description),
            )
            config = _parse_llm_output(raw)
            if config and _validate_orchestrator_output(config):
                return config
        except Exception:
            pass  # Fall through to template

    # Template fallback
    tpl_name = _match_template(description)
    return _build_config_from_template(tpl_name, description)


def expand_prompts(config, call_llm_fn):
    # type: (dict, callable) -> dict
    """Expand all agent role_goals into full prompts using prompt_gen.

    Modifies config in-place: adds prompt_template to each agent entry.

    Args:
        config: Pipeline config dict with agents containing role_goal.
        call_llm_fn: Function(system_prompt, user_prompt) -> str.

    Returns:
        The same config dict (mutated) with prompt_template filled.
    """
    for agent in config.get("agents", []):
        if agent.get("prompt_template"):
            continue  # Already has a prompt

        agent["prompt_template"] = generate_agent_prompt(
            role_name=agent.get("name", agent["role"]),
            role_goal=agent.get("role_goal", ""),
            scenario_type=agent.get("scenario_type", "general"),
            output_file=agent.get("output_file", "output.md"),
            criteria=agent.get("acceptance_criteria", []),
            call_llm_fn=call_llm_fn,
        )
    return config


# ── Internal ──

def _parse_llm_output(raw):
    # type: (str) -> dict
    """Extract JSON from LLM output, handling markdown fences."""
    raw = raw.strip()
    # Strip markdown code fences if present
    if raw.startswith("```"):
        lines = raw.split("\n")
        # Remove opening fence
        if lines[0].startswith("```"):
            lines = lines[1:]
        # Remove closing fence
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        raw = "\n".join(lines)

    # Find first { and last }
    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end == -1:
        return None
    try:
        return json_mod.loads(raw[start:end + 1])
    except (json_mod.JSONDecodeError, ValueError):
        return None


def _validate_orchestrator_output(config):
    # type: (dict) -> bool
    """Minimal validation: does the LLM output have required fields?"""
    if not isinstance(config, dict):
        return False
    if "agents" not in config or not config["agents"]:
        return False
    for agent in config["agents"]:
        if "role" not in agent or "role_goal" not in agent:
            return False
    return True


def build_pipeline_from_description(description, call_llm_fn=None, use_llm=True,
                                     provider="ollama", model="qwen2.5:7b",
                                     output_dir=None):
    # type: (str, callable, bool, str, str, str) -> dict
    """One-shot: description → config → AgentGate pipeline.

    The complete Design Brain → Execution Brain flow.

    Returns:
        {"gate": AgentGate, "stages": list, "config": dict}
    """
    from .config_loader import build_pipeline

    config = generate_pipeline_config(description, call_llm_fn, use_llm)

    if call_llm_fn:
        expand_prompts(config, call_llm_fn)

    result = build_pipeline(config, provider=provider, model=model, output_dir=output_dir)
    result["config"] = config
    return result
