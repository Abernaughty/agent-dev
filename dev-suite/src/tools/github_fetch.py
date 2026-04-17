"""GitHub issue/PR pre-fetch helpers (issue #193).

Scans a task description for issue/PR references and fetches their
titles + bodies via the GitHub REST API so the Architect has the
context without needing to call tools. Kept intentionally small so
it can be used from `gather_context_node` without spinning up a full
ToolProvider.

Public surface:
    extract_github_refs(text, default_owner, default_repo, max_refs)
        -> list[GitHubRef]
    fetch_issue_or_pr(owner, repo, number, token, max_chars, timeout)
        -> dict | None
    fetch_refs_as_context_items(text, default_owner, default_repo,
                                token, max_refs, max_chars)
        -> list[dict]   # shape matches gathered_context entries

Design notes:
- Uses httpx (already a dependency) to match the existing pattern in
  LocalToolProvider._github_read_diff.
- Best-effort: any failure (missing token, network error, 404) returns
  None for that ref. Callers treat pre-fetch as optional context.
- Issue and PR refs both hit /issues/{n}; the GitHub REST API returns
  PR data from this endpoint as an issue with a pull_request field.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)

# GitHub reference patterns (issue #193).
#
# Matches are produced by two complementary patterns:
#
# 1. Cross-repo: "Abernaughty/agent-dev#113"
#    No qualifying word needed — `owner/repo#N` is GitHub's native
#    auto-link syntax and is already unambiguous.
#
# 2. Same-repo with qualifier: "issue #113", "fixes #42",
#    "closes #7", "refs #99", "see #1", "review #12", "address #8",
#    "gh #5", "PR #113", "pull request #113", "pull #113", "pulls #5".
#
# Intentionally excludes bare `#N` (no qualifying word, no owner/repo
# prefix) to avoid false matches on markdown headings, CSS colors,
# anchor fragments, etc.
# Note: we group the optional suffix explicitly (e.g. `fix(?:e[sd])?`)
# because `fixes?` would expand to "fixe"/"fixes", missing the bare
# "fix". Accepts GitHub's full closing-keyword set: close[sd], fix[e[sd]],
# resolve[sd].
_QUALIFIER = (
    r"issues?|fix(?:e[sd])?|close[sd]?|resolve[sd]?|refs?|"
    r"see|review|address|gh|pulls?|pull\s*request|pr"
)

_CROSS_REPO_PATTERN = re.compile(
    r"(?<![\w/])(?P<owner>[\w.-]+)/(?P<repo>[\w.-]+)"
    r"#(?P<number>\d+)\b",
)

_SAME_REPO_PATTERN = re.compile(
    rf"(?<![\w/])(?:{_QUALIFIER})\s*#(?P<number>\d+)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class GitHubRef:
    """A parsed GitHub issue/PR reference."""

    owner: str
    repo: str
    number: int

    @property
    def key(self) -> tuple[str, str, int]:
        return (self.owner.lower(), self.repo.lower(), self.number)

    @property
    def synthetic_path(self) -> str:
        """A path-like identifier used as the `path` in gathered_context."""
        return f"github://{self.owner}/{self.repo}/issues/{self.number}"


def extract_github_refs(
    text: str,
    default_owner: str,
    default_repo: str,
    max_refs: int = 5,
) -> list[GitHubRef]:
    """Extract unique issue/PR references from free-form text.

    References without an explicit `owner/repo` prefix use the default.
    If default_owner/default_repo are empty, same-repo refs are dropped.

    Cross-repo refs (e.g. "Abernaughty/agent-dev#113") are always kept.

    Returns at most `max_refs` unique refs, preserving first-seen order
    across both patterns (cross-repo and same-repo matches are merged
    by starting offset in `text`).
    """
    if not text:
        return []

    # Gather candidates from both patterns with their start offsets so
    # we can merge in source order.
    candidates: list[tuple[int, GitHubRef]] = []

    for match in _CROSS_REPO_PATTERN.finditer(text):
        try:
            number = int(match.group("number"))
        except ValueError:
            continue
        if number <= 0:
            continue
        candidates.append((
            match.start(),
            GitHubRef(
                owner=match.group("owner"),
                repo=match.group("repo"),
                number=number,
            ),
        ))

    if default_owner and default_repo:
        # Track spans already claimed by cross-repo matches so we don't
        # double-count a ref like "foo/bar#1" as also matching the
        # same-repo pattern via "bar#1".
        cross_spans = [
            (m.start("number"), m.end("number"))
            for m in _CROSS_REPO_PATTERN.finditer(text)
        ]
        for match in _SAME_REPO_PATTERN.finditer(text):
            span = (match.start("number"), match.end("number"))
            if any(s <= span[0] and span[1] <= e for s, e in cross_spans):
                continue
            try:
                number = int(match.group("number"))
            except ValueError:
                continue
            if number <= 0:
                continue
            candidates.append((
                match.start(),
                GitHubRef(
                    owner=default_owner,
                    repo=default_repo,
                    number=number,
                ),
            ))

    # Sort by start offset for deterministic first-seen order.
    candidates.sort(key=lambda pair: pair[0])

    seen: set[tuple[str, str, int]] = set()
    refs: list[GitHubRef] = []
    for _start, ref in candidates:
        if ref.key in seen:
            continue
        seen.add(ref.key)
        refs.append(ref)
        if len(refs) >= max_refs:
            break

    return refs


def _summarize_issue_payload(
    data: dict, max_chars: int | None,
) -> tuple[str, bool]:
    """Build a compact text summary from the GitHub issue/PR JSON.

    When ``max_chars`` is ``None``, the body is passed through intact —
    arbitrary truncation risks cutting acceptance criteria or other
    load-bearing context, so ``None`` is the default for the Planner and
    orchestrator pre-fetch paths. Callers that need a hard cap (tests,
    experimental budgets) can still pass an explicit int.

    Returns (summary_text, truncated_flag).
    """
    number = data.get("number", "?")
    title = (data.get("title") or "").strip()
    state = (data.get("state") or "").strip()
    is_pr = "pull_request" in data
    kind = "PR" if is_pr else "Issue"
    labels = [
        label.get("name", "")
        for label in (data.get("labels") or [])
        if isinstance(label, dict) and label.get("name")
    ]
    body = (data.get("body") or "").strip()

    header_parts = [f"{kind} #{number}: {title}"]
    if state:
        header_parts.append(f"State: {state}")
    if labels:
        header_parts.append(f"Labels: {', '.join(labels)}")
    header = "\n".join(header_parts)

    if not body:
        return header, False

    truncated = False
    if max_chars is not None:
        # Leave room for header + a blank line
        body_budget = max(0, max_chars - len(header) - 2)
        if len(body) > body_budget:
            body = body[:body_budget].rstrip() + "\n... [truncated]"
            truncated = True

    return f"{header}\n\n{body}", truncated


async def fetch_issue_or_pr(
    owner: str,
    repo: str,
    number: int,
    token: str,
    max_chars: int | None = None,
    timeout: float = 10.0,
) -> dict | None:
    """Fetch a single GitHub issue or PR as a gathered_context-shaped dict.

    Returns a dict matching the `gathered_context` entry shape:
        {"path": "github://owner/repo/issues/N",
         "content": "<summary>",
         "truncated": bool,
         "source": "github_issue"}

    Returns None on any failure (missing token, network error, non-200).
    """
    if not token:
        logger.debug("[GH-FETCH] No GITHUB_TOKEN; skipping %s/%s#%d", owner, repo, number)
        return None
    if not owner or not repo:
        return None

    url = f"https://api.github.com/repos/{owner}/{repo}/issues/{number}"
    headers = {
        "Accept": "application/vnd.github.v3+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(url, headers=headers)
    except httpx.HTTPError as exc:
        logger.debug(
            "[GH-FETCH] Network error fetching %s/%s#%d: %s",
            owner, repo, number, exc,
        )
        return None

    if response.status_code != 200:
        logger.debug(
            "[GH-FETCH] %s/%s#%d -> HTTP %d",
            owner, repo, number, response.status_code,
        )
        return None

    try:
        data = response.json()
    except ValueError:
        return None

    if not isinstance(data, dict):
        return None

    summary, truncated = _summarize_issue_payload(data, max_chars=max_chars)
    return {
        "path": f"github://{owner}/{repo}/issues/{number}",
        "content": summary,
        "truncated": truncated,
        "source": "github_issue",
    }


async def fetch_refs_as_context_items(
    text: str,
    default_owner: str,
    default_repo: str,
    token: str,
    max_refs: int = 5,
    max_chars: int | None = None,
) -> list[dict]:
    """Extract and fetch issue/PR refs from text as context entries.

    Returns a (possibly empty) list of gathered_context-shaped dicts,
    skipping any refs that failed to fetch. Best-effort: never raises
    for network/auth errors — the caller can continue without them.
    """
    refs = extract_github_refs(text, default_owner, default_repo, max_refs=max_refs)
    if not refs:
        return []

    items: list[dict] = []
    for ref in refs:
        item = await fetch_issue_or_pr(
            ref.owner, ref.repo, ref.number,
            token=token, max_chars=max_chars,
        )
        if item is not None:
            items.append(item)

    if items:
        logger.info(
            "[GH-FETCH] Pre-fetched %d/%d GitHub ref(s) for gather_context",
            len(items), len(refs),
        )
    return items
