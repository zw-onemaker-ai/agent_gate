# agent_gate · 技术架构 v1.0

> 📐 **目标架构蓝图**（非当前代码状态）。基于 Q1-Q4 全部设计决策（2026-08-07）。
> 当前代码实现了 Phase 0 前的原型（7个模块、config驱动引擎可运行），本文档描述的是 Phase 0-4 完成后的最终形态。
> 标注 🆕 的模块/类/接口 = 尚未实现，按路线图分阶段交付。

## 系统全景

```
                          ┌──────────────────┐
                          │   设计脑 (LLM)    │  Design-time
                          │ 自然语言→config   │  一次性调用
                          └────────┬─────────┘
                                   │ config.json + prompts/*.md
                                   ▼
  ┌────────────────────────────────────────────────────────────┐
  │                      引擎层 (engine.py)                     │
  │                                                            │
  │  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌───────────┐ │
  │  │ 配置加载  │  │ 拓扑调度  │  │ 闸门验证  │  │ 上下文组装 │ │
  │  │ config   │  │ topology │  │ validate │  │ context   │ │
  │  │ _loader  │  │ scheduler│  │   gate   │  │ assembler │ │
  │  └──────────┘  └──────────┘  └──────────┘  └───────────┘ │
  │        │             │              │              │       │
  │        ▼             ▼              ▼              ▼       │
  │  ┌──────────────────────────────────────────────────────┐ │
  │  │                 管线状态 (pipeline_state)             │ │
  │  │  当前stage / retry_count / GateResult[] / 审计链     │ │
  │  └──────────────────────────────────────────────────────┘ │
  └────────────────────────────────────────────────────────────┘
         │                    │                    │
         ▼                    ▼                    ▼
  ┌──────────┐        ┌──────────┐        ┌──────────┐
  │ Agent 1  │        │ Agent 2  │        │ Agent 3  │
  │ (LLM调用)│        │ (LLM调用)│        │ (LLM调用)│
  └──────────┘        └──────────┘        └──────────┘
   无状态              无状态              无状态
   调完消失            调完消失            调完消失
```

---

## 一、模块拆分

| 模块 | 文件 | 职责 | Phase |
|------|------|------|:--:|
| 数据模型 | `src/models.py` | GateStatus/ExitCodeFingerprint/AgentOutput/PipelineState | 0 |
| 配置加载 | `src/config_loader.py` | config.json 加载+Schema校验+env注入 | 0 |
| 管线引擎 | `src/engine.py` | 拓扑调度+Agent调用+闸门执行+回环+记忆写入 | 0 |
| 闸门验证 | `src/validators.py` | 三道检查+EXIT指纹+定向回环判定+LLM-as-Judge | 2 |
| 验收标准 | `src/acceptance.py` | acceptance_criteria→verify_cmd(Bash)自动生成 | 2 |
| 人工闸门 | `src/human_gate.py` | 结构化提问+3选项(人修/退设计脑/放宽标准) | 2 |
| 提示词生成 | `src/prompt_gen.py` | CPOO五模块→Agent prompt_template | 3 |
| 上下文组装 | `src/context_assembler.py` | 🆕 三级预算+contract拼装+失败上下文注入 | 1 |
| 记忆管理 | `src/memory_manager.py` | 🆕 L1/L2/L3三层记忆读写+裁剪+断点恢复 | 4 |
| LLM调用 | `src/llm_client.py` | 🆕 统一LLM调用抽象(Ollama/OpenAI/LiteLLM) | 1 |
| 设计脑 | `src/orchestrator_agent.py` | 🆕 自然语言→config.json+反问+HumanGate确认 | 3 |

---

## 二、核心数据流

### 2.1 Agent 执行流程（每个Agent调用）

```
engine.run_stage(stage)
  │
  ├─ 1. context_assembler.build(agent_id)
  │     从 L1取: 项目背景+role_goal
  │     从 L2取: 上游contract + 失败上下文(若回环)
  │     计算总大小 → CTX_NORMAL/WARNING/CRITICAL
  │     → 产出: assembled_prompt (注入给Agent的完整prompt)
  │
  ├─ 2. llm_client.call(model, assembled_prompt)
  │     → Agent产出: Part A(业务文件) + Part B(contract)
  │
  ├─ 3. validators.validate(agent_output)
  │     3.1 文件检查: output_file存在且非空
  │     3.2 EXIT指纹: verify_cmd执行 → 检查EXIT:0
  │     3.3 脱敏检查: 面向客户的产出无内部术语
  │     3.4 (可选)LLM-as-Judge: 内容质量评估
  │     → 产出: GateResult(PASS/CONDITIONAL/FAIL + 失败原因)
  │
  ├─ 4. gate裁决
  │     PASS → 5. 记忆写入 → 下一stage
  │     FAIL → 4.1 定向回环判定
  │             retry_count < max_retries → 回环到目标stage
  │             retry_count ≥ max_retries → HumanGate
  │
  └─ 5. memory_manager.save(L2: contract + GateResult + ContextPackage)
       memory_manager.update(L1: pipeline_state进度)
```

### 2.2 回环流程

```
Agent2 FAIL (retry_count=1, max_retries=3)
  │
  ├─ 判定回环目标 (定向回环逻辑)
  │   代码bug → 回当前stage
  │   架构问题 → 回设计脑
  │   数据问题 → 回数据Agent所在stage
  │   else → NONE → HumanGate
  │
  ├─ context_assembler.build_for_retry(target_agent, fail_context)
  │   注入: 上次失败的具体原因 + 错误信息 + 修复建议
  │
  └─ 重新执行 target_agent，retry_count++
```

### 2.3 设计脑流程

```
用户: "帮我做一个舆情监控"
  │
  ├─ 1. orchestrator_agent.analyze(requirement)
  │     信息缺口检测 → 分层处理
  │     可推断→标注  可默认→默认+标注  不可推断→反问
  │
  ├─ 2. 若有 pending_questions → 展示给用户 → 用户回答/选默认
  │
  ├─ 3. orchestrator_agent.generate(requirement + answers)
  │     → config.json + prompts/*.md + design_notes
  │
  ├─ 4. config_loader.validate(config)  ← 约束细则校验
  │     🔴硬约束(拓扑/验收/缺失) → FAIL退回
  │     🟡警告(粒度≥3条) → 自动退回设计脑重拆
  │
  └─ 5. HumanGate: 展示config摘要 → 用户确认 → 保存config.json
```

---

## 三、各模块接口

### 3.1 context_assembler.py（🆕 核心新增）

```python
class ContextAssembler:
    def build(self, agent_id: str) -> AssembledContext:
        """为指定Agent拼装完整prompt上下文"""
        # 1. 取L1: 项目背景 + agent role_goal
        # 2. 取L2: 上游contract(s) + 失败上下文
        # 3. 计算总大小 → 决定预算模式
        # 4. 按预算模式裁剪 → 返回 assembled prompt

    def build_for_retry(self, agent_id: str, fail_reason: GateResult) -> AssembledContext:
        """回环场景：注入失败上下文"""

class AssembledContext:
    prompt: str            # 注入给Agent的完整prompt
    budget_mode: str       # NORMAL | WARNING | CRITICAL
    files_available: list  # Agent可读取的文件路径
    hints: list[str]       # 引擎给Agent的提示
```

### 3.2 memory_manager.py（🆕）

```python
class MemoryManager:
    def load_state(self) -> PipelineState:
        """加载L1全局状态"""
    def save_state(self, state: PipelineState):
        """保存L1"""
    def save_session(self, agent_id: str, output: AgentOutput, gate: GateResult):
        """写入L2会话记忆(contract+GateResult+ContextPackage)"""
    def archive_session(self):
        """管线结束→L2归档到sessions/"""
    def trim_if_needed(self):
        """防膨胀裁剪(写入4问+字段预算)"""
```

### 3.3 llm_client.py（🆕）

```python
class LLMClient:
    def call(self, provider: str, model: str, prompt: str) -> LLMResponse:
        """统一调用: ollama→requests.post / openai→openai.Chat / litellm→litellm.completion"""
    def validate_env(self, registry: dict) -> list[str]:
        """校验env完整性: ollama需要OLLAMA_HOST? openai需要OPENAI_API_KEY?"""
    def judge(self, criteria: list[str], output: str) -> JudgeResult:
        """LLM-as-Judge: 评估内容质量 → PASS/FAIL+理由"""
```

### 3.4 validators.py（需扩展）

```python
class Validator:
    # 现有:
    def check_files(self, files: list[str]) -> GateResult: ...
    def check_exit_fingerprint(self, output: str) -> GateResult: ...
    def check_desensitization(self, content: str) -> GateResult: ...

    # 新增:
    def classify_failure(self, gate_result: GateResult) -> LoopbackTarget:
        """失败分类→回环目标: BUG→当前stage / ARCH→设计脑 / DATA→数据Agent / else→NONE"""
    def judge_content(self, criteria: list[str], output: str, llm: LLMClient) -> JudgeResult:
        """🆕 LLM-as-Judge"""
```

### 3.5 engine.py（需重构）

```python
class PipelineEngine:
    def __init__(self, config_path: str):
        self.config = ConfigLoader(config_path).load()
        self.state = MemoryManager().load_state()
        self.context = ContextAssembler(self.config, self.state)
        self.validator = Validator()
        self.llm = LLMClient()

    def run(self) -> PipelineResult:
        for stage in self.config.topology.stages:
            result = self.run_stage(stage)
            if result.failed and not self.handle_loopback(stage, result):
                return PipelineResult(failed=True, state=self.state)

    def run_stage(self, stage: Stage) -> StageResult:
        # 并行组: ThreadPoolExecutor.map(run_agent, stage.agents)
        # 串行: for agent in stage.agents: run_agent(agent)

    def run_agent(self, agent: Agent) -> AgentResult:
        ctx = self.context.build(agent.id)         # ① 组装上下文
        output = self.llm.call(agent.model, ctx.prompt)  # ② 调LLM
        gate = self.validator.validate(output)     # ③ 闸门验证
        self.memory.save_session(agent.id, output, gate)  # ④ 记忆写入
        return AgentResult(output, gate)

    def handle_loopback(self, stage, result) -> bool:
        if result.retry_count < stage.max_retries:
            target = self.validator.classify_failure(result.gate)
            if target != LoopbackTarget.NONE:
                self.state.retry_count += 1
                return True  # 继续循环
        HumanGate.present(result)  # 耗尽→人工
        return False
```

---

## 四、数据模型扩展（Phase 0-1 目标）

> 当前 models.py 已有：GateStatus / LoopbackTarget / ExitCodeFingerprint / AgentOutput / QualityGateResult / ContextPackage / PipelineState。
> 以下 Contract / StageConfig / CTXBudget 为 Phase 0-1 新增。

```python
# models.py 目标形态

@dataclass
class Contract:
    """Agent产出契约——由Agent自述，下游消费"""
    agent_id: str
    summary: str           # "FastAPI Todo应用，4个CRUD端点"
    endpoints: list[dict]  # [{"method":"GET","path":"/todos","desc":"..."}]
    start_command: str     # "uvicorn main:app --port 8000"
    output_files: list[str]# ["backend/main.py"]
    test_hints: list[str]  # ["先测GET /todos返回200", ...]
    schema_info: dict      # 数据格式/表结构（可选）

@dataclass
class AgentOutput:
    files: list[str]          # 产出文件路径
    contract: Contract        # 结构化契约
    raw_output: str           # LLM原始输出
    verify_result: GateResult # 验证结果

@dataclass
class StageConfig:
    stage: int
    agents: list[str]
    mode: str                 # serial | parallel
    max_retries: int = 3      # 🆕
    on_fail: str = None       # 🆕 loopback目标

@dataclass
class PipelineState:
    project: str
    current_stage: int
    retry_count: int
    stage_results: list[StageResult]
    audit_trail: list[GateResult]  # 完整审计链
    started_at: str
    updated_at: str

class LoopbackTarget(Enum):
    CURRENT_STAGE = "current"    # 代码bug → 当前stage
    DESIGN_BRAIN = "design"      # 架构问题 → 设计脑
    PREVIOUS_STAGE = "previous"  # 上游数据问题
    NONE = "none"                # → HumanGate

class CTXBudget(Enum):
    NORMAL = "normal"       # ≤8KB 全量
    WARNING = "warning"     # ≤16KB contract+失败上下文
    CRITICAL = "critical"   # >16KB 仅摘要
```

---

## 五、config.json 目标结构（Phase 0 完成态）

> 当前 configs/ 中的配置较简（project + agents），以下为 Phase 0 完成后的目标结构。

```json
{
  "meta": {
    "project": "舆情监控",
    "version": "1.0"
  },
  "models": {
    "default": "qwen2.5:7b",
    "registry": {
      "qwen2.5:7b": {"provider": "ollama", "model": "qwen2.5:7b"},
      "gpt-4o-mini": {"provider": "openai", "model": "gpt-4o-mini"}
    }
  },
  "agents": [
    {
      "id": "collector",
      "name": "数据采集Agent",
      "role_goal": "从微博/知乎采集舆情数据",
      "model": "qwen2.5:7b",
      "prompt_file": "prompts/collector.md",
      "acceptance_criteria": [
        "输出为非空JSON",
        "每条数据含title/url/time字段"
      ],
      "verify_cmd": "python3 -c 'import json; d=json.load(open(\"data/raw.json\")); assert len(d)>0'",
      "output_file": "data/raw.json",
      "declared_tools": ["@web_fetch", "@parse_html"]
    }
  ],
  "topology": {
    "stages": [
      {"stage": 1, "agents": ["collector"], "mode": "serial", "max_retries": 3},
      {"stage": 2, "agents": ["analyzer", "summarizer"], "mode": "parallel", "max_retries": 2},
      {"stage": 3, "agents": ["reporter"], "mode": "serial", "max_retries": 3}
    ]
  },
  "tools": {
    "global_whitelist": [
      {"name": "web_fetch", "risk": "low"},
      {"name": "read_file", "risk": "low"}
    ],
    "per_agent": {
      "collector": [
        {"name": "web_fetch", "risk": "low"},
        {"name": "parse_html", "risk": "medium"}
      ]
    }
  },
  "context": {
    "routes": [
      {"from": "collector", "to": "analyzer", "files": ["data/raw.json"]},
      {"from": "collector", "to": "summarizer", "files": ["data/raw.json"]},
      {"from": ["analyzer","summarizer"], "to": "reporter", "files": ["data/analysis.json","data/summary.json"]}
    ]
  },
  "design_notes": {
    "why_these_agents": "采集和分析分离：IO密集 vs CPU密集",
    "gaps_found": ["日报格式未指定，默认Markdown"],
    "risks": ["知乎反爬可能导致采集失败"]
  }
}
```

---

## 六、实施路线图

| Phase | 内容 | 涉及模块 | 状态 |
|:--:|------|------|:--:|
| 0 | config.json驱动引擎 | config_loader + engine + models | 🔲 |
| 1 | 串行管线端到端 | + context_assembler + llm_client + contract | 🔲 |
| 2 | 闸门+回环闭环 | + validators扩展 + human_gate | 🔲 |
| 3 | 设计脑生成config | + orchestrator_agent + prompt_gen | 🔲 |
| 4 | 全量(并行/工具/CPOO/Doctor) | + memory_manager + tools系统 | 🔲 |

---

> 本文档基于以下设计决策生成（详见蓝图书 memory/projects/agent_gate_blueprint.md）：
> Q1(5问) Q2(16条) Q3(4修正) Q4(3条：记忆三层+Agent无状态+三级预算)
