"""CPOO Scorer — prompt quality evaluation against the 5-module standard (Phase 4).

Based on 一人公司 v4.4.0 CPOO (首席提示词优化官) standard:
  Module 1: Role definition (who/what/why)            — 20 points
  Module 2: Constraints (hard 🔴 + soft 🟡)            — 20 points
  Module 3: Workflow (numbered steps, input→output)    — 20 points
  Module 4: IO Format (exactly what output looks like) — 20 points
  Module 5: Quality requirements (EXIT_CODE, gate)     — 20 points

Pass threshold: ≥80/100 (matching CPOO standard).
Scores below 60 trigger automatic prompt regeneration.

Usage:
    scorer = CPOOScorer(call_llm_fn=my_llm)
    result = scorer.score(prompt_text)
    if result.total < 80:
        prompt = scorer.optimize(prompt_text, role_goal, criteria)
"""

import re
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class ModuleScore:
    name: str
    score: int       # 0-20
    max_score: int = 20
    findings: List[str] = field(default_factory=list)
    suggestions: List[str] = field(default_factory=list)


@dataclass
class CPOOResult:
    modules: List[ModuleScore]
    total: int          # 0-100
    passed: bool        # >= 80
    needs_regen: bool   # < 60
    summary: str = ""


# ── Pattern-based scoring (no LLM required) ──

MODULE_CHECKS = [
    # Module 1: Role
    {
        "name": "Role",
        "patterns": [
            (r"(you are|你是|作为).{5,80}(engineer|developer|设计师|工程师|开发者|专家)", 8,
             "Clear role identity with title"),
            (r"(your (role|job|task)|你的(角色|任务)).{5,120}", 6,
             "Explicit role description"),
            (r"(why|目标|goal|purpose|目的)", 6,
             "Explains why this role matters"),
        ],
    },
    # Module 2: Constraints
    {
        "name": "Constraints",
        "patterns": [
            (r"(must|必须|hard.constraint|强制|MUST|🔴)", 7,
             "Hard constraints explicitly marked"),
            (r"(should|建议|soft.constraint|SHOULD|🟡)", 5,
             "Soft constraints with guidance"),
            (r"(EXIT_CODE|exit.code.fingerprint|exit_fingerprint)", 8,
             "EXIT_CODE fingerprint constraint"),
        ],
    },
    # Module 3: Workflow
    {
        "name": "Workflow",
        "patterns": [
            (r"(step|步骤)\s*\d", 8,
             "Numbered step-by-step workflow"),
            (r"(input|输入|receive|接收).{5,60}(output|输出|produce|产出)", 6,
             "Clear input→process→output chain"),
            (r"(verif|验证|check|检查).{5,60}(EXIT|exit)", 6,
             "Verification step with EXIT_CODE"),
        ],
    },
    # Module 4: IO Format
    {
        "name": "IO Format",
        "patterns": [
            (r"(input.format|输入格式|input.*format|接收).{5,120}", 7,
             "Input format specified"),
            (r"(output.format|输出格式|output.*format|产出).{5,120}", 8,
             "Output format explicitly defined"),
            (r"(file.*path|文件名|output_file|产出文件)", 5,
             "Output file path specified"),
        ],
    },
    # Module 5: Quality
    {
        "name": "Quality",
        "patterns": [
            (r"(quality.gate|质量门|acceptance.criteria|验收标准)", 7,
             "Quality gate or acceptance criteria referenced"),
            (r"(self.check|自查|self.verify|validate|验证)", 5,
             "Self-verification step"),
            (r"(complete|完整|fully|齐全|no.placeholder|no.TODO)", 8,
             "Completeness requirement (no TODOs/placeholders)"),
        ],
    },
]


class CPOOScorer:
    """Score prompts against the CPOO 5-module standard.

    Supports two modes:
      - Pattern-based (fast, offline, no LLM needed): uses regex patterns
      - LLM-enhanced (when call_llm_fn provided): deeper semantic analysis
    """

    def __init__(self, call_llm_fn=None, threshold=80, regen_threshold=60):
        # type: (callable, int, int) -> None
        self.call_llm_fn = call_llm_fn
        self.threshold = threshold
        self.regen_threshold = regen_threshold

    def score(self, prompt_text):
        # type: (str) -> CPOOResult
        """Score a prompt against the CPOO 5-module standard.

        Returns CPOOResult with per-module scores and total.
        """
        modules = []
        for mc in MODULE_CHECKS:
            module_score = 0
            findings = []
            suggestions = []
            for pattern, points, label in mc["patterns"]:
                if re.search(pattern, prompt_text, re.I):
                    module_score += points
                    findings.append("OK: {}".format(label))
                else:
                    suggestions.append("Missing: {}".format(label))

            # Cap at 20
            module_score = min(module_score, 20)
            modules.append(ModuleScore(
                name=mc["name"],
                score=module_score,
                max_score=20,
                findings=findings,
                suggestions=suggestions,
            ))

        total = sum(m.score for m in modules)
        return CPOOResult(
            modules=modules,
            total=total,
            passed=total >= self.threshold,
            needs_regen=total < self.regen_threshold,
            summary=self._build_summary(modules, total),
        )

    def optimize(self, prompt_text, role_goal="", acceptance_criteria=None):
        # type: (str, str, list) -> str
        """Attempt to auto-optimize a prompt that scored below threshold.

        If call_llm_fn is available, uses LLM to rewrite. Otherwise,
        appends missing module sections as best-effort fixes.
        """
        result = self.score(prompt_text)
        if result.passed:
            return prompt_text  # Already good

        if self.call_llm_fn:
            return self._llm_optimize(prompt_text, result, role_goal, acceptance_criteria)

        # Pattern-based fix: append missing sections
        return self._pattern_fix(prompt_text, result)

    def _build_summary(self, modules, total):
        # type: (list, int) -> str
        parts = []
        for m in modules:
            bar = "#" * (m.score // 4) + "-" * (5 - m.score // 4)
            parts.append("{} [{}] {}/20".format(m.name, bar, m.score))
        parts.append("TOTAL: {}/100 {}".format(
            total,
            "PASS" if total >= self.threshold else
            "REGEN" if total < self.regen_threshold else "BELOW")
        )
        return "\n".join(parts)

    def _pattern_fix(self, prompt_text, result):
        # type: (str, CPOOResult) -> str
        """Best-effort fix: append missing module content."""
        missing = []
        for m in result.modules:
            if m.score < 12:  # Below 60% for this module
                missing.append(m.name)

        fixes = []
        if "Role" in missing:
            fixes.append("## Role\nYou are a capable agent. Your task is clearly defined above.")
        if "Constraints" in missing:
            fixes.append(
                "## Constraints\n"
                "- 🔴 MUST: Produce complete output with no placeholders\n"
                "- 🔴 MUST: All Bash verification output must carry EXIT_CODE fingerprint\n"
                "- 🟡 SHOULD: Follow best practices for the given scenario"
            )
        if "Workflow" in missing:
            fixes.append(
                "## Workflow\n"
                "1. Read input context carefully\n"
                "2. Analyze requirements and plan your approach\n"
                "3. Produce complete deliverable — no placeholders\n"
                "4. Self-verify: run verification and record EXIT_CODE"
            )
        if "IO Format" in missing:
            fixes.append(
                "## IO Format\n"
                "**Input:** Upstream agent context\n"
                "**Output:** Write to specified output file"
            )
        if "Quality" in missing:
            fixes.append(
                "## Quality Gate Note\n"
                "Your output will be automatically verified for:\n"
                "- File existence and non-emptiness\n"
                "- EXIT_CODE fingerprint in all bash output\n"
                "- Compliance with acceptance criteria"
            )

        return prompt_text + "\n\n" + "\n\n".join(fixes)

    def _llm_optimize(self, prompt_text, result, role_goal, criteria):
        # type: (str, CPOOResult, str, list) -> str
        """Use LLM to rewrite the prompt for better CPOO compliance."""
        system = (
            "You are a Prompt Optimization expert (CPOO). "
            "Given a prompt that scored {}/100 on the CPOO 5-module standard, "
            "rewrite it to achieve >= 80/100.\n\n"
            "5 modules: Role(20), Constraints(20), Workflow(20), "
            "IO Format(20), Quality(20).\n"
            "Focus on fixing these weaknesses:\n{}"
        ).format(result.total, "\n".join(
            "- Module {}: missing {} elements".format(m.name, m.suggestions)
            for m in result.modules if m.score < 15
        ))
        user = (
            "Role goal: {}\n"
            "Acceptance criteria: {}\n\n"
            "Original prompt:\n---\n{}\n---\n\n"
            "Output ONLY the rewritten prompt — no explanations."
        ).format(
            role_goal,
            ", ".join(criteria or []),
            prompt_text,
        )
        try:
            return self.call_llm_fn(system, user)
        except Exception:
            return self._pattern_fix(prompt_text, result)


# ── Quick scoring API ──

def quick_score(prompt_text):
    # type: (str) -> CPOOResult
    """Score a prompt without needing to instantiate CPOOScorer."""
    return CPOOScorer().score(prompt_text)


def quick_check(prompt_text, threshold=80):
    # type: (str, int) -> bool
    """Check if a prompt passes CPOO threshold."""
    return CPOOScorer(threshold=threshold).score(prompt_text).passed
