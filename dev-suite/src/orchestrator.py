"""LangGraph orchestrator -- Architect -> Lead Dev -> apply_code -> sandbox -> QA loop.

This is the main entry point for the agent workflow.
Implements the state machine with retry logic, token budgets,
structured Blueprint passing, human escalation, code application,
tool binding (issue #80), and memory write-back.

Issue #80: Agent tool binding -- Dev and QA agents can now use
workspace tools (filesystem_read, filesystem_write, etc.) via
LangChain's bind_tools() + iterative tool execution loop.
Tools are passed via RunnableConfig["configurable"]["tools"].
"""

import asyncio
import json
import logging
import os
import re
from enum import Enum
from pathlib import Path
from typing import Any, Literal, TypedDict

from dotenv import load_dotenv
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.runnables.config import RunnableConfig
from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel

from .agents.architect import Blueprint
from .agents.qa import FailureReport, FailureType
from .memory.factory import create_memory_store
from .memory.protocol import MemoryQueryResult, MemoryStore
from .memory.summarizer import summarize_writes_sync
from .sandbox.e2b_runner import E2BRunner, SandboxResult
from .sandbox.validation_commands import (
    ValidationPlan,
    format_validation_summary,
    get_validation_plan,
)
from .tools.code_parser import (
    CodeParserError,
    parse_generated_code,
    validate_paths_for_workspace,
)
from .tracing import add_trace_event, create_trace_config

load_dotenv()

logger = logging.getLogger(__name__)


def _safe_int(env_key: str, default: int) -> int:
    raw = os.getenv(env_key, str(default))
    try:
        return int(raw)
    except (ValueError, TypeError):
        logger.warning("%s=%r is not a valid integer, using default %d", env_key, raw, default)
        return default

MAX_RETRIES = _safe_int("MAX_RETRIES", 3)
TOKEN_BUDGET = _safe_int("TOKEN_BUDGET", 50000)
MAX_TOOL_TURNS = _safe_int("MAX_TOOL_TURNS", 10)


def _get_workspace_root() -> Path:
    raw = os.getenv("WORKSPACE_ROOT", ".")
    return Path(raw).resolve()


class WorkflowStatus(str, Enum):
    PLANNING = "planning"
    BUILDING = "building"
    REVIEWING = "reviewing"
    PASSED = "passed"
    FAILED = "failed"
    ESCALATED = "escalated"


class GraphState(TypedDict, total=False):
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
    parsed_files: list[dict]
    tool_calls_log: list[dict]
    memory_writes_flushed: list[dict]


class AgentState(BaseModel):
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
    parsed_files: list[dict] = []
    tool_calls_log: list[dict] = []


def _get_architect_llm():
    return ChatGoogleGenerativeAI(model=os.getenv("ARCHITECT_MODEL", "gemini-3-flash-preview"), google_api_key=os.getenv("GOOGLE_API_KEY"), temperature=0.2)


def _get_developer_llm():
    return ChatAnthropic(model=os.getenv("DEVELOPER_MODEL", "claude-sonnet-4-20250514"), api_key=os.getenv("ANTHROPIC_API_KEY"), temperature=0.1, max_tokens=8192)


def _get_qa_llm():
    return ChatAnthropic(model=os.getenv("QA_MODEL", "claude-sonnet-4-20250514"), api_key=os.getenv("ANTHROPIC_API_KEY"), temperature=0.0, max_tokens=4096)


def _extract_text_content(content: Any) -> str:
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
    raise json.JSONDecodeError(f"No valid JSON found in response ({len(text)} chars): {text[:200]}...", text, 0)


def _extract_token_count(response: Any) -> int:
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
    return create_memory_store()


def _fetch_memory_context(task_description: str) -> list[str]:
    try:
        store = _get_memory_store()
        results = store.query(task_description, n_results=10)
        return [r.content for r in results]
    except Exception:
        return []


def _infer_module(target_files: list[str]) -> str:
    if not target_files:
        return "global"
    first_file = target_files[0]
    parts = first_file.replace("\\", "/").split("/")
    if len(parts) > 2 and parts[0] == "src":
        return parts[1]
    if len(parts) > 1:
        return parts[0]
    return "global"


# -- Tool Binding (Issue #80) --

# Fix 2: Removed github_create_pr -- non-idempotent write should not be in
# an iterative tool loop. PR creation belongs in a post-QA-pass step.
DEV_TOOL_NAMES = {"filesystem_read", "filesystem_write", "filesystem_list", "github_read_diff"}
QA_TOOL_NAMES = {"filesystem_read", "filesystem_list", "github_read_diff"}

# Fix 4: Secret pattern regexes for sanitizing tool call previews
_SECRET_PATTERNS = [
    re.compile(r'(?:sk|pk|api|key|token|secret|password|bearer)[_-]?\w{10,}', re.IGNORECASE),
    re.compile(r'(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9_]{30,}'),
    re.compile(r'(?:eyJ)[A-Za-z0-9_-]{20,}'),
    re.compile(r'(?:AKIA|ASIA)[A-Z0-9]{16}'),
]


def _sanitize_preview(text: str, max_len: int = 200) -> str:
    """Truncate and redact known secret patterns from tool call previews."""
    if not text:
        return ""
    sanitized = text
    for pattern in _SECRET_PATTERNS:
        sanitized = pattern.sub("[REDACTED]", sanitized)
    if len(sanitized) > max_len:
        sanitized = sanitized[:max_len] + "..."
    return sanitized


def _get_agent_tools(config, allowed_names=None):
    if not config:
        return []
    configurable = config.get("configurable", {})
    tools = configurable.get("tools", [])
    if not tools:
        return []
    if allowed_names is None:
        return list(tools)
    return [t for t in tools if t.name in allowed_names]


async def _execute_tool_call(tool_call, tools):
    """Execute a single tool call. Uses ainvoke/invoke public API (Fix 3)."""
    tool_name = tool_call.get("name", "")
    tool_args = tool_call.get("args", {})
    tool_id = tool_call.get("id", "unknown")
    tool_map = {t.name: t for t in tools}
    tool = tool_map.get(tool_name)
    if not tool:
        return ToolMessage(content=f"Error: Tool '{tool_name}' not found. Available: {list(tool_map.keys())}", tool_call_id=tool_id)
    try:
        # Fix 3: Use public ainvoke/invoke API instead of tool.coroutine.
        # ainvoke handles input validation, callbacks, and config propagation.
        if hasattr(tool, "ainvoke"):
            result = await tool.ainvoke(tool_args)
        else:
            result = tool.invoke(tool_args)
        return ToolMessage(content=str(result), tool_call_id=tool_id)
    except Exception as e:
        logger.warning("[TOOLS] Tool %s failed: %s", tool_name, e)
        return ToolMessage(content=f"Error executing {tool_name}: {type(e).__name__}: {e}", tool_call_id=tool_id)


async def _run_tool_loop(llm_with_tools, messages, tools, max_turns=MAX_TOOL_TURNS, tokens_used=0, trace=None, agent_name="agent"):
    if trace is None:
        trace = []
    tool_calls_log = []
    current_messages = list(messages)
    # Fix 5: Guard against max_turns <= 0 to prevent unbound response variable
    if max_turns <= 0:
        logger.warning("[%s] max_turns=%d, skipping tool loop", agent_name.upper(), max_turns)
        trace.append(f"{agent_name}: tool loop skipped (max_turns={max_turns})")
        last_msg = current_messages[-1] if current_messages else AIMessage(content="")
        return last_msg, tokens_used, tool_calls_log
    for turn in range(max_turns):
        response = await llm_with_tools.ainvoke(current_messages)
        tokens_used += _extract_token_count(response)
        tool_calls = getattr(response, "tool_calls", None) or []
        if not tool_calls:
            trace.append(f"{agent_name}: tool loop done after {turn} tool turn(s)")
            return response, tokens_used, tool_calls_log
        trace.append(f"{agent_name}: turn {turn + 1} -- {len(tool_calls)} tool call(s): {', '.join(tc.get('name', '?') for tc in tool_calls)}")
        logger.info("[%s] Tool turn %d: %d calls", agent_name.upper(), turn + 1, len(tool_calls))
        current_messages.append(response)
        for tc in tool_calls:
            tool_msg = await _execute_tool_call(tc, tools)
            current_messages.append(tool_msg)
            # Fix 4: Sanitize previews before persisting to tool_calls_log
            tool_calls_log.append({"agent": agent_name, "turn": turn + 1, "tool": tc.get("name", "unknown"), "args_preview": _sanitize_preview(str(tc.get("args", {}))), "result_preview": _sanitize_preview(str(tool_msg.content)), "success": not tool_msg.content.startswith("Error")})
    trace.append(f"{agent_name}: tool loop hit max turns ({max_turns})")
    logger.warning("[%s] Hit max tool turns (%d)", agent_name.upper(), max_turns)
    return response, tokens_used, tool_calls_log


def _run_async(coro):
    """Run an async coroutine from sync context for the tool loop.

    Design note (re: CodeRabbit #8): This is intentional. developer_node and
    qa_node are sync functions that use _run_async() to bridge into the async
    tool loop. run_task() uses workflow.invoke() (sync). Converting everything
    to async would cascade changes to CLI, tests, and callers with no benefit
    since LangGraph handles sync nodes with internal async bridges fine.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop and loop.is_running():
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, coro).result()
    else:
        return asyncio.run(coro)


# -- Node Functions --

def architect_node(state: GraphState) -> dict:
    trace = list(state.get("trace", []))
    trace.append("architect: starting planning")
    retry_count = state.get("retry_count", 0)
    tokens_used = state.get("tokens_used", 0)
    logger.info("[ARCH] retry_count=%d, tokens_used=%d, status=%s", retry_count, tokens_used, state.get("status", "unknown"))
    memory_context = _fetch_memory_context(state.get("task_description", ""))
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
    user_msg = state.get("task_description", "")
    failure_report = state.get("failure_report")
    if failure_report and failure_report.is_architectural:
        user_msg += "\n\nPREVIOUS ATTEMPT FAILED (architectural issue):\n"
        user_msg += f"Errors: {', '.join(failure_report.errors)}\n"
        if failure_report.failed_files:
            user_msg += f"Failed files: {', '.join(failure_report.failed_files)}\n"
        user_msg += f"Recommendation: {failure_report.recommendation}\n"
        user_msg += "\nGenerate a COMPLETELY NEW Blueprint. Do not patch the old one. The previous target_files or approach was wrong."
    llm = _get_architect_llm()
    response = llm.invoke([SystemMessage(content=system_prompt), HumanMessage(content=user_msg)])
    try:
        raw = _extract_text_content(response.content)
        blueprint_data = _extract_json(raw)
        blueprint = Blueprint(**blueprint_data)
    except (json.JSONDecodeError, Exception) as e:
        trace.append(f"architect: failed to parse blueprint: {e}")
        logger.error("[ARCH] Blueprint parse failed: %s", e)
        return {"status": WorkflowStatus.FAILED, "error_message": f"Architect failed to produce valid Blueprint: {e}", "trace": trace, "memory_context": memory_context}
    trace.append(f"architect: blueprint created for {len(blueprint.target_files)} files")
    tokens_used = tokens_used + _extract_token_count(response)
    logger.info("[ARCH] done. tokens_used now=%d", tokens_used)
    return {"blueprint": blueprint, "status": WorkflowStatus.BUILDING, "tokens_used": tokens_used, "trace": trace, "memory_context": memory_context}


def developer_node(state: GraphState, config: RunnableConfig | None = None) -> dict:
    """Lead Dev: executes the Blueprint and generates code. Issue #80: tool binding support."""
    trace = list(state.get("trace", []))
    trace.append("developer: starting build")
    memory_writes = list(state.get("memory_writes", []))
    tool_calls_log = list(state.get("tool_calls_log", []))
    retry_count = state.get("retry_count", 0)
    tokens_used = state.get("tokens_used", 0)
    logger.info("[DEV] retry_count=%d, tokens_used=%d, status=%s", retry_count, tokens_used, state.get("status", "unknown"))
    blueprint = state.get("blueprint")
    if not blueprint:
        trace.append("developer: no blueprint provided")
        return {"status": WorkflowStatus.FAILED, "error_message": "Developer received no Blueprint", "trace": trace}
    tools = _get_agent_tools(config, DEV_TOOL_NAMES)
    has_tools = len(tools) > 0
    if has_tools:
        tool_names = [t.name for t in tools]
        trace.append(f"developer: {len(tools)} tools available: {', '.join(tool_names)}")
        system_prompt = """You are the Lead Dev agent. You receive a structured Blueprint and implement it using the tools available to you.

WORKFLOW:
1. Use filesystem_read to examine the existing files listed in target_files.
2. Use filesystem_list to explore the project directory structure if needed.
3. Implement the changes described in the Blueprint.
4. Use filesystem_write to write each file.
5. After writing all files, provide a summary of what you implemented.

IMPORTANT:
- Use filesystem_write for EACH file you need to create or modify.
- Follow the Blueprint exactly. Respect all constraints.
- Write clean, well-documented code.
- After completing all file writes, respond with a text summary.
- Also include the complete code in your final response using the format:
  # --- FILE: path/to/file.py ---
  (file contents)"""
    else:
        trace.append("developer: no tools available, using single-shot mode")
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
    messages = [SystemMessage(content=system_prompt), HumanMessage(content=user_msg)]
    llm = _get_developer_llm()
    if has_tools:
        llm_with_tools = llm.bind_tools(tools)
        response, tokens_used, new_tool_log = _run_async(_run_tool_loop(llm_with_tools, messages, tools, max_turns=MAX_TOOL_TURNS, tokens_used=tokens_used, trace=trace, agent_name="developer"))
        tool_calls_log.extend(new_tool_log)
    else:
        response = llm.invoke(messages)
        tokens_used += _extract_token_count(response)
    content = _extract_text_content(response.content)
    trace.append(f"developer: code generated ({len(content)} chars)")
    logger.info("[DEV] done. tokens_used now=%d", tokens_used)
    memory_writes.append({"content": f"Implemented blueprint {blueprint.task_id}: {blueprint.instructions[:200]}", "tier": "l1", "module": _infer_module(blueprint.target_files), "source_agent": "developer", "confidence": 1.0, "sandbox_origin": "locked-down", "related_files": ",".join(blueprint.target_files), "task_id": blueprint.task_id})
    return {"generated_code": content, "status": WorkflowStatus.REVIEWING, "tokens_used": tokens_used, "trace": trace, "memory_writes": memory_writes, "tool_calls_log": tool_calls_log}


def apply_code_node(state: GraphState) -> dict:
    trace = list(state.get("trace", []))
    trace.append("apply_code: starting")
    generated_code = state.get("generated_code", "")
    blueprint = state.get("blueprint")
    if not generated_code:
        trace.append("apply_code: no generated_code -- skipping")
        return {"parsed_files": [], "trace": trace}
    if not blueprint:
        trace.append("apply_code: no blueprint -- skipping")
        return {"parsed_files": [], "trace": trace}
    try:
        parsed = parse_generated_code(generated_code)
    except CodeParserError as e:
        logger.warning("[APPLY_CODE] Parse error: %s", e)
        trace.append(f"apply_code: parse error -- {e}")
        return {"parsed_files": [], "trace": trace}
    if not parsed:
        trace.append("apply_code: parser returned no files")
        return {"parsed_files": [], "trace": trace}
    workspace_root = _get_workspace_root()
    safe_files = validate_paths_for_workspace(parsed, workspace_root)
    if len(safe_files) < len(parsed):
        skipped = len(parsed) - len(safe_files)
        trace.append(f"apply_code: WARNING -- {skipped} file(s) skipped due to path validation")
    total_chars = 0
    written_count = 0
    for pf in safe_files:
        try:
            target = workspace_root / pf.path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(pf.content, encoding="utf-8")
            written_count += 1
            total_chars += len(pf.content)
        except Exception as e:
            logger.warning("[APPLY_CODE] Failed to write %s: %s", pf.path, e)
            trace.append(f"apply_code: failed to write {pf.path} -- {e}")
    trace.append(f"apply_code: wrote {written_count} files ({total_chars:,} chars total) to workspace")
    logger.info("[APPLY_CODE] Wrote %d files (%d chars) to %s", written_count, total_chars, workspace_root)
    parsed_files_data = [{"path": pf.path, "content": pf.content} for pf in safe_files]
    return {"parsed_files": parsed_files_data, "trace": trace}


def qa_node(state: GraphState, config: RunnableConfig | None = None) -> dict:
    """QA: reviews the generated code. Issue #80: read-only tool access."""
    trace = list(state.get("trace", []))
    trace.append("qa: starting review")
    memory_writes = list(state.get("memory_writes", []))
    tool_calls_log = list(state.get("tool_calls_log", []))
    retry_count = state.get("retry_count", 0)
    tokens_used = state.get("tokens_used", 0)
    logger.info("[QA] retry_count=%d, tokens_used=%d, status=%s", retry_count, tokens_used, state.get("status", "unknown"))
    generated_code = state.get("generated_code", "")
    blueprint = state.get("blueprint")
    if not generated_code or not blueprint:
        trace.append("qa: missing code or blueprint")
        return {"status": WorkflowStatus.FAILED, "error_message": "QA received no code or blueprint to review", "trace": trace}
    tools = _get_agent_tools(config, QA_TOOL_NAMES)
    has_tools = len(tools) > 0
    if has_tools:
        tool_names = [t.name for t in tools]
        trace.append(f"qa: {len(tools)} tools available: {', '.join(tool_names)}")
    system_prompt = "You are the QA agent. You review code against a Blueprint's acceptance criteria.\n\n"
    if has_tools:
        system_prompt += "You have tools to read files from the workspace. Use filesystem_read to inspect the actual files that were written, and filesystem_list to check the project structure.\n\n"
    system_prompt += 'Respond with ONLY a valid JSON object matching this schema:\n{\n  "task_id": "string (from the Blueprint)",\n  "status": "pass" or "fail" or "escalate",\n  "tests_passed": number,\n  "tests_failed": number,\n  "errors": ["list of specific error descriptions"],\n  "failed_files": ["list of files with issues"],\n  "is_architectural": true/false,\n  "failure_type": "code" or "architectural" or null (if pass),\n  "recommendation": "what to fix or why it should escalate"\n}\n\nFAILURE CLASSIFICATION (critical for correct routing):\n\nSet failure_type to "code" (status: "fail") when:\n- Implementation has bugs, syntax errors, or type errors\n- Tests fail due to logic errors in the code\n- Code does not follow the Blueprint\'s constraints\n- Missing error handling or edge cases\nAction: Lead Dev will retry with the same Blueprint.\n\nSet failure_type to "architectural" (status: "escalate") when:\n- Blueprint targets the WRONG files (code is in the wrong place)\n- A required dependency or import is missing from the Blueprint\n- The design approach is fundamentally flawed\n- Acceptance criteria are impossible to meet with current targets\n- The task requires files not listed in target_files\nAction: Architect will generate a completely NEW Blueprint.\n\nBe strict but fair. Only pass code that meets ALL acceptance criteria.\nDo not include any text before or after the JSON.'
    bp_json = blueprint.model_dump_json(indent=2)
    user_msg = f"Blueprint:\n{bp_json}\n\nGenerated Code:\n{generated_code}"
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
        user_msg += "\nUse these real test results to inform your review. If sandbox tests passed, weigh that heavily in your verdict."
    else:
        user_msg += "\n\nNote: Sandbox validation was not available for this review. Evaluate the code based on the Blueprint criteria only."
    messages = [SystemMessage(content=system_prompt), HumanMessage(content=user_msg)]
    llm = _get_qa_llm()
    if has_tools:
        llm_with_tools = llm.bind_tools(tools)
        response, tokens_used, new_tool_log = _run_async(_run_tool_loop(llm_with_tools, messages, tools, max_turns=5, tokens_used=tokens_used, trace=trace, agent_name="qa"))
        tool_calls_log.extend(new_tool_log)
    else:
        response = llm.invoke(messages)
        tokens_used += _extract_token_count(response)
    try:
        raw = _extract_text_content(response.content)
        report_data = _extract_json(raw)
        failure_report = FailureReport(**report_data)
    except (json.JSONDecodeError, Exception) as e:
        trace.append(f"qa: failed to parse report: {e}")
        return {"status": WorkflowStatus.FAILED, "error_message": f"QA failed to produce valid report: {e}", "trace": trace}
    trace.append(f"qa: verdict={failure_report.status}, passed={failure_report.tests_passed}, failed={failure_report.tests_failed}")
    if failure_report.status == "pass":
        status = WorkflowStatus.PASSED
        memory_writes.append({"content": f"QA passed for {blueprint.task_id}: {failure_report.tests_passed} tests passed", "tier": "l2", "module": _infer_module(blueprint.target_files), "source_agent": "qa", "confidence": 1.0, "sandbox_origin": "locked-down", "related_files": ",".join(blueprint.target_files), "task_id": blueprint.task_id})
    elif failure_report.is_architectural:
        status = WorkflowStatus.ESCALATED
        memory_writes.append({"content": f"Architectural issue in {blueprint.task_id}: {failure_report.recommendation}", "tier": "l0-discovered", "module": _infer_module(blueprint.target_files), "source_agent": "qa", "confidence": 0.85, "sandbox_origin": "locked-down", "related_files": ",".join(failure_report.failed_files), "task_id": blueprint.task_id})
    else:
        status = WorkflowStatus.REVIEWING
    new_retry_count = retry_count + (1 if failure_report.status != "pass" else 0)
    logger.info("[QA] verdict=%s, retry_count %d->%d, tokens_used=%d", failure_report.status, retry_count, new_retry_count, tokens_used)
    return {"failure_report": failure_report, "status": status, "tokens_used": tokens_used, "retry_count": new_retry_count, "trace": trace, "memory_writes": memory_writes, "tool_calls_log": tool_calls_log}


def _run_sandbox_validation(commands, template, generated_code, parsed_files=None, timeout=120):
    api_key = os.getenv("E2B_API_KEY")
    if not api_key:
        return None
    runner = E2BRunner(api_key=api_key, default_timeout=timeout)
    project_files = None
    if parsed_files:
        project_files = {pf["path"]: pf["content"] for pf in parsed_files}
    compound_cmd = " && ".join(commands)
    return runner.run_tests(test_command=compound_cmd, project_files=project_files, timeout=timeout, template=template)


def sandbox_validate_node(state: GraphState) -> dict:
    trace = list(state.get("trace", []))
    trace.append("sandbox_validate: starting")
    blueprint = state.get("blueprint")
    if not blueprint:
        trace.append("sandbox_validate: no blueprint -- skipping")
        return {"sandbox_result": None, "trace": trace}
    plan = get_validation_plan(blueprint.target_files)
    trace.append(f"sandbox_validate: {plan.description}")
    if not plan.commands:
        trace.append("sandbox_validate: no code validation needed -- skipping")
        return {"sandbox_result": None, "trace": trace}
    template_label = plan.template or "default"
    trace.append(f"sandbox_validate: template={template_label}, commands={len(plan.commands)}")
    generated_code = state.get("generated_code", "")
    parsed_files = state.get("parsed_files", [])
    if parsed_files:
        trace.append(f"sandbox_validate: loading {len(parsed_files)} files into sandbox")
    try:
        result = _run_sandbox_validation(commands=plan.commands, template=plan.template, generated_code=generated_code, parsed_files=parsed_files if parsed_files else None)
        if result is None:
            logger.warning("[SANDBOX] E2B_API_KEY not configured -- sandbox validation SKIPPED.")
            trace.append("sandbox_validate: WARNING -- E2B_API_KEY not configured, sandbox validation skipped")
            return {"sandbox_result": None, "trace": trace}
        trace.append(f"sandbox_validate: exit_code={result.exit_code}, passed={result.tests_passed}, failed={result.tests_failed}")
        logger.info("[SANDBOX] Validation complete: exit=%d, passed=%s, failed=%s", result.exit_code, result.tests_passed, result.tests_failed)
        return {"sandbox_result": result, "trace": trace}
    except Exception as e:
        logger.warning("[SANDBOX] Validation failed with error: %s", e)
        trace.append(f"sandbox_validate: error -- {type(e).__name__}: {e}")
        return {"sandbox_result": None, "trace": trace}


def flush_memory_node(state: GraphState) -> dict:
    trace = list(state.get("trace", []))
    trace.append("flush_memory: starting")
    memory_writes = state.get("memory_writes", [])
    if not memory_writes:
        trace.append("flush_memory: no writes to flush")
        return {"trace": trace}
    consolidated = summarize_writes_sync(memory_writes)
    trace.append(f"flush_memory: summarizer {len(memory_writes)} -> {len(consolidated)} entries")
    written_entries: list[dict] = []
    try:
        store = _get_memory_store()
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
                store.add_l0_discovered(content, module=module, source_agent=source_agent, confidence=confidence, sandbox_origin=sandbox_origin, related_files=related_files, task_id=task_id)
            elif tier == "l2":
                store.add_l2(content, module=module, source_agent=source_agent, related_files=related_files, task_id=task_id)
            else:
                store.add_l1(content, module=module, source_agent=source_agent, confidence=confidence, sandbox_origin=sandbox_origin, related_files=related_files, task_id=task_id)
            written_entries.append(entry)
        trace.append(f"flush_memory: wrote {len(written_entries)} entries to store")
        logger.info("[FLUSH] Wrote %d memory entries", len(written_entries))
    except Exception as e:
        trace.append(f"flush_memory: store write failed: {e}")
        logger.warning("[FLUSH] Memory store write failed: %s", e)
    return {"trace": trace, "memory_writes_flushed": written_entries}


def route_after_qa(state: GraphState) -> Literal["flush_memory", "developer", "architect", "__end__"]:
    status = state.get("status", WorkflowStatus.FAILED)
    retry_count = state.get("retry_count", 0)
    tokens_used = state.get("tokens_used", 0)
    logger.info("[ROUTER] status=%s, retry_count=%d, tokens_used=%d, max_retries=%d, token_budget=%d", status, retry_count, tokens_used, MAX_RETRIES, TOKEN_BUDGET)
    if status == WorkflowStatus.PASSED:
        return "flush_memory"
    if status == WorkflowStatus.FAILED:
        return END
    if retry_count >= MAX_RETRIES:
        return "flush_memory"
    if tokens_used >= TOKEN_BUDGET:
        return "flush_memory"
    if status == WorkflowStatus.ESCALATED:
        return "architect"
    return "developer"


def build_graph() -> StateGraph:
    graph = StateGraph(GraphState)
    graph.add_node("architect", architect_node)
    graph.add_node("developer", developer_node)
    graph.add_node("apply_code", apply_code_node)
    graph.add_node("sandbox_validate", sandbox_validate_node)
    graph.add_node("qa", qa_node)
    graph.add_node("flush_memory", flush_memory_node)
    graph.add_edge(START, "architect")
    graph.add_edge("architect", "developer")
    graph.add_edge("developer", "apply_code")
    graph.add_edge("apply_code", "sandbox_validate")
    graph.add_edge("sandbox_validate", "qa")
    graph.add_conditional_edges("qa", route_after_qa)
    graph.add_edge("flush_memory", END)
    return graph


def create_workflow():
    return build_graph().compile()


def init_tools_config(workspace_root=None):
    if workspace_root is None:
        workspace_root = _get_workspace_root()
    try:
        from .tools import create_provider, get_tools, load_mcp_config
        config_path = Path(workspace_root) / "mcp-config.json"
        if not config_path.is_file():
            logger.info("[TOOLS] No mcp-config.json found at %s, tools disabled", config_path)
            return {"configurable": {"tools": []}}
        mcp_config = load_mcp_config(str(config_path))
        provider = create_provider(mcp_config, workspace_root)
        tools = get_tools(provider)
        logger.info("[TOOLS] Loaded %d tools from provider", len(tools))
        return {"configurable": {"tools": tools}}
    except Exception as e:
        logger.warning("[TOOLS] Failed to initialize tools: %s", e, exc_info=True)
        return {"configurable": {"tools": []}}


def run_task(task_description, enable_tracing=True, session_id=None, tags=None):
    trace_config = create_trace_config(enabled=enable_tracing, task_description=task_description, session_id=session_id, tags=tags or ["orchestrator"], metadata={"max_retries": str(MAX_RETRIES), "token_budget": str(TOKEN_BUDGET)})
    workflow = create_workflow()
    tools_config = init_tools_config()
    initial_state: GraphState = {"task_description": task_description, "blueprint": None, "generated_code": "", "failure_report": None, "status": WorkflowStatus.PLANNING, "retry_count": 0, "tokens_used": 0, "error_message": "", "memory_context": [], "memory_writes": [], "trace": [], "sandbox_result": None, "parsed_files": [], "tool_calls_log": []}
    invoke_config = {"recursion_limit": 25, **tools_config}
    if trace_config.callbacks:
        invoke_config["callbacks"] = trace_config.callbacks
    with trace_config.propagation_context():
        add_trace_event(trace_config, "orchestrator_start", metadata={"task_preview": task_description[:200], "max_retries": MAX_RETRIES, "token_budget": TOKEN_BUDGET, "tools_available": len(tools_config.get("configurable", {}).get("tools", []))})
        result = workflow.invoke(initial_state, config=invoke_config)
        final_state = AgentState(**result)
        add_trace_event(trace_config, "orchestrator_complete", metadata={"status": final_state.status.value, "tokens_used": final_state.tokens_used, "retry_count": final_state.retry_count, "memory_writes_count": len(final_state.memory_writes), "tool_calls_count": len(final_state.tool_calls_log)})
    trace_config.flush()
    return final_state
