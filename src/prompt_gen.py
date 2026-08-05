"""Prompt Generator — auto-generate agent prompts from role descriptions.

Based on CPOO (首席提示词优化官) five-module standard:
  Module 1: Role definition (who/what/why)
  Module 2: Constraints (hard constraints with 🔴, soft with 🟡)
  Module 3: Workflow (numbered steps, input→process→output)
  Module 4: IO Format (exactly what output looks like)
  Module 5: Quality requirements (EXIT_CODE, verification, gate compliance)

Human writes: role_name + role_goal + output_description
System generates: full structured prompt meeting CPOO ≥80 standard
"""

PROMPT_GEN_SYSTEM = """You are a Prompt Engineer following the CPOO v4.4.0 standard.

Given a role description, generate a complete agent prompt with 5 modules:

## Module 1: Role
- Start with "You are a [role_name]."
- State clearly what this agent does and why it matters.

## Module 2: Constraints
- 🔴 Hard constraints: things the agent MUST do (output format, verification rules)
- 🟡 Soft constraints: things the agent SHOULD do (best practices, style guides)
- Must include: "All Bash verification output MUST end with EXIT_CODE fingerprint"

## Module 3: Workflow
- Numbered steps: 1. Receive input → 2. Process → 3. Produce output
- Include: "Step N: Run verification command and record EXIT_CODE"
- Be specific about what the agent does at each step

## Module 4: IO Format
- Input: what the agent receives (upstream context, file paths)
- Output: exact format specification
- Include placeholder {context} for upstream agent output

## Module 5: Quality Gate Compliance
- Agent must produce a deliverable file (Part A)
- Agent output will be Bash-verified — write output accordingly
- After producing output, the system automatically checks: file existence, EXIT fingerprint"""


PROMPT_GEN_USER = """Generate a complete agent prompt for:

Role Name: {role_name}
Role Goal: {role_goal}
Scenario: {scenario_type}
Output File: {output_file}
Acceptance Criteria: {criteria}

The prompt must be self-contained so the agent can work independently.
Output ONLY the prompt — no explanations or markdown wrappers."""


def generate_agent_prompt(role_name, role_goal, scenario_type="general",
                          output_file="output.md", criteria=None,
                          call_llm_fn=None):
    """Generate a complete agent prompt from role description.

    Args:
        role_name: Human-readable role name (e.g., "Backend Developer")
        role_goal: 1-2 sentence description of what this agent does
        scenario_type: "code_gen" | "content_writing" | "data_analysis" | "devops"
        output_file: Filename the agent should write to
        criteria: List of acceptance criteria (natural language)
        call_llm_fn: Function(system_prompt, user_prompt) -> str

    Returns:
        Complete prompt string meeting CPOO 5-module standard
    """
    criteria_text = "\n".join("- {}".format(c) for c in (criteria or [])) or "None specified"

    user_prompt = PROMPT_GEN_USER.format(
        role_name=role_name,
        role_goal=role_goal,
        scenario_type=scenario_type,
        output_file=output_file,
        criteria=criteria_text,
    )

    if call_llm_fn:
        return call_llm_fn(PROMPT_GEN_SYSTEM, user_prompt)

    # Fallback: template-based generation without LLM
    return _template_generate(role_name, role_goal, scenario_type, output_file, criteria_text)


def _template_generate(role_name, role_goal, scenario_type, output_file, criteria_text):
    """Template-based prompt generation when LLM is unavailable."""
    return """## Role
You are a {role_name}. {role_goal}

## Constraints
- Produce exactly ONE output file: {output_file}
- Follow the specified output format exactly
- Do not skip any required sections
- All Bash verification output will carry EXIT_CODE fingerprint

## Workflow
1. Read the input context from the upstream agent
2. Analyze the requirements and plan your output
3. Produce the complete deliverable — no placeholders, no "TODO"
4. Self-check: does the output meet ALL acceptance criteria?

## IO Format
**Input:** Upstream agent context (see below)
**Output:** {output_file}

## Acceptance Criteria
{criteria}

## Quality Gate Note
Your output will be automatically verified. Ensure:
- The output file is complete and well-formed
- All acceptance criteria are addressed
- The file path matches exactly: {output_file}
""".format(
        role_name=role_name,
        role_goal=role_goal,
        output_file=output_file,
        criteria=criteria_text,
    )
