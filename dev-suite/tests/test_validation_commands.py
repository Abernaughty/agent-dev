"""Tests for validation command mapping.

Validates that file types are correctly mapped to sandbox templates
and validation commands.
"""

from src.sandbox.validation_commands import (
    FRONTEND_COMMANDS,
    FULLSTACK_COMMANDS,
    PYTHON_COMMANDS,
    ValidationPlan,
    format_validation_summary,
    get_validation_plan,
)


class TestGetValidationPlan:
    """Tests for get_validation_plan()."""

    # -- Python files --

    def test_python_only_files(self):
        plan = get_validation_plan(["src/main.py", "tests/test_main.py"])
        assert plan.template is None
        assert plan.commands == PYTHON_COMMANDS
        assert "Python" in plan.description

    def test_pyi_stub_file(self):
        plan = get_validation_plan(["src/types.pyi"])
        assert plan.template is None
        assert plan.commands == PYTHON_COMMANDS

    # -- Frontend files --

    def test_svelte_files(self):
        plan = get_validation_plan(["dashboard/src/lib/Widget.svelte"])
        assert plan.template == "fullstack"
        assert plan.commands == FRONTEND_COMMANDS
        assert "Frontend" in plan.description

    def test_typescript_files(self):
        plan = get_validation_plan(["dashboard/src/routes/+page.ts"])
        assert plan.template == "fullstack"
        assert plan.commands == FRONTEND_COMMANDS

    def test_javascript_files(self):
        plan = get_validation_plan(["src/utils.js"])
        assert plan.template == "fullstack"
        assert plan.commands == FRONTEND_COMMANDS

    def test_jsx_files(self):
        plan = get_validation_plan(["src/Component.jsx"])
        assert plan.template == "fullstack"
        assert plan.commands == FRONTEND_COMMANDS

    def test_tsx_files(self):
        plan = get_validation_plan(["src/Component.tsx"])
        assert plan.template == "fullstack"
        assert plan.commands == FRONTEND_COMMANDS

    def test_css_files(self):
        plan = get_validation_plan(["dashboard/src/app.css"])
        assert plan.template == "fullstack"
        assert plan.commands == FRONTEND_COMMANDS

    def test_vue_files(self):
        plan = get_validation_plan(["src/App.vue"])
        assert plan.template == "fullstack"
        assert plan.commands == FRONTEND_COMMANDS

    # -- Mixed files --

    def test_mixed_python_and_frontend(self):
        files = [
            "dev-suite/src/api/main.py",
            "dashboard/src/lib/Widget.svelte",
        ]
        plan = get_validation_plan(files)
        assert plan.template == "fullstack"
        assert plan.commands == FULLSTACK_COMMANDS
        assert "Full-stack" in plan.description

    def test_mixed_python_and_typescript(self):
        files = ["src/server.py", "dashboard/src/routes/+page.ts"]
        plan = get_validation_plan(files)
        assert plan.template == "fullstack"
        assert plan.commands == FULLSTACK_COMMANDS

    # -- Non-code files --

    def test_non_code_files(self):
        files = ["data.json", "config.yaml", "schema.sql"]
        plan = get_validation_plan(files)
        assert plan.template is None
        assert plan.commands == []
        assert "non-code" in plan.description.lower()

    def test_markdown_files(self):
        plan = get_validation_plan(["README.md", "CHANGELOG.md"])
        assert plan.commands == []

    def test_makefile(self):
        plan = get_validation_plan(["Makefile"])
        assert plan.commands == []

    # -- Edge cases --

    def test_empty_files_list(self):
        plan = get_validation_plan([])
        assert plan.template is None
        assert plan.commands == []
        assert "skipping" in plan.description.lower()

    def test_mixed_code_and_non_code(self):
        """Non-code files alongside Python should still trigger Python validation."""
        files = ["src/main.py", "config.yaml"]
        plan = get_validation_plan(files)
        assert plan.commands == PYTHON_COMMANDS

    def test_mixed_frontend_and_non_code(self):
        """Non-code files alongside frontend should still trigger frontend validation."""
        files = ["dashboard/src/App.svelte", "README.md"]
        plan = get_validation_plan(files)
        assert plan.template == "fullstack"
        assert plan.commands == FRONTEND_COMMANDS


class TestValidationPlan:
    """Tests for the ValidationPlan dataclass."""

    def test_default_values(self):
        plan = ValidationPlan()
        assert plan.template is None
        assert plan.commands == []
        assert plan.description == ""

    def test_custom_values(self):
        plan = ValidationPlan(
            template="fullstack",
            commands=["pnpm check"],
            description="Frontend validation",
        )
        assert plan.template == "fullstack"
        assert plan.commands == ["pnpm check"]


class TestFormatValidationSummary:
    """Tests for format_validation_summary()."""

    def test_no_commands(self):
        plan = ValidationPlan(description="No validation needed")
        result = format_validation_summary(plan)
        assert result == "No validation needed"

    def test_with_commands(self):
        plan = ValidationPlan(
            description="Python validation: 2 files",
            commands=["ruff check .", "pytest tests/"],
        )
        result = format_validation_summary(plan)
        assert "Python validation: 2 files" in result
        assert "$ ruff check ." in result
        assert "$ pytest tests/" in result

    def test_commands_indented(self):
        plan = ValidationPlan(
            description="Test",
            commands=["cmd1"],
        )
        result = format_validation_summary(plan)
        assert "  $ cmd1" in result
