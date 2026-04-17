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

from .agents.architect import Blueprint, TaskDecomposition
from .agents.qa import FailureReport
from .memory.factory import create_memory_store
from .memory.protocol import MemoryStore
from .memory.summarizer import summarize_writes_sync
from .sandbox.e2b_runner import E2BRunner, SandboxResult
from .sandbox.project_runner import (
    ProjectValidationRunner,
    load_project_validation_config,
)
from .sandbox.validation_commands import (
    ValidationStrategy,
    get_validation_plan,
)
from .tools.code_parser import (
    CodeParserError,
    parse_generated_code,
    validate_paths_for_workspace,
)
from .tools.github_fetch import extract_github_refs, fetch_issue_or_pr
from .tools.mcp_bridge import READONLY_TOOLS
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
# Issue #193 PR 3: Architect's Phase 2 tool-loop cap. Architect only
# needs a handful of read-only probes (list → read → maybe read again)
# to disambiguate target_files, so we keep this tight vs. Developer's
# MAX_TOOL_TURNS.
MAX_ARCHITECT_TOOL_TURNS = _safe_int("MAX_ARCHITECT_TOOL_TURNS", 4)
MAX_RETRY_FILE_CHARS = _safe_int("MAX_RETRY_FILE_CHARS", 30000)
CONTEXT_BUDGET_CHARS = _safe_int("CONTEXT_BUDGET_CHARS", 120000)  # ~30k tokens
CONTEXT_FILE_MAX_LINES = _safe_int("CONTEXT_FILE_MAX_LINES", 500)
CONTEXT_FILE_TAIL_LINES = _safe_int("CONTEXT_FILE_TAIL_LINES", 50)
DECOMPOSE_FILE_THRESHOLD = _safe_int("DECOMPOSE_FILE_THRESHOLD", 5)
DECOMPOSE_DIR_THRESHOLD = _safe_int("DECOMPOSE_DIR_THRESHOLD", 2)


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
    workspace_root: str
    create_pr: bool
    workspace_type: str
    github_repo: str | None
    github_branch: str | None
    github_feature_branch: str | None
    working_branch: str | None
    pr_url: str | None
    pr_number: int | None
    gathered_context: list[dict] | None
    prefetched_gathered_context: list[dict] | None
    decomposition: TaskDecomposition | None
    current_subtask_index: int
    completed_subtasks: list[dict]


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
    workspace_root: str = ""
    create_pr: bool = True
    workspace_type: str = "local"
    github_repo: str | None = None
    github_branch: str | None = None
    github_feature_branch: str | None = None
    working_branch: str | None = None
    pr_url: str | None = None
    pr_number: int | None = None
    gathered_context: list[dict] | None = None
    prefetched_gathered_context: list[dict] | None = None
    decomposition: TaskDecomposition | None = None
    current_subtask_index: int = 0
    completed_subtasks: list[dict] = []


# -- LLM Provider Auto-Detection --


def _detect_provider(model: str) -> str:
    """Detect LLM provider from model name prefix.

    Returns "anthropic", "google", or raises ValueError for unknown models.
    """
    lower = model.lower()
    if lower.startswith("claude") or lower.startswith("anthropic"):
        return "anthropic"
    if lower.startswith("gemini") or lower.startswith("models/gemini"):
        return "google"
    raise ValueError(
        f"Cannot detect provider for model '{model}'. "
        f"Model name must start with 'claude'/'anthropic' (Anthropic) "
        f"or 'gemini' (Google). Check your .env configuration."
    )


def _create_llm(model: str, *, temperature: float = 0.2, max_tokens: int | None = None):
    """Create a LangChain LLM instance, auto-detecting provider from model name.

    Supports Anthropic (claude-*) and Google (gemini-*) models interchangeably.
    Provider is inferred from the model name prefix — no separate PROVIDER env var needed.
    """
    provider = _detect_provider(model)
    if provider == "anthropic":
        kwargs = {
            "model": model,
            "api_key": os.getenv("ANTHROPIC_API_KEY"),
            "temperature": temperature,
        }
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        else:
            # Anthropic requires max_tokens; use a sensible default
            kwargs["max_tokens"] = 8192
        return ChatAnthropic(**kwargs)
    else:
        # Google Gemini — max_tokens not supported the same way
        kwargs = {
            "model": model,
            "google_api_key": os.getenv("GOOGLE_API_KEY"),
            "temperature": temperature,
        }
        if max_tokens is not None:
            kwargs["max_output_tokens"] = max_tokens
        return ChatGoogleGenerativeAI(**kwargs)


def _get_architect_llm():
    model = os.getenv("ARCHITECT_MODEL", "gemini-3-flash-preview")
    return _create_llm(model, temperature=0.2)


def _get_developer_llm():
    model = os.getenv("DEVELOPER_MODEL", "claude-sonnet-4-20250514")
    return _create_llm(model, temperature=0.1, max_tokens=8192)


def _get_qa_llm():
    model = os.getenv("QA_MODEL", "claude-sonnet-4-20250514")
    return _create_llm(model, temperature=0.0, max_tokens=4096)


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


DEV_TOOL_NAMES = {"filesystem_read", "filesystem_write", "filesystem_patch", "filesystem_list", "github_read_diff"}
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


def _build_retry_file_context(failure_report: FailureReport, blueprint: Blueprint, workspace_root: Path, max_chars: int = MAX_RETRY_FILE_CHARS) -> str:
    files_to_read = failure_report.failed_files or blueprint.target_files
    if not files_to_read:
        return ""
    parts = []
    total_chars = 0
    for file_path in files_to_read:
        try:
            full_path = (workspace_root / file_path).resolve()
            if not full_path.is_relative_to(workspace_root):
                logger.warning("[RETRY] Path traversal blocked: %s", file_path)
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
        if total_chars + len(content) > max_chars:
            parts.append(f"\n--- FILE: {file_path} (skipped: would exceed {max_chars} char context budget) ---")
            continue
        parts.append(f"\n--- FILE: {file_path} ---\n{content}")
        total_chars += len(content)
    if not parts:
        return ""
    return "\n\nCURRENT FILES ON DISK (your previous output):\n" + "\n".join(parts)


def _build_retry_sandbox_context(sandbox_result: SandboxResult | None) -> str:
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


# -- Context Gathering (Issue #158) --

# File extensions considered relevant for auto-inference.
_CODE_EXTENSIONS = {
    ".py", ".ts", ".js", ".svelte", ".css", ".json", ".toml", ".yaml", ".yml",
    ".md", ".html", ".jsx", ".tsx", ".sh",
}
# Directories always skipped during auto-inference.
_SKIP_DIRS = {
    ".git", "__pycache__", "node_modules", ".venv", "venv", ".tox",
    ".mypy_cache", ".pytest_cache", "dist", "build", ".svelte-kit",
    ".next", "coverage", ".ruff_cache", "htmlcov", ".eggs", "egg-info",
}
# Filenames never read (secrets / large binaries).
_SKIP_FILES = {
    ".env", ".env.local", ".env.production", ".env.development",
}
_SKIP_EXTENSIONS = {".pem", ".key", ".p12", ".pfx", ".sqlite", ".db", ".lock"}


def _estimate_tokens(text: str) -> int:
    """Rough token estimate: ~4 chars per token."""
    return len(text) // 4


def _truncate_file(content: str, max_lines: int, tail_lines: int) -> tuple[str, bool]:
    """Truncate a large file, keeping head + tail with a marker."""
    lines = content.splitlines(keepends=True)
    if len(lines) <= max_lines:
        return content, False
    head = lines[:max_lines - tail_lines]
    tail = lines[-tail_lines:]
    skipped = len(lines) - (max_lines - tail_lines) - tail_lines
    marker = f"\n[... {skipped} lines truncated ...]\n\n"
    return "".join(head) + marker + "".join(tail), True


_PATH_PATTERN = re.compile(
    r"[A-Za-z0-9_./\\-]+\.(?:py|svelte|ts|tsx|js|jsx|css|md|toml|json|yaml|yml|html)\b"
)


def _find_repo_root(start: Path) -> Path:
    """Walk parent directories looking for a .git entry.

    Returns the first ancestor containing .git, or `start` itself if
    none is found. Used to let context gathering reach sibling
    directories (e.g., dashboard/ when running from dev-suite/).
    """
    start = start.resolve()
    for candidate in (start, *start.parents):
        if (candidate / ".git").exists():
            return candidate
    return start


def _resolve_target_path(
    target_path: str, workspace_root: Path, repo_root: Path
) -> Path:
    """Resolve a Blueprint target_path against the correct root.

    Mirrors LocalToolProvider's _resolve_path_smart so the orchestrator's
    read-back of tool-written files finds the same file the agent wrote
    via filesystem_patch/filesystem_write. Required when workspace_root
    is a monorepo subfolder (e.g., dev-suite/) and target_path points
    to a sibling dir (e.g., dashboard/foo.svelte) -- the tool wrote it
    at repo_root/dashboard/foo.svelte, and we need to read from the
    same place instead of a ghost under workspace_root/dashboard/.

    Prefers workspace-relative (preserves existing semantics for paths
    that are genuinely under the workspace). Falls back to repo-relative
    when the first segment is a top-level repo dir not present in the
    workspace.
    """
    workspace_root = workspace_root.resolve()
    repo_root = repo_root.resolve()
    if workspace_root == repo_root:
        return (workspace_root / target_path).resolve()
    parts = Path(target_path).parts
    if parts:
        first = parts[0]
        in_workspace = (workspace_root / first).exists()
        in_repo = (repo_root / first).exists()
        if not in_workspace and in_repo:
            return (repo_root / target_path).resolve()
    return (workspace_root / target_path).resolve()


def _extract_file_paths_from_text(text: str) -> list[str]:
    """Extract file-path-looking tokens from free-form task text.

    Matches tokens ending in a known code/config extension and
    containing at least one path separator (so plain filenames like
    `README.md` without context aren't over-matched). Returns paths
    in order of first appearance with duplicates removed.
    """
    if not text:
        return []

    seen: set[str] = set()
    results: list[str] = []
    for match in _PATH_PATTERN.finditer(text):
        raw = match.group(0).strip(".,;:)('\"`")
        # Require at least one separator to look like a path, not a bare filename
        if "/" not in raw and "\\" not in raw:
            continue
        normalized = raw.replace("\\", "/")
        if normalized not in seen:
            seen.add(normalized)
            results.append(normalized)
    return results


def _infer_relevant_files(workspace_root: Path, task_description: str) -> list[Path]:
    """Auto-infer files relevant to the task from workspace directory structure.

    Strategy:
    1. Extract keywords from task description (3+ char words)
    2. Walk workspace looking for files whose path contains a keyword
    3. Always include project manifests (pyproject.toml, package.json)
    """
    if not workspace_root.is_dir():
        return []

    # Extract meaningful keywords from task (lowercase, 3+ chars, no stopwords)
    stopwords = {"the", "and", "for", "that", "this", "with", "from", "are", "was",
                 "will", "can", "not", "but", "has", "have", "been", "into", "also",
                 "new", "add", "create", "file", "code", "function", "class", "should"}
    words = re.findall(r'[a-zA-Z_]\w{2,}', task_description.lower())
    keywords = {w for w in words if w not in stopwords}

    manifest_names = {"pyproject.toml", "package.json", "Cargo.toml", "go.mod"}
    results: list[Path] = []
    seen: set[Path] = set()

    for root, dirs, files in os.walk(workspace_root):
        # Prune skipped directories in-place
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS and not d.startswith(".")]
        root_path = Path(root)
        rel_root = root_path.relative_to(workspace_root)

        for fname in files:
            fpath = root_path / fname
            if fpath in seen:
                continue

            # Skip secrets / binary
            if fname in _SKIP_FILES:
                continue
            suffix = fpath.suffix.lower()
            if suffix in _SKIP_EXTENSIONS:
                continue
            if suffix not in _CODE_EXTENSIONS:
                continue

            # Always include manifests
            if fname in manifest_names:
                results.append(fpath)
                seen.add(fpath)
                continue

            # Check if path matches any keyword
            path_lower = str(rel_root / fname).lower()
            if any(kw in path_lower for kw in keywords):
                results.append(fpath)
                seen.add(fpath)

    return sorted(results)


def _read_context_files(
    file_paths: list[Path],
    workspace_root: Path,
    budget_chars: int = CONTEXT_BUDGET_CHARS,
    max_lines: int = CONTEXT_FILE_MAX_LINES,
    tail_lines: int = CONTEXT_FILE_TAIL_LINES,
    allowed_root: Path | None = None,
) -> list[dict]:
    """Read files into context dicts, respecting budget and truncation limits.

    ``allowed_root`` is the security boundary -- paths must resolve inside
    it to be read. Defaults to ``workspace_root`` for backward compatibility,
    but callers can pass the git repo root to allow sibling directories
    (e.g., reading ``dashboard/...`` while workspace is ``dev-suite/``).
    Paths under the workspace are reported as workspace-relative; paths
    outside the workspace but inside the repo are reported as repo-relative.
    """
    gathered: list[dict] = []
    total_chars = 0
    workspace_resolved = workspace_root.resolve()
    security_root = (allowed_root or workspace_root).resolve()

    for fpath in file_paths:
        if total_chars >= budget_chars:
            logger.info("[CONTEXT] Budget exhausted (%d chars), skipping remaining files", total_chars)
            break

        try:
            resolved = fpath.resolve()
            if not resolved.is_relative_to(security_root):
                logger.warning("[CONTEXT] Path traversal blocked: %s", fpath)
                continue
        except (ValueError, OSError):
            continue

        if not resolved.is_file():
            continue

        try:
            content = resolved.read_text(encoding="utf-8")
        except Exception as e:
            logger.warning("[CONTEXT] Failed to read %s: %s", fpath, e)
            continue

        # Truncate large files
        content, truncated = _truncate_file(content, max_lines, tail_lines)

        # Check budget
        if total_chars + len(content) > budget_chars:
            remaining = budget_chars - total_chars
            if remaining < 500:
                logger.info("[CONTEXT] Skipping %s — would exceed budget", fpath)
                continue
            content = content[:remaining] + "\n[... truncated to fit budget ...]"
            truncated = True

        # Report path relative to workspace when possible, else repo root
        if resolved.is_relative_to(workspace_resolved):
            rel_path = str(resolved.relative_to(workspace_resolved))
        else:
            rel_path = str(resolved.relative_to(security_root))
        # Normalize for cross-platform consistency
        rel_path = rel_path.replace("\\", "/")
        gathered.append({
            "path": rel_path,
            "content": content,
            "truncated": truncated,
        })
        total_chars += len(content)

    return gathered


async def gather_context_node(state: GraphState) -> dict:
    """Pre-Architect context gathering node (Issue #158).

    Reads source files from the workspace and injects their contents
    into state for the Architect to consume. No LLM call — pure I/O.

    File sources (priority order):
    1. Explicit related_files from task description (# RELATED_FILES: ... marker)
    2. Auto-inferred files from workspace keyword matching
    """
    trace = list(state.get("trace", []))
    trace.append("gather_context: starting")

    workspace_root_str = state.get("workspace_root", "")
    workspace_root = Path(workspace_root_str).resolve() if workspace_root_str else _get_workspace_root()
    repo_root = _find_repo_root(workspace_root)
    task_description = state.get("task_description", "")

    def _resolve_candidate(raw: str) -> Path | None:
        """Resolve a path candidate against workspace_root then repo_root."""
        raw = raw.strip()
        if not raw:
            return None
        for base in (workspace_root, repo_root):
            candidate = (base / raw).resolve()
            if candidate.is_file():
                return candidate
        return None

    # Source 1: Explicit related files from task description (# RELATED_FILES: marker)
    explicit_files: list[Path] = []
    for line in task_description.splitlines():
        stripped = line.strip()
        if stripped.upper().startswith("# RELATED_FILES:") or stripped.upper().startswith("RELATED_FILES:"):
            raw_files = stripped.split(":", 1)[1].strip()
            for f in raw_files.split(","):
                resolved = _resolve_candidate(f)
                if resolved is not None:
                    explicit_files.append(resolved)

    # Source 2: File-path-looking tokens anywhere in the task description
    mentioned_files: list[Path] = []
    for raw in _extract_file_paths_from_text(task_description):
        resolved = _resolve_candidate(raw)
        if resolved is not None:
            mentioned_files.append(resolved)

    # Source 3: Auto-inferred files (workspace keyword matching)
    inferred_files = _infer_relevant_files(workspace_root, task_description)

    # Merge: explicit > mentioned > inferred (deduplicated)
    seen: set[str] = set()
    ordered_files: list[Path] = []
    for f in explicit_files + mentioned_files + inferred_files:
        key = str(f.resolve())
        if key not in seen:
            seen.add(key)
            ordered_files.append(f)

    gathered: list[dict] = []
    if ordered_files:
        gathered = _read_context_files(
            ordered_files, workspace_root, allowed_root=repo_root
        )

    # Source 4a: Planner-supplied pre-fetched context (issue #193 PR 2).
    # The Planner's conversational pre-graph phase may have already
    # fetched GitHub issue/PR summaries for refs the user mentioned.
    # We fold those in here so the Architect sees them and we avoid
    # a redundant second fetch in Source 4b.
    prefetched_paths: set[str] = set()
    prefetched_items = state.get("prefetched_gathered_context") or []
    for item in prefetched_items:
        if not isinstance(item, dict):
            continue
        path = item.get("path")
        if not path or path in prefetched_paths:
            continue
        prefetched_paths.add(path)
        gathered.append(item)
    if prefetched_items:
        trace.append(
            f"gather_context: reused {len(prefetched_paths)} pre-fetched item(s)"
        )

    # Source 4b: GitHub issue/PR pre-fetch (issue #193).
    # Scans the task description for refs like "issue #113",
    # "fixes #42", or "owner/repo#99" and fetches their summaries so
    # the Architect has the context without needing tools. Best-effort:
    # missing token, network errors, and 404s are silently skipped.
    # Refs already covered by prefetched_gathered_context are filtered
    # out BEFORE the network call so the Planner's earlier fetch is
    # not repeated.
    github_token = os.getenv("GITHUB_TOKEN", "")
    new_github_items: list[dict] = []
    if github_token:
        refs = extract_github_refs(
            task_description,
            default_owner=os.getenv("GITHUB_OWNER", ""),
            default_repo=os.getenv("GITHUB_REPO", ""),
            max_refs=5,
        )
        for ref in refs:
            if ref.synthetic_path in prefetched_paths:
                continue
            item = await fetch_issue_or_pr(
                ref.owner, ref.repo, ref.number,
                token=github_token, max_chars=2000,
            )
            if item is not None:
                new_github_items.append(item)
    if new_github_items:
        gathered.extend(new_github_items)
        trace.append(
            f"gather_context: pre-fetched {len(new_github_items)} GitHub ref(s)"
        )

    if not gathered:
        trace.append("gather_context: no relevant files found")
        logger.info("[CONTEXT] No files or GitHub refs to gather for task")
        return {"gathered_context": [], "trace": trace}

    total_tokens = sum(_estimate_tokens(f["content"]) for f in gathered)
    trace.append(
        f"gather_context: gathered {len(gathered)} items (~{total_tokens} tokens)"
    )
    logger.info(
        "[CONTEXT] Gathered %d items (~%d tokens) for Architect",
        len(gathered), total_tokens,
    )

    return {"gathered_context": gathered, "trace": trace}


# -- Task Decomposition (Issue #58) --

def _needs_decomposition(task_description: str, gathered_context: list[dict] | None) -> bool:
    """Determine if a task is large enough to warrant decomposition.

    Returns True if gathered context has >= DECOMPOSE_FILE_THRESHOLD files
    OR files span >= DECOMPOSE_DIR_THRESHOLD top-level directories.
    """
    if not gathered_context:
        return False
    file_paths = [ctx["path"] for ctx in gathered_context]
    if len(file_paths) >= DECOMPOSE_FILE_THRESHOLD:
        return True
    top_dirs = {p.split("/")[0] for p in file_paths if "/" in p}
    return len(top_dirs) >= DECOMPOSE_DIR_THRESHOLD


def _build_decomposition_prompt(task_description: str, file_paths: list[str]) -> str:
    """Build the system prompt for the decomposition LLM call."""
    files_block = "\n".join(f"- {p}" for p in file_paths)
    return f"""You are a task decomposition specialist. Given a complex coding task and the relevant files,
break it down into sequenced sub-tasks that can each be implemented and tested independently.

Each sub-task should:
- Target a coherent subset of files (ideally within one module/directory)
- Have clear instructions that can stand alone
- List dependencies on prior sub-tasks

Respond with ONLY a valid JSON object matching this schema:
{{
  "parent_task_id": "string (short ID for the overall task)",
  "sub_tasks": [
    {{
      "sub_task_id": "string (e.g. parent_task_id-1)",
      "parent_task_id": "string (same as above)",
      "sequence": 0,
      "depends_on": [],
      "target_files": ["list of file paths for this sub-task"],
      "instructions": "detailed instructions for this sub-task",
      "description": "one-line summary"
    }}
  ],
  "rationale": "why this decomposition was chosen"
}}

Do not include any text before or after the JSON.

## Relevant Files
{files_block}"""


async def decompose_task_node(state: GraphState) -> dict:
    """Decompose large tasks into ordered sub-tasks (Issue #58).

    For tasks below the threshold, passes through with decomposition=None.
    For large tasks, calls the architect LLM to produce a TaskDecomposition.
    """
    trace = list(state.get("trace", []))
    trace.append("decompose_task: evaluating scope")

    task_description = state.get("task_description", "")
    gathered_context = state.get("gathered_context") or []

    if not _needs_decomposition(task_description, gathered_context):
        trace.append("decompose_task: below threshold, skipping decomposition")
        logger.info("[DECOMPOSE] Task below decomposition threshold, single-blueprint path")
        return {"decomposition": None, "current_subtask_index": 0, "completed_subtasks": [], "trace": trace}

    file_paths = [ctx["path"] for ctx in gathered_context]
    trace.append(f"decompose_task: {len(file_paths)} files detected, decomposing")
    logger.info("[DECOMPOSE] %d files across workspace, invoking LLM decomposition", len(file_paths))

    system_prompt = _build_decomposition_prompt(task_description, file_paths)
    llm = _get_architect_llm()
    tokens_used = state.get("tokens_used", 0)

    try:
        response = await llm.ainvoke([
            SystemMessage(content=system_prompt),
            HumanMessage(content=task_description),
        ])
        raw = _extract_text_content(response.content)
        decomp_data = _extract_json(raw)
        decomposition = TaskDecomposition(**decomp_data)
        tokens_used += _extract_token_count(response)
    except (json.JSONDecodeError, Exception) as e:
        trace.append(f"decompose_task: LLM decomposition failed ({e}), falling back to single blueprint")
        logger.warning("[DECOMPOSE] Decomposition failed: %s, falling back", e)
        return {"decomposition": None, "current_subtask_index": 0, "completed_subtasks": [], "trace": trace, "tokens_used": tokens_used}

    # Validate: check for overlapping target_files across sub-tasks
    all_files: list[str] = []
    has_overlap = False
    for st in decomposition.sub_tasks:
        for f in st.target_files:
            if f in all_files:
                has_overlap = True
                break
            all_files.append(f)
        if has_overlap:
            break

    if has_overlap:
        trace.append("decompose_task: overlapping target_files detected, falling back to single blueprint")
        logger.warning("[DECOMPOSE] Sub-tasks have overlapping files, falling back to single blueprint")
        return {"decomposition": None, "current_subtask_index": 0, "completed_subtasks": [], "trace": trace, "tokens_used": tokens_used}

    trace.append(f"decompose_task: created {len(decomposition.sub_tasks)} sub-tasks")
    logger.info("[DECOMPOSE] Created %d sub-tasks: %s", len(decomposition.sub_tasks), [st.description for st in decomposition.sub_tasks])

    return {
        "decomposition": decomposition,
        "current_subtask_index": 0,
        "completed_subtasks": [],
        "tokens_used": tokens_used,
        "trace": trace,
    }


# -- Node Functions --
# CRITICAL: All graph nodes MUST be async def on Python 3.13+.
# Sync nodes cause "generator didn't stop after throw()" under astream().

def _blueprint_is_sufficient(blueprint: Blueprint) -> bool:
    """Heuristic: is the Architect's Phase-1 Blueprint good enough to build?

    Issue #193 PR 3 — When the Architect has no tool access, it sometimes
    emits placeholder paths ("path/to/file.py") or an empty target_files
    because it can't verify the codebase layout. In those cases we escalate
    to Phase 2 (read-only tools). Otherwise the Phase-1 Blueprint is used
    as-is, matching the pre-#193 behavior and avoiding unnecessary tool
    cost.
    """
    if not blueprint.target_files:
        return False
    # Reject placeholder-looking paths — angle brackets, "TODO", or the
    # literal "path/to" prefix LLMs emit when they're guessing.
    for tf in blueprint.target_files:
        stripped = tf.strip()
        if not stripped:
            return False
        lower = stripped.lower()
        if "<" in stripped or ">" in stripped:
            return False
        if lower.startswith("path/to") or lower in {"todo", "tbd", "unknown"}:
            return False
    # Require non-trivial instructions so an empty/handwave Blueprint
    # doesn't squeak through.
    if len(blueprint.instructions.strip()) < 20:
        return False
    return True


async def architect_node(
    state: GraphState,
    config: RunnableConfig | None = None,
) -> dict:
    trace = list(state.get("trace", []))
    trace.append("architect: starting planning")
    retry_count = state.get("retry_count", 0)
    tokens_used = state.get("tokens_used", 0)
    logger.info("[ARCH] retry_count=%d, tokens_used=%d, status=%s", retry_count, tokens_used, state.get("status", "unknown"))
    memory_context = _fetch_memory_context(state.get("task_description", ""))
    memory_block = ""
    if memory_context:
        memory_block = "\n\nProject context from memory:\n" + "\n".join(f"- {c}" for c in memory_context)
    # Issue #158: inject gathered source-file context
    source_block = ""
    gathered_context = state.get("gathered_context") or []
    if gathered_context:
        file_sections = []
        for ctx in gathered_context:
            header = f"# --- FILE: {ctx['path']} ---"
            if ctx.get("truncated"):
                header += "  (truncated)"
            file_sections.append(f"{header}\n{ctx['content']}")
        source_block = (
            "\n\n## Source Files (from workspace)\n\n"
            "The following files are relevant to this task. Use them to understand "
            "the existing codebase structure, imports, function signatures, and patterns.\n\n"
            + "\n\n".join(file_sections)
        )
        source_block += (
            "\n\n## Preservation Rules (CRITICAL when source files are attached)\n"
            "- For targeted edits, quote the EXACT current code to change and the "
            "EXACT replacement in `instructions`. The Developer will use "
            "filesystem_patch with these strings as search/replace.\n"
            "- Add a preservation constraint to `constraints` for every non-trivial "
            "piece of existing functionality in the source files (functions, event "
            "handlers, imports, effects, stores, components, blocks). Example: "
            "\"Preserve the SSE log streaming subscription in onMount\".\n"
            "- If the change is effectively a one-line fix, say so explicitly in "
            "`instructions` and keep `target_files` minimal.\n"
            "- NEVER instruct the Developer to rewrite an entire existing file.\n"
        )
    system_prompt = f"""You are the Architect agent. Your job is to create a structured Blueprint
for a coding task. You NEVER write code yourself.

Respond with ONLY a valid JSON object matching this schema:
{{
  "task_id": "string (short unique identifier)",
  "target_files": ["list of file paths to create or modify"],
  "instructions": "clear step-by-step instructions for the developer",
  "constraints": ["list of constraints or requirements"],
  "acceptance_criteria": ["list of testable criteria for QA"],
  "summary": "imperative one-line summary of the change (<=80 chars, used as PR title)"
}}

Do not include any text before or after the JSON.{memory_block}{source_block}"""
    # Issue #58: scope architect to current sub-task when decomposed
    decomposition = state.get("decomposition")
    if decomposition is not None:
        current_idx = state.get("current_subtask_index", 0)
        current_sub = decomposition.sub_tasks[current_idx]
        user_msg = (
            f"## Sub-Task {current_idx + 1}/{len(decomposition.sub_tasks)}: {current_sub.description}\n\n"
            f"{current_sub.instructions}\n\n"
            f"Target files: {', '.join(current_sub.target_files)}\n\n"
            f"Overall task: {state.get('task_description', '')}"
        )
        completed = state.get("completed_subtasks", [])
        if completed:
            completed_block = "\n".join(
                f"- Sub-task {i + 1}: {c['description']} (PASSED, files: {', '.join(c['blueprint']['target_files']) if c.get('blueprint') else 'n/a'})"
                for i, c in enumerate(completed)
            )
            system_prompt += f"\n\n## Previously Completed Sub-Tasks\n{completed_block}"
        trace.append(f"architect: scoped to sub-task {current_idx + 1}/{len(decomposition.sub_tasks)}: {current_sub.description}")
    else:
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
    # Phase 1: no tools. Keep pre-#193 behavior intact — the Architect
    # produces a Blueprint from memory + gathered_context alone. This is
    # cheap and handles the common case where gather_context_node already
    # surfaced the right files.
    phase1_messages = [SystemMessage(content=system_prompt), HumanMessage(content=user_msg)]
    response = await llm.ainvoke(phase1_messages)
    tokens_used += _extract_token_count(response)
    try:
        raw = _extract_text_content(response.content)
        blueprint_data = _extract_json(raw)
        blueprint = Blueprint(**blueprint_data)
    except (json.JSONDecodeError, Exception) as e:
        trace.append(f"architect: phase 1 failed to parse blueprint: {e}")
        logger.error("[ARCH] Phase 1 Blueprint parse failed: %s", e)
        blueprint = None

    # Phase 2 gate (Issue #193 PR 3): escalate to read-only tools if the
    # Phase-1 Blueprint is missing/insufficient AND tools are available.
    # When no tools are configured (e.g. single-shot mode, tests without
    # a provider) we fall through with whatever Phase 1 produced.
    tool_calls_log: list[dict] = []
    phase2_attempted = False
    if blueprint is None or not _blueprint_is_sufficient(blueprint):
        readonly_tools = _get_agent_tools(config, allowed_names=READONLY_TOOLS)
        if readonly_tools:
            phase2_attempted = True
            reason = (
                "phase 1 produced no parseable blueprint"
                if blueprint is None
                else f"phase 1 blueprint insufficient (target_files={blueprint.target_files})"
            )
            trace.append(f"architect: escalating to phase 2 -- {reason}")
            logger.info(
                "[ARCH] Phase 2 start: %d read-only tool(s) bound, reason=%s",
                len(readonly_tools), reason,
            )
            # Carry Phase 1 forward as context and nudge the LLM to use
            # the tools to verify target_files before re-emitting JSON.
            escalation_prompt = (
                "The previous Blueprint was insufficient -- either empty "
                "target_files, placeholder paths, or missing instructions. "
                "Use the read-only tools (filesystem_list, filesystem_read, "
                "github_read_diff) to inspect the codebase, verify which "
                "files exist, and then emit a CORRECTED JSON Blueprint "
                "matching the schema above. Respond with ONLY the JSON "
                "when done."
            )
            phase2_messages = list(phase1_messages)
            phase2_messages.append(response)
            phase2_messages.append(HumanMessage(content=escalation_prompt))
            llm_with_tools = llm.bind_tools(readonly_tools)
            response, tokens_used, new_tool_log = await _run_tool_loop(
                llm_with_tools,
                phase2_messages,
                readonly_tools,
                max_turns=MAX_ARCHITECT_TOOL_TURNS,
                tokens_used=tokens_used,
                trace=trace,
                agent_name="architect",
            )
            tool_calls_log.extend(new_tool_log)
            try:
                raw = _extract_text_content(response.content)
                blueprint_data = _extract_json(raw)
                blueprint = Blueprint(**blueprint_data)
                trace.append(
                    f"architect: phase 2 blueprint parsed for "
                    f"{len(blueprint.target_files)} file(s)"
                )
            except (json.JSONDecodeError, Exception) as e:
                trace.append(f"architect: phase 2 failed to parse blueprint: {e}")
                logger.warning("[ARCH] Phase 2 Blueprint parse failed: %s", e)
                # If Phase 1 already gave us *something* parseable, keep it;
                # otherwise this is a hard failure.
                if blueprint is None:
                    return {
                        "status": WorkflowStatus.FAILED,
                        "error_message": (
                            f"Architect failed to produce valid Blueprint "
                            f"(phase 1 + phase 2 both failed): {e}"
                        ),
                        "trace": trace,
                        "memory_context": memory_context,
                        "tool_calls_log": tool_calls_log,
                    }

    if blueprint is None:
        # Phase 1 failed and no tools to escalate with.
        return {
            "status": WorkflowStatus.FAILED,
            "error_message": "Architect failed to produce valid Blueprint",
            "trace": trace,
            "memory_context": memory_context,
        }

    if not phase2_attempted:
        trace.append(
            f"architect: phase 1 blueprint created for "
            f"{len(blueprint.target_files)} files"
        )
    logger.info("[ARCH] done. tokens_used now=%d", tokens_used)
    result = {
        "blueprint": blueprint,
        "status": WorkflowStatus.BUILDING,
        "tokens_used": tokens_used,
        "trace": trace,
        "memory_context": memory_context,
    }
    # Only touch tool_calls_log if Phase 2 ran, so single-shot tasks
    # keep a clean empty log.
    if tool_calls_log:
        result["tool_calls_log"] = list(state.get("tool_calls_log", [])) + tool_calls_log
    return result


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
    failure_report = state.get("failure_report")
    is_retry = failure_report is not None and not failure_report.is_architectural
    tools = _get_agent_tools(config, DEV_TOOL_NAMES)
    has_tools = len(tools) > 0
    if is_retry:
        system_prompt = """You are the Lead Dev agent. You previously implemented this Blueprint but QA found issues.

IMPORTANT RETRY RULES:
1. Your previous code is shown below under "CURRENT FILES ON DISK". READ IT CAREFULLY.
2. The QA failure report describes EXACTLY what is wrong.
3. Apply the MINIMUM change needed to fix the reported issue.
4. Do NOT rewrite files from scratch. Only modify the specific lines that need fixing.
5. If a fix hint is provided, follow it precisely.
"""
        if has_tools:
            system_prompt += """\nYou have workspace tools available. Use filesystem_read to verify the current state first.

EDITING RULES (STRICT):
- NEVER rewrite an entire existing file.
- Use filesystem_patch for surgical search-and-replace edits. This is the
  default tool for modifying existing files. The search string must match
  exactly once -- include enough surrounding context to make it unique.
- Use filesystem_write ONLY for creating brand-new files.
- PRESERVE all existing functionality not explicitly changed by the fix hint
  or QA report. Do not delete functions, imports, handlers, or blocks that
  are not mentioned as broken.
- After applying your fix, provide a short text summary of ONLY what you
  changed (the search/replace pair or the new file). Do not dump entire
  file contents."""
        else:
            system_prompt += """\nRespond with ONLY the fixed code. Include file paths as comments:
# --- FILE: path/to/file.py ---

Only output files that need changes. Do not repeat unchanged files."""
    elif has_tools:
        system_prompt = """You are the Lead Dev agent. You receive a structured Blueprint and implement it using the tools available to you.

WORKFLOW:
1. Use filesystem_read to examine EVERY file listed in target_files BEFORE editing.
2. Use filesystem_list to explore the project directory structure if needed.
3. For each change:
   - If the file already exists -> use filesystem_patch to make a targeted
     search-and-replace edit. This is the default tool for modifying
     existing files. Your 'search' string must appear exactly once in the
     file; include enough surrounding context to make it unique.
   - If the file does NOT exist yet -> use filesystem_write to create it.
4. After applying every edit, respond with a short text summary of what
   you changed (the search/replace pairs, or which new files you created).

EDITING RULES (STRICT):
- NEVER rewrite an entire existing file. filesystem_write on an existing
  file is almost always wrong -- prefer filesystem_patch.
- PRESERVE all existing functionality not explicitly changed by the
  Blueprint. Do not delete functions, imports, event handlers, effects,
  stores, components, or any other code that is not targeted by the
  Blueprint instructions. Missing functionality is a scope-creep failure
  and will be rejected by QA.
- Follow the Blueprint exactly. Respect all constraints listed in it.
- Do NOT dump full file contents in your text response. A short summary
  of the edits is sufficient."""
    else:
        system_prompt = """You are the Lead Dev agent. You receive a structured Blueprint
and write the code to implement it.

Respond with the complete code implementation. Include file paths as comments
at the top of each file section, like:
# --- FILE: path/to/file.py ---

Follow the Blueprint exactly. Respect all constraints.
Write clean, well-documented code."""
    user_msg = f"Blueprint:\n{blueprint.model_dump_json(indent=2)}"
    if is_retry:
        user_msg += "\n\n" + "=" * 60
        user_msg += f"\nRETRY CONTEXT (attempt #{retry_count + 1})\n"
        user_msg += "=" * 60
        user_msg += "\n\nQA FAILURE REPORT:"
        user_msg += f"\n  Tests passed: {failure_report.tests_passed}"
        user_msg += f"\n  Tests failed: {failure_report.tests_failed}"
        user_msg += f"\n  Errors: {', '.join(failure_report.errors)}"
        user_msg += f"\n  Failed files: {', '.join(failure_report.failed_files)}"
        user_msg += f"\n  Recommendation: {failure_report.recommendation}"
        if failure_report.fix_complexity:
            user_msg += f"\n  Fix complexity: {failure_report.fix_complexity}"
        if failure_report.exact_fix_hint:
            user_msg += f"\n\n  EXACT FIX HINT: {failure_report.exact_fix_hint}"
        ws_from_state = state.get("workspace_root", "")
        workspace_root = (Path(ws_from_state).resolve() if ws_from_state else _get_workspace_root())
        file_context = _build_retry_file_context(failure_report, blueprint, workspace_root)
        if file_context:
            user_msg += file_context
            trace.append(f"developer: retry -- injected file context ({len(file_context)} chars)")
        else:
            trace.append("developer: retry -- no file context available")
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
    if not content.strip() and has_tools:
        wrote_files = [
            tc for tc in tool_calls_log
            if tc.get("tool") in ("filesystem_write", "filesystem_patch")
            and tc.get("success")
            and tc.get("agent") == "developer"
        ]
        if wrote_files and blueprint.target_files:
            ws_from_state = state.get("workspace_root", "")
            ws_root = (Path(ws_from_state).resolve() if ws_from_state else _get_workspace_root())
            repo_root = _find_repo_root(ws_root)
            recovered_parts = []
            for target_path in blueprint.target_files:
                full_path = _resolve_target_path(target_path, ws_root, repo_root)
                if full_path.is_file():
                    try:
                        file_content = full_path.read_text(encoding="utf-8")
                        recovered_parts.append(f"# --- FILE: {target_path} ---\n{file_content}")
                    except Exception as e:
                        logger.warning("[DEV] Failed to recover %s from disk: %s", target_path, e)
            if recovered_parts:
                content = "\n\n".join(recovered_parts)
                trace.append(f"developer: recovered {len(recovered_parts)} file(s) from disk ({len(content)} chars) -- LLM text was empty after tool use")
                logger.info("[DEV] Recovered %d files from disk (LLM text was empty)", len(recovered_parts))

    trace.append(f"developer: code generated ({len(content)} chars)")
    logger.info("[DEV] done. tokens_used now=%d", tokens_used)
    new_entry = {"content": f"Implemented blueprint {blueprint.task_id}: {blueprint.instructions[:200]}", "tier": "l1", "module": _infer_module(blueprint.target_files), "source_agent": "developer", "confidence": 1.0, "sandbox_origin": "locked-down", "related_files": ",".join(blueprint.target_files), "task_id": blueprint.task_id, "source_step": "developer", "source_output_ref": f"Implemented: {blueprint.instructions[:150]}"}
    replaced = False
    for i, existing in enumerate(memory_writes):
        if existing.get("task_id") == blueprint.task_id and existing.get("source_agent") == "developer":
            memory_writes[i] = new_entry
            replaced = True
            break
    if not replaced:
        memory_writes.append(new_entry)
    return {"generated_code": content, "status": WorkflowStatus.REVIEWING, "tokens_used": tokens_used, "trace": trace, "memory_writes": memory_writes, "tool_calls_log": tool_calls_log}


async def apply_code_node(state: GraphState) -> dict:
    trace = list(state.get("trace", []))
    trace.append("apply_code: starting")
    generated_code = state.get("generated_code", "")
    blueprint = state.get("blueprint")
    if not blueprint:
        trace.append("apply_code: no blueprint -- skipping")
        return {"parsed_files": [], "trace": trace}
    ws_from_state = state.get("workspace_root", "")
    workspace_root = Path(ws_from_state).resolve() if ws_from_state else _get_workspace_root()
    repo_root = _find_repo_root(workspace_root)
    tool_calls_log = state.get("tool_calls_log", [])
    wrote_via_tools = any(
        tc.get("tool") in ("filesystem_write", "filesystem_patch")
        and tc.get("success")
        for tc in tool_calls_log
    )
    if wrote_via_tools and blueprint.target_files:
        disk_files = []
        for target_path in blueprint.target_files:
            full_path = _resolve_target_path(target_path, workspace_root, repo_root)
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
            logger.info("[APPLY_CODE] Read %d tool-written files (%d chars) from %s", len(disk_files), total_chars, repo_root)
            return {"parsed_files": disk_files, "trace": trace}
        trace.append("apply_code: tool-written files not found on disk, falling back to parser")
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
            target = _resolve_target_path(pf.path, workspace_root, repo_root)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(pf.content, encoding="utf-8")
            written_count += 1
            total_chars += len(pf.content)
        except Exception as e:
            logger.warning("[APPLY_CODE] Failed to write %s: %s", pf.path, e)
            trace.append(f"apply_code: failed to write {pf.path} -- {e}")
    trace.append(f"apply_code: wrote {written_count} files ({total_chars:,} chars total) under {repo_root}")
    logger.info("[APPLY_CODE] Wrote %d files (%d chars) under %s", written_count, total_chars, repo_root)
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
    if not generated_code:
        tool_calls_log_state = state.get("tool_calls_log", [])
        wrote_via_tools = any(
            tc.get("tool") in ("filesystem_write", "filesystem_patch")
            and tc.get("success")
            for tc in tool_calls_log_state
        )
        if wrote_via_tools and blueprint.target_files:
            ws_from_state = state.get("workspace_root", "")
            ws_root = (Path(ws_from_state).resolve() if ws_from_state else _get_workspace_root())
            repo_root = _find_repo_root(ws_root)
            recovered_parts = []
            for target_path in blueprint.target_files:
                full_path = _resolve_target_path(target_path, ws_root, repo_root)
                if full_path.is_file():
                    try:
                        file_content = full_path.read_text(encoding="utf-8")
                        recovered_parts.append(f"# --- FILE: {target_path} ---\n{file_content}")
                    except Exception:
                        pass
            if recovered_parts:
                generated_code = "\n\n".join(recovered_parts)
                trace.append(f"qa: recovered {len(recovered_parts)} file(s) from disk for review (generated_code was empty)")
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
    if not blueprint.acceptance_criteria:
        system_prompt += """\n\nIMPORTANT - NO ACCEPTANCE CRITERIA PROVIDED:
The user did not specify acceptance criteria for this task. In this case:
1. Prioritize FUNCTIONAL correctness: the code runs without errors.
2. If the sandbox executed successfully (exit_code=0), bias toward PASS.
3. Do NOT invent strict formatting or cosmetic requirements.
4. Only FAIL for genuine bugs, syntax errors, or logic errors.
5. Subjective quality issues (indentation style, variable naming) are NOT failures."""
    gathered_context = state.get("gathered_context") or []
    if gathered_context:
        system_prompt += """\n\nSCOPE-CREEP DETECTION (CRITICAL):
The ORIGINAL versions of the target files are included below under
"Original Source Files". Compare the Generated Code to the originals and
FAIL (status: "fail", failure_type: "code") if ANY of the following are true:
- Functions, imports, event handlers, effects, stores, components, or
  blocks present in the original are missing from the generated version
  without being explicitly called out by the Blueprint.
- The generated file is >20% shorter than the original without a clear
  justification in the Blueprint instructions.
- The Blueprint described a one-line / surgical fix but the generated
  code rewrites the whole file.
When you detect scope creep, list the removed items in `errors` and
set `recommendation` to describe which functionality must be restored.
Set `exact_fix_hint` to point the Developer at filesystem_patch for
the targeted change."""
    bp_json = blueprint.model_dump_json(indent=2)
    user_msg = f"Blueprint:\n{bp_json}\n\nGenerated Code:\n{generated_code}"
    if gathered_context:
        original_sections = []
        for ctx in gathered_context:
            header = f"# --- ORIGINAL: {ctx['path']} ---"
            if ctx.get("truncated"):
                header += "  (truncated)"
            original_sections.append(f"{header}\n{ctx['content']}")
        user_msg += (
            "\n\nOriginal Source Files (pre-change, for scope-creep comparison):\n\n"
            + "\n\n".join(original_sections)
        )
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
        # Project-aware validation results (issue #159)
        pv = sandbox_result.project_validation
        if pv is not None:
            user_msg += "\n\nProject Validation Results (full project context):\n"
            user_msg += f"  Overall: {'PASS' if pv.overall_pass else 'FAIL'}\n"
            user_msg += f"  Tests: {pv.tests_passed} passed, {pv.tests_failed} failed\n"
            user_msg += f"  Lint errors: {pv.lint_errors}\n"
            user_msg += f"  Type errors: {pv.type_errors}\n"
            user_msg += f"  Install: {'OK' if pv.install_ok else 'FAILED'}\n"
            if pv.errors:
                user_msg += "  Errors:\n"
                for err in pv.errors[:10]:
                    user_msg += f"    - {err}\n"
            for cr in pv.command_results:
                if cr.exit_code != 0:
                    user_msg += f"\n  [{cr.phase}] `{cr.command}` (exit {cr.exit_code}):\n"
                    output = (cr.stdout[-500:] + "\n" + cr.stderr[-500:]).strip()
                    if output:
                        user_msg += f"    {output}\n"
            user_msg += "\nProject validation ran the FULL test suite against the actual project. Weight these results very heavily."
        else:
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
        memory_writes.append({"content": f"QA passed for {blueprint.task_id}: {failure_report.tests_passed} tests passed", "tier": "l2", "module": _infer_module(blueprint.target_files), "source_agent": "qa", "confidence": 1.0, "sandbox_origin": "locked-down", "related_files": ",".join(blueprint.target_files), "task_id": blueprint.task_id, "source_step": "qa", "source_output_ref": f"QA passed: {failure_report.tests_passed} tests passed"})
    elif failure_report.is_architectural:
        status = WorkflowStatus.ESCALATED
        memory_writes.append({"content": f"Architectural issue in {blueprint.task_id}: {failure_report.recommendation}", "tier": "l0-discovered", "module": _infer_module(blueprint.target_files), "source_agent": "qa", "confidence": 0.85, "sandbox_origin": "locked-down", "related_files": ",".join(failure_report.failed_files), "task_id": blueprint.task_id, "source_step": "qa", "source_output_ref": f"Architectural: {failure_report.recommendation[:150]}"})
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


def _run_project_validation(config, workspace_root, parsed_files=None, template=None):
    """Run project-aware validation in E2B sandbox (issue #159)."""
    api_key = os.getenv("E2B_API_KEY")
    if not api_key:
        return None
    runner = ProjectValidationRunner(api_key=api_key)
    changed_files = {pf["path"]: pf["content"] for pf in parsed_files} if parsed_files else {}
    return runner.run_project_validation(
        config=config,
        workspace_root=Path(workspace_root),
        changed_files=changed_files,
        template=template,
    )


async def sandbox_validate_node(state: GraphState) -> dict:
    trace = list(state.get("trace", []))
    trace.append("sandbox_validate: starting")
    blueprint = state.get("blueprint")
    if not blueprint:
        trace.append("sandbox_validate: no blueprint -- skipping")
        return {"sandbox_result": None, "trace": trace}

    # Check for project-aware validation config (issue #159)
    ws_root = state.get("workspace_root", "")
    workspace_path = Path(ws_root).resolve() if ws_root else _get_workspace_root()
    project_config = load_project_validation_config(workspace_path)

    plan = get_validation_plan(blueprint.target_files, project_config=project_config)
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
        if plan.strategy == ValidationStrategy.PROJECT:
            trace.append(f"sandbox_validate: running project validation ({len(plan.commands)} commands)")
            result = _run_project_validation(
                config=plan.project_config,
                workspace_root=workspace_path,
                parsed_files=parsed_files if parsed_files else None,
                template=plan.template,
            )
        elif plan.strategy == ValidationStrategy.SCRIPT_EXEC:
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


async def flush_memory_node(state: GraphState) -> dict:
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
            source_step = entry.get("source_step", "")
            source_output_ref = entry.get("source_output_ref", "")
            if tier == "l0-discovered":
                store.add_l0_discovered(content, module=module, source_agent=source_agent, confidence=confidence, sandbox_origin=sandbox_origin, related_files=related_files, task_id=task_id, source_step=source_step, source_output_ref=source_output_ref)
            elif tier == "l2":
                store.add_l2(content, module=module, source_agent=source_agent, related_files=related_files, task_id=task_id, source_step=source_step, source_output_ref=source_output_ref)
            else:
                store.add_l1(content, module=module, source_agent=source_agent, confidence=confidence, sandbox_origin=sandbox_origin, related_files=related_files, task_id=task_id, source_step=source_step, source_output_ref=source_output_ref)
            written_entries.append(entry)
        trace.append(f"flush_memory: wrote {len(written_entries)} entries to store")
        logger.info("[FLUSH] Wrote %d memory entries", len(written_entries))
    except Exception as e:
        trace.append(f"flush_memory: store write failed: {e}")
        logger.warning("[FLUSH] Memory store write failed: %s", e)
    return {"trace": trace, "memory_writes_flushed": written_entries}


async def _publish_code_async(state: dict) -> dict:
    from .api.github_prs import GitHubPRProvider, github_pr_provider

    trace = list(state.get("trace", []))
    trace.append("publish_code: starting")

    # Issue #153: use dynamic provider for remote workspaces
    workspace_type = state.get("workspace_type", "local")
    github_repo_str = state.get("github_repo")
    provider_is_dynamic = False

    if workspace_type == "github" and github_repo_str and "/" in github_repo_str:
        owner, repo_name = github_repo_str.split("/", 1)
        provider = GitHubPRProvider.for_repo(owner, repo_name)
        provider_is_dynamic = True
    else:
        provider = github_pr_provider

    if not provider.configured:
        trace.append("publish_code: skipped -- no GITHUB_TOKEN configured")
        logger.info("[PUBLISH] Skipped: no GITHUB_TOKEN")
        return {"trace": trace}
    create_pr = state.get("create_pr", True)
    if not create_pr:
        trace.append("publish_code: skipped -- create_pr=False (user opted out)")
        logger.info("[PUBLISH] Skipped: create_pr=False")
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
    # Issue #153: use custom feature branch name if provided
    github_feature_branch = state.get("github_feature_branch")
    branch_name = github_feature_branch if github_feature_branch else f"agent/{task_id}"
    branch_name = re.sub(r"[^a-zA-Z0-9/_-]", "-", branch_name)
    try:
        trace.append(f"publish_code: creating branch '{branch_name}'")
        pr_base = state.get("github_branch") or "main"
        branch_ok = await provider.create_branch(branch_name, from_branch=pr_base)
        if not branch_ok:
            trace.append("publish_code: FAILED to create branch")
            logger.error("[PUBLISH] Failed to create branch '%s'", branch_name)
            return {"trace": trace}
        trace.append(f"publish_code: pushing {len(parsed_files)} file(s)")
        workspace_root = state.get("workspace_root", "")
        # Issue #153: skip repo_subdir detection for remote workspaces --
        # files in a remote clone are already relative to repo root.
        # Layer B finding: when a target_path is already repo-relative
        # (e.g. "dashboard/...") we must NOT prepend repo_subdir, or the
        # PR will push "dev-suite/dashboard/..." which doesn't exist.
        repo_subdir = ""
        repo_root: Path | None = None
        if workspace_type != "github" and workspace_root:
            ws = Path(workspace_root).resolve()
            for parent in [ws, *ws.parents]:
                if (parent / ".git").exists():
                    repo_root = parent
                    try:
                        repo_subdir = str(ws.relative_to(parent))
                    except ValueError:
                        repo_subdir = ""
                    break
        files_payload = []
        for pf in parsed_files:
            repo_path = pf["path"]
            if (
                repo_subdir
                and repo_subdir != "."
                and repo_root is not None
            ):
                first_segment = Path(pf["path"]).parts[0] if Path(pf["path"]).parts else ""
                # Only prepend repo_subdir when the path is workspace-relative.
                # If the first segment is a top-level repo dir OTHER than
                # the workspace subdir itself, the path is already
                # repo-relative (e.g., "dashboard/..." from a dev-suite
                # workspace).
                is_repo_sibling = (
                    first_segment
                    and first_segment != repo_subdir.split("/")[0]
                    and (repo_root / first_segment).is_dir()
                )
                if not is_repo_sibling:
                    repo_path = f"{repo_subdir}/{pf['path']}"
            files_payload.append({"path": repo_path, "content": pf["content"]})
        push_ok = await provider.push_files_batch(files=files_payload, branch=branch_name, message=f"feat({task_id}): implement {blueprint.instructions[:60]}")
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
        pr_body = (f"## Task: `{task_id}`\n\n" + f"{blueprint.instructions}\n\n" + f"### Acceptance Criteria\n{criteria_block}" + f"{test_summary}\n\n" + "### Files Changed\n" + "\n".join(f"- `{fp['path']}`" for fp in files_payload) + "\n\n---\n_Opened automatically by the agent orchestrator._")
        # Prefer Blueprint.summary (imperative one-liner); fall back to the
        # first non-empty line of the task description; last resort is
        # instructions. Avoids multi-line instruction dumps in PR titles.
        summary_source = (blueprint.summary or "").strip()
        if not summary_source:
            task_desc = state.get("task_description", "") or ""
            for line in task_desc.splitlines():
                line = line.strip()
                if line and not line.upper().startswith(("# RELATED_FILES", "RELATED_FILES")):
                    summary_source = line
                    break
        if not summary_source:
            summary_source = blueprint.instructions.splitlines()[0] if blueprint.instructions else task_id
        title_body = summary_source[:80]
        if len(summary_source) > 80:
            title_body = title_body[:77] + "..."
        pr_title = f"feat({task_id}): {title_body}"
        trace.append("publish_code: opening PR")
        pr_result = await provider.create_pr(head=branch_name, base=pr_base, title=pr_title, body=pr_body)
        if pr_result:
            trace.append(f"publish_code: PR #{pr_result.number} opened -> {pr_result.id}")
            logger.info("[PUBLISH] PR #%d opened: %s", pr_result.number, pr_title)
            return {"working_branch": branch_name, "pr_url": f"https://github.com/{provider.owner}/{provider.repo}/pull/{pr_result.number}", "pr_number": pr_result.number, "trace": trace}
        else:
            trace.append("publish_code: FAILED to open PR (files pushed to branch)")
            logger.error("[PUBLISH] Failed to open PR (files are on branch '%s')", branch_name)
            return {"working_branch": branch_name, "trace": trace}
    except Exception as e:
        trace.append(f"publish_code: error -- {type(e).__name__}: {e}")
        logger.error("[PUBLISH] Unexpected error: %s", e, exc_info=True)
        return {"trace": trace}
    finally:
        if provider_is_dynamic:
            await provider.close()


async def publish_code_node(state: GraphState) -> dict:
    """Push files to GitHub branch and open PR after QA passes."""
    return await _publish_code_async(state)


async def advance_subtask_node(state: GraphState) -> dict:
    """Snapshot completed sub-task and reset per-sub-task state (Issue #58).

    Only meaningful when decomposition is active. For simple tasks,
    this is a pass-through so route_next_subtask sends it to flush_memory.
    """
    trace = list(state.get("trace", []))
    decomposition = state.get("decomposition")
    if decomposition is None:
        trace.append("advance_subtask: no decomposition, pass-through")
        return {"trace": trace}

    current_idx = state.get("current_subtask_index", 0)
    sub_tasks = decomposition.sub_tasks
    current_sub = sub_tasks[current_idx] if current_idx < len(sub_tasks) else None

    completed = list(state.get("completed_subtasks", []))
    if current_sub:
        blueprint = state.get("blueprint")
        completed.append({
            "sub_task_id": current_sub.sub_task_id,
            "status": "passed",
            "blueprint": blueprint.model_dump() if blueprint else None,
            "description": current_sub.description,
        })

    next_idx = current_idx + 1
    trace.append(f"advance_subtask: completed sub-task {current_idx + 1}/{len(sub_tasks)}")
    logger.info("[ADVANCE] Completed sub-task %d/%d", current_idx + 1, len(sub_tasks))

    return {
        "current_subtask_index": next_idx,
        "completed_subtasks": completed,
        "blueprint": None,
        "generated_code": "",
        "failure_report": None,
        "retry_count": 0,
        "parsed_files": [],
        "sandbox_result": None,
        "tool_calls_log": [],
        "trace": trace,
    }


def route_next_subtask(state: GraphState) -> Literal["architect", "flush_memory"]:
    """Route to next sub-task or finish (Issue #58)."""
    decomposition = state.get("decomposition")
    if decomposition is None:
        return "flush_memory"
    current_idx = state.get("current_subtask_index", 0)
    if current_idx < len(decomposition.sub_tasks):
        logger.info("[ROUTER] Advancing to sub-task %d/%d", current_idx + 1, len(decomposition.sub_tasks))
        return "architect"
    logger.info("[ROUTER] All %d sub-tasks complete", len(decomposition.sub_tasks))
    return "flush_memory"


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
    graph.add_node("gather_context", gather_context_node)
    graph.add_node("decompose_task", decompose_task_node)
    graph.add_node("architect", architect_node)
    graph.add_node("developer", developer_node)
    graph.add_node("apply_code", apply_code_node)
    graph.add_node("sandbox_validate", sandbox_validate_node)
    graph.add_node("qa", qa_node)
    graph.add_node("publish_code", publish_code_node)
    graph.add_node("advance_subtask", advance_subtask_node)
    graph.add_node("flush_memory", flush_memory_node)
    graph.add_edge(START, "gather_context")
    graph.add_edge("gather_context", "decompose_task")
    graph.add_edge("decompose_task", "architect")
    graph.add_edge("architect", "developer")
    graph.add_edge("developer", "apply_code")
    graph.add_edge("apply_code", "sandbox_validate")
    graph.add_edge("sandbox_validate", "qa")
    graph.add_conditional_edges("qa", route_after_qa)
    graph.add_edge("publish_code", "advance_subtask")
    graph.add_conditional_edges("advance_subtask", route_next_subtask)
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
    create_pr = bool(os.getenv("GITHUB_TOKEN"))
    initial_state: GraphState = {"task_description": task_description, "blueprint": None, "generated_code": "", "failure_report": None, "status": WorkflowStatus.PLANNING, "retry_count": 0, "tokens_used": 0, "error_message": "", "memory_context": [], "memory_writes": [], "trace": [], "sandbox_result": None, "parsed_files": [], "tool_calls_log": [], "create_pr": create_pr, "workspace_type": "local", "decomposition": None, "current_subtask_index": 0, "completed_subtasks": []}
    invoke_config = {"recursion_limit": 50, **tools_config}
    if trace_config.callbacks:
        invoke_config["callbacks"] = trace_config.callbacks
    with trace_config.propagation_context():
        add_trace_event(trace_config, "orchestrator_start", metadata={"task_preview": task_description[:200], "max_retries": MAX_RETRIES, "token_budget": TOKEN_BUDGET, "tools_available": len(tools_config.get("configurable", {}).get("tools", []))})
        result = asyncio.run(workflow.ainvoke(initial_state, config=invoke_config))
        final_state = AgentState(**result)
        add_trace_event(trace_config, "orchestrator_complete", metadata={"status": final_state.status.value, "tokens_used": final_state.tokens_used, "retry_count": final_state.retry_count, "memory_writes_count": len(final_state.memory_writes), "tool_calls_count": len(final_state.tool_calls_log)})
    trace_config.flush()
    return final_state
