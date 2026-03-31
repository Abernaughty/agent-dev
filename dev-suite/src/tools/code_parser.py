"""Code parser utility for extracting files from Dev agent output.

The Dev agent produces code as a single text string with markers like:
    # --- FILE: path/to/file.py ---

This module parses that output into individual (filepath, content) pairs
that can be written to the workspace and loaded into E2B sandboxes.

Pure functions, no side effects, fully testable in isolation.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

# Matches: # --- FILE: path/to/file.ext ---
# Allows optional trailing whitespace and varying dash counts (3+)
_FILE_MARKER_RE = re.compile(
    r"^#\s*-{3,}\s*FILE:\s*(.+?)\s*-{3,}\s*$",
    re.MULTILINE,
)


@dataclass(frozen=True)
class ParsedFile:
    """A single file extracted from generated code output.

    Attributes:
        path: Relative file path (forward-slash normalized).
        content: File content as a string.
    """

    path: str
    content: str


class CodeParserError(Exception):
    """Raised when code parsing encounters an unrecoverable issue."""


def _normalize_path(raw_path: str) -> str:
    """Normalize a file path to forward slashes, strip leading ./ and whitespace.

    Raises CodeParserError for dangerous paths (traversal, absolute).
    """
    cleaned = raw_path.strip()

    if not cleaned:
        raise CodeParserError("Empty file path in marker")

    # Normalize to forward slashes
    cleaned = cleaned.replace("\\", "/")

    # Strip leading ./
    while cleaned.startswith("./"):
        cleaned = cleaned[2:]

    # Reject absolute paths
    if cleaned.startswith("/"):
        raise CodeParserError(
            f"Absolute path not allowed: '{raw_path}'"
        )

    # Reject path traversal
    parts = PurePosixPath(cleaned).parts
    if ".." in parts:
        raise CodeParserError(
            f"Path traversal not allowed: '{raw_path}'"
        )

    # Reject empty after normalization
    if not cleaned or cleaned == ".":
        raise CodeParserError(
            f"Path resolves to empty after normalization: '{raw_path}'"
        )

    return cleaned


def parse_generated_code(
    generated_code: str,
    *,
    default_filename: str = "output.py",
) -> list[ParsedFile]:
    """Parse Dev agent output into individual files.

    Extracts files delimited by ``# --- FILE: path/to/file ---`` markers.
    If no markers are found, the entire output is treated as a single file
    with the given default_filename.

    Args:
        generated_code: Raw text output from the Dev agent.
        default_filename: Filename to use when no markers are present.

    Returns:
        List of ParsedFile objects. Order matches appearance in input.
        Duplicate paths: last occurrence wins (later content replaces earlier).

    Raises:
        CodeParserError: On dangerous paths (traversal, absolute).
    """
    if not generated_code or not generated_code.strip():
        return []

    # Find all markers and their positions
    markers = list(_FILE_MARKER_RE.finditer(generated_code))

    if not markers:
        # No markers -- treat entire output as single file
        content = generated_code.strip()
        if not content:
            return []
        return [ParsedFile(path=default_filename, content=content)]

    # Extract files between markers
    seen: dict[str, ParsedFile] = {}

    for i, match in enumerate(markers):
        raw_path = match.group(1)
        path = _normalize_path(raw_path)

        # Content starts after this marker's line
        content_start = match.end()

        # Content ends at the next marker (or end of string)
        if i + 1 < len(markers):
            content_end = markers[i + 1].start()
        else:
            content_end = len(generated_code)

        content = generated_code[content_start:content_end]

        # Strip leading blank line (common after marker) and trailing whitespace
        content = content.strip("\n").rstrip()

        # If content is empty after stripping, we still record the file
        # (agent may have intentionally created an empty __init__.py)

        # Last occurrence wins for duplicate paths
        seen[path] = ParsedFile(path=path, content=content)

    # Return in order of first appearance, but with last-wins content
    ordered_paths: list[str] = []
    for match in markers:
        raw_path = match.group(1)
        try:
            path = _normalize_path(raw_path)
        except CodeParserError:
            continue
        if path not in ordered_paths:
            ordered_paths.append(path)

    return [seen[p] for p in ordered_paths if p in seen]


def validate_paths_for_workspace(
    parsed_files: list[ParsedFile],
    workspace_root: Path,
) -> list[ParsedFile]:
    """Validate that all parsed file paths are within workspace bounds.

    Uses the same containment check as LocalToolProvider to prevent
    path traversal attacks.

    Args:
        parsed_files: Files to validate.
        workspace_root: Absolute path to the workspace root.

    Returns:
        List of files that passed validation (unsafe paths are logged and skipped).

    Note:
        This is a defense-in-depth measure. _normalize_path() already rejects
        obvious traversal patterns, but this catches symlink-based escapes
        and other edge cases at the filesystem level.
    """
    resolved_root = workspace_root.resolve()
    safe_files: list[ParsedFile] = []

    for pf in parsed_files:
        target = (resolved_root / pf.path).resolve()
        if not str(target).startswith(str(resolved_root)):
            # Log but don't crash -- skip the unsafe file
            import logging

            logging.getLogger(__name__).warning(
                "Skipping file with path escaping workspace: %s -> %s",
                pf.path,
                target,
            )
            continue
        safe_files.append(pf)

    return safe_files
