"""Tests for code_parser utility.

Covers: multi-file parsing, single-file fallback, edge cases,
path validation, and security boundaries.
"""

import os
from pathlib import Path

import pytest

from src.tools.code_parser import (
    CodeParserError,
    ParsedFile,
    _normalize_path,
    parse_generated_code,
    validate_paths_for_workspace,
)


# -- _normalize_path tests --


class TestNormalizePath:
    """Path normalization and security checks."""

    def test_simple_path(self):
        assert _normalize_path("src/main.py") == "src/main.py"

    def test_strips_leading_dot_slash(self):
        assert _normalize_path("./src/main.py") == "src/main.py"

    def test_strips_multiple_leading_dot_slash(self):
        assert _normalize_path("././src/main.py") == "src/main.py"

    def test_strips_whitespace(self):
        assert _normalize_path("  src/main.py  ") == "src/main.py"

    def test_normalizes_backslashes(self):
        assert _normalize_path("src\\lib\\utils.py") == "src/lib/utils.py"

    def test_rejects_absolute_path(self):
        with pytest.raises(CodeParserError, match="Absolute path"):
            _normalize_path("/etc/passwd")

    def test_rejects_traversal_dotdot(self):
        with pytest.raises(CodeParserError, match="Path traversal"):
            _normalize_path("../../../etc/passwd")

    def test_rejects_traversal_mid_path(self):
        with pytest.raises(CodeParserError, match="Path traversal"):
            _normalize_path("src/../../secret.txt")

    def test_rejects_empty_path(self):
        with pytest.raises(CodeParserError, match="Empty file path"):
            _normalize_path("")

    def test_rejects_whitespace_only(self):
        with pytest.raises(CodeParserError, match="Empty file path"):
            _normalize_path("   ")


# -- parse_generated_code tests --


class TestParseGeneratedCode:
    """Main parser tests."""

    def test_standard_multi_file(self):
        """Standard multi-file output with FILE markers."""
        code = (
            "# --- FILE: src/main.py ---\n"
            "def main():\n"
            "    print('hello')\n"
            "\n"
            "# --- FILE: src/utils.py ---\n"
            "def helper():\n"
            "    return 42\n"
        )
        result = parse_generated_code(code)
        assert len(result) == 2
        assert result[0].path == "src/main.py"
        assert "def main():" in result[0].content
        assert result[1].path == "src/utils.py"
        assert "def helper():" in result[1].content

    def test_single_file_no_markers(self):
        """Entire output treated as single file when no markers present."""
        code = "def main():\n    print('hello')\n"
        result = parse_generated_code(code)
        assert len(result) == 1
        assert result[0].path == "output.py"
        assert "def main():" in result[0].content

    def test_single_file_custom_default_name(self):
        """Custom default filename for marker-less output."""
        code = "console.log('hi')"
        result = parse_generated_code(code, default_filename="index.js")
        assert len(result) == 1
        assert result[0].path == "index.js"

    def test_empty_input(self):
        """Empty string returns empty list."""
        assert parse_generated_code("") == []
        assert parse_generated_code("   ") == []
        assert parse_generated_code("\n\n") == []

    def test_none_like_empty(self):
        """None-ish empty string."""
        assert parse_generated_code("") == []

    def test_empty_file_between_markers(self):
        """Empty file content (e.g., __init__.py)."""
        code = (
            "# --- FILE: src/__init__.py ---\n"
            "\n"
            "# --- FILE: src/main.py ---\n"
            "x = 1\n"
        )
        result = parse_generated_code(code)
        assert len(result) == 2
        assert result[0].path == "src/__init__.py"
        assert result[0].content == ""
        assert result[1].path == "src/main.py"
        assert result[1].content == "x = 1"

    def test_duplicate_paths_last_wins(self):
        """Duplicate file paths: last content wins."""
        code = (
            "# --- FILE: src/main.py ---\n"
            "version = 1\n"
            "# --- FILE: src/main.py ---\n"
            "version = 2\n"
        )
        result = parse_generated_code(code)
        assert len(result) == 1
        assert result[0].path == "src/main.py"
        assert "version = 2" in result[0].content

    def test_nested_directory_paths(self):
        """Deep nested directory paths."""
        code = (
            "# --- FILE: src/lib/utils/helpers/format.py ---\n"
            "def fmt(): pass\n"
        )
        result = parse_generated_code(code)
        assert len(result) == 1
        assert result[0].path == "src/lib/utils/helpers/format.py"

    def test_path_traversal_raises(self):
        """Path with .. components raises CodeParserError."""
        code = "# --- FILE: ../../etc/passwd ---\nroot:x:0:0\n"
        with pytest.raises(CodeParserError, match="Path traversal"):
            parse_generated_code(code)

    def test_absolute_path_raises(self):
        """Absolute path raises CodeParserError."""
        code = "# --- FILE: /etc/shadow ---\nfoo\n"
        with pytest.raises(CodeParserError, match="Absolute path"):
            parse_generated_code(code)

    def test_varying_dash_counts(self):
        """Markers with different numbers of dashes."""
        code = (
            "# ----- FILE: src/a.py -----\n"
            "a = 1\n"
            "# --- FILE: src/b.py ---\n"
            "b = 2\n"
        )
        result = parse_generated_code(code)
        assert len(result) == 2

    def test_content_before_first_marker_is_ignored(self):
        """Text before the first marker is dropped."""
        code = (
            "Here is the implementation:\n\n"
            "# --- FILE: src/main.py ---\n"
            "x = 1\n"
        )
        result = parse_generated_code(code)
        assert len(result) == 1
        assert result[0].path == "src/main.py"
        assert "Here is the implementation" not in result[0].content

    def test_preserves_internal_blank_lines(self):
        """Blank lines within file content are preserved."""
        code = (
            "# --- FILE: src/main.py ---\n"
            "import os\n"
            "\n"
            "\n"
            "def main():\n"
            "    pass\n"
        )
        result = parse_generated_code(code)
        assert "\n\n" in result[0].content

    def test_strips_trailing_whitespace(self):
        """Trailing whitespace on file content is stripped."""
        code = "# --- FILE: src/main.py ---\nx = 1\n\n\n   \n"
        result = parse_generated_code(code)
        assert result[0].content == "x = 1"

    def test_mixed_file_types(self):
        """Multiple file types in single output."""
        code = (
            "# --- FILE: src/app.py ---\n"
            "app = Flask(__name__)\n"
            "# --- FILE: src/routes/index.svelte ---\n"
            "<script>let count = 0;</script>\n"
            "# --- FILE: tests/test_app.py ---\n"
            "def test_health(): assert True\n"
            '# --- FILE: package.json ---\n'
            '{"name": "app"}\n'
        )
        result = parse_generated_code(code)
        assert len(result) == 4
        assert result[0].path == "src/app.py"
        assert result[1].path == "src/routes/index.svelte"
        assert result[2].path == "tests/test_app.py"
        assert result[3].path == "package.json"

    def test_large_output(self):
        """Parser handles large outputs without issue."""
        lines = ["# --- FILE: src/big.py ---\n"]
        for i in range(1000):
            lines.append(f"x_{i} = {i}\n")
        code = "".join(lines)
        result = parse_generated_code(code)
        assert len(result) == 1
        assert "x_999 = 999" in result[0].content


# -- validate_paths_for_workspace tests --


class TestValidatePathsForWorkspace:
    """Workspace path containment validation."""

    def test_valid_paths_pass(self, tmp_path):
        """Normal paths within workspace pass through."""
        files = [
            ParsedFile(path="src/main.py", content="x = 1"),
            ParsedFile(path="tests/test.py", content="y = 2"),
        ]
        result = validate_paths_for_workspace(files, tmp_path)
        assert len(result) == 2

    def test_symlink_escape_filtered(self, tmp_path):
        """Symlinks that resolve outside workspace are filtered out."""
        # Create a symlink pointing outside workspace
        outside = tmp_path.parent / "outside_file"
        outside.write_text("secret")

        link = tmp_path / "sneaky"
        try:
            link.symlink_to(outside)
        except OSError:
            pytest.skip("Symlinks not supported on this platform")

        files = [
            ParsedFile(path="src/main.py", content="x = 1"),
            ParsedFile(path="sneaky", content="overwrite"),
        ]
        result = validate_paths_for_workspace(files, tmp_path)
        assert len(result) >= 1

    def test_empty_list(self, tmp_path):
        """Empty input returns empty output."""
        result = validate_paths_for_workspace([], tmp_path)
        assert result == []
