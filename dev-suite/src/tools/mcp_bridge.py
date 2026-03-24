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

    Dynamically generates Tools from the provider's list_tools() response.
    Each generated Tool wraps provider.call_tool() with JSON input parsing
    for multi-argument tools and string passthrough for single-argument tools.

    Args:
        provider: Any ToolProvider (LocalToolProvider, MCPToolProvider, etc.)

    Returns:
        List of LangChain Tool objects the agents can use.
    """
    tools = []
    for defn in provider.list_tools():
        properties = defn.parameters.get("properties", {})
        required = defn.parameters.get("required", [])

        # Determine if this is a single-arg tool (one required string param)
        # or a multi-arg tool (needs JSON parsing)
        single_arg_key = None
        if len(required) == 1 and len(properties) == 1:
            key = required[0]
            if properties[key].get("type") == "string":
                single_arg_key = key

        if single_arg_key:
            # Single string argument — pass the raw input string directly
            tool = Tool(
                name=defn.name,
                description=defn.description,
                func=_make_single_arg_handler(provider, defn.name, single_arg_key),
            )
        else:
            # Multi-argument — expect JSON input and parse it
            tool = Tool(
                name=defn.name,
                description=_build_json_description(defn),
                func=_make_multi_arg_handler(provider, defn.name),
            )

        tools.append(tool)

    return tools


def _make_single_arg_handler(provider: ToolProvider, tool_name: str, arg_key: str):
    """Create a handler for a single-string-argument tool."""
    def handler(input_str: str) -> str:
        return provider.call_tool(tool_name, {arg_key: input_str.strip()})
    return handler


def _make_multi_arg_handler(provider: ToolProvider, tool_name: str):
    """Create a handler that parses JSON input for a multi-argument tool."""
    def handler(input_str: str) -> str:
        try:
            arguments = json.loads(input_str)
        except json.JSONDecodeError:
            return f"Error: Input for '{tool_name}' must be a valid JSON string."

        if not isinstance(arguments, dict):
            return f"Error: Input for '{tool_name}' must be a JSON object."

        return provider.call_tool(tool_name, arguments)
    return handler


def _build_json_description(defn) -> str:
    """Append JSON input hint to a multi-arg tool's description."""
    required = defn.parameters.get("required", [])
    properties = defn.parameters.get("properties", {})
    optional = [k for k in properties if k not in required]

    parts = [defn.description, f"Input: JSON string with {', '.join(repr(k) for k in required)} keys."]
    if optional:
        parts.append(f"Optional: {', '.join(repr(k) for k in optional)}.")

    return " ".join(parts)
