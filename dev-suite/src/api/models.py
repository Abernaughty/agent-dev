"""Pydantic models for API request/response schemas.

These define the contract between the FastAPI backend and the SvelteKit
dashboard. All endpoints return an ApiResponse envelope.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field


# ── Envelope ──


class ApiMeta(BaseModel):
    """Metadata included in every API response."""

    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    version: str = "0.2.0"


class ApiResponse(BaseModel):
    """Standard API response envelope.

    All endpoints return this shape so the dashboard can handle
    responses uniformly.
    """

    data: dict | list | None = None
    meta: ApiMeta = Field(default_factory=ApiMeta)
    errors: list[str] = []


# ── Agent Models ──


class AgentStatus(str, Enum):
    IDLE = "idle"
    PLANNING = "planning"
    CODING = "coding"
    REVIEWING = "reviewing"
    WAITING = "waiting"
    ERROR = "error"


class AgentInfo(BaseModel):
    """Agent state exposed to the dashboard."""

    id: str
    name: str
    model: str
    status: AgentStatus = AgentStatus.IDLE
    current_task_id: str | None = None
    color: str = "#64748b"  # Display color for the dashboard


# ── Task Models ──


class TaskStatus(str, Enum):
    QUEUED = "queued"
    PLANNING = "planning"
    BUILDING = "building"
    REVIEWING = "reviewing"
    PASSED = "passed"
    FAILED = "failed"
    ESCALATED = "escalated"
    CANCELLED = "cancelled"


class TimelineEvent(BaseModel):
    """A single event in a task's timeline."""

    time: str
    agent: str
    action: str
    type: str  # plan, code, exec, fail, retry, success
    sandbox: str = "locked"


class BlueprintResponse(BaseModel):
    """Blueprint as returned by the API."""

    task_id: str
    target_files: list[str]
    instructions: str
    constraints: list[str]
    acceptance_criteria: list[str]


class TaskBudget(BaseModel):
    """Token and cost budget tracking."""

    tokens_used: int = 0
    token_budget: int = 50000
    retries_used: int = 0
    max_retries: int = 3
    cost_used: float = 0.0
    cost_budget: float = 1.00


class TaskSummary(BaseModel):
    """Task as returned in list endpoints."""

    id: str
    description: str
    status: TaskStatus
    created_at: datetime
    completed_at: datetime | None = None
    budget: TaskBudget = Field(default_factory=TaskBudget)
    timeline: list[TimelineEvent] = []


class TaskDetail(TaskSummary):
    """Full task detail including blueprint and code."""

    blueprint: BlueprintResponse | None = None
    generated_code: str = ""
    error_message: str = ""


# ── Memory Models ──


class MemoryTierEnum(str, Enum):
    L0_CORE = "l0-core"
    L0_DISCOVERED = "l0-discovered"
    L1 = "l1"
    L2 = "l2"


class MemoryStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class MemoryEntryResponse(BaseModel):
    """Memory entry as returned by the API."""

    id: str
    content: str
    tier: MemoryTierEnum
    module: str = "global"
    source_agent: str = "unknown"
    verified: bool = True
    status: MemoryStatus = MemoryStatus.PENDING
    created_at: float = 0.0
    expires_at: float | None = None
    hours_remaining: float | None = None


class MemoryAction(BaseModel):
    """Request body for memory approve/reject."""

    action: str = Field(..., pattern="^(approve|reject)$")


# ── PR Models ──


class PRStatus(str, Enum):
    OPEN = "open"
    REVIEW = "review"
    MERGED = "merged"
    CLOSED = "closed"


class PRFileChange(BaseModel):
    """A single file changed in a PR."""

    name: str
    additions: int = 0
    deletions: int = 0
    status: str = "modified"  # added, modified, deleted


class PRTestResults(BaseModel):
    """Test results for a PR."""

    passed: int = 0
    failed: int = 0
    total: int = 0


class PRSummary(BaseModel):
    """Pull request as returned by the API."""

    id: str
    title: str
    author: str
    status: PRStatus
    branch: str
    base: str = "main"
    summary: str = ""
    additions: int = 0
    deletions: int = 0
    file_count: int = 0
    files: list[PRFileChange] = []
    tests: PRTestResults = Field(default_factory=PRTestResults)


# ── Task Creation ──


class CreateTaskRequest(BaseModel):
    """Request body for creating a new task."""

    description: str = Field(..., min_length=1, max_length=2000)


class CreateTaskResponse(BaseModel):
    """Response after queuing a new task."""

    task_id: str
    status: TaskStatus = TaskStatus.QUEUED


# ── Health ──


class HealthResponse(BaseModel):
    """Health check response."""

    status: str = "ok"
    version: str = "0.2.0"
    uptime_seconds: float = 0.0
    agents: int = 3
    active_tasks: int = 0
    sse_subscribers: int = 0
