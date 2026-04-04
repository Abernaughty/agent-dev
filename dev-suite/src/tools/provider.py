"""ToolProvider ABC and implementations.

Defines the async dynamic tool discovery interface that agents interact with
for external operations. The backend is swappable:

- Phase 1: LocalToolProvider (Python/pathlib/httpx)
- Phase 2: MCPToolProvider (real MCP server subprocess spawning, issue #13)

Agents and the orchestrator never import the concrete provider directly -
they call get_tools() from mcp_bridge.py, which returns LangChain Tools
backed by whichever provider is configured.

Interface design:
    - async list_tools() - dynamic discovery (supports any number of tools)
    - async call_tool(name, arguments) - unified dispatch (name-based routing)
    - ToolDefinition - Pydantic model describing each tool's schema

Async rationale (issue #27):
    The MCP Python SDK is natively async. The system architecture requires
    concurrent agent execution (multiple teams, background agents, cron jobs).
    Making the interface async from the start avoids a throwaway sync wrapper
    and enables native async MCPToolProvider in issue #13.
"""

import os
from abc import ABC, abstractmethod
from pathlib import Path, PurePosixPath

import httpx
from pydantic import BaseModel, Field

# -- Tool Definition Schema --


class ToolDefinition(BaseModel):
    """Schema describing a single tool's interface.

    Mirrors the MCP tools/list response format so MCPToolProvider
    can pass MCP inputSchema through without transformation.

    Attributes:
        name: Unique tool identifier (e.g., "filesystem_read").
        description: Human-readable description for the LLM.
        parameters: JSON Schema dict describing expected arguments.
    """

    name: str
    description: str
    parameters: dict = Field(default_factory=dict)


# -- Exceptions --


class PathValidationError(Exception):
    """Raised when a filesystem path escapes the allowed workspace root."""


class ToolNotFoundError(Exception):
    """Raised when call_tool is invoked with an unknown tool name."""


# -- Abstract Base Class --


class ToolProvider(ABC):
    """Abstract async interface for tool operations.

    Providers expose their capabilities dynamically via list_tools()
    and accept invocations via call_tool(). This enables any number
    of tools without requiring ABC method changes.

    All methods are async to support:
    - MCP SDK (natively async: stdio_client, ClientSession)
    - Concurrent agent execution (multiple teams, background agents)
    - Non-blocking I/O for httpx, subprocess spawning, etc.
    """

    @abstractmethod
    async def list_tools(self) -> list[ToolDefinition]:
        """Return all tools this provider offers."""

    @abstractmethod
    async def call_tool(self, name: str, arguments: dict) -> str:
        """Call a tool by name with the given arguments.

        Args:
            name: Tool name as returned by list_tools().
            arguments: Dict of arguments matching the tool's parameter schema.

        Returns:
            Tool execution result as a string.

        Raises:
            ToolNotFoundError: If the tool name is not recognized.
        """


# -- Path Validation --


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


# -- Local Tool Provider --


class LocalToolProvider(ToolProvider):
    """Python-native async tool provider for Phase 1.

    Filesystem operations use pathlib (with path validation).
    GitHub operations use httpx.AsyncClient against the GitHub REST API.

    Tools are registered in _TOOL_REGISTRY, which maps tool names to
    their definitions and handler methods. list_tools() and call_tool()
    are driven by this registry.
    """

    def __init__(
        self,
        workspace_root: str | Path,
        github_token: str | None = None,
        github_owner: str | None = None,
        github_repo: str | None = None,
    ):
        self.workspace_root = Path(workspace_root).resolve()

        # Use explicit value if provided (even empty string),
        # otherwise fall back to environment variable.
        self.github_token = (
            github_token if github_token is not None
            else os.getenv("GITHUB_TOKEN", "")
        )
        self.github_owner = (
            github_owner if github_owner is not None
            else os.getenv("GITHUB_OWNER", "")
        )
        self.github_repo = (
            github_repo if github_repo is not None
            else os.getenv("GITHUB_REPO", "")
        )

        if not self.workspace_root.is_dir():
            raise ValueError(
                f"Workspace root does not exist: {self.workspace_root}"
            )

        # Registry: maps tool name -> (ToolDefinition, handler_method)
        self._tool_registry: dict[str, tuple[ToolDefinition, callable]] = {
            "filesystem_read": (
                ToolDefinition(
                    name="filesystem_read",
                    description=(
                        "Read a file from the project workspace. "
                        "Input: relative file path (e.g., 'src/main.py'). "
                        "Output: file contents as text."
                    ),
                    parameters={
                        "type": "object",
                        "properties": {
                            "path": {
                                "type": "string",
                                "description": "Relative file path to read",
                            },
                        },
                        "required": ["path"],
                    },
                ),
                self._filesystem_read,
            ),
            "filesystem_write": (
                ToolDefinition(
                    name="filesystem_write",
                    description=(
                        "Write content to a file in the project workspace. "
                        "Output: confirmation message."
                    ),
                    parameters={
                        "type": "object",
                        "properties": {
                            "path": {
                                "type": "string",
                                "description": "Relative file path to write",
                            },
                            "content": {
                                "type": "string",
                                "description": "File content to write",
                            },
                        },
                        "required": ["path", "content"],
                    },
                ),
                self._filesystem_write,
            ),
            "filesystem_list": (
                ToolDefinition(
                    name="filesystem_list",
                    description=(
                        "List files and directories in the project workspace. "
                        "Input: relative directory path (e.g., 'src/' or '.'). "
                        "Output: formatted directory listing."
                    ),
                    parameters={
                        "type": "object",
                        "properties": {
                            "path": {
                                "type": "string",
                                "description": "Relative directory path to list",
                            },
                        },
                        "required": ["path"],
                    },
                ),
                self._filesystem_list,
            ),
            "github_create_pr": (
                ToolDefinition(
                    name="github_create_pr",
                    description=(
                        "Create a GitHub pull request. "
                        "Output: PR URL."
                    ),
                    parameters={
                        "type": "object",
                        "properties": {
                            "title": {
                                "type": "string",
                                "description": "PR title",
                            },
                            "body": {
                                "type": "string",
                                "description": "PR description",
                            },
                            "head_branch": {
                                "type": "string",
                                "description": "Source branch",
                            },
                            "base_branch": {
                                "type": "string",
                                "description": "Target branch",
                                "default": "main",
                            },
                        },
                        "required": ["title", "head_branch"],
                    },
                ),
                self._github_create_pr,
            ),
            "github_read_diff": (
                ToolDefinition(
                    name="github_read_diff",
                    description=(
                        "Read the diff of a GitHub pull request. "
                        "Input: PR number. Output: diff text."
                    ),
                    parameters={
                        "type": "object",
                        "properties": {
                            "pr_number": {
                                "type": "integer",
                                "description": "Pull request number",
                            },
                        },
                        "required": ["pr_number"],
                    },
                ),
                self._github_read_diff,
            ),
        }

    # -- ToolProvider interface --

    async def list_tools(self) -> list[ToolDefinition]:
        """Return all tools this provider offers."""
        return [defn for defn, _ in self._tool_registry.values()]

    async def call_tool(self, name: str, arguments: dict) -> str:
        """Call a tool by name with the given arguments.

        Validates that required arguments are present before dispatching.
        Extra arguments are ignored (Postel's Law).
        """
        if name not in self._tool_registry:
            available = ", ".join(sorted(self._tool_registry.keys()))
            raise ToolNotFoundError(
                f"Unknown tool '{name}'. Available tools: {available}"
            )

        definition, handler = self._tool_registry[name]

        # Validate required arguments
        required = definition.parameters.get("required", [])
        missing = [key for key in required if key not in arguments]
        if missing:
            raise ValueError(
                f"Tool '{name}' missing required arguments: "
                f"{', '.join(missing)}"
            )

        filtered_args = {
            k: v for k, v in arguments.items()
            if k in definition.parameters.get("properties", {})
        }
        return await handler(**filtered_args)

    # -- Filesystem operations (private handlers) --

    async def _filesystem_read(self, path: str) -> str:
        """Read a file within the workspace."""
        validated = _validate_path(path, self.workspace_root)

        if not validated.is_file():
            raise FileNotFoundError(f"File not found: {path}")

        return validated.read_text(encoding="utf-8")

    async def _filesystem_write(self, path: str, content: str) -> str:
        """Write content to a file within the workspace."""
        validated = _validate_path(path, self.workspace_root)

        validated.parent.mkdir(parents=True, exist_ok=True)
        validated.write_text(content, encoding="utf-8")

        return f"Successfully wrote {len(content)} characters to {path}"

    async def _filesystem_list(self, path: str) -> str:
        """List contents of a directory within the workspace."""
        validated = _validate_path(path, self.workspace_root)

        if not validated.is_dir():
            raise NotADirectoryError(f"Not a directory: {path}")

        entries = sorted(validated.iterdir())
        lines = []
        for entry in entries:
            prefix = "[DIR] " if entry.is_dir() else "[FILE]"
            rel = entry.relative_to(self.workspace_root)
            # Normalize to forward slashes for consistent cross-platform output
            rel_posix = PurePosixPath(rel)
            lines.append(f"{prefix} {rel_posix}")

        if not lines:
            return f"Directory '{path}' is empty"

        return "\n".join(lines)

    # -- GitHub operations (private handlers) --

    def _github_headers(self) -> dict:
        """Build GitHub API request headers."""
        return {
            "Accept": "application/vnd.github.v3+json",
            "Authorization": f"Bearer {self.github_token}",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def _github_api_url(self, endpoint: str) -> str:
        """Build a GitHub API URL for the configured repo."""
        return (
            f"https://api.github.com/repos/"
            f"{self.github_owner}/{self.github_repo}{endpoint}"
        )

    async def _github_create_pr(
        self,
        title: str,
        head_branch: str,
        body: str = "",
        base_branch: str = "main",
    ) -> str:
        """Create a pull request via the GitHub REST API."""
        if not self.github_token:
            raise ValueError(
                "GITHUB_TOKEN is required for GitHub operations"
            )

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                self._github_api_url("/pulls"),
                headers=self._github_headers(),
                json={
                    "title": title,
                    "body": body,
                    "head": head_branch,
                    "base": base_branch,
                },
            )

        if response.status_code == 201:
            pr_data = response.json()
            return (
                f"PR #{pr_data['number']} created: "
                f"{pr_data['html_url']}"
            )

        raise RuntimeError(
            f"GitHub API error {response.status_code}: "
            f"{response.text}"
        )

    async def _github_read_diff(self, pr_number: int) -> str:
        """Read the diff of a pull request via the GitHub REST API."""
        if not self.github_token:
            raise ValueError(
                "GITHUB_TOKEN is required for GitHub operations"
            )

        headers = self._github_headers()
        headers["Accept"] = "application/vnd.github.v3.diff"

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                self._github_api_url(f"/pulls/{pr_number}"),
                headers=headers,
            )

        if response.status_code == 200:
            return response.text

        raise RuntimeError(
            f"GitHub API error {response.status_code}: "
            f"{response.text}"
        )
