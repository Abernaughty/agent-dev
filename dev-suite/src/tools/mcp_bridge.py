"""MCP bridge — config loader, version validator, and tool factory.

Loads MCP server configuration from mcp-config.json, validates
version pinning, and creates LangChain Tool objects backed by
whichever ToolProvider is active.

In Phase 1, mcp-config.json is a version audit trail documenting
which MCP server interface our LocalToolProvider matches.
In Phase 2 (issue #13), it drives actual MCP server subprocess spawning.
"""

import json
import logging
from pathlib import Path

from langchain_core.tools import Tool

from .provider import LocalToolProvider, ToolProvider

logger = logging.getLogger(__name__)


class MCPConfigError(Exception):
    """Raised when mcp-config.json is invalid or missing."""


class MCPConfig:
    """Parsed MCP server configuration from mcp-config.json."""

    def __init__(self, config_path: str | Path):
        self.config_path = Path(config_path)
        self._data: dict = {}
        self.load()

    def load(self) -> None:
        """Load and validate the config file."""
        if not self.config_path.is_file():
            raise MCPConfigError(f"Config file not found: {self.config_path}")

        try:
            self._data = json.loads(self.config_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise MCPConfigError(f"Invalid JSON in {self.config_path}: {e}") from e

        if "servers" not in self._data:
            raise MCPConfigError("Config missing 'servers' key")

    @property
    def servers(self) -> dict:
        """Return the servers configuration dict."""
        return self._data.get("servers", {})

    @property
    def last_reviewed(self) -> str:
        """Return the last review date."""
        return self._data.get("last_reviewed", "unknown")

    def get_server(self, name: str) -> dict:
        """Get config for a specific server. Raises KeyError if not found."""
        if name not in self.servers:
            raise KeyError(f"Server '{name}' not found in MCP config")
        return self.servers[name]

    def validate_versions(self) -> list[str]:
        """Check all servers have valid version pins.

        Returns a list of warning messages (empty if all good).
        """
        warnings = []
        for name, server in self.servers.items():
            if "version" not in server:
                warnings.append(f"Server '{name}' has no pinned version")
            if server.get("integrity", "").startswith("TODO"):
                warnings.append(
                    f"Server '{name}' has no integrity hash — "
                    f"add sha256 after first install"
                )
        return warnings


def load_mcp_config(config_path: str | Path) -> MCPConfig:
    """Load MCP config and log any version warnings."""
    config = MCPConfig(config_path)

    warnings = config.validate_versions()
    for w in warnings:
        logger.warning("MCP config: %s", w)

    logger.info(
        "MCP config loaded: %d servers, last reviewed %s",
        len(config.servers),
        config.last_reviewed,
    )

    return config


def get_tools(provider: ToolProvider) -> list[Tool]:
    """Create LangChain Tool objects from a ToolProvider.

    This is the main entry point for the orchestrator.
    Call this with any ToolProvider implementation and get back
    a list of Tools ready to bind to LangGraph agents.

    Args:
        provider: Any ToolProvider (LocalToolProvider, MCPToolProvider, etc.)

    Returns:
        List of LangChain Tool objects the agents can use.
    """
    return [
        Tool(
            name="filesystem_read",
            description=(
                "Read a file from the project workspace. "
                "Input: relative file path (e.g., 'src/main.py'). "
                "Output: file contents as text."
            ),
            func=provider.filesystem_read,
        ),
        Tool(
            name="filesystem_write",
            description=(
                "Write content to a file in the project workspace. "
                "Input: JSON string with 'path' and 'content' keys. "
                "Output: confirmation message."
            ),
            func=lambda input_str, p=provider: _parse_write_call(p, input_str),
        ),
        Tool(
            name="filesystem_list",
            description=(
                "List files and directories in the project workspace. "
                "Input: relative directory path (e.g., 'src/' or '.'). "
                "Output: formatted directory listing."
            ),
            func=provider.filesystem_list,
        ),
        Tool(
            name="github_create_pr",
            description=(
                "Create a GitHub pull request. "
                "Input: JSON string with 'title', 'body', 'head_branch', "
                "and optional 'base_branch' keys. "
                "Output: PR URL."
            ),
            func=lambda input_str, p=provider: _parse_create_pr_call(p, input_str),
        ),
        Tool(
            name="github_read_diff",
            description=(
                "Read the diff of a GitHub pull request. "
                "Input: PR number as a string (e.g., '42'). "
                "Output: diff text."
            ),
            func=lambda input_str, p=provider: p.github_read_diff(int(input_str.strip())),
        ),
    ]


def _parse_write_call(provider: ToolProvider, input_str: str) -> str:
    """Parse JSON input for filesystem_write and call the provider."""
    try:
        data = json.loads(input_str)
    except json.JSONDecodeError:
        return "Error: Input must be a JSON string with 'path' and 'content' keys."

    path = data.get("path")
    content = data.get("content")

    if not path or content is None:
        return "Error: JSON must include 'path' and 'content' keys."

    return provider.filesystem_write(path, content)


def _parse_create_pr_call(provider: ToolProvider, input_str: str) -> str:
    """Parse JSON input for github_create_pr and call the provider."""
    try:
        data = json.loads(input_str)
    except json.JSONDecodeError:
        return "Error: Input must be a JSON string with 'title', 'body', and 'head_branch' keys."

    title = data.get("title")
    body = data.get("body", "")
    head_branch = data.get("head_branch")
    base_branch = data.get("base_branch", "main")

    if not title or not head_branch:
        return "Error: JSON must include 'title' and 'head_branch' keys."

    return provider.github_create_pr(title, body, head_branch, base_branch)
