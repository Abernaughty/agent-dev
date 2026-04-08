"""Tests for the remote GitHub workspace module (Issue #153).

Covers:
- setup_remote_workspace: clone success, failure, missing token, bad repo format
- cleanup_remote_workspace: removes dirs, handles missing dirs
- cleanup_stale_workspaces: removes old dirs, keeps recent ones
- validate_github_token_async: success, failure, missing token
- GitHubPRProvider.for_repo: factory creates instance with correct owner/repo
- publish_code_node: skips repo_subdir for remote workspaces
"""

from __future__ import annotations

import os
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.github_workspace import (
    cleanup_remote_workspace,
    cleanup_stale_workspaces,
    setup_remote_workspace,
    validate_github_token_async,
)

# -- setup_remote_workspace --


@pytest.mark.asyncio
async def test_setup_remote_workspace_success(tmp_path, monkeypatch):
    """Successful clone creates the directory and returns its path."""
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_test_token")
    base = tmp_path / "dev-suite"
    monkeypatch.setattr("src.github_workspace.REMOTE_WORKSPACE_BASE", base)

    mock_proc = AsyncMock()
    mock_proc.returncode = 0
    mock_proc.communicate = AsyncMock(return_value=(b"", b""))

    with patch("src.github_workspace.asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
        result = await setup_remote_workspace("owner/repo", "main", "task-123")

    assert result == base / "task-123"
    assert result.is_dir()
    # Verify clone command was called with correct args
    call_args = mock_exec.call_args
    assert "git" in call_args[0]
    assert "clone" in call_args[0]
    assert "--depth" in call_args[0]
    assert "https://github.com/owner/repo.git" in call_args[0]


@pytest.mark.asyncio
async def test_setup_remote_workspace_clone_failure(tmp_path, monkeypatch):
    """Failed clone raises ValueError and cleans up the directory."""
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_test_token")
    base = tmp_path / "dev-suite"
    monkeypatch.setattr("src.github_workspace.REMOTE_WORKSPACE_BASE", base)

    mock_proc = AsyncMock()
    mock_proc.returncode = 128
    mock_proc.communicate = AsyncMock(return_value=(b"", b"fatal: repository not found"))

    with patch("src.github_workspace.asyncio.create_subprocess_exec", return_value=mock_proc):
        with pytest.raises(ValueError, match="Git clone failed"):
            await setup_remote_workspace("owner/nonexistent", "main", "task-fail")

    # Directory should be cleaned up on failure
    assert not (base / "task-fail").exists()


@pytest.mark.asyncio
async def test_setup_remote_workspace_missing_token(monkeypatch):
    """Missing GITHUB_TOKEN raises ValueError immediately."""
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    with pytest.raises(ValueError, match="not set or empty"):
        await setup_remote_workspace("owner/repo", "main", "task-notoken")


@pytest.mark.asyncio
async def test_setup_remote_workspace_bad_repo_format(monkeypatch):
    """Invalid repo format (no slash) raises ValueError."""
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_test_token")

    with pytest.raises(ValueError, match="Invalid repo format"):
        await setup_remote_workspace("noslash", "main", "task-bad")


@pytest.mark.asyncio
async def test_setup_remote_workspace_custom_token_env(tmp_path, monkeypatch):
    """Custom token env var name is respected."""
    monkeypatch.setenv("MY_CUSTOM_TOKEN", "ghp_custom")
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    base = tmp_path / "dev-suite"
    monkeypatch.setattr("src.github_workspace.REMOTE_WORKSPACE_BASE", base)

    mock_proc = AsyncMock()
    mock_proc.returncode = 0
    mock_proc.communicate = AsyncMock(return_value=(b"", b""))

    with patch("src.github_workspace.asyncio.create_subprocess_exec", return_value=mock_proc):
        result = await setup_remote_workspace(
            "owner/repo", "main", "task-custom", token_env_var="MY_CUSTOM_TOKEN",
        )

    assert result == base / "task-custom"


# -- cleanup_remote_workspace --


def test_cleanup_remote_workspace(tmp_path, monkeypatch):
    """Cleanup removes the task directory."""
    base = tmp_path / "dev-suite"
    monkeypatch.setattr("src.github_workspace.REMOTE_WORKSPACE_BASE", base)

    task_dir = base / "task-cleanup"
    task_dir.mkdir(parents=True)
    (task_dir / "somefile.txt").write_text("data")

    cleanup_remote_workspace("task-cleanup")
    assert not task_dir.exists()


def test_cleanup_remote_workspace_nonexistent(tmp_path, monkeypatch):
    """Cleanup on nonexistent task_id does not raise."""
    base = tmp_path / "dev-suite"
    monkeypatch.setattr("src.github_workspace.REMOTE_WORKSPACE_BASE", base)

    cleanup_remote_workspace("task-ghost")  # Should not raise


# -- cleanup_stale_workspaces --


def test_cleanup_stale_workspaces(tmp_path, monkeypatch):
    """Removes directories older than max_age_hours, keeps recent ones."""
    base = tmp_path / "dev-suite"
    base.mkdir()
    monkeypatch.setattr("src.github_workspace.REMOTE_WORKSPACE_BASE", base)

    # Create an old directory
    old_dir = base / "task-old"
    old_dir.mkdir()
    old_mtime = time.time() - (48 * 3600)  # 48 hours ago
    os.utime(old_dir, (old_mtime, old_mtime))

    # Create a recent directory
    new_dir = base / "task-new"
    new_dir.mkdir()

    removed = cleanup_stale_workspaces(max_age_hours=24)

    assert removed == 1
    assert not old_dir.exists()
    assert new_dir.exists()


def test_cleanup_stale_workspaces_no_base_dir(tmp_path, monkeypatch):
    """Returns 0 when base directory doesn't exist."""
    monkeypatch.setattr(
        "src.github_workspace.REMOTE_WORKSPACE_BASE", tmp_path / "nonexistent",
    )
    assert cleanup_stale_workspaces() == 0


# -- validate_github_token_async --


@pytest.mark.asyncio
async def test_validate_github_token_success(monkeypatch):
    """Returns True when API responds 200."""
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_valid")

    mock_resp = MagicMock()
    mock_resp.status_code = 200

    with patch("src.github_workspace.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        result = await validate_github_token_async("owner/repo")

    assert result is True


@pytest.mark.asyncio
async def test_validate_github_token_failure(monkeypatch):
    """Returns False when API responds 404."""
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_invalid")

    mock_resp = MagicMock()
    mock_resp.status_code = 404

    with patch("src.github_workspace.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        result = await validate_github_token_async("owner/nonexistent")

    assert result is False


@pytest.mark.asyncio
async def test_validate_github_token_no_token(monkeypatch):
    """Returns False when token env var is not set."""
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    result = await validate_github_token_async("owner/repo")
    assert result is False


# -- GitHubPRProvider.for_repo --


def test_for_repo_factory():
    """for_repo() returns an instance with overridden owner/repo."""
    with patch.dict("os.environ", {"GITHUB_TOKEN": "ghp_test123"}):
        from src.api.github_prs import GitHubPRProvider

        provider = GitHubPRProvider.for_repo("other-owner", "other-repo")

        assert provider._owner == "other-owner"
        assert provider._repo == "other-repo"
        assert provider.configured  # token still from env
        assert "other-owner" in provider.base_url
        assert "other-repo" in provider.base_url


# -- WorkspaceManager.add_directory ephemeral --


def test_add_directory_ephemeral(tmp_path):
    """Ephemeral directories are added but not persisted to config."""
    from src.workspace import WorkspaceManager

    config_path = tmp_path / "workspaces.json"
    mgr = WorkspaceManager(default_root=tmp_path, config_path=config_path)

    ephemeral_dir = tmp_path / "ephemeral-workspace"
    ephemeral_dir.mkdir()

    result = mgr.add_directory(ephemeral_dir, ephemeral=True)
    assert result is True
    assert mgr.is_allowed(ephemeral_dir)

    # Config file should NOT have been written (or should not contain the ephemeral dir)
    if config_path.is_file():
        import json

        config = json.loads(config_path.read_text())
        dirs = config.get("allowed_directories", [])
        assert str(ephemeral_dir) not in dirs


# -- CreateTaskRequest model validation --


def test_create_task_request_github_valid():
    """GitHub workspace type with valid fields passes validation."""
    from src.api.models import CreateTaskRequest

    req = CreateTaskRequest(
        description="Test task",
        workspace_type="github",
        github_repo="owner/repo",
        github_branch="main",
    )
    assert req.workspace_type == "github"
    assert req.github_repo == "owner/repo"


def test_create_task_request_github_missing_repo():
    """GitHub workspace type without github_repo fails validation."""
    from src.api.models import CreateTaskRequest

    with pytest.raises(ValueError, match="github_repo"):
        CreateTaskRequest(
            description="Test task",
            workspace_type="github",
            github_branch="main",
        )


def test_create_task_request_github_missing_branch():
    """GitHub workspace type without github_branch fails validation."""
    from src.api.models import CreateTaskRequest

    with pytest.raises(ValueError, match="github_branch"):
        CreateTaskRequest(
            description="Test task",
            workspace_type="github",
            github_repo="owner/repo",
        )


def test_create_task_request_local_missing_workspace():
    """Local workspace type without workspace fails validation."""
    from src.api.models import CreateTaskRequest

    with pytest.raises(ValueError, match="workspace.*required"):
        CreateTaskRequest(
            description="Test task",
            workspace_type="local",
        )


def test_create_task_request_local_valid():
    """Local workspace type with workspace passes validation."""
    from src.api.models import CreateTaskRequest

    req = CreateTaskRequest(
        description="Test task",
        workspace="/some/path",
    )
    assert req.workspace_type == "local"
    assert req.workspace == "/some/path"


# -- publish_code_node: remote workspace skips subdir --


@pytest.mark.asyncio
async def test_publish_remote_workspace_skips_subdir():
    """For workspace_type='github', repo_subdir detection is skipped."""
    from src.orchestrator import _publish_code_async

    mock_provider = AsyncMock()
    mock_provider.configured = True
    mock_provider.create_branch = AsyncMock(return_value=True)
    mock_provider.push_files_batch = AsyncMock(return_value=True)
    mock_provider.create_pr = AsyncMock(return_value=None)  # PR fails, that's OK for this test
    mock_provider.owner = "remote-owner"
    mock_provider.repo = "remote-repo"
    mock_provider.close = AsyncMock()

    state = {
        "workspace_type": "github",
        "github_repo": "remote-owner/remote-repo",
        "github_branch": "develop",
        "create_pr": True,
        "parsed_files": [{"path": "src/main.py", "content": "print('hello')"}],
        "blueprint": MagicMock(
            task_id="test-task",
            instructions="implement hello world",
            acceptance_criteria=["it works"],
            target_files=["src/main.py"],
        ),
        "workspace_root": "/tmp/dev-suite/test-task",
        "trace": [],
    }

    with patch("src.api.github_prs.GitHubPRProvider.for_repo", return_value=mock_provider) as mock_for_repo:
        result = await _publish_code_async(state)

    # Provider should have been created via for_repo
    mock_for_repo.assert_called_once_with("remote-owner", "remote-repo")

    # push_files_batch should receive paths without subdir prefix
    push_call = mock_provider.push_files_batch.call_args
    files = push_call.kwargs.get("files") or push_call[1].get("files")
    assert files[0]["path"] == "src/main.py"  # No subdir prefix

    # Branch should be created from the target branch, not default "main"
    create_branch_call = mock_provider.create_branch.call_args
    assert create_branch_call.kwargs.get("from_branch") == "develop" or create_branch_call[1].get("from_branch") == "develop"

    # Dynamic provider should be closed
    mock_provider.close.assert_awaited_once()
