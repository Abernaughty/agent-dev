"""MCP bridge - config loader, version validator, and tool factory.

Loads MCP server configuration from mcp-config.json, validates
version pinning, and creates LangChain Tool objects backed by
whichever ToolProvider is active.

In Phase 1, mcp-config.json is a version audit trail documenting
which MCP server interface our LocalToolProvider matches.
In Phase 2 (issue #13), it drives actual MCP server subprocess spawning.

Async bridge (issue #27):
    The ToolProvider interface is now async. LangChain's Tool supports
    both sync (func) and async (coroutine) invocation. get_tools() sets
    both paths so the orchestrator works whether called via invoke()
    (sync -> func) or ainvoke() (async -> coroutine).

Provider factory (issue #13):
    create_provider() reads TOOL_PROVIDER env var to instantiate the
    correct provider. Supports graceful fallback via TOOL_PROVIDER_FALLBACK.
"""

import asyncio
import json
import logging
import os
from pathlib import Path

from langchain_core.tools import Tool

from .provider import ToolProvider

logger = logging.getLogger(__name__)


# Read-only tool allowlist (issue #193).
#
# Used by agents that should be able to explore the workspace and
# GitHub without making changes — currently the Planner and the
# Architect's optional Phase 2. Keep this in sync with the tool
# registry in provider.py: any tool whose handler performs only
# reads (no filesystem writes, no GitHub mutations) belongs here.
READONLY_TOOLS: frozenset[str] = frozenset({
    "filesystem_read",
    "filesystem_list",
    "github_read_diff",
})


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
            raise MCPConfigError(
                f"Config file not found: {self.config_path}"
            )

        try:
            self._data = json.loads(
                self.config_path.read_text(encoding="utf-8")
            )
        except json.JSONDecodeError as e:
            raise MCPConfigError(
                f"Invalid JSON in {self.config_path}: {e}"
            ) from e

        if "servers" not in self._data:
            raise MCPConfigError("Config missing 'servers' key")

    @property
    def servers(self) -> dict:
        """Return the servers configuration dict."""
        return self._data.get("servers", {})

    @property
    def blocked_file_patterns(self) -> list[str] | None:
        """Return blocked file patterns from config, or None if absent."""
        return self._data.get("blocked_file_patterns")

    @property
    def last_reviewed(self) -> str:
        """Return the last review date."""
        return self._data.get("last_reviewed", "unknown")

    def get_server(self, name: str) -> dict:
        """Get config for a specific server."""
        if name not in self.servers:
            raise KeyError(
                f"Server '{name}' not found in MCP config"
            )
        return self.servers[name]

    def validate_versions(self) -> list[str]:
        """Check all servers have valid version pins.

        Returns a list of warning messages (empty if all good).
        """
        warnings = []
        for name, server in self.servers.items():
            if "version" not in server:
                warnings.append(
                    f"Server '{name}' has no pinned version"
                )
            if server.get("integrity", "").startswith("TODO"):
                warnings.append(
                    f"Server '{name}' has no integrity hash - "
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


# -- Provider factory (issue #13) --


def create_provider(
    config: MCPConfig,
    workspace_root: str | Path,
) -> ToolProvider:
    """Create the appropriate ToolProvider based on TOOL_PROVIDER env var.

    When TOOL_PROVIDER=mcp, runs preflight_check() to validate that
    all required commands (npx, docker) and env vars are available
    BEFORE returning the provider. This ensures TOOL_PROVIDER_FALLBACK
    actually triggers for the common failure modes (missing commands,
    unresolvable env vars) rather than deferring failures to the first
    list_tools()/call_tool() call.

    Args:
        config: Parsed MCP config.
        workspace_root: Project workspace directory.

    Returns:
        A ToolProvider instance (LocalToolProvider or MCPToolProvider).

    Environment variables:
        TOOL_PROVIDER: "local" (default) or "mcp"
        TOOL_PROVIDER_FALLBACK: "local" (default) or "error"
            Controls behavior when MCP provider fails to initialize.
    """
    provider_type = os.getenv("TOOL_PROVIDER", "local").lower()
    fallback = os.getenv("TOOL_PROVIDER_FALLBACK", "local").lower()

    blocked_patterns = config.blocked_file_patterns

    if provider_type == "mcp":
        try:
            from .mcp_provider import MCPToolProvider

            provider = MCPToolProvider(
                config=config,
                workspace_root=workspace_root,
                blocked_patterns=blocked_patterns,
            )
            # Validate commands and env vars exist before returning.
            # Without this, failures would only surface on first
            # list_tools()/call_tool(), bypassing the fallback logic.
            provider.preflight_check()
            return provider
        except Exception as e:
            if fallback == "error":
                raise MCPConfigError(
                    f"Failed to create MCPToolProvider: {e}"
                ) from e
            logger.warning(
                "MCPToolProvider preflight failed: %s. "
                "Falling back to LocalToolProvider.",
                e,
            )
            provider_type = "local"

    if provider_type == "local":
        from .provider import LocalToolProvider

        return LocalToolProvider(
            workspace_root=workspace_root,
            blocked_patterns=blocked_patterns,
        )

    raise MCPConfigError(
        f"Unknown TOOL_PROVIDER value: '{provider_type}'. "
        f"Expected 'local' or 'mcp'."
    )


# -- Async-to-sync bridge utilities --


def _run_async(coro):
    """Run an async coroutine from a sync context.

    Handles the case where an event loop may or may not already
    be running. This is the ONE place where sync->async bridging
    lives. LangChain's Tool.func (sync) uses this to call the
    async provider. When LangChain uses Tool.coroutine (async),
    this bridge is bypassed entirely.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        # Inside an existing event loop (e.g., Jupyter, some test
        # runners). Create a new thread to run the coroutine.
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(
            max_workers=1
        ) as pool:
            return pool.submit(asyncio.run, coro).result()
    else:
        return asyncio.run(coro)


# -- Tool factories --


def get_tools(
    provider: ToolProvider,
    tool_filter: set[str] | frozenset[str] | None = None,
) -> list[Tool]:
    """Create LangChain Tool objects from an async ToolProvider (sync).

    Dynamically generates Tools from the provider's list_tools()
    response. Each generated Tool has both a sync func (bridges to
    async via _run_async) and an async coroutine (calls provider
    directly with await).

    LangChain uses func when called from sync context
    (workflow.invoke()) and coroutine when called from async
    context (workflow.ainvoke()).

    Args:
        provider: Any async ToolProvider
        tool_filter: Optional allowlist of tool names. When provided,
            only tools whose name is in the set are returned. Typically
            set to READONLY_TOOLS for read-only agents (Planner,
            Architect Phase 2) — see issue #193.

    Returns:
        List of LangChain Tool objects the agents can use.
    """
    # list_tools() is async, so we need to bridge here
    definitions = _run_async(provider.list_tools())
    if tool_filter is not None:
        definitions = [d for d in definitions if d.name in tool_filter]
    return _build_langchain_tools(provider, definitions)


async def aget_tools(
    provider: ToolProvider,
    tool_filter: set[str] | frozenset[str] | None = None,
) -> list[Tool]:
    """Create LangChain Tool objects from an async ToolProvider (async).

    ARCH-3: Async variant of get_tools(). Avoids the _run_async bridge
    when called from async context (e.g., the orchestrator's main loop).

    Args:
        provider: Any async ToolProvider
        tool_filter: Optional allowlist of tool names. When provided,
            only tools whose name is in the set are returned. Typically
            set to READONLY_TOOLS for read-only agents (Planner,
            Architect Phase 2) — see issue #193.

    Returns:
        List of LangChain Tool objects the agents can use.
    """
    definitions = await provider.list_tools()
    if tool_filter is not None:
        definitions = [d for d in definitions if d.name in tool_filter]
    return _build_langchain_tools(provider, definitions)


def _build_langchain_tools(
    provider: ToolProvider,
    definitions: list,
) -> list[Tool]:
    """Build LangChain Tools from ToolDefinition list.

    Shared implementation for both get_tools() and aget_tools().
    """
    tools = []
    for defn in definitions:
        properties = defn.parameters.get("properties", {})
        required = defn.parameters.get("required", [])

        # Determine if this is a single-arg tool (one required
        # scalar param) or a multi-arg tool (needs JSON parsing).
        single_arg_key = None
        single_arg_type = None
        if len(required) == 1 and len(properties) == 1:
            key = required[0]
            ptype = properties[key].get("type", "")
            if ptype in ("string", "integer", "number"):
                single_arg_key = key
                single_arg_type = ptype

        if single_arg_key:
            sync_handler = _make_single_arg_handler(
                provider, defn.name,
                single_arg_key, single_arg_type,
            )
            async_handler = _make_single_arg_async_handler(
                provider, defn.name,
                single_arg_key, single_arg_type,
            )
            tool = Tool(
                name=defn.name,
                description=defn.description,
                func=sync_handler,
                coroutine=async_handler,
            )
        else:
            sync_handler = _make_multi_arg_handler(
                provider, defn.name,
            )
            async_handler = _make_multi_arg_async_handler(
                provider, defn.name,
            )
            tool = Tool(
                name=defn.name,
                description=_build_json_description(defn),
                func=sync_handler,
                coroutine=async_handler,
            )

        tools.append(tool)

    return tools


# -- Scalar type coercion --


_SCALAR_COERCIONS = {
    "string": lambda x: x.strip(),
    "integer": lambda x: int(x.strip()),
    "number": lambda x: float(x.strip()),
}


# -- Sync handler factories (bridge async provider via _run_async) --


def _make_single_arg_handler(
    provider: ToolProvider,
    tool_name: str,
    arg_key: str,
    arg_type: str = "string",
):
    """Create a sync handler for a single-scalar-argument tool."""
    coerce = _SCALAR_COERCIONS.get(
        arg_type, lambda x: x.strip()
    )

    def handler(input_str: str) -> str:
        try:
            value = coerce(input_str)
        except (ValueError, TypeError) as e:
            return (
                f"Error: Invalid input for '{tool_name}': {e}"
            )
        return _run_async(
            provider.call_tool(tool_name, {arg_key: value})
        )
    return handler


def _make_multi_arg_handler(
    provider: ToolProvider, tool_name: str,
):
    """Create a sync handler that parses JSON input."""
    def handler(input_str: str) -> str:
        try:
            arguments = json.loads(input_str)
        except json.JSONDecodeError:
            return (
                f"Error: Input for '{tool_name}' must be "
                f"a valid JSON string."
            )

        if not isinstance(arguments, dict):
            return (
                f"Error: Input for '{tool_name}' must be "
                f"a JSON object."
            )

        try:
            return _run_async(
                provider.call_tool(tool_name, arguments)
            )
        except (ValueError, Exception) as e:
            return f"Error: {e}"
    return handler


# -- Async handler factories (call provider directly with await) --


def _make_single_arg_async_handler(
    provider: ToolProvider,
    tool_name: str,
    arg_key: str,
    arg_type: str = "string",
):
    """Create an async handler for a single-scalar-argument tool."""
    coerce = _SCALAR_COERCIONS.get(
        arg_type, lambda x: x.strip()
    )

    async def handler(input_str: str) -> str:
        try:
            value = coerce(input_str)
        except (ValueError, TypeError) as e:
            return (
                f"Error: Invalid input for '{tool_name}': {e}"
            )
        return await provider.call_tool(
            tool_name, {arg_key: value}
        )
    return handler


def _make_multi_arg_async_handler(
    provider: ToolProvider, tool_name: str,
):
    """Create an async handler that parses JSON input."""
    async def handler(input_str: str) -> str:
        try:
            arguments = json.loads(input_str)
        except json.JSONDecodeError:
            return (
                f"Error: Input for '{tool_name}' must be "
                f"a valid JSON string."
            )

        if not isinstance(arguments, dict):
            return (
                f"Error: Input for '{tool_name}' must be "
                f"a JSON object."
            )

        try:
            return await provider.call_tool(
                tool_name, arguments
            )
        except (ValueError, Exception) as e:
            return f"Error: {e}"
    return handler


# -- Description builder --


def _build_json_description(defn) -> str:
    """Append JSON input hint to a multi-arg tool's description."""
    required = defn.parameters.get("required", [])
    properties = defn.parameters.get("properties", {})
    optional = [k for k in properties if k not in required]

    required_keys = ', '.join(repr(k) for k in required)
    parts = [
        defn.description,
        f"Input: JSON string with {required_keys} keys.",
    ]
    if optional:
        parts.append(
            f"Optional: {', '.join(repr(k) for k in optional)}."
        )

    return " ".join(parts)
