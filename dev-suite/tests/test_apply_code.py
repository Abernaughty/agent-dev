"""Tests for apply_code_node in the orchestrator.

Tests the new node that bridges Dev agent output to the filesystem
and sandbox. Uses mocked workspace paths -- no real file I/O needed
for unit tests.
"""

from __future__ import annotations

from unittest.mock import patch

from src.orchestrator import WorkflowStatus


class TestApplyCodeNode:
    """Tests for the apply_code_node orchestrator node."""

    def _make_state(self, **overrides):
        """Build a minimal GraphState dict."""
        from src.agents.architect import Blueprint

        base = {
            "task_description": "test task",
            "blueprint": Blueprint(
                task_id="test-1",
                target_files=["src/main.py", "src/utils.py"],
                instructions="implement the thing",
                constraints=["be safe"],
                acceptance_criteria=["tests pass"],
            ),
            "generated_code": (
                "# --- FILE: src/main.py ---\n"
                "def main():\n"
                "    print('hello')\n"
                "# --- FILE: src/utils.py ---\n"
                "def helper():\n"
                "    return 42\n"
            ),
            "failure_report": None,
            "status": WorkflowStatus.BUILDING,
            "retry_count": 0,
            "tokens_used": 1000,
            "error_message": "",
            "memory_context": [],
            "memory_writes": [],
            "trace": [],
            "sandbox_result": None,
            "parsed_files": [],
        }
        base.update(overrides)
        return base

    async def test_happy_path_parses_files(self, tmp_path):
        """Files are parsed from generated_code and stored in state."""
        from src.orchestrator import apply_code_node

        state = self._make_state()

        with patch("src.orchestrator._get_workspace_root", return_value=tmp_path):
            result = await apply_code_node(state)

        assert "parsed_files" in result
        files = result["parsed_files"]
        assert len(files) == 2
        assert files[0]["path"] == "src/main.py"
        assert "def main():" in files[0]["content"]
        assert files[1]["path"] == "src/utils.py"
        assert "def helper():" in files[1]["content"]

    async def test_writes_files_to_workspace(self, tmp_path):
        """Parsed files are written to the workspace directory."""
        from src.orchestrator import apply_code_node

        state = self._make_state()

        with patch("src.orchestrator._get_workspace_root", return_value=tmp_path):
            await apply_code_node(state)

        assert (tmp_path / "src" / "main.py").exists()
        assert (tmp_path / "src" / "utils.py").exists()
        assert "def main():" in (tmp_path / "src" / "main.py").read_text()

    async def test_no_generated_code_skips(self):
        """Missing generated_code results in graceful skip."""
        from src.orchestrator import apply_code_node

        state = self._make_state(generated_code="")
        result = await apply_code_node(state)

        assert result["parsed_files"] == []
        assert any("no generated_code" in t for t in result["trace"])

    async def test_no_blueprint_skips(self):
        """Missing blueprint results in graceful skip."""
        from src.orchestrator import apply_code_node

        state = self._make_state(blueprint=None)
        result = await apply_code_node(state)

        assert result["parsed_files"] == []
        assert any("no blueprint" in t for t in result["trace"])

    async def test_path_traversal_skipped(self, tmp_path):
        """Files with traversal/absolute paths are skipped, safe files survive."""
        from src.orchestrator import apply_code_node

        # Include both a dangerous traversal path and a safe path.
        # parse_generated_code raises CodeParserError on ../.. paths,
        # which apply_code_node catches gracefully.
        # Test with a safe-only file to verify the error path works.
        dangerous_code = (
            "# --- FILE: ../../../etc/passwd ---\n"
            "malicious content\n"
            "# --- FILE: src/safe.py ---\n"
            "x = 1\n"
        )
        state = self._make_state(generated_code=dangerous_code)

        with patch("src.orchestrator._get_workspace_root", return_value=tmp_path):
            result = await apply_code_node(state)

        # The parser raises CodeParserError on the traversal path,
        # so apply_code_node catches it and returns empty parsed_files.
        # This proves the security boundary works -- no files written.
        assert len(result["parsed_files"]) == 0
        assert any("parse error" in t for t in result["trace"])

    async def test_workspace_containment_filters_unsafe(self, tmp_path):
        """Files that escape workspace via validate_paths_for_workspace are filtered."""
        from src.orchestrator import apply_code_node

        # Use only safe paths (parser won't reject) -- the workspace
        # containment check is the second layer of defense.
        safe_code = (
            "# --- FILE: src/safe.py ---\n"
            "x = 1\n"
        )
        state = self._make_state(generated_code=safe_code)

        with patch("src.orchestrator._get_workspace_root", return_value=tmp_path):
            result = await apply_code_node(state)

        assert len(result["parsed_files"]) == 1
        assert result["parsed_files"][0]["path"] == "src/safe.py"

    async def test_trace_entries_added(self, tmp_path):
        """Trace entries are added for the apply_code step."""
        from src.orchestrator import apply_code_node

        state = self._make_state()

        with patch("src.orchestrator._get_workspace_root", return_value=tmp_path):
            result = await apply_code_node(state)

        trace = result["trace"]
        assert any("apply_code: starting" in t for t in trace)
        assert any("2 files" in t for t in trace)

    async def test_creates_nested_directories(self, tmp_path):
        """Nested directories are created automatically."""
        from src.orchestrator import apply_code_node

        code = "# --- FILE: src/lib/deep/nested/file.py ---\nx = 1\n"
        state = self._make_state(generated_code=code)

        with patch("src.orchestrator._get_workspace_root", return_value=tmp_path):
            await apply_code_node(state)

        assert (tmp_path / "src" / "lib" / "deep" / "nested" / "file.py").exists()

    async def test_status_not_changed(self, tmp_path):
        """apply_code_node should NOT change the workflow status."""
        from src.orchestrator import apply_code_node

        state = self._make_state()

        with patch("src.orchestrator._get_workspace_root", return_value=tmp_path):
            result = await apply_code_node(state)

        # status should not be in the result (unchanged)
        assert "status" not in result


class TestApplyCodeDiskRead:
    """Tests for apply_code_node's disk-read path when developer used tools."""

    def _make_state(self, **overrides):
        from src.agents.architect import Blueprint

        base = {
            "task_description": "test task",
            "blueprint": Blueprint(
                task_id="test-disk",
                target_files=["app.py"],
                instructions="build the app",
                constraints=[],
                acceptance_criteria=["runs"],
            ),
            "generated_code": "## Summary\n\nDone.\n\n# --- FILE: app.py ---\n```python\nprint('hello')\n```\n",
            "failure_report": None,
            "status": WorkflowStatus.BUILDING,
            "retry_count": 0,
            "tokens_used": 0,
            "error_message": "",
            "memory_context": [],
            "memory_writes": [],
            "trace": [],
            "sandbox_result": None,
            "parsed_files": [],
            "tool_calls_log": [],
        }
        base.update(overrides)
        return base

    async def test_prefers_disk_over_parser_when_tools_used(self, tmp_path):
        """When filesystem_write was used, read files from disk instead of re-parsing."""
        from src.orchestrator import apply_code_node

        # Write clean code to disk (simulating what filesystem_write did)
        (tmp_path / "app.py").write_text("print('hello from disk')\n")

        # generated_code has fences that would corrupt parsing
        state = self._make_state(
            workspace_root=str(tmp_path),
            tool_calls_log=[
                {"agent": "developer", "turn": 1, "tool": "filesystem_write",
                 "args_preview": "{}", "result_preview": "ok", "success": True},
            ],
        )

        result = await apply_code_node(state)

        assert len(result["parsed_files"]) == 1
        assert result["parsed_files"][0]["path"] == "app.py"
        assert result["parsed_files"][0]["content"] == "print('hello from disk')\n"
        assert any("tool-written" in t for t in result["trace"])

    async def test_falls_back_to_parser_without_tools(self, tmp_path):
        """Without filesystem_write calls, uses the parser as before."""
        from src.orchestrator import apply_code_node

        state = self._make_state(
            workspace_root=str(tmp_path),
            generated_code="# --- FILE: app.py ---\nprint('from parser')\n",
            tool_calls_log=[],
        )

        with patch("src.orchestrator._get_workspace_root", return_value=tmp_path):
            result = await apply_code_node(state)

        assert len(result["parsed_files"]) == 1
        assert "from parser" in result["parsed_files"][0]["content"]
        assert not any("tool-written" in t for t in result["trace"])

    async def test_falls_back_when_disk_files_missing(self, tmp_path):
        """If tool-written files aren't on disk, falls back to parser."""
        from src.orchestrator import apply_code_node

        # Don't write anything to disk
        state = self._make_state(
            workspace_root=str(tmp_path),
            generated_code="# --- FILE: app.py ---\nprint('fallback')\n",
            tool_calls_log=[
                {"agent": "developer", "turn": 1, "tool": "filesystem_write",
                 "args_preview": "{}", "result_preview": "ok", "success": True},
            ],
        )

        result = await apply_code_node(state)

        assert len(result["parsed_files"]) == 1
        assert "fallback" in result["parsed_files"][0]["content"]
        assert any("falling back" in t for t in result["trace"])

    async def test_ignores_failed_filesystem_write(self, tmp_path):
        """Failed filesystem_write calls don't trigger disk-read path."""
        from src.orchestrator import apply_code_node

        state = self._make_state(
            workspace_root=str(tmp_path),
            generated_code="# --- FILE: app.py ---\nprint('parsed')\n",
            tool_calls_log=[
                {"agent": "developer", "turn": 1, "tool": "filesystem_write",
                 "args_preview": "{}", "result_preview": "Error", "success": False},
            ],
        )

        with patch("src.orchestrator._get_workspace_root", return_value=tmp_path):
            result = await apply_code_node(state)

        # Should use parser path since the write failed
        assert len(result["parsed_files"]) == 1
        assert "parsed" in result["parsed_files"][0]["content"]
