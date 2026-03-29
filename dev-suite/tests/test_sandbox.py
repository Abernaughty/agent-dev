"""Tests for the E2B sandbox runner.

These tests validate the output wrapper, secret scanning,
template selection, and test output parsing without requiring
an E2B API key (unit tests on parsing logic).

Integration tests that spin up real sandboxes require E2B_API_KEY
and are marked with @pytest.mark.integration.
"""

import os

import pytest

from src.sandbox.e2b_runner import (
    E2BRunner,
    SandboxProfile,
    SandboxResult,
    _extract_errors,
    _get_template_id,
    _parse_test_output,
    _scan_for_secrets,
    select_template_for_files,
)


# -- Unit Tests (no API key needed) --


class TestSecretScanning:
    def test_redacts_anthropic_key(self):
        text = "Using key sk-ant-abc123def456ghi789jkl012mno"
        assert "sk-ant-" not in _scan_for_secrets(text)
        assert "[REDACTED]" in _scan_for_secrets(text)

    def test_redacts_github_pat(self):
        text = "Token: ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghij"
        assert "ghp_" not in _scan_for_secrets(text)

    def test_redacts_github_app_token(self):
        text = "Token: ghs_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghij"
        assert "ghs_" not in _scan_for_secrets(text)

    def test_redacts_npm_token(self):
        text = "Token: npm_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghij"
        assert "npm_" not in _scan_for_secrets(text)
        assert "[REDACTED]" in _scan_for_secrets(text)

    def test_redacts_generic_password(self):
        text = "password=my_super_secret_123"
        assert "my_super_secret_123" not in _scan_for_secrets(text)

    def test_preserves_safe_text(self):
        text = "All 14 tests passed in 2.3s"
        assert _scan_for_secrets(text) == text


class TestTestOutputParsing:
    def test_pytest_output(self):
        output = "===== 14 passed, 2 failed in 3.21s ====="
        result = _parse_test_output(output)
        assert result["tests_passed"] == 14
        assert result["tests_failed"] == 2

    def test_pytest_all_pass(self):
        output = "===== 8 passed in 1.5s ====="
        result = _parse_test_output(output)
        assert result["tests_passed"] == 8
        assert result["tests_failed"] == 0

    def test_jest_output(self):
        output = "Tests: 1 failed, 5 passed, 6 total"
        result = _parse_test_output(output)
        assert result["tests_passed"] == 5
        assert result["tests_failed"] == 1

    def test_unittest_output(self):
        output = "Ran 7 tests in 0.3s\n\nOK"
        result = _parse_test_output(output)
        assert result["tests_passed"] == 7
        assert result["tests_failed"] == 0

    def test_svelte_check_clean(self):
        output = "svelte-check found 0 errors and 0 warnings in 3 files"
        result = _parse_test_output(output)
        assert result["tests_passed"] == 1
        assert result["tests_failed"] == 0

    def test_svelte_check_errors(self):
        output = "svelte-check found 3 errors and 1 warnings in 8 files"
        result = _parse_test_output(output)
        assert result["tests_passed"] == 0
        assert result["tests_failed"] == 3

    def test_tsc_clean(self):
        output = "Found 0 errors. Watching for file changes."
        result = _parse_test_output(output)
        assert result["tests_passed"] == 1
        assert result["tests_failed"] == 0

    def test_tsc_errors(self):
        output = "Found 5 errors in 2 files."
        result = _parse_test_output(output)
        assert result["tests_passed"] == 0
        assert result["tests_failed"] == 5

    def test_no_test_output(self):
        output = "Hello world"
        result = _parse_test_output(output)
        assert result["tests_passed"] is None
        assert result["tests_failed"] is None


class TestErrorExtraction:
    def test_python_traceback(self):
        output = """Traceback (most recent call last):
  File "app.py", line 42
    x = 1 / 0
ZeroDivisionError: division by zero"""
        errors = _extract_errors(output)
        assert any("ZeroDivisionError" in e for e in errors)

    def test_type_error(self):
        output = "TypeError: Cannot read properties of undefined"
        errors = _extract_errors(output)
        assert len(errors) >= 1

    def test_svelte_error(self):
        output = "Error: Expected valid attribute value but found end of tag"
        errors = _extract_errors(output)
        assert any("Expected valid attribute" in e for e in errors)

    def test_caps_errors_at_10(self):
        output = "\n".join(f"ValueError: error {i}" for i in range(20))
        errors = _extract_errors(output)
        assert len(errors) <= 10


class TestSandboxResult:
    def test_default_values(self):
        result = SandboxResult(exit_code=0)
        assert result.tests_passed is None
        assert result.errors == []
        assert result.timed_out is False

    def test_full_result(self):
        result = SandboxResult(
            exit_code=1,
            tests_passed=5,
            tests_failed=2,
            errors=["TypeError in auth.js line 42"],
            output_summary="Test run complete",
            timed_out=False,
        )
        assert result.exit_code == 1
        assert result.tests_failed == 2


# -- Template Selection Tests --


class TestTemplateSelection:
    """Tests for file-type to template routing."""

    def test_python_only_returns_none(self):
        files = ["src/main.py", "tests/test_main.py"]
        assert select_template_for_files(files) is None

    def test_svelte_returns_fullstack(self):
        files = ["dashboard/src/lib/components/Widget.svelte"]
        assert select_template_for_files(files) == "fullstack"

    def test_typescript_returns_fullstack(self):
        files = ["dashboard/src/routes/api/memory/+server.ts"]
        assert select_template_for_files(files) == "fullstack"

    def test_css_returns_fullstack(self):
        files = ["dashboard/src/app.css"]
        assert select_template_for_files(files) == "fullstack"

    def test_mixed_python_and_svelte_returns_fullstack(self):
        files = ["dev-suite/src/api/main.py", "dashboard/src/lib/Widget.svelte"]
        assert select_template_for_files(files) == "fullstack"

    def test_js_returns_fullstack(self):
        files = ["src/utils.js"]
        assert select_template_for_files(files) == "fullstack"

    def test_jsx_returns_fullstack(self):
        files = ["src/component.jsx"]
        assert select_template_for_files(files) == "fullstack"

    def test_empty_files_returns_none(self):
        assert select_template_for_files([]) is None

    def test_no_extension_returns_none(self):
        files = ["Makefile", "README"]
        assert select_template_for_files(files) is None

    def test_unknown_extension_returns_none(self):
        files = ["data.json", "config.yaml", "schema.sql"]
        assert select_template_for_files(files) is None


class TestGetTemplateId:
    """Tests for template name to ID resolution."""

    def test_none_returns_default_env(self, monkeypatch):
        monkeypatch.setenv("E2B_TEMPLATE_DEFAULT", "my-python-tmpl")
        assert _get_template_id(None) == "my-python-tmpl"

    def test_none_returns_none_when_no_env(self, monkeypatch):
        monkeypatch.delenv("E2B_TEMPLATE_DEFAULT", raising=False)
        assert _get_template_id(None) is None

    def test_fullstack_returns_env(self, monkeypatch):
        monkeypatch.setenv("E2B_TEMPLATE_FULLSTACK", "my-fullstack-tmpl")
        assert _get_template_id("fullstack") == "my-fullstack-tmpl"

    def test_fullstack_dev_alias(self, monkeypatch):
        monkeypatch.setenv("E2B_TEMPLATE_FULLSTACK", "my-fullstack-tmpl")
        assert _get_template_id("fullstack-dev") == "my-fullstack-tmpl"

    def test_python_alias(self, monkeypatch):
        monkeypatch.setenv("E2B_TEMPLATE_DEFAULT", "my-python-tmpl")
        assert _get_template_id("python") == "my-python-tmpl"

    def test_raw_id_passthrough(self):
        assert _get_template_id("custom-abc123") == "custom-abc123"

    def test_case_insensitive(self, monkeypatch):
        monkeypatch.setenv("E2B_TEMPLATE_FULLSTACK", "my-tmpl")
        assert _get_template_id("FULLSTACK") == "my-tmpl"
        assert _get_template_id("Fullstack") == "my-tmpl"

    def test_missing_env_returns_none(self, monkeypatch):
        monkeypatch.delenv("E2B_TEMPLATE_FULLSTACK", raising=False)
        assert _get_template_id("fullstack") is None


# -- Integration Tests (require E2B_API_KEY) --

@pytest.mark.integration
class TestE2BIntegration:
    """These tests spin up real E2B sandboxes.

    Run with: uv run pytest tests/test_sandbox.py -v -m integration
    Requires E2B_API_KEY in .env or environment.
    """

    @pytest.fixture
    def runner(self):
        api_key = os.environ.get("E2B_API_KEY")
        if not api_key:
            pytest.skip("E2B_API_KEY not set")
        return E2BRunner(api_key=api_key)

    def test_simple_execution(self, runner):
        result = runner.run("print('hello from sandbox')")
        assert result.exit_code == 0
        assert "hello from sandbox" in result.output_summary

    def test_error_execution(self, runner):
        result = runner.run("raise ValueError('test error')")
        assert result.exit_code == 1
        assert any("ValueError" in e for e in result.errors)

    def test_permissive_gets_no_secrets(self, runner):
        result = runner.run(
            "import os; print(os.environ.get('SECRET', 'not found'))",
            profile=SandboxProfile.PERMISSIVE,
            env_vars={"SECRET": "should_not_appear"},
        )
        assert "should_not_appear" not in result.output_summary
        assert "not found" in result.output_summary

    def test_secret_scanning_in_output(self, runner):
        result = runner.run("print('key is sk-ant-abc123def456ghi789jkl012mno')")
        assert "sk-ant-" not in result.output_summary
        assert "[REDACTED]" in result.output_summary
