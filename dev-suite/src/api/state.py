"""State manager bridging the LangGraph orchestrator to the API layer.

Provides a singleton that holds current agent/task/memory state and
exposes it to FastAPI routes. Starts with mock data shaped identically
to the real orchestrator models, so the dashboard can develop against
a realistic API surface before live wiring.

Issue #35: Mutation methods now emit SSE events via the EventBus,
so the dashboard receives real-time updates when state changes.
Issue #50: PR methods delegate to GitHubPRProvider for live data.

Usage:
    from src.api.state import state_manager
    agents = state_manager.get_agents()
    tasks = state_manager.get_tasks()
"""

from __future__ import annotations

import logging
import os
import time
import uuid
from datetime import datetime, timezone

from .events import EventType, SSEEvent, event_bus
from .models import (
    AgentInfo,
    AgentStatus,
    BlueprintResponse,
    MemoryEntryResponse,
    MemoryStatus,
    MemoryTierEnum,
    PRFileChange,
    PRStatus,
    PRSummary,
    PRTestResults,
    TaskBudget,
    TaskDetail,
    TaskStatus,
    TaskSummary,
    TimelineEvent,
)

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class StateManager:
    """In-memory state for the API layer.

    Phase 1: Mock data matching the dashboard mockup shape.
    Phase 2: Wire to real LangGraph state, Chroma, and GitHub.
    """

    def __init__(self):
        self._start_time = time.time()
        self._agents = self._init_agents()
        self._tasks: dict[str, TaskDetail] = {}
        self._memory: dict[str, MemoryEntryResponse] = {}
        self._prs: dict[str, PRSummary] = {}

        if os.getenv("API_SEED_MOCK_DATA", "true").lower() == "true":
            self._seed_mock_data()

    # ── Event Emission ──

    async def _emit(self, event_type: EventType, data: dict) -> None:
        try:
            await event_bus.publish(SSEEvent(type=event_type, data=data))
        except Exception:
            logger.debug("Failed to emit %s event", event_type.value, exc_info=True)

    # ── Agent State ──

    def _init_agents(self) -> dict[str, AgentInfo]:
        return {
            "arch": AgentInfo(id="arch", name="Architect", model=os.getenv("ARCHITECT_MODEL", "gemini-3-flash-preview"), status=AgentStatus.IDLE, color="#22d3ee"),
            "dev": AgentInfo(id="dev", name="Lead Dev", model=os.getenv("DEVELOPER_MODEL", "claude-sonnet-4-20250514"), status=AgentStatus.IDLE, color="#a78bfa"),
            "qa": AgentInfo(id="qa", name="QA Agent", model=os.getenv("QA_MODEL", "claude-sonnet-4-20250514"), status=AgentStatus.IDLE, color="#34d399"),
        }

    def get_agents(self) -> list[AgentInfo]:
        return list(self._agents.values())

    async def update_agent_status(self, agent_id: str, status: AgentStatus, task_id: str | None = None) -> None:
        if agent_id in self._agents:
            self._agents[agent_id].status = status
            self._agents[agent_id].current_task_id = task_id
            await self._emit(EventType.AGENT_STATUS, {"agent": agent_id, "status": status.value, "task_id": task_id})

    # ── Task State ──

    def get_tasks(self) -> list[TaskSummary]:
        return [TaskSummary(**t.model_dump(exclude={"blueprint", "generated_code", "error_message"})) for t in self._tasks.values()]

    def get_task(self, task_id: str) -> TaskDetail | None:
        return self._tasks.get(task_id)

    async def create_task(self, description: str) -> str:
        task_id = f"task-{uuid.uuid4().hex[:8]}"
        self._tasks[task_id] = TaskDetail(id=task_id, description=description, status=TaskStatus.QUEUED, created_at=_utcnow())
        logger.info("Task created: %s", task_id)
        await self._emit(EventType.TASK_PROGRESS, {"task_id": task_id, "event": "task_queued", "agent": None, "detail": f"Task queued: {description[:100]}"})
        return task_id

    async def cancel_task(self, task_id: str) -> bool:
        task = self._tasks.get(task_id)
        if not task:
            return False
        if task.status in (TaskStatus.PASSED, TaskStatus.CANCELLED):
            return False
        task.status = TaskStatus.CANCELLED
        task.completed_at = _utcnow()
        await self._emit(EventType.TASK_COMPLETE, {"task_id": task_id, "status": "cancelled", "detail": "Task cancelled by user"})
        return True

    async def retry_task(self, task_id: str) -> bool:
        task = self._tasks.get(task_id)
        if not task:
            return False
        if task.status not in (TaskStatus.FAILED, TaskStatus.ESCALATED):
            return False
        task.status = TaskStatus.QUEUED
        task.completed_at = None
        task.budget.retries_used = 0
        await self._emit(EventType.TASK_PROGRESS, {"task_id": task_id, "event": "task_retried", "agent": None, "detail": "Task re-queued for retry"})
        return True

    # ── Memory State ──

    def get_memory(self, tier: MemoryTierEnum | None = None, status: MemoryStatus | None = None) -> list[MemoryEntryResponse]:
        entries = list(self._memory.values())
        if tier:
            entries = [e for e in entries if e.tier == tier]
        if status:
            entries = [e for e in entries if e.status == status]
        return entries

    def get_memory_entry(self, entry_id: str) -> MemoryEntryResponse | None:
        return self._memory.get(entry_id)

    async def approve_memory(self, entry_id: str) -> MemoryEntryResponse | None:
        entry = self._memory.get(entry_id)
        if not entry:
            return None
        entry.status = MemoryStatus.APPROVED
        entry.verified = True
        entry.expires_at = None
        entry.hours_remaining = None
        await self._emit(EventType.MEMORY_ADDED, {"id": entry_id, "tier": entry.tier.value, "agent": entry.source_agent, "content": entry.content, "status": "approved"})
        return entry

    async def reject_memory(self, entry_id: str) -> MemoryEntryResponse | None:
        entry = self._memory.get(entry_id)
        if not entry:
            return None
        entry.status = MemoryStatus.REJECTED
        await self._emit(EventType.MEMORY_ADDED, {"id": entry_id, "tier": entry.tier.value, "agent": entry.source_agent, "content": entry.content, "status": "rejected"})
        return entry

    # ── PR State ──

    def get_prs(self) -> list[PRSummary]:
        """Return mock PRs (for backward compat). Use async methods for live data."""
        return list(self._prs.values())

    def get_pr(self, pr_id: str) -> PRSummary | None:
        """Return mock PR by id. Use async methods for live data."""
        return self._prs.get(pr_id)

    async def get_live_prs(self, state: str = "all") -> list[PRSummary]:
        """Fetch real PRs from GitHub API. Falls back to mock data."""
        from .github_prs import github_pr_provider
        if not github_pr_provider.configured:
            logger.debug("No GITHUB_TOKEN — returning mock PR data")
            return list(self._prs.values())
        try:
            return await github_pr_provider.list_prs(state=state)
        except Exception:
            logger.warning("GitHub PR fetch failed, returning mock data", exc_info=True)
            return list(self._prs.values())

    async def get_live_pr(self, number: int) -> PRSummary | None:
        """Fetch a single PR with reviews and check status."""
        from .github_prs import github_pr_provider
        if not github_pr_provider.configured:
            return self._prs.get(f"#{number}")
        try:
            return await github_pr_provider.get_pr(number)
        except Exception:
            logger.warning("GitHub PR detail fetch failed", exc_info=True)
            return self._prs.get(f"#{number}")

    async def get_live_pr_files(self, number: int) -> list:
        """Fetch changed files for a PR."""
        from .github_prs import github_pr_provider
        if not github_pr_provider.configured:
            pr = self._prs.get(f"#{number}")
            return pr.files if pr else []
        try:
            return await github_pr_provider.get_pr_files(number)
        except Exception:
            logger.warning("GitHub PR files fetch failed", exc_info=True)
            return []

    async def get_live_pr_reviews(self, number: int) -> list:
        """Fetch reviews for a PR."""
        from .github_prs import github_pr_provider
        try:
            return await github_pr_provider.get_pr_reviews(number)
        except Exception:
            logger.warning("GitHub PR reviews fetch failed", exc_info=True)
            return []

    async def get_live_pr_comments(self, number: int) -> list:
        """Fetch all comments (top-level + inline) for a PR."""
        from .github_prs import github_pr_provider
        try:
            return await github_pr_provider.get_pr_comments(number)
        except Exception:
            logger.warning("GitHub PR comments fetch failed", exc_info=True)
            return []

    async def create_live_pr(self, head: str, base: str, title: str, body: str = "") -> PRSummary | None:
        from .github_prs import github_pr_provider
        return await github_pr_provider.create_pr(head, base, title, body)

    async def post_live_review(self, number: int, event: str, body: str = "", comments: list | None = None):
        from .github_prs import github_pr_provider
        return await github_pr_provider.post_review(number, event, body, comments)

    async def add_live_comment(self, number: int, body: str):
        from .github_prs import github_pr_provider
        return await github_pr_provider.add_comment(number, body)

    async def merge_live_pr(self, number: int, method: str = "squash") -> bool:
        from .github_prs import github_pr_provider
        return await github_pr_provider.merge_pr(number, method)

    # ── Health ──

    def get_uptime(self) -> float:
        return time.time() - self._start_time

    def get_active_task_count(self) -> int:
        active_statuses = {TaskStatus.QUEUED, TaskStatus.PLANNING, TaskStatus.BUILDING, TaskStatus.REVIEWING}
        return sum(1 for t in self._tasks.values() if t.status in active_statuses)

    # ── Mock Data Seeding ──

    def _seed_mock_data(self):
        logger.info("Seeding mock data for API development")
        self._agents["arch"].status = AgentStatus.IDLE
        self._agents["dev"].status = AgentStatus.IDLE
        self._agents["qa"].status = AgentStatus.IDLE

        task_id = "supabase-auth-rls"
        self._tasks[task_id] = TaskDetail(
            id=task_id, description="Set up Supabase auth with RLS for the user_profiles table",
            status=TaskStatus.PASSED,
            created_at=datetime(2026, 3, 25, 14, 32, tzinfo=timezone.utc),
            completed_at=datetime(2026, 3, 25, 14, 37, tzinfo=timezone.utc),
            budget=TaskBudget(tokens_used=38200, token_budget=50000, retries_used=1, max_retries=3, cost_used=0.47, cost_budget=1.00),
            timeline=[
                TimelineEvent(time="14:32", agent="arch", action="Blueprint created for auth-middleware module", type="plan"),
                TimelineEvent(time="14:33", agent="dev", action="Picked up blueprint. Writing auth.js...", type="code"),
                TimelineEvent(time="14:35", agent="dev", action="E2B sandbox spun up. Running npm test...", type="exec"),
                TimelineEvent(time="14:36", agent="qa", action="2 tests failed: session cookie not set on redirect", type="fail"),
                TimelineEvent(time="14:36", agent="dev", action="Retry 1/3 \u2014 applying fix from QA failure report", type="retry"),
                TimelineEvent(time="14:37", agent="dev", action="All 14 tests passing. PR #142 opened.", type="success"),
            ],
            blueprint=BlueprintResponse(
                task_id="supabase-auth-rls",
                target_files=["src/middleware/auth.js", "src/lib/supabase.js", "supabase/migrations/003_rls.sql", "tests/auth.test.js"],
                instructions="Implement cookie-based authentication middleware using supabase-ssr. Create a server client that reads session from cookies, validates it, and redirects unauthenticated users to /login. Add RLS policies to user_profiles table restricting access to authenticated users only.",
                constraints=["Use supabase-ssr createServerClient, NOT the legacy supabase-js createClient", "Sessions must use cookie storage, not localStorage", "RLS policies must cover SELECT, INSERT, and UPDATE operations", "Do not expose SUPABASE_SERVICE_ROLE_KEY to the client"],
                acceptance_criteria=["All 14 existing auth test specs pass", "Unauthenticated requests to /api/* return 302 redirect to /login", "user_profiles table rejects SELECT from unauthenticated roles", "Session refresh works automatically on cookie expiry"],
            ),
            generated_code="// Generated auth middleware code...",
        )

        now = time.time()
        self._memory = {
            "mem-1": MemoryEntryResponse(id="mem-1", content="Supabase auth requires RLS policies on all public tables", tier=MemoryTierEnum.L0_DISCOVERED, module="auth", source_agent="Architect", verified=False, status=MemoryStatus.PENDING, created_at=now - 120, expires_at=now + (47 * 3600 + 58 * 60), hours_remaining=47.97),
            "mem-2": MemoryEntryResponse(id="mem-2", content="auth.js depends on supabase-ssr v0.5+ for cookie-based sessions", tier=MemoryTierEnum.L1, module="auth", source_agent="Lead Dev", verified=True, status=MemoryStatus.PENDING, created_at=now - 480),
            "mem-3": MemoryEntryResponse(id="mem-3", content="Rate limiter middleware must wrap all /api/* routes", tier=MemoryTierEnum.L0_DISCOVERED, module="middleware", source_agent="QA Agent", verified=False, status=MemoryStatus.PENDING, created_at=now - 840, expires_at=now + (47 * 3600 + 46 * 60), hours_remaining=47.77),
        }

        self._prs = {
            "#142": PRSummary(id="#142", title="feat: add Supabase auth middleware", author="Lead Dev", status=PRStatus.REVIEW, branch="feature/supabase-auth", summary="Adds cookie-based auth middleware with automatic session refresh.", additions=187, deletions=23, file_count=4, files=[PRFileChange(name="src/middleware/auth.js", additions=94, deletions=0, status="added"), PRFileChange(name="src/lib/supabase.js", additions=52, deletions=12, status="modified"), PRFileChange(name="supabase/migrations/003_rls.sql", additions=28, deletions=0, status="added"), PRFileChange(name="tests/auth.test.js", additions=13, deletions=11, status="modified")], tests=PRTestResults(passed=14, failed=0, total=14)),
            "#141": PRSummary(id="#141", title="fix: RLS policy for user_profiles", author="Lead Dev", status=PRStatus.MERGED, branch="fix/rls-profiles", summary="Fixes permissive RLS policy.", additions=34, deletions=8, file_count=2, files=[PRFileChange(name="supabase/migrations/002_rls.sql", additions=34, deletions=8, status="modified")], tests=PRTestResults(passed=9, failed=0, total=9)),
        }


state_manager = StateManager()
