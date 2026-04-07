"""LangGraph orchestrator -- Architect -> Lead Dev -> apply_code -> sandbox -> QA -> publish loop.

This is the main entry point for the agent workflow.
Implements the state machine with retry logic, token budgets,
structured Blueprint passing, human escalation, code application,
tool binding (issue #80), memory write-back, and PR publication (#89).

Issue #80: Agent tool binding -- Dev and QA agents can now use
workspace tools (filesystem_read, filesystem_write, etc.) via
LangChain's bind_tools() + iterative tool execution loop.
Tools are passed via RunnableConfig["configurable"]["tools"].

Issue #89: publish_code_node -- After QA passes, creates a branch,
pushes files, and opens a PR via GitHub REST API. Guard chain skips
gracefully when GITHUB_TOKEN is missing, no files exist, or user opts out.

Issue #105: Workspace security -- GraphState carries workspace_root,
apply_code_node uses per-task workspace instead of global env var.

Issue #125: Retry loop fix -- On retry, developer_node now receives
current file contents from disk, sandbox stdout, and a targeted
"fix, don't rewrite" system prompt. QA leniency for empty criteria.
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
from .agents.qa import FailureReport
from .memory.factory import create_memory_store
from .memory.protocol import MemoryStore
from .memory.summarizer import summarize_writes_sync
from .sandbox.e2b_runner import E2BRunner, SandboxResult
from .sandbox.validation_commands import (
    ValidationStrategy,
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
# Issue #125: Max chars of file content to inject into retry prompt
# to prevent context window exhaustion on large files.
MAX_RETRY_FILE_CHARS = _safe_int("MAX_RETRY_FILE_CHARS", 30000)


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
    workspace_root: str  # Issue #105: per-task workspace directory
    # Issue #89: PR publication fields
    publish_pr: bool  # Whether to create branch + PR after QA passes
    working_branch: str | None  # Branch created for this task
    pr_url: str | None  # URL of opened PR
    pr_number: int | None  # PR number


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
    workspace_root: str = ""  # Issue #105
    # Issue #89
    publish_pr: bool = True
    working_branch: str | None = None
    pr_url: str | None = None
    pr_number: int | None = None


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
        results = store.query(task_description, n_results=10, min_score=0.3)
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

DEV_TOOL_NAMES = {"filesystem_read", "filesystem_write", "filesystem_list", "github_read_diff"}
QA_TOOL_NAMES = {"filesystem_read", "filesystem_list", "github_read_diff"}

_SECRET_PATTERNS = [
    re.compile(r'(?:sk|pk|api|key|token|secret|password|bearer)[_-]?\w{10,}', re.IGNORECASE),
    re.compile(r'(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9_]{30,}'),
    re.compile(r'(?:eyJ)[A-Za-z0-9_-]{20,}'),
    re.compile(r'(?:AKIA|ASIA)[A-Z0-9]{16}'),
]


def _sanitize_preview(text: str, max_len: int = 200) -> str:
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
    tool_name = tool_call.get("name", "")
    tool_args = tool_call.get("args", {})
    tool_id = tool_call.get("id", "unknown")
    tool_map = {t.name: t for t in tools}
    tool = tool_map.get(tool_name)
    if not tool:
        return ToolMessage(content=f"Error: Tool '{tool_name}' not found. Available: {list(tool_map.keys())}", tool_call_id=tool_id)
    try:
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
            tool_calls_log.append({"agent": agent_name, "turn": turn + 1, "tool": tc.get("name", "unknown"), "args_preview": _sanitize_preview(str(tc.get("args", {}))), "result_preview": _sanitize_preview(str(tool_msg.content)), "success": not tool_msg.content.startswith("Error")})
    trace.append(f"{agent_name}: tool loop hit max turns ({max_turns})")
    logger.warning("[%s] Hit max tool turns (%d)", agent_name.upper(), max_turns)
    return response, tokens_used, tool_calls_log


def _run_async(coro):
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


# -- Issue #125: Retry Context Builder --


def _build_retry_file_context(
    failure_report: FailureReport,
    blueprint: Blueprint,
    workspace_root: Path,
    max_chars: int = MAX_RETRY_FILE_CHARS,
) -> str:
    """Read current file contents from disk for failed files.

    Only reads files listed in failure_report.failed_files to keep
    context budget manageable. Falls back to all target_files if
    failed_files is empty. Respects workspace_root boundary.

    Returns a formatted string block for injection into the retry prompt.
    """
    # Determine which files to include
    files_to_read = failure_report.failed_files or blueprint.target_files
    if not files_to_read:
        return ""

    parts = []
    total_chars = 0

    for file_path in files_to_read:
        # Security: resolve and validate path stays within workspace
        try:
            full_path = (workspace_root / file_path).resolve()
            if not full_path.is_relative_to(workspace_root):
                logger.warning(
                    "[RETRY] Path traversal blocked: %s", file_path
                )
                continue
        except (ValueError, OSError):
            continue

        if not full_path.is_file():
            parts.append(f"\n--- FILE: {file_path} (not found on disk) ---")
            continue

        try:
            content = full_path.read_text(encoding="utf-8")
        except Exception as e:
            parts.append(f"\n--- FILE: {file_path} (read error: {e}) ---")
            continue

        # Budget check: skip if adding this file would exceed limit
        if total_chars + len(content) > max_chars:
            parts.append(
                f"\n--- FILE: {file_path} (skipped: would exceed "
                f"{max_chars} char context budget) ---"
            )
            continue

        parts.append(f"\n--- FILE: {file_path} ---\n{content}")
        total_chars += len(content)

    if not parts:
        return ""

    return "\n\nCURRENT FILES ON DISK (your previous output):\n" + "\n".join(parts)


def _build_retry_sandbox_context(sandbox_result: SandboxResult | None) -> str:
    """Format sandbox execution output for the retry prompt."""
    if sandbox_result is None:
        return ""

    parts = ["\n\nSANDBOX EXECUTION RESULTS (what your code actually produced):"]
    parts.append(f"  Exit code: {sandbox_result.exit_code}")

    if sandbox_result.tests_passed is not None:
        parts.append(f"  Tests passed: {sandbox_result.tests_passed}")
    if sandbox_result.tests_failed is not None:
        parts.append(f"  Tests failed: {sandbox_result.tests_failed}")
    if sandbox_result.errors:
        parts.append(f"  Errors: {', '.join(sandbox_result.errors)}")
    if sandbox_result.output_summary:
        parts.append(f"  Output:\n{sandbox_result.output_summary}")

    return "\n".join(parts)


# -- Node Functions --

async def architect_node(state: GraphState) -> dict:
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
    response = await llm.ainvoke([SystemMessage(content=system_prompt), HumanMessage(content=user_msg)])
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


async def developer_node(state: GraphState, config: RunnableConfig | None = None) -> dict:
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

    # Issue #125: Detect retry vs first attempt
    failure_report = state.get("failure_report")
    is_retry = failure_report is not None and not failure_report.is_architectural

    tools = _get_agent_tools(config, DEV_TOOL_NAMES)
    has_tools = len(tools) > 0

    # -- Build system prompt (different for retry vs first attempt) --
    if is_retry:
        # Issue #125: Retry-specific system prompt
        system_prompt = """You are the Lead Dev agent. You previously implemented this Blueprint but QA found issues.

IMPORTANT RETRY RULES:
1. Your previous code is shown below under "CURRENT FILES ON DISK". READ IT CAREFULLY.
2. The QA failure report describes EXACTLY what is wrong.
3. Apply the MINIMUM change needed to fix the reported issue.
4. Do NOT rewrite files from scratch. Only modify the specific lines that need fixing.
5. If a fix hint is provided, follow it precisely.
"""
        if has_tools:
            system_prompt += """\nYou have workspace tools available. Use filesystem_read to verify the current state if needed,
then use filesystem_write to apply your targeted fix.
After writing, provide a summary of ONLY what you changed.

Also include the complete fixed code using the format:
# --- FILE: path/to/file.py ---
(file contents)"""
        else:
            system_prompt += """\nRespond with ONLY the fixed code. Include file paths as comments:
# --- FILE: path/to/file.py ---

Only output files that need changes. Do not repeat unchanged files."""
    elif has_tools:
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
        system_prompt = """You are the Lead Dev agent. You receive a structured Blueprint
and write the code to implement it.

Respond with the complete code implementation. Include file paths as comments
at the top of each file section, like:
# --- FILE: path/to/file.py ---

Follow the Blueprint exactly. Respect all constraints.
Write clean, well-documented code."""

    # -- Build user message --
    user_msg = f"Blueprint:\n{blueprint.model_dump_json(indent=2)}"

    if is_retry:
        # Issue #125: Structured retry context
        user_msg += "\n\n" + "=" * 60
        user_msg += f"\nRETRY CONTEXT (attempt #{retry_count + 1})\n"
        user_msg += "=" * 60

        # QA failure details
        user_msg += "\n\nQA FAILURE REPORT:"
        user_msg += f"\n  Tests passed: {failure_report.tests_passed}"
        user_msg += f"\n  Tests failed: {failure_report.tests_failed}"
        user_msg += f"\n  Errors: {', '.join(failure_report.errors)}"
        user_msg += f"\n  Failed files: {', '.join(failure_report.failed_files)}"
        user_msg += f"\n  Recommendation: {failure_report.recommendation}"

        # Issue #125: Fix complexity and hint
        if failure_report.fix_complexity:
            user_msg += f"\n  Fix complexity: {failure_report.fix_complexity}"
        if failure_report.exact_fix_hint:
            user_msg += f"\n\n  EXACT FIX HINT: {failure_report.exact_fix_hint}"

        # Issue #125: Current file contents from disk
        ws_from_state = state.get("workspace_root", "")
        workspace_root = (
            Path(ws_from_state).resolve() if ws_from_state
            else _get_workspace_root()
        )
        file_context = _build_retry_file_context(
            failure_report, blueprint, workspace_root
        )
        if file_context:
            user_msg += file_context
            trace.append(
                f"developer: retry -- injected file context "
                f"({len(file_context)} chars)"
            )
        else:
            trace.append("developer: retry -- no file context available")

        # Issue #125: Sandbox output
        sandbox_result = state.get("sandbox_result")
        sandbox_context = _build_retry_sandbox_context(sandbox_result)
        if sandbox_context:
            user_msg += sandbox_context
            trace.append("developer: retry -- injected sandbox output")

    messages = [SystemMessage(content=system_prompt), HumanMessage(content=user_msg)]
    llm = _get_developer_llm()
    if has_tools:
        llm_with_tools = llm.bind_tools(tools)
        response, tokens_used, new_tool_log = await _run_tool_loop(llm_with_tools, messages, tools, max_turns=MAX_TOOL_TURNS, tokens_used=tokens_used, trace=trace, agent_name="developer")
        tool_calls_log.extend(new_tool_log)
    else:
        response = await llm.ainvoke(messages)
        tokens_used += _extract_token_count(response)
    content = _extract_text_content(response.content)

    # -- Recover generated_code from tool-written files --
    # When the developer uses filesystem_write tools, the final LLM response
    # often contains only a summary (or empty text). But the actual code is
    # on disk. Read it back so downstream nodes (apply_code, QA) see it.
    if not content.strip() and has_tools:
        wrote_files = [
            tc for tc in tool_calls_log
            if tc.get("tool") == "filesystem_write"
            and tc.get("success")
            and tc.get("agent") == "developer"
        ]
        if wrote_files and blueprint.target_files:
            ws_from_state = state.get("workspace_root", "")
            ws_root = (
                Path(ws_from_state).resolve() if ws_from_state
                else _get_workspace_root()
            )
            recovered_parts = []
            for target_path in blueprint.target_files:
                full_path = ws_root / target_path
                if full_path.is_file():
                    try:
                        file_content = full_path.read_text(encoding="utf-8")
                        recovered_parts.append(
                            f"# --- FILE: {target_path} ---\n{file_content}"
                        )
                    except Exception as e:
                        logger.warning(
                            "[DEV] Failed to recover %s from disk: %s",
                            target_path, e,
                        )
            if recovered_parts:
                content = "\n\n".join(recovered_parts)
                trace.append(
                    f"developer: recovered {len(recovered_parts)} file(s) "
                    f"from disk ({len(content)} chars) -- "
                    f"LLM text was empty after tool use"
                )
                logger.info(
                    "[DEV] Recovered %d files from disk (LLM text was empty)",
                    len(recovered_parts),
                )

    trace.append(f"developer: code generated ({len(content)} chars)")
    logger.info("[DEV] done. tokens_used now=%d", tokens_used)
    # Deduplicate: on retries, replace existing developer entry for this task_id
    # rather than appending duplicates (trace review showed 3x identical entries).
    new_entry = {"content": f"Implemented blueprint {blueprint.task_id}: {blueprint.instructions[:200]}", "tier": "l1", "module": _infer_module(blueprint.target_files), "source_agent": "developer", "confidence": 1.0, "sandbox_origin": "locked-down", "related_files": ",".join(blueprint.target_files), "task_id": blueprint.task_id}
    replaced = False
    for i, existing in enumerate(memory_writes):
        if existing.get("task_id") == blueprint.task_id and existing.get("source_agent") == "developer":
            memory_writes[i] = new_entry
            replaced = True
            break
    if not replaced:
        memory_writes.append(new_entry)
    return {"generated_code": content, "status": WorkflowStatus.REVIEWING, "tokens_used": tokens_used, "trace": trace, "memory_writes": memory_writes, "tool_calls_log": tool_calls_log}


def apply_code_node(state: GraphState) -> dict:
    trace = list(state.get("trace", []))
    trace.append("apply_code: starting")
    generated_code = state.get("generated_code", "")
    blueprint = state.get("blueprint")
    if not blueprint:
        trace.append("apply_code: no blueprint -- skipping")
        return {"parsed_files": [], "trace": trace}

    # Issue #105: Use per-task workspace from GraphState, fall back to env var
    ws_from_state = state.get("workspace_root", "")
    workspace_root = Path(ws_from_state).resolve() if ws_from_state else _get_workspace_root()

    # When the developer used filesystem_write tools, the clean code is already
    # on disk. Reading from disk avoids re-parsing the LLM's summary text which
    # often contains markdown fences and prose that corrupt the code.
    # Trace review (task-ba22eafa) showed 2 wasted retries from this mismatch.
    tool_calls_log = state.get("tool_calls_log", [])
    wrote_via_tools = any(
        tc.get("tool") == "filesystem_write" and tc.get("success")
        for tc in tool_calls_log
    )

    if wrote_via_tools and blueprint.target_files:
        disk_files = []
        for target_path in blueprint.target_files:
            full_path = workspace_root / target_path
            if full_path.is_file():
                try:
                    content = full_path.read_text(encoding="utf-8")
                    disk_files.append({"path": target_path, "content": content})
                except Exception as e:
                    logger.warning("[APPLY_CODE] Failed to read tool-written file %s: %s", target_path, e)
                    trace.append(f"apply_code: failed to read {target_path} from disk -- {e}")

        if disk_files:
            total_chars = sum(len(f["content"]) for f in disk_files)
            trace.append(f"apply_code: read {len(disk_files)} file(s) from disk (tool-written, {total_chars:,} chars total)")
            logger.info("[APPLY_CODE] Read %d tool-written files (%d chars) from %s", len(disk_files), total_chars, workspace_root)
            return {"parsed_files": disk_files, "trace": trace}

        trace.append("apply_code: tool-written files not found on disk, falling back to parser")

    # Fallback: parse generated_code (single-shot mode or disk read failed)
    if not generated_code:
        trace.append("apply_code: no generated_code and no tool-written files on disk -- skipping")
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
    trace.append(f"apply_code: wrote {written_count} files ({total_chars:,} chars total) to {workspace_root}")
    logger.info("[APPLY_CODE] Wrote %d files (%d chars) to %s", written_count, total_chars, workspace_root)
    parsed_files_data = [{"path": pf.path, "content": pf.content} for pf in safe_files]
    return {"parsed_files": parsed_files_data, "trace": trace}


async def qa_node(state: GraphState, config: RunnableConfig | None = None) -> dict:
    trace = list(state.get("trace", []))
    trace.append("qa: starting review")
    memory_writes = list(state.get("memory_writes", []))
    tool_calls_log = list(state.get("tool_calls_log", []))
    retry_count = state.get("retry_count", 0)
    tokens_used = state.get("tokens_used", 0)
    logger.info("[QA] retry_count=%d, tokens_used=%d, status=%s", retry_count, tokens_used, state.get("status", "unknown"))
    generated_code = state.get("generated_code", "")
    blueprint = state.get("blueprint")
    if not blueprint:
        trace.append("qa: missing blueprint")
        return {"status": WorkflowStatus.FAILED, "error_message": "QA received no blueprint to review", "trace": trace}

    # If generated_code is empty but developer wrote files via tools,
    # recover from disk so QA can still review the actual code.
    if not generated_code:
        tool_calls_log_state = state.get("tool_calls_log", [])
        wrote_via_tools = any(
            tc.get("tool") == "filesystem_write" and tc.get("success")
            for tc in tool_calls_log_state
        )
        if wrote_via_tools and blueprint.target_files:
            ws_from_state = state.get("workspace_root", "")
            ws_root = (
                Path(ws_from_state).resolve() if ws_from_state
                else _get_workspace_root()
            )
            recovered_parts = []
            for target_path in blueprint.target_files:
                full_path = ws_root / target_path
                if full_path.is_file():
                    try:
                        file_content = full_path.read_text(encoding="utf-8")
                        recovered_parts.append(
                            f"# --- FILE: {target_path} ---\n{file_content}"
                        )
                    except Exception:
                        pass
            if recovered_parts:
                generated_code = "\n\n".join(recovered_parts)
                trace.append(
                    f"qa: recovered {len(recovered_parts)} file(s) from "
                    f"disk for review (generated_code was empty)"
                )

        if not generated_code:
            trace.append("qa: missing code (no generated_code and no tool-written files)")
            return {"status": WorkflowStatus.FAILED, "error_message": "QA received no code or blueprint to review", "trace": trace}
    tools = _get_agent_tools(config, QA_TOOL_NAMES)
    has_tools = len(tools) > 0
    if has_tools:
        tool_names = [t.name for t in tools]
        trace.append(f"qa: {len(tools)} tools available: {', '.join(tool_names)}")
    system_prompt = "You are the QA agent. You review code against a Blueprint's acceptance criteria.\n\n"
    if has_tools:
        system_prompt += "You have tools to read files from the workspace. Use filesystem_read to inspect the actual files that were written, and filesystem_list to check the project structure.\n\n"
    system_prompt += 'Respond with ONLY a valid JSON object matching this schema:\n{\n  "task_id": "string (from the Blueprint)",\n  "status": "pass" or "fail" or "escalate",\n  "tests_passed": number,\n  "tests_failed": number,\n  "errors": ["list of specific error descriptions"],\n  "failed_files": ["list of files with issues"],\n  "is_architectural": true/false,\n  "failure_type": "code" or "architectural" or null (if pass),\n  "fix_complexity": "trivial" or "moderate" or "complex" or null (if pass),\n  "exact_fix_hint": "specific instruction for what to change, e.g. Line 5: add 6 spaces before /\\\\" or null,\n  "recommendation": "what to fix or why it should escalate"\n}\n\nFAILURE CLASSIFICATION (critical for correct routing):\n\nSet failure_type to "code" (status: "fail") when:\n- Implementation has bugs, syntax errors, or type errors\n- Tests fail due to logic errors in the code\n- Code does not follow the Blueprint\'s constraints\n- Missing error handling or edge cases\nAction: Lead Dev will retry with the same Blueprint.\n\nSet failure_type to "architectural" (status: "escalate") when:\n- Blueprint targets the WRONG files (code is in the wrong place)\n- A required dependency or import is missing from the Blueprint\n- The design approach is fundamentally flawed\n- Acceptance criteria are impossible to meet with current targets\n- The task requires files not listed in target_files\nAction: Architect will generate a completely NEW Blueprint.\n\nFIX COMPLEXITY CLASSIFICATION (helps Lead Dev on retry):\n- "trivial": One-line fix (spacing, typo, missing import, off-by-one error)\n- "moderate": Multi-line fix within the same function or code block\n- "complex": Structural changes across multiple functions or files\n\nEXACT FIX HINT: When fix_complexity is "trivial" or "moderate", provide a specific\ninstruction describing EXACTLY what to change (e.g., "Line 1: add 6 leading spaces\nbefore the /\\\\ character" or "Add `import os` at line 3"). This helps the Lead Dev\nmake a targeted fix instead of rewriting the entire file.\n\nBe strict but fair. Only pass code that meets ALL acceptance criteria.\nDo not include any text before or after the JSON.'

    # Issue #125: QA leniency when acceptance criteria are empty
    if not blueprint.acceptance_criteria:
        system_prompt += """\n\nIMPORTANT - NO ACCEPTANCE CRITERIA PROVIDED:
The user did not specify acceptance criteria for this task. In this case:
1. Prioritize FUNCTIONAL correctness: the code runs without errors.
2. If the sandbox executed successfully (exit_code=0), bias toward PASS.
3. Do NOT invent strict formatting or cosmetic requirements.
4. Only FAIL for genuine bugs, syntax errors, or logic errors.
5. Subjective quality issues (indentation style, variable naming) are NOT failures."""

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
        response, tokens_used, new_tool_log = await _run_tool_loop(llm_with_tools, messages, tools, max_turns=5, tokens_used=tokens_used, trace=trace, agent_name="qa")
        tool_calls_log.extend(new_tool_log)
    else:
        response = await llm.ainvoke(messages)
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


def _run_sandbox_tests(commands, template, parsed_files=None, timeout=120):
    api_key = os.getenv("E2B_API_KEY")
    if not api_key:
        return None
    runner = E2BRunner(api_key=api_key, default_timeout=timeout)
    project_files = None
    if parsed_files:
        project_files = {pf["path"]: pf["content"] for pf in parsed_files}
    return runner.run_tests(commands=commands, project_files=project_files, timeout=timeout, template=template)


def _run_sandbox_script(script_file, template, parsed_files=None, timeout=30):
    api_key = os.getenv("E2B_API_KEY")
    if not api_key:
        return None
    runner = E2BRunner(api_key=api_key, default_timeout=timeout)
    project_files = None
    if parsed_files:
        project_files = {pf["path"]: pf["content"] for pf in parsed_files}
    return runner.run_script(script_file=script_file, project_files=project_files, timeout=timeout, template=template)


def sandbox_validate_node(state: GraphState) -> dict:
    trace = list(state.get("trace", []))
    trace.append("sandbox_validate: starting")
    blueprint = state.get("blueprint")
    if not blueprint:
        trace.append("sandbox_validate: no blueprint -- skipping")
        return {"sandbox_result": None, "trace": trace}
    plan = get_validation_plan(blueprint.target_files)
    trace.append(f"sandbox_validate: {plan.description}")
    if plan.strategy == ValidationStrategy.SKIP:
        trace.append("sandbox_validate: no code validation needed -- skipping")
        return {"sandbox_result": None, "trace": trace}
    parsed_files = state.get("parsed_files", [])
    if plan.strategy in {ValidationStrategy.SCRIPT_EXEC, ValidationStrategy.TEST_SUITE, ValidationStrategy.LINT_ONLY}:
        if not parsed_files:
            trace.append("sandbox_validate: no parsed files available -- skipping")
            return {"sandbox_result": None, "trace": trace}
    trace.append(f"sandbox_validate: loading {len(parsed_files)} files into sandbox")
    template_label = plan.template or "default"
    trace.append(f"sandbox_validate: strategy={plan.strategy.value}, template={template_label}")
    try:
        result = None
        if plan.strategy == ValidationStrategy.SCRIPT_EXEC:
            trace.append(f"sandbox_validate: executing script {plan.script_file}")
            result = _run_sandbox_script(script_file=plan.script_file, template=plan.template, parsed_files=parsed_files if parsed_files else None)
        elif plan.strategy in {ValidationStrategy.TEST_SUITE, ValidationStrategy.LINT_ONLY}:
            trace.append(f"sandbox_validate: running {len(plan.commands)} command(s) sequentially")
            result = _run_sandbox_tests(commands=plan.commands, template=plan.template, parsed_files=parsed_files if parsed_files else None)
        else:
            trace.append(f"sandbox_validate: unhandled strategy {plan.strategy.value} -- skipping")
            return {"sandbox_result": None, "trace": trace}
        if result is None:
            logger.warning("[SANDBOX] E2B_API_KEY not configured -- sandbox validation SKIPPED.")
            trace.append("sandbox_validate: WARNING -- E2B_API_KEY not configured, sandbox validation skipped")
            return {"sandbox_result": None, "trace": trace}
        if result.warnings:
            for w in result.warnings:
                trace.append(f"sandbox_validate: WARNING -- {w}")
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


# -- Publish Code Node (Issue #89) --


async def _publish_code_async(state: dict) -> dict:
    from .api.github_prs import github_pr_provider

    trace = list(state.get("trace", []))
    trace.append("publish_code: starting")

    if not github_pr_provider.configured:
        trace.append("publish_code: skipped -- no GITHUB_TOKEN configured")
        logger.info("[PUBLISH] Skipped: no GITHUB_TOKEN")
        return {"trace": trace}

    publish_pr = state.get("publish_pr", True)
    if not publish_pr:
        trace.append("publish_code: skipped -- publish_pr=False (user opted out)")
        logger.info("[PUBLISH] Skipped: publish_pr=False")
        return {"trace": trace}

    parsed_files = state.get("parsed_files", [])
    if not parsed_files:
        trace.append("publish_code: skipped -- no parsed_files")
        logger.info("[PUBLISH] Skipped: no parsed_files")
        return {"trace": trace}

    blueprint = state.get("blueprint")
    if not blueprint:
        trace.append("publish_code: skipped -- no blueprint")
        logger.info("[PUBLISH] Skipped: no blueprint")
        return {"trace": trace}

    task_id = blueprint.task_id
    branch_name = f"agent/{task_id}"
    branch_name = re.sub(r"[^a-zA-Z0-9/_-]", "-", branch_name)

    try:
        trace.append(f"publish_code: creating branch '{branch_name}'")
        branch_ok = await github_pr_provider.create_branch(branch_name)
        if not branch_ok:
            trace.append("publish_code: FAILED to create branch")
            logger.error("[PUBLISH] Failed to create branch '%s'", branch_name)
            return {"trace": trace}

        trace.append(f"publish_code: pushing {len(parsed_files)} file(s)")
        workspace_root = state.get("workspace_root", "")
        repo_subdir = ""
        if workspace_root:
            ws = Path(workspace_root).resolve()
            for parent in [ws, *ws.parents]:
                if (parent / ".git").exists():
                    try:
                        repo_subdir = str(ws.relative_to(parent))
                    except ValueError:
                        repo_subdir = ""
                    break

        files_payload = []
        for pf in parsed_files:
            repo_path = pf["path"]
            if repo_subdir and repo_subdir != ".":
                repo_path = f"{repo_subdir}/{pf['path']}"
            files_payload.append({"path": repo_path, "content": pf["content"]})
        push_ok = await github_pr_provider.push_files_batch(
            files=files_payload, branch=branch_name,
            message=f"feat({task_id}): implement {blueprint.instructions[:60]}",
        )
        if not push_ok:
            trace.append("publish_code: FAILED to push files")
            logger.error("[PUBLISH] Failed to push files to '%s'", branch_name)
            return {"working_branch": branch_name, "trace": trace}

        criteria_lines = [f"- [x] {ac}" for ac in blueprint.acceptance_criteria]
        criteria_block = "\n".join(criteria_lines) if criteria_lines else "_No criteria specified._"

        sandbox_result = state.get("sandbox_result")
        test_summary = ""
        if sandbox_result is not None:
            passed = getattr(sandbox_result, "tests_passed", None)
            failed = getattr(sandbox_result, "tests_failed", None)
            if passed is not None or failed is not None:
                test_summary = f"\n\n### Test Results\n- Passed: {passed or 0}\n- Failed: {failed or 0}\n"

        pr_body = (
            f"## Task: `{task_id}`\n\n"
            f"{blueprint.instructions}\n\n"
            f"### Acceptance Criteria\n{criteria_block}"
            f"{test_summary}\n\n"
            f"### Files Changed\n"
            + "\n".join(f"- `{fp['path']}`" for fp in files_payload)
            + "\n\n---\n_Opened automatically by the agent orchestrator._"
        )

        pr_title = f"feat({task_id}): {blueprint.instructions[:80]}"
        if len(blueprint.instructions) > 80:
            pr_title = pr_title[:83] + "..."

        trace.append("publish_code: opening PR")
        pr_result = await github_pr_provider.create_pr(
            head=branch_name, base="main", title=pr_title, body=pr_body,
        )

        if pr_result:
            trace.append(f"publish_code: PR #{pr_result.number} opened -> {pr_result.id}")
            logger.info("[PUBLISH] PR #%d opened: %s", pr_result.number, pr_title)
            return {
                "working_branch": branch_name,
                "pr_url": f"https://github.com/{github_pr_provider.owner}/{github_pr_provider.repo}/pull/{pr_result.number}",
                "pr_number": pr_result.number,
                "trace": trace,
            }
        else:
            trace.append("publish_code: FAILED to open PR (files pushed to branch)")
            logger.error("[PUBLISH] Failed to open PR (files are on branch '%s')", branch_name)
            return {"working_branch": branch_name, "trace": trace}

    except Exception as e:
        trace.append(f"publish_code: error -- {type(e).__name__}: {e}")
        logger.error("[PUBLISH] Unexpected error: %s", e, exc_info=True)
        return {"trace": trace}


async def publish_code_node(state: GraphState) -> dict:
    """Push files to GitHub branch and open PR after QA passes."""
    return await _publish_code_async(state)


def route_after_qa(state: GraphState) -> Literal["publish_code", "flush_memory", "developer", "architect", "__end__"]:
    status = state.get("status", WorkflowStatus.FAILED)
    retry_count = state.get("retry_count", 0)
    tokens_used = state.get("tokens_used", 0)
    logger.info("[ROUTER] status=%s, retry_count=%d, tokens_used=%d, max_retries=%d, token_budget=%d", status, retry_count, tokens_used, MAX_RETRIES, TOKEN_BUDGET)
    if status == WorkflowStatus.PASSED:
        return "publish_code"
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
    graph.add_node("publish_code", publish_code_node)
    graph.add_node("flush_memory", flush_memory_node)
    graph.add_edge(START, "architect")
    graph.add_edge("architect", "developer")
    graph.add_edge("developer", "apply_code")
    graph.add_edge("apply_code", "sandbox_validate")
    graph.add_edge("sandbox_validate", "qa")
    graph.add_conditional_edges("qa", route_after_qa)
    graph.add_edge("publish_code", "flush_memory")
    graph.add_edge("flush_memory", END)
    return graph


def create_workflow():
    return build_graph().compile()


def _get_mcp_config_path() -> Path:
    explicit = os.getenv("MCP_CONFIG_PATH")
    if explicit:
        p = Path(explicit).expanduser()
        if not p.is_absolute():
            p = Path(__file__).resolve().parent.parent / p
        return p.resolve()
    return Path(__file__).resolve().parent.parent / "mcp-config.json"


def init_tools_config(workspace_root=None):
    if workspace_root is None:
        workspace_root = _get_workspace_root()
    try:
        from .tools import create_provider, get_tools, load_mcp_config
        config_path = _get_mcp_config_path()
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
    publish_pr = bool(os.getenv("GITHUB_TOKEN"))
    initial_state: GraphState = {"task_description": task_description, "blueprint": None, "generated_code": "", "failure_report": None, "status": WorkflowStatus.PLANNING, "retry_count": 0, "tokens_used": 0, "error_message": "", "memory_context": [], "memory_writes": [], "trace": [], "sandbox_result": None, "parsed_files": [], "tool_calls_log": [], "publish_pr": publish_pr}
    invoke_config = {"recursion_limit": 25, **tools_config}
    if trace_config.callbacks:
        invoke_config["callbacks"] = trace_config.callbacks
    with trace_config.propagation_context():
        add_trace_event(trace_config, "orchestrator_start", metadata={"task_preview": task_description[:200], "max_retries": MAX_RETRIES, "token_budget": TOKEN_BUDGET, "tools_available": len(tools_config.get("configurable", {}).get("tools", []))})
        result = asyncio.run(workflow.ainvoke(initial_state, config=invoke_config))
        final_state = AgentState(**result)
        add_trace_event(trace_config, "orchestrator_complete", metadata={"status": final_state.status.value, "tokens_used": final_state.tokens_used, "retry_count": final_state.retry_count, "memory_writes_count": len(final_state.memory_writes), "tool_calls_count": len(final_state.tool_calls_log)})
    trace_config.flush()
    return final_state
