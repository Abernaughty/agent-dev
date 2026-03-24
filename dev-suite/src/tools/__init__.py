"""MCP and external tool integrations.

The ToolProvider pattern exposes tools dynamically via list_tools()
and call_tool(). The backend is swappable without changing agent code:

- Phase 1: LocalToolProvider (Python/pathlib/httpx)
- Phase 2: MCPToolProvider (real MCP subprocess spawning, issue #13)

Usage:
    from src.tools import LocalToolProvider, get_tools, load_mcp_config

    config = load_mcp_config("mcp-config.json")
    provider = LocalToolProvider(workspace_root="./my-project")
    tools = get_tools(provider)  # auto-generates LangChain Tools from list_tools()
"""

from .mcp_bridge import MCPConfig, MCPConfigError, get_tools, load_mcp_config
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
    "PathValidationError",
    "MCPConfig",
    "MCPConfigError",
    "get_tools",
    "load_mcp_config",
]
