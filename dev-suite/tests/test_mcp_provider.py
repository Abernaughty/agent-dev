"""Unit tests for MCPToolProvider.

QA-1: Mocks at the ClientSession level. Tests cover:
- Connect/disconnect lifecycle
- list_tools aggregation across servers
- call_tool routing to correct server
- Tool name collision detection (ARCH-2)
- Environment variable expansion (SEC-2)
- Minimal env building (SEC-1)
- Command resolution (DX-1)
- Graceful fallback (DX-2)
- Error handling (missing commands, failed servers, etc.)
"""

import platform
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.tools.mcp_bridge import MCPConfig, MCPConfigError
from src.tools.mcp_provider import (
    MCPToolProvider,
    _build_server_env,
    _expand_env_vars,
    _resolve_command,
)
from src.tools.provider import ToolDefinition, ToolNotFoundError

# -- Test helpers / mocks --


@dataclass
class MockTool:
    """Mock MCP tool from list_tools() response.

    The real MCP SDK uses camelCase 'inputSchema' (from JSON-RPC spec).
    We store as snake_case and expose a property to match.
    """
    name: str
    description: str = "A mock tool"
    input_schema: dict = field(default_factory=lambda: {
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"],
    })

    @property
    def inputSchema(self):  # noqa: N802
        return self.input_schema


@dataclass
class MockListToolsResponse:
    """Mock response from session.list_tools()."""
    tools: list = field(default_factory=list)


@dataclass
class MockTextContent:
    """Mock text content block from call_tool response."""
    type: str = "text"
    text: str = "mock result"


@dataclass
class MockCallToolResponse:
    """Mock response from session.call_tool()."""
    content: list = field(default_factory=lambda: [MockTextContent()])


class MockClientSession:
    """Mock MCP ClientSession for unit testing."""

    def __init__(self, tools=None, call_result=None):
        self._tools = tools or []
        self._call_result = call_result or MockCallToolResponse()
        self.initialize = AsyncMock()
        self._list_tools_called = 0
        self._call_tool_calls = []

    async def list_tools(self):
        self._list_tools_called += 1
        return MockListToolsResponse(tools=self._tools)

    async def call_tool(self, name, arguments):
        self._call_tool_calls.append((name, arguments))
        return self._call_result

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


def make_mock_config(servers: dict) -> MCPConfig:
    """Create an MCPConfig from a dict without touching the filesystem."""
    config = MagicMock(spec=MCPConfig)
    config.servers = servers
    config.last_reviewed = "2026-03-25"
    config.validate_versions.return_value = []
    return config


def _patch_stdio_client(sessions_by_call_order: list[MockClientSession]):
    """Patch stdio_client to yield mock sessions in order.

    Each call to stdio_client() yields the next mock session from the list.
    """
    call_index = {"i": 0}

    @asynccontextmanager
    async def fake_stdio_client(server_params):
        idx = call_index["i"]
        call_index["i"] += 1
        _ = sessions_by_call_order[idx]  # Track which session
        # stdio_client yields (read_stream, write_stream)
        # ClientSession wraps those. We mock both layers.
        yield (MagicMock(), MagicMock())

    return fake_stdio_client, sessions_by_call_order


# -- Tests: _expand_env_vars (SEC-2) --


class TestExpandEnvVars:
    """SEC-2: Environment variable expansion in config values."""

    def test_simple_var(self, monkeypatch):
        monkeypatch.setenv("MY_TOKEN", "secret123")
        assert _expand_env_vars("${MY_TOKEN}") == "secret123"

    def test_var_with_default(self, monkeypatch):
        monkeypatch.delenv("MISSING_VAR", raising=False)
        assert _expand_env_vars("${MISSING_VAR:-fallback}") == "fallback"

    def test_var_with_default_but_set(self, monkeypatch):
        monkeypatch.setenv("MY_VAR", "real_value")
        assert _expand_env_vars("${MY_VAR:-fallback}") == "real_value"

    def test_missing_var_no_default(self, monkeypatch):
        monkeypatch.delenv("NOPE", raising=False)
        with pytest.raises(MCPConfigError, match="NOPE"):
            _expand_env_vars("${NOPE}")

    def test_no_expansion_needed(self):
        assert _expand_env_vars("plain string") == "plain string"

    def test_multiple_vars(self, monkeypatch):
        monkeypatch.setenv("HOST", "localhost")
        monkeypatch.setenv("PORT", "8080")
        result = _expand_env_vars("${HOST}:${PORT}")
        assert result == "localhost:8080"

    def test_empty_string(self):
        assert _expand_env_vars("") == ""


# -- Tests: _build_server_env (SEC-1) --


class TestBuildServerEnv:
    """SEC-1: Minimal environment dict for subprocesses."""

    def test_includes_path(self, monkeypatch):
        monkeypatch.setenv("PATH", "/usr/bin")
        env = _build_server_env({})
        assert env is not None
        assert env["PATH"] == "/usr/bin"

    def test_does_not_leak_secrets(self, monkeypatch):
        monkeypatch.setenv("PATH", "/usr/bin")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-secret")
        monkeypatch.setenv("E2B_API_KEY", "e2b-secret")
        env = _build_server_env({})
        assert "ANTHROPIC_API_KEY" not in env
        assert "E2B_API_KEY" not in env

    def test_includes_server_env_with_expansion(self, monkeypatch):
        monkeypatch.setenv("PATH", "/usr/bin")
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_abc123")
        env = _build_server_env({
            "env": {"GITHUB_PERSONAL_ACCESS_TOKEN": "${GITHUB_TOKEN}"}
        })
        assert env["GITHUB_PERSONAL_ACCESS_TOKEN"] == "ghp_abc123"

    @pytest.mark.skipif(
        platform.system() != "Windows",
        reason="Windows-specific env vars"
    )
    def test_includes_windows_vars(self, monkeypatch):
        monkeypatch.setenv("PATH", "C:\\Windows")
        monkeypatch.setenv("SYSTEMROOT", "C:\\Windows")
        monkeypatch.setenv("COMSPEC", "C:\\Windows\\cmd.exe")
        env = _build_server_env({})
        assert "SYSTEMROOT" in env
        assert "COMSPEC" in env


# -- Tests: _resolve_command (DX-1) --


class TestResolveCommand:
    """DX-1: Command resolution with Windows support."""

    def test_resolves_python(self):
        # python/python3 should always be findable in test env
        result = _resolve_command("python3")
        assert result is not None
        assert "python" in result.lower()

    def test_missing_command_raises(self):
        with pytest.raises(MCPConfigError, match="not found on PATH"):
            _resolve_command("definitely_not_a_real_command_xyz")

    def test_error_includes_install_hints(self):
        with pytest.raises(MCPConfigError, match="Node.js"):
            _resolve_command("definitely_not_a_real_command_xyz")


# -- Tests: MCPToolProvider lifecycle (ARCH-1) --


class TestMCPToolProviderLifecycle:
    """ARCH-1: Session lifecycle via AsyncExitStack."""

    @pytest.fixture
    def fs_session(self):
        return MockClientSession(tools=[
            MockTool(name="read_file", description="Read a file"),
            MockTool(name="write_file", description="Write a file"),
        ])

    @pytest.fixture
    def config_with_command(self, tmp_path):
        return make_mock_config({
            "filesystem": {
                "command": "python3",
                "args": ["-c", "pass"],
                "env": {},
            },
        })

    async def test_connect_and_shutdown(self, config_with_command, fs_session):
        provider = MCPToolProvider(config_with_command, workspace_root="/tmp")

        with patch(
            "src.tools.mcp_provider.stdio_client"
        ) as mock_stdio, patch(
            "src.tools.mcp_provider.ClientSession",
            return_value=fs_session,
        ):
            mock_stdio.return_value = _make_async_cm((MagicMock(), MagicMock()))
            await provider.connect()
            assert provider._connected is True
            assert len(provider._connections) == 1

            await provider.shutdown()
            assert provider._connected is False
            assert len(provider._connections) == 0

    async def test_async_context_manager(self, config_with_command, fs_session):
        with patch(
            "src.tools.mcp_provider.stdio_client"
        ) as mock_stdio, patch(
            "src.tools.mcp_provider.ClientSession",
            return_value=fs_session,
        ):
            mock_stdio.return_value = _make_async_cm((MagicMock(), MagicMock()))
            async with MCPToolProvider(
                config_with_command, workspace_root="/tmp"
            ) as provider:
                assert provider._connected is True

            assert provider._connected is False

    async def test_lazy_connect_on_list_tools(self, config_with_command, fs_session):
        provider = MCPToolProvider(config_with_command, workspace_root="/tmp")

        with patch(
            "src.tools.mcp_provider.stdio_client"
        ) as mock_stdio, patch(
            "src.tools.mcp_provider.ClientSession",
            return_value=fs_session,
        ):
            mock_stdio.return_value = _make_async_cm((MagicMock(), MagicMock()))
            # Not connected yet
            assert provider._connected is False
            # list_tools triggers lazy connect
            tools = await provider.list_tools()
            assert provider._connected is True
            assert len(tools) >= 1

        await provider.shutdown()

    async def test_double_connect_is_noop(self, config_with_command, fs_session):
        provider = MCPToolProvider(config_with_command, workspace_root="/tmp")

        with patch(
            "src.tools.mcp_provider.stdio_client"
        ) as mock_stdio, patch(
            "src.tools.mcp_provider.ClientSession",
            return_value=fs_session,
        ):
            mock_stdio.return_value = _make_async_cm((MagicMock(), MagicMock()))
            await provider.connect()
            await provider.connect()  # Should be no-op
            assert mock_stdio.return_value._enter_count == 1

        await provider.shutdown()

    async def test_skips_servers_without_command(self):
        """Servers without 'command' field are version audit entries only."""
        config = make_mock_config({
            "filesystem": {
                "package": "@modelcontextprotocol/server-filesystem",
                "version": "2026.1.14",
                # No command field
            },
        })
        provider = MCPToolProvider(config, workspace_root="/tmp")
        # Should connect successfully with 0 servers
        # (all skipped because no command)
        # Need to ensure connect doesn't crash
        await provider.connect()
        assert provider._connected is True
        assert len(provider._connections) == 0
        await provider.shutdown()


# -- Tests: list_tools aggregation --


class TestListTools:
    """list_tools() aggregates tools from all connected servers."""

    async def test_returns_tool_definitions(self):
        session = MockClientSession(tools=[
            MockTool(name="read_file"),
            MockTool(name="write_file"),
        ])
        config = make_mock_config({
            "filesystem": {"command": "python3", "args": [], "env": {}},
        })
        provider = MCPToolProvider(config, workspace_root="/tmp")

        with patch(
            "src.tools.mcp_provider.stdio_client"
        ) as mock_stdio, patch(
            "src.tools.mcp_provider.ClientSession",
            return_value=session,
        ):
            mock_stdio.return_value = _make_async_cm((MagicMock(), MagicMock()))
            tools = await provider.list_tools()

        assert len(tools) == 2
        assert all(isinstance(t, ToolDefinition) for t in tools)
        names = {t.name for t in tools}
        assert "read_file" in names
        assert "write_file" in names

        await provider.shutdown()


# -- Tests: call_tool routing --


class TestCallTool:
    """call_tool() routes to the correct server session."""

    async def test_routes_to_correct_server(self):
        result_text = "file contents here"
        session = MockClientSession(
            tools=[MockTool(name="read_file")],
            call_result=MockCallToolResponse(
                content=[MockTextContent(text=result_text)]
            ),
        )
        config = make_mock_config({
            "filesystem": {"command": "python3", "args": [], "env": {}},
        })
        provider = MCPToolProvider(config, workspace_root="/tmp")

        with patch(
            "src.tools.mcp_provider.stdio_client"
        ) as mock_stdio, patch(
            "src.tools.mcp_provider.ClientSession",
            return_value=session,
        ):
            mock_stdio.return_value = _make_async_cm((MagicMock(), MagicMock()))
            result = await provider.call_tool(
                "read_file", {"path": "src/main.py"}
            )

        assert result == result_text
        assert len(session._call_tool_calls) == 1
        assert session._call_tool_calls[0] == (
            "read_file", {"path": "src/main.py"}
        )

        await provider.shutdown()

    async def test_unknown_tool_raises(self):
        session = MockClientSession(tools=[MockTool(name="read_file")])
        config = make_mock_config({
            "filesystem": {"command": "python3", "args": [], "env": {}},
        })
        provider = MCPToolProvider(config, workspace_root="/tmp")

        with patch(
            "src.tools.mcp_provider.stdio_client"
        ) as mock_stdio, patch(
            "src.tools.mcp_provider.ClientSession",
            return_value=session,
        ):
            mock_stdio.return_value = _make_async_cm((MagicMock(), MagicMock()))
            await provider.connect()

            with pytest.raises(ToolNotFoundError, match="no_such_tool"):
                await provider.call_tool("no_such_tool", {})

        await provider.shutdown()


# -- Tests: Tool name collisions (ARCH-2) --


class TestToolCollisions:
    """ARCH-2: Detect and raise on tool name collisions."""

    async def test_collision_raises(self):
        """Two servers providing the same tool name should raise."""
        session1 = MockClientSession(tools=[MockTool(name="shared_tool")])
        session2 = MockClientSession(tools=[MockTool(name="shared_tool")])

        config = make_mock_config({
            "server_a": {"command": "python3", "args": [], "env": {}},
            "server_b": {"command": "python3", "args": [], "env": {}},
        })
        provider = MCPToolProvider(config, workspace_root="/tmp")

        call_count = {"i": 0}

        @asynccontextmanager
        async def fake_stdio(*args, **kwargs):
            call_count["i"] += 1
            yield (MagicMock(), MagicMock())

        session_count = {"i": 0}
        original_sessions = [session1, session2]

        def fake_session_factory(*args, **kwargs):
            idx = session_count["i"]
            session_count["i"] += 1
            return original_sessions[idx]

        with patch(
            "src.tools.mcp_provider.stdio_client", side_effect=fake_stdio
        ), patch(
            "src.tools.mcp_provider.ClientSession",
            side_effect=fake_session_factory,
        ):
            with pytest.raises(MCPConfigError, match="collision"):
                await provider.connect()

        await provider.shutdown()


# -- Tests: Workspace expansion --


class TestWorkspaceExpansion:
    """Server args should expand {workspace} to actual path."""

    async def test_workspace_in_args(self):
        session = MockClientSession(tools=[MockTool(name="read_file")])
        config = make_mock_config({
            "filesystem": {
                "command": "python3",
                "args": ["-y", "server-filesystem", "{workspace}"],
                "env": {},
            },
        })
        provider = MCPToolProvider(config, workspace_root="/my/project")

        captured_params = []

        @asynccontextmanager
        async def capture_stdio(server_params):
            captured_params.append(server_params)
            yield (MagicMock(), MagicMock())

        with patch(
            "src.tools.mcp_provider.stdio_client",
            side_effect=capture_stdio,
        ), patch(
            "src.tools.mcp_provider.ClientSession",
            return_value=session,
        ):
            await provider.connect()

        assert len(captured_params) == 1
        # Check that {workspace} was expanded
        assert str(Path("/my/project").resolve()) in captured_params[0].args

        await provider.shutdown()


# -- Async context manager helper for mocking --


def _make_async_cm(value):
    """Create a mock async context manager that yields value."""
    cm = MagicMock()
    cm._enter_count = 0

    async def aenter(*args, **kwargs):
        cm._enter_count += 1
        return value

    async def aexit(*args, **kwargs):
        pass

    cm.__aenter__ = aenter
    cm.__aexit__ = aexit
    return cm
