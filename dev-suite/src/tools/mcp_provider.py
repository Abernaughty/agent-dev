"""MCPToolProvider — real MCP server subprocess spawning.

Implements the async ToolProvider ABC by spawning actual MCP server
subprocesses (Filesystem, GitHub) and communicating via JSON-RPC over
stdio using the Python MCP SDK.

Phase 2 replacement for LocalToolProvider (Phase 1).

Architecture decisions (from issue #13 brainstorm):
    ARCH-1: AsyncExitStack for session lifecycle management
    ARCH-2: Tool name collision detection at connection time
    SEC-1:  Minimal env dict per server (no os.environ.copy())
    SEC-2:  ${VAR} expansion for secret references in config
    DX-1:   Windows command resolution via shutil.which()
    DX-2:   Preflight checks with actionable error messages
"""

import json as json_module
import logging
import os
import platform
import re
import shutil
from contextlib import AsyncExitStack
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from .mcp_bridge import MCPConfig, MCPConfigError
from .provider import ToolDefinition, ToolNotFoundError, ToolProvider

logger = logging.getLogger(__name__)

# Env vars always passed to subprocesses (platform-dependent)
_WINDOWS_REQUIRED_ENV = ("SYSTEMROOT", "COMSPEC", "TEMP", "TMP")
_ALWAYS_PASSTHROUGH = ("PATH",)


def _resolve_command(command: str) -> str:
    """Resolve a command to its full path, handling Windows quirks.

    On Windows, npx is actually npx.cmd. shutil.which() handles this
    automatically on Windows, but we call it explicitly for clear errors.

    Args:
        command: Command name (e.g., "npx", "docker").

    Returns:
        Resolved absolute path to the executable.

    Raises:
        MCPConfigError: If the command cannot be found on PATH.
    """
    resolved = shutil.which(command)
    if resolved:
        return resolved

    # On Windows, also try explicit .cmd/.exe variants
    if platform.system() == "Windows":
        for ext in (".cmd", ".exe", ".bat"):
            resolved = shutil.which(command + ext)
            if resolved:
                return resolved

    raise MCPConfigError(
        f"Command '{command}' not found on PATH. "
        f"Ensure it is installed and available.\n"
        f"  - For npx: install Node.js (https://nodejs.org/)\n"
        f"  - For docker: install Docker Desktop (https://docker.com/)"
    )


def _expand_env_vars(value: str) -> str:
    r"""Expand ${VAR} references in a string from os.environ.

    Supports ${VAR} and ${VAR:-default} syntax, matching the pattern
    used by Claude Desktop and Pydantic AI MCP configs.

    Args:
        value: String potentially containing ${VAR} references.

    Returns:
        String with all references resolved.

    Raises:
        MCPConfigError: If a referenced variable is not set and no default.
    """
    def replacer(match: re.Match) -> str:
        var_name = match.group(1)
        default = match.group(2)  # Group 2 is the default after ":-"

        env_value = os.environ.get(var_name)
        if env_value is not None:
            return env_value
        if default is not None:
            return default
        raise MCPConfigError(
            f"Environment variable '{var_name}' is not set "
            f"(referenced in mcp-config.json). "
            f"Set it or use ${{{{var_name}}:-default}} syntax."
        )

    # Match ${VAR} or ${VAR:-default}
    return re.sub(r"\$\{([^}:]+)(?::-(.*?))?\}", replacer, value)


def _build_server_env(server_config: dict) -> dict[str, str] | None:
    """Build a minimal environment dict for an MCP server subprocess.

    SEC-1: Never pass os.environ.copy(). Only include:
    - PATH (required for subprocess resolution)
    - Windows-required vars (SYSTEMROOT, COMSPEC, TEMP, TMP)
    - Server-specific env vars from config (with ${VAR} expansion)

    Returns None if no env customization needed (lets MCP SDK decide).
    """
    env_config = server_config.get("env")

    # Always build explicit env to prevent leakage
    env: dict[str, str] = {}

    # Platform essentials
    for key in _ALWAYS_PASSTHROUGH:
        val = os.environ.get(key)
        if val:
            env[key] = val

    if platform.system() == "Windows":
        for key in _WINDOWS_REQUIRED_ENV:
            val = os.environ.get(key)
            if val:
                env[key] = val

    # Server-specific env with ${VAR} expansion (SEC-2)
    if env_config:
        for key, value in env_config.items():
            env[key] = _expand_env_vars(str(value))

    return env if env else None


class _ServerConnection:
    """Internal: tracks a single MCP server's session and tool names."""

    __slots__ = ("name", "session", "tool_names")

    def __init__(self, name: str, session: ClientSession):
        self.name = name
        self.session = session
        self.tool_names: set[str] = set()


class MCPToolProvider(ToolProvider):
    """Async ToolProvider that spawns real MCP server subprocesses.

    Implements the ToolProvider ABC by managing one or more MCP server
    processes, aggregating their tools, and routing call_tool() to the
    correct server session.

    Usage:
        config = load_mcp_config("mcp-config.json")
        async with MCPToolProvider(config) as provider:
            tools = await provider.list_tools()
            result = await provider.call_tool("read_file", {"path": "src/main.py"})

    Lifecycle (ARCH-1):
        - connect() spawns all configured servers, initializes sessions
        - list_tools() aggregates tools from all servers
        - call_tool() routes to the correct server by tool name
        - shutdown() closes all sessions and kills subprocesses
        - Supports async context manager (async with)
        - Lazy connect: if list_tools/call_tool called before connect(),
          auto-connects first
    """

    def __init__(
        self,
        config: MCPConfig,
        workspace_root: str | Path | None = None,
    ):
        """Initialize MCPToolProvider.

        Args:
            config: Parsed MCP config from mcp-config.json.
            workspace_root: Workspace directory for filesystem servers.
                Used to expand {workspace} in server args.
        """
        self._config = config
        self._workspace_root = (
            Path(workspace_root).resolve() if workspace_root else None
        )
        self._exit_stack: AsyncExitStack | None = None
        self._connections: dict[str, _ServerConnection] = {}
        self._tool_to_server: dict[str, str] = {}
        self._connected = False

    # -- Preflight validation (DX-2, used by create_provider fallback) --

    def preflight_check(self) -> None:
        """Validate config without spawning subprocesses.

        Checks that all configured commands are resolvable and that
        required env vars are set. Called by create_provider() so
        failures are caught at factory time, enabling graceful fallback
        to LocalToolProvider.

        Raises:
            MCPConfigError: If any command is missing or env var unresolvable.
        """
        for server_name, server_config in self._config.servers.items():
            command = server_config.get("command")
            if not command:
                continue  # Version audit entry only

            # DX-1: Check command exists
            _resolve_command(command)

            # SEC-2: Check env var expansion works
            _build_server_env(server_config)

    # -- Async context manager (ARCH-1) --

    async def __aenter__(self) -> "MCPToolProvider":
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.shutdown()

    # -- Lifecycle --

    async def connect(self) -> None:
        """Spawn all configured MCP servers and initialize sessions.

        Performs preflight checks (DX-2), resolves commands (DX-1),
        builds minimal envs (SEC-1), expands env vars (SEC-2),
        and detects tool name collisions (ARCH-2).

        On failure, cleans up any already-opened subprocesses via
        the AsyncExitStack before re-raising.
        """
        if self._connected:
            return

        self._exit_stack = AsyncExitStack()
        await self._exit_stack.__aenter__()

        try:
            await self._connect_all_servers()
        except BaseException:
            # Clean up any already-opened subprocesses on failure
            await self._exit_stack.aclose()
            self._exit_stack = None
            self._connections.clear()
            self._tool_to_server.clear()
            raise

        self._connected = True
        logger.info(
            "MCPToolProvider connected: %d servers, %d tools",
            len(self._connections),
            len(self._tool_to_server),
        )

    async def _connect_all_servers(self) -> None:
        """Connect to all configured servers. Raises on fatal errors."""
        failed_servers: list[tuple[str, str]] = []

        for server_name, server_config in self._config.servers.items():
            if not server_config.get("command"):
                logger.debug(
                    "Server '%s' has no command — skipping "
                    "(version audit entry only)",
                    server_name,
                )
                continue

            try:
                await self._connect_server(server_name, server_config)
            except MCPConfigError:
                raise  # Config errors are fatal
            except Exception as e:
                failed_servers.append((server_name, str(e)))
                logger.warning(
                    "Failed to connect to MCP server '%s': %s",
                    server_name,
                    e,
                )

        if failed_servers and not self._connections:
            # All servers failed — this is fatal
            details = "; ".join(
                f"{name}: {err}" for name, err in failed_servers
            )
            raise MCPConfigError(
                f"All MCP servers failed to connect: {details}"
            )

        # Detect tool name collisions (ARCH-2)
        self._check_tool_collisions()

    async def _connect_server(
        self, name: str, config: dict
    ) -> None:
        """Connect to a single MCP server subprocess."""
        command = config["command"]

        # DX-1: Resolve command (handles Windows .cmd)
        try:
            resolved_command = _resolve_command(command)
        except MCPConfigError as e:
            logger.warning(str(e))
            raise

        # Build args, expanding {workspace}
        raw_args = list(config.get("args", []))
        args = [
            arg.replace(
                "{workspace}",
                str(self._workspace_root) if self._workspace_root else ".",
            )
            for arg in raw_args
        ]

        # SEC-1 + SEC-2: Build minimal env with var expansion
        env = _build_server_env(config)

        logger.info(
            "Spawning MCP server '%s': %s %s",
            name,
            resolved_command,
            " ".join(args),
        )

        # Create StdioServerParameters
        server_params = StdioServerParameters(
            command=resolved_command,
            args=args,
            env=env,
        )

        # Enter the nested async context managers via exit stack
        read_stream, write_stream = await self._exit_stack.enter_async_context(
            stdio_client(server_params)
        )
        session = await self._exit_stack.enter_async_context(
            ClientSession(read_stream, write_stream)
        )
        await session.initialize()

        # Discover tools
        tools_response = await session.list_tools()
        conn = _ServerConnection(name, session)

        for tool in tools_response.tools:
            conn.tool_names.add(tool.name)
            self._tool_to_server[tool.name] = name

        self._connections[name] = conn
        logger.info(
            "Server '%s' connected: %d tools (%s)",
            name,
            len(conn.tool_names),
            ", ".join(sorted(conn.tool_names)),
        )

    def _check_tool_collisions(self) -> None:
        """Detect and raise on tool name collisions across servers.

        ARCH-2: Fail loudly rather than silently shadowing tools.
        """
        seen: dict[str, str] = {}
        collisions: list[str] = []

        for server_name, conn in self._connections.items():
            for tool_name in conn.tool_names:
                if tool_name in seen:
                    collisions.append(
                        f"Tool '{tool_name}' provided by both "
                        f"'{seen[tool_name]}' and '{server_name}'"
                    )
                else:
                    seen[tool_name] = server_name

        if collisions:
            raise MCPConfigError(
                "Tool name collisions detected across MCP servers:\n"
                + "\n".join(f"  - {c}" for c in collisions)
            )

    async def shutdown(self) -> None:
        """Close all MCP sessions and kill subprocesses."""
        if self._exit_stack:
            await self._exit_stack.aclose()
            self._exit_stack = None

        self._connections.clear()
        self._tool_to_server.clear()
        self._connected = False
        logger.info("MCPToolProvider shut down")

    # -- Lazy connect guard --

    async def _ensure_connected(self) -> None:
        """Connect if not already connected (lazy init)."""
        if not self._connected:
            await self.connect()

    # -- ToolProvider interface --

    async def list_tools(self) -> list[ToolDefinition]:
        """Return all tools from all connected MCP servers.

        Aggregates tools from every server into a flat list of
        ToolDefinition objects compatible with the existing bridge.
        """
        await self._ensure_connected()

        definitions: list[ToolDefinition] = []

        for conn in self._connections.values():
            tools_response = await conn.session.list_tools()

            for tool in tools_response.tools:
                definitions.append(
                    ToolDefinition(
                        name=tool.name,
                        description=tool.description or "",
                        parameters=(
                            tool.inputSchema
                            if tool.inputSchema
                            else {}
                        ),
                    )
                )

        return definitions

    async def call_tool(self, name: str, arguments: dict) -> str:
        """Route a tool call to the correct MCP server session.

        Looks up which server provides the named tool, then dispatches
        via the MCP SDK's call_tool(). Handles both traditional content
        blocks (text, images) and structured output (structuredContent)
        introduced in newer MCP spec versions.

        Args:
            name: Tool name as returned by list_tools().
            arguments: Dict of arguments matching the tool's schema.

        Returns:
            Tool result as a string.

        Raises:
            ToolNotFoundError: If no server provides this tool.
        """
        await self._ensure_connected()

        server_name = self._tool_to_server.get(name)
        if not server_name:
            available = ", ".join(sorted(self._tool_to_server.keys()))
            raise ToolNotFoundError(
                f"Unknown tool '{name}'. "
                f"Available MCP tools: {available}"
            )

        conn = self._connections[server_name]
        result = await conn.session.call_tool(name, arguments)

        # Extract text from MCP result content blocks
        parts: list[str] = []
        for content_block in result.content:
            if hasattr(content_block, "text"):
                parts.append(content_block.text)
            else:
                # Non-text content (images, etc.) — stringify
                parts.append(str(content_block))

        if parts:
            return "\n".join(parts)

        # Fall back to structuredContent if no text content blocks.
        # Newer MCP servers may return structured JSON data via this
        # field instead of (or alongside) traditional content blocks.
        structured = getattr(result, "structuredContent", None)
        if structured is not None:
            if isinstance(structured, dict):
                return json_module.dumps(structured)
            return str(structured)

        return ""
