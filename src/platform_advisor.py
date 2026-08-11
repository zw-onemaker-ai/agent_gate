"""Platform Advisor v1.2 — AI平台智能规划 & API依赖分析

设计脑主动分析项目需求 → 推荐最优平台组合 → 告诉用户需要哪些API Key
→ 用户提供Key后 → 自动写回config.json

支持的平台:
  百炼(DashScope)  — 便宜、中文好，qwen系列
  OpenAI           — 最强代码生成，贵
  DeepSeek         — 性价比高，代码能力强
  Groq             — 推理速度极快
  Ollama(本地)     — 免费、隐私、数据不出本机
  Anthropic        — 长文本分析最强
  Google Gemini    — 多模态、免费额度大
"""

import json as json_mod
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional


# ── Platform Knowledge Base ──

@dataclass
class PlatformModel:
    name: str               # e.g. "qwen-coder-plus"
    strengths: List[str]    # e.g. ["code_generation", "chinese"]
    cost_per_1k_tokens: float  # USD
    context_window: int     # tokens
    speed: str              # "fast" | "medium" | "slow"
    requires_key: bool = True

@dataclass
class PlatformProfile:
    id: str                 # e.g. "bailian"
    name: str               # e.g. "阿里百炼"
    base_url: str           # API endpoint
    provider: str           # "litellm" | "ollama"
    models: List[PlatformModel]
    env_var: str            # e.g. "DASHSCOPE_API_KEY"
    website: str            # where to get the key
    free_tier: str = ""     # e.g. "100万tokens/月免费"


PLATFORMS = {
    "bailian": PlatformProfile(
        id="bailian",
        name="阿里百炼 (DashScope)",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        provider="litellm",
        env_var="DASHSCOPE_API_KEY",
        website="https://bailian.console.aliyun.com",
        free_tier="100万tokens/月 (qwen-turbo)",
        models=[
            PlatformModel("qwen-turbo", ["chinese", "fast", "cheap"], 0.0004, 131072, "fast"),
            PlatformModel("qwen-plus", ["chinese", "balanced", "coding"], 0.002, 131072, "medium"),
            PlatformModel("qwen-max", ["chinese", "complex_reasoning"], 0.02, 32768, "slow"),
            PlatformModel("qwen-coder-plus", ["code_generation", "chinese", "debugging"], 0.0035, 131072, "medium"),
        ],
    ),
    "openai": PlatformProfile(
        id="openai",
        name="OpenAI",
        base_url="https://api.openai.com/v1",
        provider="litellm",
        env_var="OPENAI_API_KEY",
        website="https://platform.openai.com",
        free_tier="无 (需预充值)",
        models=[
            PlatformModel("gpt-4o-mini", ["fast", "cheap", "general"], 0.00015, 128000, "fast"),
            PlatformModel("gpt-4o", ["code_generation", "complex_reasoning", "safety"], 0.005, 128000, "medium"),
            PlatformModel("o3-mini", ["code_generation", "math", "reasoning"], 0.0011, 200000, "fast"),
        ],
    ),
    "deepseek": PlatformProfile(
        id="deepseek",
        name="DeepSeek",
        base_url="https://api.deepseek.com/v1",
        provider="litellm",
        env_var="DEEPSEEK_API_KEY",
        website="https://platform.deepseek.com",
        free_tier="500万tokens (新用户)",
        models=[
            PlatformModel("deepseek-chat", ["general", "chinese", "cheap"], 0.00014, 65536, "fast"),
            PlatformModel("deepseek-coder", ["code_generation", "debugging"], 0.00014, 65536, "fast"),
            PlatformModel("deepseek-reasoner", ["complex_reasoning", "math"], 0.00055, 65536, "slow"),
        ],
    ),
    "groq": PlatformProfile(
        id="groq",
        name="Groq",
        base_url="https://api.groq.com/openai/v1",
        provider="litellm",
        env_var="GROQ_API_KEY",
        website="https://console.groq.com",
        free_tier="免费额度 (速率限制)",
        models=[
            PlatformModel("llama-3.1-8b-instant", ["fast", "cheap", "general"], 0.00005, 131072, "fast", requires_key=False),
            PlatformModel("llama-3.3-70b", ["complex_reasoning", "coding"], 0.00059, 131072, "medium"),
        ],
    ),
    "ollama": PlatformProfile(
        id="ollama",
        name="本地 Ollama",
        base_url="http://localhost:11434",
        provider="ollama",
        env_var="",
        website="https://ollama.com",
        free_tier="完全免费 (需要GPU)",
        models=[
            PlatformModel("qwen2.5:7b", ["general", "chinese", "cheap"], 0, 32768, "medium", requires_key=False),
            PlatformModel("qwen2.5-coder:7b", ["code_generation", "chinese"], 0, 32768, "medium", requires_key=False),
            PlatformModel("codellama:13b", ["code_generation", "english"], 0, 16384, "slow", requires_key=False),
            PlatformModel("deepseek-r1:8b", ["complex_reasoning", "chinese"], 0, 32768, "slow", requires_key=False),
        ],
    ),
    "anthropic": PlatformProfile(
        id="anthropic",
        name="Anthropic Claude",
        base_url="https://api.anthropic.com/v1",
        provider="litellm",
        env_var="ANTHROPIC_API_KEY",
        website="https://console.anthropic.com",
        free_tier="无",
        models=[
            PlatformModel("claude-3.5-sonnet", ["code_generation", "long_context", "safety"], 0.003, 200000, "medium"),
            PlatformModel("claude-3.5-haiku", ["fast", "cheap", "general"], 0.0008, 200000, "fast"),
        ],
    ),
}


# ── Capability → Role mapping ──

# What each role needs from a model
ROLE_CAPABILITIES = {
    "R1": {"needs": ["general", "chinese"], "priority": "cheap", "reason": "需求分析是轻量文字任务"},
    "R2": {"needs": ["general", "chinese"], "priority": "cheap", "reason": "产品设计是轻量文字任务"},
    "R3": {"needs": ["general", "chinese"], "priority": "cheap", "reason": "架构设计文字为主"},
    "R4": {"needs": ["code_generation", "debugging"], "priority": "quality", "reason": "后端代码质量决定产品质量"},
    "R5": {"needs": ["code_generation"], "priority": "quality", "reason": "前端代码直接影响用户体验"},
    "R6": {"needs": ["safety", "general"], "priority": "privacy", "reason": "安全审计涉及敏感代码，建议本地运行"},
    "R7": {"needs": ["code_generation", "general"], "priority": "balanced", "reason": "测试代码不需要最强模型"},
    "R8": {"needs": ["general", "fast"], "priority": "balanced", "reason": "DevOps脚本，平衡即可"},
    "R9": {"needs": ["general", "chinese"], "priority": "cheap", "reason": "文档撰写轻量任务"},
    "R10":{"needs": ["code_generation", "general"], "priority": "cheap", "reason": "代码审查用便宜模型即可"},
}


@dataclass
class PlatformRecommendation:
    platform_id: str
    platform_name: str
    model: str
    assigned_roles: List[str]
    reason: str
    cost_estimate: str  # e.g. "~$0.001/次调用"
    api_key_needed: str  # e.g. "DASHSCOPE_API_KEY" or ""

@dataclass
class DependencyPlan:
    project_name: str
    recommendations: List[PlatformRecommendation]
    total_platforms: int
    total_api_keys_needed: List[str]
    estimated_monthly_cost: str
    summary: str = ""


# ── Main API ──

def analyze_project(description, available_keys=None):
    # type: (str, list) -> DependencyPlan
    """Analyze a project and recommend the optimal platform/model combination.

    Args:
        description: Natural language project description.
        available_keys: List of env var names the user already has
                       (e.g., ["DASHSCOPE_API_KEY", "OPENAI_API_KEY"]).

    Returns:
        DependencyPlan with specific recommendations per role.
    """
    available_keys = available_keys or _detect_available_keys()

    # Extract project features from description
    features = _extract_features(description)

    # Determine needed roles
    needed_roles = _determine_needed_roles(description, features)

    # Match each role to optimal platform+model
    recommendations = []
    for role in needed_roles:
        rec = _match_role_to_platform(role, available_keys, features)
        recommendations.append(rec)

    # Deduplicate and merge
    recommendations = _deduplicate_recommendations(recommendations)

    # Calculate API keys needed
    keys_needed = sorted(set(
        r.api_key_needed for r in recommendations if r.api_key_needed
    ))

    # Estimate cost
    monthly_cost = _estimate_cost(recommendations, needed_roles)

    return DependencyPlan(
        project_name=_derive_project_name(description),
        recommendations=recommendations,
        total_platforms=len(set(r.platform_id for r in recommendations)),
        total_api_keys_needed=keys_needed,
        estimated_monthly_cost=monthly_cost,
        summary=_build_plan_summary(recommendations, keys_needed, monthly_cost),
    )


def resolve_config(dependency_plan, user_keys=None):
    # type: (DependencyPlan, dict) -> dict
    """Generate a complete config.json from a dependency plan + user's API keys.

    Args:
        dependency_plan: Output from analyze_project().
        user_keys: Dict of env_var_name → api_key_value
                  (e.g., {"DASHSCOPE_API_KEY": "sk-xxx"}).

    Returns:
        Complete config.json dict ready for build_pipeline().
    """
    user_keys = user_keys or {}

    # Build providers section
    providers = {}
    models_registry = {}
    platform_ids = set(r.platform_id for r in dependency_plan.recommendations)
    for pid in platform_ids:
        if pid not in PLATFORMS:
            continue
        p = PLATFORMS[pid]
        if p.provider != "ollama":
            key = user_keys.get(p.env_var, "")
            if not key:
                key = "${}".format(p.env_var)
            providers[p.provider] = {
                "base_url": p.base_url,
                "api_key": key,
            }
        # Register models used
        for rec in dependency_plan.recommendations:
            if rec.platform_id == pid:
                models_registry[rec.model] = {
                    "provider": p.provider,
                    "model": rec.model,
                }

    # Build agents
    agents = []
    for rec in dependency_plan.recommendations:
        for role in rec.assigned_roles:
            cap = ROLE_CAPABILITIES.get(role, {})
            agents.append({
                "role": role,
                "name": _role_name(role),
                "model": rec.model,
                "role_goal": cap.get("reason", "Auto-assigned by Platform Advisor"),
                "output_file": _role_output_file(role),
                "acceptance_criteria": _default_criteria(role),
                "scenario_type": "code_gen" if role in ("R4","R5","R7","R8") else "content_writing",
                "declared_tools": ["@write_file", "@read_file"],
            })

    # Build topology (serial by default)
    stages = [{"stage": i+1, "agents": [a["role"]], "mode": "serial", "max_retries": 3}
              for i, a in enumerate(agents)]

    # Context routes
    routes = []
    for i in range(1, len(agents)):
        upstream_files = [agents[i-1]["output_file"]]
        if i >= 2:
            upstream_files.append(agents[0]["output_file"])
        routes.append({
            "from": agents[i-1]["role"],
            "to": agents[i]["role"],
            "files": upstream_files,
        })

    return {
        "meta": {
            "project": dependency_plan.project_name,
            "version": "1.2",
            "description": "Auto-generated by Platform Advisor",
        },
        "providers": providers,
        "models": {
            "default": dependency_plan.recommendations[0].model if dependency_plan.recommendations else "qwen-turbo",
            "registry": models_registry,
        },
        "agents": agents,
        "topology": {"stages": stages},
        "context": {"routes": routes},
        "design_notes": {
            "why_these_agents": "Platform Advisor: {}".format(
                ", ".join("{}→{}".format(r.platform_name, r.model)
                          for r in dependency_plan.recommendations)),
            "gaps_found": [],
            "risks": ["验证各平台API Key是否有效"],
        },
    }


# ── Internal ──

def _extract_features(description):
    # type: (str) -> set
    """Extract project features from description."""
    import re
    desc = description.lower()
    features = set()

    if re.search(r"(api|rest|crud|backend|fastapi|flask|express)", desc):
        features.add("backend_api")
    if re.search(r"(frontend|ui|react|vue|page|界面)", desc):
        features.add("frontend")
    if re.search(r"(auth|login|oauth|jwt|权限)", desc):
        features.add("authentication")
    if re.search(r"(data|etl|analytics|可视化|报表)", desc):
        features.add("data_processing")
    if re.search(r"(chat|ai|llm|智能|对话)", desc):
        features.add("ai_feature")
    if re.search(r"(mobile|app|小程序|h5)", desc):
        features.add("mobile")
    if re.search(r"(安全|security|encrypt|加密|audit)", desc):
        features.add("security")
    if re.search(r"(电商|shop|store|商城|订单)", desc):
        features.add("ecommerce")
    if re.search(r"(中文|chinese|中国)", desc):
        features.add("chinese_content")

    return features or {"general"}


def _determine_needed_roles(description, features):
    # type: (str, set) -> list
    """Determine which agent roles are needed."""
    roles = ["R1"]  # Always need requirements

    if "backend_api" in features or "ecommerce" in features:
        roles.extend(["R2", "R4"])
    elif "frontend" in features:
        roles.extend(["R2", "R5"])
    elif "data_processing" in features:
        roles.extend(["R3", "R4"])
    elif "ai_feature" in features:
        roles.extend(["R2", "R4"])
    else:
        roles.append("R2")  # At least design

    if "authentication" in features or "security" in features:
        roles.append("R6")

    if "frontend" in features and "backend_api" in features:
        if "R5" not in roles:
            roles.append("R5")

    # Documentation is always good
    if len(roles) >= 3:
        roles.append("R9")

    # Deduplicate preserving order
    seen = set()
    result = []
    for r in roles:
        if r not in seen:
            result.append(r)
            seen.add(r)
    return result


def _match_role_to_platform(role, available_keys, features):
    # type: (str, list, set) -> PlatformRecommendation
    """Find the best platform + model for a given role."""
    cap = ROLE_CAPABILITIES.get(role, {"needs": ["general"], "priority": "cheap"})
    needs = set(cap["needs"])
    priority = cap["priority"]

    best_score = -1
    best_platform = None
    best_model = None

    for pid, platform in PLATFORMS.items():
        # Check if key is available (skip if requires key but not available)
        if platform.env_var and platform.env_var not in available_keys:
            if platform.provider != "ollama":
                continue

        for model in platform.models:
            score = 0
            model_strengths = set(model.strengths)

            # Capability match
            overlap = needs & model_strengths
            score += len(overlap) * 10

            # Priority match
            if priority == "cheap" and "cheap" in model_strengths:
                score += 15
            elif priority == "quality" and "code_generation" in model_strengths and "cheap" not in model_strengths:
                score += 15
            elif priority == "balanced" and "balanced" in model_strengths:
                score += 10
            elif priority == "privacy" and pid == "ollama":
                score += 20

            # Chinese content bonus
            if "chinese_content" in features and "chinese" in model_strengths:
                score += 10

            # Speed bonus for fast tasks
            if "fast" in model_strengths and model.speed == "fast":
                score += 5

            # Free/local bonus
            if pid == "ollama" or not model.requires_key:
                score += 8

            if score > best_score:
                best_score = score
                best_platform = platform
                best_model = model

    if best_platform is None:
        # Ultimate fallback: Ollama qwen2.5:7b
        best_platform = PLATFORMS["ollama"]
        best_model = best_platform.models[0]

    return PlatformRecommendation(
        platform_id=best_platform.id,
        platform_name=best_platform.name,
        model=best_model.name,
        assigned_roles=[role],
        reason=cap.get("reason", "自动匹配"),
        cost_estimate=_format_cost(best_model.cost_per_1k_tokens),
        api_key_needed=best_platform.env_var,
    )


def _deduplicate_recommendations(recommendations):
    # type: (list) -> list
    """Merge recommendations that use the same platform+model."""
    merged = {}
    for rec in recommendations:
        key = (rec.platform_id, rec.model)
        if key in merged:
            merged[key].assigned_roles.extend(rec.assigned_roles)
        else:
            merged[key] = rec
    return list(merged.values())


def _estimate_cost(recommendations, needed_roles):
    # type: (list, list) -> str
    """Estimate monthly cost based on model pricing and expected usage."""
    total_per_call = 0
    for rec in recommendations:
        for pid, platform in PLATFORMS.items():
            if rec.platform_id == pid:
                for m in platform.models:
                    if m.name == rec.model:
                        total_per_call += m.cost_per_1k_tokens * 2  # ~2K tokens avg
                        break

    calls_per_month = len(needed_roles) * 10  # ~10 iterations per role
    monthly = total_per_call * calls_per_month
    if monthly < 0.01:
        return "<$0.01/月 (几乎免费)"
    elif monthly < 1:
        return "~${:.2f}/月".format(monthly)
    else:
        return "~${:.2f}/月".format(monthly)


def _detect_available_keys():
    # type: () -> list
    """Detect which API keys are already set in environment."""
    available = []
    for pid, platform in PLATFORMS.items():
        if platform.env_var and os.environ.get(platform.env_var):
            available.append(platform.env_var)
    if "OLLAMA_HOST" in os.environ or True:  # Ollama is always "available" (will try localhost)
        available.append("OLLAMA_HOST")
    return available


def _derive_project_name(description):
    # type: (str) -> str
    import re
    words = re.findall(r'[a-zA-Z0-9_一-鿿]+', description)
    return "_".join(words[:4]) if words else "agent_pipeline"


def _format_cost(cost_per_1k):
    # type: (float) -> str
    if cost_per_1k == 0:
        return "免费"
    return "~${:.4f}/1K tokens".format(cost_per_1k)


def _role_name(role):
    # type: (str) -> str
    return {
        "R1": "需求分析", "R2": "产品设计", "R3": "技术架构",
        "R4": "后端开发", "R5": "前端开发", "R6": "安全审计",
        "R7": "测试验证", "R8": "DevOps", "R9": "文档撰写", "R10": "代码审查",
    }.get(role, role)


def _role_output_file(role):
    # type: (str) -> str
    return {
        "R1": "requirements.md", "R2": "product_spec.md", "R3": "architecture.md",
        "R4": "backend/main.py", "R5": "frontend/app.js", "R6": "security_report.md",
        "R7": "tests/test_main.py", "R8": "deploy/docker-compose.yml",
        "R9": "README.md", "R10": "review_report.md",
    }.get(role, "output.md")


def _default_criteria(role):
    # type: (str) -> list
    defaults = {
        "R1": ["含3+用户故事", "每个故事有验收标准"],
        "R2": ["含功能清单和优先级", "MVP范围明确"],
        "R4": ["代码可运行", "API端点可访问"],
        "R5": ["组件可渲染", "UI响应式"],
        "R6": ["含漏洞清单", "每个漏洞有修复建议"],
        "R7": ["测试可运行", "覆盖率≥80%"],
        "R9": ["含安装说明", "含使用示例"],
    }
    return defaults.get(role, ["产出文件为非空"])


def _build_plan_summary(recommendations, keys_needed, monthly_cost):
    # type: (list, list, str) -> str
    lines = [
        "=" * 55,
        "  Platform Advisor — API 依赖规划",
        "=" * 55,
        "",
        "平台分配:",
    ]
    for rec in recommendations:
        roles_str = ", ".join(rec.assigned_roles)
        lines.append(
            "  {} | {} → {} ({})".format(
                rec.platform_name, rec.model, roles_str, rec.reason[:40]))

    lines.append("")
    lines.append("需要申请的 API Key:")
    if keys_needed:
        for k in keys_needed:
            # Find the platform for this key
            for pid, p in PLATFORMS.items():
                if p.env_var == k:
                    lines.append("  export {}=\"your-key\"  ← {}".format(k, p.website))
                    break
            else:
                lines.append("  export {}=\"your-key\"".format(k))
    else:
        lines.append("  (无需额外Key — 使用本地Ollama)")

    lines.append("")
    lines.append("预估月成本: {}".format(monthly_cost))
    lines.append("")
    lines.append("用户提供Key后 → advisor.resolve_config() → 自动生成config.json")
    lines.append("")
    return "\n".join(lines)
