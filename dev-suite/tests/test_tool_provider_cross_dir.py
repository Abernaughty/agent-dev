"""Tests for LocalToolProvider cross-directory resolution.

Layer B gate test on #113 proved that #180's Fix 1 was partial:
gather_context got repo-root resolution for reads, but the
LocalToolProvider was still scoped to workspace_root only. When the
workspace is a monorepo subfolder (the real self-dev scenario),
filesystem_patch/write/read on sibling-dir paths failed or created
ghost files under the workspace subfolder.

These tests pin the fixed behavior: tool handlers share the same
repo-root walk used by gather_context, and paths that clearly
reference a top-level repo directory resolve repo-relative.

Self-contained: uses a minimal inline Svelte-like fixture so the
tests don't depend on any checked-in file. Standalone from the
broader test_surgical_edits.py suite.
"""

from pathlib import Path

import pytest

from src.tools.provider import (
    LocalToolProvider,
    PathValidationError,
    _find_repo_root,
    _resolve_path_smart,
)

# Minimal Svelte-like fixture: just enough structure to exercise the
# surgical-edit scenario (Math.min clamp change), without pulling in
# the full 209-line BottomPanel fixture from the other test file.
FIXTURE_CONTENT = """<script lang=\"ts\">
	import { onMount } from 'svelte';

	let height = 0;

	function handleMouseMove(e: MouseEvent) {
		const newHeight = Math.max(60, Math.min(400, height + e.movementY));
		height = newHeight;
	}

	onMount(() => {
		console.log('mounted');
	});
</script>
"""


def _make_repo(tmp_path: Path) -> tuple[Path, Path]:
    """Create a monorepo layout with .git/, dev-suite/, and a
    dashboard/ sibling dir containing a Svelte file.

    Returns (repo_root, workspace_root) where workspace_root is
    dev-suite/ -- exactly the #113 Layer B scenario.
    """
    (tmp_path / ".git").mkdir()
    workspace_root = tmp_path / "dev-suite"
    workspace_root.mkdir()
    target_dir = tmp_path / "dashboard" / "src" / "lib" / "components"
    target_dir.mkdir(parents=True)
    target_file = target_dir / "BottomPanel.svelte"
    target_file.write_text(FIXTURE_CONTENT, encoding="utf-8")
    return tmp_path, workspace_root


# -- Unit tests for the helper functions --


class TestFindRepoRoot:
    def test_finds_git_parent(self, tmp_path):
        (tmp_path / ".git").mkdir()
        nested = tmp_path / "a" / "b" / "c"
        nested.mkdir(parents=True)
        assert _find_repo_root(nested) == tmp_path

    def test_returns_start_when_no_git(self, tmp_path):
        start = tmp_path / "orphan"
        start.mkdir()
        assert _find_repo_root(start) == start


class TestResolvePathSmart:
    def test_same_root_uses_workspace(self, tmp_path):
        (tmp_path / "foo.txt").write_text("x")
        resolved = _resolve_path_smart("foo.txt", tmp_path, tmp_path)
        assert resolved == (tmp_path / "foo.txt").resolve()

    def test_first_segment_only_in_repo_picks_repo(self, tmp_path):
        ws = tmp_path / "dev-suite"
        ws.mkdir()
        (tmp_path / "dashboard").mkdir()
        resolved = _resolve_path_smart("dashboard/x.ts", ws, tmp_path)
        assert resolved == (tmp_path / "dashboard" / "x.ts").resolve()

    def test_first_segment_in_workspace_picks_workspace(self, tmp_path):
        ws = tmp_path / "dev-suite"
        ws.mkdir()
        (ws / "src").mkdir()
        (tmp_path / "src").mkdir()
        resolved = _resolve_path_smart("src/main.py", ws, tmp_path)
        assert resolved == (ws / "src" / "main.py").resolve()

    def test_unknown_first_segment_defaults_to_workspace(self, tmp_path):
        ws = tmp_path / "dev-suite"
        ws.mkdir()
        resolved = _resolve_path_smart("new_folder/foo.py", ws, tmp_path)
        assert resolved == (ws / "new_folder" / "foo.py").resolve()


# -- Integration tests against LocalToolProvider --


class TestLocalToolProviderAllowedRoot:
    def test_auto_detects_repo_root_via_git_walk(self, tmp_path):
        _, workspace_root = _make_repo(tmp_path)
        provider = LocalToolProvider(workspace_root=workspace_root)
        assert provider.allowed_root == tmp_path.resolve()
        assert provider.workspace_root == workspace_root.resolve()

    def test_falls_back_to_workspace_without_git(self, tmp_path):
        ws = tmp_path / "no_git_here"
        ws.mkdir()
        provider = LocalToolProvider(workspace_root=ws)
        assert provider.allowed_root == ws.resolve()

    def test_explicit_override(self, tmp_path):
        _, workspace_root = _make_repo(tmp_path)
        explicit = tmp_path / "dashboard"
        provider = LocalToolProvider(
            workspace_root=workspace_root, allowed_root=explicit
        )
        assert provider.allowed_root == explicit.resolve()


class TestToolHandlersCrossDirectory:
    """Tool handlers must hit real sibling-dir files instead of
    creating ghosts under workspace_root. Reproduces the #113 Layer B
    failure mode."""

    async def test_patch_hits_real_sibling_file_not_ghost(self, tmp_path):
        _, workspace_root = _make_repo(tmp_path)
        real_file = (
            tmp_path / "dashboard" / "src" / "lib" / "components" / "BottomPanel.svelte"
        )
        ghost = (
            workspace_root / "dashboard" / "src" / "lib" / "components" / "BottomPanel.svelte"
        )
        assert real_file.exists() and not ghost.exists()

        provider = LocalToolProvider(workspace_root=workspace_root)
        result = await provider.call_tool(
            "filesystem_patch",
            {
                "path": "dashboard/src/lib/components/BottomPanel.svelte",
                "search": "Math.min(400, height + e.movementY)",
                "replace": "Math.min(window.innerHeight * 0.8, height + e.movementY)",
            },
        )
        assert "Successfully patched" in result
        assert "window.innerHeight * 0.8" in real_file.read_text(encoding="utf-8")
        assert not ghost.exists(), (
            "filesystem_patch must edit the real sibling-dir file, "
            "not create a ghost copy under the workspace subfolder"
        )

    async def test_write_creates_at_repo_location_not_workspace(self, tmp_path):
        _, workspace_root = _make_repo(tmp_path)
        provider = LocalToolProvider(workspace_root=workspace_root)

        result = await provider.call_tool(
            "filesystem_write",
            {
                "path": "dashboard/src/lib/new_helper.ts",
                "content": "export const hello = 'world';\n",
            },
        )
        assert "Successfully wrote" in result

        real_new = tmp_path / "dashboard" / "src" / "lib" / "new_helper.ts"
        ghost_new = workspace_root / "dashboard" / "src" / "lib" / "new_helper.ts"
        assert real_new.is_file()
        assert not ghost_new.exists()
        assert real_new.read_text(encoding="utf-8") == "export const hello = 'world';\n"

    async def test_read_returns_real_sibling_content(self, tmp_path):
        _, workspace_root = _make_repo(tmp_path)
        provider = LocalToolProvider(workspace_root=workspace_root)

        content = await provider.call_tool(
            "filesystem_read",
            {"path": "dashboard/src/lib/components/BottomPanel.svelte"},
        )
        assert "onMount" in content
        assert "Math.min(400" in content

    async def test_list_sibling_directory(self, tmp_path):
        _, workspace_root = _make_repo(tmp_path)
        provider = LocalToolProvider(workspace_root=workspace_root)

        listing = await provider.call_tool(
            "filesystem_list", {"path": "dashboard/src/lib/components"}
        )
        assert "BottomPanel.svelte" in listing
        # Entry path is reported relative to repo root so subsequent
        # filesystem_read calls resolve correctly.
        assert "dashboard/src/lib/components/BottomPanel.svelte" in listing

    async def test_workspace_relative_paths_still_work(self, tmp_path):
        """Regression guard: paths whose first segment is a workspace
        subdir still resolve workspace-relative."""
        _, workspace_root = _make_repo(tmp_path)
        (workspace_root / "src").mkdir()
        (workspace_root / "src" / "main.py").write_text("workspace version", encoding="utf-8")
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.py").write_text("repo version", encoding="utf-8")

        provider = LocalToolProvider(workspace_root=workspace_root)
        content = await provider.call_tool(
            "filesystem_read", {"path": "src/main.py"}
        )
        assert content == "workspace version"


class TestCrossDirectorySecurityBoundary:
    async def test_path_outside_repo_root_rejected(self, tmp_path):
        _, workspace_root = _make_repo(tmp_path)
        outside = tmp_path.parent / f"outside-{tmp_path.name}.secret"
        outside.write_text("leaked", encoding="utf-8")
        try:
            provider = LocalToolProvider(workspace_root=workspace_root)
            with pytest.raises(PathValidationError):
                await provider.call_tool(
                    "filesystem_read",
                    {"path": f"../../{outside.name}"},
                )
        finally:
            if outside.exists():
                outside.unlink()

    async def test_blocked_patterns_still_enforced_across_repo(self, tmp_path):
        """.env at the repo root must still be rejected, even though
        the boundary widened from workspace to repo."""
        _, workspace_root = _make_repo(tmp_path)
        (tmp_path / ".env").write_text("SECRET=x", encoding="utf-8")

        provider = LocalToolProvider(workspace_root=workspace_root)
        from src.tools.provider import BlockedPathError

        with pytest.raises(BlockedPathError):
            await provider.call_tool("filesystem_read", {"path": ".env"})
