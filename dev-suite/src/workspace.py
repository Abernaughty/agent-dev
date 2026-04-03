"""Workspace security model — allowed directories, protected workspaces, PIN auth.

Issue #105: Constrains agent file operations to explicitly approved directories.
Includes protected workspace authentication for sensitive codebases.

The WorkspaceManager is the single source of truth for:
  - Which directories agents are allowed to write to
  - Which workspaces require elevated auth (PIN) before write access
  - Persistence of the allowed-directories registry across restarts

Usage:
    from src.workspace import WorkspaceManager

    manager = WorkspaceManager.from_env()
    manager.is_allowed(Path("/some/path"))        # True/False
    manager.is_protected("agent-dev")             # True/False
    manager.verify_pin("1234")                    # True/False
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path

import bcrypt

logger = logging.getLogger(__name__)

# Default config file location (next to pyproject.toml in dev-suite/)
_DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "workspace-config.json"

# Patterns used to detect a workspace reference as "agent-dev"
# regardless of how it's specified (local path, repo name, GitHub URL).
_AGENT_DEV_PATTERNS = [
    re.compile(r"(?:^|[\\/])agent-dev(?:[\\/]?$)", re.IGNORECASE),
    re.compile(r"github\.com[/:].*?/agent-dev(?:\.git)?$", re.IGNORECASE),
]


class WorkspaceManager:
    """Manages the allowed-directories registry and protected workspace auth.

    Allowed directories are persisted to a JSON config file so they
    survive server restarts. The default WORKSPACE_ROOT is always
    included as an allowed directory.

    Protected workspaces require PIN verification before agents can
    write to them. The PIN hash is stored in the WORKSPACE_PROTECTED_PIN
    env var (bcrypt format).
    """

    def __init__(
        self,
        default_root: Path,
        protected_patterns: list[str] | None = None,
        pin_hash: str | None = None,
        config_path: Path | None = None,
    ):
        self._default_root = default_root.resolve()
        self._protected_patterns = protected_patterns or []
        self._pin_hash = pin_hash.encode("utf-8") if pin_hash else None
        self._config_path = config_path or _DEFAULT_CONFIG_PATH

        # Build the compiled regex list for protected workspace detection.
        # Includes built-in agent-dev patterns + user-configured patterns.
        self._protected_regexes: list[re.Pattern] = list(_AGENT_DEV_PATTERNS)
        for pattern in self._protected_patterns:
            # Each user pattern matches as a substring (case-insensitive)
            # against the resolved path string or workspace reference.
            escaped = re.escape(pattern)
            self._protected_regexes.append(
                re.compile(rf"(?:^|[\\/]){escaped}(?:[\\/]?$)", re.IGNORECASE)
            )

        # Load persisted directories; default_root is always included.
        self._allowed_dirs: list[Path] = [self._default_root]
        self._load_config()

    # -- Factory --

    @classmethod
    def from_env(cls, config_path: Path | None = None) -> WorkspaceManager:
        """Create a WorkspaceManager from environment variables.

        Env vars:
            WORKSPACE_ROOT: Default workspace directory (required).
            PROTECTED_WORKSPACES: Comma-separated list of protected
                workspace patterns (paths, repo names, URLs).
            WORKSPACE_PROTECTED_PIN: bcrypt hash of the admin PIN.
        """
        raw_root = os.getenv("WORKSPACE_ROOT", ".")
        default_root = Path(raw_root).resolve()

        raw_protected = os.getenv("PROTECTED_WORKSPACES", "")
        protected_patterns = [
            p.strip() for p in raw_protected.split(",") if p.strip()
        ]

        pin_hash = os.getenv("WORKSPACE_PROTECTED_PIN")

        return cls(
            default_root=default_root,
            protected_patterns=protected_patterns,
            pin_hash=pin_hash,
            config_path=config_path,
        )

    # -- Allowed Directories --

    @property
    def default_root(self) -> Path:
        """The default WORKSPACE_ROOT directory."""
        return self._default_root

    def list_directories(self) -> list[dict]:
        """Return all allowed directories with metadata.

        Returns a list of dicts with keys:
            path: str — resolved absolute path
            is_default: bool — True for WORKSPACE_ROOT
            is_protected: bool — True if in the protected list
        """
        seen: set[str] = set()
        result: list[dict] = []
        for d in self._allowed_dirs:
            key = str(d)
            if key in seen:
                continue
            seen.add(key)
            result.append({
                "path": key,
                "is_default": d == self._default_root,
                "is_protected": self.is_protected(key),
            })
        return result

    def add_directory(self, path: str | Path) -> bool:
        """Add a directory to the allowed list.

        Returns True if added, False if already present or invalid.
        The directory must exist on the filesystem.
        """
        resolved = Path(path).resolve()
        if not resolved.is_dir():
            logger.warning(
                "Cannot add non-existent directory: %s (resolved: %s)",
                path, resolved,
            )
            return False
        if resolved in self._allowed_dirs:
            return False
        self._allowed_dirs.append(resolved)
        self._save_config()
        logger.info("Added allowed directory: %s", resolved)
        return True

    def remove_directory(self, path: str | Path) -> bool:
        """Remove a directory from the allowed list.

        Returns True if removed, False if not found or if it's the default.
        The default WORKSPACE_ROOT cannot be removed.
        """
        resolved = Path(path).resolve()
        if resolved == self._default_root:
            logger.warning("Cannot remove default WORKSPACE_ROOT: %s", resolved)
            return False
        try:
            self._allowed_dirs.remove(resolved)
            self._save_config()
            logger.info("Removed allowed directory: %s", resolved)
            return True
        except ValueError:
            return False

    def is_allowed(self, path: str | Path) -> bool:
        """Check if a file path falls within any allowed directory.

        Resolves the path and checks if it's a descendant of any
        allowed directory. Symlinks are resolved to prevent bypasses.
        """
        try:
            resolved = Path(path).resolve()
        except (OSError, ValueError):
            return False
        for allowed in self._allowed_dirs:
            try:
                resolved.relative_to(allowed)
                return True
            except ValueError:
                continue
        return False

    def validate_file_paths(
        self, file_paths: list[str], workspace_root: Path | None = None,
    ) -> tuple[list[str], list[str]]:
        """Validate a list of file paths against the allowed directories.

        If workspace_root is provided, paths are resolved relative to it.
        Otherwise they're resolved relative to the process CWD.

        Returns:
            (allowed, rejected) — two lists of the original path strings.
        """
        base = workspace_root or Path.cwd()
        allowed: list[str] = []
        rejected: list[str] = []
        for fp in file_paths:
            full = (base / fp).resolve()
            if self.is_allowed(full):
                allowed.append(fp)
            else:
                rejected.append(fp)
        return allowed, rejected

    # -- Protected Workspaces --

    def is_protected(self, workspace_ref: str) -> bool:
        """Check if a workspace reference matches any protected pattern.

        The workspace_ref can be a local path, GitHub URL, or repo name.
        Detection is case-insensitive and resolves local paths.
        """
        # Check against the resolved path string
        try:
            resolved = str(Path(workspace_ref).resolve())
        except (OSError, ValueError):
            resolved = workspace_ref

        for regex in self._protected_regexes:
            if regex.search(resolved) or regex.search(workspace_ref):
                return True
        return False

    def verify_pin(self, pin: str) -> bool:
        """Verify a PIN against the stored bcrypt hash.

        Returns True if the PIN matches, False if it doesn't or
        if no PIN hash is configured.
        """
        if not self._pin_hash:
            logger.warning("PIN verification attempted but no hash configured")
            return False
        try:
            return bcrypt.checkpw(pin.encode("utf-8"), self._pin_hash)
        except Exception as e:
            logger.error("PIN verification error: %s", e)
            return False

    @property
    def has_pin_configured(self) -> bool:
        """Whether a protected workspace PIN hash is set."""
        return self._pin_hash is not None

    # -- Workspace Resolution --

    def resolve_workspace(self, workspace_ref: str) -> Path:
        """Resolve a workspace reference to an absolute Path.

        Handles local paths (relative and absolute). GitHub URL resolution
        is deferred to Phase 2+ (Planner agent + GitHub MCP).

        Raises ValueError if the resolved path is not in the allowed list.
        """
        resolved = Path(workspace_ref).resolve()
        if not self.is_allowed(resolved):
            raise ValueError(
                f"Workspace '{workspace_ref}' (resolved: {resolved}) "
                f"is not in the allowed directories list."
            )
        return resolved

    # -- Persistence --

    def _load_config(self) -> None:
        """Load persisted allowed directories from JSON config."""
        if not self._config_path.is_file():
            return
        try:
            with open(self._config_path, encoding="utf-8") as f:
                data = json.load(f)
            dirs = data.get("allowed_directories", [])
            for d in dirs:
                resolved = Path(d).resolve()
                if resolved not in self._allowed_dirs:
                    # Only add if the directory still exists on disk.
                    if resolved.is_dir():
                        self._allowed_dirs.append(resolved)
                    else:
                        logger.debug(
                            "Skipping persisted dir (no longer exists): %s",
                            resolved,
                        )
            logger.info(
                "Loaded workspace config: %d allowed directories",
                len(self._allowed_dirs),
            )
        except Exception as e:
            logger.warning("Failed to load workspace config: %s", e)

    def _save_config(self) -> None:
        """Persist allowed directories to JSON config."""
        # Don't persist the default root — it comes from env vars.
        extra_dirs = [
            str(d) for d in self._allowed_dirs if d != self._default_root
        ]
        data = {"allowed_directories": extra_dirs}
        try:
            with open(self._config_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            logger.debug("Saved workspace config: %d extra directories", len(extra_dirs))
        except Exception as e:
            logger.warning("Failed to save workspace config: %s", e)


# -- Utility: Generate a PIN hash for .env --


def hash_pin(pin: str) -> str:
    """Generate a bcrypt hash for a PIN. Use this to create the
    WORKSPACE_PROTECTED_PIN value for .env.

    Usage:
        python -c "from src.workspace import hash_pin; print(hash_pin('1234'))"
    """
    return bcrypt.hashpw(pin.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
