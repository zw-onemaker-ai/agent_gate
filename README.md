# AgentGate

**A lightweight quality assurance framework for AI agent pipelines.**

Every agent output is Bash-verified, fingerprint-checked, and gate-audited before it reaches the next agent. No hallucinations pass through.

---

## The Problem

AI agents hallucinate. Multi-agent pipelines amplify this: Agent 2 trusts Agent 1's output, Agent 3 trusts Agent 2, and by step 4, the errors are buried under layers of unverified claims.

Existing frameworks (LangGraph, CrewAI, AutoGen) focus on **"getting agents to work"**. None focus on **"verifying agents worked correctly"**.

## The Solution

AgentGate adds three simple but non-negotiable quality protocols:

| Protocol | What It Does | Why It Matters |
|----------|-------------|----------------|
| **1. EXIT_CODE Fingerprint** | All Bash verification outputs must carry `EXIT:<code>` — printed by the shell, unforgeable by LLMs | Prevents agents from fabricating "all tests passed" |
| **2. Quality Gate** | Every agent output is checked for file existence + EXIT fingerprint before passing to the next agent | Stops error propagation at the boundary |
| **3. Oriented Loopback** | Failures route to the correct upstream agent (CSS bug→Frontend, not always→Requirements) | 80% fewer wasted re-runs |

---

## Quick Start

### Install

```bash
git clone https://github.com/yourname/agent-gate.git
cd agent-gate
pip install -r requirements.txt
```

### Run the Demo

```bash
# Mock mode (no LLM needed — tests the gate logic):
python3.9 examples/demo_3role.py --mock

# With Ollama (local model):
ollama pull qwen2.5:7b
python3.9 examples/demo_3role.py --provider ollama --model qwen2.5:7b

# With OpenAI:
export OPENAI_API_KEY=sk-...
python3.9 examples/demo_3role.py --provider openai --model gpt-4o-mini
```

### Concepts

```
  ┌─────────┐    ┌──────────────┐    ┌─────────┐    ┌──────────────┐    ┌─────────┐
  │ Agent 1 │───▶│ Quality Gate │───▶│ Agent 2 │───▶│ Quality Gate │───▶│ Agent 3 │
  │ (R1)    │    │ ✓ files      │    │ (R2)    │    │ ✓ files      │    │ (R3)    │
  │         │    │ ✓ EXIT:0     │    │         │    │ ✓ EXIT:0     │    │         │
  └─────────┘    └──────────────┘    └─────────┘    └──────────────┘    └─────────┘
       │               │                  │               │                  │
       ▼               ▼                  ▼               ▼                  ▼
  requirements.md  Gate:PASS         main.py         Gate:PASS        review_report.md
                   EXIT:0                             EXIT:0
```

### Core API

```python
from agent_gate import AgentGate

gate = AgentGate(project_name="my_project")

# Register agents in pipeline order
gate.register_agent(
    role="R1", name="Requirements",
    prompt_template=R1_PROMPT,
    output_file="requirements.md",
    verify_cmd="ls requirements.md && echo 'OK'",
)

gate.register_agent(
    role="R2", name="Backend Dev",
    prompt_template=R2_PROMPT,
    output_file="main.py",
    verify_cmd="python3 -m py_compile main.py",
)

# Run with quality gates at every step
state = gate.run_pipeline(initial_context="Build a Todo API")
print(gate.summary())
```

---

## Design Philosophy

**1. Verification over trust.** Agent output is guilty until proven correct.

**2. Minimal, not maximal.** Three protocols, not a framework. You bring your own agents (LangChain, CrewAI, raw LLM calls) — AgentGate only adds the verification layer.

**3. Bash as universal validator.** No custom DSL. If you can verify it with a shell command, AgentGate can enforce it.

**4. Model-agnostic.** Works with Ollama, OpenAI, LiteLLM, or any model that can receive a prompt and return text.

---

## Background

Extracted from production experience building 4 full-stack AI products as a solo developer. The core protocols were battle-tested across 50+ pipeline runs with real code generation. Key insight: **Bash verification with EXIT_CODE fingerprints is the cheapest, most reliable anti-hallucination mechanism available.**

---

## Project Structure

```
agent_gate/
├── src/
│   ├── engine.py        # Pipeline orchestrator + quality gate engine
│   ├── models.py        # Data models (AgentOutput, GateResult, PipelineState)
│   └── validators.py    # Bash verification + EXIT fingerprint + gate logic
├── examples/
│   └── demo_3role.py    # 3-role demo: Requirements → Code → Review
├── docs/
│   └── protocols/       # Protocol design docs
│       ├── 01_exit_fingerprint.md
│       ├── 02_quality_gate.md
│       └── 03_oriented_loopback.md
├── tests/
├── requirements.txt
└── README.md
```

## License

MIT
