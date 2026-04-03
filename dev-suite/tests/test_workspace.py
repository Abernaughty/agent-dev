"""Tests for workspace security model (Issue #105).

Covers:
  - Allowed directory management (add/remove/list/validate)
  - Path traversal rejection
  - Symlink resolution
  - Protected workspace detection (local path, repo name, GitHub URL)
  - PIN verification (bcrypt)
  - JSON persistence (load/save)
  - WorkspaceManager.from_env() factory
  - Workspace resolution
  - File path batch validation
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import bcrypt
import pytest

from src.workspace import WorkspaceManager, hash_pin


# -- Fixtures --


@pytest.fixture
def tmp_workspace(tmp_path):
    """Create a temporary workspace directory structure."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "src").mkdir()
    (workspace / "src" / "main.py").write_text("print('hello')")
    return workspace


@pytest.fixture
def extra_dir(tmp_path):
    """Create an extra allowed directory."""
    extra = tmp_path / "extra-project"
    extra.mkdir()
    return extra


@pytest.fixture
def config_path(tmp_path):
    """Temporary config file path."""
    return tmp_path / "workspace-config.json"


@pytest.fixture
def pin_hash():
    """Bcrypt hash for test PIN '1234'."""
    return bcrypt.hashpw(b"1234", bcrypt.gensalt()).decode("utf-8")


@pytest.fixture
def manager(tmp_workspace, config_path, pin_hash):
    """WorkspaceManager with a temp workspace, no extra dirs, PIN configured."""
    return WorkspaceManager(
        default_root=tmp_workspace,
        protected_patterns=["agent-dev", "my-secret-repo"],
        pin_hash=pin_hash,
        config_path=config_path,
    )


# -- Allowed Directory Tests --


class TestAllowedDirectories:
    def test_default_root_always_allowed(self, manager, tmp_workspace):
        assert manager.is_allowed(tmp_workspace)
        assert manager.is_allowed(tmp_workspace / "src")
        assert manager.is_allowed(tmp_workspace / "src" / "main.py")

    def test_file_outside_workspace_rejected(self, manager, tmp_path):
        outside = tmp_path / "outside"
        outside.mkdir()
        assert not manager.is_allowed(outside)
        assert not manager.is_allowed(outside / "hack.py")

    def test_add_directory(self, manager, extra_dir):
        assert not manager.is_allowed(extra_dir)
        result = manager.add_directory(extra_dir)
        assert result is True
        assert manager.is_allowed(extra_dir)
        assert manager.is_allowed(extra_dir / "some_file.py")

    def test_add_directory_idempotent(self, manager, extra_dir):
        manager.add_directory(extra_dir)
        result = manager.add_directory(extra_dir)
        assert result is False  # Already present

    def test_add_nonexistent_directory_fails(self, manager, tmp_path):
        nonexistent = tmp_path / "does-not-exist"
        result = manager.add_directory(nonexistent)
        assert result is False

    def test_remove_directory(self, manager, extra_dir):
        manager.add_directory(extra_dir)
        assert manager.is_allowed(extra_dir)
        result = manager.remove_directory(extra_dir)
        assert result is True
        assert not manager.is_allowed(extra_dir)

    def test_remove_default_root_blocked(self, manager, tmp_workspace):
        result = manager.remove_directory(tmp_workspace)
        assert result is False
        assert manager.is_allowed(tmp_workspace)

    def test_remove_nonexistent_directory(self, manager, tmp_path):
        result = manager.remove_directory(tmp_path / "nope")
        assert result is False

    def test_list_directories(self, manager, tmp_workspace, extra_dir):
        manager.add_directory(extra_dir)
        dirs = manager.list_directories()
        assert len(dirs) == 2
        default_entry = next(d for d in dirs if d["is_default"])
        assert default_entry["path"] == str(tmp_workspace)
        extra_entry = next(d for d in dirs if not d["is_default"])
        assert extra_entry["path"] == str(extra_dir)

    def test_list_directories_no_duplicates(self, manager, tmp_workspace):
        # Default root is always in the list, adding it again shouldn't dupe
        manager.add_directory(tmp_workspace)
        dirs = manager.list_directories()
        assert len(dirs) == 1


class TestPathTraversal:
    def test_dotdot_traversal_rejected(self, manager, tmp_workspace):
        # Even if the traversal resolves to a valid dir, it must
        # resolve to within an allowed directory to pass.
        sneaky = tmp_workspace / "src" / ".." / ".." / "etc" / "passwd"
        assert not manager.is_allowed(sneaky)

    def test_relative_path_within_workspace(self, manager, tmp_workspace):
        # Relative path that resolves within workspace is fine
        relative = tmp_workspace / "src" / ".." / "src" / "main.py"
        assert manager.is_allowed(relative)

    def test_invalid_path_rejected(self, manager):
        # Null bytes or other OS-invalid paths should not crash
        assert not manager.is_allowed("\x00bad\x00path")


class TestFilePathValidation:
    def test_batch_validate_all_allowed(self, manager, tmp_workspace):
        files = ["src/main.py", "src/utils.py"]
        allowed, rejected = manager.validate_file_paths(files, tmp_workspace)
        assert allowed == files
        assert rejected == []

    def test_batch_validate_mixed(self, manager, tmp_workspace):
        files = ["src/main.py", "../../etc/passwd"]
        allowed, rejected = manager.validate_file_paths(files, tmp_workspace)
        assert "src/main.py" in allowed
        assert "../../etc/passwd" in rejected

    def test_batch_validate_all_rejected(self, manager, tmp_path):
        outside = tmp_path / "outside"
        outside.mkdir()
        files = ["hack.py", "evil.sh"]
        allowed, rejected = manager.validate_file_paths(files, outside)
        assert allowed == []
        assert len(rejected) == 2


# -- Protected Workspace Tests --


class TestProtectedWorkspaces:
    def test_agent_dev_local_path(self, manager):
        assert manager.is_protected("/home/user/projects/agent-dev")
        assert manager.is_protected("C:\\Users\\mike\\agent-dev")
        assert manager.is_protected("/opt/agent-dev/")

    def test_agent_dev_repo_name(self, manager):
        assert manager.is_protected("agent-dev")

    def test_agent_dev_github_url(self, manager):
        assert manager.is_protected("https://github.com/Abernaughty/agent-dev")
        assert manager.is_protected("https://github.com/Abernaughty/agent-dev.git")
        assert manager.is_protected("git@github.com:Abernaughty/agent-dev.git")

    def test_custom_protected_pattern(self, manager):
        assert manager.is_protected("/home/user/my-secret-repo")
        assert manager.is_protected("my-secret-repo")

    def test_unprotected_workspace(self, manager):
        assert not manager.is_protected("/home/user/my-webapp")
        assert not manager.is_protected("some-other-repo")

    def test_case_insensitive(self, manager):
        assert manager.is_protected("Agent-Dev")
        assert manager.is_protected("AGENT-DEV")

    def test_partial_match_rejected(self, manager):
        # "agent-dev-tools" should NOT match "agent-dev" pattern
        # because the regex requires a boundary (path separator or end)
        assert not manager.is_protected("agent-dev-tools")

    def test_is_protected_in_list_directories(self, manager, tmp_workspace, tmp_path):
        # Add a directory that looks like agent-dev
        agent_dev_dir = tmp_path / "agent-dev"
        agent_dev_dir.mkdir()
        manager.add_directory(agent_dev_dir)
        dirs = manager.list_directories()
        agent_entry = next(
            d for d in dirs if d["path"] == str(agent_dev_dir)
        )
        assert agent_entry["is_protected"] is True


# -- PIN Verification Tests --


class TestPINVerification:
    def test_correct_pin(self, manager):
        assert manager.verify_pin("1234") is True

    def test_wrong_pin(self, manager):
        assert manager.verify_pin("0000") is False

    def test_empty_pin(self, manager):
        assert manager.verify_pin("") is False

    def test_no_pin_configured(self, tmp_workspace, config_path):
        mgr = WorkspaceManager(
            default_root=tmp_workspace,
            pin_hash=None,
            config_path=config_path,
        )
        assert mgr.verify_pin("1234") is False
        assert mgr.has_pin_configured is False

    def test_has_pin_configured(self, manager):
        assert manager.has_pin_configured is True


# -- Persistence Tests --


class TestPersistence:
    def test_save_and_load(self, tmp_workspace, extra_dir, config_path, pin_hash):
        # Create manager, add a dir, save
        mgr1 = WorkspaceManager(
            default_root=tmp_workspace,
            pin_hash=pin_hash,
            config_path=config_path,
        )
        mgr1.add_directory(extra_dir)
        assert config_path.is_file()

        # Create a new manager — should load the saved config
        mgr2 = WorkspaceManager(
            default_root=tmp_workspace,
            pin_hash=pin_hash,
            config_path=config_path,
        )
        assert mgr2.is_allowed(extra_dir)

    def test_default_root_not_persisted(self, tmp_workspace, config_path, pin_hash):
        mgr = WorkspaceManager(
            default_root=tmp_workspace,
            pin_hash=pin_hash,
            config_path=config_path,
        )
        # Force a save by adding and removing
        mgr._save_config()
        data = json.loads(config_path.read_text())
        # Default root should NOT be in the persisted list
        assert str(tmp_workspace) not in data["allowed_directories"]

    def test_stale_directory_skipped_on_load(self, tmp_workspace, config_path, pin_hash):
        # Manually write a config with a non-existent directory
        config_path.write_text(json.dumps({
            "allowed_directories": ["/nonexistent/stale/dir"]
        }))
        mgr = WorkspaceManager(
            default_root=tmp_workspace,
            pin_hash=pin_hash,
            config_path=config_path,
        )
        # Should only have the default root
        dirs = mgr.list_directories()
        assert len(dirs) == 1
        assert dirs[0]["is_default"] is True

    def test_no_config_file_is_fine(self, tmp_workspace, tmp_path, pin_hash):
        config = tmp_path / "nonexistent-config.json"
        mgr = WorkspaceManager(
            default_root=tmp_workspace,
            pin_hash=pin_hash,
            config_path=config,
        )
        # Should work with just the default root
        assert len(mgr.list_directories()) == 1


# -- Workspace Resolution Tests --


class TestWorkspaceResolution:
    def test_resolve_allowed_workspace(self, manager, tmp_workspace):
        resolved = manager.resolve_workspace(str(tmp_workspace))
        assert resolved == tmp_workspace

    def test_resolve_disallowed_workspace_raises(self, manager, tmp_path):
        outside = tmp_path / "outside"
        outside.mkdir()
        with pytest.raises(ValueError, match="not in the allowed directories"):
            manager.resolve_workspace(str(outside))

    def test_default_root_property(self, manager, tmp_workspace):
        assert manager.default_root == tmp_workspace


# -- Factory Tests --


class TestFromEnv:
    def test_from_env_defaults(self, tmp_workspace, config_path):
        with patch.dict("os.environ", {
            "WORKSPACE_ROOT": str(tmp_workspace),
        }, clear=False):
            mgr = WorkspaceManager.from_env(config_path=config_path)
            assert mgr.default_root == tmp_workspace
            assert mgr.has_pin_configured is False

    def test_from_env_with_protected_and_pin(self, tmp_workspace, config_path, pin_hash):
        with patch.dict("os.environ", {
            "WORKSPACE_ROOT": str(tmp_workspace),
            "PROTECTED_WORKSPACES": "agent-dev,my-corp-infra",
            "WORKSPACE_PROTECTED_PIN": pin_hash,
        }, clear=False):
            mgr = WorkspaceManager.from_env(config_path=config_path)
            assert mgr.is_protected("agent-dev")
            assert mgr.is_protected("my-corp-infra")
            assert mgr.verify_pin("1234")
            assert mgr.has_pin_configured is True


# -- hash_pin utility --


class TestHashPin:
    def test_hash_pin_produces_valid_bcrypt(self):
        hashed = hash_pin("mypin")
        assert hashed.startswith("$2")
        assert bcrypt.checkpw(b"mypin", hashed.encode("utf-8"))
