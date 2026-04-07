"""Remote GitHub workspace support — isolated temp clone + MCP push.

Issue #153: Enables users to select a GitHub repo as the task workspace.
Agents work in an isolated temp directory, completely separated from
the user's local checkout. Clone is for read context only (shallow).
All pushes go through the existing GitHub MCP / REST API.

Architecture: Approach D — Isolated Temp Clone + MCP Push
  - Clone into /tmp/dev-suite/{task-id}/ (shallow --depth 1)
  - Agents read/write files in the temp dir (pipeline unchanged)
  - push_files via GitHub API at the end (~2 API calls per task)
  - User's local filesystem is never touched

Security:
  - GIT_ASKPASS for clone auth — token never embedded in URLs
  - Token never written to persistent disk files
  - Temp directories cleaned up on task completion
  - Startup sweep removes stale dirs older than 24h

Usage:
    from src.github_workspace import (
        setup_remote_workspace,
        cleanup_remote_workspace,
        cleanup_stale_workspaces,
        validate_github_token,
    )

    # Before task starts:
    await validate_github_token("owner/repo", token)
    workspace_path = await setup_remote_workspace(
        repo="owner/repo", branch="main",
        task_id="task-abc", token=token,
    )

    # After task completes:
    cleanup_remote_workspace("task-abc")
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import stat
import time
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

# Base directory for all remote workspace temp clones.
# Configurable via env var for testing; defaults to /tmp/dev-suite.
REMOTE_WORKSPACE_BASE = Path(
    os.getenv("REMOTE_WORKSPACE_BASE", "/tmp/dev-suite")
)

# Maximum age (in hours) for stale workspace cleanup on startup.
STALE_MAX_AGE_HOURS = 24

# Clone timeout in seconds.
CLONE_TIMEOUT_SECONDS = 120


def _workspace_dir(task_id: str) -> Path:
    """Return the temp directory path for a given task."""
    return REMOTE_WORKSPACE_BASE / task_id


def _parse_repo(repo: str) -> tuple[str, str]:
    """Parse an 'owner/repo' string into (owner, repo).

    Raises ValueError if the format is invalid.
    """
    parts = repo.strip().split("/")
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise ValueError(
            f"Invalid repo format: '{repo}'. Expected 'owner/repo'."
        )
    # Strip .git suffix if present
    repo_name = parts[1].removesuffix(".git")
    return parts[0], repo_name


async def validate_github_token(repo: str, token: str) -> None:
    """Validate that a GitHub token has access to the target repo.

    Makes a lightweight GET /repos/{owner}/{repo} call to verify
    the token can see the repository. Fails fast with a clear
    error message if access is denied or the repo doesn't exist.

    Raises:
        ValueError: If the token lacks access or the repo is invalid.
    """
    owner, repo_name = _parse_repo(repo)

    if not token:
        raise ValueError(
            "No GitHub token configured. Set GITHUB_TOKEN in .env "
            "to enable remote workspaces."
        )

    url = f"https://api.github.com/repos/{owner}/{repo_name}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
        try:
            resp = await client.get(url, headers=headers)
        except httpx.HTTPError as e:
            raise ValueError(
                f"Failed to reach GitHub API: {type(e).__name__}: {e}"
            ) from e

        if resp.status_code == 404:
            raise ValueError(
                f"Repository '{owner}/{repo_name}' not found. "
                "Check the repo name and that your token has access."
            )
        if resp.status_code == 403:
            raise ValueError(
                f"Token does not have access to '{owner}/{repo_name}'. "
                "Ensure your GITHUB_TOKEN has 'Contents' scope on this repo."
            )
        if resp.status_code != 200:
            raise ValueError(
                f"GitHub API returned {resp.status_code} for "
                f"'{owner}/{repo_name}': {resp.text[:200]}"
            )

        # Verify we have push access by checking permissions
        data = resp.json()
        permissions = data.get("permissions", {})
        if not permissions.get("push", False):
            raise ValueError(
                f"Token has read-only access to '{owner}/{repo_name}'. "
                "Remote workspaces require push (write) access. "
                "Update your token's 'Contents' permission to read/write."
            )

    logger.info(
        "Token validated for %s/%s (push access confirmed)",
        owner, repo_name,
    )


async def setup_remote_workspace(
    repo: str,
    branch: str,
    task_id: str,
    token: str,
) -> Path:
    """Clone a GitHub repo into an isolated temp directory.

    Creates a shallow clone (--depth 1) of the specified branch into
    /tmp/dev-suite/{task-id}/. The clone is for read context only —
    agents use this file tree to understand the project structure.

    Auth uses GIT_ASKPASS to avoid embedding the token in URLs or
    writing credentials to disk.

    Args:
        repo: Repository in "owner/repo" format.
        branch: Branch to clone.
        task_id: Unique task identifier (used as directory name).
        token: GitHub personal access token.

    Returns:
        Path to the cloned workspace directory.

    Raises:
        ValueError: If the repo format is invalid or clone fails.
        FileExistsError: If the task directory already exists.
    """
    owner, repo_name = _parse_repo(repo)
    dest = _workspace_dir(task_id)

    if dest.exists():
        raise FileExistsError(
            f"Workspace directory already exists: {dest}. "
            f"Task '{task_id}' may already be running."
        )

    # Ensure base directory exists
    REMOTE_WORKSPACE_BASE.mkdir(parents=True, exist_ok=True)

    clone_url = f"https://github.com/{owner}/{repo_name}.git"

    # GIT_ASKPASS approach: use a shell command that echoes the token.
    # The token is passed via environment variable, never in the URL
    # or on disk. GIT_ASKPASS is called by git when it needs credentials.
    env = {
        **os.environ,
        "GIT_TERMINAL_PROMPT": "0",
        "_GIT_TOKEN": token,
    }

    # Write a minimal askpass helper to a temp location inside our
    # controlled base dir. It's deleted immediately after clone.
    askpass_path = REMOTE_WORKSPACE_BASE / f".askpass-{task_id}"
    try:
        askpass_path.write_text(
            '#!/bin/sh\necho "$_GIT_TOKEN"\n',
            encoding="utf-8",
        )
        askpass_path.chmod(stat.S_IRWXU)  # 0o700 — owner-only execute
        env["GIT_ASKPASS"] = str(askpass_path)

        cmd = [
            "git", "clone",
            "--depth", "1",
            "--branch", branch,
            "--single-branch",
            clone_url,
            str(dest),
        ]

        logger.info(
            "Cloning %s/%s (branch: %s) into %s",
            owner, repo_name, branch, dest,
        )

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(),
            timeout=CLONE_TIMEOUT_SECONDS,
        )

        if proc.returncode != 0:
            # Clean up partial clone on failure
            if dest.exists():
                shutil.rmtree(dest, ignore_errors=True)
            stderr_text = stderr.decode("utf-8", errors="replace").strip()
            # Sanitize: ensure token doesn't appear in error messages
            sanitized = stderr_text.replace(token, "***")
            raise ValueError(
                f"Git clone failed (exit {proc.returncode}) for "
                f"'{owner}/{repo_name}' branch '{branch}': {sanitized}"
            )

    except asyncio.TimeoutError:
        if dest.exists():
            shutil.rmtree(dest, ignore_errors=True)
        raise ValueError(
            f"Git clone timed out after {CLONE_TIMEOUT_SECONDS}s for "
            f"'{owner}/{repo_name}' branch '{branch}'. "
            "The repository may be very large or the network is slow."
        )
    finally:
        # Always delete the askpass helper — never leave credentials on disk
        if askpass_path.exists():
            askpass_path.unlink()

    logger.info(
        "Remote workspace ready: %s (%s/%s @ %s)",
        dest, owner, repo_name, branch,
    )
    return dest


def cleanup_remote_workspace(task_id: str) -> bool:
    """Remove a task's remote workspace temp directory.

    Safe to call even if the directory doesn't exist.

    Returns True if a directory was removed, False if nothing to clean.
    """
    dest = _workspace_dir(task_id)
    if not dest.exists():
        logger.debug("No remote workspace to clean for task %s", task_id)
        return False

    try:
        shutil.rmtree(dest)
        logger.info("Cleaned up remote workspace: %s", dest)
        return True
    except Exception as e:
        logger.warning(
            "Failed to clean remote workspace %s: %s", dest, e,
        )
        return False


def cleanup_stale_workspaces(max_age_hours: int = STALE_MAX_AGE_HOURS) -> int:
    """Remove stale remote workspace directories on startup.

    Scans REMOTE_WORKSPACE_BASE for directories older than max_age_hours
    and removes them. This catches orphaned workspaces from crashed tasks.

    Called during server startup (app lifespan).

    Returns the number of directories cleaned up.
    """
    if not REMOTE_WORKSPACE_BASE.exists():
        return 0

    max_age_seconds = max_age_hours * 3600
    now = time.time()
    cleaned = 0

    for entry in REMOTE_WORKSPACE_BASE.iterdir():
        if not entry.is_dir():
            # Skip non-directories (e.g., leftover askpass files)
            if entry.name.startswith(".askpass-"):
                try:
                    entry.unlink()
                    logger.debug("Removed stale askpass file: %s", entry)
                except OSError:
                    pass
            continue

        try:
            mtime = entry.stat().st_mtime
            age = now - mtime
            if age > max_age_seconds:
                shutil.rmtree(entry)
                logger.info(
                    "Removed stale workspace: %s (age: %.1f hours)",
                    entry, age / 3600,
                )
                cleaned += 1
        except Exception as e:
            logger.warning("Error checking/removing %s: %s", entry, e)

    if cleaned > 0:
        logger.info(
            "Startup cleanup: removed %d stale workspace(s) from %s",
            cleaned, REMOTE_WORKSPACE_BASE,
        )
    return cleaned
