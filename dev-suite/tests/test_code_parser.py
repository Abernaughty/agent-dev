"""Tests for code_parser utility.

Covers: multi-file parsing, single-file fallback, edge cases,
path validation, security boundaries, and markdown fence stripping.
"""

import os
from pathlib import Path

import pytest

from src.tools.code_parser import (
    CodeParserError,
    ParsedFile,
    _normalize_path,
    _strip_markdown_fences,
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

    def test_rejects_windows_absolute_path(self):
        with pytest.raises(CodeParserError, match="Absolute path"):
            _normalize_path("C:\\Users\\secret.txt")

    def test_rejects_windows_absolute_path_forward_slash(self):
        with pytest.raises(CodeParserError, match="Absolute path"):
            _normalize_path("D:/secret.txt")

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


# -- _strip_markdown_fences tests --


class TestStripMarkdownFences:
    """Markdown fence stripping from extracted file content."""

    def test_strips_python_fence(self):
        content = "```python\ndef hello():\n    print('hi')\n```"
        result = _strip_markdown_fences(content, "test.py")
        assert result == "def hello():\n    print('hi')"

    def test_strips_bare_fence(self):
        content = "```\nsome content\n```"
        result = _strip_markdown_fences(content, "test.txt")
        assert result == "some content"

    def test_strips_js_fence(self):
        content = "```javascript\nconsole.log('hi')\n```"
        result = _strip_markdown_fences(content, "test.js")
        assert result == "console.log('hi')"

    def test_strips_sql_fence(self):
        content = "```sql\nSELECT * FROM users;\n```"
        result = _strip_markdown_fences(content, "test.sql")
        assert result == "SELECT * FROM users;"

    def test_no_fence_unchanged(self):
        content = "def hello():\n    print('hi')"
        result = _strip_markdown_fences(content, "test.py")
        assert result == content

    def test_preserves_interior_backticks(self):
        """Interior triple backticks (e.g., in a README) are NOT stripped."""
        content = (
            "```python\n"
            "# This generates markdown\n"
            "output = '```python\\nprint(1)\\n```'\n"
            "```"
        )
        result = _strip_markdown_fences(content, "gen.py")
        assert result == (
            "# This generates markdown\n"
            "output = '```python\\nprint(1)\\n```'"
        )

    def test_only_leading_fence(self):
        """Leading fence without trailing -- still strip the leading one."""
        content = "```python\ndef hello():\n    pass"
        result = _strip_markdown_fences(content, "test.py")
        assert result == "def hello():\n    pass"

    def test_only_trailing_fence(self):
        """Trailing fence without leading -- still strip the trailing one."""
        content = "def hello():\n    pass\n```"
        result = _strip_markdown_fences(content, "test.py")
        assert result == "def hello():\n    pass"

    def test_empty_content(self):
        assert _strip_markdown_fences("", "test.py") == ""

    def test_fence_with_trailing_whitespace(self):
        content = "```python   \ndef hello():\n    pass\n```  "
        result = _strip_markdown_fences(content, "test.py")
        assert result == "def hello():\n    pass"


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

    # -- Markdown fence stripping integration tests --

    def test_strips_python_fence_in_marker_block(self):
        """Fence after FILE marker is stripped (the #101 bug)."""
        code = (
            "# --- FILE: triforce.py ---\n"
            "```python\n"
            "#!/usr/bin/env python3\n"
            "print('triforce')\n"
            "```\n"
        )
        result = parse_generated_code(code)
        assert len(result) == 1
        assert result[0].path == "triforce.py"
        assert result[0].content.startswith("#!/usr/bin/env python3")
        assert "```" not in result[0].content

    def test_strips_bare_fence_in_marker_block(self):
        """Bare ``` fences (no language tag) are also stripped."""
        code = (
            "# --- FILE: output.txt ---\n"
            "```\n"
            "hello world\n"
            "```\n"
        )
        result = parse_generated_code(code)
        assert result[0].content == "hello world"

    def test_clean_code_unaffected_by_fence_stripping(self):
        """Code without fences is not modified by the new logic."""
        code = (
            "# --- FILE: main.py ---\n"
            "#!/usr/bin/env python3\n"
            "print('hello')\n"
        )
        result = parse_generated_code(code)
        assert result[0].content == "#!/usr/bin/env python3\nprint('hello')"

    def test_multi_file_with_mixed_fences(self):
        """Some files have fences, some don't."""
        code = (
            "# --- FILE: a.py ---\n"
            "```python\n"
            "x = 1\n"
            "```\n"
            "# --- FILE: b.py ---\n"
            "y = 2\n"
            "# --- FILE: c.js ---\n"
            "```javascript\n"
            "const z = 3;\n"
            "```\n"
        )
        result = parse_generated_code(code)
        assert len(result) == 3
        assert result[0].content == "x = 1"
        assert "```" not in result[0].content
        assert result[1].content == "y = 2"
        assert result[2].content == "const z = 3;"
        assert "```" not in result[2].content

    def test_regression_triforce_trace(self):
        """Regression test: exact pattern from trace-2779973899882ec73c9b4eb161ae53f5.

        The Dev agent generated code with a FILE marker followed by ```python
        fence, which caused SyntaxError in E2B sandbox on all 3 retries.
        """
        code = (
            "I've created the triforce.py file.\n\n"
            "# --- FILE: triforce.py ---\n"
            "```python\n"
            "#!/usr/bin/env python3\n"
            '"""Triforce ASCII Art Generator"""\n'
            "\n"
            "def print_triforce():\n"
            '    print("    /\\\\    ")\n'
            '    print("   /  \\\\   ")\n'
            '    print("  /____\\\\  ")\n'
            '    print(" /\\\\  /\\\\  ")\n'
            '    print("/____\\\\/____\\\\")\n'
            "\n"
            'if __name__ == "__main__":\n'
            "    print_triforce()\n"
            "```\n"
        )
        result = parse_generated_code(code)
        assert len(result) == 1
        assert result[0].path == "triforce.py"
        # Must NOT start with ```python
        assert not result[0].content.startswith("```")
        # Must start with the shebang
        assert result[0].content.startswith("#!/usr/bin/env python3")
        # Must NOT contain any fences
        assert "```" not in result[0].content
        # Must be valid Python (no SyntaxError)
        compile(result[0].content, "triforce.py", "exec")

    def test_interior_backticks_preserved(self):
        """Triple backticks inside file content (not first/last line) survive."""
        code = (
            "# --- FILE: README.md ---\n"
            "# My Project\n"
            "\n"
            "```python\n"
            "print('example')\n"
            "```\n"
            "\n"
            "More docs here.\n"
        )
        result = parse_generated_code(code)
        # The first ``` is on line 3 (not line 1) so it should NOT be stripped
        assert "```python" in result[0].content
        assert "```" in result[0].content


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
        # Only the safe file should remain; symlink escape is filtered
        assert len(result) == 1
        assert result[0].path == "src/main.py"

    def test_empty_list(self, tmp_path):
        """Empty input returns empty output."""
        result = validate_paths_for_workspace([], tmp_path)
        assert result == []
