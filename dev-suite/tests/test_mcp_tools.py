"""Tests for MCP integration - ToolProvider pattern.

Unit tests cover:
- ToolProvider ABC contract (async list_tools + async call_tool)
- ToolDefinition Pydantic model
- call_tool dispatch, validation, and error handling
- LocalToolProvider filesystem operations (private handlers)
- LocalToolProvider GitHub operations (mocked, private handlers)
- Path validation (directory traversal prevention)
- MCP config loading and version validation
- Dynamic LangChain tool wrapping via get_tools()

Integration tests cover:
- Real filesystem operations in a temp directory
- Full round-trip via call_tool interface

Async notes (issue #27):
- pytest asyncio_mode = "auto" in pyproject.toml auto-detects async tests
- No @pytest.mark.asyncio decorators needed
- Tests calling provider methods are async; pure logic tests stay sync
"""

import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.tools.mcp_bridge import (
    MCPConfig,
    MCPConfigError,
    get_tools,
    load_mcp_config,
)
from src.tools.provider import (
    LocalToolProvider,
    PathValidationError,
    ToolDefinition,
    ToolNotFoundError,
    ToolProvider,
    _validate_path,
)


# ============================================================
# ToolDefinition model
# ============================================================


class TestToolDefinition:
    """Verify ToolDefinition Pydantic model."""

    def test_basic_creation(self):
        defn = ToolDefinition(
            name="test_tool",
            description="A test tool",
            parameters={"type": "object", "properties": {"x": {"type": "string"}}},
        )
        assert defn.name == "test_tool"
        assert defn.description == "A test tool"
        assert "properties" in defn.parameters

    def test_default_empty_parameters(self):
        defn = ToolDefinition(name="simple", description="No params")
        assert defn.parameters == {}

    def test_serialization_round_trip(self):
        defn = ToolDefinition(
            name="roundtrip",
            description="Test",
            parameters={"type": "object", "required": ["a"]},
        )
        data = defn.model_dump()
        restored = ToolDefinition(**data)
        assert restored == defn


# ============================================================
# ToolProvider ABC contract
# ============================================================


class TestToolProviderABC:
    """Verify the ToolProvider ABC enforces the interface."""

    def test_cannot_instantiate_abc(self):
        with pytest.raises(TypeError):
            ToolProvider()

    def test_abc_defines_two_methods(self):
        abstract_methods = ToolProvider.__abstractmethods__
        expected = {"list_tools", "call_tool"}
        assert abstract_methods == expected

    def test_local_provider_is_valid_implementation(self, tmp_path):
        provider = LocalToolProvider(workspace_root=tmp_path)
        assert isinstance(provider, ToolProvider)


# ============================================================
# LocalToolProvider - list_tools
# ============================================================


class TestListTools:
    """Test dynamic tool discovery via list_tools()."""

    @pytest.fixture
    def provider(self, tmp_path):
        return LocalToolProvider(workspace_root=tmp_path)

    async def test_returns_five_tools(self, provider):
        tools = await provider.list_tools()
        assert len(tools) == 5

    async def test_returns_tool_definitions(self, provider):
        tools = await provider.list_tools()
        for tool in tools:
            assert isinstance(tool, ToolDefinition)

    async def test_tool_names(self, provider):
        tools = await provider.list_tools()
        names = {t.name for t in tools}
        expected = {
            "filesystem_read", "filesystem_write",
            "filesystem_list", "github_create_pr",
            "github_read_diff",
        }
        assert names == expected

    async def test_all_tools_have_descriptions(self, provider):
        for tool in await provider.list_tools():
            assert tool.description
            assert len(tool.description) > 10

    async def test_all_tools_have_parameter_schemas(self, provider):
        for tool in await provider.list_tools():
            assert "type" in tool.parameters
            assert tool.parameters["type"] == "object"

    async def test_all_tools_have_required_fields(self, provider):
        for tool in await provider.list_tools():
            assert "required" in tool.parameters


# ============================================================
# LocalToolProvider - call_tool dispatch
# ============================================================


class TestCallTool:
    """Test call_tool dispatch, validation, and error handling."""

    @pytest.fixture
    def workspace(self, tmp_path):
        (tmp_path / "test.txt").write_text("test content", encoding="utf-8")
        (tmp_path / "src").mkdir()
        return tmp_path

    @pytest.fixture
    def provider(self, workspace):
        return LocalToolProvider(workspace_root=workspace)

    async def test_dispatch_filesystem_read(self, provider):
        result = await provider.call_tool(
            "filesystem_read", {"path": "test.txt"}
        )
        assert result == "test content"

    async def test_dispatch_filesystem_write(self, provider, workspace):
        result = await provider.call_tool(
            "filesystem_write",
            {"path": "new.txt", "content": "hello"},
        )
        assert "Successfully wrote" in result
        assert (workspace / "new.txt").read_text() == "hello"

    async def test_dispatch_filesystem_list(self, provider):
        result = await provider.call_tool(
            "filesystem_list", {"path": "."}
        )
        assert "test.txt" in result

    async def test_unknown_tool_raises_error(self, provider):
        with pytest.raises(ToolNotFoundError, match="Unknown tool"):
            await provider.call_tool("nonexistent", {})

    async def test_unknown_tool_lists_available(self, provider):
        with pytest.raises(ToolNotFoundError, match="filesystem_read"):
            await provider.call_tool("bad_name", {})

    async def test_missing_required_argument(self, provider):
        with pytest.raises(ValueError, match="missing required"):
            await provider.call_tool("filesystem_read", {})

    async def test_missing_multiple_required_arguments(self, provider):
        with pytest.raises(ValueError, match="path.*content|content.*path"):
            await provider.call_tool("filesystem_write", {})

    async def test_extra_arguments_ignored(self, provider):
        result = await provider.call_tool(
            "filesystem_read",
            {"path": "test.txt", "extra_field": "ignored"},
        )
        assert result == "test content"

    async def test_dispatch_github_create_pr(self, tmp_path):
        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.json.return_value = {
            "number": 99,
            "html_url": "https://github.com/test/pr/99",
        }
        provider = LocalToolProvider(
            workspace_root=tmp_path, github_token="tok",
            github_owner="owner", github_repo="repo",
        )
        with patch("httpx.AsyncClient") as mc:
            client = AsyncMock()
            client.post.return_value = mock_response
            client.__aenter__ = AsyncMock(return_value=client)
            client.__aexit__ = AsyncMock(return_value=False)
            mc.return_value = client
            result = await provider.call_tool("github_create_pr", {
                "title": "Test PR", "body": "Desc",
                "head_branch": "feature/test",
            })
        assert "PR #99" in result

    async def test_dispatch_github_read_diff(self, tmp_path):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = "diff --git a/file.py b/file.py"
        provider = LocalToolProvider(
            workspace_root=tmp_path, github_token="tok",
            github_owner="owner", github_repo="repo",
        )
        with patch("httpx.AsyncClient") as mc:
            client = AsyncMock()
            client.get.return_value = mock_response
            client.__aenter__ = AsyncMock(return_value=client)
            client.__aexit__ = AsyncMock(return_value=False)
            mc.return_value = client
            result = await provider.call_tool(
                "github_read_diff", {"pr_number": 42}
            )
        assert "diff --git" in result


# ============================================================
# Path validation
# ============================================================


class TestPathValidation:
    """Verify path validation prevents directory traversal."""

    def test_valid_relative_path(self, tmp_path):
        (tmp_path / "file.txt").touch()
        result = _validate_path("file.txt", tmp_path)
        assert result == (tmp_path / "file.txt").resolve()

    def test_valid_nested_path(self, tmp_path):
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.py").touch()
        result = _validate_path("src/main.py", tmp_path)
        assert result == (tmp_path / "src" / "main.py").resolve()

    def test_traversal_with_dotdot_rejected(self, tmp_path):
        with pytest.raises(PathValidationError):
            _validate_path("../../../etc/passwd", tmp_path)

    def test_traversal_with_absolute_path_rejected(self, tmp_path):
        with pytest.raises(PathValidationError):
            _validate_path("/etc/passwd", tmp_path)

    def test_traversal_hidden_in_middle(self, tmp_path):
        with pytest.raises(PathValidationError):
            _validate_path("src/../../etc/passwd", tmp_path)

    def test_path_within_workspace_after_dotdot_allowed(self, tmp_path):
        (tmp_path / "src").mkdir()
        (tmp_path / "file.txt").touch()
        result = _validate_path("src/../file.txt", tmp_path)
        assert result == (tmp_path / "file.txt").resolve()


# ============================================================
# LocalToolProvider - filesystem operations (private handlers)
# ============================================================


class TestLocalToolProviderFilesystem:
    """Test filesystem operations with real temp directories."""

    @pytest.fixture
    def workspace(self, tmp_path):
        (tmp_path / "hello.txt").write_text("Hello, world!", encoding="utf-8")
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.py").write_text("print('hi')", encoding="utf-8")
        return tmp_path

    @pytest.fixture
    def provider(self, workspace):
        return LocalToolProvider(workspace_root=workspace)

    async def test_read_existing_file(self, provider):
        assert await provider._filesystem_read("hello.txt") == "Hello, world!"

    async def test_read_nested_file(self, provider):
        assert await provider._filesystem_read("src/main.py") == "print('hi')"

    async def test_read_nonexistent_file(self, provider):
        with pytest.raises(FileNotFoundError):
            await provider._filesystem_read("does_not_exist.txt")

    async def test_read_path_traversal_blocked(self, provider):
        with pytest.raises(PathValidationError):
            await provider._filesystem_read("../../etc/passwd")

    async def test_write_new_file(self, provider, workspace):
        result = await provider._filesystem_write("new_file.txt", "new content")
        assert "Successfully wrote" in result
        assert (workspace / "new_file.txt").read_text() == "new content"

    async def test_write_creates_directories(self, provider, workspace):
        result = await provider._filesystem_write(
            "deep/nested/file.txt", "deep content"
        )
        assert "Successfully wrote" in result
        assert (workspace / "deep" / "nested" / "file.txt").read_text() == "deep content"

    async def test_write_path_traversal_blocked(self, provider):
        with pytest.raises(PathValidationError):
            await provider._filesystem_write("../../etc/evil", "bad")

    async def test_list_directory(self, provider):
        result = await provider._filesystem_list(".")
        assert "[FILE] hello.txt" in result
        assert "[DIR]  src" in result

    async def test_list_subdirectory(self, provider):
        result = await provider._filesystem_list("src")
        assert "[FILE] src/main.py" in result

    async def test_list_nonexistent_directory(self, provider):
        with pytest.raises(NotADirectoryError):
            await provider._filesystem_list("nonexistent")

    async def test_list_path_traversal_blocked(self, provider):
        with pytest.raises(PathValidationError):
            await provider._filesystem_list("../../")

    def test_invalid_workspace_root(self):
        with pytest.raises(ValueError, match="does not exist"):
            LocalToolProvider(workspace_root="/nonexistent/path/xyz")


# ============================================================
# LocalToolProvider - GitHub operations (mocked)
# ============================================================


class TestLocalToolProviderGitHub:
    """Test GitHub operations with mocked HTTP calls."""

    @pytest.fixture
    def provider(self, tmp_path):
        return LocalToolProvider(
            workspace_root=tmp_path, github_token="test-token",
            github_owner="testowner", github_repo="testrepo",
        )

    async def test_create_pr_success(self, provider):
        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.json.return_value = {
            "number": 42,
            "html_url": "https://github.com/test/pr/42",
        }
        with patch("httpx.AsyncClient") as mc:
            client = AsyncMock()
            client.post.return_value = mock_response
            client.__aenter__ = AsyncMock(return_value=client)
            client.__aexit__ = AsyncMock(return_value=False)
            mc.return_value = client
            result = await provider._github_create_pr(
                title="Test PR", body="Description",
                head_branch="feature/test",
            )
        assert "PR #42" in result
        assert "https://github.com/test/pr/42" in result

    async def test_create_pr_api_error(self, provider):
        mock_response = MagicMock()
        mock_response.status_code = 422
        mock_response.text = "Validation Failed"
        with patch("httpx.AsyncClient") as mc:
            client = AsyncMock()
            client.post.return_value = mock_response
            client.__aenter__ = AsyncMock(return_value=client)
            client.__aexit__ = AsyncMock(return_value=False)
            mc.return_value = client
            with pytest.raises(RuntimeError, match="GitHub API error 422"):
                await provider._github_create_pr(
                    title="PR", body="body", head_branch="branch"
                )

    async def test_create_pr_no_token(self, tmp_path):
        provider = LocalToolProvider(
            workspace_root=tmp_path, github_token="",
        )
        with pytest.raises(ValueError, match="GITHUB_TOKEN"):
            await provider._github_create_pr(
                title="PR", body="body", head_branch="branch"
            )

    async def test_read_diff_success(self, provider):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = "diff --git a/file.py b/file.py\n+new line"
        with patch("httpx.AsyncClient") as mc:
            client = AsyncMock()
            client.get.return_value = mock_response
            client.__aenter__ = AsyncMock(return_value=client)
            client.__aexit__ = AsyncMock(return_value=False)
            mc.return_value = client
            result = await provider._github_read_diff(42)
        assert "diff --git" in result

    async def test_read_diff_api_error(self, provider):
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_response.text = "Not Found"
        with patch("httpx.AsyncClient") as mc:
            client = AsyncMock()
            client.get.return_value = mock_response
            client.__aenter__ = AsyncMock(return_value=client)
            client.__aexit__ = AsyncMock(return_value=False)
            mc.return_value = client
            with pytest.raises(RuntimeError, match="GitHub API error 404"):
                await provider._github_read_diff(999)


# ============================================================
# MCP config loading
# ============================================================


class TestMCPConfig:
    """Test mcp-config.json loading and validation."""

    @pytest.fixture
    def valid_config(self, tmp_path):
        config = {
            "$schema": "MCP server version pinning",
            "last_reviewed": "2026-03-24",
            "servers": {
                "filesystem": {
                    "package": "@anthropic/mcp-filesystem",
                    "version": "0.6.2",
                    "integrity": "sha256-abc123",
                    "purpose": "Read/write access to project code",
                },
                "github": {
                    "package": "@anthropic/mcp-github",
                    "version": "0.6.2",
                    "integrity": "sha256-def456",
                    "purpose": "Manage PRs, issues, diffs",
                },
            },
        }
        config_path = tmp_path / "mcp-config.json"
        config_path.write_text(json.dumps(config), encoding="utf-8")
        return config_path

    def test_load_valid_config(self, valid_config):
        config = MCPConfig(valid_config)
        assert len(config.servers) == 2
        assert config.last_reviewed == "2026-03-24"

    def test_get_server(self, valid_config):
        config = MCPConfig(valid_config)
        assert config.get_server("filesystem")["version"] == "0.6.2"

    def test_get_nonexistent_server(self, valid_config):
        config = MCPConfig(valid_config)
        with pytest.raises(KeyError, match="not found"):
            config.get_server("nonexistent")

    def test_validate_versions_clean(self, valid_config):
        assert MCPConfig(valid_config).validate_versions() == []

    def test_validate_versions_warns_on_todo_hash(self, tmp_path):
        config_data = {"servers": {"fs": {
            "version": "0.6.2", "integrity": "TODO: add sha256 hash",
        }}}
        p = tmp_path / "mcp-config.json"
        p.write_text(json.dumps(config_data), encoding="utf-8")
        warnings = MCPConfig(p).validate_versions()
        assert any("no integrity hash" in w for w in warnings)

    def test_validate_versions_warns_on_missing_version(self, tmp_path):
        config_data = {"servers": {"fs": {"package": "@anthropic/mcp-fs"}}}
        p = tmp_path / "mcp-config.json"
        p.write_text(json.dumps(config_data), encoding="utf-8")
        warnings = MCPConfig(p).validate_versions()
        assert any("no pinned version" in w for w in warnings)

    def test_missing_config_file(self, tmp_path):
        with pytest.raises(MCPConfigError, match="not found"):
            MCPConfig(tmp_path / "nonexistent.json")

    def test_invalid_json(self, tmp_path):
        p = tmp_path / "bad.json"
        p.write_text("not valid json{{{", encoding="utf-8")
        with pytest.raises(MCPConfigError, match="Invalid JSON"):
            MCPConfig(p)

    def test_missing_servers_key(self, tmp_path):
        p = tmp_path / "empty.json"
        p.write_text('{"something": "else"}', encoding="utf-8")
        with pytest.raises(MCPConfigError, match="missing 'servers'"):
            MCPConfig(p)

    def test_load_mcp_config_logs_warnings(self, tmp_path, caplog):
        config_data = {"last_reviewed": "2026-03-24", "servers": {
            "fs": {"version": "0.6.2", "integrity": "TODO: add hash"},
        }}
        p = tmp_path / "mcp-config.json"
        p.write_text(json.dumps(config_data), encoding="utf-8")
        import logging
        with caplog.at_level(logging.WARNING):
            config = load_mcp_config(p)
        assert isinstance(config, MCPConfig)


# ============================================================
# Dynamic LangChain tool wrapping via get_tools()
# ============================================================


class TestGetTools:
    """Test that get_tools() produces valid LangChain Tools."""

    @pytest.fixture
    def provider(self, tmp_path):
        (tmp_path / "test.txt").write_text("test content", encoding="utf-8")
        return LocalToolProvider(workspace_root=tmp_path)

    def test_returns_five_tools(self, provider):
        assert len(get_tools(provider)) == 5

    def test_tool_names_match_provider(self, provider):
        tools = get_tools(provider)
        tool_names = {t.name for t in tools}
        from src.tools.mcp_bridge import _run_async
        provider_names = {d.name for d in _run_async(provider.list_tools())}
        assert tool_names == provider_names

    def test_all_tools_have_descriptions(self, provider):
        for tool in get_tools(provider):
            assert tool.description
            assert len(tool.description) > 10

    def test_filesystem_read_tool_works(self, provider):
        tools = get_tools(provider)
        read_tool = next(t for t in tools if t.name == "filesystem_read")
        assert read_tool.invoke("test.txt") == "test content"

    def test_filesystem_write_tool_works(self, provider, tmp_path):
        tools = get_tools(provider)
        write_tool = next(t for t in tools if t.name == "filesystem_write")
        result = write_tool.invoke(json.dumps({"path": "out.txt", "content": "hello"}))
        assert "Successfully wrote" in result
        assert (tmp_path / "out.txt").read_text() == "hello"

    def test_filesystem_list_tool_works(self, provider):
        tools = get_tools(provider)
        list_tool = next(t for t in tools if t.name == "filesystem_list")
        assert "test.txt" in list_tool.invoke(".")

    def test_multi_arg_tool_invalid_json(self, provider):
        tools = get_tools(provider)
        write_tool = next(t for t in tools if t.name == "filesystem_write")
        assert "Error" in write_tool.invoke("not json")

    def test_works_with_any_provider(self):
        """get_tools() works with any async ToolProvider."""
        class MockProvider(ToolProvider):
            async def list_tools(self):
                return [ToolDefinition(
                    name="mock_tool", description="A mock tool",
                    parameters={
                        "type": "object",
                        "properties": {"x": {"type": "string"}},
                        "required": ["x"],
                    },
                )]
            async def call_tool(self, name, arguments):
                return f"mock result: {arguments.get('x')}"

        tools = get_tools(MockProvider())
        assert len(tools) == 1
        assert tools[0].invoke("hello") == "mock result: hello"

    def test_tools_have_coroutine(self, provider):
        """Each tool has both func (sync) and coroutine (async)."""
        for tool in get_tools(provider):
            assert tool.func is not None
            assert tool.coroutine is not None


# ============================================================
# Integration: full filesystem round-trip
# ============================================================


class TestFilesystemIntegration:
    """Integration tests with real temp directory operations."""

    async def test_full_round_trip(self):
        """Write, list, read back via call_tool."""
        with tempfile.TemporaryDirectory() as tmpdir:
            provider = LocalToolProvider(workspace_root=tmpdir)
            await provider.call_tool(
                "filesystem_write",
                {"path": "src/app.py", "content": "def main():\n    pass\n"},
            )
            list_result = await provider.call_tool(
                "filesystem_list", {"path": "src"},
            )
            assert "app.py" in list_result
            read_result = await provider.call_tool(
                "filesystem_read", {"path": "src/app.py"},
            )
            assert "def main():" in read_result

    async def test_overwrite_existing_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            provider = LocalToolProvider(workspace_root=tmpdir)
            await provider.call_tool(
                "filesystem_write",
                {"path": "file.txt", "content": "version 1"},
            )
            await provider.call_tool(
                "filesystem_write",
                {"path": "file.txt", "content": "version 2"},
            )
            content = await provider.call_tool(
                "filesystem_read", {"path": "file.txt"},
            )
            assert content == "version 2"

    async def test_empty_directory_listing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            provider = LocalToolProvider(workspace_root=tmpdir)
            (Path(tmpdir) / "empty_dir").mkdir()
            result = await provider.call_tool(
                "filesystem_list", {"path": "empty_dir"},
            )
            assert "empty" in result.lower()


# ============================================================
# Codex feedback fixes: scalar coercion + error recovery
# ============================================================


class TestCodexFixes:
    """Tests for Codex review feedback on PR #26."""

    @pytest.fixture
    def provider(self, tmp_path):
        return LocalToolProvider(
            workspace_root=tmp_path, github_token="tok",
            github_owner="owner", github_repo="repo",
        )

    def test_github_read_diff_plain_number_input(self, provider):
        """github_read_diff accepts plain '42' as input."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = "diff --git a/file.py b/file.py"
        with patch("httpx.AsyncClient") as mc:
            client = AsyncMock()
            client.get.return_value = mock_response
            client.__aenter__ = AsyncMock(return_value=client)
            client.__aexit__ = AsyncMock(return_value=False)
            mc.return_value = client
            tools = get_tools(provider)
            diff_tool = next(t for t in tools if t.name == "github_read_diff")
            result = diff_tool.invoke("42")
        assert "diff --git" in result

    def test_multi_arg_missing_keys_returns_error_string(self, provider):
        """Missing required keys return error string, not raise."""
        tools = get_tools(provider)
        write_tool = next(t for t in tools if t.name == "filesystem_write")
        result = write_tool.invoke(json.dumps({"path": "file.txt"}))
        assert "Error" in result

    def test_single_arg_integer_invalid_input(self, provider):
        """Non-numeric input for integer tool returns error string."""
        tools = get_tools(provider)
        diff_tool = next(t for t in tools if t.name == "github_read_diff")
        assert "Error" in diff_tool.invoke("not_a_number")
