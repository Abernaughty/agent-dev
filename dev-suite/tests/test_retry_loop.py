"""Tests for Issue #125: Retry loop improvements.

Covers:
- FailureReport new fields (fix_complexity, exact_fix_hint)
- Developer retry prompt includes file context + sandbox output
- QA leniency for empty acceptance criteria
- _build_retry_file_context and _build_retry_sandbox_context helpers
"""

from unittest.mock import MagicMock, patch

import pytest
from src.agents.architect import Blueprint
from src.agents.qa import FailureReport, FailureType, FixComplexity
from src.orchestrator import (
    GraphState,
    WorkflowStatus,
    _build_retry_file_context,
    _build_retry_sandbox_context,
    developer_node,
    qa_node,
)
from src.sandbox.e2b_runner import SandboxResult


# ---------------------------------------------------------------------------
# FailureReport: new fields (fix_complexity, exact_fix_hint)
# ---------------------------------------------------------------------------


class TestFailureReportNewFields:
    """Issue #125: FailureReport schema additions."""

    def _make_report(self, **overrides):
        defaults = dict(
            task_id="test-task",
            status="fail",
            tests_passed=2,
            tests_failed=1,
            errors=["spacing error"],
            failed_files=["triforce.py"],
            is_architectural=False,
            recommendation="add 6 leading spaces",
        )
        defaults.update(overrides)
        return FailureReport(**defaults)

    def test_fix_complexity_trivial(self):
        r = self._make_report(fix_complexity="trivial")
        assert r.fix_complexity == FixComplexity.TRIVIAL

    def test_fix_complexity_moderate(self):
        r = self._make_report(fix_complexity="moderate")
        assert r.fix_complexity == FixComplexity.MODERATE

    def test_fix_complexity_complex(self):
        r = self._make_report(fix_complexity="complex")
        assert r.fix_complexity == FixComplexity.COMPLEX

    def test_fix_complexity_none_by_default(self):
        r = self._make_report()
        assert r.fix_complexity is None

    def test_fix_complexity_case_insensitive(self):
        r = self._make_report(fix_complexity="TRIVIAL")
        assert r.fix_complexity == FixComplexity.TRIVIAL

    def test_fix_complexity_with_whitespace(self):
        r = self._make_report(fix_complexity="  moderate  ")
        assert r.fix_complexity == FixComplexity.MODERATE

    def test_fix_complexity_unknown_falls_back(self):
        r = self._make_report(fix_complexity="easy_peasy")
        assert r.fix_complexity is None

    def test_fix_complexity_empty_string(self):
        r = self._make_report(fix_complexity="")
        assert r.fix_complexity is None

    def test_exact_fix_hint_present(self):
        r = self._make_report(exact_fix_hint="Line 1: add 6 spaces")
        assert r.exact_fix_hint == "Line 1: add 6 spaces"

    def test_exact_fix_hint_none_by_default(self):
        r = self._make_report()
        assert r.exact_fix_hint is None

    def test_json_round_trip_with_new_fields(self):
        r = self._make_report(
            fix_complexity="trivial",
            exact_fix_hint="Add spaces on line 1",
        )
        data = r.model_dump()
        assert data["fix_complexity"] == "trivial"
        assert data["exact_fix_hint"] == "Add spaces on line 1"
        r2 = FailureReport(**data)
        assert r2.fix_complexity == FixComplexity.TRIVIAL
        assert r2.exact_fix_hint == "Add spaces on line 1"

    def test_backward_compat_without_new_fields(self):
        """Old LLM output without fix_complexity/exact_fix_hint works."""
        from_llm = {
            "task_id": "t1",
            "status": "fail",
            "tests_passed": 1,
            "tests_failed": 1,
            "errors": ["TypeError"],
            "failed_files": ["app.py"],
            "is_architectural": False,
            "recommendation": "fix type error",
        }
        r = FailureReport(**from_llm)
        assert r.fix_complexity is None
        assert r.exact_fix_hint is None
        assert r.failure_type == FailureType.CODE

    def test_pass_report_ignores_new_fields(self):
        r = self._make_report(
            status="pass",
            tests_passed=5,
            tests_failed=0,
            errors=[],
            failed_files=[],
            fix_complexity=None,
            exact_fix_hint=None,
        )
        assert r.fix_complexity is None
        assert r.failure_type is None


# ---------------------------------------------------------------------------
# FixComplexity enum
# ---------------------------------------------------------------------------


class TestFixComplexity:
    def test_values(self):
        assert FixComplexity.TRIVIAL == "trivial"
        assert FixComplexity.MODERATE == "moderate"
        assert FixComplexity.COMPLEX == "complex"

    def test_from_string(self):
        assert FixComplexity("trivial") == FixComplexity.TRIVIAL

    def test_invalid_raises(self):
        with pytest.raises(ValueError):
            FixComplexity("unknown")


# ---------------------------------------------------------------------------
# _build_retry_file_context
# ---------------------------------------------------------------------------


class TestBuildRetryFileContext:
    """Issue #125: File context builder for retry prompts."""

    def _make_blueprint(self, target_files=None):
        return Blueprint(
            task_id="test-retry",
            target_files=target_files if target_files is not None else ["app.py"],
            instructions="Build a test app",
            constraints=[],
            acceptance_criteria=[],
        )

    def _make_failure_report(self, failed_files=None):
        return FailureReport(
            task_id="test-retry",
            status="fail",
            tests_passed=0,
            tests_failed=1,
            errors=["spacing error"],
            failed_files=failed_files if failed_files is not None else ["app.py"],
            is_architectural=False,
            recommendation="fix spacing",
        )

    def test_reads_failed_files_from_disk(self, tmp_path):
        """Should read file content from disk."""
        (tmp_path / "app.py").write_text("print('hello')")
        fr = self._make_failure_report(failed_files=["app.py"])
        bp = self._make_blueprint(target_files=["app.py"])

        result = _build_retry_file_context(fr, bp, tmp_path)

        assert "CURRENT FILES ON DISK" in result
        assert "print('hello')" in result
        assert "--- FILE: app.py ---" in result

    def test_uses_failed_files_not_all_targets(self, tmp_path):
        """Should only read failure_report.failed_files, not all targets."""
        (tmp_path / "app.py").write_text("app code")
        (tmp_path / "utils.py").write_text("utils code")

        fr = self._make_failure_report(failed_files=["app.py"])
        bp = self._make_blueprint(target_files=["app.py", "utils.py"])

        result = _build_retry_file_context(fr, bp, tmp_path)

        assert "app code" in result
        assert "utils code" not in result

    def test_falls_back_to_targets_when_failed_files_empty(self, tmp_path):
        """When failed_files is empty, fall back to blueprint targets."""
        (tmp_path / "app.py").write_text("app code")
        fr = self._make_failure_report(failed_files=[])
        bp = self._make_blueprint(target_files=["app.py"])

        result = _build_retry_file_context(fr, bp, tmp_path)

        assert "app code" in result

    def test_handles_missing_file(self, tmp_path):
        """Missing files should be noted, not crash."""
        fr = self._make_failure_report(failed_files=["nonexistent.py"])
        bp = self._make_blueprint()

        result = _build_retry_file_context(fr, bp, tmp_path)

        assert "not found on disk" in result

    def test_respects_char_budget(self, tmp_path):
        """Large files should be skipped when over budget."""
        (tmp_path / "big.py").write_text("x" * 50000)
        fr = self._make_failure_report(failed_files=["big.py"])
        bp = self._make_blueprint(target_files=["big.py"])

        result = _build_retry_file_context(
            fr, bp, tmp_path, max_chars=1000
        )

        assert "skipped: would exceed" in result
        assert "x" * 50000 not in result

    def test_blocks_path_traversal(self, tmp_path):
        """Path traversal attempts should be blocked."""
        fr = self._make_failure_report(
            failed_files=["../../etc/passwd"]
        )
        bp = self._make_blueprint()

        result = _build_retry_file_context(fr, bp, tmp_path)

        # Path traversal is blocked: file is skipped entirely
        assert "etc/passwd" not in result

    def test_returns_empty_for_no_files(self, tmp_path):
        """No files to read returns empty string."""
        fr = self._make_failure_report(failed_files=[])
        bp = self._make_blueprint(target_files=[])

        result = _build_retry_file_context(fr, bp, tmp_path)

        assert result == ""


# ---------------------------------------------------------------------------
# _build_retry_sandbox_context
# ---------------------------------------------------------------------------


class TestBuildRetrySandboxContext:
    """Issue #125: Sandbox output for retry prompts."""

    def test_formats_sandbox_result(self):
        sr = SandboxResult(
            exit_code=1,
            tests_passed=3,
            tests_failed=1,
            errors=["AssertionError"],
            output_summary="Expected X got Y",
        )
        result = _build_retry_sandbox_context(sr)

        assert "SANDBOX EXECUTION RESULTS" in result
        assert "Exit code: 1" in result
        assert "Tests passed: 3" in result
        assert "Tests failed: 1" in result
        assert "Expected X got Y" in result

    def test_returns_empty_for_none(self):
        assert _build_retry_sandbox_context(None) == ""

    def test_handles_no_output_summary(self):
        sr = SandboxResult(
            exit_code=0,
            tests_passed=1,
            tests_failed=0,
            errors=[],
        )
        result = _build_retry_sandbox_context(sr)

        assert "Exit code: 0" in result
        # output_summary defaults to "" which is falsy, so no Output line
        assert "Output:" not in result


# ---------------------------------------------------------------------------
# Developer retry prompt integration
# ---------------------------------------------------------------------------


class TestDeveloperRetryPrompt:
    """Issue #125: developer_node uses retry-specific prompts."""

    def _make_blueprint(self):
        return Blueprint(
            task_id="test-retry",
            target_files=["triforce.py"],
            instructions="Print a Triforce",
            constraints=[],
            acceptance_criteria=[],
        )

    def _make_failure_report(self):
        return FailureReport(
            task_id="test-retry",
            status="fail",
            tests_passed=0,
            tests_failed=1,
            errors=["First line missing 6 leading spaces"],
            failed_files=["triforce.py"],
            is_architectural=False,
            recommendation="Add 6 spaces before /\\",
            fix_complexity="trivial",
            exact_fix_hint="Line 1: change '/\\' to '      /\\'",
        )

    @patch("src.orchestrator._get_developer_llm")
    def test_retry_includes_file_context(self, mock_get_llm, tmp_path):
        """On retry, developer_node should inject current file contents."""
        mock_llm = MagicMock()
        mock_response = MagicMock()
        mock_response.content = "# --- FILE: triforce.py ---\nprint('fixed')"
        mock_response.usage_metadata = {"total_tokens": 100}
        mock_llm.invoke.return_value = mock_response
        mock_get_llm.return_value = mock_llm

        # Write a file to disk
        (tmp_path / "triforce.py").write_text("/\\\n")

        state: GraphState = {
            "trace": [],
            "memory_writes": [],
            "tool_calls_log": [],
            "retry_count": 1,
            "tokens_used": 1000,
            "status": WorkflowStatus.REVIEWING,
            "blueprint": self._make_blueprint(),
            "generated_code": "",
            "failure_report": self._make_failure_report(),
            "sandbox_result": SandboxResult(
                exit_code=0,
                tests_passed=1,
                tests_failed=0,
                errors=[],
                output_summary="/\\\n",
            ),
            "workspace_root": str(tmp_path),
        }

        result = developer_node(state, config=None)

        # Verify the LLM was called
        assert mock_llm.invoke.called
        call_args = mock_llm.invoke.call_args[0][0]

        # System message should be retry-specific
        system_msg = call_args[0].content
        assert "MINIMUM change" in system_msg
        assert "Do NOT rewrite" in system_msg

        # User message should contain file context
        user_msg = call_args[1].content
        assert "RETRY CONTEXT" in user_msg
        assert "QA FAILURE REPORT" in user_msg
        assert "CURRENT FILES ON DISK" in user_msg
        assert "EXACT FIX HINT" in user_msg

        # Trace should note file context injection
        assert any("injected file context" in t for t in result["trace"])

    @patch("src.orchestrator._get_developer_llm")
    def test_retry_includes_sandbox_output(self, mock_get_llm, tmp_path):
        """On retry, developer_node should inject sandbox stdout."""
        mock_llm = MagicMock()
        mock_response = MagicMock()
        mock_response.content = "print('fixed')"
        mock_response.usage_metadata = {"total_tokens": 100}
        mock_llm.invoke.return_value = mock_response
        mock_get_llm.return_value = mock_llm

        (tmp_path / "triforce.py").write_text("old code")

        state: GraphState = {
            "trace": [],
            "memory_writes": [],
            "tool_calls_log": [],
            "retry_count": 1,
            "tokens_used": 1000,
            "status": WorkflowStatus.REVIEWING,
            "blueprint": self._make_blueprint(),
            "generated_code": "",
            "failure_report": self._make_failure_report(),
            "sandbox_result": SandboxResult(
                exit_code=0,
                tests_passed=1,
                tests_failed=0,
                errors=[],
                output_summary="/\\\n/ \\\n/____\\",
            ),
            "workspace_root": str(tmp_path),
        }

        result = developer_node(state, config=None)

        # Trace should note sandbox output injection
        assert any("injected sandbox output" in t for t in result["trace"])

    @patch("src.orchestrator._get_developer_llm")
    def test_first_attempt_uses_standard_prompt(self, mock_get_llm):
        """First attempt (no failure_report) should use standard prompt."""
        mock_llm = MagicMock()
        mock_response = MagicMock()
        mock_response.content = "print('hello')"
        mock_response.usage_metadata = {"total_tokens": 100}
        mock_llm.invoke.return_value = mock_response
        mock_get_llm.return_value = mock_llm

        state: GraphState = {
            "trace": [],
            "memory_writes": [],
            "tool_calls_log": [],
            "retry_count": 0,
            "tokens_used": 0,
            "status": WorkflowStatus.BUILDING,
            "blueprint": self._make_blueprint(),
            "generated_code": "",
            "failure_report": None,
        }

        developer_node(state, config=None)

        call_args = mock_llm.invoke.call_args[0][0]
        system_msg = call_args[0].content

        # Standard prompt, not retry prompt
        assert "MINIMUM change" not in system_msg
        assert "Blueprint" in call_args[1].content
        assert "RETRY CONTEXT" not in call_args[1].content

    @patch("src.orchestrator._get_developer_llm")
    def test_architectural_failure_uses_standard_prompt(self, mock_get_llm):
        """Architectural failures go to Architect, not retry Dev prompt."""
        mock_llm = MagicMock()
        mock_response = MagicMock()
        mock_response.content = "print('hello')"
        mock_response.usage_metadata = {"total_tokens": 100}
        mock_llm.invoke.return_value = mock_response
        mock_get_llm.return_value = mock_llm

        arch_failure = FailureReport(
            task_id="test-retry",
            status="escalate",
            tests_passed=0,
            tests_failed=1,
            errors=["wrong file"],
            failed_files=["wrong.py"],
            is_architectural=True,
            recommendation="target different file",
        )

        state: GraphState = {
            "trace": [],
            "memory_writes": [],
            "tool_calls_log": [],
            "retry_count": 1,
            "tokens_used": 1000,
            "status": WorkflowStatus.BUILDING,
            "blueprint": self._make_blueprint(),
            "generated_code": "",
            "failure_report": arch_failure,
        }

        developer_node(state, config=None)

        call_args = mock_llm.invoke.call_args[0][0]
        system_msg = call_args[0].content

        # Should NOT use retry prompt for architectural failures
        assert "MINIMUM change" not in system_msg
        assert "RETRY CONTEXT" not in call_args[1].content


# ---------------------------------------------------------------------------
# QA leniency for empty acceptance criteria
# ---------------------------------------------------------------------------


class TestQALeniency:
    """Issue #125: QA should be lenient when no acceptance criteria exist."""

    @patch("src.orchestrator._get_qa_llm")
    def test_qa_prompt_includes_leniency_for_empty_criteria(self, mock_get_llm):
        """QA system prompt should include leniency text when criteria are empty."""
        mock_llm = MagicMock()
        mock_response = MagicMock()
        mock_response.content = '{"task_id": "t1", "status": "pass", "tests_passed": 1, "tests_failed": 0, "errors": [], "failed_files": [], "is_architectural": false, "recommendation": ""}'
        mock_response.usage_metadata = {"total_tokens": 100}
        mock_llm.invoke.return_value = mock_response
        mock_get_llm.return_value = mock_llm

        state: GraphState = {
            "trace": [],
            "memory_writes": [],
            "tool_calls_log": [],
            "retry_count": 0,
            "tokens_used": 0,
            "status": WorkflowStatus.REVIEWING,
            "blueprint": Blueprint(
                task_id="test-lenient",
                target_files=["script.py"],
                instructions="Print hello world",
                constraints=[],
                acceptance_criteria=[],  # Empty!
            ),
            "generated_code": "print('hello world')",
        }

        qa_node(state, config=None)

        call_args = mock_llm.invoke.call_args[0][0]
        system_msg = call_args[0].content
        assert "NO ACCEPTANCE CRITERIA PROVIDED" in system_msg
        assert "Do NOT invent strict formatting" in system_msg

    @patch("src.orchestrator._get_qa_llm")
    def test_qa_prompt_no_leniency_with_criteria(self, mock_get_llm):
        """Leniency should NOT be added when criteria exist."""
        mock_llm = MagicMock()
        mock_response = MagicMock()
        mock_response.content = '{"task_id": "t1", "status": "pass", "tests_passed": 1, "tests_failed": 0, "errors": [], "failed_files": [], "is_architectural": false, "recommendation": ""}'
        mock_response.usage_metadata = {"total_tokens": 100}
        mock_llm.invoke.return_value = mock_response
        mock_get_llm.return_value = mock_llm

        state: GraphState = {
            "trace": [],
            "memory_writes": [],
            "tool_calls_log": [],
            "retry_count": 0,
            "tokens_used": 0,
            "status": WorkflowStatus.REVIEWING,
            "blueprint": Blueprint(
                task_id="test-strict",
                target_files=["script.py"],
                instructions="Print hello world",
                constraints=[],
                acceptance_criteria=["Output says hello world"],
            ),
            "generated_code": "print('hello world')",
        }

        qa_node(state, config=None)

        call_args = mock_llm.invoke.call_args[0][0]
        system_msg = call_args[0].content
        assert "NO ACCEPTANCE CRITERIA PROVIDED" not in system_msg
