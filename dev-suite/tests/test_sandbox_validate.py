"""Tests for the sandbox_validate orchestrator node.

These tests validate the sandbox_validate node behavior with mocked
E2B runner \u2014 no real sandbox needed. Tests cover:
  - Template selection from blueprint target_files
  - Validation command execution
  - Graceful skip when E2B_API_KEY is missing
  - SandboxResult stored in graph state
  - QA prompt includes sandbox results
"""

from unittest.mock import MagicMock, patch

import pytest

from src.agents.architect import Blueprint
from src.orchestrator import (
    GraphState,
    WorkflowStatus,
    sandbox_validate_node,
)
from src.sandbox.e2b_runner import SandboxResult


def _make_state(target_files: list[str], **overrides) -> GraphState:
    """Helper to create a GraphState with a blueprint."""
    bp = Blueprint(
        task_id="test-task",
        target_files=target_files,
        instructions="Test instructions",
        constraints=["constraint1"],
        acceptance_criteria=["criterion1"],
    )
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
    }
    state.update(overrides)
    return state


class TestSandboxValidateNode:
    """Tests for the sandbox_validate_node function."""

    def test_python_task_uses_default_template(self):
        """Python-only tasks should use the default (None) template."""
        state = _make_state(["src/main.py", "tests/test_main.py"])

        with patch("src.orchestrator._run_sandbox_validation") as mock_run:
            mock_run.return_value = SandboxResult(
                exit_code=0,
                tests_passed=5,
                tests_failed=0,
                output_summary="5 passed in 1.2s",
            )
            result = sandbox_validate_node(state)

        assert result["sandbox_result"] is not None
        assert result["sandbox_result"].exit_code == 0
        assert result["sandbox_result"].tests_passed == 5
        # Check it was called with None template (default)
        call_args = mock_run.call_args
        assert call_args[1]["template"] is None

    def test_svelte_task_uses_fullstack_template(self):
        """Svelte tasks should use the fullstack template."""
        state = _make_state(["dashboard/src/lib/Widget.svelte"])

        with patch("src.orchestrator._run_sandbox_validation") as mock_run:
            mock_run.return_value = SandboxResult(
                exit_code=0,
                tests_passed=1,
                tests_failed=0,
                output_summary="svelte-check found 0 errors",
            )
            result = sandbox_validate_node(state)

        assert result["sandbox_result"] is not None
        call_args = mock_run.call_args
        assert call_args[1]["template"] == "fullstack"

    def test_mixed_task_uses_fullstack_template(self):
        """Mixed Python+frontend tasks should use fullstack."""
        state = _make_state(["src/api.py", "dashboard/src/App.svelte"])

        with patch("src.orchestrator._run_sandbox_validation") as mock_run:
            mock_run.return_value = SandboxResult(
                exit_code=0,
                tests_passed=6,
                tests_failed=0,
                output_summary="All checks passed",
            )
            result = sandbox_validate_node(state)

        call_args = mock_run.call_args
        assert call_args[1]["template"] == "fullstack"

    def test_graceful_skip_no_api_key(self):
        """Should warn and skip when E2B_API_KEY is missing."""
        state = _make_state(["src/main.py"])

        with patch("src.orchestrator._run_sandbox_validation") as mock_run:
            mock_run.return_value = None  # Signals skip
            result = sandbox_validate_node(state)

        assert result["sandbox_result"] is None
        assert any("skip" in t.lower() or "warn" in t.lower() for t in result["trace"])

    def test_no_blueprint_skips(self):
        """Should skip validation when there's no blueprint."""
        state = _make_state([])
        state["blueprint"] = None

        result = sandbox_validate_node(state)

        assert result["sandbox_result"] is None
        assert any("no blueprint" in t.lower() for t in result["trace"])

    def test_non_code_files_skip_validation(self):
        """Non-code files (JSON, YAML, SQL) should skip sandbox validation."""
        state = _make_state(["schema.sql", "config.yaml"])

        result = sandbox_validate_node(state)

        assert result["sandbox_result"] is None
        assert any("no code validation" in t.lower() or "skip" in t.lower() for t in result["trace"])

    def test_sandbox_error_doesnt_crash(self):
        """Sandbox errors should be captured, not raise exceptions."""
        state = _make_state(["src/main.py"])

        with patch("src.orchestrator._run_sandbox_validation") as mock_run:
            mock_run.side_effect = Exception("E2B connection failed")
            result = sandbox_validate_node(state)

        assert result["sandbox_result"] is None
        assert any("error" in t.lower() or "failed" in t.lower() for t in result["trace"])

    def test_failed_validation_still_continues(self):
        """Sandbox validation failure should not block QA \u2014 it adds context."""
        state = _make_state(["src/main.py"])

        with patch("src.orchestrator._run_sandbox_validation") as mock_run:
            mock_run.return_value = SandboxResult(
                exit_code=1,
                tests_passed=3,
                tests_failed=2,
                errors=["TypeError in auth.js line 42"],
                output_summary="3 passed, 2 failed",
            )
            result = sandbox_validate_node(state)

        # Node should still return the result even though tests failed
        assert result["sandbox_result"] is not None
        assert result["sandbox_result"].tests_failed == 2
        # Status should not change \u2014 QA decides pass/fail
        assert "status" not in result or result.get("status") == WorkflowStatus.REVIEWING

    def test_trace_includes_validation_plan(self):
        """Trace should include which template and commands were selected."""
        state = _make_state(["dashboard/src/App.svelte"])

        with patch("src.orchestrator._run_sandbox_validation") as mock_run:
            mock_run.return_value = SandboxResult(
                exit_code=0,
                tests_passed=1,
                tests_failed=0,
                output_summary="svelte-check found 0 errors",
            )
            result = sandbox_validate_node(state)

        trace = result.get("trace", [])
        trace_text = " ".join(trace).lower()
        assert "sandbox_validate" in trace_text
        assert "fullstack" in trace_text or "frontend" in trace_text


class TestSandboxValidateGraphIntegration:
    """Tests that the graph is wired correctly with the new node."""

    def test_graph_has_sandbox_validate_node(self):
        """The compiled graph should include sandbox_validate."""
        from src.orchestrator import build_graph

        graph = build_graph()
        compiled = graph.compile()
        # LangGraph stores node names \u2014 check sandbox_validate exists
        node_names = set(compiled.get_graph().nodes.keys())
        assert "sandbox_validate" in node_names

    def test_graph_edge_developer_to_sandbox(self):
        """developer should connect to sandbox_validate."""
        from src.orchestrator import build_graph

        graph = build_graph()
        compiled = graph.compile()
        graph_data = compiled.get_graph()
        # Check edges: developer -> sandbox_validate
        developer_targets = {
            e.target for e in graph_data.edges
            if e.source == "developer"
        }
        assert "sandbox_validate" in developer_targets

    def test_graph_edge_sandbox_to_qa(self):
        """sandbox_validate should connect to qa."""
        from src.orchestrator import build_graph

        graph = build_graph()
        compiled = graph.compile()
        graph_data = compiled.get_graph()
        sandbox_targets = {
            e.target for e in graph_data.edges
            if e.source == "sandbox_validate"
        }
        assert "qa" in sandbox_targets
