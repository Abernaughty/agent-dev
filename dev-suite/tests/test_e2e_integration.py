"""E2E integration test -- full pipeline with real LLMs and MCP tools.

Issue #86: Proves the complete orchestrator pipeline works end-to-end:
  Architect (Gemini) -> Developer (Claude + tools) -> apply_code ->
  sandbox_validate (E2B) -> QA (Claude + tools) -> flush_memory

Prerequisites:
    - GOOGLE_API_KEY in dev-suite/.env (Gemini -- Architect)
    - ANTHROPIC_API_KEY in dev-suite/.env (Claude -- Dev + QA)
    - Node.js + npx on PATH (Filesystem MCP server)
    - E2B_API_KEY in dev-suite/.env (optional -- sandbox skipped without it)

Usage:
    cd dev-suite
    uv run --group dev --group api pytest tests/test_e2e_integration.py -v -s -m integration
"""

from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path

import pytest

import src.orchestrator as orchestrator
from src.orchestrator import AgentState, WorkflowStatus, run_task


# ============================================================
# Helpers
# ============================================================


def _has_node_in_trace(trace: list[str], node_name: str) -> bool:
    """Check if a graph node appears in the execution trace."""
    return any(node_name in entry for entry in trace)


def _print_result(result: AgentState, elapsed: float):
    """Diagnostic dump on test completion or failure."""
    print("\n" + "=" * 70)
    print("  E2E INTEGRATION TEST RESULT")
    print("=" * 70)
    print(f"  Status:       {result.status.value}")
    print(f"  Tokens used:  {result.tokens_used:,}")
    print(f"  Retries:      {result.retry_count}")
    print(f"  Elapsed:      {elapsed:.1f}s")
    print()

    if result.blueprint:
        print("  BLUEPRINT:")
        print(f"    Task ID:      {result.blueprint.task_id}")
        print(f"    Target files: {', '.join(result.blueprint.target_files)}")
        print(f"    Criteria:     {len(result.blueprint.acceptance_criteria)}")
        print()

    if result.generated_code:
        lines = result.generated_code.strip().split("\n")
        print(f"  GENERATED CODE ({len(lines)} lines):")
        for line in lines[:15]:
            print(f"    {line}")
        if len(lines) > 15:
            print(f"    ... ({len(lines) - 15} more lines)")
        print()

    if result.parsed_files:
        print(f"  PARSED FILES ({len(result.parsed_files)}):")
        for pf in result.parsed_files:
            print(f"    {pf['path']} ({len(pf['content'])} chars)")
        print()

    if result.tool_calls_log:
        print(f"  TOOL CALLS ({len(result.tool_calls_log)}):")
        for tc in result.tool_calls_log[:10]:
            icon = "ok" if tc.get("success") else "FAIL"
            print(f"    [{icon}] {tc.get('agent', '?')}: {tc.get('tool', '?')}")
        if len(result.tool_calls_log) > 10:
            print(f"    ... ({len(result.tool_calls_log) - 10} more)")
        print()

    if result.sandbox_result:
        sr = result.sandbox_result
        print("  SANDBOX RESULT:")
        print(f"    Exit code:    {sr.exit_code}")
        print(f"    Tests passed: {sr.tests_passed}")
        print(f"    Tests failed: {sr.tests_failed}")
        if sr.errors:
            print(f"    Errors:       {', '.join(sr.errors[:3])}")
        print()

    if result.failure_report:
        fr = result.failure_report
        print("  QA REPORT:")
        print(f"    Verdict:      {fr.status}")
        print(f"    Passed:       {fr.tests_passed}")
        print(f"    Failed:       {fr.tests_failed}")
        if fr.errors:
            print(f"    Errors:       {', '.join(fr.errors[:3])}")
        print()

    if result.memory_writes:
        print(f"  MEMORY WRITES ({len(result.memory_writes)}):")
        for mw in result.memory_writes[:5]:
            print(f"    [{mw.get('tier', '?')}] {mw.get('content', '')[:80]}")
        print()

    if result.trace:
        print(f"  TRACE ({len(result.trace)} entries):")
        for t in result.trace:
            print(f"    -> {t}")
        print()

    if result.error_message:
        print(f"  ERROR: {result.error_message}")
        print()

    print("=" * 70)


# ============================================================
# Fixtures
# ============================================================


@pytest.fixture
def workspace(tmp_path):
    """Seed a minimal Python project for the agents to work on.

    Creates:
      - utils.py: stub file (agent must add the greet function)
      - test_utils.py: test expecting greet('World') == 'Hello, World!'
    """
    utils = tmp_path / "utils.py"
    utils.write_text('"""Utility functions."""\n', encoding="utf-8")

    test_utils = tmp_path / "test_utils.py"
    test_utils.write_text(
        '"""Tests for utils module."""\n'
        "from utils import greet\n"
        "\n\n"
        "def test_greet_basic():\n"
        '    assert greet("World") == "Hello, World!"\n'
        "\n\n"
        "def test_greet_name():\n"
        '    assert greet("Alice") == "Hello, Alice!"\n',
        encoding="utf-8",
    )

    return tmp_path


@pytest.fixture
def mcp_config(workspace):
    """Seed mcp-config.json pointing Filesystem MCP at the workspace.

    Enables tool binding for Dev (read/write) and QA (read-only).
    Requires Node.js + npx on PATH.
    """
    has_npx = shutil.which("npx") is not None
    if not has_npx:
        # No npx -- write a config with no servers so tools are empty
        # but the file exists (init_tools_config won't bail on missing file)
        config = {"servers": {}}
    else:
        config = {
            "servers": {
                "filesystem": {
                    "package": "@modelcontextprotocol/server-filesystem",
                    "version": "2026.1.14",
                    "command": "npx",
                    "args": [
                        "-y",
                        "@modelcontextprotocol/server-filesystem@2026.1.14",
                        str(workspace),
                    ],
                    "env": {},
                }
            }
        }

    config_path = workspace / "mcp-config.json"
    config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")
    return config_path


# ============================================================
# Test class
# ============================================================


@pytest.mark.integration
class TestE2EIntegration:
    """Full pipeline integration test with real LLMs.

    Gating: @pytest.mark.integration -- excluded from CI via -m "not integration".
    Keys loaded from dev-suite/.env by conftest.py's load_dotenv().
    """

    @pytest.fixture(autouse=True)
    def _check_keys(self):
        """Skip if required API keys are missing."""
        if not os.getenv("ANTHROPIC_API_KEY"):
            pytest.skip("ANTHROPIC_API_KEY not set -- set in dev-suite/.env")
        if not os.getenv("GOOGLE_API_KEY"):
            pytest.skip("GOOGLE_API_KEY not set -- set in dev-suite/.env")

    @pytest.fixture(autouse=True)
    def _configure_env(self, workspace, mcp_config, monkeypatch):
        """Point the orchestrator at the temp workspace with generous budgets.

        Note: TOKEN_BUDGET and MAX_RETRIES are module-level globals in
        src.orchestrator, evaluated at import time via _safe_int(). Setting
        os.environ alone won't affect them -- we must patch the module
        globals directly.
        """
        monkeypatch.setenv("WORKSPACE_ROOT", str(workspace))
        monkeypatch.setenv("TOKEN_BUDGET", "100000")
        monkeypatch.setenv("MAX_RETRIES", "3")
        # Patch module globals directly (evaluated at import time)
        monkeypatch.setattr(orchestrator, "TOKEN_BUDGET", 100000)
        monkeypatch.setattr(orchestrator, "MAX_RETRIES", 3)
        # Store workspace on self for assertions
        self._workspace = workspace
        self._has_e2b = bool(os.getenv("E2B_API_KEY"))
        self._has_npx = shutil.which("npx") is not None

    def test_workspace_seeded_correctly(self, workspace):
        """Sanity check: fixture creates the expected seed files."""
        assert (workspace / "utils.py").exists()
        assert (workspace / "test_utils.py").exists()
        assert (workspace / "mcp-config.json").exists()

        test_content = (workspace / "test_utils.py").read_text()
        assert "greet" in test_content
        assert "Hello, World!" in test_content

    def test_full_pipeline(self, workspace):
        """Run the complete orchestrator pipeline with real LLMs.

        Asserts structural completion -- every graph node executed and
        produced output. Does NOT assert on code quality or LLM content,
        since model output is non-deterministic.
        """
        task = (
            "Add a greet(name) function to utils.py that takes a name "
            "parameter (str) and returns the string 'Hello, {name}!'. "
            "For example, greet('World') should return 'Hello, World!'. "
            "Include a type hint and a docstring."
        )

        print(f"\n  Task: {task}")
        print(f"  Workspace: {workspace}")
        print(f"  E2B available: {self._has_e2b}")
        print(f"  npx available: {self._has_npx}")
        print("  Running pipeline...\n")

        start = time.time()
        result = run_task(
            task,
            enable_tracing=bool(os.getenv("LANGFUSE_PUBLIC_KEY")),
        )
        elapsed = time.time() - start

        # Always print diagnostics
        _print_result(result, elapsed)

        # -- Hard assertions: pipeline completed without crash --

        assert isinstance(result, AgentState), "run_task must return AgentState"
        assert result.tokens_used > 0, "Real API calls should consume tokens"

        # -- Architect produced a Blueprint --

        assert result.blueprint is not None, "Architect should produce a Blueprint"
        assert len(result.blueprint.target_files) > 0, "Blueprint should have target files"
        assert len(result.blueprint.instructions) > 0, "Blueprint should have instructions"

        # -- Developer produced code (text or summary) --

        assert len(result.generated_code) > 0, "Developer should generate code or summary"

        # -- Trace covers core graph nodes --
        # Note: flush_memory only runs on PASSED or retry-exhausted paths,
        # not on FAILED (QA parse error). We check the nodes that always run.

        trace = result.trace
        assert _has_node_in_trace(trace, "architect"), "Trace should show architect ran"
        assert _has_node_in_trace(trace, "developer"), "Trace should show developer ran"
        assert _has_node_in_trace(trace, "apply_code"), "Trace should show apply_code ran"
        assert _has_node_in_trace(trace, "sandbox_validate"), "Trace should show sandbox_validate ran"
        assert _has_node_in_trace(trace, "qa"), "Trace should show qa ran"

        # flush_memory runs on PASSED or retry-exhausted, not on FAILED
        if result.status != WorkflowStatus.FAILED:
            assert _has_node_in_trace(trace, "flush_memory"), (
                "Trace should show flush_memory ran on non-FAILED status"
            )

        # -- Memory layer engaged --

        assert len(result.memory_writes) > 0, "Pipeline should produce memory writes"

        # -- Tool binding verification --
        # When tools load successfully, Dev and QA use them. When tools
        # are used, Dev writes files via filesystem_write (not # --- FILE:
        # markers), so parsed_files from apply_code may be empty.

        tools_were_used = len(result.tool_calls_log) > 0

        if tools_were_used:
            # Verify Dev used filesystem tools
            dev_calls = [
                tc for tc in result.tool_calls_log
                if tc.get("agent") == "developer"
            ]
            assert len(dev_calls) > 0, "Developer should have made tool calls"
            dev_tool_names = {tc.get("tool") for tc in dev_calls}
            assert dev_tool_names & {
                "filesystem_read", "filesystem_write", "filesystem_list"
            }, (
                f"Developer should use filesystem tools, got: {dev_tool_names}"
            )

            # When Dev uses tools, files are written directly via
            # filesystem_write. The generated_code may be a summary
            # without # --- FILE: markers, so apply_code's parser
            # may find nothing. Check that the target file exists on
            # disk (written by tool or by apply_code).
            for target in result.blueprint.target_files:
                target_path = workspace / target
                assert target_path.exists(), (
                    f"Target file should exist on disk (via tools or apply_code): {target}"
                )
        else:
            # Single-shot mode: apply_code should have parsed files
            if self._has_npx:
                print(
                    "  NOTE: npx available but tools not used --"
                    " check init_tools_config logs"
                )

            if result.parsed_files:
                workspace_root = workspace.resolve()
                for pf in result.parsed_files:
                    file_path = (workspace / pf["path"]).resolve()
                    assert (
                        file_path == workspace_root
                        or workspace_root in file_path.parents
                    ), f"Parsed file escaped workspace: {pf['path']}"
                    assert file_path.exists(), (
                        f"Parsed file should exist on disk: {pf['path']}"
                    )
                    disk_content = file_path.read_text(encoding="utf-8").strip()
                    parsed_content = pf["content"].strip()
                    assert disk_content == parsed_content, (
                        f"Disk content should match parsed content for {pf['path']}"
                    )

        # -- Sandbox validation (conditional on E2B key) --

        if self._has_e2b:
            assert result.sandbox_result is not None, (
                "With E2B_API_KEY set, sandbox_validate should produce a result"
            )
        else:
            assert _has_node_in_trace(trace, "sandbox_validate"), (
                "sandbox_validate node should still appear in trace even when skipped"
            )

        # -- Terminal state --

        assert result.status in (
            WorkflowStatus.PASSED,
            WorkflowStatus.FAILED,
            WorkflowStatus.ESCALATED,
        ), f"Pipeline should reach a terminal state, got: {result.status}"

        # -- Log outcome --

        if result.status == WorkflowStatus.PASSED:
            print(
                f"\n  >>> PIPELINE PASSED"
                f" ({elapsed:.1f}s, {result.tokens_used:,} tokens)"
            )
        else:
            print(f"\n  >>> PIPELINE ended: {result.status.value} ({elapsed:.1f}s)")
            print(
                f"  >>> Retries: {result.retry_count},"
                f" Error: {result.error_message or 'none'}"
            )
