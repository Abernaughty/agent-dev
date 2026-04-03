"""Pydantic models for API request/response schemas.

These define the contract between the FastAPI backend and the SvelteKit
dashboard. All endpoints return an ApiResponse envelope.

Issue #19: Added AuditLogEntry, confidence/sandbox/related_files to MemoryEntryResponse
Issue #89: Added publish_pr to CreateTaskRequest, pr_url/working_branch/pr_number to TaskSummary
Issue #105: Added workspace models, workspace field to CreateTaskRequest/TaskSummary
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field


# -- Envelope --


class ApiMeta(BaseModel):
    """Metadata included in every API response."""

    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    version: str = "0.2.0"


class ApiResponse(BaseModel):
    """Standard API response envelope."""

    data: dict | list | None = None
    meta: ApiMeta = Field(default_factory=ApiMeta)
    errors: list[str] = []


# -- Agent Models --


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
    color: str = "#64748b"


# -- Task Models --


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
    type: str
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
    workspace: str = ""
    # Issue #89: PR publication fields
    pr_url: str | None = None
    pr_number: int | None = None
    working_branch: str | None = None


class TaskDetail(TaskSummary):
    """Full task detail including blueprint and code."""

    blueprint: BlueprintResponse | None = None
    generated_code: str = ""
    error_message: str = ""


# -- Memory Models --


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
    confidence: float = 0.0
    sandbox: str = "locked-down"
    related_files: list[str] = []


class MemoryAction(BaseModel):
    """Request body for memory approve/reject."""

    action: str = Field(..., pattern="^(approve|reject)$")


# -- Audit Log Models --


class AuditAction(str, Enum):
    APPROVE = "approve"
    REJECT = "reject"


class AuditLogEntry(BaseModel):
    """Record of a memory approval/rejection action."""

    id: str
    entry_id: str
    entry_content: str
    entry_tier: str
    entry_module: str
    action: AuditAction
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# -- PR Models --


class PRStatus(str, Enum):
    OPEN = "open"
    REVIEW = "review"
    MERGED = "merged"
    CLOSED = "closed"
    DRAFT = "draft"


class PRFileChange(BaseModel):
    name: str
    additions: int = 0
    deletions: int = 0
    status: str = "modified"
    patch: str = ""


class PRTestResults(BaseModel):
    passed: int = 0
    failed: int = 0
    total: int = 0


class PRReview(BaseModel):
    id: int
    author: str
    state: str = ""
    body: str = ""
    submitted_at: str = ""
    is_bot: bool = False


class PRComment(BaseModel):
    id: int
    author: str
    body: str = ""
    path: str | None = None
    line: int | None = None
    created_at: str = ""
    is_bot: bool = False


class PRCheckStatus(BaseModel):
    name: str
    status: str = ""
    conclusion: str | None = None


class PRSummary(BaseModel):
    id: str
    number: int = 0
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
    draft: bool = False
    mergeable: bool | None = None
    head_sha: str = ""
    reviews: list[PRReview] = []
    check_status: list[PRCheckStatus] = []


class CreatePRRequest(BaseModel):
    head: str = Field(..., min_length=1, description="Source branch")
    base: str = Field(default="main", description="Target branch")
    title: str = Field(..., min_length=1, max_length=256)
    body: str = ""


class PostReviewRequest(BaseModel):
    event: str = Field(..., pattern="^(APPROVE|REQUEST_CHANGES|COMMENT)$")
    body: str = ""
    comments: list[dict] = Field(default_factory=list)


class PostCommentRequest(BaseModel):
    body: str = Field(..., min_length=1)


class MergePRRequest(BaseModel):
    method: str = Field(default="squash", pattern="^(merge|squash|rebase)$")


# -- Task Creation --


class CreateTaskRequest(BaseModel):
    """Request body for creating a new task.

    Issue #89: publish_pr controls whether a PR is opened after QA passes.
    Defaults to True when GITHUB_TOKEN is configured.
    Issue #105: workspace is required. The dashboard pre-fills it with
    WORKSPACE_ROOT, but it must be explicitly sent. For protected
    workspaces, pin must also be provided.
    """

    description: str = Field(..., min_length=1, max_length=2000)
    workspace: str = Field(
        ...,
        min_length=1,
        description="Target workspace directory (absolute path). "
        "Must be in the allowed directories list.",
    )
    pin: str | None = Field(
        default=None,
        description="Admin PIN for protected workspaces. "
        "Required when workspace is protected.",
    )
    publish_pr: bool | None = Field(
        default=None,
        description="Whether to create a branch and open a PR after QA passes. "
        "Defaults to True if GITHUB_TOKEN is configured, False otherwise.",
    )


class CreateTaskResponse(BaseModel):
    task_id: str
    status: TaskStatus = TaskStatus.QUEUED


# -- Workspace Models (Issue #105) --


class WorkspaceInfo(BaseModel):
    """Workspace directory info as returned by GET /workspaces."""

    path: str
    is_default: bool = False
    is_protected: bool = False


class AddWorkspaceRequest(BaseModel):
    """Request body for adding a workspace directory."""

    path: str = Field(..., min_length=1, description="Absolute path to directory")


class VerifyWorkspaceAuthRequest(BaseModel):
    """Request body for verifying protected workspace PIN."""

    workspace: str = Field(..., min_length=1, description="Workspace path or reference")
    pin: str = Field(..., min_length=1, description="Admin PIN")


class VerifyWorkspaceAuthResponse(BaseModel):
    """Response for PIN verification."""

    workspace: str
    authorized: bool
    is_protected: bool


# -- Health --


class HealthResponse(BaseModel):
    status: str = "ok"
    version: str = "0.2.0"
    uptime_seconds: float = 0.0
    agents: int = 3
    active_tasks: int = 0
    sse_subscribers: int = 0
