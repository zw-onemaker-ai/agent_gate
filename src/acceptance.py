"""Acceptance Criteria → Verify Command auto-generation.

Key design:
  Human writes acceptance criteria in natural language (R1).
  LLM translates them into executable bash verification commands.
  AgentGate runs the commands — no human writes raw bash.
"""


ACCEPTANCE_PROMPT_TEMPLATE = """You are a QA engineer. Given a list of acceptance criteria and 
the names of output files produced by an agent, generate a SINGLE bash verification command.

Rules:
- Use standard Unix tools: curl, grep, wc, test, awk, python3, jq, openssl, etc.
- The command must exit with code 0 if ALL criteria pass, non-zero if ANY fail.
- Chain checks with && so it fails fast.
- Output should ONLY be the bash command — no explanations, no markdown.
- Use {output_dir} as the base path for files.

Acceptance criteria:
{criteria}

Output files:
{files}

Bash verification command:"""


def build_acceptance_prompt(criteria, files, output_dir="./output"):
    """Build the prompt for generating verify_cmd from acceptance criteria."""
    criteria_text = "\n".join("- {}".format(c) for c in criteria)
    files_text = "\n".join("- {}".format(f) for f in files)
    return ACCEPTANCE_PROMPT_TEMPLATE.format(
        criteria=criteria_text,
        files=files_text,
        output_dir=output_dir,
    )


def generate_verify_cmd(criteria, files, call_llm_fn, output_dir="./output"):
    """Generate bash verification command from acceptance criteria.
    
    Args:
        criteria: List of natural language acceptance criteria
        files: List of output file paths
        call_llm_fn: Function(system_prompt, user_prompt) -> str (the LLM)
        output_dir: Base directory for file paths
    
    Returns:
        Bash command string that exits 0 if all criteria pass
    """
    if not criteria:
        return "echo 'No acceptance criteria — skipping verification'"

    prompt = build_acceptance_prompt(criteria, files, output_dir)
    verify_cmd = call_llm_fn(
        system_prompt="You generate bash verification commands. Output ONLY the command.",
        user_prompt=prompt,
    )
    # Strip markdown code fences if LLM wraps it
    verify_cmd = verify_cmd.strip()
    if verify_cmd.startswith("```"):
        verify_cmd = verify_cmd.split("\n", 1)[-1]
        if verify_cmd.endswith("```"):
            verify_cmd = verify_cmd[:-3]
    return verify_cmd.strip()
