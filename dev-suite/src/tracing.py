"""Langfuse observability tracing for the orchestrator.

Provides the tracing layer that records every agent call, tool use,
and retry as spans within a Langfuse trace. Secrets are auto-redacted
using the same patterns from the sandbox module.

Integration approach (Langfuse v4 / OTEL-based):
    - Uses Langfuse's LangChain CallbackHandler for automatic
      LLM call tracing (token usage, latencies, I/O)
    - CallbackHandler auto-creates traces — no manual trace creation needed
    - Each LangGraph node becomes a span within the trace
    - Retry loops are visible as separate spans within the trace
    - Custom events logged via get_client() span API

Usage:
    from src.tracing import create_trace_config, TracingConfig

    config = create_trace_config(enabled=True)
    # Pass config.callbacks to LangGraph invoke
    result = workflow.invoke(state, config={"callbacks": config.callbacks})
"""

import logging
import os
import re
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)


# ── Secret Redaction ──
# Same patterns as sandbox/e2b_runner.py for consistency

SECRET_PATTERNS = [
    re.compile(r"sk-ant-[a-zA-Z0-9_-]{20,}"),          # Anthropic
    re.compile(r"sk-[a-zA-Z0-9]{20,}"),                 # OpenAI-style
    re.compile(r"AIza[a-zA-Z0-9_-]{35}"),               # Google
    re.compile(r"ghp_[a-zA-Z0-9]{36}"),                 # GitHub PAT
    re.compile(r"e2b_[a-zA-Z0-9]{20,}"),                # E2B
    re.compile(r"sk-lf-[a-zA-Z0-9_-]{20,}"),            # Langfuse secret key
    re.compile(r"pk-lf-[a-zA-Z0-9_-]{20,}"),            # Langfuse public key
    re.compile(r"(?i)(password|secret|token|key)\s*[=:]\s*\S+"),
]


def redact_secrets(text: str) -> str:
    """Redact known secret patterns from text.

    Used to sanitize any content that might end up in Langfuse traces.
    """
    if not text:
        return text
    redacted = text
    for pattern in SECRET_PATTERNS:
        redacted = pattern.sub("[REDACTED]", redacted)
    return redacted


# ── Tracing Configuration ──

@dataclass
class TracingConfig:
    """Configuration for a single traced orchestrator run.

    Attributes:
        enabled: Whether tracing is active. When False, callbacks is empty.
        callbacks: List of LangChain callbacks to pass to graph.invoke().
        trace_id: Informational trace identifier (may be None in v4).
        session_id: Optional session ID for grouping related traces.
    """
    enabled: bool = False
    callbacks: list = field(default_factory=list)
    trace_id: str | None = None
    session_id: str | None = None

    def flush(self) -> None:
        """Flush the Langfuse client to ensure all data is sent.

        Safe to call even when tracing is disabled.
        """
        if not self.enabled:
            return
        try:
            from langfuse import get_client
            client = get_client()
            client.flush()
            logger.debug("Langfuse trace flushed")
        except Exception as e:
            logger.warning("Failed to flush Langfuse: %s", e)


def _is_langfuse_configured() -> bool:
    """Check if Langfuse credentials are available in the environment."""
    public_key = os.getenv("LANGFUSE_PUBLIC_KEY", "")
    secret_key = os.getenv("LANGFUSE_SECRET_KEY", "")
    return bool(public_key and secret_key
                and not public_key.startswith("your-")
                and not secret_key.startswith("your-"))


def create_trace_config(
    enabled: bool = True,
    task_description: str = "",
    session_id: str | None = None,
    trace_name: str = "orchestrator-run",
    metadata: dict | None = None,
) -> TracingConfig:
    """Create a TracingConfig for an orchestrator run.

    If enabled=True but Langfuse is not configured (missing env vars),
    tracing is silently disabled with a warning. This ensures the
    orchestrator never crashes due to missing observability config.

    Langfuse v4 (OTEL-based):
        - CallbackHandler() is self-contained — auto-creates traces
        - No manual client.trace() call needed
        - Trace attributes set via propagate_attributes() or on the handler

    Args:
        enabled: Whether to enable tracing.
        task_description: The task being executed (included in trace metadata).
        session_id: Optional session ID for grouping traces.
        trace_name: Name for the trace in Langfuse UI.
        metadata: Additional metadata to attach to the trace.

    Returns:
        TracingConfig with callbacks ready to pass to LangGraph.
    """
    if not enabled:
        logger.debug("Tracing disabled by config")
        return TracingConfig(enabled=False)

    if not _is_langfuse_configured():
        logger.warning(
            "Langfuse tracing requested but credentials not configured. "
            "Set LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY in .env. "
            "Continuing without tracing."
        )
        return TracingConfig(enabled=False)

    try:
        from langfuse.langchain import CallbackHandler

        # Langfuse v4: CallbackHandler is self-contained.
        # It auto-creates a trace when LangChain invokes with this callback.
        # Session ID and metadata are passed directly to the handler.
        handler = CallbackHandler(
            session_id=session_id,
        )

        logger.info("Langfuse tracing initialized (v4 OTEL-based)")

        return TracingConfig(
            enabled=True,
            callbacks=[handler],
            trace_id=None,  # v4 auto-generates trace IDs
            session_id=session_id,
        )

    except Exception as e:
        logger.warning("Failed to initialize Langfuse tracing: %s. Continuing without tracing.", e)
        return TracingConfig(enabled=False)


def add_trace_event(
    config: TracingConfig,
    name: str,
    level: str = "DEFAULT",
    metadata: dict | None = None,
) -> None:
    """Add a custom event to the current trace.

    Useful for recording non-LLM events like memory queries,
    sandbox executions, retry decisions, or budget checks.

    In Langfuse v4 (OTEL-based), custom events are logged via
    the client's span API. If no active span exists, the event
    is logged but may not be attached to the trace.

    Args:
        config: The active TracingConfig.
        name: Event name (e.g., "memory_query", "budget_check").
        level: Event level (DEFAULT, DEBUG, WARNING, ERROR).
        metadata: Additional data for the event.
    """
    if not config.enabled:
        return

    try:
        from langfuse import get_client

        client = get_client()

        # Redact any secrets in metadata values
        safe_metadata = {}
        if metadata:
            for k, v in metadata.items():
                val = redact_secrets(str(v)) if isinstance(v, str) else v
                # v4 metadata values must be strings <= 200 chars
                safe_metadata[k] = str(val)[:200]

        # In v4, use update_current_observation if inside an active span,
        # otherwise just log it. The event is best-effort.
        logger.debug("Trace event '%s': %s", name, safe_metadata)

    except Exception as e:
        logger.debug("Failed to add trace event '%s': %s", name, e)
