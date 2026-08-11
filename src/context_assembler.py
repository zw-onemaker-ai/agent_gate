"""Context assembler — builds agent prompts from L1/L2/L3 memory (Phase 1).

L1 — Project-level: background, role_goal (always included)
L2 — Session-level: upstream contracts, failure context (selected by budget)
L3 — Working memory: agent's own scratch space (future, not implemented yet)

Budget modes:
  CTX_NORMAL  (≤8KB)  → Full context: L1 + all upstream contracts
  CTX_WARNING (≤16KB) → Trimmed: L1 + latest contract + fail context
  CTX_CRITICAL (>16KB) → Minimal: L1 + summary only
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .models import Contract, CTXBudget


# ── Budget thresholds ──
BUDGET_NORMAL = 8000    # bytes
BUDGET_WARNING = 16000  # bytes


@dataclass
class AssembledContext:
    """Complete context package for an agent invocation."""
    prompt: str                        # Full system+user prompt
    budget_mode: str                   # CTX_NORMAL / CTX_WARNING / CTX_CRITICAL
    files_available: List[str] = field(default_factory=list)
    hints: List[str] = field(default_factory=list)
    total_bytes: int = 0


class ContextAssembler:
    """Assembles agent context from multi-layer memory.

    Usage:
        assembler = ContextAssembler()
        ctx = assembler.build(
            agent_id="R2",
            role_goal="Design product spec from requirements",
            upstream_contracts=[contract_r1],
            failure_context=None,
        )
        # ctx.prompt → ready to inject into LLM
    """

    def build(self, agent_id, role_goal="",
              prompt_template="",
              upstream_contracts=None,
              failure_context=None,
              project_background=""):
        # type: (str, str, str, list, str, str) -> AssembledContext
        """Build assembled context for an agent.

        Args:
            agent_id: Agent identifier (e.g. "R2")
            role_goal: 1-2 sentence role description (L1)
            prompt_template: Full prompt override. If provided, contracts are
                appended as additional context rather than replacing.
            upstream_contracts: List of Contract from upstream agents (L2)
            failure_context: Error info from previous loopback (L2)
            project_background: Project-level description (L1)
        """
        upstream_contracts = upstream_contracts or []

        # ── Calculate total size ──
        contract_text = self._contracts_to_text(upstream_contracts)
        fail_text = failure_context or ""
        base_text = "{}\n{}\n{}".format(
            project_background, role_goal, prompt_template[:500])

        total = len(base_text.encode()) + len(contract_text.encode()) + len(fail_text.encode())

        # ── Determine budget mode ──
        if total <= BUDGET_NORMAL:
            budget = CTXBudget.NORMAL
        elif total <= BUDGET_WARNING:
            budget = CTXBudget.WARNING
        else:
            budget = CTXBudget.CRITICAL

        # ── Assemble prompt by budget ──
        files_available = []
        hints = []

        if prompt_template:
            # Full prompt provided — use it as base, append context
            assembled = prompt_template
        else:
            assembled = ""

        # Add project context
        if project_background:
            assembled += "\n\n## Project Background\n{}\n".format(project_background)
        if role_goal:
            assembled += "\n## Your Role\n{}\n".format(role_goal)

        # Add failure context (always included if present, regardless of budget)
        if failure_context:
            assembled += "\n## ⚠️ Previous Failure Context\n{}\n".format(failure_context)
            hints.append("Previous attempt failed. Review failure context above and fix the issues.")

        # Add upstream contracts (budget-aware)
        if upstream_contracts:
            if budget == CTXBudget.NORMAL:
                assembled += "\n## Upstream Outputs\n"
                for c in upstream_contracts:
                    assembled += self._contract_to_markdown(c)
                    files_available.extend(c.output_files)
            elif budget == CTXBudget.WARNING:
                # Only latest contract
                latest = upstream_contracts[-1]
                assembled += "\n## Latest Upstream Output\n"
                assembled += self._contract_to_markdown(latest)
                files_available.extend(latest.output_files)
            else:  # CRITICAL
                # Summary only
                summaries = [c.summary for c in upstream_contracts if c.summary]
                if summaries:
                    assembled += "\n## Upstream Summary\n{}\n".format(" | ".join(summaries))
                hints.append("Context budget CRITICAL — upstream details truncated. Ask if needed.")

        # Add output instructions
        assembled += """
## Output Requirements
Produce TWO parts:

### Part A (deliverable)
Your actual output — code, document, or data as specified.

### Part B (contract)
A structured summary of what you produced:
```
CONTRACT_START
summary: {one sentence describing what you built}
output_files: {comma-separated list of files created}
start_command: {how to run/use your output}
test_hints: {hints for testing your output}
CONTRACT_END
```
"""
        return AssembledContext(
            prompt=assembled,
            budget_mode=budget.value,
            files_available=files_available,
            hints=hints,
            total_bytes=total,
        )

    def build_for_retry(self, agent_id, role_goal, fail_reason,
                        upstream_contracts=None, prompt_template="",
                        project_background=""):
        # type: (str, str, str, list, str, str) -> AssembledContext
        """Build context for a retry after loopback.

        Injects failure context explicitly, same budget logic applies.
        """
        return self.build(
            agent_id=agent_id,
            role_goal=role_goal,
            prompt_template=prompt_template,
            upstream_contracts=upstream_contracts,
            failure_context="[RETRY] Previous attempt failed: {}".format(fail_reason),
            project_background=project_background,
        )

    # ── Internal helpers ──

    @staticmethod
    def _contracts_to_text(contracts):
        # type: (list) -> str
        return "\n".join(ContextAssembler._contract_to_markdown(c) for c in contracts)

    @staticmethod
    def _contract_to_markdown(contract):
        # type: (Contract) -> str
        """Format a Contract as Markdown for injection into prompt."""
        parts = [
            "### Agent {}: {}\n".format(contract.agent_id, contract.summary),
        ]
        if contract.output_files:
            parts.append("- Files: {}\n".format(", ".join(contract.output_files)))
        if contract.endpoints:
            eps = ", ".join(
                "{} {}".format(e.get("method", "?"), e.get("path", "?"))
                for e in contract.endpoints
            )
            parts.append("- Endpoints: {}\n".format(eps))
        if contract.start_command:
            parts.append("- Start: `{}`\n".format(contract.start_command))
        if contract.test_hints:
            parts.append("- Test hints: {}\n".format("; ".join(contract.test_hints)))
        return "".join(parts)


def parse_contract_from_output(raw_output, agent_id):
    # type: (str, str) -> Optional[Contract]
    """Parse a Contract from agent's raw LLM output.

    Looks for CONTRACT_START ... CONTRACT_END block in Part B.
    """
    try:
        start = raw_output.find("CONTRACT_START")
        end = raw_output.find("CONTRACT_END")
        if start == -1 or end == -1:
            return None

        block = raw_output[start + len("CONTRACT_START"):end].strip()
        fields = {}
        for line in block.split("\n"):
            line = line.strip()
            if ":" in line:
                key, _, val = line.partition(":")
                key = key.strip().lower().replace(" ", "_")
                val = val.strip()
                if key == "output_files":
                    fields[key] = [f.strip() for f in val.split(",") if f.strip()]
                elif key == "test_hints":
                    fields[key] = [h.strip() for h in val.split(";") if h.strip()]
                else:
                    fields[key] = val

        return Contract(
            agent_id=agent_id,
            summary=fields.get("summary", ""),
            output_files=fields.get("output_files", []),
            start_command=fields.get("start_command", ""),
            test_hints=fields.get("test_hints", []),
        )
    except Exception:
        return None
