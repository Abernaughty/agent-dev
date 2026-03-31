"""Tests for the GitHub PR provider — live REST API integration.

Issue #50: PR store ↔ GitHub REST API

Covers:
- Provider configuration and initialization
- Cache behavior (TTL, ETag, invalidation, stale fallback)
- PR listing and mapping (status, fork detection, draft PRs)
- File changes, reviews, comments (issue + inline)
- Write operations: create PR, post review, add comment, merge, update
- Graceful degradation (no token, API errors)
- StateManager delegation to provider
- API endpoints (GET/POST /prs/...)
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.api.github_prs import CACHE_TTL, GitHubPRProvider, _CacheEntry
from src.api.models import PRCheckStatus, PRComment, PRFileChange, PRReview, PRStatus, PRSummary

SAMPLE_PR_OPEN = {"number": 52, "title": "feat: orchestrator bridge", "state": "open", "draft": False, "merged_at": None, "mergeable": True, "body": "Adds TaskRunner", "additions": 500, "deletions": 20, "changed_files": 3, "user": {"login": "Abernaughty", "type": "User"}, "head": {"ref": "feat/orchestrator-bridge", "sha": "abc123def", "repo": {"full_name": "Abernaughty/agent-dev", "owner": {"login": "Abernaughty"}}}, "base": {"ref": "main", "repo": {"full_name": "Abernaughty/agent-dev", "owner": {"login": "Abernaughty"}}}}
SAMPLE_PR_MERGED = {"number": 47, "title": "feat: SSE events", "state": "closed", "draft": False, "merged_at": "2026-03-25T14:00:00Z", "merged": True, "body": "SSE system", "additions": 300, "deletions": 10, "changed_files": 2, "user": {"login": "Abernaughty", "type": "User"}, "head": {"ref": "feat/sse", "sha": "def456", "repo": {"full_name": "Abernaughty/agent-dev", "owner": {"login": "Abernaughty"}}}, "base": {"ref": "main", "repo": {"full_name": "Abernaughty/agent-dev", "owner": {"login": "Abernaughty"}}}}
SAMPLE_PR_DRAFT = {"number": 53, "title": "wip: new feature", "state": "open", "draft": True, "merged_at": None, "body": "", "additions": 10, "deletions": 0, "changed_files": 1, "user": {"login": "Abernaughty", "type": "User"}, "head": {"ref": "wip/new-feature", "sha": "wip123", "repo": {"full_name": "Abernaughty/agent-dev", "owner": {"login": "Abernaughty"}}}, "base": {"ref": "main", "repo": {"full_name": "Abernaughty/agent-dev", "owner": {"login": "Abernaughty"}}}}
SAMPLE_PR_FORK = {"number": 99, "title": "fix: typo from fork", "state": "open", "draft": False, "merged_at": None, "body": "Typo fix", "additions": 1, "deletions": 1, "changed_files": 1, "user": {"login": "contributor", "type": "User"}, "head": {"ref": "fix-typo", "sha": "fork999", "repo": {"full_name": "contributor/agent-dev", "owner": {"login": "contributor"}}}, "base": {"ref": "main", "repo": {"full_name": "Abernaughty/agent-dev", "owner": {"login": "Abernaughty"}}}}
SAMPLE_REVIEW = {"id": 1001, "user": {"login": "codex-bot", "type": "Bot"}, "state": "APPROVED", "body": "LGTM", "submitted_at": "2026-03-26T10:00:00Z"}
SAMPLE_ISSUE_COMMENT = {"id": 2001, "user": {"login": "Abernaughty", "type": "User"}, "body": "Looks good, merging", "created_at": "2026-03-26T11:00:00Z"}
SAMPLE_REVIEW_COMMENT = {"id": 3001, "user": {"login": "codex-bot", "type": "Bot"}, "body": "Consider adding a docstring", "path": "src/api/runner.py", "line": 42, "created_at": "2026-03-26T10:30:00Z"}
SAMPLE_FILE = {"filename": "src/api/runner.py", "additions": 300, "deletions": 0, "status": "added", "patch": "@@ -0,0 +1,300 @@\n+code here"}
SAMPLE_CHECK_RUN = {"name": "tests", "status": "completed", "conclusion": "success"}


@pytest.fixture
def provider():
    with patch.dict("os.environ", {"GITHUB_TOKEN": "ghp_test123", "GITHUB_OWNER": "TestOwner", "GITHUB_REPO": "test-repo"}):
        p = GitHubPRProvider()
    return p

@pytest.fixture
def provider_no_token():
    with patch.dict("os.environ", {"GITHUB_TOKEN": "", "GITHUB_OWNER": "TestOwner", "GITHUB_REPO": "test-repo"}, clear=False):
        p = GitHubPRProvider()
    p._token = ""
    return p

def _mock_response(data, status_code=200, headers=None):
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.json.return_value = data
    resp.headers = headers or {}
    resp.raise_for_status = MagicMock()
    if status_code >= 400:
        resp.raise_for_status.side_effect = httpx.HTTPStatusError("error", request=MagicMock(), response=resp)
    return resp


class TestProviderConfig:
    def test_configured_with_token(self, provider):
        assert provider.configured is True
    def test_not_configured_without_token(self, provider_no_token):
        assert provider_no_token.configured is False
    def test_base_url(self, provider):
        assert provider.base_url == "https://api.github.com/repos/TestOwner/test-repo"


class TestCache:
    def test_cache_entry_fresh(self):
        assert _CacheEntry(data={"test": True}).is_fresh is True
    def test_cache_entry_stale(self):
        entry = _CacheEntry(data={"test": True})
        entry.timestamp = time.time() - CACHE_TTL - 1
        assert entry.is_fresh is False
    def test_invalidate_all(self, provider):
        provider._cache["pulls_list_all"] = _CacheEntry([])
        provider._cache["pulls/52"] = _CacheEntry({})
        provider.invalidate()
        assert len(provider._cache) == 0
    def test_invalidate_specific_pr(self, provider):
        provider._cache["pulls_list_all"] = _CacheEntry([])
        provider._cache["pulls/52"] = _CacheEntry({})
        provider._cache["pulls/52/files"] = _CacheEntry([])
        provider._cache["pulls/47"] = _CacheEntry({})
        provider.invalidate(52)
        assert "pulls/47" in provider._cache
        assert "pulls/52" not in provider._cache
        assert "pulls/52/files" not in provider._cache
        assert "pulls_list_all" not in provider._cache


class TestStatusMapping:
    def test_open_pr(self, provider):
        assert provider._map_pr_status(SAMPLE_PR_OPEN) == PRStatus.REVIEW
    def test_merged_pr(self, provider):
        assert provider._map_pr_status(SAMPLE_PR_MERGED) == PRStatus.MERGED
    def test_draft_pr(self, provider):
        assert provider._map_pr_status(SAMPLE_PR_DRAFT) == PRStatus.DRAFT
    def test_closed_not_merged(self, provider):
        assert provider._map_pr_status({"state": "closed", "merged_at": None, "draft": False}) == PRStatus.CLOSED


class TestPRMapping:
    def test_map_open_pr(self, provider):
        pr = provider._map_pr(SAMPLE_PR_OPEN)
        assert pr.id == "#52" and pr.number == 52 and pr.status == PRStatus.REVIEW
        assert pr.branch == "feat/orchestrator-bridge" and pr.head_sha == "abc123def"
        assert pr.additions == 500 and pr.draft is False and pr.mergeable is True
    def test_map_fork_pr_shows_fork_owner(self, provider):
        pr = provider._map_pr(SAMPLE_PR_FORK)
        assert pr.branch == "contributor:fix-typo" and pr.author == "contributor"
    def test_map_review(self, provider):
        r = provider._map_review(SAMPLE_REVIEW)
        assert r.id == 1001 and r.state == "approved" and r.is_bot is True
    def test_map_comment(self, provider):
        c = provider._map_comment(SAMPLE_REVIEW_COMMENT)
        assert c.id == 3001 and c.path == "src/api/runner.py" and c.line == 42 and c.is_bot is True
    def test_map_file(self, provider):
        f = provider._map_file(SAMPLE_FILE)
        assert f.name == "src/api/runner.py" and f.additions == 300 and f.status == "added" and f.patch != ""
    def test_map_check_run(self, provider):
        c = provider._map_check_run(SAMPLE_CHECK_RUN)
        assert c.name == "tests" and c.conclusion == "success"


class TestReadOperations:
    async def test_list_prs(self, provider):
        mock_client = AsyncMock()
        mock_client.get.return_value = _mock_response([SAMPLE_PR_OPEN, SAMPLE_PR_MERGED], headers={"ETag": '"etag123"'})
        mock_client.is_closed = False
        provider._client = mock_client
        prs = await provider.list_prs()
        assert len(prs) == 2 and prs[0].number == 52 and prs[1].status == PRStatus.MERGED

    async def test_list_prs_uses_cache(self, provider):
        mock_client = AsyncMock()
        mock_client.get.return_value = _mock_response([SAMPLE_PR_OPEN])
        mock_client.is_closed = False
        provider._client = mock_client
        await provider.list_prs()
        await provider.list_prs()
        assert mock_client.get.call_count == 1

    async def test_list_prs_etag_304(self, provider):
        provider._cache["pulls_list_all"] = _CacheEntry([SAMPLE_PR_OPEN], etag='"etag-old"')
        provider._cache["pulls_list_all"].timestamp = 0
        mock_client = AsyncMock()
        resp_304 = _mock_response(None, status_code=304)
        resp_304.raise_for_status = MagicMock()
        mock_client.get.return_value = resp_304
        mock_client.is_closed = False
        provider._client = mock_client
        result = await provider.list_prs()
        assert len(result) == 1 and result[0].number == 52
        assert provider._cache["pulls_list_all"].is_fresh

    async def test_get_pr_files(self, provider):
        mock_client = AsyncMock()
        mock_client.get.return_value = _mock_response([SAMPLE_FILE])
        mock_client.is_closed = False
        provider._client = mock_client
        files = await provider.get_pr_files(52)
        assert len(files) == 1 and files[0].name == "src/api/runner.py"

    async def test_get_pr_reviews(self, provider):
        mock_client = AsyncMock()
        mock_client.get.return_value = _mock_response([SAMPLE_REVIEW])
        mock_client.is_closed = False
        provider._client = mock_client
        reviews = await provider.get_pr_reviews(52)
        assert len(reviews) == 1 and reviews[0].state == "approved" and reviews[0].is_bot is True

    async def test_get_pr_comments_combines_issue_and_review(self, provider):
        mock_client = AsyncMock()
        async def side_effect(url, **kwargs):
            if "/issues/" in url:
                return _mock_response([SAMPLE_ISSUE_COMMENT])
            return _mock_response([SAMPLE_REVIEW_COMMENT])
        mock_client.get = side_effect
        mock_client.is_closed = False
        provider._client = mock_client
        comments = await provider.get_pr_comments(52)
        assert len(comments) == 2 and comments[0].id == 3001 and comments[1].id == 2001

    async def test_get_check_status(self, provider):
        mock_client = AsyncMock()
        mock_client.get.return_value = _mock_response({"total_count": 1, "check_runs": [SAMPLE_CHECK_RUN]})
        mock_client.is_closed = False
        provider._client = mock_client
        checks = await provider.get_check_status("abc123")
        assert len(checks) == 1 and checks[0].conclusion == "success"

    async def test_get_check_status_empty_ref(self, provider):
        assert await provider.get_check_status("") == []


class TestWriteOperations:
    async def test_create_pr(self, provider):
        mock_client = AsyncMock()
        mock_client.post.return_value = _mock_response({**SAMPLE_PR_OPEN, "number": 55, "title": "feat: new thing"}, status_code=201)
        mock_client.is_closed = False
        provider._client = mock_client
        provider._cache["pulls_list_all"] = _CacheEntry([])
        pr = await provider.create_pr("feat/new", "main", "feat: new thing", "body")
        assert pr is not None and pr.number == 55
        assert "pulls_list_all" not in provider._cache

    async def test_post_review(self, provider):
        mock_client = AsyncMock()
        mock_client.post.return_value = _mock_response(SAMPLE_REVIEW, status_code=200)
        mock_client.is_closed = False
        provider._client = mock_client
        review = await provider.post_review(52, "APPROVE", "LGTM")
        assert review is not None and review.state == "approved"

    async def test_add_comment(self, provider):
        mock_client = AsyncMock()
        mock_client.post.return_value = _mock_response(SAMPLE_ISSUE_COMMENT, status_code=201)
        mock_client.is_closed = False
        provider._client = mock_client
        comment = await provider.add_comment(52, "Great work!")
        assert comment is not None and comment.author == "Abernaughty"

    async def test_merge_pr_success(self, provider):
        mock_client = AsyncMock()
        mock_client.put.return_value = _mock_response({"merged": True, "sha": "merged123"})
        mock_client.is_closed = False
        provider._client = mock_client
        assert await provider.merge_pr(52, "squash") is True

    async def test_merge_pr_failure(self, provider):
        mock_client = AsyncMock()
        mock_client.put.return_value = _mock_response({"merged": False, "message": "conflict"})
        mock_client.is_closed = False
        provider._client = mock_client
        assert await provider.merge_pr(52, "squash") is False

    async def test_update_pr(self, provider):
        mock_client = AsyncMock()
        mock_client.patch.return_value = _mock_response({**SAMPLE_PR_OPEN, "title": "new title"})
        mock_client.is_closed = False
        provider._client = mock_client
        pr = await provider.update_pr(52, title="new title")
        assert pr is not None and pr.title == "new title"

    async def test_update_pr_empty_payload_returns_none(self, provider):
        assert await provider.update_pr(52) is None


class TestGracefulDegradation:
    async def test_list_prs_no_token(self, provider_no_token):
        mock_client = AsyncMock()
        mock_client.get.return_value = _mock_response([SAMPLE_PR_OPEN])
        mock_client.is_closed = False
        provider_no_token._client = mock_client
        prs = await provider_no_token.list_prs()
        assert len(prs) == 1

    async def test_write_no_token_returns_none(self, provider_no_token):
        assert await provider_no_token.create_pr("a", "b", "c") is None

    async def test_merge_no_token_returns_false(self, provider_no_token):
        assert await provider_no_token.merge_pr(52) is False

    async def test_api_error_returns_stale_cache(self, provider):
        provider._cache["pulls_list_all"] = _CacheEntry([SAMPLE_PR_OPEN])
        provider._cache["pulls_list_all"].timestamp = 0
        mock_client = AsyncMock()
        mock_client.get.return_value = _mock_response({"message": "server error"}, status_code=500)
        mock_client.is_closed = False
        provider._client = mock_client
        result = await provider.list_prs()
        assert len(result) == 1 and result[0].number == 52

    async def test_api_unreachable_returns_empty(self, provider):
        mock_client = AsyncMock()
        mock_client.get.side_effect = httpx.ConnectError("refused")
        mock_client.is_closed = False
        provider._client = mock_client
        assert await provider.list_prs() == []

    async def test_api_error_on_write_returns_none(self, provider):
        mock_client = AsyncMock()
        mock_client.post.side_effect = httpx.ConnectError("refused")
        mock_client.is_closed = False
        provider._client = mock_client
        assert await provider.create_pr("a", "b", "c") is None


class TestStateManagerDelegation:
    async def test_get_live_prs_delegates(self):
        from src.api.state import StateManager
        sm = StateManager()
        with patch("src.api.github_prs.github_pr_provider") as mock_provider:
            mock_provider.configured = True
            mock_provider.list_prs = AsyncMock(return_value=[PRSummary(id="#52", number=52, title="test", author="user", status=PRStatus.REVIEW, branch="feat/x")])
            result = await sm.get_live_prs()
            assert len(result) == 1 and result[0].number == 52

    async def test_get_live_prs_no_token_returns_empty(self):
        """Without GITHUB_TOKEN, get_live_prs returns an empty list (no mock fallback)."""
        from src.api.state import StateManager
        sm = StateManager()
        with patch("src.api.github_prs.github_pr_provider") as mock_provider:
            mock_provider.configured = False
            result = await sm.get_live_prs()
            assert len(result) == 0


class TestAPIEndpoints:
    @pytest.fixture
    def client(self):
        from fastapi.testclient import TestClient
        from src.api.main import app
        from src.api import state as state_mod, main as main_mod
        from src.api.state import StateManager
        fresh = StateManager()
        state_mod.state_manager = fresh
        main_mod.state_manager = fresh
        return TestClient(app)

    def test_get_prs_endpoint(self, client):
        assert client.get("/prs").status_code == 200
    def test_get_prs_with_state_filter(self, client):
        assert client.get("/prs?state=open").status_code == 200
    def test_get_pr_detail_not_found(self, client):
        assert client.get("/prs/99999").status_code == 404
    def test_create_pr_no_token(self, client):
        with patch("src.api.github_prs.github_pr_provider") as m:
            m.create_pr = AsyncMock(return_value=None)
            assert client.post("/prs", json={"head": "feat/x", "base": "main", "title": "test"}).status_code == 502
    def test_merge_pr_no_token(self, client):
        with patch("src.api.github_prs.github_pr_provider") as m:
            m.merge_pr = AsyncMock(return_value=False)
            assert client.post("/prs/52/merge", json={"method": "squash"}).status_code == 502
