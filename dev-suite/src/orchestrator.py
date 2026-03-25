"""LangGraph orchestrator — Architect -> Lead Dev -> QA loop.

This is the main entry point for the agent workflow.
Implements the state machine with retry logic, token budgets,
structured Blueprint passing, and human escalation.
"""

import json
import logging
import os
from enum import Enum
from typing import Any, Literal, TypedDict

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

logger = logging.getLogger(__name__)


# -- Configuration --

def _safe_int(env_key: str, default: int) -> int:
    """Parse an integer from an env var, falling back to default on error."""
    raw = os.getenv(env_key, str(default))
    try:
        return int(raw)
    except (ValueError, TypeError):
        logger.warning(
            "%s=%r is not a valid integer, using default %d",
            env_key, raw, default,
        )
        return default

MAX_RETRIES = _safe_int("MAX_RETRIES", 3)
TOKEN_BUDGET = _safe_int("TOKEN_BUDGET", 50000)


# -- Workflow State --

class WorkflowStatus(str, Enum):
    PLANNING = "planning"
    BUILDING = "building"
    REVIEWING = "reviewing"
    PASSED = "passed"
    FAILED = "failed"
    ESCALATED = "escalated"


class GraphState(TypedDict, total=False):
    """State that flows through the LangGraph state machine.

    D1 fix: Uses TypedDict (not Pydantic BaseModel) for reliable dict-merge
    semantics in LangGraph. Fields present in a node's return dict replace
    the existing value; fields absent are left unchanged. This prevents the
    state-reset bug where Pydantic defaults (0, "", []) silently overwrite
    accumulated values like retry_count and tokens_used.
    """

    task_description: str
    blueprint: Blueprint | None
    generated_code: str
    failure_report: FailureReport | None
    status: WorkflowStatus
    retry_count: int
    tokens_used: int
    error_message: str
    memory_context: list[str]
    trace: list[str]


class AgentState(BaseModel):
    """Pydantic model used at the boundary — for constructing the initial
    state and wrapping the final result with validation and attribute access.

    Not used as the LangGraph graph state (that's GraphState TypedDict).
    """

    task_description: str = ""
    blueprint: Blueprint | None = None
    generated_code: str = ""
    failure_report: FailureReport | None = None
    status: WorkflowStatus = WorkflowStatus.PLANNING
    retry_count: int = 0
    tokens_used: int = 0
    error_message: str = ""
    memory_context: list[str] = []
    trace: list[str] = []


# -- LLM Initialization --

def _get_architect_llm():
    """Gemini for the Architect agent (large context, planning only).

    Default: gemini-3-flash-preview — frontier-class reasoning with
    free tier access. Best balance of intelligence and cost for
    structured Blueprint generation.

    Override via ARCHITECT_MODEL env var.
    """
    return ChatGoogleGenerativeAI(
        model=os.getenv("ARCHITECT_MODEL", "gemini-3-flash-preview"),
        google_api_key=os.getenv("GOOGLE_API_KEY"),
        temperature=0.2,
    )


def _get_developer_llm():
    """Claude for the Lead Dev agent (code execution)."""
    return ChatAnthropic(
        model=os.getenv("DEVELOPER_MODEL", "claude-sonnet-4-20250514"),
        api_key=os.getenv("ANTHROPIC_API_KEY"),
        temperature=0.1,
        max_tokens=8192,
    )


def _get_qa_llm():
    """Claude for the QA agent (review and testing)."""
    return ChatAnthropic(
        model=os.getenv("QA_MODEL", "claude-sonnet-4-20250514"),
        api_key=os.getenv("ANTHROPIC_API_KEY"),
        temperature=0.0,
        max_tokens=4096,
    )


# -- Helpers --

def _extract_text_content(content: Any) -> str:
    """Extract text from an LLM response's content field.

    D3 fix: Handles both string content (Anthropic) and list-of-blocks
    content (Google GenAI / Gemini with thinking mode). When content is
    a list, concatenates all text blocks.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and "text" in block:
                parts.append(block["text"])
            elif hasattr(block, "text"):
                parts.append(block.text)
        return "\n".join(parts)
    return str(content)


def _extract_json(raw: str) -> dict:
    """Extract a JSON object from LLM output text.

    D3 fix: Handles clean JSON, code-fenced JSON, and JSON embedded
    in preamble text (scans for first { and last }).
    """
    text = raw.strip()

    # Try 1: direct parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Try 2: code fence extraction
    if "```" in text:
        parts = text.split("```")
        for part in parts[1::2]:
            inner = part.strip()
            if inner.startswith("json"):
                inner = inner[4:].strip()
            try:
                return json.loads(inner)
            except json.JSONDecodeError:
                continue

    # Try 3: scan for first { and last }
    first_brace = text.find("{")
    last_brace = text.rfind("}")
    if first_brace != -1 and last_brace > first_brace:
        try:
            return json.loads(text[first_brace:last_brace + 1])
        except json.JSONDecodeError:
            pass

    raise json.JSONDecodeError(
        f"No valid JSON found in response ({len(text)} chars): {text[:200]}...",
        text, 0,
    )


def _extract_token_count(response: Any) -> int:
    """Extract total token count from an LLM response.

    Handles different metadata formats across providers (Anthropic, Google).
    Returns 0 if token count cannot be determined.
    """
    meta = getattr(response, "usage_metadata", None)
    if not meta:
        return 0
    if isinstance(meta, dict):
        for key in ("total_tokens", "totalTokenCount", "total_token_count"):
            if key in meta:
                return int(meta[key])
        input_t = meta.get("input_tokens", meta.get("prompt_tokens", 0))
        output_t = meta.get("output_tokens", meta.get("completion_tokens", 0))
        if input_t or output_t:
            return int(input_t) + int(output_t)
    return 0


def _fetch_memory_context(task_description: str) -> list[str]:
    """Query Chroma for relevant context across all tiers."""
    try:
        store = ChromaMemoryStore()
        results = store.query(task_description, n_results=10)
        return [r["content"] for r in results]
    except Exception:
        return []


# -- Node Functions --

def architect_node(state: GraphState) -> dict:
    """Architect: generates a structured Blueprint from the task description.

    Queries memory for context, then uses Gemini to plan the task.
    Never writes code -- only produces a Blueprint JSON.
    """
    trace = list(state.get("trace", []))
    trace.append("architect: starting planning")

    retry_count = state.get("retry_count", 0)
    tokens_used = state.get("tokens_used", 0)
    logger.info("[ARCH] retry_count=%d, tokens_used=%d, status=%s",
                retry_count, tokens_used, state.get("status", "unknown"))

    memory_context = _fetch_memory_context(state.get("task_description", ""))

    memory_block = ""
    if memory_context:
        memory_block = (
            "\n\nProject context from memory:\n"
            + "\n".join(f"- {c}" for c in memory_context)
        )

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

    user_msg = state.get("task_description", "")
    failure_report = state.get("failure_report")
    if failure_report and failure_report.is_architectural:
        user_msg += "\n\nPREVIOUS ATTEMPT FAILED (architectural issue):\n"
        user_msg += f"Errors: {', '.join(failure_report.errors)}\n"
        user_msg += f"Recommendation: {failure_report.recommendation}"

    llm = _get_architect_llm()
    response = llm.invoke([
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_msg),
    ])

    try:
        raw = _extract_text_content(response.content)
        blueprint_data = _extract_json(raw)
        blueprint = Blueprint(**blueprint_data)
    except (json.JSONDecodeError, Exception) as e:
        trace.append(f"architect: failed to parse blueprint: {e}")
        logger.error("[ARCH] Blueprint parse failed: %s", e)
        return {
            "status": WorkflowStatus.FAILED,
            "error_message": f"Architect failed to produce valid Blueprint: {e}",
            "trace": trace,
            "memory_context": memory_context,
        }

    trace.append(f"architect: blueprint created for {len(blueprint.target_files)} files")
    tokens_used = tokens_used + _extract_token_count(response)
    logger.info("[ARCH] done. tokens_used now=%d", tokens_used)

    return {
        "blueprint": blueprint,
        "status": WorkflowStatus.BUILDING,
        "tokens_used": tokens_used,
        "trace": trace,
        "memory_context": memory_context,
    }


def developer_node(state: GraphState) -> dict:
    """Lead Dev: executes the Blueprint and generates code."""
    trace = list(state.get("trace", []))
    trace.append("developer: starting build")

    retry_count = state.get("retry_count", 0)
    tokens_used = state.get("tokens_used", 0)
    logger.info("[DEV] retry_count=%d, tokens_used=%d, status=%s",
                retry_count, tokens_used, state.get("status", "unknown"))

    blueprint = state.get("blueprint")
    if not blueprint:
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

    user_msg = f"Blueprint:\n{blueprint.model_dump_json(indent=2)}"

    failure_report = state.get("failure_report")
    if failure_report and not failure_report.is_architectural:
        user_msg += "\n\nPREVIOUS ATTEMPT FAILED:\n"
        user_msg += f"Tests passed: {failure_report.tests_passed}\n"
        user_msg += f"Tests failed: {failure_report.tests_failed}\n"
        user_msg += f"Errors: {', '.join(failure_report.errors)}\n"
        user_msg += f"Failed files: {', '.join(failure_report.failed_files)}\n"
        user_msg += f"Recommendation: {failure_report.recommendation}\n"
        user_msg += "\nFix the issues and regenerate the code."

    llm = _get_developer_llm()
    response = llm.invoke([
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_msg),
    ])

    content = _extract_text_content(response.content)
    trace.append(f"developer: code generated ({len(content)} chars)")
    tokens_used = tokens_used + _extract_token_count(response)
    logger.info("[DEV] done. tokens_used now=%d", tokens_used)

    return {
        "generated_code": content,
        "status": WorkflowStatus.REVIEWING,
        "tokens_used": tokens_used,
        "trace": trace,
    }


def qa_node(state: GraphState) -> dict:
    """QA: reviews the generated code and produces a structured FailureReport."""
    trace = list(state.get("trace", []))
    trace.append("qa: starting review")

    retry_count = state.get("retry_count", 0)
    tokens_used = state.get("tokens_used", 0)
    logger.info("[QA] retry_count=%d, tokens_used=%d, status=%s",
                retry_count, tokens_used, state.get("status", "unknown"))

    generated_code = state.get("generated_code", "")
    blueprint = state.get("blueprint")
    if not generated_code or not blueprint:
        trace.append("qa: missing code or blueprint")
        return {
            "status": WorkflowStatus.FAILED,
            "error_message": "QA received no code or blueprint to review",
            "trace": trace,
        }

    system_prompt = (
        "You are the QA agent. You review code against a Blueprint's "
        "acceptance criteria.\n\n"
        "Respond with ONLY a valid JSON object matching this schema:\n"
        "{\n"
        '  "task_id": "string (from the Blueprint)",\n'
        '  "status": "pass" or "fail" or "escalate",\n'
        '  "tests_passed": number,\n'
        '  "tests_failed": number,\n'
        '  "errors": ["list of specific error descriptions"],\n'
        '  "failed_files": ["list of files with issues"],\n'
        '  "is_architectural": true/false '
        "(set true if the failure is a design/planning issue),\n"
        '  "recommendation": "what to fix or why it should escalate"\n'
        "}\n\n"
        '"escalate" means the Blueprint itself is wrong, not just the '
        "implementation.\n"
        "Be strict but fair. Only pass code that meets ALL acceptance "
        "criteria.\n"
        "Do not include any text before or after the JSON."
    )

    bp_json = blueprint.model_dump_json(indent=2)
    user_msg = f"Blueprint:\n{bp_json}\n\nGenerated Code:\n{generated_code}"

    llm = _get_qa_llm()
    response = llm.invoke([
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_msg),
    ])

    try:
        raw = _extract_text_content(response.content)
        report_data = _extract_json(raw)
        failure_report = FailureReport(**report_data)
    except (json.JSONDecodeError, Exception) as e:
        trace.append(f"qa: failed to parse report: {e}")
        return {
            "status": WorkflowStatus.FAILED,
            "error_message": f"QA failed to produce valid report: {e}",
            "trace": trace,
        }

    trace.append(
        f"qa: verdict={failure_report.status}, "
        f"passed={failure_report.tests_passed}, "
        f"failed={failure_report.tests_failed}"
    )
    tokens_used = tokens_used + _extract_token_count(response)

    if failure_report.status == "pass":
        status = WorkflowStatus.PASSED
    elif failure_report.is_architectural:
        status = WorkflowStatus.ESCALATED
    else:
        status = WorkflowStatus.REVIEWING

    new_retry_count = retry_count + (1 if failure_report.status != "pass" else 0)
    logger.info("[QA] verdict=%s, retry_count %d->%d, tokens_used=%d",
                failure_report.status, retry_count, new_retry_count, tokens_used)

    return {
        "failure_report": failure_report,
        "status": status,
        "tokens_used": tokens_used,
        "retry_count": new_retry_count,
        "trace": trace,
    }


# -- Routing Functions --

def route_after_qa(state: GraphState) -> Literal["developer", "architect", "__end__"]:
    """Decide where to go after QA review."""
    status = state.get("status", WorkflowStatus.FAILED)
    retry_count = state.get("retry_count", 0)
    tokens_used = state.get("tokens_used", 0)

    logger.info(
        "[ROUTER] status=%s, retry_count=%d, tokens_used=%d, "
        "max_retries=%d, token_budget=%d",
        status, retry_count, tokens_used, MAX_RETRIES, TOKEN_BUDGET,
    )

    if status == WorkflowStatus.PASSED:
        logger.info("[ROUTER] -> END (passed)")
        return END

    if status == WorkflowStatus.FAILED:
        logger.info("[ROUTER] -> END (failed: %s)", state.get("error_message", ""))
        return END

    if retry_count >= MAX_RETRIES:
        logger.info("[ROUTER] -> END (max retries)")
        return END
    if tokens_used >= TOKEN_BUDGET:
        logger.info("[ROUTER] -> END (token budget)")
        return END

    if status == WorkflowStatus.ESCALATED:
        logger.info("[ROUTER] -> architect (escalation)")
        return "architect"
    else:
        logger.info("[ROUTER] -> developer (retry)")
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
    graph = StateGraph(GraphState)

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
    trace_config = create_trace_config(
        enabled=enable_tracing,
        task_description=task_description,
    )

    workflow = create_workflow()

    initial_state: GraphState = {
        "task_description": task_description,
        "blueprint": None,
        "generated_code": "",
        "failure_report": None,
        "status": WorkflowStatus.PLANNING,
        "retry_count": 0,
        "tokens_used": 0,
        "error_message": "",
        "memory_context": [],
        "trace": [],
    }

    invoke_config = {
        "recursion_limit": 25,
    }
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

    trace_config.flush()

    return final_state
