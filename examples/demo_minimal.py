#!/usr/bin/env python3
"""AgentGate Demo: Minimal Registration — Auto-generated prompts & verify_cmd.

Human only provides:
  - role_name + role_goal (→ auto-generates full CPOO-standard prompt)
  - acceptance_criteria (→ auto-generates bash verify command)

This is AgentGate's vision: you describe WHAT the agent does and HOW to verify it.
The system generates everything else.
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from src.engine import AgentGate


def run_demo(provider="ollama", model="qwen2.5:7b", mock=True):
    print("=" * 50)
    print("  AgentGate — Minimal Registration Demo")
    print("  Human writes: role_goal + acceptance_criteria")
    print("  System generates: prompt + verify_cmd")
    print("=" * 50)

    gate = AgentGate(
        project_name="minimal_demo",
        output_dir="./output/minimal_demo",
        max_iterations=3,
        model_provider=provider,
        model_name=model,
    )

    # R1: Data Analyst — no prompt_template, no verify_cmd
    #      Just role_goal + acceptance_criteria. Everything else auto-generated.
    gate.register_agent(
        role="R1",
        name="Data Analyst",
        # NO prompt_template → auto-generated from role_goal
        role_goal="Analyze a CSV dataset and produce a summary report with key statistics, trends, and recommendations.",
        output_file="data_report.md",
        # NO verify_cmd → auto-generated from acceptance_criteria
        acceptance_criteria=[
            "Report must be >200 characters",
            "Must include section '## Key Statistics'",
            "Must include section '## Recommendations'",
            "Must mention the number of rows analyzed",
        ],
        scenario_type="data_analysis",
    )

    # R2: Data Visualization Designer
    gate.register_agent(
        role="R2",
        name="Visualization Designer",
        role_goal="Based on the data analysis report, design a visualization plan: choose chart types, color schemes, and specify what data goes on which axis.",
        output_file="viz_plan.md",
        acceptance_criteria=[
            "Must specify at least 2 chart types",
            "Must explain color scheme rationale",
            "Must reference data from the analysis report",
        ],
        scenario_type="data_analysis",
    )

    # R3: Dashboard Builder
    gate.register_agent(
        role="R3",
        name="Dashboard Builder",
        role_goal="Generate a complete HTML dashboard with embedded Chart.js visualizations based on the visualization plan.",
        output_file="dashboard.html",
        acceptance_criteria=[
            "File must be valid HTML with <html>, <head>, <body> tags",
            "Must include Chart.js CDN script tag",
            "Must contain at least one <canvas> element",
            "Dashboard title must be in <h1>",
        ],
        scenario_type="code_gen",
    )

    if mock:
        print("\n[MOCK MODE] Showing what gets auto-generated...\n")

        # Show what would happen for each agent
        from src.prompt_gen import _template_generate

        for role in ["R1", "R2", "R3"]:
            agent = gate._agents[role]
            print("-" * 50)
            print("  {}: {} → {}".format(role, agent["name"], agent["output_file"]))
            print("  Role goal: {}".format(agent["role_goal"]))
            print("  Criteria: {}".format(agent["acceptance_criteria"]))
            print()

            # Show auto-generated prompt
            prompt = _template_generate(
                role_name=agent["name"],
                role_goal=agent["role_goal"],
                scenario_type=agent["scenario_type"],
                output_file=agent["output_file"],
                criteria_text="\n".join("- {}".format(c) for c in agent["acceptance_criteria"]),
            )
            print("  [AUTO-PROMPT] {} chars".format(len(prompt)))
            print("  First line: {}".format(prompt.strip().split("\n")[0]))
            print()

            # Show auto-generated verify_cmd would be like
            from src.acceptance import build_acceptance_prompt
            vprompt = build_acceptance_prompt(
                criteria=agent["acceptance_criteria"],
                files=["output/minimal_demo/{}".format(agent["output_file"])],
            )
            print("  [AUTO-VERIFY would contain checks for:]")
            for c in agent["acceptance_criteria"]:
                print("    - {}".format(c))
            print()

        print("=" * 50)
        print("  Human wrote: 3 role_goals + 9 acceptance criteria")
        print("  System would generate: 3 CPOO prompts + 3 verify commands")
        print("  Gate layer: UNCHANGED (same validators.py, same engine.py)")
        print("=" * 50)
        return gate.state

    state = gate.run_pipeline(
        initial_context="Dataset: monthly sales data, 500 rows, columns: date/product/revenue/region"
    )
    print("\n" + gate.summary())
    return state


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--provider", default="ollama")
    p.add_argument("--model", default="qwen2.5:7b")
    p.add_argument("--mock", action="store_true", default=True)
    args = p.parse_args()
    run_demo(provider=args.provider, model=args.model, mock=args.mock)
