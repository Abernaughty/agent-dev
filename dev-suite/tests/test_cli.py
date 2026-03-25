"""Tests for the CLI module.

All tests are mocked — no live API calls. Tests cover argument parsing,
config validation, output formatting, and command dispatch.
"""

import os
from unittest.mock import MagicMock, patch

import pytest

from src.cli import (
    __version__,
    _check_env_key,
    build_parser,
    handle_dry_run,
    handle_plan,
    handle_run,
    main,
    validate_config,
)

# ── Fixtures ──


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Ensure a clean env for each test.

    Removes all keys that could interfere with config validation,
    then sets known values for the required keys.
    """
    keys_to_clear = [
        "GOOGLE_API_KEY", "ANTHROPIC_API_KEY", "E2B_API_KEY",
        "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY",
        "ARCHITECT_MODEL", "DEVELOPER_MODEL", "QA_MODEL",
        "TOKEN_BUDGET", "MAX_RETRIES",
    ]
    for key in keys_to_clear:
        monkeypatch.delenv(key, raising=False)


@pytest.fixture
def valid_env(monkeypatch):
    """Set all required env vars to valid values."""
    monkeypatch.setenv("GOOGLE_API_KEY", "test-google-key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-anthropic-key")
    monkeypatch.setenv("E2B_API_KEY", "test-e2b-key")


@pytest.fixture
def full_env(valid_env, monkeypatch):
    """Set all env vars including optional ones."""
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-test")


# ── Version & Help ──


class TestHelpAndVersion:

    def test_version_output(self, capsys):
        """--version prints version and exits."""
        with pytest.raises(SystemExit) as exc_info:
            main(["--version"])
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert __version__ in captured.out

    def test_help_output(self, capsys):
        """--help prints usage and exits."""
        with pytest.raises(SystemExit) as exc_info:
            main(["--help"])
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "dev-suite" in captured.out
        assert "run" in captured.out

    def test_no_command_shows_help(self, capsys):
        """No subcommand prints help and returns 0."""
        result = main([])
        assert result == 0
        captured = capsys.readouterr()
        assert "dev-suite" in captured.out

    def test_run_help(self, capsys):
        """run --help shows run-specific options."""
        with pytest.raises(SystemExit) as exc_info:
            main(["run", "--help"])
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "--dry-run" in captured.out
        assert "--plan" in captured.out
        assert "--budget" in captured.out


# ── Argument Parsing ──


class TestArgParsing:

    def test_run_parses_task(self):
        parser = build_parser()
        args = parser.parse_args(["run", "Build a REST API"])
        assert args.command == "run"
        assert args.task == "Build a REST API"

    def test_dry_run_flag(self):
        parser = build_parser()
        args = parser.parse_args(["run", "test task", "--dry-run"])
        assert args.dry_run is True
        assert args.plan is False

    def test_plan_flag(self):
        parser = build_parser()
        args = parser.parse_args(["run", "test task", "--plan"])
        assert args.plan is True
        assert args.dry_run is False

    def test_dry_run_and_plan_mutually_exclusive(self):
        """--dry-run and --plan cannot be used together."""
        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["run", "test", "--dry-run", "--plan"])

    def test_model_overrides(self):
        parser = build_parser()
        args = parser.parse_args([
            "run", "test",
            "--model-architect", "gemini-2.0-ultra",
            "--model-developer", "deepseek-coder-v3",
            "--model-qa", "claude-haiku-4-5-20251001",
        ])
        assert args.model_architect == "gemini-2.0-ultra"
        assert args.model_developer == "deepseek-coder-v3"
        assert args.model_qa == "claude-haiku-4-5-20251001"

    def test_budget_override(self):
        parser = build_parser()
        args = parser.parse_args(["run", "test", "--budget", "100000"])
        assert args.budget == 100000

    def test_workspace_override(self):
        parser = build_parser()
        args = parser.parse_args(["run", "test", "--workspace", "/tmp/myproject"])
        assert args.workspace == "/tmp/myproject"

    def test_verbose_flag(self):
        parser = build_parser()
        args = parser.parse_args(["run", "test", "--verbose"])
        assert args.verbose is True

    def test_verbose_short_flag(self):
        parser = build_parser()
        args = parser.parse_args(["run", "test", "-v"])
        assert args.verbose is True

    def test_no_trace_flag(self):
        parser = build_parser()
        args = parser.parse_args(["run", "test", "--no-trace"])
        assert args.no_trace is True

    def test_defaults(self):
        parser = build_parser()
        args = parser.parse_args(["run", "test"])
        assert args.dry_run is False
        assert args.plan is False
        assert args.model_architect is None
        assert args.model_developer is None
        assert args.model_qa is None
        assert args.budget is None
        assert args.workspace is None
        assert args.verbose is False
        assert args.no_trace is False


# ── Config Validation ──


class TestConfigValidation:

    def test_check_env_key_present(self, monkeypatch):
        monkeypatch.setenv("TEST_KEY", "real-value")
        assert _check_env_key("TEST_KEY") is True

    def test_check_env_key_missing(self):
        assert _check_env_key("NONEXISTENT_KEY_XYZ") is False

    def test_check_env_key_placeholder(self, monkeypatch):
        monkeypatch.setenv("TEST_KEY", "your-key-here")
        assert _check_env_key("TEST_KEY") is False

    def test_check_env_key_empty(self, monkeypatch):
        monkeypatch.setenv("TEST_KEY", "")
        assert _check_env_key("TEST_KEY") is False

    def test_validate_config_all_present(self, valid_env):
        config = validate_config()
        assert config["valid"] is True
        assert config["missing"] == []

    def test_validate_config_missing_keys(self):
        config = validate_config()
        assert config["valid"] is False
        assert "GOOGLE_API_KEY" in config["missing"]
        assert "ANTHROPIC_API_KEY" in config["missing"]
        assert "E2B_API_KEY" in config["missing"]

    def test_validate_config_partial_keys(self, monkeypatch):
        monkeypatch.setenv("GOOGLE_API_KEY", "real-key")
        # Missing ANTHROPIC_API_KEY and E2B_API_KEY
        config = validate_config()
        assert config["valid"] is False
        assert "GOOGLE_API_KEY" not in config["missing"]
        assert "ANTHROPIC_API_KEY" in config["missing"]

    def test_validate_config_default_models(self, valid_env):
        config = validate_config()
        assert config["models"]["architect"] == "gemini-3-flash-preview"
        assert config["models"]["developer"] == "claude-sonnet-4-20250514"
        assert config["models"]["qa"] == "claude-sonnet-4-20250514"

    def test_validate_config_overridden_models(self, valid_env, monkeypatch):
        monkeypatch.setenv("ARCHITECT_MODEL", "gemini-2.0-ultra")
        monkeypatch.setenv("DEVELOPER_MODEL", "deepseek-coder")
        config = validate_config()
        assert config["models"]["architect"] == "gemini-2.0-ultra"
        assert config["models"]["developer"] == "deepseek-coder"

    def test_validate_config_budget_defaults(self, valid_env):
        config = validate_config()
        assert config["budget"]["token_budget"] == 50000
        assert config["budget"]["max_retries"] == 3

    def test_validate_config_budget_overrides(self, valid_env, monkeypatch):
        monkeypatch.setenv("TOKEN_BUDGET", "100000")
        monkeypatch.setenv("MAX_RETRIES", "5")
        config = validate_config()
        assert config["budget"]["token_budget"] == 100000
        assert config["budget"]["max_retries"] == 5

    def test_validate_config_invalid_token_budget(self, valid_env, monkeypatch):
        """Invalid TOKEN_BUDGET falls back to default and reports error."""
        monkeypatch.setenv("TOKEN_BUDGET", "50k")
        config = validate_config()
        assert config["budget"]["token_budget"] == 50000
        assert config["valid"] is False
        assert any("TOKEN_BUDGET" in e for e in config["errors"])

    def test_validate_config_invalid_max_retries(self, valid_env, monkeypatch):
        """Invalid MAX_RETRIES falls back to default and reports error."""
        monkeypatch.setenv("MAX_RETRIES", "three")
        config = validate_config()
        assert config["budget"]["max_retries"] == 3
        assert any("MAX_RETRIES" in e for e in config["errors"])

    def test_validate_config_valid_numeric_no_errors(self, valid_env):
        """Valid numeric config produces no errors."""
        config = validate_config()
        assert config["errors"] == []

    def test_optional_keys_tracked(self, valid_env):
        config = validate_config()
        assert "LANGFUSE_PUBLIC_KEY" in config["optional_missing"]
        assert "LANGFUSE_SECRET_KEY" in config["optional_missing"]

    def test_optional_keys_present(self, full_env):
        config = validate_config()
        assert config["optional_missing"] == []


# ── Dry Run ──


class TestDryRun:

    def test_dry_run_valid_config(self, valid_env, capsys):
        """Dry run with valid config returns 0."""
        parser = build_parser()
        args = parser.parse_args(["run", "test task", "--dry-run"])
        result = handle_dry_run(args)
        assert result == 0
        captured = capsys.readouterr()
        assert "DRY RUN" in captured.out
        assert "test task" in captured.out

    def test_dry_run_missing_keys(self, capsys):
        """Dry run with missing keys returns 1."""
        parser = build_parser()
        args = parser.parse_args(["run", "test task", "--dry-run"])
        result = handle_dry_run(args)
        assert result == 1
        captured = capsys.readouterr()
        assert "MISSING" in captured.out

    def test_dry_run_invalid_workspace(self, valid_env, capsys):
        """Dry run with nonexistent workspace returns 1."""
        parser = build_parser()
        args = parser.parse_args(
            ["run", "test task", "--dry-run", "--workspace", "/nonexistent/path"]
        )
        result = handle_dry_run(args)
        assert result == 1
        captured = capsys.readouterr()
        assert "does not exist" in captured.out

    def test_dry_run_shows_models(self, valid_env, capsys):
        """Dry run displays resolved model names."""
        parser = build_parser()
        args = parser.parse_args(["run", "test task", "--dry-run"])
        handle_dry_run(args)
        captured = capsys.readouterr()
        assert "gemini-3-flash-preview" in captured.out
        assert "claude-sonnet-4-20250514" in captured.out


# ── Model Overrides ──


class TestModelOverrides:

    def test_overrides_set_env_vars(self, valid_env):
        """Model override flags set the corresponding env vars."""
        parser = build_parser()
        args = parser.parse_args([
            "run", "test",
            "--model-architect", "custom-arch",
            "--model-developer", "custom-dev",
            "--model-qa", "custom-qa",
            "--budget", "99999",
        ])

        from src.cli import _apply_overrides
        _apply_overrides(args)

        assert os.environ["ARCHITECT_MODEL"] == "custom-arch"
        assert os.environ["DEVELOPER_MODEL"] == "custom-dev"
        assert os.environ["QA_MODEL"] == "custom-qa"
        assert os.environ["TOKEN_BUDGET"] == "99999"

    def test_no_overrides_no_env_change(self, valid_env):
        """When no override flags are set, env vars are not modified."""
        parser = build_parser()
        args = parser.parse_args(["run", "test"])

        from src.cli import _apply_overrides

        # Ensure they're not set
        for key in ["ARCHITECT_MODEL", "DEVELOPER_MODEL", "QA_MODEL"]:
            os.environ.pop(key, None)

        _apply_overrides(args)

        assert "ARCHITECT_MODEL" not in os.environ
        assert "DEVELOPER_MODEL" not in os.environ
        assert "QA_MODEL" not in os.environ


# ── Plan Mode ──


class TestPlanMode:

    def test_plan_missing_google_key_exits(self, capsys):
        """--plan with missing GOOGLE_API_KEY returns 1."""
        parser = build_parser()
        args = parser.parse_args(["run", "test task", "--plan"])
        result = handle_plan(args)
        assert result == 1
        captured = capsys.readouterr()
        assert "GOOGLE_API_KEY" in captured.out

    @patch("src.tracing.create_trace_config")
    @patch("src.orchestrator.architect_node")
    def test_plan_only_needs_google_key(self, mock_arch, mock_trace, monkeypatch, capsys):
        """--plan succeeds with only GOOGLE_API_KEY (no Anthropic/E2B needed)."""
        from src.agents.architect import Blueprint

        monkeypatch.setenv("GOOGLE_API_KEY", "test-google-key")
        # Deliberately NOT setting ANTHROPIC_API_KEY or E2B_API_KEY
        mock_trace.return_value = MagicMock(callbacks=[], enabled=False, flush=MagicMock())
        mock_arch.return_value = {
            "blueprint": Blueprint(
                task_id="t1", target_files=["f.py"],
                instructions="x", constraints=[], acceptance_criteria=[],
            ),
            "status": "building", "tokens_used": 100,
            "trace": [], "memory_context": [],
        }
        parser = build_parser()
        args = parser.parse_args(["run", "test task", "--plan", "--no-trace"])
        result = handle_plan(args)
        assert result == 0

    @patch("src.tracing.create_trace_config")
    @patch("src.orchestrator.architect_node")
    def test_plan_calls_architect_only(self, mock_arch, mock_trace, valid_env, capsys):
        """--plan invokes only the architect node via a partial graph."""
        from src.agents.architect import Blueprint

        mock_trace.return_value = MagicMock(callbacks=[], enabled=False, flush=MagicMock())
        mock_bp = Blueprint(
            task_id="test-123",
            target_files=["src/main.py"],
            instructions="Build a thing",
            constraints=["Use Python"],
            acceptance_criteria=["Tests pass"],
        )
        mock_arch.return_value = {
            "blueprint": mock_bp,
            "status": "building",
            "tokens_used": 500,
            "trace": ["architect: done"],
            "memory_context": [],
        }

        parser = build_parser()
        args = parser.parse_args(["run", "test task", "--plan", "--no-trace"])
        result = handle_plan(args)
        assert result == 0
        mock_arch.assert_called_once()

        captured = capsys.readouterr()
        assert "Blueprint" in captured.out or "PLAN" in captured.out
        assert "test-123" in captured.out

    @patch("src.tracing.create_trace_config")
    @patch("src.orchestrator.architect_node")
    def test_plan_architect_failure(self, mock_arch, mock_trace, valid_env, capsys):
        """--plan returns 1 when architect fails to produce a Blueprint."""
        mock_trace.return_value = MagicMock(callbacks=[], enabled=False, flush=MagicMock())
        mock_arch.return_value = {
            "blueprint": None,
            "status": "failed",
            "tokens_used": 200,
            "trace": ["architect: failed"],
            "error_message": "Parse error",
        }

        parser = build_parser()
        args = parser.parse_args(["run", "test task", "--plan", "--no-trace"])
        result = handle_plan(args)
        assert result == 1


# ── Full Run ──


class TestFullRun:

    @patch("src.orchestrator.run_task")
    def test_run_dispatches_to_run_task(self, mock_run, valid_env, capsys):
        """Full run calls run_task and prints results."""
        from src.orchestrator import AgentState, WorkflowStatus

        mock_run.return_value = AgentState(
            task_description="test task",
            status=WorkflowStatus.PASSED,
            tokens_used=3000,
            retry_count=0,
            trace=["architect: done", "developer: done", "qa: pass"],
        )

        parser = build_parser()
        args = parser.parse_args(["run", "test task"])
        result = handle_run(args)
        assert result == 0
        mock_run.assert_called_once_with(
            task_description="test task",
            enable_tracing=True,
        )
        captured = capsys.readouterr()
        assert "PASSED" in captured.out

    @patch("src.orchestrator.run_task")
    def test_run_failure_returns_1(self, mock_run, valid_env, capsys):
        """Full run returns 1 when workflow fails."""
        from src.orchestrator import AgentState, WorkflowStatus

        mock_run.return_value = AgentState(
            task_description="test task",
            status=WorkflowStatus.FAILED,
            tokens_used=1000,
            retry_count=3,
            error_message="Budget exhausted",
            trace=["failed"],
        )

        parser = build_parser()
        args = parser.parse_args(["run", "test task"])
        result = handle_run(args)
        assert result == 1
        captured = capsys.readouterr()
        assert "FAILED" in captured.out

    @patch("src.orchestrator.run_task")
    def test_run_no_trace_flag(self, mock_run, valid_env):
        """--no-trace passes enable_tracing=False to run_task."""
        from src.orchestrator import AgentState, WorkflowStatus

        mock_run.return_value = AgentState(
            task_description="test",
            status=WorkflowStatus.PASSED,
        )

        parser = build_parser()
        args = parser.parse_args(["run", "test", "--no-trace"])
        handle_run(args)
        mock_run.assert_called_once_with(
            task_description="test",
            enable_tracing=False,
        )

    @patch("src.orchestrator.run_task")
    def test_run_exception_returns_1(self, mock_run, valid_env, capsys):
        """Full run returns 1 when run_task raises an exception."""
        mock_run.side_effect = RuntimeError("Connection failed")

        parser = build_parser()
        args = parser.parse_args(["run", "test task"])
        result = handle_run(args)
        assert result == 1
        captured = capsys.readouterr()
        assert "Connection failed" in captured.out


# ── Integration: main() dispatch ──


class TestMainDispatch:

    def test_main_dry_run(self, valid_env, capsys):
        """main() correctly routes to dry-run handler."""
        result = main(["run", "test task", "--dry-run"])
        assert result == 0
        captured = capsys.readouterr()
        assert "DRY RUN" in captured.out

    @patch("src.orchestrator.run_task")
    def test_main_full_run(self, mock_run, valid_env, capsys):
        """main() correctly routes to full run handler."""
        from src.orchestrator import AgentState, WorkflowStatus

        mock_run.return_value = AgentState(
            task_description="test",
            status=WorkflowStatus.PASSED,
        )

        result = main(["run", "test"])
        assert result == 0
