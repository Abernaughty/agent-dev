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
        logger.warning("%s=%r is not a valid integer, using default %d", env_key, raw, default)
        return default

MAX_RETRIES = _safe_int("MAX_RETRIES", 3)
TOKEN_BUDGET = _safe_int("TOKEN_BUDGET", 50000)
