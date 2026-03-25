"""MCP and external tool integrations.

The ToolProvider pattern exposes tools dynamically via list_tools()
and call_tool(). The backend is swappable without changing agent code:

- Phase 1: LocalToolProvider (Python/pathlib/httpx)
- Phase 2: MCPToolProvider (real MCP subprocess spawning, issue #13)

Usage:
    from src.tools import LocalToolProvider, get_tools, load_mcp_config

    # Phase 1 (local):
    config = load_mcp_config("mcp-config.json")
    provider = LocalToolProvider(workspace_root="./my-project")
    tools = get_tools(provider)

    # Phase 2 (MCP):
    config = load_mcp_config("mcp-config.json")
    async with MCPToolProvider(config, workspace_root="./my-project") as provider:
        tools = await aget_tools(provider)
"""

from .mcp_bridge import (
    MCPConfig,
    MCPConfigError,
    aget_tools,
    create_provider,
    get_tools,
    load_mcp_config,
)
from .mcp_provider import MCPToolProvider
from .provider import (
    LocalToolProvider,
    PathValidationError,
    ToolDefinition,
    ToolNotFoundError,
    ToolProvider,
)

__all__ = [
    "ToolProvider",
    "ToolDefinition",
    "ToolNotFoundError",
    "LocalToolProvider",
    "MCPToolProvider",
    "PathValidationError",
    "MCPConfig",
    "MCPConfigError",
    "get_tools",
    "aget_tools",
    "create_provider",
    "load_mcp_config",
]
