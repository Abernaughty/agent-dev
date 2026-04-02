"""Code parser utility for extracting files from Dev agent output.

The Dev agent produces code as a single text string with markers like:
    # --- FILE: path/to/file.py ---

This module parses that output into individual (filepath, content) pairs
that can be written to the workspace and loaded into E2B sandboxes.

Pure functions, no side effects, fully testable in isolation.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

logger = logging.getLogger(__name__)

# Matches: # --- FILE: path/to/file.ext ---
# Allows optional trailing whitespace and varying dash counts (3+)
_FILE_MARKER_RE = re.compile(
    r"^#\s*-{3,}\s*FILE:\s*(.+?)\s*-{3,}\s*$",
    re.MULTILINE,
)

# Matches a markdown fence line: ```python, ```c++, ```objective-c, ```, etc.
# Supports info strings with non-word chars (hyphens, pluses).
# Only anchored at line boundaries -- used to strip leading/trailing fences.
_FENCE_RE = re.compile(r"^\s*```(?:[^`\s][^`]*)?\s*$")


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

    # Reject Unix absolute paths
    if cleaned.startswith("/"):
        raise CodeParserError(
            f"Absolute path not allowed: '{raw_path}'"
        )

    # Reject Windows-style absolute paths (e.g., C:/ or D:\)
    if len(cleaned) >= 2 and cleaned[1] == ":" and cleaned[0].isalpha():
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


def _strip_markdown_fences(content: str, path: str) -> str:
    """Strip leading/trailing markdown fence lines from extracted file content.

    LLMs frequently wrap code blocks in ```lang ... ``` fences even inside
    FILE-marker sections.  Only the *first* and *last* lines are checked so
    that interior backtick usage (e.g. in a README template) is preserved.

    Args:
        content: Raw file content extracted between FILE markers.
        path: File path (used only for logging).

    Returns:
        Content with leading/trailing fences removed, if any were present.
    """
    if not content:
        return content

    lines = content.split("\n")
    stripped = False

    # Strip leading fence (```python, ```js, ```, etc.)
    if lines and _FENCE_RE.match(lines[0]):
        lines = lines[1:]
        stripped = True

    # Strip trailing fence (```)
    if lines and _FENCE_RE.match(lines[-1]):
        lines = lines[:-1]
        stripped = True

    if stripped:
        logger.info("code_parser: stripped markdown fences from %s", path)

    return "\n".join(lines)


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
        content = _strip_markdown_fences(content, default_filename)
        content = content.strip("\n").rstrip()
        if not content:
            return []
        return [ParsedFile(path=default_filename, content=content)]

    # Extract files between markers.
    # Track insertion order alongside last-wins content (Python 3.7+ dict
    # preserves insertion order, but we need first-seen order with last-wins
    # content for duplicates).
    seen: dict[str, ParsedFile] = {}
    first_seen_order: list[str] = []

    for i, match in enumerate(markers):
        raw_path = match.group(1)
        path = _normalize_path(raw_path)

        if path not in seen:
            first_seen_order.append(path)

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

        # Strip markdown fences that LLMs wrap around code blocks
        content = _strip_markdown_fences(content, path)

        # Re-strip after fence removal (fence removal may expose blank lines)
        content = content.strip("\n").rstrip()

        # If content is empty after stripping, we still record the file
        # (agent may have intentionally created an empty __init__.py)

        # Last occurrence wins for duplicate paths
        seen[path] = ParsedFile(path=path, content=content)

    return [seen[p] for p in first_seen_order]


def validate_paths_for_workspace(
    parsed_files: list[ParsedFile],
    workspace_root: Path,
) -> list[ParsedFile]:
    """Validate that all parsed file paths are within workspace bounds.

    Uses pathlib's relative_to() for proper containment checking to prevent
    path traversal attacks, including sibling-directory false positives.

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
        try:
            target.relative_to(resolved_root)
        except ValueError:
            # Path escapes workspace -- log but don't crash
            logger.warning(
                "Skipping file with path escaping workspace: %s -> %s",
                pf.path,
                target,
            )
            continue
        safe_files.append(pf)

    return safe_files
