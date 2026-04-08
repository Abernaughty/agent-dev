"""Remote GitHub workspace management (Issue #153).

Provides shallow-clone workspace setup, cleanup, and token validation
for running agent tasks against remote GitHub repositories.

Security:
- Clone uses GIT_ASKPASS — token never embedded in URL or CLI args.
- Token passed via subprocess env var (GIT_TOKEN_VALUE), not written to disk.
- Per-task askpass scripts avoid concurrency collisions.
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

REMOTE_WORKSPACE_BASE = Path("/tmp/dev-suite")


def _workspace_path(task_id: str) -> Path:
    """Return the temp directory path for a given task."""
    return REMOTE_WORKSPACE_BASE / task_id


async def setup_remote_workspace(
    repo: str,
    branch: str,
    task_id: str,
    token_env_var: str = "GITHUB_TOKEN",
) -> Path:
    """Shallow-clone a GitHub repo into a temp directory.

    Args:
        repo: GitHub repo in ``owner/repo`` format.
        branch: Branch to clone (e.g. ``main``).
        task_id: Unique task identifier (used as directory name).
        token_env_var: Name of the env var holding the GitHub PAT.

    Returns:
        Path to the cloned directory.

    Raises:
        ValueError: If the token is missing, the clone fails, or the
            repo/branch format is invalid.
    """
    token = os.getenv(token_env_var, "")
    if not token:
        raise ValueError(
            f"Environment variable '{token_env_var}' is not set or empty. "
            f"A GitHub token is required to clone '{repo}'."
        )

    if "/" not in repo:
        raise ValueError(f"Invalid repo format '{repo}' — expected 'owner/repo'.")

    clone_dir = _workspace_path(task_id)
    clone_dir.mkdir(parents=True, exist_ok=True)

    # Write a per-task GIT_ASKPASS helper script.
    # The script echoes $GIT_TOKEN_VALUE which is passed via the subprocess env.
    askpass_script = clone_dir / ".git-askpass.sh"
    askpass_script.write_text('#!/bin/sh\necho "$GIT_TOKEN_VALUE"\n', encoding="utf-8")
    askpass_script.chmod(askpass_script.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP)

    clone_env = {
        **os.environ,
        "GIT_ASKPASS": str(askpass_script),
        "GIT_TOKEN_VALUE": token,
        # Suppress interactive prompts if askpass fails.
        "GIT_TERMINAL_PROMPT": "0",
    }

    logger.info(
        "[WORKSPACE] Cloning %s (branch=%s) into %s",
        repo, branch, clone_dir,
    )

    proc = await asyncio.create_subprocess_exec(
        "git", "clone",
        "--depth", "1",
        "--single-branch",
        "-b", branch,
        f"https://github.com/{repo}.git",
        ".",
        cwd=str(clone_dir),
        env=clone_env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()

    # Clean up the askpass script immediately after clone.
    try:
        askpass_script.unlink(missing_ok=True)
    except OSError:
        pass

    if proc.returncode != 0:
        stderr_text = stderr.decode(errors="replace").strip()
        # Clean up the failed clone directory.
        shutil.rmtree(clone_dir, ignore_errors=True)
        raise ValueError(
            f"Git clone failed for '{repo}' (branch={branch}): {stderr_text}"
        )

    logger.info("[WORKSPACE] Clone complete: %s", clone_dir)
    return clone_dir


def cleanup_remote_workspace(task_id: str) -> None:
    """Delete the temp directory for a completed task."""
    clone_dir = _workspace_path(task_id)
    if clone_dir.is_dir():
        shutil.rmtree(clone_dir, ignore_errors=True)
        logger.info("[WORKSPACE] Cleaned up remote workspace: %s", clone_dir)
    else:
        logger.debug("[WORKSPACE] No workspace to clean up for task %s", task_id)


def cleanup_stale_workspaces(max_age_hours: int = 24) -> int:
    """Remove remote workspace directories older than *max_age_hours*.

    Called during application startup to prevent temp dir accumulation.
    Returns the number of directories removed.
    """
    if not REMOTE_WORKSPACE_BASE.is_dir():
        return 0

    cutoff = time.time() - (max_age_hours * 3600)
    removed = 0

    for entry in REMOTE_WORKSPACE_BASE.iterdir():
        if not entry.is_dir():
            continue
        try:
            mtime = entry.stat().st_mtime
            if mtime < cutoff:
                shutil.rmtree(entry, ignore_errors=True)
                logger.info("[WORKSPACE] Removed stale workspace: %s", entry)
                removed += 1
        except OSError as exc:
            logger.warning("[WORKSPACE] Error inspecting %s: %s", entry, exc)

    return removed


async def validate_github_token_async(
    repo: str,
    token_env_var: str = "GITHUB_TOKEN",
) -> bool:
    """Check whether the token can access *repo* on GitHub.

    Returns True if the API responds with 200 for the repo,
    False otherwise (missing token, 404, 403, network error).
    """
    token = os.getenv(token_env_var, "")
    if not token:
        return False

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(5.0)) as client:
            resp = await client.get(
                f"https://api.github.com/repos/{repo}",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            )
            return resp.status_code == 200
    except httpx.HTTPError as exc:
        logger.warning("[WORKSPACE] Token validation failed for %s: %s", repo, exc)
        return False
