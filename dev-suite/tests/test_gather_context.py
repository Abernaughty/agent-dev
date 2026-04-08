"""Tests for the pre-Architect context gathering node (Issue #158).

Covers: file reading, truncation, budget management, auto-inference,
explicit RELATED_FILES parsing, and the gather_context_node itself.
"""

from pathlib import Path

import pytest

from src.orchestrator import (
    CONTEXT_BUDGET_CHARS,
    GraphState,
    WorkflowStatus,
    _infer_relevant_files,
    _read_context_files,
    _truncate_file,
    gather_context_node,
)


# -- Helpers --

def _make_workspace(tmp_path: Path, files: dict[str, str]) -> Path:
    """Create a temporary workspace with the given files."""
    for relpath, content in files.items():
        fpath = tmp_path / relpath
        fpath.parent.mkdir(parents=True, exist_ok=True)
        fpath.write_text(content, encoding="utf-8")
    return tmp_path


# -- _truncate_file Tests --

class TestTruncateFile:
    def test_short_file_unchanged(self):
        content = "line1\nline2\nline3\n"
        result, truncated = _truncate_file(content, max_lines=500, tail_lines=50)
        assert result == content
        assert truncated is False

    def test_large_file_truncated(self):
        lines = [f"line {i}\n" for i in range(1000)]
        content = "".join(lines)
        result, truncated = _truncate_file(content, max_lines=100, tail_lines=20)
        assert truncated is True
        assert "[... 900 lines truncated ...]" in result
        # Head preserved
        assert "line 0\n" in result
        assert "line 79\n" in result
        # Tail preserved
        assert "line 999\n" in result
        assert "line 980\n" in result
        # Middle removed
        assert "line 500\n" not in result

    def test_exact_boundary_not_truncated(self):
        lines = [f"line {i}\n" for i in range(100)]
        content = "".join(lines)
        result, truncated = _truncate_file(content, max_lines=100, tail_lines=20)
        assert truncated is False
        assert result == content


# -- _infer_relevant_files Tests --

class TestInferRelevantFiles:
    def test_finds_manifest_files(self, tmp_path):
        ws = _make_workspace(tmp_path, {
            "pyproject.toml": "[project]\nname = 'test'",
            "src/main.py": "print('hello')",
        })
        result = _infer_relevant_files(ws, "do something")
        paths = [str(p.relative_to(ws)) for p in result]
        assert "pyproject.toml" in paths

    def test_keyword_matching(self, tmp_path):
        ws = _make_workspace(tmp_path, {
            "src/memory/store.py": "class MemoryStore: pass",
            "src/api/main.py": "app = FastAPI()",
            "src/tools/bridge.py": "def bridge(): pass",
        })
        result = _infer_relevant_files(ws, "fix the memory store bug")
        paths = [str(p.relative_to(ws)) for p in result]
        assert "src/memory/store.py" in paths
        # "api" and "tools" not mentioned in task
        assert "src/api/main.py" not in paths

    def test_skips_dotenv_files(self, tmp_path):
        ws = _make_workspace(tmp_path, {
            ".env": "SECRET=foo",
            "src/main.py": "print('hello')",
        })
        result = _infer_relevant_files(ws, "update main")
        paths = [str(p.relative_to(ws)) for p in result]
        assert ".env" not in paths

    def test_skips_node_modules(self, tmp_path):
        ws = _make_workspace(tmp_path, {
            "node_modules/foo/index.js": "module.exports = {}",
            "src/index.js": "console.log('hello')",
        })
        result = _infer_relevant_files(ws, "update index")
        paths = [str(p.relative_to(ws)) for p in result]
        assert all("node_modules" not in p for p in paths)

    def test_empty_workspace(self, tmp_path):
        result = _infer_relevant_files(tmp_path, "do something")
        assert result == []

    def test_nonexistent_workspace(self, tmp_path):
        result = _infer_relevant_files(tmp_path / "nope", "do something")
        assert result == []


# -- _read_context_files Tests --

class TestReadContextFiles:
    def test_reads_files(self, tmp_path):
        ws = _make_workspace(tmp_path, {
            "src/main.py": "print('hello')",
            "src/utils.py": "def util(): pass",
        })
        files = [ws / "src/main.py", ws / "src/utils.py"]
        result = _read_context_files(files, ws)
        assert len(result) == 2
        assert result[0]["path"] == "src/main.py"
        assert result[0]["content"] == "print('hello')"
        assert result[0]["truncated"] is False

    def test_respects_budget(self, tmp_path):
        ws = _make_workspace(tmp_path, {
            "a.py": "x" * 5000,
            "b.py": "y" * 5000,
            "c.py": "z" * 5000,
        })
        files = [ws / "a.py", ws / "b.py", ws / "c.py"]
        result = _read_context_files(files, ws, budget_chars=8000)
        # Should read first file fully, second partially or skip third
        assert len(result) <= 3
        total = sum(len(r["content"]) for r in result)
        assert total <= 8500  # some slack for truncation marker

    def test_skips_nonexistent_files(self, tmp_path):
        ws = _make_workspace(tmp_path, {"a.py": "hello"})
        files = [ws / "a.py", ws / "missing.py"]
        result = _read_context_files(files, ws)
        assert len(result) == 1
        assert result[0]["path"] == "a.py"

    def test_blocks_path_traversal(self, tmp_path):
        ws = _make_workspace(tmp_path, {"src/main.py": "ok"})
        # Create a file outside workspace
        outside = tmp_path.parent / "outside_secret.py"
        outside.write_text("SECRET", encoding="utf-8")
        files = [ws / "../../outside_secret.py"]
        result = _read_context_files(files, ws)
        assert len(result) == 0

    def test_truncates_large_files(self, tmp_path):
        lines = "\n".join(f"line {i}" for i in range(1000))
        ws = _make_workspace(tmp_path, {"big.py": lines})
        files = [ws / "big.py"]
        result = _read_context_files(files, ws, max_lines=100, tail_lines=20)
        assert len(result) == 1
        assert result[0]["truncated"] is True
        assert "[..." in result[0]["content"]


# -- gather_context_node Tests --

class TestGatherContextNode:
    @pytest.mark.asyncio
    async def test_empty_workspace(self, tmp_path):
        state: GraphState = {
            "task_description": "do something",
            "workspace_root": str(tmp_path),
            "trace": [],
            "status": WorkflowStatus.PLANNING,
        }
        result = await gather_context_node(state)
        assert result["gathered_context"] == []
        assert any("no relevant files" in t for t in result["trace"])

    @pytest.mark.asyncio
    async def test_gathers_from_keywords(self, tmp_path):
        ws = _make_workspace(tmp_path, {
            "src/orchestrator.py": "class Orchestrator: pass",
            "src/api/main.py": "app = FastAPI()",
        })
        state: GraphState = {
            "task_description": "refactor the orchestrator retry logic",
            "workspace_root": str(ws),
            "trace": [],
            "status": WorkflowStatus.PLANNING,
        }
        result = await gather_context_node(state)
        paths = [c["path"] for c in result["gathered_context"]]
        assert "src/orchestrator.py" in paths

    @pytest.mark.asyncio
    async def test_explicit_related_files(self, tmp_path):
        ws = _make_workspace(tmp_path, {
            "src/memory/store.py": "class Store: pass",
            "src/api/main.py": "app = FastAPI()",
        })
        state: GraphState = {
            "task_description": "Fix the caching bug\nRELATED_FILES: src/memory/store.py, src/api/main.py",
            "workspace_root": str(ws),
            "trace": [],
            "status": WorkflowStatus.PLANNING,
        }
        result = await gather_context_node(state)
        paths = [c["path"] for c in result["gathered_context"]]
        assert "src/memory/store.py" in paths
        assert "src/api/main.py" in paths

    @pytest.mark.asyncio
    async def test_explicit_files_take_priority(self, tmp_path):
        ws = _make_workspace(tmp_path, {
            "src/explicit.py": "# explicit",
            "src/inferred.py": "# inferred",
        })
        state: GraphState = {
            "task_description": "Fix inferred module\nRELATED_FILES: src/explicit.py",
            "workspace_root": str(ws),
            "trace": [],
            "status": WorkflowStatus.PLANNING,
        }
        result = await gather_context_node(state)
        paths = [c["path"] for c in result["gathered_context"]]
        # Explicit file should come first
        assert paths[0] == "src/explicit.py"

    @pytest.mark.asyncio
    async def test_trace_reports_file_count(self, tmp_path):
        ws = _make_workspace(tmp_path, {
            "pyproject.toml": "[project]\nname = 'test'",
        })
        state: GraphState = {
            "task_description": "add a feature",
            "workspace_root": str(ws),
            "trace": [],
            "status": WorkflowStatus.PLANNING,
        }
        result = await gather_context_node(state)
        assert any("gathered 1 files" in t for t in result["trace"])

    @pytest.mark.asyncio
    async def test_no_workspace_root_uses_default(self, tmp_path, monkeypatch):
        """When workspace_root is empty, falls back to WORKSPACE_ROOT env."""
        ws = _make_workspace(tmp_path, {
            "pyproject.toml": "[project]\nname = 'test'",
        })
        monkeypatch.setenv("WORKSPACE_ROOT", str(ws))
        state: GraphState = {
            "task_description": "add a feature",
            "workspace_root": "",
            "trace": [],
            "status": WorkflowStatus.PLANNING,
        }
        result = await gather_context_node(state)
        assert len(result["gathered_context"]) >= 1
