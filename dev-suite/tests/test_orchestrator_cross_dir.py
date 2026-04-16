"""Tests for cross-directory resolution in orchestrator nodes.

Layer B gate-test on #113 revealed that #187's tool-provider fix was
not enough on its own: the Developer correctly used filesystem_patch
to produce a +1/-1 surgical edit on the real sibling-dir file, but
apply_code_node / publish_code_node still resolved target_path
against workspace_root, so they missed the edit and published a
bogus PR containing the Developer's text summary as `output.py`.

These tests pin the fix for that gap:

- `_resolve_target_path` helper mirrors provider's smart resolution
- `apply_code_node` reads tool-written files from the real repo path
- `publish_code_node`'s `files_payload` builds correct repo-relative
  paths for cross-dir targets (no `dev-suite/dashboard/...` bogus)
"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agents.architect import Blueprint
from src.orchestrator import (
    GraphState,
    WorkflowStatus,
    _find_repo_root,
    _resolve_target_path,
    apply_code_node,
    publish_code_node,
)


def _make_monorepo(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Create .git/, dev-suite/, dashboard/ layout.

    Returns (repo_root, workspace_root=dev-suite, dashboard_root).
    """
    (tmp_path / ".git").mkdir()
    workspace = tmp_path / "dev-suite"
    workspace.mkdir()
    dashboard = tmp_path / "dashboard" / "src" / "lib" / "components"
    dashboard.mkdir(parents=True)
    return tmp_path, workspace, dashboard


# -- Helper tests --


class TestResolveTargetPath:
    def test_same_root_no_walk_needed(self, tmp_path):
        (tmp_path / "foo.py").write_text("x", encoding="utf-8")
        assert _resolve_target_path("foo.py", tmp_path, tmp_path) == (tmp_path / "foo.py").resolve()

    def test_sibling_dir_resolves_to_repo(self, tmp_path):
        repo, ws, _ = _make_monorepo(tmp_path)
        # target lives at <repo>/dashboard/foo.svelte -- a sibling of workspace
        assert _resolve_target_path(
            "dashboard/src/lib/components/Foo.svelte", ws, repo
        ) == (repo / "dashboard" / "src" / "lib" / "components" / "Foo.svelte").resolve()

    def test_workspace_subdir_wins_when_ambiguous(self, tmp_path):
        repo, ws, _ = _make_monorepo(tmp_path)
        (ws / "src").mkdir()
        (repo / "src").mkdir()
        # "src/main.py" -- both workspace and repo have src/, prefer workspace
        assert _resolve_target_path("src/main.py", ws, repo) == (ws / "src" / "main.py").resolve()

    def test_unknown_first_segment_defaults_workspace(self, tmp_path):
        _, ws, _ = _make_monorepo(tmp_path)
        # "new_folder/foo.py" doesn't exist anywhere yet -- creating new
        # under workspace is the safe default for the parser path.
        assert _resolve_target_path("new_folder/foo.py", ws, tmp_path) == (ws / "new_folder" / "foo.py").resolve()


class TestFindRepoRoot:
    def test_walks_to_git_marker(self, tmp_path):
        repo, ws, _ = _make_monorepo(tmp_path)
        assert _find_repo_root(ws) == repo

    def test_falls_back_to_start(self, tmp_path):
        lonely = tmp_path / "no_git"
        lonely.mkdir()
        assert _find_repo_root(lonely) == lonely


# -- apply_code_node cross-dir behavior --


class TestApplyCodeNodeCrossDirectory:
    """Layer B reproduction: filesystem_patch writes the real sibling
    file; apply_code_node must read from the same location."""

    async def test_reads_tool_written_sibling_file(self, tmp_path):
        repo, workspace, dashboard = _make_monorepo(tmp_path)
        target_rel = "dashboard/src/lib/components/Panel.svelte"
        real_file = repo / target_rel
        real_file.write_text("const x = 42;\n", encoding="utf-8")

        state: GraphState = {
            "task_description": "",
            "generated_code": "",
            "blueprint": Blueprint(
                task_id="t1",
                target_files=[target_rel],
                instructions="patch it",
                constraints=[],
                acceptance_criteria=["ok"],
                summary="patch the panel",
            ),
            "failure_report": None,
            "status": WorkflowStatus.REVIEWING,
            "retry_count": 0,
            "tokens_used": 0,
            "error_message": "",
            "memory_context": [],
            "memory_writes": [],
            "trace": [],
            "sandbox_result": None,
            "parsed_files": [],
            "tool_calls_log": [
                {"tool": "filesystem_patch", "success": True, "agent": "developer"}
            ],
            "workspace_root": str(workspace),
        }

        result = await apply_code_node(state)
        files = result["parsed_files"]
        assert len(files) == 1
        assert files[0]["path"] == target_rel
        assert files[0]["content"] == "const x = 42;\n"
        # Regression guard: must NOT fall back to parser (which would
        # produce empty parsed_files since there's no generated_code).
        assert not any("falling back to parser" in t for t in result["trace"])

    async def test_workspace_relative_target_still_works(self, tmp_path):
        """Paths that are legitimately workspace-relative still resolve
        against workspace_root (regression guard)."""
        repo, workspace, _ = _make_monorepo(tmp_path)
        (workspace / "src").mkdir()
        (workspace / "src" / "main.py").write_text("def hello(): pass", encoding="utf-8")

        state: GraphState = {
            "task_description": "",
            "generated_code": "",
            "blueprint": Blueprint(
                task_id="t2",
                target_files=["src/main.py"],
                instructions="patch it",
                constraints=[],
                acceptance_criteria=["ok"],
                summary="patch main",
            ),
            "failure_report": None,
            "status": WorkflowStatus.REVIEWING,
            "retry_count": 0,
            "tokens_used": 0,
            "error_message": "",
            "memory_context": [],
            "memory_writes": [],
            "trace": [],
            "sandbox_result": None,
            "parsed_files": [],
            "tool_calls_log": [
                {"tool": "filesystem_write", "success": True, "agent": "developer"}
            ],
            "workspace_root": str(workspace),
        }
        result = await apply_code_node(state)
        files = result["parsed_files"]
        assert len(files) == 1
        assert files[0]["content"] == "def hello(): pass"


# -- publish_code_node files_payload --


def _make_pr_mock(pr_number: int = 999):
    m = MagicMock()
    m.number = pr_number
    m.id = f"#{pr_number}"
    return m


def _make_publish_state(workspace: Path, parsed_files: list[dict]) -> GraphState:
    return {
        "task_description": "t",
        "blueprint": Blueprint(
            task_id="fix-113-resize-cap",
            target_files=[pf["path"] for pf in parsed_files],
            instructions="fix",
            constraints=[],
            acceptance_criteria=["ok"],
            summary="fix the resize cap",
        ),
        "generated_code": "",
        "failure_report": None,
        "status": WorkflowStatus.PASSED,
        "retry_count": 0,
        "tokens_used": 1000,
        "error_message": "",
        "memory_context": [],
        "memory_writes": [],
        "trace": [],
        "sandbox_result": None,
        "parsed_files": parsed_files,
        "tool_calls_log": [],
        "workspace_root": str(workspace),
        "create_pr": True,
    }


class TestPublishCodeFilesPayloadCrossDirectory:
    """Layer B finding: `files_payload` construction was prepending
    `dev-suite/` to every path, so cross-dir targets like
    `dashboard/foo.svelte` became `dev-suite/dashboard/foo.svelte` --
    a path that doesn't exist in the repo. PRs ended up either
    404'ing on push or containing bogus new files."""

    @patch("src.api.github_prs.github_pr_provider")
    async def test_sibling_dir_path_pushed_as_repo_relative(
        self, mock_provider, tmp_path
    ):
        _, workspace, _ = _make_monorepo(tmp_path)
        mock_provider.configured = True
        mock_provider.owner = "Abernaughty"
        mock_provider.repo = "agent-dev"
        mock_provider.create_branch = AsyncMock(return_value=True)
        mock_provider.push_files_batch = AsyncMock(return_value=True)
        mock_provider.create_pr = AsyncMock(return_value=_make_pr_mock(999))

        state = _make_publish_state(
            workspace,
            [{
                "path": "dashboard/src/lib/components/BottomPanel.svelte",
                "content": "// patched\n",
            }],
        )
        await publish_code_node(state)

        push_args = mock_provider.push_files_batch.call_args.kwargs
        files_payload = push_args["files"]
        assert len(files_payload) == 1
        # Critical: path was NOT prefixed with "dev-suite/"
        assert files_payload[0]["path"] == "dashboard/src/lib/components/BottomPanel.svelte"
        assert files_payload[0]["content"] == "// patched\n"

    @patch("src.api.github_prs.github_pr_provider")
    async def test_workspace_relative_path_still_gets_repo_subdir_prefix(
        self, mock_provider, tmp_path
    ):
        """Regression guard: when the target is genuinely workspace-relative
        (not a sibling dir), the existing repo_subdir prefix behavior is
        preserved."""
        _, workspace, _ = _make_monorepo(tmp_path)
        (workspace / "src").mkdir()
        mock_provider.configured = True
        mock_provider.owner = "Abernaughty"
        mock_provider.repo = "agent-dev"
        mock_provider.create_branch = AsyncMock(return_value=True)
        mock_provider.push_files_batch = AsyncMock(return_value=True)
        mock_provider.create_pr = AsyncMock(return_value=_make_pr_mock(1000))

        state = _make_publish_state(
            workspace,
            [{"path": "src/main.py", "content": "print('hi')\n"}],
        )
        await publish_code_node(state)

        files_payload = mock_provider.push_files_batch.call_args.kwargs["files"]
        assert len(files_payload) == 1
        # workspace is dev-suite/, so workspace-relative "src/main.py" becomes
        # "dev-suite/src/main.py" for the PR (existing behavior preserved).
        assert files_payload[0]["path"] == "dev-suite/src/main.py"

    @patch("src.api.github_prs.github_pr_provider")
    async def test_mixed_payload_paths_routed_correctly(
        self, mock_provider, tmp_path
    ):
        """Target_files can mix sibling-dir and workspace-relative paths;
        each should route independently."""
        _, workspace, _ = _make_monorepo(tmp_path)
        (workspace / "src").mkdir()
        mock_provider.configured = True
        mock_provider.owner = "Abernaughty"
        mock_provider.repo = "agent-dev"
        mock_provider.create_branch = AsyncMock(return_value=True)
        mock_provider.push_files_batch = AsyncMock(return_value=True)
        mock_provider.create_pr = AsyncMock(return_value=_make_pr_mock(1001))

        state = _make_publish_state(
            workspace,
            [
                {"path": "dashboard/src/lib/x.svelte", "content": "<script>\n"},
                {"path": "src/utils.py", "content": "# helper\n"},
            ],
        )
        await publish_code_node(state)

        paths = [pf["path"] for pf in mock_provider.push_files_batch.call_args.kwargs["files"]]
        assert "dashboard/src/lib/x.svelte" in paths
        assert "dev-suite/src/utils.py" in paths
