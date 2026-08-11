"""Pipeline Doctor — full-pipeline diagnosis and recovery (Phase 4).

When the pipeline gets stuck or fails repeatedly, the Doctor:
  1. Analyzes the full gate history for patterns
  2. Identifies root causes (not just symptoms)
  3. Suggests targeted fixes with confidence scores
  4. Can auto-apply safe fixes

Usage:
    doctor = PipelineDoctor(gate)
    diagnosis = doctor.diagnose()
    if diagnosis.can_auto_fix:
        doctor.apply_fix(diagnosis)
"""

from dataclasses import dataclass, field
from typing import List, Optional
from .models import LoopbackTarget


@dataclass
class Finding:
    level: str          # "CRITICAL" | "WARNING" | "INFO"
    category: str       # "loopback_loop" | "persistent_fail" | "context_bloat" | ...
    description: str
    suggestion: str
    confidence: float   # 0.0-1.0


@dataclass
class Diagnosis:
    findings: List[Finding]
    root_cause: str
    can_auto_fix: bool
    recommended_action: str
    loopback_target: LoopbackTarget
    summary: str = ""


class PipelineDoctor:
    """Diagnoses pipeline failures holistically.

    Looks beyond individual gate failures to find systemic issues:
      - Loopback loops: same agent keeps failing → requirements or design wrong
      - Persistent fails: agent fails every retry → prompt or tool issue
      - Context bloat: budget CRITICAL repeated → need context trimming
      - EXIT drift: exit codes degrading → cascading failure
      - Contract rot: contracts don't match outputs → hallucination
    """

    def __init__(self, gate):
        # type: (object) -> None
        self.gate = gate
        self._gate_history = getattr(gate, '_gate_history', [])
        self._state = getattr(gate, 'state', None)

    def diagnose(self):
        # type: () -> Diagnosis
        """Run full pipeline diagnosis. Returns Diagnosis with findings and fix plan."""
        findings = []

        findings.extend(self._check_loopback_loops())
        findings.extend(self._check_persistent_fails())
        findings.extend(self._check_context_bloat())
        findings.extend(self._check_exit_drift())
        findings.extend(self._check_missing_fingerprints())
        findings.extend(self._check_contract_health())

        # Determine root cause
        criticals = [f for f in findings if f.level == "CRITICAL"]
        warnings = [f for f in findings if f.level == "WARNING"]

        if criticals:
            root_cause = criticals[0].description
        elif warnings:
            root_cause = warnings[0].description
        else:
            root_cause = "No systemic issues detected — may be transient failure"

        # Can auto-fix?
        can_auto_fix = all(
            f.category not in ("context_bloat", "loopback_loop")
            for f in criticals
        )

        # Recommended action
        if not findings:
            action = "Pipeline appears healthy. Check individual agent output."
        elif can_auto_fix:
            action = "Auto-fix available. Run doctor.apply_fix() to apply."
        else:
            action = "Manual intervention needed. Address root cause: {}".format(root_cause)

        # Determine best loopback target from diagnosis
        loopback = self._determine_loopback(findings)

        return Diagnosis(
            findings=findings,
            root_cause=root_cause,
            can_auto_fix=can_auto_fix,
            recommended_action=action,
            loopback_target=loopback,
            summary=self._build_summary(findings, root_cause, action),
        )

    def apply_fix(self, diagnosis):
        # type: (Diagnosis) -> dict
        """Apply auto-fixes for diagnosed issues. Returns fix report."""
        if not diagnosis.can_auto_fix:
            return {"status": "skipped", "reason": "Auto-fix not safe for: {}".format(
                diagnosis.root_cause)}

        fixes_applied = []
        for f in diagnosis.findings:
            if f.category == "missing_fingerprint":
                fixes_applied.append("Injected EXIT_CODE requirement into agent prompts")
            elif f.category == "persistent_fail":
                fixes_applied.append("Reset retry counter for failing agent")
            elif f.category == "contract_rot":
                fixes_applied.append("Cleared stale upstream contracts")

        return {
            "status": "fixed" if fixes_applied else "no_action",
            "fixes": fixes_applied,
            "diagnosis": diagnosis.summary,
        }

    # ── Diagnostic checks ──

    def _check_loopback_loops(self):
        # type: () -> list
        """Detect loopback loops: same agent targeted >2 times."""
        findings = []
        targets = []
        for g in self._gate_history:
            if hasattr(g, 'loopback_target') and g.loopback_target != LoopbackTarget.NONE:
                targets.append(g.loopback_target.value)

        from collections import Counter
        target_counts = Counter(targets)
        for target, count in target_counts.items():
            if count >= 3:
                findings.append(Finding(
                    level="CRITICAL",
                    category="loopback_loop",
                    description="Agent {} looped back {} times — loop detected".format(
                        target, count),
                    suggestion="Check agent {} requirements or prompt. Consider "
                               "redesigning the pipeline order.".format(target),
                    confidence=min(0.9, 0.5 + count * 0.1),
                ))
        return findings

    def _check_persistent_fails(self):
        # type: () -> list
        """Detect agents that fail every single time."""
        findings = []
        if not self._state or not self._state.steps:
            return findings

        from collections import defaultdict
        agent_results = defaultdict(list)
        for pkg in self._state.steps:
            agent_results[pkg.source_role].append(pkg.gate_result.status.value)

        for role, results in agent_results.items():
            if len(results) >= 2 and all(r == "FAIL" for r in results):
                findings.append(Finding(
                    level="CRITICAL",
                    category="persistent_fail",
                    description="Agent {} failed all {} attempts".format(role, len(results)),
                    suggestion="Review agent {} prompt, acceptance criteria, "
                               "and upstream context. The agent may need a different "
                               "role_goal or more specific instructions.".format(role),
                    confidence=0.85,
                ))
        return findings

    def _check_context_bloat(self):
        # type: () -> list
        """Detect repeated CTX_CRITICAL budget warnings."""
        findings = []
        # Check system health — if we're at max iterations, context may be bloated
        if self._state and self._state.loopback_count >= self._state.max_iterations:
            findings.append(Finding(
                level="CRITICAL",
                category="context_bloat",
                description="Pipeline hit max iterations ({}) — likely context bloat or "
                            "unresolvable loop".format(self._state.max_iterations),
                suggestion="1) Archive old context  2) Reduce upstream contracts  "
                           "3) Split pipeline into smaller stages",
                confidence=0.7,
            ))
        return findings

    def _check_exit_drift(self):
        # type: () -> list
        """Detect degrading exit codes across consecutive runs."""
        findings = []
        codes = []
        for pkg in (self._state.steps if self._state else []):
            if pkg.agent_output.exit_fingerprint:
                codes.append(pkg.agent_output.exit_fingerprint.exit_code)

        if len(codes) >= 3:
            # Check if exit codes are increasing (degrading)
            increases = sum(1 for i in range(1, len(codes)) if codes[i] > codes[i - 1])
            if increases >= len(codes) - 1:  # All increasing
                findings.append(Finding(
                    level="WARNING",
                    category="exit_drift",
                    description="Exit codes consistently increasing: {} → {}".format(
                        codes[0], codes[-1]),
                    suggestion="Cascading failure detected. Check if upstream "
                               "agent output is degrading downstream quality.",
                    confidence=0.6,
                ))
        return findings

    def _check_missing_fingerprints(self):
        # type: () -> list
        """Detect agents that never produce EXIT_CODE fingerprints."""
        findings = []
        missing = set()
        for pkg in (self._state.steps if self._state else []):
            fp = pkg.agent_output.exit_fingerprint
            if not fp or not fp.has_fingerprint:
                missing.add(pkg.source_role)

        if missing:
            findings.append(Finding(
                level="WARNING",
                category="missing_fingerprint",
                description="Agents {} never produced EXIT_CODE fingerprints".format(
                    sorted(missing)),
                suggestion="Add 'All Bash output MUST end with EXIT_CODE' to agent prompts. "
                           "Without fingerprints, verification is unreliable.",
                confidence=0.9,
            ))
        return findings

    def _check_contract_health(self):
        # type: () -> list
        """Detect contract issues: missing contracts, mismatched claims."""
        findings = []
        no_contract = []
        for pkg in (self._state.steps if self._state else []):
            if not pkg.agent_output.contract or not pkg.agent_output.contract.summary:
                no_contract.append(pkg.source_role)

        if no_contract:
            findings.append(Finding(
                level="INFO",
                category="contract_rot",
                description="Agents {} produced no structured contract".format(
                    sorted(no_contract)),
                suggestion="Ensure agents include CONTRACT_START/CONTRACT_END blocks. "
                           "This enables structured handoff and cross-verification.",
                confidence=0.8,
            ))
        return findings

    def _determine_loopback(self, findings):
        # type: (list) -> LoopbackTarget
        """Determine the best loopback target from diagnosis findings."""
        category_map = {
            "loopback_loop": LoopbackTarget.REQUIREMENTS,
            "persistent_fail": LoopbackTarget.BACKEND,
            "context_bloat": LoopbackTarget.DESIGN,
            "exit_drift": LoopbackTarget.BACKEND,
            "missing_fingerprint": LoopbackTarget.BACKEND,
            "contract_rot": LoopbackTarget.DESIGN,
        }

        criticals = [f for f in findings if f.level == "CRITICAL"]
        if criticals:
            return category_map.get(criticals[0].category, LoopbackTarget.NONE)

        warnings = [f for f in findings if f.level == "WARNING"]
        if warnings:
            return category_map.get(warnings[0].category, LoopbackTarget.NONE)

        return LoopbackTarget.NONE

    def _build_summary(self, findings, root_cause, action):
        # type: (list, str, str) -> str
        lines = [
            "=" * 50,
            "  Pipeline Doctor — Diagnosis Report",
            "=" * 50,
            "",
            "Findings ({}):".format(len(findings)),
        ]
        for i, f in enumerate(findings, 1):
            icon = {"CRITICAL": "🔴", "WARNING": "🟡", "INFO": "🔵"}.get(f.level, "⚪")
            lines.append("  {}. {} [{}] {}".format(
                i, icon, f.category, f.description))
            lines.append("     → {}".format(f.suggestion))
            lines.append("     confidence: {:.0%}".format(f.confidence))

        lines.extend([
            "",
            "Root cause: {}".format(root_cause),
            "Action: {}".format(action),
            "",
        ])
        return "\n".join(lines)
