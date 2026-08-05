#!/usr/bin/env python3
"""AgentGate Demo: Content Writing Pipeline (non-code scenario).

Proves AgentGate is NOT a code generation framework.
Same engine, same gate, same validators — different domain.

Pipeline: Topic Planner → Writer → Editor
Verification: acceptance_criteria → auto-generated verify_cmd
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from src.engine import AgentGate


R1_PROMPT = """You are a Content Strategist. Given a topic, produce a content plan.

Output format:
# Content Plan

## Topic
[the given topic]

## Target Audience
[who this is for]

## 3 Article Angles
1. [angle 1]
2. [angle 2]
3. [angle 3]

## Key Points to Cover
- [point]
- [point]

## Acceptance Criteria
- Final draft must be >300 words
- Must include at least 2 subheadings (##)
- Must end with a call to action
"""

R2_PROMPT = """You are a Writer. Based on the content plan, write a complete article draft.

Write in clear Chinese. The article should be engaging and practical.
Include the subheadings and call to action as specified in the plan.

Content plan:
{context}
"""

R3_PROMPT = """You are a Senior Editor. Review the article draft for quality.

Check:
- Word count meets requirement
- Subheadings present and logical
- Call to action is clear
- No filler or repetition

Output:
# Editorial Review

## Verdict: [PASS / NEEDS REVISION]

## Issues Found
- [list or "None"]

## Final Article (with edits applied)
[the polished article]

Article to review:
{context}
"""


def run_demo(provider="ollama", model="qwen2.5:7b", mock=False):
    print("=" * 50)
    print("  AgentGate — Content Writing Pipeline")
    print("  Topic Planner → Writer → Editor")
    print("  Verification: auto-generated from acceptance criteria")
    print("=" * 50)

    gate = AgentGate(
        project_name="content_demo",
        output_dir="./output/content_demo",
        max_iterations=3,
        model_provider=provider,
        model_name=model,
    )

    # R1: Topic Planner — writes acceptance criteria in its output
    gate.register_agent(
        role="R1",
        name="Content Strategist",
        prompt_template=R1_PROMPT,
        output_file="content_plan.md",
        verify_cmd="grep -c 'Angle' output/content_demo/content_plan.md",
    )

    # R2: Writer — verify_cmd auto-generated from acceptance criteria
    gate.register_agent(
        role="R2",
        name="Writer",
        prompt_template=R2_PROMPT,
        output_file="draft.md",
        # NO verify_cmd — auto-generated from acceptance_criteria below
        acceptance_criteria=[
            "Draft must be >300 words",
            "Must include at least 2 subheadings (## heading)",
            "Must end with a call to action",
        ],
    )

    # R3: Editor
    gate.register_agent(
        role="R3",
        name="Editor",
        prompt_template=R3_PROMPT,
        output_file="final_article.md",
        verify_cmd="grep -qi 'pass\\|approved' output/content_demo/final_article.md",
    )

    if mock:
        print("\n[MOCK MODE] Simulating without LLM...\n")
        return _run_mock(gate)

    state = gate.run_pipeline(
        initial_context="Topic: 运维工程师如何利用AI提升工作效率"
    )
    print("\n" + gate.summary())
    return state


def _run_mock(gate):
    """Mock mode — show the auto-verification cmd generation."""
    from src.acceptance import generate_verify_cmd, build_acceptance_prompt

    # Show what verify_cmd would be auto-generated
    agent = gate._agents["R2"]
    prompt = build_acceptance_prompt(
        criteria=agent["acceptance_criteria"],
        files=["output/content_demo/draft.md"],
    )

    print("=== Auto-generated verify_cmd prompt ===")
    print(prompt)
    print()
    print("[MOCK] In real mode, LLM would generate a bash command like:")
    print("  test $(wc -w < draft.md) -gt 300 && \\")
    print("  test $(grep -c '^## ' draft.md) -ge 2 && \\")
    print("  grep -qi '立即\\|开始\\|试试\\|行动' draft.md")
    print()

    # Simulate the full pipeline with mock outputs
    mock_outputs = {
        "R1": "# Content Plan\n## Topic\n运维工程师如何利用AI\n\n## 3 Article Angles\n1. 零基础入门\n2. 实战案例\n3. 进阶路线\n",
        "R2": "# 运维工程师的AI实战指南\n\n## 从零开始\nAI不是遥不可及。对于每天跟服务器打交道的运维工程师来说，AI是一个可以被立即使用的工具。从自动生成监控脚本到智能分析日志告警，AI可以将原本需要半小时的手动排查缩短到三十秒。关键是不要被\"机器学习\"\"深度学习\"这些术语吓到——你不需要成为算法工程师。你只需要知道AI能解决你的什么问题。\n\n## 三个实战案例\n第一个案例：用AI生成Nginx日志分析脚本。只需要把一段日志样本丢给AI，描述你想提取的字段，它能在十秒内生一个完整的Python分析脚本。第二个案例：告警智能分级。每天上百条告警，真正需要处理的不到五条。AI可以学习你的处理习惯，自动标记告警优先级。第三个案例：变更风险评估。每次上线前把变更内容丢给AI做风险分析，它可以从历史故障模式中识别出潜在风险点。\n\n## 从工具到思维\nAI不只是新工具，是一种新的工作方式。运维工程师的核心能力不是敲命令，是快速定位问题和保证系统稳定。AI把机械劳动自动化了，让你有更多时间做真正需要判断力的事。\n\n## 立即行动\n今天就尝试一件事：把你最近一次手动排查故障的过程写下来，丢给AI，问它\"这个流程里哪一步可以自动化\"。你可能会惊讶于它的答案。\n",
        "R3": "# Editorial Review\n## Verdict: PASS\n## Issues Found\n- None\n",
    }

    for role in ["R1", "R2", "R3"]:
        agent = gate._agents[role]
        output_file = gate.output_dir / agent["output_file"]
        output_file.parent.mkdir(parents=True, exist_ok=True)
        output_file.write_text(mock_outputs[role])

        verify_cmd = agent.get("verify_cmd", "")
        # R2: auto-generate from acceptance criteria
        if not verify_cmd and agent.get("acceptance_criteria"):
            verify_cmd = "test $(wc -m < output/content_demo/draft.md) -gt 300 && test $(grep -c '^## ' output/content_demo/draft.md) -ge 2"

        from src.validators import run_bash
        verify_output, _ = run_bash(verify_cmd) if verify_cmd else ("EXIT:0", 0)

        from src.validators import quality_gate_check
        result = quality_gate_check(
            role=role,
            part_a_files=[str(output_file)],
            verification_output=verify_output,
        )
        gate._gate_history.append(result)
        gate._print_gate_report(result)

    print("\n" + "=" * 50)
    print("  MOCK PIPELINE COMPLETE — Content Writing")
    for f in sorted(gate.output_dir.rglob("*")):
        if f.is_file() and f.suffix != ".pyc":
            print("    {}".format(f.relative_to(gate.output_dir)))
    print("=" * 50)
    return gate.state


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="AgentGate Content Writing Demo")
    p.add_argument("--provider", default="ollama")
    p.add_argument("--model", default="qwen2.5:7b")
    p.add_argument("--mock", action="store_true")
    args = p.parse_args()
    run_demo(provider=args.provider, model=args.model, mock=args.mock)
