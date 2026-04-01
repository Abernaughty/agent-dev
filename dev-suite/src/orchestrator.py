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

import json
import logging
import os
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
MAX_TOOL_TURNS = _safe_int("MAX_TOOL_TURNS", 10)


# -- Workspace --

def _get_workspace_root() -> Path:
    """Get the workspace root directory.

    Reads WORKSPACE_ROOT env var, falling back to current working directory.
    """
    raw = os.getenv("WORKSPACE_ROOT", ".")
    return Path(raw).resolve()


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
    parsed_files: list[dict]
    tool_calls_log: list[dict]


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
    parsed_files: list[dict] = []
    tool_calls_log: list[dict] = []
