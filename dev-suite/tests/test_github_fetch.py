"""Tests for GitHub issue/PR pre-fetch (issue #193).

Covers:
- Ref pattern extraction (same-repo, cross-repo, dedupe, max_refs cap)
- Rejection of bare `#N` without qualifier
- fetch_issue_or_pr: success (issue body), truncation, PR detection,
  missing token, non-200, network error, malformed JSON
- fetch_refs_as_context_items end-to-end with mocked httpx
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.tools.github_fetch import (
    GitHubRef,
    extract_github_refs,
    fetch_issue_or_pr,
    fetch_refs_as_context_items,
)

# --- extract_github_refs ---------------------------------------------------


class TestExtractGithubRefs:
    def test_empty_text(self):
        assert extract_github_refs("", "o", "r") == []
        assert extract_github_refs(None, "o", "r") == []  # type: ignore[arg-type]

    def test_simple_issue_reference(self):
        refs = extract_github_refs("fix issue #113", "owner", "repo")
        assert refs == [GitHubRef(owner="owner", repo="repo", number=113)]

    def test_all_qualifiers_accepted(self):
        qualifiers = [
            # singular, plural, and past-tense closing keywords
            "issue", "issues",
            "fix", "fixes", "fixed",
            "close", "closes", "closed",
            "resolve", "resolves", "resolved",
            "ref", "refs", "see", "review",
            "address", "gh", "pr", "pull", "pulls",
        ]
        for q in qualifiers:
            refs = extract_github_refs(f"{q} #42", "o", "r")
            assert len(refs) == 1, f"qualifier '{q}' did not match"
            assert refs[0].number == 42

    def test_pull_request_multiword_qualifier(self):
        refs = extract_github_refs("see pull request #77", "o", "r")
        assert refs == [GitHubRef(owner="o", repo="r", number=77)]

    def test_case_insensitive_qualifier(self):
        refs = extract_github_refs("FIXES #5 and Closes #6", "o", "r")
        assert len(refs) == 2
        assert {r.number for r in refs} == {5, 6}

    def test_cross_repo_reference(self):
        refs = extract_github_refs(
            "see Abernaughty/agent-dev#113", "default", "default"
        )
        assert refs == [
            GitHubRef(owner="Abernaughty", repo="agent-dev", number=113),
        ]

    def test_cross_repo_without_default(self):
        # Cross-repo still works even without default owner/repo
        refs = extract_github_refs("foo/bar#1", "", "")
        assert refs == [GitHubRef(owner="foo", repo="bar", number=1)]

    def test_bare_hash_number_rejected(self):
        # No qualifier, no cross-repo prefix — must not match
        refs = extract_github_refs(
            "Heading\n# 113\n\nSome #456 random text",
            "owner", "repo",
        )
        assert refs == []

    def test_markdown_heading_not_matched(self):
        refs = extract_github_refs(
            "# Main heading\n## Subheading",
            "o", "r",
        )
        assert refs == []

    def test_hex_color_not_matched(self):
        # #abc123 starts with letter, regex requires digits only after #
        refs = extract_github_refs("color #abc123 and #deadbeef", "o", "r")
        assert refs == []

    def test_issue_prefix_on_longer_word_not_matched(self):
        # "issue123" (no space) should not be treated as qualifier
        refs = extract_github_refs("see issue123\n#42 here", "o", "r")
        assert refs == []

    def test_default_owner_repo_used(self):
        refs = extract_github_refs("fixes #42", "myowner", "myrepo")
        assert refs[0].owner == "myowner"
        assert refs[0].repo == "myrepo"

    def test_same_repo_dropped_when_no_default(self):
        # Same-repo refs require a default owner/repo to resolve
        refs = extract_github_refs("fixes #42", "", "")
        assert refs == []

    def test_deduplication(self):
        refs = extract_github_refs(
            "fixes #10, closes #10, refs #10", "o", "r",
        )
        assert len(refs) == 1
        assert refs[0].number == 10

    def test_dedup_across_same_and_cross_repo(self):
        # Same number from different repos is NOT a duplicate
        refs = extract_github_refs(
            "fixes #10 and foo/bar#10", "o", "r",
        )
        assert len(refs) == 2
        keys = {r.key for r in refs}
        assert ("o", "r", 10) in keys
        assert ("foo", "bar", 10) in keys

    def test_cross_repo_not_double_counted(self):
        # "foo/bar#1" must not also match "bar#1" via same-repo
        refs = extract_github_refs("see foo/bar#1", "o", "r")
        assert len(refs) == 1
        assert refs[0].owner == "foo"

    def test_max_refs_cap(self):
        text = " ".join(f"fixes #{i}" for i in range(1, 20))
        refs = extract_github_refs(text, "o", "r", max_refs=5)
        assert len(refs) == 5
        # First-seen order preserved
        assert [r.number for r in refs] == [1, 2, 3, 4, 5]

    def test_preserves_first_seen_order(self):
        refs = extract_github_refs(
            "see foo/bar#99 and fixes #1 then closes #2",
            "o", "r",
        )
        assert [r.number for r in refs] == [99, 1, 2]

    def test_zero_and_negative_rejected(self):
        refs = extract_github_refs("fixes #0", "o", "r")
        assert refs == []

    def test_synthetic_path(self):
        ref = GitHubRef(owner="Abernaughty", repo="agent-dev", number=113)
        assert ref.synthetic_path == "github://Abernaughty/agent-dev/issues/113"


# --- fetch_issue_or_pr -----------------------------------------------------


def _make_response(status_code=200, json_data=None):
    """Build a mock httpx.Response-like object."""
    response = MagicMock()
    response.status_code = status_code
    response.json = MagicMock(return_value=json_data or {})
    return response


class TestFetchIssueOrPr:
    @pytest.mark.asyncio
    async def test_returns_none_without_token(self):
        result = await fetch_issue_or_pr("o", "r", 1, token="")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_without_owner_repo(self):
        result = await fetch_issue_or_pr("", "r", 1, token="t")
        assert result is None
        result = await fetch_issue_or_pr("o", "", 1, token="t")
        assert result is None

    @pytest.mark.asyncio
    async def test_success_issue(self):
        payload = {
            "number": 113,
            "title": "Gate test",
            "state": "open",
            "body": "The gate test body.",
            "labels": [{"name": "phase/2"}, {"name": "P0"}],
        }
        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None
        mock_client.get.return_value = _make_response(200, payload)

        with patch("src.tools.github_fetch.httpx.AsyncClient", return_value=mock_client):
            result = await fetch_issue_or_pr("owner", "repo", 113, token="t")

        assert result is not None
        assert result["path"] == "github://owner/repo/issues/113"
        assert result["source"] == "github_issue"
        assert result["truncated"] is False
        assert "Issue #113: Gate test" in result["content"]
        assert "State: open" in result["content"]
        assert "Labels: phase/2, P0" in result["content"]
        assert "The gate test body." in result["content"]

    @pytest.mark.asyncio
    async def test_success_pr_marked(self):
        payload = {
            "number": 200,
            "title": "A PR",
            "state": "open",
            "body": "diff coming",
            "pull_request": {"url": "..."},  # presence indicates PR
        }
        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None
        mock_client.get.return_value = _make_response(200, payload)

        with patch("src.tools.github_fetch.httpx.AsyncClient", return_value=mock_client):
            result = await fetch_issue_or_pr("o", "r", 200, token="t")

        assert result is not None
        assert "PR #200" in result["content"]

    @pytest.mark.asyncio
    async def test_body_truncated(self):
        big_body = "X" * 5000
        payload = {"number": 1, "title": "t", "state": "open", "body": big_body}
        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None
        mock_client.get.return_value = _make_response(200, payload)

        with patch("src.tools.github_fetch.httpx.AsyncClient", return_value=mock_client):
            result = await fetch_issue_or_pr("o", "r", 1, token="t", max_chars=500)

        assert result is not None
        assert result["truncated"] is True
        assert "[truncated]" in result["content"]
        # Overall content respects budget (roughly)
        assert len(result["content"]) <= 600

    @pytest.mark.asyncio
    async def test_non_200_returns_none(self):
        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None
        mock_client.get.return_value = _make_response(404, {})

        with patch("src.tools.github_fetch.httpx.AsyncClient", return_value=mock_client):
            result = await fetch_issue_or_pr("o", "r", 999, token="t")
        assert result is None

    @pytest.mark.asyncio
    async def test_network_error_returns_none(self):
        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None
        mock_client.get.side_effect = httpx.ConnectError("boom")

        with patch("src.tools.github_fetch.httpx.AsyncClient", return_value=mock_client):
            result = await fetch_issue_or_pr("o", "r", 1, token="t")
        assert result is None

    @pytest.mark.asyncio
    async def test_malformed_json_returns_none(self):
        response = MagicMock()
        response.status_code = 200
        response.json = MagicMock(side_effect=ValueError("bad json"))

        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None
        mock_client.get.return_value = response

        with patch("src.tools.github_fetch.httpx.AsyncClient", return_value=mock_client):
            result = await fetch_issue_or_pr("o", "r", 1, token="t")
        assert result is None

    @pytest.mark.asyncio
    async def test_empty_body_ok(self):
        payload = {"number": 1, "title": "Title", "state": "closed", "body": None}
        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None
        mock_client.get.return_value = _make_response(200, payload)

        with patch("src.tools.github_fetch.httpx.AsyncClient", return_value=mock_client):
            result = await fetch_issue_or_pr("o", "r", 1, token="t")

        assert result is not None
        assert result["truncated"] is False
        assert "Issue #1: Title" in result["content"]


# --- fetch_refs_as_context_items ------------------------------------------


class TestFetchRefsAsContextItems:
    @pytest.mark.asyncio
    async def test_no_refs_returns_empty(self):
        result = await fetch_refs_as_context_items(
            "just some text", "o", "r", token="t",
        )
        assert result == []

    @pytest.mark.asyncio
    async def test_best_effort_skips_failed_fetches(self):
        """When one fetch fails, others still succeed."""
        payload_ok = {
            "number": 10, "title": "OK", "state": "open", "body": "body"
        }

        call_count = {"n": 0}

        def side_effect(*args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return _make_response(404, {})  # first fails
            return _make_response(200, payload_ok)

        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None
        mock_client.get.side_effect = side_effect

        with patch("src.tools.github_fetch.httpx.AsyncClient", return_value=mock_client):
            items = await fetch_refs_as_context_items(
                "fixes #9, closes #10", "o", "r", token="t",
            )

        assert len(items) == 1
        assert items[0]["path"] == "github://o/r/issues/10"

    @pytest.mark.asyncio
    async def test_no_token_returns_empty(self):
        items = await fetch_refs_as_context_items(
            "fixes #1", "o", "r", token="",
        )
        assert items == []

    @pytest.mark.asyncio
    async def test_respects_max_refs(self):
        payload = {"number": 1, "title": "t", "state": "open", "body": "b"}
        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None
        mock_client.get.return_value = _make_response(200, payload)

        text = " ".join(f"fixes #{i}" for i in range(1, 20))
        with patch("src.tools.github_fetch.httpx.AsyncClient", return_value=mock_client):
            items = await fetch_refs_as_context_items(
                text, "o", "r", token="t", max_refs=3,
            )

        assert len(items) == 3
