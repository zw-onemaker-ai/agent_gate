#!/usr/bin/env python3
"""AgentGate 3-Role Demo: Requirements → Code → Review with Quality Gates.

Demonstrates the core protocol:
  1. Anti-hallucination: every agent output is Bash-verified
  2. EXIT_CODE fingerprint: all verification carries unforgeable EXIT:? mark
  3. Oriented loopback: failures route to the correct upstream agent
  4. Context cards: compact summaries prevent context bloat

Usage:
  # With Ollama (local):
  python3 examples/demo_3role.py --provider ollama --model qwen2.5:7b

  # With OpenAI:
  python3 examples/demo_3role.py --provider openai --model gpt-4o-mini

  # With LiteLLM (any provider):
  python3 examples/demo_3role.py --provider litellm --model "openai/gpt-4o-mini"

  # Dry-run (no LLM, uses mock):
  python3 examples/demo_3role.py --mock
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.engine import AgentGate

# ── Agent Prompt Templates ──

R1_PROMPT = """You are a Requirements Analyst. Given a user's product idea, produce a clear requirements document.

Output format:
# Requirements Document

## Product Idea
[restate the idea]

## User Stories (at least 3)
- As a [user], I want [action] so that [value]

## Acceptance Criteria (numbered, testable)
1. [criterion]

## MVP Scope
[minimum features for first release]

## Scenario Type: [public | internal | poc | personal]
"""

R2_PROMPT = """You are a Senior Backend Developer. Given requirements, produce a working Python FastAPI application.

Output ONLY valid Python code. The file must be runnable with: python3 main.py

Requirements:
- FastAPI app with at least 1 endpoint
- Health check endpoint at /health
- Request/response models using pydantic
- Proper error handling
- Include: if __name__ == "__main__": uvicorn.run(app)

Input requirements:
{context}
"""

R3_PROMPT = """You are a Code Reviewer. Review the provided code for bugs, security issues, and style problems.

Output format:
# Code Review Report

## Summary
[1-2 sentences]

## Issues Found
- [severity] [file:line] Description

## Security Check
- [OWASP / injection / auth]

## Verdict: [PASS / FAIL with fixes needed]

## Fixed Code
[If fixes needed, provide corrected version]

Code to review:
{context}
"""


def run_demo(provider="ollama", model="qwen2.5:7b", mock=False):
    print("╔══════════════════════════════════════════════╗")
    print("║  AgentGate — 3-Role Quality-Gated Pipeline  ║")
    print("║  Requirements → Backend Code → Code Review  ║")
    print("╚══════════════════════════════════════════════╝")

    gate = AgentGate(
        project_name="demo_todo_api",
        output_dir="./output/demo_todo_api",
        max_iterations=3,
        model_provider=provider,
        model_name=model,
    )

    # Register agents in pipeline order
    gate.register_agent(
        role="R1",
        name="Requirements Analyst",
        prompt_template=R1_PROMPT,
        output_file="requirements.md",
        verify_cmd="ls -la output/demo_todo_api/requirements.md && [ -s output/demo_todo_api/requirements.md ] && echo 'File OK'",
    )

    gate.register_agent(
        role="R2",
        name="Backend Developer",
        prompt_template=R2_PROMPT,
        output_file="main.py",
        verify_cmd="python3 -m py_compile output/demo_todo_api/main.py 2>&1 && echo 'Syntax OK'",
    )

    gate.register_agent(
        role="R3",
        name="Code Reviewer",
        prompt_template=R3_PROMPT,
        output_file="review_report.md",
        verify_cmd="ls -la output/demo_todo_api/review_report.md && grep -qi 'verdict' output/demo_todo_api/review_report.md && echo 'Review OK'",
    )

    initial_idea = "Build a simple Todo List REST API with create, list, complete, and delete endpoints."

    if mock:
        print("\n[MOCK MODE] Simulating agent outputs without LLM...\n")
        return _run_mock(gate, initial_idea)

    state = gate.run_pipeline(initial_context=initial_idea)
    print("\n" + gate.summary())
    return state


def _run_mock(gate, idea):
    """Mock pipeline for dry-run testing without LLM."""
    import time

    # Simulated outputs
    outputs = {
        "R1": """# Requirements Document
## Product Idea
Simple Todo List REST API

## User Stories
- As a user, I want to create a todo item so that I can track tasks
- As a user, I want to list all todos so that I can see what's pending
- As a user, I want to mark a todo as complete so that I can track progress
- As a user, I want to delete a todo so that I can remove finished items

## Acceptance Criteria
1. POST /todos creates a new todo with title and returns it with an id
2. GET /todos returns a list of all todos
3. PATCH /todos/{id}/complete marks a todo as done
4. DELETE /todos/{id} removes a todo
5. GET /health returns {"status": "ok"}

## MVP Scope
- In-memory storage (no database)
- FastAPI with 5 endpoints
- JSON request/response

## Scenario Type: personal""",

        "R2": '''"""Todo List REST API — FastAPI"""
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Optional
import uvicorn

app = FastAPI(title="Todo API", version="0.1.0")

todos: dict[int, dict] = {}
next_id = 1

class TodoCreate(BaseModel):
    title: str

class TodoResponse(BaseModel):
    id: int
    title: str
    completed: bool = False

@app.get("/health")
def health():
    return {"status": "ok"}

@app.post("/todos", response_model=TodoResponse)
def create_todo(item: TodoCreate):
    global next_id
    todo = {"id": next_id, "title": item.title, "completed": False}
    todos[next_id] = todo
    next_id += 1
    return todo

@app.get("/todos", response_model=list[TodoResponse])
def list_todos():
    return [TodoResponse(**t) for t in todos.values()]

@app.patch("/todos/{todo_id}/complete", response_model=TodoResponse)
def complete_todo(todo_id: int):
    if todo_id not in todos:
        raise HTTPException(status_code=404, detail="Todo not found")
    todos[todo_id]["completed"] = True
    return TodoResponse(**todos[todo_id])

@app.delete("/todos/{todo_id}")
def delete_todo(todo_id: int):
    if todo_id not in todos:
        raise HTTPException(status_code=404, detail="Todo not found")
    del todos[todo_id]
    return {"status": "deleted"}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
''',

        "R3": """# Code Review Report

## Summary
Todo API is well-structured FastAPI app with proper endpoints. One issue found: missing input validation on title field.

## Issues Found
- [MEDIUM] main.py:20 — TodoCreate.title has no min_length constraint. Empty titles allowed.
- [LOW] main.py:38 — No pagination on list endpoint, could OOM with many items.

## Security Check
- ✅ No SQL injection (in-memory storage)
- ✅ No XSS vectors (API-only, no HTML rendering)
- ✅ CORS not configured (acceptable for personal tool)

## Verdict: CONDITIONAL PASS
Minor issues, none blocking for MVP. Add min_length=1 to title field before production use.

## Fixed Code
In TodoCreate: `title: str = Field(..., min_length=1)`"""
    }

    for role in ["R1", "R2", "R3"]:
        agent = gate._agents[role]
        output_file = gate.output_dir / agent["output_file"]
        output_file.parent.mkdir(parents=True, exist_ok=True)
        output_file.write_text(outputs[role])

        verify_cmd = agent.get("verify_cmd", "")
        if verify_cmd:
            from src.validators import run_bash
            verify_output, _ = run_bash(verify_cmd)

        from src.validators import quality_gate_check
        gate_result = quality_gate_check(
            role=role,
            part_a_files=[str(output_file)],
            verification_output=verify_output if verify_cmd else "EXIT:0",
        )

        from src.models import AgentOutput, ContextPackage
        agent_output = AgentOutput(
            role=role, role_name=agent["name"],
            part_a_files=[str(output_file)],
            quality_gate=gate_result.status,
            exit_fingerprint=gate_result.exit_fingerprint,
        )
        gate._gate_history.append(gate_result)
        gate._print_gate_report(gate_result)

        pkg = ContextPackage(
            package_id=f"ctx-{role}-mock",
            source_role=role,
            target_roles=[],
            agent_output=agent_output,
            gate_result=gate_result,
        )
        gate.state.steps.append(pkg)

        if gate_result.status.value == "FAIL":
            print(f"  [LOOPBACK] → {gate_result.loopback_target.value}")
            if gate.state.loopback_count >= gate.state.max_iterations:
                break
            gate.state.loopback_count += 1
            continue

    print(f"\n{'='*50}")
    print(f"  ✅ MOCK PIPELINE COMPLETE")
    print(f"  Files produced:")
    for f in sorted(gate.output_dir.rglob("*")):
        if f.is_file():
            print(f"    {f.relative_to(gate.output_dir)}")
    print(f"{'='*50}")
    return gate.state


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="AgentGate 3-Role Demo")
    p.add_argument("--provider", default="ollama", help="LLM provider (ollama, openai, litellm)")
    p.add_argument("--model", default="qwen2.5:7b", help="Model name")
    p.add_argument("--mock", action="store_true", help="Dry-run with mock outputs")
    args = p.parse_args()
    run_demo(provider=args.provider, model=args.model, mock=args.mock)
