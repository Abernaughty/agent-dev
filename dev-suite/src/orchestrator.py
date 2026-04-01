"""LangGraph orchestrator -- Architect -> Lead Dev -> apply_code -> sandbox -> QA loop.

This is the main entry point for the agent workflow.
Implements the state machine with retry logic, token budgets,
structured Blueprint passing, human escalation, code application,
tool binding (issue #80), and memory write-back.

Issue #80: Agent tool binding -- Dev and QA agents can now use
workspace tools (filesystem_read, filesystem_write, etc.) via
LangChain's bind_tools() + iterative tool execution loop.
Tools are passed via RunnableConfig["configurable"]["tools"].

Issue #92: flush_memory_node returns consolidated entries in
memory_writes_flushed for the runner to bridge into StateManager.
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
