"""Tests for Step 6: Langfuse observability tracing.

Unit tests cover:
- Secret redaction patterns
- TracingConfig structure and flush behavior
- create_trace_config with various env states
- add_trace_event with and without active tracing
- Graceful degradation when Langfuse is not configured

All Langfuse SDK calls are mocked — no real API calls made.
"""

import os
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
        assert config.trace_id is None

    def test_flush_when_disabled_is_safe(self):
        config = TracingConfig(enabled=False)
        config.flush()  # Should not raise

    @patch("src.tracing.get_client")
    def test_flush_when_enabled(self, mock_get_client):
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        config = TracingConfig(enabled=True, trace_id="test-trace-id")
        config.flush()

        mock_client.flush.assert_called_once()

    @patch("src.tracing.get_client", side_effect=Exception("flush failed"))
    def test_flush_handles_errors(self, mock_get_client):
        config = TracingConfig(enabled=True, trace_id="test-trace-id")
        config.flush()  # Should not raise


# ============================================================
# create_trace_config
# ============================================================


class TestCreateTraceConfig:
    """Test trace config creation with various scenarios."""

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

    @patch("src.tracing.CallbackHandler")
    @patch("src.tracing.get_client")
    def test_configured_returns_enabled(self, mock_get_client, mock_handler_cls):
        mock_client = MagicMock()
        mock_trace = MagicMock()
        mock_trace.id = "trace-abc123"
        mock_client.trace.return_value = mock_trace
        mock_get_client.return_value = mock_client

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
        assert config.trace_id == "trace-abc123"
        assert config.session_id == "session-1"
        assert len(config.callbacks) == 1

        # Verify trace was created with correct params
        mock_client.trace.assert_called_once()
        call_kwargs = mock_client.trace.call_args[1]
        assert call_kwargs["name"] == "orchestrator-run"
        assert call_kwargs["session_id"] == "session-1"
        assert "task_preview" in call_kwargs["metadata"]

    @patch("src.tracing.CallbackHandler")
    @patch("src.tracing.get_client")
    def test_secrets_redacted_in_metadata(self, mock_get_client, mock_handler_cls):
        mock_client = MagicMock()
        mock_trace = MagicMock()
        mock_trace.id = "trace-xyz"
        mock_client.trace.return_value = mock_trace
        mock_get_client.return_value = mock_client
        mock_handler_cls.return_value = MagicMock()

        with patch.dict(os.environ, {
            "LANGFUSE_PUBLIC_KEY": "pk-lf-test",
            "LANGFUSE_SECRET_KEY": "sk-lf-test",
        }):
            config = create_trace_config(
                enabled=True,
                task_description="Use token: sk-ant-mysecretkey1234567890abc",
            )

        call_kwargs = mock_client.trace.call_args[1]
        assert "sk-ant-" not in call_kwargs["metadata"]["task_preview"]

    @patch("src.tracing.get_client", side_effect=Exception("SDK init failed"))
    def test_sdk_error_returns_disabled(self, mock_get_client):
        with patch.dict(os.environ, {
            "LANGFUSE_PUBLIC_KEY": "pk-lf-test",
            "LANGFUSE_SECRET_KEY": "sk-lf-test",
        }):
            config = create_trace_config(enabled=True)
            assert config.enabled is False

    @patch("src.tracing.CallbackHandler")
    @patch("src.tracing.get_client")
    def test_custom_metadata_included(self, mock_get_client, mock_handler_cls):
        mock_client = MagicMock()
        mock_trace = MagicMock()
        mock_trace.id = "trace-meta"
        mock_client.trace.return_value = mock_trace
        mock_get_client.return_value = mock_client
        mock_handler_cls.return_value = MagicMock()

        with patch.dict(os.environ, {
            "LANGFUSE_PUBLIC_KEY": "pk-lf-test",
            "LANGFUSE_SECRET_KEY": "sk-lf-test",
        }):
            config = create_trace_config(
                enabled=True,
                metadata={"model": "gemini-2.0", "retry_count": 0},
            )

        call_kwargs = mock_client.trace.call_args[1]
        assert call_kwargs["metadata"]["model"] == "gemini-2.0"
        assert call_kwargs["metadata"]["retry_count"] == 0


# ============================================================
# add_trace_event
# ============================================================


class TestAddTraceEvent:
    """Test custom event recording."""

    def test_noop_when_disabled(self):
        config = TracingConfig(enabled=False)
        add_trace_event(config, "test_event")  # Should not raise

    def test_noop_when_no_trace_id(self):
        config = TracingConfig(enabled=True, trace_id=None)
        add_trace_event(config, "test_event")  # Should not raise

    @patch("src.tracing.get_client")
    def test_event_sent_when_enabled(self, mock_get_client):
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        config = TracingConfig(enabled=True, trace_id="trace-evt")
        add_trace_event(
            config,
            name="memory_query",
            level="DEFAULT",
            metadata={"tier": "L0-Core", "results": 5},
        )

        mock_client.event.assert_called_once()
        call_kwargs = mock_client.event.call_args[1]
        assert call_kwargs["trace_id"] == "trace-evt"
        assert call_kwargs["name"] == "memory_query"
        assert call_kwargs["metadata"]["tier"] == "L0-Core"

    @patch("src.tracing.get_client")
    def test_secrets_redacted_in_event_metadata(self, mock_get_client):
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        config = TracingConfig(enabled=True, trace_id="trace-sec")
        add_trace_event(
            config,
            name="debug_info",
            metadata={"api_key": "sk-ant-supersecretkey1234567890abc"},
        )

        call_kwargs = mock_client.event.call_args[1]
        assert "sk-ant-" not in call_kwargs["metadata"]["api_key"]
        assert "[REDACTED]" in call_kwargs["metadata"]["api_key"]

    @patch("src.tracing.get_client", side_effect=Exception("event failed"))
    def test_event_error_does_not_raise(self, mock_get_client):
        config = TracingConfig(enabled=True, trace_id="trace-err")
        add_trace_event(config, "test_event")  # Should not raise


# ============================================================
# Integration: full trace lifecycle (mocked SDK)
# ============================================================


class TestTracingLifecycle:
    """Test the full create → event → flush lifecycle."""

    @patch("src.tracing.CallbackHandler")
    @patch("src.tracing.get_client")
    def test_full_lifecycle(self, mock_get_client, mock_handler_cls):
        mock_client = MagicMock()
        mock_trace = MagicMock()
        mock_trace.id = "trace-lifecycle"
        mock_client.trace.return_value = mock_trace
        mock_get_client.return_value = mock_client
        mock_handler_cls.return_value = MagicMock()

        with patch.dict(os.environ, {
            "LANGFUSE_PUBLIC_KEY": "pk-lf-test",
            "LANGFUSE_SECRET_KEY": "sk-lf-test",
        }):
            # 1. Create trace config
            config = create_trace_config(
                enabled=True,
                task_description="Build an email validator",
                session_id="session-lifecycle",
            )
            assert config.enabled is True
            assert config.trace_id == "trace-lifecycle"

            # 2. Add events for each phase
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

        # Verify all events were sent
        assert mock_client.event.call_count == 5
        mock_client.flush.assert_called_once()
