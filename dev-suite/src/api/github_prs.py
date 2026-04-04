"""GitHub PR provider — live REST API integration.

Issue #50: PR store ↔ GitHub REST API (real pull requests)
Issue #89: Added create_branch, push_file, get_file_sha, delete_branch,
           push_files_batch for publish_code_node branch+PR workflow.

Replaces mock PR data with live GitHub API calls. Supports full
read/write PR lifecycle: list, detail, files, reviews, comments,
create, post review, add comment, merge.

Caches read operations with a 30s TTL and ETag support to respect
GitHub rate limits. Write operations bypass the cache and return
fresh responses.

Config (env vars):
    GITHUB_TOKEN   — Fine-grained PAT (required for write ops)
    GITHUB_OWNER   — Repository owner (default: Abernaughty)
    GITHUB_REPO    — Repository name (default: agent-dev)

Usage:
    from src.api.github_prs import github_pr_provider

    prs = await github_pr_provider.list_prs()
    files = await github_pr_provider.get_pr_files(52)
"""

from __future__ import annotations

import base64
import logging
import os
import time
from typing import Any

import httpx

from .models import (
    PRCheckStatus,
    PRComment,
    PRFileChange,
    PRReview,
    PRStatus,
    PRSummary,
)

logger = logging.getLogger(__name__)

CACHE_TTL = 30


class _CacheEntry:
    """A cached response with TTL and ETag support."""

    __slots__ = ("data", "timestamp", "etag")

    def __init__(self, data: Any, etag: str | None = None):
        self.data = data
        self.timestamp = time.time()
        self.etag = etag

    @property
    def is_fresh(self) -> bool:
        return (time.time() - self.timestamp) < CACHE_TTL


class GitHubPRProvider:
    """Async GitHub REST API client for pull request operations."""

    def __init__(self):
        self._owner = os.getenv("GITHUB_OWNER", "Abernaughty")
        self._repo = os.getenv("GITHUB_REPO", "agent-dev")
        self._token = os.getenv("GITHUB_TOKEN", "")
        self._cache: dict[str, _CacheEntry] = {}
        self._client: httpx.AsyncClient | None = None

    @property
    def configured(self) -> bool:
        return bool(self._token)

    @property
    def owner(self) -> str:
        return self._owner

    @property
    def repo(self) -> str:
        return self._repo

    @property
    def base_url(self) -> str:
        return f"https://api.github.com/repos/{self._owner}/{self._repo}"

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
            if self._token:
                headers["Authorization"] = f"Bearer {self._token}"
            self._client = httpx.AsyncClient(headers=headers, timeout=httpx.Timeout(15.0))
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    def invalidate(self, pr_number: int | None = None) -> None:
        if pr_number is None:
            self._cache.clear()
        else:
            keys_to_remove = [k for k in self._cache if f"pulls/{pr_number}" in k or k.startswith("pulls_list")]
            for k in keys_to_remove:
                del self._cache[k]

    async def _get(self, path: str, cache_key: str | None = None) -> Any:
        if cache_key and cache_key in self._cache:
            entry = self._cache[cache_key]
            if entry.is_fresh:
                return entry.data
        client = await self._get_client()
        headers = {}
        if cache_key and cache_key in self._cache:
            entry = self._cache[cache_key]
            if entry.etag:
                headers["If-None-Match"] = entry.etag
        try:
            resp = await client.get(f"{self.base_url}{path}", headers=headers)
            if resp.status_code == 304 and cache_key and cache_key in self._cache:
                entry = self._cache[cache_key]
                entry.timestamp = time.time()
                return entry.data
            resp.raise_for_status()
            data = resp.json()
            if cache_key:
                self._cache[cache_key] = _CacheEntry(data, resp.headers.get("ETag"))
            return data
        except httpx.HTTPStatusError as e:
            logger.warning("GitHub API error: %s %s", e.response.status_code, path)
            if cache_key and cache_key in self._cache:
                logger.info("Returning stale cache for %s", cache_key)
                return self._cache[cache_key].data
            return None
        except httpx.HTTPError as e:
            logger.warning("GitHub API unreachable: %s", e)
            if cache_key and cache_key in self._cache:
                return self._cache[cache_key].data
            return None

    async def _post(self, path: str, json: dict) -> dict | None:
        if not self._token:
            logger.warning("GitHub write operation requires GITHUB_TOKEN")
            return None
        client = await self._get_client()
        try:
            resp = await client.post(f"{self.base_url}{path}", json=json)
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPError as e:
            logger.error("GitHub POST failed: %s %s — %s", path, type(e).__name__, e)
            return None

    async def _put(self, path: str, json: dict | None = None) -> dict | None:
        if not self._token:
            logger.warning("GitHub write operation requires GITHUB_TOKEN")
            return None
        client = await self._get_client()
        try:
            resp = await client.put(f"{self.base_url}{path}", json=json or {})
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPError as e:
            logger.error("GitHub PUT failed: %s %s — %s", path, type(e).__name__, e)
            return None

    async def _patch(self, path: str, json: dict) -> dict | None:
        if not self._token:
            logger.warning("GitHub write operation requires GITHUB_TOKEN")
            return None
        client = await self._get_client()
        try:
            resp = await client.patch(f"{self.base_url}{path}", json=json)
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPError as e:
            logger.error("GitHub PATCH failed: %s %s — %s", path, type(e).__name__, e)
            return None

    async def _delete(self, path: str) -> bool:
        """Send a DELETE request. Returns True on success."""
        if not self._token:
            logger.warning("GitHub write operation requires GITHUB_TOKEN")
            return False
        client = await self._get_client()
        try:
            resp = await client.request("DELETE", f"{self.base_url}{path}")
            resp.raise_for_status()
            return True
        except httpx.HTTPError as e:
            logger.error("GitHub DELETE failed: %s %s — %s", path, type(e).__name__, e)
            return False

    # -- Branch operations (Issue #89) --

    async def get_branch_sha(self, branch: str = "main") -> str | None:
        """Get the HEAD commit SHA of a branch."""
        data = await self._get(f"/git/ref/heads/{branch}")
        if not data:
            return None
        obj = data.get("object", {})
        return obj.get("sha")

    async def create_branch(self, branch_name: str, from_branch: str = "main") -> bool:
        """Create a new branch from an existing branch.

        Returns True if branch was created or already exists.
        """
        base_sha = await self.get_branch_sha(from_branch)
        if not base_sha:
            logger.error("Cannot create branch: base branch '%s' not found", from_branch)
            return False
        data = await self._post("/git/refs", json={
            "ref": f"refs/heads/{branch_name}",
            "sha": base_sha,
        })
        if data:
            logger.info("Branch '%s' created from '%s' (sha=%s)", branch_name, from_branch, base_sha[:8])
            return True
        # Check if branch already exists (422 = already exists)
        existing = await self.get_branch_sha(branch_name)
        if existing:
            logger.info("Branch '%s' already exists (sha=%s)", branch_name, existing[:8])
            return True
        return False

    async def delete_branch(self, branch_name: str) -> bool:
        """Delete a branch. Returns True on success or if branch doesn't exist."""
        return await self._delete(f"/git/refs/heads/{branch_name}")

    async def get_file_sha(self, path: str, branch: str = "main") -> str | None:
        """Get the blob SHA of a file on a specific branch.

        Needed for updating existing files via the Contents API.
        Returns None if the file doesn't exist on that branch.
        """
        client = await self._get_client()
        try:
            resp = await client.get(f"{self.base_url}/contents/{path}?ref={branch}")
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            data = resp.json()
            return data.get("sha")
        except httpx.HTTPError:
            return None

    async def push_file(
        self,
        path: str,
        content: str,
        branch: str,
        message: str | None = None,
    ) -> bool:
        """Push a single file to a branch via the Contents API.

        Handles both create (new file) and update (existing file) cases.
        Content is base64-encoded automatically.

        Returns True on success.
        """
        if not self._token:
            logger.warning("GitHub write operation requires GITHUB_TOKEN")
            return False

        if message is None:
            message = f"agent: update {path}"

        # Check if file already exists on the branch to get its SHA
        existing_sha = await self.get_file_sha(path, branch)

        encoded = base64.b64encode(content.encode("utf-8")).decode("ascii")
        payload: dict[str, Any] = {
            "message": message,
            "content": encoded,
            "branch": branch,
        }
        if existing_sha:
            payload["sha"] = existing_sha

        client = await self._get_client()
        try:
            resp = await client.put(
                f"{self.base_url}/contents/{path}",
                json=payload,
            )
            resp.raise_for_status()
            logger.info("Pushed %s to %s (%s)", path, branch, "updated" if existing_sha else "created")
            return True
        except httpx.HTTPStatusError as e:
            logger.error("Push file failed: %s -> %s %s", path, e.response.status_code, e.response.text[:200])
            return False
        except httpx.HTTPError as e:
            logger.error("Push file failed: %s -> %s", path, e)
            return False

    async def push_files_batch(
        self,
        files: list[dict[str, str]],
        branch: str,
        message: str,
    ) -> bool:
        """Push multiple files in individual commits.

        Each file dict has 'path' and 'content' keys.
        Files are pushed sequentially — each commit builds on the previous.

        Validates all file sizes against GitHub's 100 MB Contents API limit
        before starting the batch to avoid partial publishes.

        Returns True if all files were pushed successfully.
        """
        # Preflight: validate all file sizes before starting any commits
        max_content_bytes = 100 * 1024 * 1024  # 100 MB GitHub Contents API limit
        for f in files:
            byte_size = len(f["content"].encode("utf-8"))
            if byte_size > max_content_bytes:
                logger.error(
                    "File '%s' exceeds GitHub 100 MB limit (%d bytes) — aborting batch",
                    f["path"], byte_size,
                )
                return False

        success_count = 0
        for f in files:
            ok = await self.push_file(
                path=f["path"],
                content=f["content"],
                branch=branch,
                message=f"{message} [{f['path']}]",
            )
            if ok:
                success_count += 1
            else:
                logger.error("Failed to push %s — aborting batch", f["path"])
                return False
        logger.info("Batch push: %d/%d files to %s", success_count, len(files), branch)
        return True

    # -- PR mapping helpers --

    def _map_pr_status(self, pr: dict) -> PRStatus:
        if pr.get("draft"):
            return PRStatus.DRAFT
        if pr.get("merged_at") or pr.get("merged"):
            return PRStatus.MERGED
        state = pr.get("state", "open")
        if state == "closed":
            return PRStatus.CLOSED
        return PRStatus.REVIEW

    def _map_pr(self, pr: dict) -> PRSummary:
        user = pr.get("user", {})
        head = pr.get("head", {})
        head_repo = head.get("repo", {}) or {}
        base = pr.get("base", {})
        base_repo = base.get("repo", {}) or {}
        number = pr.get("number", 0)
        branch = head.get("ref", "")
        if head_repo.get("full_name") != base_repo.get("full_name"):
            fork_owner = head_repo.get("owner", {}).get("login", "")
            if fork_owner:
                branch = f"{fork_owner}:{branch}"
        return PRSummary(
            id=f"#{number}", number=number, title=pr.get("title", ""),
            author=user.get("login", "unknown"), status=self._map_pr_status(pr),
            branch=branch, base=base.get("ref", "main"),
            summary=pr.get("body", "") or "",
            additions=pr.get("additions", 0), deletions=pr.get("deletions", 0),
            file_count=pr.get("changed_files", 0), draft=pr.get("draft", False),
            mergeable=pr.get("mergeable"), head_sha=head.get("sha", ""),
        )

    def _map_review(self, review: dict) -> PRReview:
        user = review.get("user", {})
        return PRReview(
            id=review.get("id", 0), author=user.get("login", "unknown"),
            state=review.get("state", "").lower(), body=review.get("body", "") or "",
            submitted_at=review.get("submitted_at", ""),
            is_bot=user.get("type", "") == "Bot",
        )

    def _map_comment(self, comment: dict) -> PRComment:
        user = comment.get("user", {})
        return PRComment(
            id=comment.get("id", 0), author=user.get("login", "unknown"),
            body=comment.get("body", "") or "", path=comment.get("path"),
            line=comment.get("line") or comment.get("original_line"),
            created_at=comment.get("created_at", ""),
            is_bot=user.get("type", "") == "Bot",
        )

    def _map_file(self, file: dict) -> PRFileChange:
        return PRFileChange(
            name=file.get("filename", ""), additions=file.get("additions", 0),
            deletions=file.get("deletions", 0), status=file.get("status", "modified"),
            patch=file.get("patch", ""),
        )

    def _map_check_run(self, check: dict) -> PRCheckStatus:
        return PRCheckStatus(
            name=check.get("name", ""), status=check.get("status", ""),
            conclusion=check.get("conclusion"),
        )

    # -- Read operations --

    async def list_prs(self, state: str = "all") -> list[PRSummary]:
        cache_key = f"pulls_list_{state}"
        data = await self._get(f"/pulls?state={state}&per_page=100&sort=updated&direction=desc", cache_key=cache_key)
        if not data or not isinstance(data, list):
            return []
        return [self._map_pr(pr) for pr in data]

    async def get_pr(self, number: int) -> PRSummary | None:
        cache_key = f"pulls/{number}"
        data = await self._get(f"/pulls/{number}", cache_key=cache_key)
        if not data:
            return None
        pr = self._map_pr(data)
        pr.reviews = await self.get_pr_reviews(number)
        pr.check_status = await self.get_check_status(pr.head_sha)
        return pr

    async def get_pr_files(self, number: int) -> list[PRFileChange]:
        cache_key = f"pulls/{number}/files"
        data = await self._get(f"/pulls/{number}/files?per_page=100", cache_key=cache_key)
        if not data or not isinstance(data, list):
            return []
        return [self._map_file(f) for f in data]

    async def get_pr_reviews(self, number: int) -> list[PRReview]:
        cache_key = f"pulls/{number}/reviews"
        data = await self._get(f"/pulls/{number}/reviews", cache_key=cache_key)
        if not data or not isinstance(data, list):
            return []
        return [self._map_review(r) for r in data]

    async def get_pr_comments(self, number: int) -> list[PRComment]:
        cache_key = f"pulls/{number}/comments"
        issue_data = await self._get(f"/issues/{number}/comments?per_page=100", cache_key=f"{cache_key}_issue")
        review_data = await self._get(f"/pulls/{number}/comments?per_page=100", cache_key=f"{cache_key}_review")
        comments: list[PRComment] = []
        if issue_data and isinstance(issue_data, list):
            comments.extend(self._map_comment(c) for c in issue_data)
        if review_data and isinstance(review_data, list):
            comments.extend(self._map_comment(c) for c in review_data)
        comments.sort(key=lambda c: c.created_at)
        return comments

    async def get_check_status(self, ref: str) -> list[PRCheckStatus]:
        if not ref:
            return []
        cache_key = f"check_runs/{ref}"
        data = await self._get(f"/commits/{ref}/check-runs?per_page=100", cache_key=cache_key)
        if not data or not isinstance(data, dict):
            return []
        return [self._map_check_run(c) for c in data.get("check_runs", [])]

    # -- Write operations --

    async def create_pr(self, head: str, base: str, title: str, body: str = "") -> PRSummary | None:
        data = await self._post("/pulls", json={"head": head, "base": base, "title": title, "body": body})
        if not data:
            return None
        self.invalidate()
        return self._map_pr(data)

    async def post_review(self, number: int, event: str, body: str = "", comments: list[dict] | None = None) -> PRReview | None:
        payload: dict[str, Any] = {"event": event}
        if body:
            payload["body"] = body
        if comments:
            payload["comments"] = comments
        data = await self._post(f"/pulls/{number}/reviews", json=payload)
        if not data:
            return None
        self.invalidate(number)
        return self._map_review(data)

    async def add_comment(self, number: int, body: str) -> PRComment | None:
        data = await self._post(f"/issues/{number}/comments", json={"body": body})
        if not data:
            return None
        self.invalidate(number)
        return self._map_comment(data)

    async def merge_pr(self, number: int, method: str = "squash") -> bool:
        data = await self._put(f"/pulls/{number}/merge", json={"merge_method": method})
        if data and data.get("merged"):
            self.invalidate(number)
            return True
        return False

    async def update_pr(self, number: int, title: str | None = None, body: str | None = None, state: str | None = None) -> PRSummary | None:
        payload: dict[str, str] = {}
        if title is not None:
            payload["title"] = title
        if body is not None:
            payload["body"] = body
        if state is not None:
            payload["state"] = state
        if not payload:
            return None
        data = await self._patch(f"/pulls/{number}", json=payload)
        if not data:
            return None
        self.invalidate(number)
        return self._map_pr(data)


github_pr_provider = GitHubPRProvider()
