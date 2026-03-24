"""LangGraph orchestrator — Architect -> Lead Dev -> QA loop.

This is the main entry point for the agent workflow.
Implements the state machine with retry logic, token budgets,
structured Blueprint passing, and human escalation.
"""

import json
import os
from enum import Enum
from typing import Literal

from dotenv import load_dotenv
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel

from .agents.architect import Blueprint
from .agents.qa import FailureReport
from .memory.chroma_store import ChromaMemoryStore
from .tracing import add_trace_event, create_trace_config

load_dotenv()


# -- Configuration --

MAX_RETRIES = int(os.getenv("MAX_RETRIES", "3"))
TOKEN_BUDGET = int(os.getenv("TOKEN_BUDGET", "50000"))


# -- Workflow State --

class WorkflowStatus(str, Enum):
    PLANNING = "planning"
    BUILDING = "building"
    REVIEWING = "reviewing"
    PASSED = "passed"
    FAILED = "failed"
    ESCALATED = "escalated"


class AgentState(BaseModel):
    """State that flows through the LangGraph state machine."""

    # Task input
    task_description: str = ""

    # Architect output
    blueprint: Blueprint | None = None

    # Lead Dev output
    generated_code: str = ""

    # QA output
    failure_report: FailureReport | None = None

    # Workflow control
    status: WorkflowStatus = WorkflowStatus.PLANNING
    retry_count: int = 0
    tokens_used: int = 0
    error_message: str = ""

    # Memory context
    memory_context: list[str] = []

    # Conversation trace (for observability)
    trace: list[str] = []


# -- LLM Initialization --

def _get_architect_llm():
    """Gemini for the Architect agent (large context, planning only)."""
    return ChatGoogleGenerativeAI(
        model="gemini-2.0-flash",
        google_api_key=os.getenv("GOOGLE_API_KEY"),
        temperature=0.2,
    )


def _get_developer_llm():
    """Claude for the Lead Dev agent (code execution)."""
    return ChatAnthropic(
        model="claude-sonnet-4-20250514",
        api_key=os.getenv("ANTHROPIC_API_KEY"),
        temperature=0.1,
        max_tokens=8192,
    )


def _get_qa_llm():
    """Claude for the QA agent (review and testing)."""
    return ChatAnthropic(
        model="claude-sonnet-4-20250514",
        api_key=os.getenv("ANTHROPIC_API_KEY"),
        temperature=0.0,
        max_tokens=4096,
    )


# -- Memory Helper --

def _fetch_memory_context(task_description: str) -> list[str]:
    """Query Chroma for relevant context across all tiers."""
    try:
        store = ChromaMemoryStore()
        results = store.query(task_description, n_results=10)
        return [r["content"] for r in results]
    except Exception:
        return []


# -- Node Functions --

def architect_node(state: AgentState) -> dict:
    """Architect: generates a structured Blueprint from the task description.

    Queries memory for context, then uses Gemini to plan the task.
    Never writes code -- only produces a Blueprint JSON.
    """
    trace = list(state.trace)
    trace.append("architect: starting planning")

    # Fetch memory context
    memory_context = _fetch_memory_context(state.task_description)

    memory_block = ""
    if memory_context:
        memory_block = "\n\nProject context from memory:\n" + "\n".join(f"- {c}" for c in memory_context)

    system_prompt = f"""You are the Architect agent. Your job is to create a structured Blueprint
for a coding task. You NEVER write code yourself.

Respond with ONLY a valid JSON object matching this schema:
{{
  "task_id": "string (short unique identifier)",
  "target_files": ["list of file paths to create or modify"],
  "instructions": "clear step-by-step instructions for the developer",
  "constraints": ["list of constraints or requirements"],
  "acceptance_criteria": ["list of testable criteria for QA"]
}}

Do not include any text before or after the JSON.{memory_block}"""

    # Include failure context if this is a re-plan after escalation
    user_msg = state.task_description
    if state.failure_report and state.failure_report.is_architectural:
        user_msg += f"\n\nPREVIOUS ATTEMPT FAILED (architectural issue):\n"
        user_msg += f"Errors: {', '.join(state.failure_report.errors)}\n"
        user_msg += f"Recommendation: {state.failure_report.recommendation}"

    llm = _get_architect_llm()
    response = llm.invoke([
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_msg),
    ])

    # Parse the Blueprint from response
    try:
        raw = response.content.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.strip()
        blueprint_data = json.loads(raw)
        blueprint = Blueprint(**blueprint_data)
    except (json.JSONDecodeError, Exception) as e:
        trace.append(f"architect: failed to parse blueprint: {e}")
        return {
            "status": WorkflowStatus.FAILED,
            "error_message": f"Architect failed to produce valid Blueprint: {e}",
            "trace": trace,
            "memory_context": memory_context,
        }

    trace.append(f"architect: blueprint created for {len(blueprint.target_files)} files")
    tokens_used = state.tokens_used + (response.usage_metadata.get("total_tokens", 0) if response.usage_metadata else 0)

    return {
        "blueprint": blueprint,
        "status": WorkflowStatus.BUILDING,
        "tokens_used": tokens_used,
        "trace": trace,
        "memory_context": memory_context,
    }


def developer_node(state: AgentState) -> dict:
    """Lead Dev: executes the Blueprint and generates code.

    Receives a structured Blueprint JSON and writes code accordingly.
    """
    trace = list(state.trace)
    trace.append("developer: starting build")

    if not state.blueprint:
        trace.append("developer: no blueprint provided")
        return {
            "status": WorkflowStatus.FAILED,
            "error_message": "Developer received no Blueprint",
            "trace": trace,
        }

    system_prompt = """You are the Lead Dev agent. You receive a structured Blueprint
and write the code to implement it.

Respond with the complete code implementation. Include file paths as comments
at the top of each file section, like:
# --- FILE: path/to/file.py ---

Follow the Blueprint exactly. Respect all constraints.
Write clean, well-documented code."""

    user_msg = f"Blueprint:\n{state.blueprint.model_dump_json(indent=2)}"

    # Include failure report context if retrying
    if state.failure_report and not state.failure_report.is_architectural:
        user_msg += f"\n\nPREVIOUS ATTEMPT FAILED:\n"
        user_msg += f"Tests passed: {state.failure_report.tests_passed}\n"
        user_msg += f"Tests failed: {state.failure_report.tests_failed}\n"
        user_msg += f"Errors: {', '.join(state.failure_report.errors)}\n"
        user_msg += f"Failed files: {', '.join(state.failure_report.failed_files)}\n"
        user_msg += f"Recommendation: {state.failure_report.recommendation}\n"
        user_msg += "\nFix the issues and regenerate the code."

    llm = _get_developer_llm()
    response = llm.invoke([
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_msg),
    ])

    trace.append(f"developer: code generated ({len(response.content)} chars)")
    tokens_used = state.tokens_used + (response.usage_metadata.get("total_tokens", 0) if response.usage_metadata else 0)

    return {
        "generated_code": response.content,
        "status": WorkflowStatus.REVIEWING,
        "tokens_used": tokens_used,
        "trace": trace,
    }


def qa_node(state: AgentState) -> dict:
    """QA: reviews the generated code and produces a structured FailureReport.

    Checks the code against the Blueprint's acceptance criteria.
    Returns pass/fail/escalate decision.
    """
    trace = list(state.trace)
    trace.append("qa: starting review")

    if not state.generated_code or not state.blueprint:
        trace.append("qa: missing code or blueprint")
        return {
            "status": WorkflowStatus.FAILED,
            "error_message": "QA received no code or blueprint to review",
            "trace": trace,
        }

    system_prompt = """You are the QA agent. You review code against a Blueprint's acceptance criteria.

Respond with ONLY a valid JSON object matching this schema:
{
  "task_id": "string (from the Blueprint)",
  "status": "pass" or "fail" or "escalate",
  "tests_passed": number,
  "tests_failed": number,
  "errors": ["list of specific error descriptions"],
  "failed_files": ["list of files with issues"],
  "is_architectural": true/false (set true if the failure is a design/planning issue),
  "recommendation": "what to fix or why it should escalate"
}

\"escalate\" means the Blueprint itself is wrong, not just the implementation.
Be strict but fair. Only pass code that meets ALL acceptance criteria.
Do not include any text before or after the JSON."""

    user_msg = f"Blueprint:\n{state.blueprint.model_dump_json(indent=2)}\n\nGenerated Code:\n{state.generated_code}"

    llm = _get_qa_llm()
    response = llm.invoke([
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_msg),
    ])

    # Parse the FailureReport
    try:
        raw = response.content.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.strip()
        report_data = json.loads(raw)
        failure_report = FailureReport(**report_data)
    except (json.JSONDecodeError, Exception) as e:
        trace.append(f"qa: failed to parse report: {e}")
        return {
            "status": WorkflowStatus.FAILED,
            "error_message": f"QA failed to produce valid report: {e}",
            "trace": trace,
        }

    trace.append(f"qa: verdict={failure_report.status}, passed={failure_report.tests_passed}, failed={failure_report.tests_failed}")
    tokens_used = state.tokens_used + (response.usage_metadata.get("total_tokens", 0) if response.usage_metadata else 0)

    if failure_report.status == "pass":
        status = WorkflowStatus.PASSED
    elif failure_report.is_architectural:
        status = WorkflowStatus.ESCALATED
    else:
        status = WorkflowStatus.REVIEWING

    return {
        "failure_report": failure_report,
        "status": status,
        "tokens_used": tokens_used,
        "retry_count": state.retry_count + (1 if failure_report.status != "pass" else 0),
        "trace": trace,
    }


# -- Routing Functions --

def route_after_qa(state: AgentState) -> Literal["developer", "architect", "__end__"]:
    """Decide where to go after QA review."""
    if state.status == WorkflowStatus.PASSED:
        return END

    if state.retry_count >= MAX_RETRIES:
        return END
    if state.tokens_used >= TOKEN_BUDGET:
        return END

    if state.status == WorkflowStatus.ESCALATED:
        return "architect"
    else:
        return "developer"


# -- Graph Construction --

def build_graph() -> StateGraph:
    """Build the LangGraph state machine.

    Flow:
        START -> architect -> developer -> qa -> (conditional)
            -> pass: END
            -> fail: developer (retry)
            -> escalate: architect (re-plan)
            -> budget exhausted: END
    """
    graph = StateGraph(AgentState)

    graph.add_node("architect", architect_node)
    graph.add_node("developer", developer_node)
    graph.add_node("qa", qa_node)

    graph.add_edge(START, "architect")
    graph.add_edge("architect", "developer")
    graph.add_edge("developer", "qa")
    graph.add_conditional_edges("qa", route_after_qa)

    return graph


def create_workflow():
    """Create and compile the workflow. Ready to invoke."""
    graph = build_graph()
    return graph.compile()


# -- Entry Point --

def run_task(task_description: str, enable_tracing: bool = True) -> AgentState:
    """Run a task through the full agent workflow.

    Args:
        task_description: What you want the agents to build.
        enable_tracing: Whether to send traces to Langfuse (default True).
            Gracefully degrades if Langfuse is not configured.

    Returns:
        Final AgentState with results, trace, and status.
    """
    # Initialize tracing (no-ops if Langfuse not configured)
    trace_config = create_trace_config(
        enabled=enable_tracing,
        task_description=task_description,
    )

    workflow = create_workflow()
    initial_state = AgentState(task_description=task_description)

    # Pass Langfuse callbacks to LangGraph — this automatically
    # creates spans for each LLM call with token usage and latencies
    invoke_config = {}
    if trace_config.callbacks:
        invoke_config["callbacks"] = trace_config.callbacks

    add_trace_event(trace_config, "orchestrator_start", metadata={
        "task_preview": task_description[:200],
        "max_retries": MAX_RETRIES,
        "token_budget": TOKEN_BUDGET,
    })

    result = workflow.invoke(initial_state, config=invoke_config)
    final_state = AgentState(**result)

    add_trace_event(trace_config, "orchestrator_complete", metadata={
        "status": final_state.status.value,
        "tokens_used": final_state.tokens_used,
        "retry_count": final_state.retry_count,
    })

    # Flush to ensure all trace data is sent
    trace_config.flush()

    return final_state
