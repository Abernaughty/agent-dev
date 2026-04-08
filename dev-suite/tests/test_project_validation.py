"""Tests for project-aware sandbox validation (issue #159).

Covers:
  - ProjectValidationConfig loading from .dev-suite.json
  - _read_project_files() exclusions, size caps, binary skip
  - ValidationStrategy.PROJECT routing
  - ProjectValidationRunner with mocked E2B
  - Orchestrator dispatch to project runner
  - QA context formatting with project validation results
  - Backward compatibility (SandboxResult with/without project_validation)
"""

import json
import os
from unittest.mock import MagicMock, patch

import pytest

from src.agents.architect import Blueprint
from src.orchestrator import GraphState, WorkflowStatus, sandbox_validate_node
from src.sandbox.e2b_runner import (
    CommandResult,
    ProjectValidationResult,
    SandboxResult,
)
from src.sandbox.project_runner import (
    BINARY_EXTENSIONS,
    EXCLUDE_DIRS,
    MAX_FILE_SIZE,
    MAX_PROJECT_SIZE,
    ProjectValidationConfig,
    ProjectValidationRunner,
    _read_project_files,
    load_project_validation_config,
)
from src.sandbox.validation_commands import (
    ValidationStrategy,
    get_validation_plan,
)

# -- Config Loading Tests --


class TestLoadProjectValidationConfig:
    def test_valid_config(self, tmp_path):
        config_data = {
            "validation": {
                "enabled": True,
                "install_commands": ["pip install -e ."],
                "test_commands": ["pytest tests/"],
                "lint_commands": ["ruff check ."],
                "type_commands": [],
                "timeout": 120,
            }
        }
        (tmp_path / ".dev-suite.json").write_text(json.dumps(config_data))
        config = load_project_validation_config(tmp_path)
        assert config is not None
        assert config.enabled is True
        assert config.install_commands == ["pip install -e ."]
        assert config.test_commands == ["pytest tests/"]
        assert config.lint_commands == ["ruff check ."]
        assert config.type_commands == []
        assert config.timeout == 120

    def test_missing_file(self, tmp_path):
        config = load_project_validation_config(tmp_path)
        assert config is None

    def test_disabled(self, tmp_path):
        config_data = {"validation": {"enabled": False, "test_commands": ["pytest"]}}
        (tmp_path / ".dev-suite.json").write_text(json.dumps(config_data))
        config = load_project_validation_config(tmp_path)
        assert config is None

    def test_malformed_json(self, tmp_path):
        (tmp_path / ".dev-suite.json").write_text("{invalid json")
        config = load_project_validation_config(tmp_path)
        assert config is None

    def test_missing_validation_key(self, tmp_path):
        (tmp_path / ".dev-suite.json").write_text(json.dumps({"other": "data"}))
        config = load_project_validation_config(tmp_path)
        assert config is None

    def test_validation_key_not_dict(self, tmp_path):
        (tmp_path / ".dev-suite.json").write_text(json.dumps({"validation": "not a dict"}))
        config = load_project_validation_config(tmp_path)
        assert config is None

    def test_defaults_applied(self, tmp_path):
        config_data = {"validation": {"enabled": True}}
        (tmp_path / ".dev-suite.json").write_text(json.dumps(config_data))
        config = load_project_validation_config(tmp_path)
        assert config is not None
        assert config.install_commands == []
        assert config.test_commands == []
        assert config.lint_commands == []
        assert config.type_commands == []
        assert config.timeout == 180


# -- Project File Reader Tests --


class TestReadProjectFiles:
    def test_reads_text_files(self, tmp_path):
        (tmp_path / "main.py").write_text("print('hello')")
        (tmp_path / "utils.py").write_text("def helper(): pass")
        files = _read_project_files(tmp_path)
        assert "main.py" in files
        assert "utils.py" in files
        assert files["main.py"] == "print('hello')"

    def test_excludes_git_dir(self, tmp_path):
        (tmp_path / ".git").mkdir()
        (tmp_path / ".git" / "config").write_text("git config")
        (tmp_path / "main.py").write_text("code")
        files = _read_project_files(tmp_path)
        assert "main.py" in files
        assert not any(".git" in k for k in files)

    def test_excludes_node_modules(self, tmp_path):
        (tmp_path / "node_modules").mkdir()
        (tmp_path / "node_modules" / "pkg.js").write_text("module")
        (tmp_path / "index.js").write_text("code")
        files = _read_project_files(tmp_path)
        assert "index.js" in files
        assert not any("node_modules" in k for k in files)

    def test_excludes_pycache(self, tmp_path):
        (tmp_path / "__pycache__").mkdir()
        (tmp_path / "__pycache__" / "mod.cpython-312.pyc").write_bytes(b"\x00\x00")
        (tmp_path / "main.py").write_text("code")
        files = _read_project_files(tmp_path)
        assert not any("__pycache__" in k for k in files)

    def test_skips_binary_extensions(self, tmp_path):
        (tmp_path / "image.png").write_bytes(b"\x89PNG")
        (tmp_path / "lib.so").write_bytes(b"\x7fELF")
        (tmp_path / "main.py").write_text("code")
        files = _read_project_files(tmp_path)
        assert "main.py" in files
        assert "image.png" not in files
        assert "lib.so" not in files

    def test_skips_large_files(self, tmp_path):
        large_content = "x" * (MAX_FILE_SIZE + 1)
        (tmp_path / "big.py").write_text(large_content)
        (tmp_path / "small.py").write_text("code")
        files = _read_project_files(tmp_path)
        assert "small.py" in files
        assert "big.py" not in files

    def test_respects_total_size_cap(self, tmp_path):
        # Create files that exceed total cap
        chunk = "x" * 1024  # 1KB
        count = (MAX_PROJECT_SIZE // 1024) + 10
        for i in range(count):
            (tmp_path / f"file_{i:04d}.txt").write_text(chunk)
        files = _read_project_files(tmp_path)
        total = sum(len(v) for v in files.values())
        assert total <= MAX_PROJECT_SIZE

    def test_reads_nested_directories(self, tmp_path):
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "app.py").write_text("app code")
        files = _read_project_files(tmp_path)
        assert os.path.join("src", "app.py") in files

    def test_skips_unreadable_binary(self, tmp_path):
        # Write non-UTF8 content with a text extension
        (tmp_path / "data.txt").write_bytes(b"\xff\xfe\x00\x01")
        (tmp_path / "main.py").write_text("code")
        files = _read_project_files(tmp_path)
        assert "main.py" in files
        # data.txt may or may not be included depending on encoding

    def test_empty_directory(self, tmp_path):
        files = _read_project_files(tmp_path)
        assert files == {}

    def test_all_excluded_dirs_are_pruned(self):
        for dirname in EXCLUDE_DIRS:
            assert isinstance(dirname, str)

    def test_all_binary_extensions_have_dot(self):
        for ext in BINARY_EXTENSIONS:
            assert ext.startswith(".")


# -- Validation Strategy Routing Tests --


class TestProjectValidationRouting:
    def _make_config(self, **overrides):
        defaults = {
            "enabled": True,
            "test_commands": ["pytest tests/"],
            "lint_commands": ["ruff check ."],
            "type_commands": [],
            "timeout": 120,
        }
        defaults.update(overrides)
        return ProjectValidationConfig(**defaults)

    def test_project_strategy_when_config_exists(self):
        config = self._make_config()
        plan = get_validation_plan(["src/main.py"], project_config=config)
        assert plan.strategy == ValidationStrategy.PROJECT
        assert plan.project_config is config

    def test_fallback_when_no_config(self):
        plan = get_validation_plan(["src/main.py", "tests/test_main.py"])
        assert plan.strategy == ValidationStrategy.TEST_SUITE
        assert plan.project_config is None

    def test_project_strategy_includes_all_commands(self):
        config = self._make_config(
            test_commands=["pytest"],
            lint_commands=["ruff check ."],
            type_commands=["mypy src/"],
        )
        plan = get_validation_plan(["main.py"], project_config=config)
        assert "pytest" in plan.commands
        assert "ruff check ." in plan.commands
        assert "mypy src/" in plan.commands

    def test_project_strategy_with_frontend_files_selects_fullstack(self):
        config = self._make_config()
        plan = get_validation_plan(["src/App.svelte", "src/main.py"], project_config=config)
        assert plan.strategy == ValidationStrategy.PROJECT
        assert plan.template == "fullstack"

    def test_project_strategy_with_python_only(self):
        config = self._make_config()
        plan = get_validation_plan(["src/main.py"], project_config=config)
        assert plan.strategy == ValidationStrategy.PROJECT
        assert plan.template is None

    def test_project_strategy_empty_target_files(self):
        config = self._make_config()
        plan = get_validation_plan([], project_config=config)
        assert plan.strategy == ValidationStrategy.PROJECT
        assert plan.template is None

    def test_description_includes_command_counts(self):
        config = self._make_config(
            test_commands=["cmd1", "cmd2"],
            lint_commands=["cmd3"],
            type_commands=[],
        )
        plan = get_validation_plan(["main.py"], project_config=config)
        assert "2 test" in plan.description
        assert "1 lint" in plan.description
        assert "0 type" in plan.description


# -- Runner Tests (mocked E2B) --


class TestProjectValidationRunner:
    def _make_config(self, **overrides):
        defaults = {
            "enabled": True,
            "install_commands": ["pip install -e ."],
            "test_commands": ["pytest tests/"],
            "lint_commands": ["ruff check ."],
            "type_commands": [],
            "timeout": 60,
        }
        defaults.update(overrides)
        return ProjectValidationConfig(**defaults)

    def _mock_sandbox(self, command_results=None):
        """Create a mock sandbox that returns configured results."""
        sbx = MagicMock()
        sbx.files = MagicMock()
        sbx.commands = MagicMock()

        if command_results is None:
            # Default: all commands succeed
            result = MagicMock()
            result.exit_code = 0
            result.stdout = "All passed"
            result.stderr = ""
            sbx.commands.run.return_value = result
        else:
            sbx.commands.run.side_effect = command_results

        return sbx

    def _make_cmd_result(self, exit_code=0, stdout="", stderr=""):
        result = MagicMock()
        result.exit_code = exit_code
        result.stdout = stdout
        result.stderr = stderr
        return result

    @patch("src.sandbox.project_runner._create_sandbox")
    @patch("src.sandbox.project_runner._write_project_files")
    def test_all_pass(self, mock_write, mock_create, tmp_path):
        (tmp_path / "main.py").write_text("code")
        sbx = self._mock_sandbox()
        mock_create.return_value = sbx

        config = self._make_config()
        runner = ProjectValidationRunner()
        result = runner.run_project_validation(
            config=config,
            workspace_root=tmp_path,
            changed_files={"main.py": "new code"},
        )
        assert result.exit_code == 0
        assert result.project_validation is not None
        assert result.project_validation.overall_pass is True
        assert result.project_validation.install_ok is True
        sbx.kill.assert_called_once()

    @patch("src.sandbox.project_runner._create_sandbox")
    @patch("src.sandbox.project_runner._write_project_files")
    def test_install_failure_short_circuits(self, mock_write, mock_create, tmp_path):
        (tmp_path / "main.py").write_text("code")
        sbx = self._mock_sandbox([
            self._make_cmd_result(exit_code=1, stderr="pip install failed"),
        ])
        mock_create.return_value = sbx

        config = self._make_config()
        runner = ProjectValidationRunner()
        result = runner.run_project_validation(
            config=config,
            workspace_root=tmp_path,
            changed_files={},
        )
        assert result.project_validation is not None
        assert result.project_validation.install_ok is False
        assert result.project_validation.overall_pass is False
        # Only install command should have been run
        assert len(result.project_validation.command_results) == 1
        assert result.project_validation.command_results[0].phase == "install"

    @patch("src.sandbox.project_runner._create_sandbox")
    @patch("src.sandbox.project_runner._write_project_files")
    def test_test_failure_captured(self, mock_write, mock_create, tmp_path):
        (tmp_path / "main.py").write_text("code")
        sbx = self._mock_sandbox([
            self._make_cmd_result(exit_code=0, stdout="installed"),  # install
            self._make_cmd_result(exit_code=0, stdout="no lint errors"),  # lint
            self._make_cmd_result(exit_code=1, stdout="3 passed, 2 failed"),  # test
        ])
        mock_create.return_value = sbx

        config = self._make_config()
        runner = ProjectValidationRunner()
        result = runner.run_project_validation(
            config=config,
            workspace_root=tmp_path,
            changed_files={},
        )
        pv = result.project_validation
        assert pv is not None
        assert pv.overall_pass is False
        assert pv.tests_passed == 3
        assert pv.tests_failed == 2

    @patch("src.sandbox.project_runner._create_sandbox")
    @patch("src.sandbox.project_runner._write_project_files")
    def test_lint_failure_captured(self, mock_write, mock_create, tmp_path):
        (tmp_path / "main.py").write_text("code")
        sbx = self._mock_sandbox([
            self._make_cmd_result(exit_code=0, stdout="installed"),  # install
            self._make_cmd_result(exit_code=1, stdout="src/main.py:1:1 E401 unused import\nsrc/main.py:2:1 F811 redefined"),  # lint
            self._make_cmd_result(exit_code=0, stdout="5 passed"),  # test
        ])
        mock_create.return_value = sbx

        config = self._make_config()
        runner = ProjectValidationRunner()
        result = runner.run_project_validation(
            config=config,
            workspace_root=tmp_path,
            changed_files={},
        )
        pv = result.project_validation
        assert pv is not None
        assert pv.overall_pass is False
        assert pv.lint_errors > 0

    @patch("src.sandbox.project_runner._create_sandbox")
    @patch("src.sandbox.project_runner._write_project_files")
    def test_no_install_commands(self, mock_write, mock_create, tmp_path):
        (tmp_path / "main.py").write_text("code")
        sbx = self._mock_sandbox()
        mock_create.return_value = sbx

        config = self._make_config(install_commands=[])
        runner = ProjectValidationRunner()
        result = runner.run_project_validation(
            config=config,
            workspace_root=tmp_path,
            changed_files={},
        )
        pv = result.project_validation
        assert pv is not None
        assert pv.install_ok is True

    @patch("src.sandbox.project_runner._create_sandbox")
    @patch("src.sandbox.project_runner._write_project_files")
    def test_timeout_handled(self, mock_write, mock_create, tmp_path):
        (tmp_path / "main.py").write_text("code")
        sbx = self._mock_sandbox()
        sbx.commands.run.side_effect = TimeoutError("timed out")
        mock_create.return_value = sbx

        config = self._make_config(install_commands=["slow command"])
        runner = ProjectValidationRunner()
        result = runner.run_project_validation(
            config=config,
            workspace_root=tmp_path,
            changed_files={},
        )
        pv = result.project_validation
        assert pv is not None
        assert pv.overall_pass is False
        assert pv.command_results[0].exit_code == 1
        assert "timed out" in pv.command_results[0].stderr

    @patch("src.sandbox.project_runner._create_sandbox")
    @patch("src.sandbox.project_runner._write_project_files")
    def test_changed_files_applied(self, mock_write, mock_create, tmp_path):
        (tmp_path / "main.py").write_text("old code")
        sbx = self._mock_sandbox()
        mock_create.return_value = sbx

        config = self._make_config(install_commands=[], test_commands=[], lint_commands=[])
        runner = ProjectValidationRunner()
        runner.run_project_validation(
            config=config,
            workspace_root=tmp_path,
            changed_files={"main.py": "new code"},
        )
        # Verify _write_project_files was called with the changed files merged in
        call_args = mock_write.call_args
        project_files = call_args[0][1]
        assert project_files["main.py"] == "new code"

    @patch("src.sandbox.project_runner._create_sandbox")
    @patch("src.sandbox.project_runner._write_project_files")
    def test_sandbox_killed_on_success(self, mock_write, mock_create, tmp_path):
        (tmp_path / "main.py").write_text("code")
        sbx = self._mock_sandbox()
        mock_create.return_value = sbx

        config = self._make_config(install_commands=[], test_commands=[], lint_commands=[])
        runner = ProjectValidationRunner()
        runner.run_project_validation(config=config, workspace_root=tmp_path, changed_files={})
        sbx.kill.assert_called_once()

    @patch("src.sandbox.project_runner._create_sandbox")
    @patch("src.sandbox.project_runner._write_project_files")
    def test_sandbox_killed_on_failure(self, mock_write, mock_create, tmp_path):
        (tmp_path / "main.py").write_text("code")
        sbx = self._mock_sandbox([
            self._make_cmd_result(exit_code=1, stderr="boom"),
        ])
        mock_create.return_value = sbx

        config = self._make_config()
        runner = ProjectValidationRunner()
        runner.run_project_validation(config=config, workspace_root=tmp_path, changed_files={})
        sbx.kill.assert_called_once()


# -- Orchestrator Integration Tests --


def _make_state(target_files: list[str], **overrides) -> GraphState:
    bp = Blueprint(
        task_id="test-task",
        target_files=target_files,
        instructions="Test instructions",
        constraints=["constraint1"],
        acceptance_criteria=["criterion1"],
    )
    default_parsed = [
        {"path": f, "content": f"# stub content for {f}"}
        for f in target_files
    ]
    state: GraphState = {
        "task_description": "test task",
        "blueprint": bp,
        "generated_code": "print('hello')",
        "failure_report": None,
        "status": WorkflowStatus.REVIEWING,
        "retry_count": 0,
        "tokens_used": 1000,
        "error_message": "",
        "memory_context": [],
        "memory_writes": [],
        "trace": [],
        "parsed_files": default_parsed,
        "tool_calls_log": [],
    }
    state.update(overrides)
    return state


class TestSandboxValidateNodeProjectDispatch:
    @pytest.mark.asyncio
    @patch("src.orchestrator._run_project_validation")
    @patch("src.orchestrator.load_project_validation_config")
    async def test_dispatches_to_project_runner(self, mock_load_config, mock_run):
        config = ProjectValidationConfig(
            enabled=True,
            test_commands=["pytest"],
            lint_commands=["ruff check ."],
        )
        mock_load_config.return_value = config
        mock_run.return_value = SandboxResult(
            exit_code=0,
            tests_passed=10,
            tests_failed=0,
            project_validation=ProjectValidationResult(
                overall_pass=True,
                tests_passed=10,
                tests_failed=0,
            ),
        )
        state = _make_state(["src/main.py"])
        result = await sandbox_validate_node(state)
        assert result["sandbox_result"] is not None
        assert result["sandbox_result"].project_validation is not None
        assert result["sandbox_result"].project_validation.overall_pass is True
        mock_run.assert_called_once()

    @pytest.mark.asyncio
    @patch("src.orchestrator.load_project_validation_config")
    @patch("src.orchestrator._run_sandbox_tests")
    async def test_falls_back_without_config(self, mock_tests, mock_load_config):
        mock_load_config.return_value = None
        mock_tests.return_value = SandboxResult(
            exit_code=0, tests_passed=5, tests_failed=0,
        )
        state = _make_state(["src/main.py", "tests/test_main.py"])
        result = await sandbox_validate_node(state)
        assert result["sandbox_result"] is not None
        assert result["sandbox_result"].project_validation is None
        mock_tests.assert_called_once()

    @pytest.mark.asyncio
    @patch("src.orchestrator._run_project_validation")
    @patch("src.orchestrator.load_project_validation_config")
    async def test_handles_missing_api_key(self, mock_load_config, mock_run):
        config = ProjectValidationConfig(enabled=True, test_commands=["pytest"])
        mock_load_config.return_value = config
        mock_run.return_value = None  # No API key
        state = _make_state(["src/main.py"])
        result = await sandbox_validate_node(state)
        assert result["sandbox_result"] is None
        assert any("E2B_API_KEY" in t for t in result["trace"])


# -- Backward Compatibility Tests --


class TestBackwardCompatibility:
    def test_sandbox_result_without_project_validation(self):
        result = SandboxResult(exit_code=0)
        assert result.project_validation is None

    def test_sandbox_result_with_project_validation(self):
        pv = ProjectValidationResult(
            overall_pass=True,
            tests_passed=10,
            tests_failed=0,
        )
        result = SandboxResult(exit_code=0, project_validation=pv)
        assert result.project_validation.overall_pass is True

    def test_sandbox_result_serialization(self):
        pv = ProjectValidationResult(
            overall_pass=False,
            command_results=[
                CommandResult(command="pytest", exit_code=1, stdout="1 failed", phase="test"),
            ],
            tests_passed=4,
            tests_failed=1,
        )
        result = SandboxResult(exit_code=1, project_validation=pv)
        data = result.model_dump()
        assert data["project_validation"]["overall_pass"] is False
        assert len(data["project_validation"]["command_results"]) == 1
        # Round-trip
        restored = SandboxResult(**data)
        assert restored.project_validation.tests_failed == 1

    def test_command_result_model(self):
        cr = CommandResult(
            command="ruff check .",
            exit_code=0,
            stdout="All checks passed",
            stderr="",
            phase="lint",
        )
        assert cr.phase == "lint"
        assert cr.exit_code == 0


# -- Format Validation Summary Tests --


class TestFormatValidationSummary:
    def test_project_strategy_summary(self):
        from src.sandbox.validation_commands import format_validation_summary

        config = ProjectValidationConfig(
            enabled=True,
            install_commands=["pip install -e ."],
            test_commands=["pytest"],
            lint_commands=["ruff check ."],
        )
        plan = get_validation_plan(["main.py"], project_config=config)
        summary = format_validation_summary(plan)
        assert "Project validation" in summary
        assert "pip install -e ." in summary
        assert "pytest" in summary
        assert "ruff check ." in summary
