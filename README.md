# AgentGate — AI Agent Quality Framework

> **作者**: 一人公司 · 产品工厂  
> **版本**: v1.2  
> **测试**: 76 passed, 0 failed  
> **许可**: MIT License

一句话需求 → 自动拆解Agent团队 → 智能分配多平台模型 → 质量门禁 → 定向回环修复 → 全链路诊断

---

## 架构

```
你说 "做个电商后台"
        ↓
  ┌─────────────────────────────┐
  │  设计脑 (Orchestrator)       │
  │  自动拆解: R1需求→R2设计→R4开发 │
  │  Platform Advisor: 百炼写代码, Ollama做轻活 │
  └─────────────┬───────────────┘
                ↓ config.json
  ┌─────────────────────────────┐
  │  执行脑 (AgentGate Engine)   │
  │  Agent → Quality Gate → Contract → 下一Agent │
  │  Fail → Loopback → 自动修复   │
  │  Pipeline Doctor → 全链路诊断  │
  └─────────────────────────────┘
```

## Quick Start

```bash
# 1. 设置你的API Key
export DASHSCOPE_API_KEY="sk-xxx"   # 百炼
# 或: export OPENAI_API_KEY="sk-xxx"
# 或: 什么都不设 → 自动用本地Ollama (免费)

# 2. 初始化设计脑
python3 -c "from src.workspace import init_workspace; init_workspace()"

# 3. 一句话建项目
python3 -c "
from src.platform_advisor import analyze_project
plan = analyze_project('做一个FastAPI Todo后端，支持CRUD和用户认证')
print(plan.summary)
"

# 4. 自动生成配置 + 跑管线
python3 -c "
from src.platform_advisor import analyze_project, resolve_config
from src.config_loader import build_pipeline
import json
plan = analyze_project('做一个FastAPI Todo后端')
config = resolve_config(plan)
with open('config.json', 'w') as f:
    json.dump(config, f, indent=2, ensure_ascii=False)
gate = build_pipeline(config, provider='litellm', model='qwen-turbo')['gate']
gate.run_pipeline(initial_context='按需求开发')
"

# 5. 跑测试
python3 tests/test_core.py
```

## 核心模块

| 模块 | 功能 |
|------|------|
| `platform_advisor.py` | 6平台知识库, 自动推荐最优模型组合, 预估成本 |
| `orchestrator_agent.py` | 设计脑: 自然语言→管线配置 |
| `engine.py` | 执行引擎: 多Agent串行/并行, 质量门禁, 定向回环 |
| `cpoo_scorer.py` | 提示词质量评分 (≥80合格) |
| `pipeline_doctor.py` | 6项全链路诊断, 自动修复 |
| `tool_registry.py` | 10工具白名单, 风险分级 |
| `memory_manager.py` | 管线状态持久化, 4KB索引防膨胀 |
| `workspace.py` | 设计脑统一配置入口 |

## 支持的AI平台

百炼(DashScope) · OpenAI · DeepSeek · Groq · Ollama(本地) · Anthropic

自动选择: 轻量任务→便宜模型, 代码生成→最强模型, 安全审计→本地模型

## License

MIT License — Copyright (c) 2026 一人公司 · 产品工厂

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND.
