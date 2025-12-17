#!/usr/bin/env python3
"""
Unit tests for git_commit_agent.py

Run with: pytest test_git_commit_agent.py -v
"""

import pytest
from git_commit_agent import (
    parse_tracking_info,
    BranchInfo,
    Config,
    DEFAULT_CONFIG
)


class TestParseTrackingInfo:
    """Tests for parse_tracking_info function."""
    
    def test_ahead_only(self):
        """Test parsing when branch is only ahead."""
        ahead, behind = parse_tracking_info("[ahead 3]")
        assert ahead == 3
        assert behind == 0
    
    def test_behind_only(self):
        """Test parsing when branch is only behind."""
        ahead, behind = parse_tracking_info("[behind 2]")
        assert ahead == 0
        assert behind == 2
    
    def test_ahead_and_behind(self):
        """Test parsing when branch is both ahead and behind."""
        ahead, behind = parse_tracking_info("[ahead 5, behind 2]")
        assert ahead == 5
        assert behind == 2
    
    def test_empty_string(self):
        """Test parsing empty tracking info."""
        ahead, behind = parse_tracking_info("")
        assert ahead == 0
        assert behind == 0
    
    def test_no_tracking(self):
        """Test parsing when no tracking info present."""
        ahead, behind = parse_tracking_info("[]")
        assert ahead == 0
        assert behind == 0
    
    def test_different_order(self):
        """Test parsing when behind comes before ahead."""
        ahead, behind = parse_tracking_info("[behind 1, ahead 4]")
        assert ahead == 4
        assert behind == 1


class TestBranchInfo:
    """Tests for BranchInfo dataclass."""
    
    def test_branch_info_creation(self):
        """Test creating a BranchInfo object."""
        branch = BranchInfo(
            name="feature/test",
            upstream="origin/main",
            ahead=3,
            behind=0,
            diff_stat="2 files changed, 10 insertions(+), 5 deletions(-)",
            diff_content="diff --git a/test.py...",
            commit_log="abc123 Add feature\ndef456 Fix bug",
            files_changed=2,
            insertions=10,
            deletions=5
        )
        
        assert branch.name == "feature/test"
        assert branch.upstream == "origin/main"
        assert branch.ahead == 3
        assert branch.behind == 0
        assert branch.files_changed == 2
        assert branch.insertions == 10
        assert branch.deletions == 5
    
    def test_branch_info_defaults(self):
        """Test BranchInfo with default values."""
        branch = BranchInfo(
            name="main",
            upstream=None,
            ahead=0,
            behind=0,
            diff_stat="",
            diff_content="",
            commit_log=""
        )
        
        assert branch.files_changed == 0
        assert branch.insertions == 0
        assert branch.deletions == 0


class TestConfig:
    """Tests for Config dataclass."""
    
    def test_config_creation(self):
        """Test creating a Config object."""
        config = Config(
            max_diff_chars=50000,
            model="claude-3-5-sonnet-20241022",
            temperature=0.7,
            max_retries=3,
            commit_types=["feat", "fix", "docs"]
        )
        
        assert config.max_diff_chars == 50000
        assert config.model == "claude-3-5-sonnet-20241022"
        assert config.temperature == 0.7
        assert config.max_retries == 3
        assert len(config.commit_types) == 3
    
    def test_default_config(self):
        """Test default configuration values."""
        config = Config(**DEFAULT_CONFIG)
        
        assert config.max_diff_chars == 50000
        assert config.model == "claude-3-5-sonnet-20241022"
        assert config.temperature == 0.7
        assert config.max_retries == 3
        assert "feat" in config.commit_types
        assert "fix" in config.commit_types


class TestDiffTruncation:
    """Tests for diff content truncation logic."""
    
    def test_truncation_message(self):
        """Test that truncation adds appropriate message."""
        config = Config(**DEFAULT_CONFIG)
        large_diff = "x" * 100000
        
        # Simulate truncation logic from get_branch_diff
        original_size = len(large_diff)
        if len(large_diff) > config.max_diff_chars:
            truncated = large_diff[:config.max_diff_chars]
            truncated += f"\n\n... [Truncated: showing first {config.max_diff_chars} of {original_size} characters]"
        else:
            truncated = large_diff
        
        assert len(truncated) > config.max_diff_chars  # Includes truncation message
        assert "Truncated" in truncated
        assert str(original_size) in truncated
    
    def test_no_truncation_needed(self):
        """Test that small diffs are not truncated."""
        config = Config(**DEFAULT_CONFIG)
        small_diff = "x" * 1000
        
        # Simulate truncation logic
        original_size = len(small_diff)
        if len(small_diff) > config.max_diff_chars:
            truncated = small_diff[:config.max_diff_chars]
            truncated += f"\n\n... [Truncated: showing first {config.max_diff_chars} of {original_size} characters]"
        else:
            truncated = small_diff
        
        assert len(truncated) == 1000
        assert "Truncated" not in truncated


class TestCommitMessageFormat:
    """Tests for commit message format validation."""
    
    def test_valid_commit_message_format(self):
        """Test that a valid commit message matches expected format."""
        message = """feat(auth): implement JWT authentication

- Add JWT token generation and validation
- Implement user login endpoint
- Add password hashing with bcrypt"""
        
        lines = message.split('\n')
        first_line = lines[0]
        
        # Check first line format
        assert '(' in first_line
        assert ')' in first_line
        assert ':' in first_line
        assert len(first_line) <= 72
        
        # Check for bullet points in body
        body_lines = [line for line in lines[1:] if line.strip()]
        assert any(line.strip().startswith('-') for line in body_lines)
    
    def test_commit_type_validation(self):
        """Test that commit types are from allowed list."""
        config = Config(**DEFAULT_CONFIG)
        valid_types = config.commit_types
        
        test_messages = [
            "feat(api): add endpoint",
            "fix(auth): resolve bug",
            "docs(readme): update guide",
            "refactor(core): simplify logic"
        ]
        
        for msg in test_messages:
            commit_type = msg.split('(')[0]
            assert commit_type in valid_types


class TestFileStatisticsParsing:
    """Tests for parsing git diff statistics."""
    
    def test_parse_files_changed(self):
        """Test parsing number of files changed."""
        import re
        
        diff_stat = "3 files changed, 42 insertions(+), 15 deletions(-)"
        
        stat_match = re.search(r'(\d+) files? changed', diff_stat)
        assert stat_match is not None
        assert int(stat_match.group(1)) == 3
    
    def test_parse_insertions(self):
        """Test parsing number of insertions."""
        import re
        
        diff_stat = "3 files changed, 42 insertions(+), 15 deletions(-)"
        
        insert_match = re.search(r'(\d+) insertions?', diff_stat)
        assert insert_match is not None
        assert int(insert_match.group(1)) == 42
    
    def test_parse_deletions(self):
        """Test parsing number of deletions."""
        import re
        
        diff_stat = "3 files changed, 42 insertions(+), 15 deletions(-)"
        
        delete_match = re.search(r'(\d+) deletions?', diff_stat)
        assert delete_match is not None
        assert int(delete_match.group(1)) == 15
    
    def test_parse_single_file(self):
        """Test parsing when only one file changed."""
        import re
        
        diff_stat = "1 file changed, 5 insertions(+)"
        
        stat_match = re.search(r'(\d+) files? changed', diff_stat)
        assert stat_match is not None
        assert int(stat_match.group(1)) == 1
    
    def test_parse_no_deletions(self):
        """Test parsing when there are no deletions."""
        import re
        
        diff_stat = "2 files changed, 20 insertions(+)"
        
        delete_match = re.search(r'(\d+) deletions?', diff_stat)
        assert delete_match is None


class TestEdgeCases:
    """Tests for edge cases and error conditions."""
    
    def test_empty_diff_stat(self):
        """Test handling empty diff stat."""
        import re
        
        diff_stat = ""
        
        stat_match = re.search(r'(\d+) files? changed', diff_stat)
        files_changed = int(stat_match.group(1)) if stat_match else 0
        
        assert files_changed == 0
    
    def test_branch_with_no_upstream(self):
        """Test BranchInfo with no upstream."""
        branch = BranchInfo(
            name="local-branch",
            upstream=None,
            ahead=0,
            behind=0,
            diff_stat="",
            diff_content="",
            commit_log=""
        )
        
        assert branch.upstream is None
        assert branch.ahead == 0
    
    def test_very_long_branch_name(self):
        """Test handling very long branch names."""
        long_name = "feature/" + "x" * 200
        branch = BranchInfo(
            name=long_name,
            upstream="origin/main",
            ahead=1,
            behind=0,
            diff_stat="",
            diff_content="",
            commit_log=""
        )
        
        assert branch.name == long_name
        assert len(branch.name) > 200


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
