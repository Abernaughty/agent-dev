"""Tests for Step 5: MCP integration — ToolProvider pattern.

Unit tests cover:
- ToolProvider ABC contract
- LocalToolProvider filesystem operations
- Path validation (directory traversal prevention)
- MCP config loading and version validation
- LangChain tool wrapping via get_tools()

Integration tests cover:
- Real filesystem operations in a temp directory
"""

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.tools.mcp_bridge import (
    MCPConfig,
    MCPConfigError,
    _parse_create_pr_call,
    _parse_write_call,
    get_tools,
    load_mcp_config,
)
from src.tools.provider import (
    LocalToolProvider,
    PathValidationError,
    ToolProvider,
    _validate_path,
)


# ============================================================
# ToolProvider ABC contract
# ============================================================


class TestToolProviderABC:
    """Verify the ToolProvider ABC enforces the interface."""

    def test_cannot_instantiate_abc(self):
        """ToolProvider itself cannot be instantiated."""
        with pytest.raises(TypeError):
            ToolProvider()

    def test_abc_defines_five_methods(self):
        """The ABC defines exactly the 5 expected abstract methods."""
        abstract_methods = ToolProvider.__abstractmethods__
        expected = {
            "filesystem_read",
            "filesystem_write",
            "filesystem_list",
            "github_create_pr",
            "github_read_diff",
        }
        assert abstract_methods == expected

    def test_local_provider_is_valid_implementation(self, tmp_path):
        """LocalToolProvider satisfies the ABC contract."""
        provider = LocalToolProvider(workspace_root=tmp_path)
        assert isinstance(provider, ToolProvider)


# ============================================================
# Path validation
# ============================================================


class TestPathValidation:
    """Verify path validation prevents directory traversal."""

    def test_valid_relative_path(self, tmp_path):
        """A simple relative path within workspace passes validation."""
        (tmp_path / "file.txt").touch()
        result = _validate_path("file.txt", tmp_path)
        assert result == (tmp_path / "file.txt").resolve()

    def test_valid_nested_path(self, tmp_path):
        """A nested path within workspace passes validation."""
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.py").touch()
        result = _validate_path("src/main.py", tmp_path)
        assert result == (tmp_path / "src" / "main.py").resolve()

    def test_traversal_with_dotdot_rejected(self, tmp_path):
        """A path with .. that escapes the workspace is rejected."""
        with pytest.raises(PathValidationError, match="outside workspace root"):
            _validate_path("../../../etc/passwd", tmp_path)

    def test_traversal_with_absolute_path_rejected(self, tmp_path):
        """An absolute path outside the workspace is rejected."""
        with pytest.raises(PathValidationError, match="outside workspace root"):
            _validate_path("/etc/passwd", tmp_path)

    def test_traversal_hidden_in_middle(self, tmp_path):
        """A path that goes up and back down is rejected if it leaves workspace."""
        with pytest.raises(PathValidationError, match="outside workspace root"):
            _validate_path("src/../../etc/passwd", tmp_path)

    def test_path_within_workspace_after_dotdot_allowed(self, tmp_path):
        """A path with .. that stays within workspace is allowed."""
        (tmp_path / "src").mkdir()
        (tmp_path / "file.txt").touch()
        result = _validate_path("src/../file.txt", tmp_path)
        assert result == (tmp_path / "file.txt").resolve()


# ============================================================
# LocalToolProvider — filesystem operations
# ============================================================


class TestLocalToolProviderFilesystem:
    """Test filesystem operations with real temp directories."""

    @pytest.fixture
    def workspace(self, tmp_path):
        """Create a workspace with sample files."""
        (tmp_path / "hello.txt").write_text("Hello, world!", encoding="utf-8")
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.py").write_text("print('hi')", encoding="utf-8")
        return tmp_path

    @pytest.fixture
    def provider(self, workspace):
        return LocalToolProvider(workspace_root=workspace)

    def test_read_existing_file(self, provider):
        result = provider.filesystem_read("hello.txt")
        assert result == "Hello, world!"

    def test_read_nested_file(self, provider):
        result = provider.filesystem_read("src/main.py")
        assert result == "print('hi')"

    def test_read_nonexistent_file(self, provider):
        with pytest.raises(FileNotFoundError):
            provider.filesystem_read("does_not_exist.txt")

    def test_read_path_traversal_blocked(self, provider):
        with pytest.raises(PathValidationError):
            provider.filesystem_read("../../etc/passwd")

    def test_write_new_file(self, provider, workspace):
        result = provider.filesystem_write("new_file.txt", "new content")
        assert "Successfully wrote" in result
        assert (workspace / "new_file.txt").read_text() == "new content"

    def test_write_creates_directories(self, provider, workspace):
        result = provider.filesystem_write("deep/nested/file.txt", "deep content")
        assert "Successfully wrote" in result
        assert (workspace / "deep" / "nested" / "file.txt").read_text() == "deep content"

    def test_write_path_traversal_blocked(self, provider):
        with pytest.raises(PathValidationError):
            provider.filesystem_write("../../etc/evil", "bad")

    def test_list_directory(self, provider):
        result = provider.filesystem_list(".")
        assert "[FILE] hello.txt" in result
        assert "[DIR]  src" in result

    def test_list_subdirectory(self, provider):
        result = provider.filesystem_list("src")
        assert "[FILE] src/main.py" in result

    def test_list_nonexistent_directory(self, provider):
        with pytest.raises(NotADirectoryError):
            provider.filesystem_list("nonexistent")

    def test_list_path_traversal_blocked(self, provider):
        with pytest.raises(PathValidationError):
            provider.filesystem_list("../../")

    def test_invalid_workspace_root(self):
        with pytest.raises(ValueError, match="does not exist"):
            LocalToolProvider(workspace_root="/nonexistent/path/xyz")


# ============================================================
# LocalToolProvider — GitHub operations (mocked)
# ============================================================


class TestLocalToolProviderGitHub:
    """Test GitHub operations with mocked HTTP calls."""

    @pytest.fixture
    def provider(self, tmp_path):
        return LocalToolProvider(
            workspace_root=tmp_path,
            github_token="test-token",
            github_owner="testowner",
            github_repo="testrepo",
        )

    @patch("src.tools.provider.httpx.post")
    def test_create_pr_success(self, mock_post, provider):
        mock_post.return_value = MagicMock(
            status_code=201,
            json=lambda: {"number": 42, "html_url": "https://github.com/test/pr/42"},
        )

        result = provider.github_create_pr(
            title="Test PR",
            body="Description",
            head_branch="feature/test",
        )

        assert "PR #42" in result
        assert "https://github.com/test/pr/42" in result
        mock_post.assert_called_once()

    @patch("src.tools.provider.httpx.post")
    def test_create_pr_api_error(self, mock_post, provider):
        mock_post.return_value = MagicMock(
            status_code=422,
            text="Validation Failed",
        )

        with pytest.raises(RuntimeError, match="GitHub API error 422"):
            provider.github_create_pr("PR", "body", "branch")

    def test_create_pr_no_token(self, tmp_path):
        provider = LocalToolProvider(
            workspace_root=tmp_path,
            github_token="",
        )
        with pytest.raises(ValueError, match="GITHUB_TOKEN"):
            provider.github_create_pr("PR", "body", "branch")

    @patch("src.tools.provider.httpx.get")
    def test_read_diff_success(self, mock_get, provider):
        mock_get.return_value = MagicMock(
            status_code=200,
            text="diff --git a/file.py b/file.py\n+new line",
        )

        result = provider.github_read_diff(42)
        assert "diff --git" in result

    @patch("src.tools.provider.httpx.get")
    def test_read_diff_api_error(self, mock_get, provider):
        mock_get.return_value = MagicMock(
            status_code=404,
            text="Not Found",
        )

        with pytest.raises(RuntimeError, match="GitHub API error 404"):
            provider.github_read_diff(999)


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
        fs = config.get_server("filesystem")
        assert fs["version"] == "0.6.2"

    def test_get_nonexistent_server(self, valid_config):
        config = MCPConfig(valid_config)
        with pytest.raises(KeyError, match="not found"):
            config.get_server("nonexistent")

    def test_validate_versions_clean(self, valid_config):
        config = MCPConfig(valid_config)
        warnings = config.validate_versions()
        assert warnings == []

    def test_validate_versions_warns_on_todo_hash(self, tmp_path):
        config_data = {
            "servers": {
                "filesystem": {
                    "version": "0.6.2",
                    "integrity": "TODO: add sha256 hash",
                },
            },
        }
        config_path = tmp_path / "mcp-config.json"
        config_path.write_text(json.dumps(config_data), encoding="utf-8")

        config = MCPConfig(config_path)
        warnings = config.validate_versions()
        assert any("no integrity hash" in w for w in warnings)

    def test_validate_versions_warns_on_missing_version(self, tmp_path):
        config_data = {
            "servers": {
                "filesystem": {"package": "@anthropic/mcp-filesystem"},
            },
        }
        config_path = tmp_path / "mcp-config.json"
        config_path.write_text(json.dumps(config_data), encoding="utf-8")

        config = MCPConfig(config_path)
        warnings = config.validate_versions()
        assert any("no pinned version" in w for w in warnings)

    def test_missing_config_file(self, tmp_path):
        with pytest.raises(MCPConfigError, match="not found"):
            MCPConfig(tmp_path / "nonexistent.json")

    def test_invalid_json(self, tmp_path):
        config_path = tmp_path / "bad.json"
        config_path.write_text("not valid json{{{", encoding="utf-8")
        with pytest.raises(MCPConfigError, match="Invalid JSON"):
            MCPConfig(config_path)

    def test_missing_servers_key(self, tmp_path):
        config_path = tmp_path / "empty.json"
        config_path.write_text('{"something": "else"}', encoding="utf-8")
        with pytest.raises(MCPConfigError, match="missing 'servers'"):
            MCPConfig(config_path)

    def test_load_mcp_config_logs_warnings(self, tmp_path, caplog):
        config_data = {
            "last_reviewed": "2026-03-24",
            "servers": {
                "filesystem": {
                    "version": "0.6.2",
                    "integrity": "TODO: add hash",
                },
            },
        }
        config_path = tmp_path / "mcp-config.json"
        config_path.write_text(json.dumps(config_data), encoding="utf-8")

        import logging

        with caplog.at_level(logging.WARNING):
            config = load_mcp_config(config_path)

        assert isinstance(config, MCPConfig)


# ============================================================
# LangChain tool wrapping via get_tools()
# ============================================================


class TestGetTools:
    """Test that get_tools() produces valid LangChain Tools."""

    @pytest.fixture
    def provider(self, tmp_path):
        (tmp_path / "test.txt").write_text("test content", encoding="utf-8")
        return LocalToolProvider(workspace_root=tmp_path)

    def test_returns_five_tools(self, provider):
        tools = get_tools(provider)
        assert len(tools) == 5

    def test_tool_names(self, provider):
        tools = get_tools(provider)
        names = {t.name for t in tools}
        expected = {
            "filesystem_read",
            "filesystem_write",
            "filesystem_list",
            "github_create_pr",
            "github_read_diff",
        }
        assert names == expected

    def test_all_tools_have_descriptions(self, provider):
        tools = get_tools(provider)
        for tool in tools:
            assert tool.description, f"Tool '{tool.name}' has no description"
            assert len(tool.description) > 10, f"Tool '{tool.name}' description too short"

    def test_filesystem_read_tool_works(self, provider):
        tools = get_tools(provider)
        read_tool = next(t for t in tools if t.name == "filesystem_read")
        result = read_tool.invoke("test.txt")
        assert result == "test content"

    def test_filesystem_write_tool_works(self, provider, tmp_path):
        tools = get_tools(provider)
        write_tool = next(t for t in tools if t.name == "filesystem_write")
        result = write_tool.invoke(json.dumps({"path": "out.txt", "content": "hello"}))
        assert "Successfully wrote" in result
        assert (tmp_path / "out.txt").read_text() == "hello"

    def test_filesystem_list_tool_works(self, provider):
        tools = get_tools(provider)
        list_tool = next(t for t in tools if t.name == "filesystem_list")
        result = list_tool.invoke(".")
        assert "test.txt" in result


# ============================================================
# JSON input parsing helpers
# ============================================================


class TestInputParsers:
    """Test the JSON parsing helpers for write and create_pr tools."""

    @pytest.fixture
    def provider(self, tmp_path):
        return LocalToolProvider(workspace_root=tmp_path)

    def test_parse_write_valid(self, provider):
        result = _parse_write_call(
            provider, json.dumps({"path": "file.txt", "content": "hi"})
        )
        assert "Successfully wrote" in result

    def test_parse_write_invalid_json(self, provider):
        result = _parse_write_call(provider, "not json")
        assert "Error" in result

    def test_parse_write_missing_keys(self, provider):
        result = _parse_write_call(provider, json.dumps({"path": "file.txt"}))
        assert "Error" in result

    @patch("src.tools.provider.httpx.post")
    def test_parse_create_pr_valid(self, mock_post, tmp_path):
        mock_post.return_value = MagicMock(
            status_code=201,
            json=lambda: {"number": 1, "html_url": "https://url"},
        )
        provider = LocalToolProvider(
            workspace_root=tmp_path,
            github_token="tok",
            github_owner="owner",
            github_repo="repo",
        )
        result = _parse_create_pr_call(
            provider,
            json.dumps({"title": "PR", "body": "desc", "head_branch": "feat"}),
        )
        assert "PR #1" in result

    def test_parse_create_pr_invalid_json(self, provider):
        result = _parse_create_pr_call(provider, "not json")
        assert "Error" in result

    def test_parse_create_pr_missing_keys(self, provider):
        result = _parse_create_pr_call(provider, json.dumps({"title": "only"}))
        assert "Error" in result


# ============================================================
# Integration: full filesystem round-trip
# ============================================================


class TestFilesystemIntegration:
    """Integration tests with real temp directory operations."""

    def test_full_round_trip(self):
        """Write a file, list the directory, read it back."""
        with tempfile.TemporaryDirectory() as tmpdir:
            provider = LocalToolProvider(workspace_root=tmpdir)

            # Write
            write_result = provider.filesystem_write(
                "project/src/app.py", "def main():\n    pass\n"
            )
            assert "Successfully wrote" in write_result

            # List
            list_result = provider.filesystem_list("project/src")
            assert "app.py" in list_result

            # Read
            read_result = provider.filesystem_read("project/src/app.py")
            assert "def main():" in read_result

    def test_overwrite_existing_file(self):
        """Writing to an existing file overwrites it."""
        with tempfile.TemporaryDirectory() as tmpdir:
            provider = LocalToolProvider(workspace_root=tmpdir)

            provider.filesystem_write("file.txt", "version 1")
            provider.filesystem_write("file.txt", "version 2")

            content = provider.filesystem_read("file.txt")
            assert content == "version 2"

    def test_empty_directory_listing(self):
        """Listing an empty directory gives a clear message."""
        with tempfile.TemporaryDirectory() as tmpdir:
            provider = LocalToolProvider(workspace_root=tmpdir)
            (Path(tmpdir) / "empty_dir").mkdir()

            result = provider.filesystem_list("empty_dir")
            assert "empty" in result.lower()
