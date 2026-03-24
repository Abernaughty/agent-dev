"""Tests for Step 6: Langfuse observability tracing.

Unit tests cover:
- Secret redaction patterns
- TracingConfig structure and flush behavior
- create_trace_config with various env states
- add_trace_event with v4 OTEL span creation
- Graceful degradation when Langfuse is not configured

All Langfuse SDK calls are mocked — no real API calls made.

Updated for Langfuse v4 (OTEL-based SDK):
- CallbackHandler() is self-contained, no constructor args
- No manual client.trace() calls — traces are auto-created
- Custom events use client.start_as_current_observation()
- Metadata values are dict[str, str] with 200 char limit
"""

import os
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest

from src.tracing import (
    TracingConfig,
    _is_langfuse_configured,
    add_trace_event,
    create_trace_config,
    redact_secrets,
)


# ============================================================
# Secret redaction
# ============================================================


class TestRedactSecrets:
    """Verify secret patterns are properly redacted."""

    def test_anthropic_key(self):
        text = "My key is sk-ant-abc123def456ghi789jkl012"
        result = redact_secrets(text)
        assert "sk-ant-" not in result
        assert "[REDACTED]" in result

    def test_openai_key(self):
        text = "key=sk-proj1234567890abcdefghij"
        result = redact_secrets(text)
        assert "sk-proj" not in result

    def test_google_key(self):
        text = "AIzaSyAbcdefghij1234567890ABCDEFGHIJKLM"
        result = redact_secrets(text)
        assert "AIza" not in result

    def test_github_pat(self):
        text = "ghp_abcdefghijklmnopqrstuvwxyz0123456789"
        result = redact_secrets(text)
        assert "ghp_" not in result

    def test_e2b_key(self):
        text = "e2b_abc123def456ghi789jkl012"
        result = redact_secrets(text)
        assert "e2b_" not in result

    def test_langfuse_secret_key(self):
        text = "sk-lf-abcdef1234567890abcdef"
        result = redact_secrets(text)
        assert "sk-lf-" not in result

    def test_langfuse_public_key(self):
        text = "pk-lf-abcdef1234567890abcdef"
        result = redact_secrets(text)
        assert "pk-lf-" not in result

    def test_generic_password_pattern(self):
        text = "password=my_super_secret_123"
        result = redact_secrets(text)
        assert "my_super_secret" not in result

    def test_generic_token_pattern(self):
        text = "token: abc123xyz"
        result = redact_secrets(text)
        assert "abc123xyz" not in result

    def test_no_secrets_unchanged(self):
        text = "This is a normal message with no secrets"
        result = redact_secrets(text)
        assert result == text

    def test_empty_string(self):
        assert redact_secrets("") == ""

    def test_none_passthrough(self):
        assert redact_secrets(None) is None

    def test_multiple_secrets_in_one_string(self):
        text = "Key1: sk-ant-abc123def456ghi789jkl012 and ghp_abcdefghijklmnopqrstuvwxyz0123456789"
        result = redact_secrets(text)
        assert "sk-ant-" not in result
        assert "ghp_" not in result
        assert result.count("[REDACTED]") >= 2


# ============================================================
# Langfuse configuration detection
# ============================================================


class TestIsLangfuseConfigured:
    """Test environment variable detection for Langfuse."""

    def test_configured_with_real_keys(self):
        with patch.dict(os.environ, {
            "LANGFUSE_PUBLIC_KEY": "pk-lf-realkey123",
            "LANGFUSE_SECRET_KEY": "sk-lf-realkey456",
        }):
            assert _is_langfuse_configured() is True

    def test_not_configured_missing_keys(self):
        with patch.dict(os.environ, {}, clear=True):
            # Remove the keys entirely
            env = os.environ.copy()
            env.pop("LANGFUSE_PUBLIC_KEY", None)
            env.pop("LANGFUSE_SECRET_KEY", None)
            with patch.dict(os.environ, env, clear=True):
                assert _is_langfuse_configured() is False

    def test_not_configured_placeholder_keys(self):
        with patch.dict(os.environ, {
            "LANGFUSE_PUBLIC_KEY": "your-langfuse-public-key-here",
            "LANGFUSE_SECRET_KEY": "your-langfuse-secret-key-here",
        }):
            assert _is_langfuse_configured() is False

    def test_not_configured_empty_keys(self):
        with patch.dict(os.environ, {
            "LANGFUSE_PUBLIC_KEY": "",
            "LANGFUSE_SECRET_KEY": "",
        }):
            assert _is_langfuse_configured() is False

    def test_not_configured_only_public_key(self):
        with patch.dict(os.environ, {
            "LANGFUSE_PUBLIC_KEY": "pk-lf-realkey123",
            "LANGFUSE_SECRET_KEY": "",
        }):
            assert _is_langfuse_configured() is False


# ============================================================
# TracingConfig
# ============================================================


class TestTracingConfig:
    """Test TracingConfig dataclass behavior."""

    def test_default_disabled(self):
        config = TracingConfig()
        assert config.enabled is False
        assert config.callbacks == []
        assert config.session_id is None

    def test_flush_when_disabled_is_safe(self):
        config = TracingConfig(enabled=False)
        config.flush()  # Should not raise

    @patch("langfuse.get_client")
    def test_flush_when_enabled(self, mock_get_client):
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        config = TracingConfig(enabled=True)
        config.flush()

        mock_client.flush.assert_called_once()

    @patch("langfuse.get_client", side_effect=Exception("flush failed"))
    def test_flush_handles_errors(self, mock_get_client):
        config = TracingConfig(enabled=True)
        config.flush()  # Should not raise


# ============================================================
# create_trace_config (v4 OTEL-based)
# ============================================================


class TestCreateTraceConfig:
    """Test trace config creation with various scenarios.

    In v4, CallbackHandler() is self-contained — no manual client.trace()
    calls are needed. The handler auto-creates traces when LangChain invokes.
    """

    def test_disabled_returns_empty_config(self):
        config = create_trace_config(enabled=False)
        assert config.enabled is False
        assert config.callbacks == []

    def test_no_credentials_returns_disabled(self):
        with patch.dict(os.environ, {
            "LANGFUSE_PUBLIC_KEY": "",
            "LANGFUSE_SECRET_KEY": "",
        }):
            config = create_trace_config(enabled=True)
            assert config.enabled is False

    @patch("langfuse.langchain.CallbackHandler")
    def test_configured_returns_enabled_with_handler(self, mock_handler_cls):
        """v4: CallbackHandler is instantiated with no args.
        No client.trace() call should be made."""
        mock_handler = MagicMock()
        mock_handler_cls.return_value = mock_handler

        with patch.dict(os.environ, {
            "LANGFUSE_PUBLIC_KEY": "pk-lf-test",
            "LANGFUSE_SECRET_KEY": "sk-lf-test",
        }):
            config = create_trace_config(
                enabled=True,
                task_description="Create a function",
                session_id="session-1",
            )

        assert config.enabled is True
        assert config.session_id == "session-1"
        assert len(config.callbacks) == 1
        assert config.callbacks[0] is mock_handler

        # v4: CallbackHandler() takes no constructor args
        mock_handler_cls.assert_called_once_with()

    @patch("langfuse.langchain.CallbackHandler")
    def test_session_id_propagated(self, mock_handler_cls):
        """Session ID is stored on the config for use with propagate_attributes."""
        mock_handler_cls.return_value = MagicMock()

        with patch.dict(os.environ, {
            "LANGFUSE_PUBLIC_KEY": "pk-lf-test",
            "LANGFUSE_SECRET_KEY": "sk-lf-test",
        }):
            config = create_trace_config(
                enabled=True,
                session_id="session-abc",
            )

        assert config.session_id == "session-abc"

    @patch("langfuse.langchain.CallbackHandler", side_effect=Exception("SDK init failed"))
    def test_sdk_error_returns_disabled(self, mock_handler_cls):
        with patch.dict(os.environ, {
            "LANGFUSE_PUBLIC_KEY": "pk-lf-test",
            "LANGFUSE_SECRET_KEY": "sk-lf-test",
        }):
            config = create_trace_config(enabled=True)
            assert config.enabled is False

    @patch("langfuse.langchain.CallbackHandler")
    def test_no_trace_id_in_v4(self, mock_handler_cls):
        """v4: trace_id is not set manually — OTEL auto-generates it."""
        mock_handler_cls.return_value = MagicMock()

        with patch.dict(os.environ, {
            "LANGFUSE_PUBLIC_KEY": "pk-lf-test",
            "LANGFUSE_SECRET_KEY": "sk-lf-test",
        }):
            config = create_trace_config(enabled=True)

        # TracingConfig no longer has trace_id field
        assert not hasattr(config, "trace_id") or config.__dict__.get("trace_id") is None


# ============================================================
# add_trace_event (v4 OTEL event-based)
# ============================================================


class TestAddTraceEvent:
    """Test custom event recording via v4 OTEL observations.

    In v4, add_trace_event creates an event observation via
    client.start_as_current_observation(as_type="event") with
    level passed as a direct SDK argument.
    """

    def test_noop_when_disabled(self):
        config = TracingConfig(enabled=False)
        add_trace_event(config, "test_event")  # Should not raise

    @patch("langfuse.get_client")
    def test_creates_event_when_enabled(self, mock_get_client):
        """v4: Events are recorded as OTEL observations via start_as_current_observation."""
        mock_client = MagicMock()
        captured_kwargs = {}

        # Mock the context manager
        @contextmanager
        def mock_start_observation(**kwargs):
            captured_kwargs.update(kwargs)
            yield MagicMock()

        mock_client.start_as_current_observation = mock_start_observation
        mock_get_client.return_value = mock_client

        config = TracingConfig(enabled=True)
        add_trace_event(
            config,
            name="memory_query",
            level="DEFAULT",
            metadata={"tier": "L0-Core", "results": 5},
        )

        # Verify observation was created with correct type and level as SDK arg
        assert captured_kwargs["as_type"] == "event"
        assert captured_kwargs["level"] == "DEFAULT"
        assert captured_kwargs["name"] == "memory_query"

    @patch("langfuse.get_client")
    def test_span_receives_correct_name_and_metadata(self, mock_get_client):
        """Verify the span name and metadata are passed through."""
        mock_client = MagicMock()
        captured_kwargs = {}

        @contextmanager
        def mock_start_observation(**kwargs):
            captured_kwargs.update(kwargs)
            yield MagicMock()

        mock_client.start_as_current_observation = mock_start_observation
        mock_get_client.return_value = mock_client

        config = TracingConfig(enabled=True)
        add_trace_event(
            config,
            name="budget_check",
            metadata={"tokens_used": 12000, "budget": 50000},
        )

        assert captured_kwargs["name"] == "budget_check"
        assert captured_kwargs["as_type"] == "event"
        assert captured_kwargs["level"] == "DEFAULT"
        # Non-string metadata values are coerced to strings (v4 requirement)
        assert captured_kwargs["metadata"]["tokens_used"] == "12000"
        assert captured_kwargs["metadata"]["budget"] == "50000"

    @patch("langfuse.get_client")
    def test_secrets_redacted_in_event_metadata(self, mock_get_client):
        """String metadata values have secrets redacted before span creation."""
        mock_client = MagicMock()
        captured_kwargs = {}

        @contextmanager
        def mock_start_observation(**kwargs):
            captured_kwargs.update(kwargs)
            yield MagicMock()

        mock_client.start_as_current_observation = mock_start_observation
        mock_get_client.return_value = mock_client

        config = TracingConfig(enabled=True)
        add_trace_event(
            config,
            name="debug_info",
            metadata={"api_key": "sk-ant-supersecretkey1234567890abc"},
        )

        assert "sk-ant-" not in captured_kwargs["metadata"]["api_key"]
        assert "[REDACTED]" in captured_kwargs["metadata"]["api_key"]

    @patch("langfuse.get_client")
    def test_metadata_values_truncated_to_200_chars(self, mock_get_client):
        """v4 requirement: metadata values must be <= 200 characters."""
        mock_client = MagicMock()
        captured_kwargs = {}

        @contextmanager
        def mock_start_observation(**kwargs):
            captured_kwargs.update(kwargs)
            yield MagicMock()

        mock_client.start_as_current_observation = mock_start_observation
        mock_get_client.return_value = mock_client

        config = TracingConfig(enabled=True)
        long_value = "x" * 300
        add_trace_event(
            config,
            name="long_metadata",
            metadata={"long_field": long_value},
        )

        assert len(captured_kwargs["metadata"]["long_field"]) == 200

    @patch("langfuse.get_client", side_effect=Exception("event failed"))
    def test_event_error_does_not_raise(self, mock_get_client):
        config = TracingConfig(enabled=True)
        add_trace_event(config, "test_event")  # Should not raise

    @patch("langfuse.get_client")
    def test_no_metadata_creates_span_with_empty_dict(self, mock_get_client):
        """When no metadata is provided, span is created with empty metadata."""
        mock_client = MagicMock()
        captured_kwargs = {}

        @contextmanager
        def mock_start_observation(**kwargs):
            captured_kwargs.update(kwargs)
            yield MagicMock()

        mock_client.start_as_current_observation = mock_start_observation
        mock_get_client.return_value = mock_client

        config = TracingConfig(enabled=True)
        add_trace_event(config, name="simple_event")

        assert captured_kwargs["metadata"] == {}


# ============================================================
# Integration: full trace lifecycle (mocked SDK)
# ============================================================


class TestTracingLifecycle:
    """Test the full create → event → flush lifecycle with v4 SDK."""

    @patch("langfuse.langchain.CallbackHandler")
    @patch("langfuse.get_client")
    def test_full_lifecycle(self, mock_get_client, mock_handler_cls):
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_handler_cls.return_value = MagicMock()

        # Track span creation calls
        span_names = []

        @contextmanager
        def mock_start_observation(**kwargs):
            span_names.append(kwargs.get("name"))
            yield MagicMock()

        mock_client.start_as_current_observation = mock_start_observation

        with patch.dict(os.environ, {
            "LANGFUSE_PUBLIC_KEY": "pk-lf-test",
            "LANGFUSE_SECRET_KEY": "sk-lf-test",
        }):
            # 1. Create trace config (v4: no client.trace() call)
            config = create_trace_config(
                enabled=True,
                task_description="Build an email validator",
                session_id="session-lifecycle",
            )
            assert config.enabled is True
            assert config.session_id == "session-lifecycle"

            # 2. Add events for each phase (v4: creates OTEL observations)
            add_trace_event(config, "memory_query", metadata={"results": 10})
            add_trace_event(config, "architect_start")
            add_trace_event(config, "developer_start", metadata={"retry": 0})
            add_trace_event(config, "qa_review", metadata={"verdict": "pass"})
            add_trace_event(config, "budget_check", metadata={
                "tokens_used": 12000,
                "budget": 50000,
            })

            # 3. Flush
            config.flush()

        # Verify all events created observations
        assert span_names == [
            "memory_query",
            "architect_start",
            "developer_start",
            "qa_review",
            "budget_check",
        ]
        mock_client.flush.assert_called_once()
