"""Tests for validation command mapping.

Validates that file types are correctly mapped to sandbox templates,
validation strategies, and validation commands.

Issue #95: Added tests for ValidationStrategy selection --
SCRIPT_EXEC for simple scripts, TEST_SUITE when tests present.
"""

from src.sandbox.validation_commands import (
    FRONTEND_COMMANDS,
    FULLSTACK_COMMANDS,
    PYTHON_COMMANDS,
    ValidationPlan,
    ValidationStrategy,
    format_validation_summary,
    get_validation_plan,
)


class TestGetValidationPlan:
    """Tests for get_validation_plan()."""

    # -- Python files with tests (TEST_SUITE) --

    def test_python_with_test_files(self):
        plan = get_validation_plan(["src/main.py", "tests/test_main.py"])
        assert plan.strategy == ValidationStrategy.TEST_SUITE
        assert plan.template is None
        assert plan.commands == PYTHON_COMMANDS
        assert "Python" in plan.description

    def test_python_with_test_prefix(self):
        plan = get_validation_plan(["src/utils.py", "test_utils.py"])
        assert plan.strategy == ValidationStrategy.TEST_SUITE
        assert plan.commands == PYTHON_COMMANDS

    def test_python_with_test_suffix(self):
        plan = get_validation_plan(["src/auth.py", "src/auth_test.py"])
        assert plan.strategy == ValidationStrategy.TEST_SUITE

    def test_pyi_stub_with_tests(self):
        plan = get_validation_plan(["src/types.pyi", "tests/test_types.py"])
        assert plan.strategy == ValidationStrategy.TEST_SUITE
        assert plan.commands == PYTHON_COMMANDS

    # -- Python files without tests (SCRIPT_EXEC) --

    def test_single_python_script(self):
        plan = get_validation_plan(["hello_world.py"])
        assert plan.strategy == ValidationStrategy.SCRIPT_EXEC
        assert plan.script_file == "hello_world.py"
        assert plan.commands == []
        assert "Script execution" in plan.description

    def test_python_module_no_tests(self):
        plan = get_validation_plan(["src/utils.py"])
        assert plan.strategy == ValidationStrategy.SCRIPT_EXEC
        assert plan.script_file == "src/utils.py"

    def test_multiple_python_no_tests(self):
        plan = get_validation_plan(["src/main.py", "src/helpers.py"])
        assert plan.strategy == ValidationStrategy.SCRIPT_EXEC
        assert plan.script_file == "src/main.py"

    def test_pyi_only_no_tests(self):
        plan = get_validation_plan(["src/types.pyi"])
        assert plan.strategy == ValidationStrategy.SCRIPT_EXEC
        assert plan.script_file == "src/types.pyi"

    # -- Frontend files (TEST_SUITE) --

    def test_svelte_files(self):
        plan = get_validation_plan(["dashboard/src/lib/Widget.svelte"])
        assert plan.strategy == ValidationStrategy.TEST_SUITE
        assert plan.template == "fullstack"
        assert plan.commands == FRONTEND_COMMANDS
        assert "Frontend" in plan.description

    def test_typescript_files(self):
        plan = get_validation_plan(["dashboard/src/routes/+page.ts"])
        assert plan.strategy == ValidationStrategy.TEST_SUITE
        assert plan.template == "fullstack"
        assert plan.commands == FRONTEND_COMMANDS

    def test_javascript_files(self):
        plan = get_validation_plan(["src/utils.js"])
        assert plan.strategy == ValidationStrategy.TEST_SUITE
        assert plan.template == "fullstack"

    def test_jsx_files(self):
        plan = get_validation_plan(["src/Component.jsx"])
        assert plan.strategy == ValidationStrategy.TEST_SUITE
        assert plan.template == "fullstack"

    def test_tsx_files(self):
        plan = get_validation_plan(["src/Component.tsx"])
        assert plan.strategy == ValidationStrategy.TEST_SUITE
        assert plan.template == "fullstack"

    def test_css_files(self):
        plan = get_validation_plan(["dashboard/src/app.css"])
        assert plan.strategy == ValidationStrategy.TEST_SUITE
        assert plan.template == "fullstack"

    def test_vue_files(self):
        plan = get_validation_plan(["src/App.vue"])
        assert plan.strategy == ValidationStrategy.TEST_SUITE
        assert plan.template == "fullstack"

    # -- Mixed files (TEST_SUITE fullstack) --

    def test_mixed_python_and_frontend(self):
        files = [
            "dev-suite/src/api/main.py",
            "dashboard/src/lib/Widget.svelte",
        ]
        plan = get_validation_plan(files)
        assert plan.strategy == ValidationStrategy.TEST_SUITE
        assert plan.template == "fullstack"
        assert plan.commands == FULLSTACK_COMMANDS
        assert "Full-stack" in plan.description

    def test_mixed_python_and_typescript(self):
        files = ["src/server.py", "dashboard/src/routes/+page.ts"]
        plan = get_validation_plan(files)
        assert plan.strategy == ValidationStrategy.TEST_SUITE
        assert plan.template == "fullstack"
        assert plan.commands == FULLSTACK_COMMANDS

    # -- Non-code files (SKIP) --

    def test_non_code_files(self):
        files = ["data.json", "config.yaml", "schema.sql"]
        plan = get_validation_plan(files)
        assert plan.strategy == ValidationStrategy.SKIP
        assert plan.template is None
        assert plan.commands == []
        assert "non-code" in plan.description.lower()

    def test_markdown_files(self):
        plan = get_validation_plan(["README.md", "CHANGELOG.md"])
        assert plan.strategy == ValidationStrategy.SKIP
        assert plan.commands == []

    def test_makefile(self):
        plan = get_validation_plan(["Makefile"])
        assert plan.strategy == ValidationStrategy.SKIP
        assert plan.commands == []

    # -- Edge cases --

    def test_empty_files_list(self):
        plan = get_validation_plan([])
        assert plan.strategy == ValidationStrategy.SKIP
        assert plan.template is None
        assert plan.commands == []
        assert "skipping" in plan.description.lower()

    def test_mixed_code_and_non_code(self):
        """Non-code files alongside Python without tests -> SCRIPT_EXEC."""
        files = ["src/main.py", "config.yaml"]
        plan = get_validation_plan(files)
        assert plan.strategy == ValidationStrategy.SCRIPT_EXEC
        assert plan.script_file == "src/main.py"

    def test_mixed_code_and_non_code_with_tests(self):
        """Non-code files alongside Python with tests -> TEST_SUITE."""
        files = ["src/main.py", "tests/test_main.py", "config.yaml"]
        plan = get_validation_plan(files)
        assert plan.strategy == ValidationStrategy.TEST_SUITE
        assert plan.commands == PYTHON_COMMANDS

    def test_mixed_frontend_and_non_code(self):
        """Non-code files alongside frontend should still trigger frontend validation."""
        files = ["dashboard/src/App.svelte", "README.md"]
        plan = get_validation_plan(files)
        assert plan.strategy == ValidationStrategy.TEST_SUITE
        assert plan.template == "fullstack"
        assert plan.commands == FRONTEND_COMMANDS

    # -- Script file selection --

    def test_script_file_prefers_non_test(self):
        """_get_primary_script should prefer non-test files."""
        files = ["src/main.py", "test_main.py"]
        plan = get_validation_plan(files)
        assert plan.strategy == ValidationStrategy.TEST_SUITE

    def test_script_file_single_file(self):
        plan = get_validation_plan(["utils.py"])
        assert plan.script_file == "utils.py"


class TestValidationPlan:
    """Tests for the ValidationPlan dataclass."""

    def test_default_values(self):
        plan = ValidationPlan()
        assert plan.strategy == ValidationStrategy.SKIP
        assert plan.template is None
        assert plan.commands == []
        assert plan.script_file is None
        assert plan.description == ""

    def test_custom_values(self):
        plan = ValidationPlan(
            strategy=ValidationStrategy.TEST_SUITE,
            template="fullstack",
            commands=["pnpm check"],
            description="Frontend validation",
        )
        assert plan.strategy == ValidationStrategy.TEST_SUITE
        assert plan.template == "fullstack"
        assert plan.commands == ["pnpm check"]

    def test_script_exec_plan(self):
        plan = ValidationPlan(
            strategy=ValidationStrategy.SCRIPT_EXEC,
            script_file="hello.py",
            description="Run hello.py",
        )
        assert plan.strategy == ValidationStrategy.SCRIPT_EXEC
        assert plan.script_file == "hello.py"
        assert plan.commands == []


class TestFormatValidationSummary:
    """Tests for format_validation_summary()."""

    def test_skip_strategy(self):
        plan = ValidationPlan(
            strategy=ValidationStrategy.SKIP,
            description="No validation needed",
        )
        result = format_validation_summary(plan)
        assert result == "No validation needed"

    def test_script_exec_strategy(self):
        plan = ValidationPlan(
            strategy=ValidationStrategy.SCRIPT_EXEC,
            script_file="hello.py",
            description="Script execution: 1 files",
        )
        result = format_validation_summary(plan)
        assert "Script execution" in result
        assert "python hello.py" in result

    def test_test_suite_with_commands(self):
        plan = ValidationPlan(
            strategy=ValidationStrategy.TEST_SUITE,
            description="Python validation: 2 files",
            commands=["ruff check .", "pytest tests/"],
        )
        result = format_validation_summary(plan)
        assert "Python validation: 2 files" in result
        assert "$ ruff check ." in result
        assert "$ pytest tests/" in result

    def test_commands_indented(self):
        plan = ValidationPlan(
            strategy=ValidationStrategy.TEST_SUITE,
            description="Test",
            commands=["cmd1"],
        )
        result = format_validation_summary(plan)
        assert "  $ cmd1" in result
