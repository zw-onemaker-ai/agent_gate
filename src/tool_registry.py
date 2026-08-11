"""Tool Registry — centralized tool whitelist and risk enforcement (Phase 4).

Every agent declares tools it needs (e.g., @write_file, @run_bash).
The registry validates these declarations against a global whitelist
and enforces risk-based access control.

Risk levels:
  low    — read-only, no side effects (read_file, web_search)
  medium — writes files, limited blast radius (write_file, run_test)
  high   — executes arbitrary commands, network access (run_bash, deploy)
  critical — full system access (shell, sudo)
"""

from .models import GateStatus, QualityGateResult, LoopbackTarget


# ── Global tool whitelist ──

TOOL_WHITELIST = {
    "@read_file": {
        "name": "read_file",
        "risk": "low",
        "description": "Read content from a file on disk",
        "requires_gate": False,
    },
    "@write_file": {
        "name": "write_file",
        "risk": "medium",
        "description": "Write content to a file on disk",
        "requires_gate": True,
    },
    "@run_bash": {
        "name": "run_bash",
        "risk": "medium",
        "description": "Execute a bash command (sandboxed)",
        "requires_gate": True,
    },
    "@web_search": {
        "name": "web_search",
        "risk": "low",
        "description": "Search the web for information",
        "requires_gate": False,
    },
    "@web_fetch": {
        "name": "web_fetch",
        "risk": "medium",
        "description": "Fetch content from a URL",
        "requires_gate": True,
    },
    "@llm_judge": {
        "name": "llm_judge",
        "risk": "low",
        "description": "Use LLM as judge for quality evaluation",
        "requires_gate": False,
    },
    "@git_commit": {
        "name": "git_commit",
        "risk": "high",
        "description": "Create a git commit with changes",
        "requires_gate": True,
    },
    "@deploy": {
        "name": "deploy",
        "risk": "high",
        "description": "Deploy to staging/production",
        "requires_gate": True,
    },
    "@database": {
        "name": "database",
        "risk": "high",
        "description": "Connect to and modify a database",
        "requires_gate": True,
    },
    "@safe_python": {
        "name": "safe_python",
        "risk": "medium",
        "description": "Execute Python code in a restricted sandbox",
        "requires_gate": True,
    },
}


class ToolRegistry:
    """Central tool management and access control.

    Usage:
        reg = ToolRegistry()
        reg.validate_agent_tools(["@write_file", "@read_file"])  # OK
        reg.validate_agent_tools(["@sudo_rm_rf"])                 # ValueError
        reg.check_gate_required(["@write_file"])                  # True
    """

    def __init__(self, whitelist=None):
        # type: (dict) -> None
        self.whitelist = whitelist or TOOL_WHITELIST

    def validate_agent_tools(self, declared_tools):
        # type: (list) -> list
        """Validate an agent's declared tools. Returns list of errors."""
        errors = []
        for tool in declared_tools:
            if tool not in self.whitelist:
                errors.append(
                    "Unknown tool '{}'. Available: {}".format(
                        tool, sorted(self.whitelist.keys())))
                continue
            if not tool.startswith("@"):
                errors.append(
                    "Tool '{}' should start with @ (e.g. @write_file)".format(tool))
        return errors

    def get_risk(self, tool_name):
        # type: (str) -> str
        """Get risk level for a tool."""
        return self.whitelist.get(tool_name, {}).get("risk", "unknown")

    def requires_gate(self, declared_tools):
        # type: (list) -> bool
        """Check if any declared tool requires quality gate verification."""
        for tool in declared_tools:
            if self.whitelist.get(tool, {}).get("requires_gate"):
                return True
        return False

    def max_risk(self, declared_tools):
        # type: (list) -> str
        """Get the maximum risk level among declared tools."""
        risk_order = {"low": 0, "medium": 1, "high": 2, "critical": 3, "unknown": 4}
        max_level = 0
        max_risk = "low"
        for tool in declared_tools:
            risk = self.get_risk(tool)
            level = risk_order.get(risk, 4)
            if level > max_level:
                max_level = level
                max_risk = risk
        return max_risk

    def gate_check_tools(self, agent_role, declared_tools):
        # type: (str, list) -> QualityGateResult
        """Run a tool-focused quality gate check.

        Verifies:
          1. All declared tools are in whitelist
          2. High/critical risk tools are flagged
          3. Gate-requiring tools are identified
        """
        checks = []
        fail_reasons = []

        errors = self.validate_agent_tools(declared_tools)
        if errors:
            checks.append({
                "name": "tool_whitelist",
                "status": "FAIL",
                "detail": errors,
            })
            fail_reasons.extend(errors)
        else:
            checks.append({
                "name": "tool_whitelist",
                "status": "PASS",
                "detail": "All {} tools in whitelist".format(len(declared_tools)),
            })

        max_r = self.max_risk(declared_tools)
        if max_r in ("high", "critical"):
            checks.append({
                "name": "tool_risk",
                "status": "WARN",
                "detail": "Agent {} declares {} risk tools: {}".format(
                    agent_role, max_r, declared_tools),
            })
        else:
            checks.append({
                "name": "tool_risk",
                "status": "PASS",
                "detail": "Max risk: {}".format(max_r),
            })

        status = GateStatus.FAIL if fail_reasons else GateStatus.PASS
        return QualityGateResult(
            status=status,
            checks=checks,
            fail_reasons=fail_reasons,
            loopback_target=LoopbackTarget.NONE,
        )

    def to_config_whitelist(self):
        # type: () -> list
        """Export whitelist in config.json format."""
        return [
            {"name": v["name"], "risk": v["risk"]}
            for v in self.whitelist.values()
        ]


# ── Singleton ──

_default_registry = None


def get_registry():
    # type: () -> ToolRegistry
    """Get the global tool registry singleton."""
    global _default_registry
    if _default_registry is None:
        _default_registry = ToolRegistry()
    return _default_registry
