"""ToolProvider ABC and implementations.

Defines the interface ("front desk") that agents interact with for
filesystem and GitHub operations. The backend is swappable:

- Phase 1: LocalToolProvider (Python/pathlib/httpx)
- Phase 2: MCPToolProvider (real MCP server subprocess spawning, issue #13)

Agents and the orchestrator never import the concrete provider directly —
they call get_tools() from mcp_bridge.py, which returns LangChain Tools
backed by whichever provider is configured.
"""

import os
from abc import ABC, abstractmethod
from pathlib import Path

import httpx


class PathValidationError(Exception):
    """Raised when a filesystem path escapes the allowed workspace root."""


class ToolProvider(ABC):
    """Abstract interface for tool operations.

    Defines the 5 methods agents can call. Any concrete provider
    (local Python, MCP subprocess, mock) must implement all of them.
    """

    @abstractmethod
    def filesystem_read(self, path: str) -> str:
        """Read a file and return its contents as text."""

    @abstractmethod
    def filesystem_write(self, path: str, content: str) -> str:
        """Write content to a file. Returns confirmation message."""

    @abstractmethod
    def filesystem_list(self, path: str) -> str:
        """List files and directories at the given path. Returns formatted listing."""

    @abstractmethod
    def github_create_pr(
        self, title: str, body: str, head_branch: str, base_branch: str = "main"
    ) -> str:
        """Create a GitHub pull request. Returns PR URL."""

    @abstractmethod
    def github_read_diff(self, pr_number: int) -> str:
        """Read the diff of a GitHub pull request. Returns diff text."""


def _validate_path(requested: str, workspace_root: Path) -> Path:
    """Validate that a path is within the workspace root.

    Resolves symlinks and relative paths, then checks containment.
    Raises PathValidationError if the path escapes the workspace.
    """
    resolved = (workspace_root / requested).resolve()

    if not str(resolved).startswith(str(workspace_root.resolve())):
        raise PathValidationError(
            f"Path '{requested}' resolves to '{resolved}' which is outside "
            f"workspace root '{workspace_root}'"
        )

    return resolved


class LocalToolProvider(ToolProvider):
    """Python-native tool provider for Phase 1.

    Filesystem operations use pathlib (with path validation).
    GitHub operations use httpx against the GitHub REST API.
    """

    def __init__(
        self,
        workspace_root: str | Path,
        github_token: str | None = None,
        github_owner: str | None = None,
        github_repo: str | None = None,
    ):
        self.workspace_root = Path(workspace_root).resolve()
        self.github_token = github_token or os.getenv("GITHUB_TOKEN", "")
        self.github_owner = github_owner or os.getenv("GITHUB_OWNER", "")
        self.github_repo = github_repo or os.getenv("GITHUB_REPO", "")

        if not self.workspace_root.is_dir():
            raise ValueError(f"Workspace root does not exist: {self.workspace_root}")

    def filesystem_read(self, path: str) -> str:
        """Read a file within the workspace."""
        validated = _validate_path(path, self.workspace_root)

        if not validated.is_file():
            raise FileNotFoundError(f"File not found: {path}")

        return validated.read_text(encoding="utf-8")

    def filesystem_write(self, path: str, content: str) -> str:
        """Write content to a file within the workspace."""
        validated = _validate_path(path, self.workspace_root)

        validated.parent.mkdir(parents=True, exist_ok=True)
        validated.write_text(content, encoding="utf-8")

        return f"Successfully wrote {len(content)} characters to {path}"

    def filesystem_list(self, path: str) -> str:
        """List contents of a directory within the workspace."""
        validated = _validate_path(path, self.workspace_root)

        if not validated.is_dir():
            raise NotADirectoryError(f"Not a directory: {path}")

        entries = sorted(validated.iterdir())
        lines = []
        for entry in entries:
            prefix = "[DIR] " if entry.is_dir() else "[FILE]"
            rel = entry.relative_to(self.workspace_root)
            lines.append(f"{prefix} {rel}")

        if not lines:
            return f"Directory '{path}' is empty"

        return "\n".join(lines)

    def _github_headers(self) -> dict:
        """Build GitHub API request headers."""
        return {
            "Accept": "application/vnd.github.v3+json",
            "Authorization": f"Bearer {self.github_token}",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def _github_api_url(self, endpoint: str) -> str:
        """Build a GitHub API URL for the configured repo."""
        return f"https://api.github.com/repos/{self.github_owner}/{self.github_repo}{endpoint}"

    def github_create_pr(
        self, title: str, body: str, head_branch: str, base_branch: str = "main"
    ) -> str:
        """Create a pull request via the GitHub REST API."""
        if not self.github_token:
            raise ValueError("GITHUB_TOKEN is required for GitHub operations")

        response = httpx.post(
            self._github_api_url("/pulls"),
            headers=self._github_headers(),
            json={
                "title": title,
                "body": body,
                "head": head_branch,
                "base": base_branch,
            },
            timeout=30.0,
        )

        if response.status_code == 201:
            pr_data = response.json()
            return f"PR #{pr_data['number']} created: {pr_data['html_url']}"

        raise RuntimeError(
            f"GitHub API error {response.status_code}: {response.text}"
        )

    def github_read_diff(self, pr_number: int) -> str:
        """Read the diff of a pull request via the GitHub REST API."""
        if not self.github_token:
            raise ValueError("GITHUB_TOKEN is required for GitHub operations")

        headers = self._github_headers()
        headers["Accept"] = "application/vnd.github.v3.diff"

        response = httpx.get(
            self._github_api_url(f"/pulls/{pr_number}"),
            headers=headers,
            timeout=30.0,
        )

        if response.status_code == 200:
            return response.text

        raise RuntimeError(
            f"GitHub API error {response.status_code}: {response.text}"
        )
