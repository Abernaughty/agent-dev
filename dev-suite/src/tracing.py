"""Langfuse observability tracing for the orchestrator.

Provides the tracing layer that records every agent call, tool use,
and retry as spans within a Langfuse trace. Secrets are auto-redacted
using the same patterns from the sandbox module.

Integration approach (Langfuse v4 / OTEL-based):
    - Uses Langfuse's LangChain CallbackHandler for automatic
      LLM call tracing (token usage, latencies, I/O)
    - CallbackHandler auto-creates traces — no manual trace creation needed
    - Uses get_client().start_as_current_observation() for custom spans
    - Uses propagate_attributes() for session/user/tag context (issue #25)
    - Each LangGraph node becomes a span within the trace
    - Retry loops are visible as separate spans within the trace

Usage:
    from src.tracing import create_trace_config, TracingConfig

    config = create_trace_config(
        enabled=True,
        session_id="session-1",
        tags=["orchestrator", "phase-2"],
        metadata={"task": "auth-middleware"},
    )

    # Wrap workflow.invoke in propagation context
    with config.propagation_context():
        result = workflow.invoke(state, config={"callbacks": config.callbacks})
"""

import logging
import os
import re
from contextlib import contextmanager
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)


# -- Secret Redaction --
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


# -- Tracing Configuration --

@dataclass
class TracingConfig:
    """Configuration for a single traced orchestrator run.

    Attributes:
        enabled: Whether tracing is active. When False, callbacks is empty.
        callbacks: List of LangChain callbacks to pass to graph.invoke().
        session_id: Optional session ID for grouping related traces.
        tags: Tags for filtering traces in Langfuse UI.
        metadata: Additional metadata to attach to the trace.
    """
    enabled: bool = False
    callbacks: list = field(default_factory=list)
    session_id: str | None = None
    tags: list[str] = field(default_factory=list)
    metadata: dict[str, str] = field(default_factory=dict)

    @contextmanager
    def propagation_context(self):
        """Context manager that wraps workflow.invoke() with propagate_attributes().

        In Langfuse v4 (OTEL-based), trace-level attributes like session_id,
        tags, and metadata must be set via propagate_attributes() wrapping the
        LangChain/LangGraph invocation. Without this, CallbackHandler creates
        traces but they lack session grouping and metadata.

        When tracing is disabled, yields a no-op context.

        Usage:
            with config.propagation_context():
                result = workflow.invoke(state, config={"callbacks": config.callbacks})
        """
        if not self.enabled:
            yield
            return

        # Build the propagation context manager BEFORE yielding, so
        # setup errors don't interfere with the caller's exceptions.
        ctx = None
        try:
            from langfuse import propagate_attributes

            kwargs: dict = {}
            if self.session_id:
                kwargs["session_id"] = self.session_id
            if self.tags:
                kwargs["tags"] = self.tags
            if self.metadata:
                safe_meta = {}
                for k, v in self.metadata.items():
                    val = redact_secrets(str(v)) if isinstance(v, str) else str(v)
                    safe_meta[k] = val[:200]
                kwargs["metadata"] = safe_meta

            ctx = propagate_attributes(**kwargs)
        except ImportError:
            logger.warning(
                "langfuse.propagate_attributes not available — "
                "upgrade to langfuse>=3.0 for trace context propagation"
            )
        except Exception as e:
            logger.warning("Failed to propagate trace attributes: %s", e)

        if ctx is not None:
            with ctx:
                logger.debug(
                    "Propagating trace attributes: session_id=%s, tags=%s, metadata_keys=%s",
                    self.session_id,
                    self.tags,
                    list(self.metadata.keys()) if self.metadata else [],
                )
                yield
        else:
            yield

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
    tags: list[str] | None = None,
    metadata: dict | None = None,
) -> TracingConfig:
    """Create a TracingConfig for an orchestrator run.

    If enabled=True but Langfuse is not configured (missing env vars),
    tracing is silently disabled with a warning. This ensures the
    orchestrator never crashes due to missing observability config.

    Langfuse v4 (OTEL-based):
        - CallbackHandler() is self-contained — auto-creates traces
        - No manual client.trace() call needed
        - Session/user context set via propagate_attributes()
        - Metadata values must be dict[str, str] with values <= 200 chars
        - Tags support filtering in Langfuse UI

    Args:
        enabled: Whether to enable tracing.
        task_description: The task being executed (included in trace metadata).
        session_id: Optional session ID for grouping traces.
        trace_name: Name for the trace in Langfuse UI.
        tags: Tags for filtering traces (e.g., ["orchestrator", "phase-2"]).
        metadata: Additional metadata to attach to the trace.

    Returns:
        TracingConfig with callbacks and propagation context ready.
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

    # Build trace metadata from task_description + any additional metadata
    trace_metadata: dict[str, str] = {}
    if task_description:
        trace_metadata["task_description"] = task_description[:200]
    if metadata:
        for k, v in metadata.items():
            val = redact_secrets(str(v)) if isinstance(v, str) else str(v)
            trace_metadata[k] = val[:200]

    # Default tags include the trace name
    trace_tags = list(tags or [])
    if trace_name and trace_name not in trace_tags:
        trace_tags.append(trace_name)

    try:
        from langfuse.langchain import CallbackHandler

        # Langfuse v4: CallbackHandler is self-contained.
        # It auto-creates a trace when LangChain invokes with this callback.
        # Credentials are read from LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY env vars.
        handler = CallbackHandler()

        logger.info(
            "Langfuse tracing initialized (v4 OTEL-based) — "
            "session_id=%s, tags=%s",
            session_id,
            trace_tags,
        )

        return TracingConfig(
            enabled=True,
            callbacks=[handler],
            session_id=session_id,
            tags=trace_tags,
            metadata=trace_metadata,
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

    In Langfuse v4 (OTEL-based), custom events are recorded as
    observations via get_client().start_as_current_observation().
    The level parameter is passed directly to the SDK for proper
    Langfuse-level semantics (filtering/status behavior).

    When called within a propagation_context(), events are properly
    attached to the active trace with session_id and tags inherited.

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

        with client.start_as_current_observation(
            as_type="event",
            name=name,
            metadata=safe_metadata,
            level=level,
        ):
            pass  # event is point-in-time, no body needed

        logger.debug("Trace event '%s': %s", name, safe_metadata)

    except Exception as e:
        logger.debug("Failed to add trace event '%s': %s", name, e)
