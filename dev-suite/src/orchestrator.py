"""LangGraph orchestrator -- Architect -> Lead Dev -> QA loop.

This is the main entry point for the agent workflow.
Implements the state machine with retry logic, token budgets,
structured Blueprint passing, human escalation, and memory
write-back (flush_memory node with mini-summarizer).
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
from .memory.factory import create_memory_store
from .memory.protocol import MemoryQueryResult, MemoryStore
from .memory.summarizer import summarize_writes_sync
from .sandbox.e2b_runner import E2BRunner, SandboxResult
from .sandbox.validation_commands import (
    ValidationPlan,
    format_validation_summary,
    get_validation_plan,
)
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
    the existing value; fields absent are left unchanged.
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
    memory_writes: list[dict]
    trace: list[str]
    sandbox_result: SandboxResult | None


class AgentState(BaseModel):
    """Pydantic model used at the boundary -- for constructing the initial
    state and wrapping the final result with validation and attribute access.
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
    memory_writes: list[dict] = []
    trace: list[str] = []
    sandbox_result: SandboxResult | None = None


# -- LLM Initialization --

def _get_architect_llm():
    """Gemini for the Architect agent (large context, planning only)."""
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
    """Extract text from an LLM response's content field."""
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
    """Extract a JSON object from LLM output text."""
    text = raw.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
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
    """Extract total token count from an LLM response."""
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


def _get_memory_store() -> MemoryStore:
    """Get the memory store via factory (respects MEMORY_BACKEND env var)."""
    return create_memory_store()


def _fetch_memory_context(task_description: str) -> list[str]:
    """Query memory for relevant context across all tiers."""
    try:
        store = _get_memory_store()
        results = store.query(task_description, n_results=10)
        return [r.content for r in results]
    except Exception:
        return []


def _infer_module(target_files: list[str]) -> str:
    """Infer module name from target file paths."""
    if not target_files:
        return "global"
    first_file = target_files[0]
    parts = first_file.replace("\\", "/").split("/")
    if len(parts) > 2 and parts[0] == "src":
        return parts[1]
    if len(parts) > 1:
        return parts[0]
    return "global"


# -- Node Functions --

def architect_node(state: GraphState) -> dict:
    """Architect: generates a structured Blueprint from the task description."""
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
    memory_writes = list(state.get("memory_writes", []))

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

    memory_writes.append({
        "content": f"Implemented blueprint {blueprint.task_id}: {blueprint.instructions[:200]}",
        "tier": "l1",
        "module": _infer_module(blueprint.target_files),
        "source_agent": "developer",
        "confidence": 1.0,
        "sandbox_origin": "locked-down",
        "related_files": ",".join(blueprint.target_files),
        "task_id": blueprint.task_id,
    })

    return {
        "generated_code": content,
        "status": WorkflowStatus.REVIEWING,
        "tokens_used": tokens_used,
        "trace": trace,
        "memory_writes": memory_writes,
    }


def qa_node(state: GraphState) -> dict:
    """QA: reviews the generated code and produces a structured FailureReport."""
    trace = list(state.get("trace", []))
    trace.append("qa: starting review")
    memory_writes = list(state.get("memory_writes", []))

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

    # Include sandbox validation results if available
    sandbox_result = state.get("sandbox_result")
    if sandbox_result is not None:
        user_msg += "\n\nSandbox Validation Results:\n"
        user_msg += f"  Exit code: {sandbox_result.exit_code}\n"
        if sandbox_result.tests_passed is not None:
            user_msg += f"  Tests passed: {sandbox_result.tests_passed}\n"
        if sandbox_result.tests_failed is not None:
            user_msg += f"  Tests failed: {sandbox_result.tests_failed}\n"
        if sandbox_result.errors:
            user_msg += f"  Errors: {', '.join(sandbox_result.errors)}\n"
        if sandbox_result.output_summary:
            user_msg += f"  Output:\n{sandbox_result.output_summary}\n"
        user_msg += (
            "\nUse these real test results to inform your review. "
            "If sandbox tests passed, weigh that heavily in your verdict."
        )
    else:
        user_msg += (
            "\n\nNote: Sandbox validation was not available for this review. "
            "Evaluate the code based on the Blueprint criteria only."
        )

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
        memory_writes.append({
            "content": f"QA passed for {blueprint.task_id}: {failure_report.tests_passed} tests passed",
            "tier": "l2",
            "module": _infer_module(blueprint.target_files),
            "source_agent": "qa",
            "confidence": 1.0,
            "sandbox_origin": "locked-down",
            "related_files": ",".join(blueprint.target_files),
            "task_id": blueprint.task_id,
        })
    elif failure_report.is_architectural:
        status = WorkflowStatus.ESCALATED
        memory_writes.append({
            "content": f"Architectural issue in {blueprint.task_id}: {failure_report.recommendation}",
            "tier": "l0-discovered",
            "module": _infer_module(blueprint.target_files),
            "source_agent": "qa",
            "confidence": 0.85,
            "sandbox_origin": "locked-down",
            "related_files": ",".join(failure_report.failed_files),
            "task_id": blueprint.task_id,
        })
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
        "memory_writes": memory_writes,
    }


# -- Sandbox Validation --

def _run_sandbox_validation(
    commands: list[str],
    template: str | None,
    generated_code: str,
    timeout: int = 120,
) -> SandboxResult | None:
    """Execute validation commands in an E2B sandbox.

    Returns None if E2B_API_KEY is not configured (graceful skip).
    Raises on unexpected errors so the caller can log them.
    """
    api_key = os.getenv("E2B_API_KEY")
    if not api_key:
        return None

    runner = E2BRunner(api_key=api_key, default_timeout=timeout)

    # Build a compound command that runs all validations sequentially
    # and captures all output. We join with && so early failures are visible
    # but use || true in the individual commands (already present in
    # PYTHON_COMMANDS / FRONTEND_COMMANDS) so we get all output.
    compound_cmd = " && ".join(commands)

    return runner.run_tests(
        test_command=compound_cmd,
        timeout=timeout,
        template=template,
    )


def sandbox_validate_node(state: GraphState) -> dict:
    """Run sandbox validation on generated code before QA review.

    Selects the appropriate template and validation commands based on
    the Blueprint's target_files, then executes them in an E2B sandbox.

    Behavior:
      - Optional: if E2B_API_KEY is not set, logs a warning and skips.
      - Errors are caught and logged, never crash the workflow.
      - SandboxResult is stored in state for QA to consume.
    """
    trace = list(state.get("trace", []))
    trace.append("sandbox_validate: starting")

    blueprint = state.get("blueprint")
    if not blueprint:
        trace.append("sandbox_validate: no blueprint -- skipping")
        return {"sandbox_result": None, "trace": trace}

    # Determine what to validate
    plan = get_validation_plan(blueprint.target_files)
    trace.append(f"sandbox_validate: {plan.description}")

    if not plan.commands:
        trace.append("sandbox_validate: no code validation needed -- skipping")
        return {"sandbox_result": None, "trace": trace}

    template_label = plan.template or "default"
    trace.append(
        f"sandbox_validate: template={template_label}, "
        f"commands={len(plan.commands)}"
    )

    generated_code = state.get("generated_code", "")

    try:
        result = _run_sandbox_validation(
            commands=plan.commands,
            template=plan.template,
            generated_code=generated_code,
        )

        if result is None:
            # No API key -- warn loudly
            logger.warning(
                "[SANDBOX] E2B_API_KEY not configured -- sandbox validation "
                "SKIPPED. QA will review without real test results. "
                "Set E2B_API_KEY in .env to enable sandbox validation."
            )
            trace.append(
                "sandbox_validate: WARNING -- E2B_API_KEY not configured, "
                "sandbox validation skipped"
            )
            return {"sandbox_result": None, "trace": trace}

        trace.append(
            f"sandbox_validate: exit_code={result.exit_code}, "
            f"passed={result.tests_passed}, failed={result.tests_failed}"
        )
        logger.info(
            "[SANDBOX] Validation complete: exit=%d, passed=%s, failed=%s",
            result.exit_code, result.tests_passed, result.tests_failed,
        )

        return {"sandbox_result": result, "trace": trace}

    except Exception as e:
        logger.warning("[SANDBOX] Validation failed with error: %s", e)
        trace.append(f"sandbox_validate: error -- {type(e).__name__}: {e}")
        return {"sandbox_result": None, "trace": trace}


def flush_memory_node(state: GraphState) -> dict:
    """Flush accumulated memory_writes to the memory store.

    Runs the mini-summarizer to deduplicate/compress writes,
    then persists to Chroma. Gracefully degrades if store is unreachable.
    """
    trace = list(state.get("trace", []))
    trace.append("flush_memory: starting")

    memory_writes = state.get("memory_writes", [])
    if not memory_writes:
        trace.append("flush_memory: no writes to flush")
        return {"trace": trace}

    consolidated = summarize_writes_sync(memory_writes)
    trace.append(
        f"flush_memory: summarizer {len(memory_writes)} -> {len(consolidated)} entries"
    )

    try:
        store = _get_memory_store()
        written = 0
        for entry in consolidated:
            tier = entry.get("tier", "l1")
            content = entry.get("content", "")
            module = entry.get("module", "global")
            source_agent = entry.get("source_agent", "unknown")
            confidence = entry.get("confidence", 1.0)
            sandbox_origin = entry.get("sandbox_origin", "none")
            related_files = entry.get("related_files", "")
            task_id = entry.get("task_id", "")

            if tier == "l0-discovered":
                store.add_l0_discovered(
                    content, module=module, source_agent=source_agent,
                    confidence=confidence, sandbox_origin=sandbox_origin,
                    related_files=related_files, task_id=task_id,
                )
            elif tier == "l2":
                store.add_l2(
                    content, module=module, source_agent=source_agent,
                    related_files=related_files, task_id=task_id,
                )
            else:
                store.add_l1(
                    content, module=module, source_agent=source_agent,
                    confidence=confidence, sandbox_origin=sandbox_origin,
                    related_files=related_files, task_id=task_id,
                )
            written += 1

        trace.append(f"flush_memory: wrote {written} entries to store")
        logger.info("[FLUSH] Wrote %d memory entries", written)
    except Exception as e:
        trace.append(f"flush_memory: store write failed: {e}")
        logger.warning("[FLUSH] Memory store write failed: %s", e)

    return {"trace": trace}


# -- Routing Functions --

def route_after_qa(state: GraphState) -> Literal["flush_memory", "developer", "architect", "__end__"]:
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
        logger.info("[ROUTER] -> flush_memory (passed)")
        return "flush_memory"

    if status == WorkflowStatus.FAILED:
        logger.info("[ROUTER] -> END (failed: %s)", state.get("error_message", ""))
        return END

    if retry_count >= MAX_RETRIES:
        logger.info("[ROUTER] -> flush_memory (max retries, saving what we have)")
        return "flush_memory"
    if tokens_used >= TOKEN_BUDGET:
        logger.info("[ROUTER] -> flush_memory (token budget, saving what we have)")
        return "flush_memory"

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
        START -> architect -> developer -> sandbox_validate -> qa -> (conditional)
            -> pass: flush_memory -> END
            -> fail: developer (retry)
            -> escalate: architect (re-plan)
            -> budget/retries exhausted: flush_memory -> END
    """
    graph = StateGraph(GraphState)

    graph.add_node("architect", architect_node)
    graph.add_node("developer", developer_node)
    graph.add_node("sandbox_validate", sandbox_validate_node)
    graph.add_node("qa", qa_node)
    graph.add_node("flush_memory", flush_memory_node)

    graph.add_edge(START, "architect")
    graph.add_edge("architect", "developer")
    graph.add_edge("developer", "sandbox_validate")
    graph.add_edge("sandbox_validate", "qa")
    graph.add_conditional_edges("qa", route_after_qa)
    graph.add_edge("flush_memory", END)

    return graph


def create_workflow():
    """Create and compile the workflow. Ready to invoke."""
    graph = build_graph()
    return graph.compile()


# -- Entry Point --

def run_task(
    task_description: str,
    enable_tracing: bool = True,
    session_id: str | None = None,
    tags: list[str] | None = None,
) -> AgentState:
    """Run a task through the full agent workflow.

    Args:
        task_description: What to build.
        enable_tracing: Whether to send traces to Langfuse.
        session_id: Optional session ID for grouping related traces.
        tags: Optional tags for filtering traces in Langfuse UI.
    """
    trace_config = create_trace_config(
        enabled=enable_tracing,
        task_description=task_description,
        session_id=session_id,
        tags=tags or ["orchestrator"],
        metadata={
            "max_retries": str(MAX_RETRIES),
            "token_budget": str(TOKEN_BUDGET),
        },
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
        "memory_writes": [],
        "trace": [],
        "sandbox_result": None,
    }

    invoke_config = {
        "recursion_limit": 25,
    }
    if trace_config.callbacks:
        invoke_config["callbacks"] = trace_config.callbacks

    # Wrap the entire workflow execution in propagation context so that
    # session_id, tags, and metadata flow through to all Langfuse spans.
    with trace_config.propagation_context():
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
            "memory_writes_count": len(final_state.memory_writes),
        })

    trace_config.flush()

    return final_state
