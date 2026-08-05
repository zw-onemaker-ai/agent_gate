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
    }
}


def human_gate_prompt(fail_reasons, verification_output):
    """Build a structured prompt for human intervention."""
    lines = [
        "",
        "=" * 50,
        "  [HUMAN GATE] Automated classification failed",
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
    for i, (label, target) in enumerate(HUMAN_CHECKLIST["crash"]["options"], 1):
        lines.append("  [{}] {} → loopback to {}".format(i, label, target.value))
    lines.append("")
    return "\n".join(lines)


def human_gate_interactive(fail_reasons, verification_output):
    """Interactive human gate (CLI). Returns selected LoopbackTarget."""
    print(human_gate_prompt(fail_reasons, verification_output))
    options = HUMAN_CHECKLIST["crash"]["options"]

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
