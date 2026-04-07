"""Tests for github_workspace module — remote GitHub workspace support.

Issue #153: Tests cover setup, cleanup, validation, stale sweep,
GIT_ASKPASS security, and error handling.
"""

from __future__ import annotations

import asyncio
import os
import time
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.github_workspace import (
    REMOTE_WORKSPACE_BASE,
    _parse_repo,
    _workspace_dir,
    cleanup_remote_workspace,
    cleanup_stale_workspaces,
    setup_remote_workspace,
    validate_github_token,
)


# ---------------------------------------------------------------------------
# _parse_repo
# ---------------------------------------------------------------------------


class TestParseRepo:
    def test_valid_owner_repo(self):
        assert _parse_repo("Abernaughty/agent-dev") == ("Abernaughty", "agent-dev")

    def test_valid_with_git_suffix(self):
        assert _parse_repo("owner/repo.git") == ("owner", "repo")

    def test_valid_with_whitespace(self):
        assert _parse_repo("  owner/repo  ") == ("owner", "repo")

    def test_invalid_no_slash(self):
        with pytest.raises(ValueError, match="Expected 'owner/repo'"):
            _parse_repo("justrepo")

    def test_invalid_too_many_slashes(self):
        with pytest.raises(ValueError, match="Expected 'owner/repo'"):
            _parse_repo("a/b/c")

    def test_invalid_empty_owner(self):
        with pytest.raises(ValueError, match="Expected 'owner/repo'"):
            _parse_repo("/repo")

    def test_invalid_empty_repo(self):
        with pytest.raises(ValueError, match="Expected 'owner/repo'"):
            _parse_repo("owner/")

    def test_invalid_empty_string(self):
        with pytest.raises(ValueError):
            _parse_repo("")


# ---------------------------------------------------------------------------
# _workspace_dir
# ---------------------------------------------------------------------------


class TestWorkspaceDir:
    def test_returns_correct_path(self):
        result = _workspace_dir("task-abc")
        assert result == REMOTE_WORKSPACE_BASE / "task-abc"

    def test_unique_per_task(self):
        assert _workspace_dir("task-1") != _workspace_dir("task-2")


# ---------------------------------------------------------------------------
# validate_github_token
# ---------------------------------------------------------------------------


class TestValidateGithubToken:
    @pytest.mark.asyncio
    async def test_success_with_push_access(self):
        """Token with push access should pass validation."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "permissions": {"push": True, "pull": True, "admin": False},
        }

        with patch("src.github_workspace.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            # Should not raise
            await validate_github_token("owner/repo", "ghp_test123")

            # Verify the request was made to the right URL
            mock_client.get.assert_called_once()
            call_args = mock_client.get.call_args
            assert "repos/owner/repo" in call_args[0][0]

    @pytest.mark.asyncio
    async def test_no_token_raises(self):
        """Empty token should raise immediately without API call."""
        with pytest.raises(ValueError, match="No GitHub token configured"):
            await validate_github_token("owner/repo", "")

    @pytest.mark.asyncio
    async def test_repo_not_found_404(self):
        """404 response should give a clear 'not found' error."""
        mock_response = MagicMock()
        mock_response.status_code = 404

        with patch("src.github_workspace.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            with pytest.raises(ValueError, match="not found"):
                await validate_github_token("owner/nonexistent", "ghp_test")

    @pytest.mark.asyncio
    async def test_forbidden_403(self):
        """403 response should give a clear 'access denied' error."""
        mock_response = MagicMock()
        mock_response.status_code = 403

        with patch("src.github_workspace.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            with pytest.raises(ValueError, match="does not have access"):
                await validate_github_token("owner/private", "ghp_bad")

    @pytest.mark.asyncio
    async def test_read_only_access_rejected(self):
        """Token with pull-only access should be rejected."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "permissions": {"push": False, "pull": True, "admin": False},
        }

        with patch("src.github_workspace.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            with pytest.raises(ValueError, match="read-only access"):
                await validate_github_token("owner/repo", "ghp_readonly")

    @pytest.mark.asyncio
    async def test_invalid_repo_format(self):
        """Invalid repo format should raise before making API call."""
        with pytest.raises(ValueError, match="Expected 'owner/repo'"):
            await validate_github_token("badformat", "ghp_test")

    @pytest.mark.asyncio
    async def test_network_error(self):
        """Network errors should give a clear error message."""
        with patch("src.github_workspace.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get.side_effect = httpx.ConnectError("Connection refused")
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            with pytest.raises(ValueError, match="Failed to reach GitHub API"):
                await validate_github_token("owner/repo", "ghp_test")


# ---------------------------------------------------------------------------
# setup_remote_workspace
# ---------------------------------------------------------------------------


class TestSetupRemoteWorkspace:
    @pytest.mark.asyncio
    async def test_successful_clone(self, tmp_path):
        """Successful clone should create the workspace directory."""
        task_id = "test-clone-ok"
        base = tmp_path / "dev-suite"

        with (
            patch("src.github_workspace.REMOTE_WORKSPACE_BASE", base),
            patch("src.github_workspace._workspace_dir", return_value=base / task_id),
            patch("src.github_workspace.asyncio.create_subprocess_exec") as mock_exec,
        ):
            mock_proc = AsyncMock()
            mock_proc.returncode = 0
            mock_proc.communicate.return_value = (b"Cloning...\n", b"")
            mock_exec.return_value = mock_proc

            # Pre-create the dir to simulate git clone creating it
            (base / task_id).mkdir(parents=True)

            result = await setup_remote_workspace(
                repo="owner/repo",
                branch="main",
                task_id=task_id,
                token="ghp_test123",
            )

            assert result == base / task_id
            mock_exec.assert_called_once()

            # Verify git clone command
            call_args = mock_exec.call_args[0]
            assert "git" in call_args[0]
            assert "clone" in call_args
            assert "--depth" in call_args
            assert "1" in call_args
            assert "--branch" in call_args
            assert "main" in call_args

    @pytest.mark.asyncio
    async def test_clone_url_never_contains_token(self, tmp_path):
        """The clone URL must never contain the token."""
        task_id = "test-no-token-in-url"
        base = tmp_path / "dev-suite"
        token = "ghp_supersecret12345"

        with (
            patch("src.github_workspace.REMOTE_WORKSPACE_BASE", base),
            patch("src.github_workspace._workspace_dir", return_value=base / task_id),
            patch("src.github_workspace.asyncio.create_subprocess_exec") as mock_exec,
        ):
            mock_proc = AsyncMock()
            mock_proc.returncode = 0
            mock_proc.communicate.return_value = (b"", b"")
            mock_exec.return_value = mock_proc

            (base / task_id).mkdir(parents=True)

            await setup_remote_workspace(
                repo="owner/repo",
                branch="main",
                task_id=task_id,
                token=token,
            )

            # Check that the token does NOT appear in any positional args
            call_args = mock_exec.call_args[0]
            for arg in call_args:
                assert token not in str(arg), (
                    f"Token found in clone command arg: {arg}"
                )

    @pytest.mark.asyncio
    async def test_askpass_uses_env_not_url(self, tmp_path):
        """GIT_ASKPASS must be set in env, token passed via _GIT_TOKEN."""
        task_id = "test-askpass-env"
        base = tmp_path / "dev-suite"
        token = "ghp_envtoken"

        with (
            patch("src.github_workspace.REMOTE_WORKSPACE_BASE", base),
            patch("src.github_workspace._workspace_dir", return_value=base / task_id),
            patch("src.github_workspace.asyncio.create_subprocess_exec") as mock_exec,
        ):
            mock_proc = AsyncMock()
            mock_proc.returncode = 0
            mock_proc.communicate.return_value = (b"", b"")
            mock_exec.return_value = mock_proc

            (base / task_id).mkdir(parents=True)

            await setup_remote_workspace(
                repo="owner/repo",
                branch="main",
                task_id=task_id,
                token=token,
            )

            # Verify env was passed with _GIT_TOKEN
            call_kwargs = mock_exec.call_args[1]
            env = call_kwargs.get("env", {})
            assert env.get("_GIT_TOKEN") == token
            assert "GIT_ASKPASS" in env

    @pytest.mark.asyncio
    async def test_askpass_file_cleaned_up(self, tmp_path):
        """The askpass helper file must be deleted after clone."""
        task_id = "test-askpass-cleanup"
        base = tmp_path / "dev-suite"
        base.mkdir(parents=True)

        with (
            patch("src.github_workspace.REMOTE_WORKSPACE_BASE", base),
            patch("src.github_workspace._workspace_dir", return_value=base / task_id),
            patch("src.github_workspace.asyncio.create_subprocess_exec") as mock_exec,
        ):
            mock_proc = AsyncMock()
            mock_proc.returncode = 0
            mock_proc.communicate.return_value = (b"", b"")
            mock_exec.return_value = mock_proc

            (base / task_id).mkdir(parents=True)

            await setup_remote_workspace(
                repo="owner/repo",
                branch="main",
                task_id=task_id,
                token="ghp_test",
            )

            # Askpass file should be cleaned up
            askpass = base / f".askpass-{task_id}"
            assert not askpass.exists(), "Askpass file was not cleaned up"

    @pytest.mark.asyncio
    async def test_askpass_cleaned_on_failure(self, tmp_path):
        """The askpass file must be deleted even if clone fails."""
        task_id = "test-askpass-fail-cleanup"
        base = tmp_path / "dev-suite"
        base.mkdir(parents=True)

        with (
            patch("src.github_workspace.REMOTE_WORKSPACE_BASE", base),
            patch("src.github_workspace._workspace_dir", return_value=base / task_id),
            patch("src.github_workspace.asyncio.create_subprocess_exec") as mock_exec,
        ):
            mock_proc = AsyncMock()
            mock_proc.returncode = 128
            mock_proc.communicate.return_value = (b"", b"fatal: repo not found")
            mock_exec.return_value = mock_proc

            with pytest.raises(ValueError, match="Git clone failed"):
                await setup_remote_workspace(
                    repo="owner/repo",
                    branch="main",
                    task_id=task_id,
                    token="ghp_test",
                )

            askpass = base / f".askpass-{task_id}"
            assert not askpass.exists(), "Askpass file not cleaned after failure"

    @pytest.mark.asyncio
    async def test_clone_failure_cleans_partial_dir(self, tmp_path):
        """Failed clone should remove any partial directory."""
        task_id = "test-clone-fail"
        base = tmp_path / "dev-suite"
        dest = base / task_id

        with (
            patch("src.github_workspace.REMOTE_WORKSPACE_BASE", base),
            patch("src.github_workspace._workspace_dir", return_value=dest),
            patch("src.github_workspace.asyncio.create_subprocess_exec") as mock_exec,
        ):
            mock_proc = AsyncMock()
            mock_proc.returncode = 128
            mock_proc.communicate.return_value = (
                b"",
                b"fatal: repository 'https://github.com/x/y.git' not found",
            )
            mock_exec.return_value = mock_proc

            # Simulate git creating the dir before failing
            dest.mkdir(parents=True)

            with pytest.raises(ValueError, match="Git clone failed"):
                await setup_remote_workspace(
                    repo="x/y", branch="main",
                    task_id=task_id, token="ghp_test",
                )

            assert not dest.exists(), "Partial clone dir was not cleaned up"

    @pytest.mark.asyncio
    async def test_clone_error_sanitizes_token(self, tmp_path):
        """Error messages must not contain the token."""
        task_id = "test-sanitize"
        base = tmp_path / "dev-suite"
        token = "ghp_SUPERSECRET_DONT_LEAK"

        with (
            patch("src.github_workspace.REMOTE_WORKSPACE_BASE", base),
            patch("src.github_workspace._workspace_dir", return_value=base / task_id),
            patch("src.github_workspace.asyncio.create_subprocess_exec") as mock_exec,
        ):
            mock_proc = AsyncMock()
            mock_proc.returncode = 128
            # Simulate git including the token in stderr (unlikely but defensive)
            mock_proc.communicate.return_value = (
                b"",
                f"fatal: could not read from {token}@github.com".encode(),
            )
            mock_exec.return_value = mock_proc

            with pytest.raises(ValueError) as exc_info:
                await setup_remote_workspace(
                    repo="owner/repo", branch="main",
                    task_id=task_id, token=token,
                )

            assert token not in str(exc_info.value), (
                "Token was leaked in error message"
            )
            assert "***" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_existing_dir_raises(self, tmp_path):
        """If the task dir already exists, raise FileExistsError."""
        task_id = "test-exists"
        base = tmp_path / "dev-suite"
        dest = base / task_id
        dest.mkdir(parents=True)

        with (
            patch("src.github_workspace.REMOTE_WORKSPACE_BASE", base),
            patch("src.github_workspace._workspace_dir", return_value=dest),
        ):
            with pytest.raises(FileExistsError, match="already exists"):
                await setup_remote_workspace(
                    repo="owner/repo", branch="main",
                    task_id=task_id, token="ghp_test",
                )

    @pytest.mark.asyncio
    async def test_timeout_cleans_up(self, tmp_path):
        """Clone timeout should clean up and raise ValueError."""
        task_id = "test-timeout"
        base = tmp_path / "dev-suite"
        dest = base / task_id

        with (
            patch("src.github_workspace.REMOTE_WORKSPACE_BASE", base),
            patch("src.github_workspace._workspace_dir", return_value=dest),
            patch("src.github_workspace.CLONE_TIMEOUT_SECONDS", 0.01),
            patch("src.github_workspace.asyncio.create_subprocess_exec") as mock_exec,
        ):
            mock_proc = AsyncMock()
            # Simulate a hanging clone
            mock_proc.communicate.side_effect = asyncio.TimeoutError()
            mock_exec.return_value = mock_proc

            dest.mkdir(parents=True)

            with pytest.raises(ValueError, match="timed out"):
                await setup_remote_workspace(
                    repo="owner/repo", branch="main",
                    task_id=task_id, token="ghp_test",
                )


# ---------------------------------------------------------------------------
# cleanup_remote_workspace
# ---------------------------------------------------------------------------


class TestCleanupRemoteWorkspace:
    def test_removes_existing_dir(self, tmp_path):
        task_id = "test-cleanup"
        base = tmp_path / "dev-suite"
        dest = base / task_id
        dest.mkdir(parents=True)
        (dest / "some-file.py").write_text("content")

        with patch("src.github_workspace._workspace_dir", return_value=dest):
            result = cleanup_remote_workspace(task_id)

        assert result is True
        assert not dest.exists()

    def test_nonexistent_dir_returns_false(self, tmp_path):
        task_id = "test-no-dir"
        dest = tmp_path / "dev-suite" / task_id

        with patch("src.github_workspace._workspace_dir", return_value=dest):
            result = cleanup_remote_workspace(task_id)

        assert result is False

    def test_handles_permission_error(self, tmp_path):
        task_id = "test-perm-error"
        dest = tmp_path / "dev-suite" / task_id
        dest.mkdir(parents=True)

        with (
            patch("src.github_workspace._workspace_dir", return_value=dest),
            patch("src.github_workspace.shutil.rmtree", side_effect=PermissionError("denied")),
        ):
            result = cleanup_remote_workspace(task_id)

        assert result is False


# ---------------------------------------------------------------------------
# cleanup_stale_workspaces
# ---------------------------------------------------------------------------


class TestCleanupStaleWorkspaces:
    def test_removes_old_dirs(self, tmp_path):
        base = tmp_path / "dev-suite"
        base.mkdir()

        # Create a "stale" directory (we'll mock its age)
        stale = base / "old-task"
        stale.mkdir()
        # Set mtime to 25 hours ago
        old_time = time.time() - (25 * 3600)
        os.utime(stale, (old_time, old_time))

        with patch("src.github_workspace.REMOTE_WORKSPACE_BASE", base):
            cleaned = cleanup_stale_workspaces(max_age_hours=24)

        assert cleaned == 1
        assert not stale.exists()

    def test_keeps_fresh_dirs(self, tmp_path):
        base = tmp_path / "dev-suite"
        base.mkdir()

        fresh = base / "fresh-task"
        fresh.mkdir()
        # mtime is now (fresh) — should survive

        with patch("src.github_workspace.REMOTE_WORKSPACE_BASE", base):
            cleaned = cleanup_stale_workspaces(max_age_hours=24)

        assert cleaned == 0
        assert fresh.exists()

    def test_mixed_old_and_fresh(self, tmp_path):
        base = tmp_path / "dev-suite"
        base.mkdir()

        old = base / "old-task"
        old.mkdir()
        old_time = time.time() - (48 * 3600)
        os.utime(old, (old_time, old_time))

        fresh = base / "fresh-task"
        fresh.mkdir()

        with patch("src.github_workspace.REMOTE_WORKSPACE_BASE", base):
            cleaned = cleanup_stale_workspaces(max_age_hours=24)

        assert cleaned == 1
        assert not old.exists()
        assert fresh.exists()

    def test_base_doesnt_exist(self, tmp_path):
        base = tmp_path / "nonexistent"

        with patch("src.github_workspace.REMOTE_WORKSPACE_BASE", base):
            cleaned = cleanup_stale_workspaces()

        assert cleaned == 0

    def test_cleans_stale_askpass_files(self, tmp_path):
        base = tmp_path / "dev-suite"
        base.mkdir()

        askpass = base / ".askpass-orphaned"
        askpass.write_text("#!/bin/sh\necho token")

        with patch("src.github_workspace.REMOTE_WORKSPACE_BASE", base):
            cleanup_stale_workspaces()

        assert not askpass.exists()

    def test_ignores_non_dir_files(self, tmp_path):
        base = tmp_path / "dev-suite"
        base.mkdir()

        # Regular file (not .askpass) should be left alone
        regular = base / "notes.txt"
        regular.write_text("some notes")

        with patch("src.github_workspace.REMOTE_WORKSPACE_BASE", base):
            cleaned = cleanup_stale_workspaces()

        assert cleaned == 0
        assert regular.exists()
