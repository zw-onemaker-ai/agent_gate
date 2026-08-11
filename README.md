<div align="center">
  <h1>AgentGate</h1>
  <p><strong>AI 管线质量框架 — 让 Agent 不再胡说八道</strong></p>
</div>

<div align="center">
  <a href="https://github.com/zw-onemaker-ai/agent_gate/blob/master/LICENSE"><img src="https://img.shields.io/badge/license-MIT-green" alt="License"></a>
  <a href="#"><img src="https://img.shields.io/badge/tests-76%20passed-brightgreen" alt="Tests"></a>
  <a href="#"><img src="https://img.shields.io/badge/version-1.2.0-blue" alt="Version"></a>
  <a href="#"><img src="https://img.shields.io/badge/python-3.8%2B-blue" alt="Python"></a>
</div>

<br>

## 这是什么

AgentGate 解决一个具体问题：**当你让多个 AI Agent 协作完成一个项目时，怎么保证它们不互相传递垃圾？**

市面上大多数 Agent 框架只关心"怎么串起来"。AgentGate 关心的是"串起来之后怎么保证质量"——每个 Agent 的输出必须通过质量门禁才能传给下一个，失败了定向回环修复，整个管线有医生诊断。它出身于[一人公司·产品工厂](https://github.com/zw-onemaker-ai)的实战场景：一个人管理多 AI Agent 协作，没有人工审核的奢侈，必须靠系统自身保证输出质量。

### 跟其他框架的区别

| | LangChain | CrewAI | AgentGate |
|------|-----------|--------|-----------|
| 定位 | LLM应用开发 | 多Agent协作 | **Agent管线质量保证** |
| 反幻觉 | 无内置 | 无内置 | EXIT_CODE指纹 + 契约交叉验证 |
| 质量门禁 | 无 | 无 | 每个Agent输出自动验证，失败定向回环 |
| 管线诊断 | 无 | 无 | Pipeline Doctor 6项检查 |
| 模型选择 | 手动 | 手动 | Platform Advisor 自动选最优平台+模型 |
| 适用场景 | 通用LLM应用 | 多Agent任务 | **生产级Agent管线** |

---

## 核心设计：双脑架构

```
你说 "做一个电商后台，FastAPI+Vue，支持OAuth登录"

  ┌────────────────────────────────────────────┐
  │  🧠 设计脑 (Design Brain)                     │
  │                                              │
  │  分析项目 → 拆解为 Agent 团队                   │
  │  R1(需求) R2(设计) R4(后端) R5(前端) R6(安全)    │
  │                                              │
  │  Platform Advisor 决定模型分配:                 │
  │  R1,R2 → 本地 Ollama (免费, 轻量任务)           │
  │  R4,R5 → 百炼 qwen-coder-plus (代码质量)       │
  │  R6    → 本地 Ollama (敏感代码不出本机)          │
  │                                              │
  │  输出: config.json                            │
  └──────────────────┬───────────────────────────┘
                     ↓
  ┌──────────────────────────────────────────────┐
  │  ⚙️ 执行脑 (Execution Brain)                    │
  │                                                │
  │  R1 ──→ [Quality Gate] ──→ R2 ──→ [Gate] ──→ R4 │
  │   ↑         ↓FAIL              ↑    ↓FAIL       │
  │   └── 定向回环 ────────────────┘    └── 回环 ────│
  │                                                │
  │  每步验证:                                       │
  │  - EXIT_CODE 指纹 (防伪造)                       │
  │  - 契约交叉验证 (声称产出 vs 实际文件)              │
  │  - 提示词CPOO评分 (≥80分才放行)                   │
  │  - 工具白名单风控                                 │
  └──────────────────────────────────────────────┘
```

设计脑用 LLM 做规划（需要创造力），执行脑用纯规则引擎做验证（需要可靠性）。这是刻意的不对称设计——规划可以模糊，执行必须精确。

---

## 快速开始

### 安装

```bash
git clone https://github.com/zw-onemaker-ai/agent_gate.git
cd agent_gate

# 可选: 安装 LLM 依赖
pip install litellm  # 使用云端模型
# 或者什么都不装 → 自动用本地 Ollama
```

### 零配置试用（纯本地，不花一分钱）

```bash
# 1. 确保 Ollama 在运行
ollama pull qwen2.5:7b

# 2. 分析一个项目
python3 -c "
from src.platform_advisor import analyze_project
plan = analyze_project('做一个Markdown转PDF的CLI工具，支持中文')
print(plan.summary)
"

# 输出:
# 平台分配:
#   本地 Ollama | qwen2.5:7b → R1, R9 (轻量任务)
#   本地 Ollama | qwen2.5-coder:7b → R4 (代码生成)
# 需要API Key: 无
# 预估月成本: <$0.01/月
```

### 接入百炼（便宜且中文好）

```bash
export DASHSCOPE_API_KEY="sk-你的key"

# 初始化设计脑
python3 -c "from src.workspace import init_workspace; init_workspace()"

# 设计脑会自动: R1→qwen-turbo(便宜) R4→qwen-coder-plus(代码强)
```

### 支持的所有平台

| 平台 | 适合场景 | 月成本参考 |
|------|---------|-----------|
| 本地 Ollama | 轻量任务、敏感数据、零成本 | 免费 |
| 阿里百炼 | 中文项目、性价比高 | ~$0.5 |
| DeepSeek | 代码生成、性价比极高 | ~$0.3 |
| OpenAI | 最复杂推理、英文项目 | ~$5 |
| Groq | 极速推理 | 有免费额度 |
| Anthropic | 长文本分析 | ~$3 |

---

## 整个管线怎么工作

### 1. 一句话建项目

```python
from src.platform_advisor import analyze_project, resolve_config
from src.config_loader import build_pipeline, load_config
import json

# 分析 → 规划 → 生成配置
plan = analyze_project("做一个会议室预定系统，FastAPI后端+React前端，支持Google日历同步")
config = resolve_config(plan)

# 保存配置
with open("meeting_room.json", "w") as f:
    json.dump(config, f, indent=2, ensure_ascii=False)
```

生成的 `meeting_room.json` 已经包含了完整的 Agent 团队、每个 Agent 用的模型、验收标准、拓扑结构。

### 2. 跑管线

```python
from src.config_loader import build_pipeline

result = build_pipeline(config, provider="litellm", model="qwen-turbo")
gate = result["gate"]

# 执行管线
gate.run_pipeline(initial_context="按照 meeting_room.json 中的规格开发")

# 诊断（如果出错）
diagnosis = gate.diagnose()
print(diagnosis.summary)

# 保存状态
session_id = gate.save()
```

### 3. 怎么保证质量

AgentGate 做了五层验证，每一步都不能跳过：

```
Agent 产出
  ↓
① 文件存在性检查 (Part A 文件是否真实存在)
  ↓
② EXIT_CODE 指纹验证 (Bash 命令必须输出 EXIT:0, 防伪造)
  ↓
③ 契约交叉验证 (Agent 声称产出了 main.py → 检查文件确实存在且非空)
  ↓
④ CPOO 提示词评分 (提示词质量≥80分, <60分自动重写)
  ↓
⑤ 工具白名单风控 (Agent 只能用声明的工具, 高风险工具强制门禁)
  ↓
通过 → 传给下一个Agent
失败 → 定向回环 (不是盲目重启, 而是回退到出问题的那个Agent)
```

---

## 项目结构

```
agent_gate/
├── src/
│   ├── engine.py              ← 执行引擎核心
│   ├── orchestrator_agent.py  ← 设计脑 (需求→配置)
│   ├── platform_advisor.py    ← 平台顾问 (自动选模型)
│   ├── validators.py          ← 质量验证器 (EXIT指纹/契约验证)
│   ├── human_gate.py          ← 人工干预 (6类失败场景)
│   ├── cpoo_scorer.py         ← 提示词评分
│   ├── tool_registry.py       ← 工具白名单
│   ├── memory_manager.py      ← 状态持久化
│   ├── pipeline_doctor.py     ← 管线诊断
│   ├── context_assembler.py   ← 3级上下文预算
│   ├── llm_client.py          ← 多模型统一调用
│   ├── config_loader.py       ← 配置验证 (向后兼容)
│   └── workspace.py           ← 工作区配置
├── configs/                   ← 配置模板
├── tests/test_core.py         ← 76个测试
└── memory/                    ← 项目记忆
```

---

## 设计哲学

### 一人公司驱动

这个项目来自真实需求：一个人管多个 AI Agent，没有团队帮你审核输出。所以 AgentGate 把"不信任任何 Agent 的输出"作为默认姿态——每个 Agent 的输出都必须通过 Bash 验证，每次 Bash 验证都必须带 EXIT_CODE 指纹。

### 反幻觉不是口号

很多框架说"我们解决幻觉"，但只是让 LLM 自己检查自己——这跟让学生自己批改自己的考卷有什么区别？AgentGate 的做法是：

- **EXIT_CODE 指纹**: Agent 说的"我验证过了"不算数，Bash 命令的退出码才算
- **契约交叉验证**: Agent 声称产出了 `main.py` → 系统实际去磁盘上检查文件存不存在、是不是空的
- **CPOO 评分**: 提示词本身经过 5 模块评分，低于 80 分自动优化

### 设计脑 vs 执行脑

这是从编译器设计借鉴的思路——前端负责理解和规划（可以模糊），后端负责生成和验证（必须精确）。设计脑用 LLM 做需求拆解，执行脑用纯 Python 做规则执行。LLM 可能会胡说，但 `subprocess.run()` 不会。

---

## 贡献

这是一个一人公司项目，但欢迎任何形式的贡献：

- **Bug 报告**: [Issues](https://github.com/zw-onemaker-ai/agent_gate/issues)
- **功能建议**: [Discussions](https://github.com/zw-onemaker-ai/agent_gate/discussions)
- **PR**: 确保 `python3 tests/test_core.py` 全部通过

## 许可

MIT License © 2026 一人公司 · 产品工厂
