"""HumanGate — structured human intervention point.

When the automated gate cannot determine a loopback target,
escalate to human with a focused checklist — not a blank prompt.
"""

import sys
from .models import LoopbackTarget

HUMAN_CHECKLIST = {
    "crash": {
        "question": "What caused this failure?",
        "options": [
            ("Backend code bug/crash", LoopbackTarget.BACKEND),
            ("Security vulnerability", LoopbackTarget.SECURITY),
            ("Frontend/UI issue", LoopbackTarget.FRONTEND),
            ("Architecture/design flaw", LoopbackTarget.DESIGN),
            ("Requirements unclear or wrong", LoopbackTarget.REQUIREMENTS),
            ("Skip — false alarm, let it pass", LoopbackTarget.NONE),
        ],
    },
    "content_quality": {
        "question": "Output quality is below threshold — what's the root cause?",
        "options": [
            ("Requirements too vague → re-scope", LoopbackTarget.REQUIREMENTS),
            ("Architecture doesn't support the output → redesign", LoopbackTarget.DESIGN),
            ("Backend implementation flawed → redo", LoopbackTarget.BACKEND),
            ("Frontend rendering wrong → redo", LoopbackTarget.FRONTEND),
            ("Skip — acceptable for now", LoopbackTarget.NONE),
        ],
    },
    "design_decision": {
        "question": "A design decision needs human judgment — which direction?",
        "options": [
            ("Go with backend-first approach", LoopbackTarget.BACKEND),
            ("Go with design-first approach", LoopbackTarget.DESIGN),
            ("Re-clarify requirements first", LoopbackTarget.REQUIREMENTS),
            ("Both approaches needed (parallel)", LoopbackTarget.NONE),
        ],
    },
    "requirements_gap": {
        "question": "Requirements are incomplete or conflicting — what to do?",
        "options": [
            ("Re-write requirements from scratch", LoopbackTarget.REQUIREMENTS),
            ("Design a flexible architecture to accommodate ambiguity", LoopbackTarget.DESIGN),
            ("Build a prototype to discover requirements", LoopbackTarget.BACKEND),
            ("Skip — proceed with best-guess", LoopbackTarget.NONE),
        ],
    },
    "security_alert": {
        "question": "Security issue detected — how severe?",
        "options": [
            ("Critical — fix immediately, full audit", LoopbackTarget.SECURITY),
            ("High — fix and re-verify", LoopbackTarget.SECURITY),
            ("Medium — proceed with warnings, fix later", LoopbackTarget.NONE),
            ("False positive — dismiss", LoopbackTarget.NONE),
        ],
    },
    "contract_broken": {
        "question": "Agent contract claims don't match actual output — what to do?",
        "options": [
            ("Re-run the agent that broke the contract", LoopbackTarget.BACKEND),
            ("The contract itself was wrong → redesign", LoopbackTarget.DESIGN),
            ("Requirements didn't specify this clearly", LoopbackTarget.REQUIREMENTS),
            ("Accept the discrepancy — update contract", LoopbackTarget.NONE),
        ],
    },
}

# Category matching: fail reason keywords → checklist category
FAIL_CATEGORY_MAP = [
    (r"(quality|judge|score|evaluation|not\s*good\s*enough|below\s*threshold)", "content_quality"),
    (r"(design\s*decision|architecture\s*choice|trade.off|which\s*approach)", "design_decision"),
    (r"(missing\s*requirement|unclear|ambiguous|conflicting|gap\b|incomplete)", "requirements_gap"),
    (r"(security|cve|vulnerability|exploit|attack|injection|xss\b|csrf\b)", "security_alert"),
    (r"(contract.*(?:broken|mismatch|claim|verify)|output.*(?:missing|empty|wrong))", "contract_broken"),
]


def _detect_category(fail_reasons):
    # type: (list) -> str
    """Auto-detect the most relevant HumanGate checklist category from fail reasons."""
    import re
    combined = " ".join(fail_reasons)
    for pattern, category in FAIL_CATEGORY_MAP:
        if re.search(pattern, combined, re.I):
            return category
    return "crash"  # default fallback


def _get_checklist(category):
    # type: (str) -> dict
    """Get the checklist dict for a category, falling back to 'crash'."""
    return HUMAN_CHECKLIST.get(category, HUMAN_CHECKLIST["crash"])


def human_gate_prompt(fail_reasons, verification_output):
    """Build a structured prompt for human intervention."""
    import re as _re
    category = _detect_category(fail_reasons)
    checklist = _get_checklist(category)

    lines = [
        "",
        "=" * 50,
        "  [HUMAN GATE] Automated classification failed",
        "  Category: {}".format(category.upper().replace("_", " ")),
        "  Question: {}".format(checklist["question"]),
        "=" * 50,
        "",
        "Failure reasons:",
    ]
    for r in fail_reasons:
        lines.append("  - {}".format(r))
    lines.append("")
    lines.append("Verification output (last 500 chars):")
    lines.append("  {}".format(verification_output[-500:]))
    lines.append("")
    lines.append("Select loopback target:")
    for i, (label, target) in enumerate(checklist["options"], 1):
        lines.append("  [{}] {} → loopback to {}".format(i, label, target.value))
    lines.append("")
    return "\n".join(lines)


def human_gate_interactive(fail_reasons, verification_output):
    """Interactive human gate (CLI). Returns selected LoopbackTarget."""
    category = _detect_category(fail_reasons)
    checklist = _get_checklist(category)

    print(human_gate_prompt(fail_reasons, verification_output))
    options = checklist["options"]

    try:
        choice = input("  Choice [1-{}]: ".format(len(options))).strip()
        idx = int(choice) - 1
        if 0 <= idx < len(options):
            return options[idx][1]
    except (ValueError, KeyboardInterrupt, EOFError):
        pass

    # Default: escalate to requirements
    print("  [DEFAULT] No valid choice → loopback to REQUIREMENTS")
    return LoopbackTarget.REQUIREMENTS
