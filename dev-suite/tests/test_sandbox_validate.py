"""Tests for the sandbox_validate orchestrator node.

These tests validate the sandbox_validate node behavior with mocked
E2B runner -- no real sandbox needed. Tests cover:
  - Strategy-based dispatch (TEST_SUITE, SCRIPT_EXEC, SKIP, LINT_ONLY)
  - Template selection from blueprint target_files
  - Graceful skip when E2B_API_KEY is missing
  - SandboxResult stored in graph state
  - Warnings surfaced in trace
  - Empty parsed_files guard

Issue #95: Updated for ValidationStrategy dispatch.
"""

from unittest.mock import patch

from src.agents.architect import Blueprint
from src.orchestrator import (
    GraphState,
    WorkflowStatus,
    sandbox_validate_node,
)
from src.sandbox.e2b_runner import SandboxResult


def _make_state(target_files: list[str], **overrides) -> GraphState:
    """Helper to create a GraphState with a blueprint.

    By default, populates parsed_files from target_files so the
    empty-files guard doesn't skip sandbox dispatch. Tests that
    need empty parsed_files should pass parsed_files=[] explicitly.
    """
    bp = Blueprint(
        task_id="test-task",
        target_files=target_files,
        instructions="Test instructions",
        constraints=["constraint1"],
        acceptance_criteria=["criterion1"],
    )
    # Default: populate parsed_files with dummy content for each target file
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


class TestSandboxValidateStrategy:
    """Tests for strategy-based dispatch in sandbox_validate_node."""

    async def test_script_exec_for_single_python_file(self):
        """Single .py file without tests should use SCRIPT_EXEC."""
        state = _make_state(["hello_world.py"])

        with patch("src.orchestrator._run_sandbox_script") as mock_script:
            mock_script.return_value = SandboxResult(
                exit_code=0,
                tests_passed=1,
                tests_failed=0,
                output_summary="Hello, World!",
            )
            result = await sandbox_validate_node(state)

        assert result["sandbox_result"] is not None
        assert result["sandbox_result"].exit_code == 0
        assert result["sandbox_result"].tests_passed == 1
        mock_script.assert_called_once()
        call_kwargs = mock_script.call_args[1]
        assert call_kwargs["script_file"] == "hello_world.py"

    async def test_test_suite_for_python_with_tests(self):
        """Python files with test files should use TEST_SUITE."""
        state = _make_state(["src/main.py", "tests/test_main.py"])

        with patch("src.orchestrator._run_sandbox_tests") as mock_tests:
            mock_tests.return_value = SandboxResult(
                exit_code=0,
                tests_passed=5,
                tests_failed=0,
                output_summary="5 passed in 1.2s",
            )
            result = await sandbox_validate_node(state)

        assert result["sandbox_result"] is not None
        assert result["sandbox_result"].tests_passed == 5
        mock_tests.assert_called_once()
        call_kwargs = mock_tests.call_args[1]
        assert call_kwargs["template"] is None

    async def test_test_suite_for_frontend_files(self):
        """Frontend files should use TEST_SUITE with fullstack template."""
        state = _make_state(["dashboard/src/lib/Widget.svelte"])

        with patch("src.orchestrator._run_sandbox_tests") as mock_tests:
            mock_tests.return_value = SandboxResult(
                exit_code=0,
                tests_passed=1,
                tests_failed=0,
                output_summary="svelte-check found 0 errors",
            )
            result = await sandbox_validate_node(state)

        assert result["sandbox_result"] is not None
        call_kwargs = mock_tests.call_args[1]
        assert call_kwargs["template"] == "fullstack"

    async def test_skip_for_non_code_files(self):
        """Non-code files should return None (no sandbox run)."""
        state = _make_state(["schema.sql", "config.yaml"])

        result = await sandbox_validate_node(state)

        assert result["sandbox_result"] is None
        assert any("skip" in t.lower() for t in result["trace"])

    async def test_empty_parsed_files_skips_sandbox(self):
        """CR fix: empty parsed_files should skip sandbox dispatch."""
        state = _make_state(
            ["src/main.py", "tests/test_main.py"],
            parsed_files=[],
        )

        result = await sandbox_validate_node(state)

        assert result["sandbox_result"] is None
        assert any("no parsed files" in t.lower() for t in result["trace"])

    async def test_lint_only_for_pyi_stubs(self):
        """CR fix: .pyi-only files should dispatch via LINT_ONLY -> _run_sandbox_tests."""
        state = _make_state(["src/types.pyi"])

        with patch("src.orchestrator._run_sandbox_tests") as mock_tests:
            mock_tests.return_value = SandboxResult(
                exit_code=0,
                tests_passed=None,
                tests_failed=None,
                output_summary="ruff check passed",
            )
            result = await sandbox_validate_node(state)

        assert result["sandbox_result"] is not None
        assert result["sandbox_result"].exit_code == 0
        mock_tests.assert_called_once()
        trace_text = " ".join(result.get("trace", []))
        assert "lint_only" in trace_text.lower() or "command(s) sequentially" in trace_text.lower()


class TestSandboxValidateNode:
    """Core sandbox_validate_node tests."""

    async def test_python_task_uses_default_template(self):
        """Python-only tasks should use the default (None) template."""
        state = _make_state(["src/main.py", "tests/test_main.py"])

        with patch("src.orchestrator._run_sandbox_tests") as mock_run:
            mock_run.return_value = SandboxResult(
                exit_code=0,
                tests_passed=5,
                tests_failed=0,
                output_summary="5 passed in 1.2s",
            )
            result = await sandbox_validate_node(state)

        assert result["sandbox_result"] is not None
        assert result["sandbox_result"].exit_code == 0
        assert result["sandbox_result"].tests_passed == 5
        call_kwargs = mock_run.call_args[1]
        assert call_kwargs["template"] is None

    async def test_svelte_task_uses_fullstack_template(self):
        """Svelte tasks should use the fullstack template."""
        state = _make_state(["dashboard/src/lib/Widget.svelte"])

        with patch("src.orchestrator._run_sandbox_tests") as mock_run:
            mock_run.return_value = SandboxResult(
                exit_code=0,
                tests_passed=1,
                tests_failed=0,
                output_summary="svelte-check found 0 errors",
            )
            result = await sandbox_validate_node(state)

        assert result["sandbox_result"] is not None
        call_kwargs = mock_run.call_args[1]
        assert call_kwargs["template"] == "fullstack"

    async def test_mixed_task_uses_fullstack_template(self):
        """Mixed Python+frontend tasks should use fullstack."""
        state = _make_state(["src/api.py", "dashboard/src/App.svelte"])

        with patch("src.orchestrator._run_sandbox_tests") as mock_run:
            mock_run.return_value = SandboxResult(
                exit_code=0,
                tests_passed=6,
                tests_failed=0,
                output_summary="All checks passed",
            )
            result = await sandbox_validate_node(state)

        call_kwargs = mock_run.call_args[1]
        assert call_kwargs["template"] == "fullstack"

    async def test_graceful_skip_no_api_key(self):
        """Should return None when E2B_API_KEY is missing."""
        state = _make_state(["src/main.py", "tests/test_main.py"])

        with patch("src.orchestrator._run_sandbox_tests") as mock_run:
            mock_run.return_value = None
            result = await sandbox_validate_node(state)

        assert result["sandbox_result"] is None

    async def test_no_blueprint_skips(self):
        """Should skip validation when there's no blueprint."""
        state = _make_state([])
        state["blueprint"] = None

        result = await sandbox_validate_node(state)

        assert result["sandbox_result"] is None
        assert any("no blueprint" in t.lower() for t in result["trace"])

    async def test_sandbox_error_doesnt_crash(self):
        """Sandbox errors should be captured, not raise exceptions."""
        state = _make_state(["src/main.py", "tests/test_main.py"])

        with patch("src.orchestrator._run_sandbox_tests") as mock_run:
            mock_run.side_effect = Exception("E2B connection failed")
            result = await sandbox_validate_node(state)

        assert result["sandbox_result"] is None
        assert any("error" in t.lower() or "failed" in t.lower() for t in result["trace"])

    async def test_failed_validation_still_continues(self):
        """Sandbox validation failure should not block QA -- it adds context."""
        state = _make_state(["src/main.py", "tests/test_main.py"])

        with patch("src.orchestrator._run_sandbox_tests") as mock_run:
            mock_run.return_value = SandboxResult(
                exit_code=1,
                tests_passed=3,
                tests_failed=2,
                errors=["TypeError in auth.js line 42"],
                output_summary="3 passed, 2 failed",
            )
            result = await sandbox_validate_node(state)

        assert result["sandbox_result"] is not None
        assert result["sandbox_result"].tests_failed == 2

    async def test_warnings_surfaced_in_trace(self):
        """Sandbox warnings should appear in the trace log."""
        state = _make_state(["src/main.py", "tests/test_main.py"])

        with patch("src.orchestrator._run_sandbox_tests") as mock_run:
            mock_run.return_value = SandboxResult(
                exit_code=0,
                tests_passed=1,
                tests_failed=0,
                output_summary="1 passed",
                warnings=["[WARN] ruff not available -- lint skipped"],
            )
            result = await sandbox_validate_node(state)

        trace_text = " ".join(result.get("trace", []))
        assert "ruff" in trace_text.lower()

    async def test_trace_includes_strategy(self):
        """Trace should include which strategy was selected."""
        state = _make_state(["hello_world.py"])

        with patch("src.orchestrator._run_sandbox_script") as mock_script:
            mock_script.return_value = SandboxResult(
                exit_code=0,
                tests_passed=1,
                tests_failed=0,
                output_summary="Hello, World!",
            )
            result = await sandbox_validate_node(state)

        trace_text = " ".join(result.get("trace", []))
        assert "script_exec" in trace_text.lower()


class TestSandboxValidateGraphIntegration:
    """Tests that the graph is wired correctly with the new node."""

    def test_graph_has_sandbox_validate_node(self):
        """The compiled graph should include sandbox_validate."""
        from src.orchestrator import build_graph

        graph = build_graph()
        compiled = graph.compile()
        node_names = set(compiled.get_graph().nodes.keys())
        assert "sandbox_validate" in node_names

    def test_graph_has_apply_code_node(self):
        """The compiled graph should include apply_code."""
        from src.orchestrator import build_graph

        graph = build_graph()
        compiled = graph.compile()
        node_names = set(compiled.get_graph().nodes.keys())
        assert "apply_code" in node_names

    def test_graph_edge_developer_to_apply_code(self):
        """developer should connect to apply_code (not directly to sandbox)."""
        from src.orchestrator import build_graph

        graph = build_graph()
        compiled = graph.compile()
        graph_data = compiled.get_graph()
        developer_targets = {
            e.target for e in graph_data.edges
            if e.source == "developer"
        }
        assert "apply_code" in developer_targets

    def test_graph_edge_apply_code_to_sandbox(self):
        """apply_code should connect to sandbox_validate."""
        from src.orchestrator import build_graph

        graph = build_graph()
        compiled = graph.compile()
        graph_data = compiled.get_graph()
        apply_code_targets = {
            e.target for e in graph_data.edges
            if e.source == "apply_code"
        }
        assert "sandbox_validate" in apply_code_targets

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
