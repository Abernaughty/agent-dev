"""State manager bridging the LangGraph orchestrator to the API layer.

Provides a singleton that holds current agent/task/memory state and
exposes it to FastAPI routes. State starts empty and is populated by
real orchestrator runs, Chroma queries, and GitHub API calls.

Issue #35: Mutation methods now emit SSE events via the EventBus,
so the dashboard receives real-time updates when state changes.
Issue #50: PR methods delegate to GitHubPRProvider for live data.
Issue #51: Removed mock data seeding -- always starts with clean state.
Issue #19: Added audit log for memory approve/reject actions.
Issue #92: Added add_memory_entry() to bridge flush_memory -> dashboard.
Issue #105: Added WorkspaceManager singleton, workspace-aware task creation.

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

# CRITICAL: load_dotenv() MUST be called before WorkspaceManager.from_env()
# and before _init_agents() reads model env vars. The state_manager singleton
# is constructed at module import time (bottom of this file), which happens
# BEFORE main.py's load_dotenv() call. Without this, os.getenv() falls back
# to defaults and ignores the user's .env settings entirely.
# This was the root cause of the WORKSPACE_ROOT=dev-suite bug.
from dotenv import load_dotenv

load_dotenv()

from ..workspace import WorkspaceManager
from .events import EventType, SSEEvent, event_bus
from .models import (
    AgentInfo,
    AgentStatus,
    AuditAction,
    AuditLogEntry,
    MemoryEntryResponse,
    MemoryStatus,
    MemoryTierEnum,
    PRSummary,
    TaskDetail,
    TaskStatus,
    TaskSummary,
)

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class StateManager:
    """In-memory state for the API layer.

    State starts empty. Populated by real orchestrator runs,
    Chroma memory queries, and GitHub API calls.

    Issue #105: Now holds a WorkspaceManager singleton for workspace
    security validation.
    """

    def __init__(self):
        self._start_time = time.time()
        self._agents = self._init_agents()
        self._tasks: dict[str, TaskDetail] = {}
        self._memory: dict[str, MemoryEntryResponse] = {}
        self._prs: dict[str, PRSummary] = {}
        self._audit_log: list[AuditLogEntry] = []
        # Issue #105: Workspace security manager
        self._workspace_manager = WorkspaceManager.from_env()
        logger.info(
            "WorkspaceManager initialized: %d allowed dirs, default=%s",
            len(self._workspace_manager.list_directories()),
            self._workspace_manager.default_root,
        )

    @property
    def workspace_manager(self) -> WorkspaceManager:
        """Access the workspace security manager."""
        return self._workspace_manager

    # -- Event Emission --

    async def _emit(self, event_type: EventType, data: dict) -> None:
        try:
            await event_bus.publish(SSEEvent(type=event_type, data=data))
        except Exception:
            logger.debug("Failed to emit %s event", event_type.value, exc_info=True)

    # -- Agent State --

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

    # -- Task State --

    def get_tasks(self) -> list[TaskSummary]:
        return [TaskSummary(**t.model_dump(exclude={"blueprint", "generated_code", "error_message"})) for t in self._tasks.values()]

    def get_task(self, task_id: str) -> TaskDetail | None:
        return self._tasks.get(task_id)

    async def create_task(self, description: str, workspace: str = "") -> str:
        """Create a new task.

        Issue #105: Now accepts workspace parameter, stored on task detail.
        """
        task_id = f"task-{uuid.uuid4().hex[:8]}"
        task = TaskDetail(
            id=task_id,
            description=description,
            status=TaskStatus.QUEUED,
            created_at=_utcnow(),
            workspace=workspace or str(self._workspace_manager.default_root),
        )
        self._tasks[task_id] = task
        logger.info("Task created: %s (workspace=%s)", task_id, task.workspace)
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

    # -- Memory State --

    def get_memory(self, tier: MemoryTierEnum | None = None, status: MemoryStatus | None = None) -> list[MemoryEntryResponse]:
        entries = list(self._memory.values())
        if tier:
            entries = [e for e in entries if e.tier == tier]
        if status:
            entries = [e for e in entries if e.status == status]
        return entries

    def get_memory_entry(self, entry_id: str) -> MemoryEntryResponse | None:
        return self._memory.get(entry_id)

    def _record_audit(self, entry: MemoryEntryResponse, action: AuditAction) -> None:
        """Record an approve/reject action in the audit log."""
        self._audit_log.append(AuditLogEntry(
            id=f"audit-{uuid.uuid4().hex[:8]}",
            entry_id=entry.id,
            entry_content=entry.content,
            entry_tier=entry.tier.value,
            entry_module=entry.module,
            action=action,
        ))

    async def add_memory_entry(
        self,
        content: str,
        tier: str = "l1",
        module: str = "global",
        source_agent: str = "unknown",
        confidence: float = 0.0,
        sandbox_origin: str = "locked-down",
        related_files: str = "",
        task_id: str = "",
    ) -> MemoryEntryResponse:
        """Create a memory entry from flush_memory output and emit SSE event.

        Issue #92: Bridges flush_memory (which writes to Chroma) into the
        StateManager's in-memory store so GET /memory returns entries and
        the dashboard receives memory_added SSE events.
        """
        entry_id = f"mem-{uuid.uuid4().hex[:8]}"

        # Parse related_files: may be comma-separated string or already a list
        if isinstance(related_files, str):
            files_list = [f.strip() for f in related_files.split(",") if f.strip()] if related_files else []
        else:
            files_list = list(related_files)

        # Map tier string to enum
        tier_enum = MemoryTierEnum(tier) if tier in [t.value for t in MemoryTierEnum] else MemoryTierEnum.L1

        # Calculate expiry for L0-Discovered (48h from now)
        now_ts = time.time()
        if tier_enum == MemoryTierEnum.L0_DISCOVERED:
            expires_at = now_ts + (48 * 3600)
            hours_remaining = 48.0
        else:
            expires_at = None
            hours_remaining = None

        entry = MemoryEntryResponse(
            id=entry_id,
            content=content,
            tier=tier_enum,
            module=module,
            source_agent=source_agent,
            verified=False,
            status=MemoryStatus.PENDING,
            created_at=now_ts,
            expires_at=expires_at,
            hours_remaining=hours_remaining,
            confidence=confidence,
            sandbox=sandbox_origin,
            related_files=files_list,
        )

        self._memory[entry_id] = entry
        logger.info("Memory entry added: %s (tier=%s, agent=%s)", entry_id, tier, source_agent)

        # Emit SSE event with full entry data so the dashboard can upsert
        await self._emit(EventType.MEMORY_ADDED, {
            "id": entry_id,
            "tier": tier_enum.value,
            "agent": source_agent,
            "content": content,
            "status": "pending",
            "module": module,
            "confidence": confidence,
            "sandbox": sandbox_origin,
            "related_files": files_list,
            "expires_at": expires_at,
            "hours_remaining": hours_remaining,
        })

        return entry

    async def approve_memory(self, entry_id: str) -> MemoryEntryResponse | None:
        entry = self._memory.get(entry_id)
        if not entry:
            return None
        entry.status = MemoryStatus.APPROVED
        entry.verified = True
        entry.expires_at = None
        entry.hours_remaining = None
        self._record_audit(entry, AuditAction.APPROVE)
        await self._emit(EventType.MEMORY_ADDED, {"id": entry_id, "tier": entry.tier.value, "agent": entry.source_agent, "content": entry.content, "status": "approved"})
        return entry

    async def reject_memory(self, entry_id: str) -> MemoryEntryResponse | None:
        entry = self._memory.get(entry_id)
        if not entry:
            return None
        entry.status = MemoryStatus.REJECTED
        self._record_audit(entry, AuditAction.REJECT)
        await self._emit(EventType.MEMORY_ADDED, {"id": entry_id, "tier": entry.tier.value, "agent": entry.source_agent, "content": entry.content, "status": "rejected"})
        return entry

    # -- Audit Log --

    def get_audit_log(self, limit: int = 100) -> list[AuditLogEntry]:
        """Return most recent audit entries, newest first."""
        return list(reversed(self._audit_log[-limit:]))

    # -- PR State --

    def get_prs(self) -> list[PRSummary]:
        """Return in-memory PRs (for backward compat). Use async methods for live data."""
        return list(self._prs.values())

    def get_pr(self, pr_id: str) -> PRSummary | None:
        """Return in-memory PR by id. Use async methods for live data."""
        return self._prs.get(pr_id)

    async def get_live_prs(self, state: str = "all") -> list[PRSummary]:
        """Fetch real PRs from GitHub API. Returns empty list if unavailable."""
        from .github_prs import github_pr_provider
        if not github_pr_provider.configured:
            logger.debug("No GITHUB_TOKEN -- returning empty PR list")
            return []
        try:
            return await github_pr_provider.list_prs(state=state)
        except Exception:
            logger.warning("GitHub PR fetch failed, returning empty list", exc_info=True)
            return []

    async def get_live_pr(self, number: int) -> PRSummary | None:
        """Fetch a single PR with reviews and check status."""
        from .github_prs import github_pr_provider
        if not github_pr_provider.configured:
            return None
        try:
            return await github_pr_provider.get_pr(number)
        except Exception:
            logger.warning("GitHub PR detail fetch failed", exc_info=True)
            return None

    async def get_live_pr_files(self, number: int) -> list:
        """Fetch changed files for a PR."""
        from .github_prs import github_pr_provider
        if not github_pr_provider.configured:
            return []
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

    # -- Health --

    def get_uptime(self) -> float:
        return time.time() - self._start_time

    def get_active_task_count(self) -> int:
        active_statuses = {TaskStatus.QUEUED, TaskStatus.PLANNING, TaskStatus.BUILDING, TaskStatus.REVIEWING}
        return sum(1 for t in self._tasks.values() if t.status in active_statuses)


state_manager = StateManager()
