"""AgentGate Engine — model-agnostic quality-gated agent pipeline.

Core design (extracted from 一人公司 v4.4.0):
  - Every agent output passes through quality_gate before reaching next agent
  - All Bash verification must carry EXIT_CODE fingerprint
  - Failed gate → oriented loopback (not always back to start)
  - Context packages are immutable snapshots for audit trail
"""

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional

from .models import (
    AgentOutput, ContextPackage, GateStatus, LoopbackTarget,
    PipelineState, QualityGateResult,
)
from .validators import quality_gate_check, run_bash, check_context_budget


class AgentGate:
    """Quality-gated agent pipeline engine."""

    def __init__(self, project_name, output_dir="./output", max_iterations=5,
                 model_provider="ollama", model_name="qwen2.5:7b"):
        # type: (str, str, int, str, str) -> None
        self.state = PipelineState(
            project_name=project_name,
            max_iterations=max_iterations,
        )
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.model_provider = model_provider
        self.model_name = model_name
        self._agents = {}  # type: Dict[str, dict]
        self._gate_history = []  # type: List[QualityGateResult]

    def register_agent(self, role, name, prompt_template="",
                       verify_cmd="", output_file="",
                       acceptance_criteria=None,
                       role_goal="", scenario_type="general"):
        # type: (str, str, str, str, str, list, str, str) -> None
        """Register an agent in the pipeline.

        Args:
            prompt_template: Full prompt. If empty AND role_goal provided,
                prompt will be auto-generated from role_goal via CPOO standard.
            acceptance_criteria: Natural language criteria.
                If provided and verify_cmd is empty, verify_cmd auto-generated.
            role_goal: 1-2 sentence role description. Triggers prompt auto-gen
                when prompt_template is empty.
            scenario_type: "code_gen" | "content_writing" | "data_analysis" | "devops"
        """
        self._agents[role] = {
            "name": name,
            "prompt_template": prompt_template,
            "verify_cmd": verify_cmd,
            "output_file": output_file,
            "acceptance_criteria": acceptance_criteria or [],
            "role_goal": role_goal,
            "scenario_type": scenario_type,
        }

    def _call_llm(self, system_prompt, user_prompt):
        # type: (str, str) -> str
        """Call LLM. Supports Ollama and LiteLLM providers."""
        if self.model_provider == "ollama":
            return self._call_ollama(system_prompt, user_prompt)
        elif self.model_provider in ("openai", "litellm"):
            return self._call_litellm(system_prompt, user_prompt)
        else:
            raise ValueError("Unknown provider: {}".format(self.model_provider))

    def _call_ollama(self, system, user):
        # type: (str, str) -> str
        """Call Ollama local model."""
        import urllib.request
        payload = json.dumps({
            "model": self.model_name,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": False,
        }).encode()
        req = urllib.request.Request(
            "http://localhost:11434/api/chat",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = json.loads(resp.read())
                return data.get("message", {}).get("content", "")
        except Exception as e:
            return "[OLLAMA_ERROR: {}]".format(e)

    def _call_litellm(self, system, user):
        # type: (str, str) -> str
        """Call via LiteLLM (supports 100+ providers)."""
        try:
            from litellm import completion
            resp = completion(
                model="{}/{}".format(self.model_provider, self.model_name),
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            )
            return resp.choices[0].message.content
        except ImportError:
            return "[LITELLM_NOT_INSTALLED: pip install litellm]"
        except Exception as e:
            return "[LITELLM_ERROR: {}]".format(e)

    # ── Pipeline Execution ──

    def run_step(self, role, context=""):
        # type: (str, str) -> AgentOutput
        """Execute a single agent step with quality gate."""
        agent = self._agents.get(role)
        if not agent:
            raise ValueError("Agent {} not registered".format(role))

        print("\n" + "=" * 50)
        print("  {}: {}".format(role, agent["name"]))
        print("=" * 50)

        # Auto-generate prompt from role_goal if not provided
        prompt_template = agent.get("prompt_template", "")
        if not prompt_template and agent.get("role_goal"):
            from .prompt_gen import generate_agent_prompt
            print("  [AUTO] Generating prompt from role_goal...")
            prompt_template = generate_agent_prompt(
                role_name=agent["name"],
                role_goal=agent["role_goal"],
                scenario_type=agent.get("scenario_type", "general"),
                output_file=agent.get("output_file", "output.md"),
                criteria=agent.get("acceptance_criteria", []),
                call_llm_fn=self._call_llm,
            )
            agent["prompt_template"] = prompt_template
            print("  [AUTO] Prompt generated ({} chars)".format(len(prompt_template)))

        raw_output = self._call_llm(
            system_prompt=prompt_template,
            user_prompt=context or "Proceed with your task.",
        )

        output_file = agent.get("output_file", "")
        part_a_files = []
        if output_file:
            full_path = self.output_dir / output_file
            full_path.parent.mkdir(parents=True, exist_ok=True)
            full_path.write_text(raw_output, encoding="utf-8")
            part_a_files = [str(full_path)]

        verify_cmd = agent.get("verify_cmd", "")
        acceptance_criteria = agent.get("acceptance_criteria", [])

        # Auto-generate verify_cmd from acceptance criteria if not provided
        if not verify_cmd and acceptance_criteria:
            from .acceptance import generate_verify_cmd
            print("  [AUTO] Generating verify_cmd from {} acceptance criteria...".format(
                len(acceptance_criteria)))
            verify_cmd = generate_verify_cmd(
                criteria=acceptance_criteria,
                files=part_a_files,
                call_llm_fn=self._call_llm,
                output_dir=str(self.output_dir),
            )
            print("  [AUTO] verify_cmd = {}".format(verify_cmd[:120]))
            # Cache it back so downstream can see it
            agent["verify_cmd"] = verify_cmd

        verify_output = ""
        if verify_cmd:
            verify_output, _ = run_bash(verify_cmd)

        gate_result = quality_gate_check(
            role=role,
            part_a_files=part_a_files,
            verification_output=verify_output,
        )
        self._gate_history.append(gate_result)

        agent_output = AgentOutput(
            role=role,
            role_name=agent["name"],
            part_a_files=part_a_files,
            part_b="Quality Gate: {}".format(gate_result.status.value),
            exit_fingerprint=gate_result.exit_fingerprint,
            quality_gate=gate_result.status,
            context_card=self._build_context_card(role, agent, gate_result),
        )

        self._print_gate_report(gate_result)
        return agent_output

    def run_pipeline(self, initial_context=""):
        # type: (str) -> PipelineState
        """Run the full pipeline with loopback."""
        context = initial_context
        roles = list(self._agents.keys())

        while self.state.current_role in self._agents:
            current = self.state.current_role

            try:
                output = self.run_step(current, context)
            except Exception as e:
                print("\n[ERROR] {} crashed: {}".format(current, e))
                self.state.loopback_count += 1
                if self.state.loopback_count >= self.state.max_iterations:
                    print("\n[FATAL] Max iterations ({}) reached.".format(self.state.max_iterations))
                    break
                continue

            pkg = ContextPackage(
                package_id="ctx-{}-{}".format(current, datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")),
                source_role=current,
                target_roles=self._next_roles(current, roles),
                agent_output=output,
                gate_result=self._gate_history[-1],
            )
            self.state.steps.append(pkg)

            if output.quality_gate == GateStatus.FAIL:
                self.state.loopback_count += 1
                if self.state.loopback_count >= self.state.max_iterations:
                    print("\n[FATAL] Loopback limit ({}) exceeded.".format(self.state.max_iterations))
                    break
                target = self._gate_history[-1].loopback_target

                # Human gate: when automated classification fails
                if target == LoopbackTarget.NONE:
                    print("\n[HUMAN_GATE] Cannot determine loopback target automatically.")
                    from .human_gate import human_gate_interactive
                    target = human_gate_interactive(
                        self._gate_history[-1].fail_reasons,
                        self._gate_history[-1].exit_fingerprint.raw_output if self._gate_history[-1].exit_fingerprint else "",
                    )

                print("\n[LOOPBACK] -> {} (reason: {})".format(
                    target.value, ", ".join(self._gate_history[-1].fail_reasons)))
                self.state.current_role = target.value
                context = "[LOOPBACK from {}] {}".format(
                    current, ", ".join(self._gate_history[-1].fail_reasons))
                continue

            next_idx = roles.index(current) + 1
            if next_idx >= len(roles):
                print("\n" + "=" * 50)
                print("  PIPELINE COMPLETE")
                print("  Steps: {} | Loopbacks: {}".format(
                    len(self.state.steps), self.state.loopback_count))
                print("=" * 50)
                break
            self.state.current_role = roles[next_idx]
            context = output.context_card

        return self.state

    def _next_roles(self, current, roles):
        # type: (str, List[str]) -> List[str]
        idx = roles.index(current) if current in roles else -1
        if idx < 0 or idx + 1 >= len(roles):
            return []
        return [roles[idx + 1]]

    def _build_context_card(self, role, agent, gate):
        # type: (str, dict, QualityGateResult) -> str
        exit_code = gate.exit_fingerprint.exit_code if gate.exit_fingerprint else "N/A"
        return "[{}:{}] Gate:{} EXIT:{} Issues:{}".format(
            role, agent["name"], gate.status.value, exit_code, len(gate.fail_reasons))

    def _print_gate_report(self, gate):
        # type: (QualityGateResult) -> None
        icon = "PASS" if gate.status == GateStatus.PASS else "FAIL"
        print("\n  [{}] Quality Gate: {}".format(icon, gate.status.value))
        for c in gate.checks:
            s = "PASS" if c["status"] == "PASS" else "FAIL"
            print("     [{}] {}".format(s, c["name"]))
        if gate.fail_reasons:
            for r in gate.fail_reasons:
                print("     [!] {}".format(r))
        if gate.exit_fingerprint and gate.exit_fingerprint.has_fingerprint:
            print("     [FINGERPRINT] EXIT:{} ({} instances)".format(
                gate.exit_fingerprint.exit_code, gate.exit_fingerprint.count))
        else:
            print("     [!] No EXIT_CODE fingerprint!")

    def summary(self):
        # type: () -> str
        lines = [
            "Pipeline: {}".format(self.state.project_name),
            "Steps executed: {}".format(len(self.state.steps)),
            "Loopbacks: {}".format(self.state.loopback_count),
            "Final role: {}".format(self.state.current_role),
            "Max iterations: {}".format(self.state.max_iterations),
            "",
            "Gate History:",
        ]
        for i, g in enumerate(self._gate_history):
            lines.append("  Step {}: {} | Loopback->{}".format(
                i + 1, g.status.value, g.loopback_target.value))
        return "\n".join(lines)
