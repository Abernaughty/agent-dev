"""FastAPI application exposing orchestrator state to the dashboard.

Issue #34: FastAPI Bootstrap -- API Layer for Orchestrator
Issue #35: SSE Event System -- Real-Time Task Streaming
Issue #50: Full GitHub PR endpoints (read + write)
Issue #51: Removed mock_data from startup log
Issue #19: Memory audit log endpoint
Issue #105: Workspace security endpoints + workspace-aware task creation
Issue #106: Filesystem browse endpoint + Planner session endpoints

Run with:
    uv run --group api uvicorn src.api.main:app --reload --port 8000 \\
        --reload-exclude .venv --reload-exclude workspace \\
        --reload-exclude __pycache__ --reload-exclude chroma_data \\
        --reload-exclude node_modules --reload-exclude '*.pyc'

IMPORTANT: The --reload-exclude flags are critical. Without them, agent
file writes to the workspace (or SDK imports touching .venv/) will
trigger a server restart, killing active tasks and severing SSE streams.
See: ERR_INCOMPLETE_CHUNKED_ENCODING / "ASGI callable returned without
completing response" in uvicorn logs.
"""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import Body, Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel as PydanticBaseModel
from sse_starlette import EventSourceResponse

from .auth import require_auth
from .events import EventType, SSEEvent, event_bus
from .models import (
    AddWorkspaceRequest,
    ApiResponse,
    BrowseDirectoryEntry,
    BrowseDirectoryResponse,
    CreatePRRequest,
    CreateTaskRequest,
    CreateTaskResponse,
    HealthResponse,
    MemoryAction,
    MemoryStatus,
    MemoryTierEnum,
    MergePRRequest,
    PlannerChecklistItemResponse,
    PlannerChecklistResponse,
    PlannerMessageRequest,
    PlannerSessionResponse,
    PlannerStartRequest,
    PlannerSubmitResponse,
    PlannerTaskSpecResponse,
    PostCommentRequest,
    PostReviewRequest,
    VerifyWorkspaceAuthRequest,
    VerifyWorkspaceAuthResponse,
    WorkspaceInfo,
)
from .state import state_manager

# override=True so .env beats any stale value pre-set in the parent shell
# (common failure mode: a prior key cached in a Windows User env var silently
# overrides .env and produces opaque 401s).
load_dotenv(override=True)

logger = logging.getLogger(__name__)

SSE_HEARTBEAT_SECONDS = 15


@asynccontextmanager
async def lifespan(app: FastAPI):
    secret_set = bool(os.getenv("API_SECRET"))
    ws_mgr = state_manager.workspace_manager
    ws_count = len(ws_mgr.list_directories())
    ws_root = ws_mgr.default_root
    # Issue #153: clean up stale remote workspaces on startup
    from ..github_workspace import cleanup_stale_workspaces

    stale_count = cleanup_stale_workspaces(max_age_hours=24)
    if stale_count:
        logger.info("Cleaned up %d stale remote workspace(s)", stale_count)

    logger.info(
        "Dev Suite API starting | auth=%s | cors=%s | workspaces=%d | workspace_root=%s",
        "enabled" if secret_set else "disabled (dev mode)",
        _allowed_origins,
        ws_count,
        ws_root,
    )
    yield
    from .runner import task_runner
    logger.info("Dev Suite API shutting down | running_tasks=%d | sse_subscribers=%d | events_published=%d", task_runner.running_count, event_bus.subscriber_count, event_bus.event_counter)
    await task_runner.shutdown()
    await event_bus.clear()
    from .github_prs import github_pr_provider
    await github_pr_provider.close()


_allowed_origins = os.getenv("CORS_ORIGINS", "http://localhost:5173,http://localhost:4173").split(",")

app = FastAPI(title="Dev Suite API", description="Exposes orchestrator state to the SvelteKit dashboard", version="0.3.0", docs_url="/docs", redoc_url=None, lifespan=lifespan)

app.add_middleware(CORSMiddleware, allow_origins=[o.strip() for o in _allowed_origins], allow_credentials=True, allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"], allow_headers=["Authorization", "Content-Type"])


def _ok(data) -> ApiResponse:
    if isinstance(data, list):
        serialized = [d.model_dump() if hasattr(d, "model_dump") else d for d in data]
    elif hasattr(data, "model_dump"):
        serialized = data.model_dump()
    else:
        serialized = data
    return ApiResponse(data=serialized)


def _error(message: str, status: int = 400) -> None:
    raise HTTPException(status_code=status, detail=message)


@app.get("/health", response_model=HealthResponse)
async def health():
    return HealthResponse(uptime_seconds=state_manager.get_uptime(), active_tasks=state_manager.get_active_task_count(), sse_subscribers=event_bus.subscriber_count)


@app.get("/stream")
async def stream(request: Request, _auth: str | None = Depends(require_auth)):
    return EventSourceResponse(_sse_generator(request), media_type="text/event-stream")


async def _sse_generator(request: Request):
    queue = await event_bus.subscribe()
    event_id = event_bus.event_counter
    logger.info("SSE client connected (subscribers: %d)", event_bus.subscriber_count)
    try:
        while True:
            if await request.is_disconnected():
                break
            try:
                event: SSEEvent = await asyncio.wait_for(queue.get(), timeout=SSE_HEARTBEAT_SECONDS)
                event_id += 1
                yield {"event": event.type.value, "data": event.model_dump_json(), "id": str(event_id)}
            except asyncio.TimeoutError:
                yield {"comment": "keepalive"}
    except asyncio.CancelledError:
        logger.debug("SSE generator cancelled")
    finally:
        await event_bus.unsubscribe(queue)
        logger.info("SSE client cleaned up (subscribers: %d)", event_bus.subscriber_count)


# -- Agents --

@app.get("/agents", response_model=ApiResponse)
async def get_agents(_auth: str | None = Depends(require_auth)):
    return _ok(state_manager.get_agents())


# -- Tasks --

@app.get("/tasks", response_model=ApiResponse)
async def get_tasks(_auth: str | None = Depends(require_auth)):
    return _ok(state_manager.get_tasks())


@app.get("/tasks/{task_id}", response_model=ApiResponse)
async def get_task(task_id: str, _auth: str | None = Depends(require_auth)):
    task = state_manager.get_task(task_id)
    if not task:
        _error(f"Task '{task_id}' not found", 404)
    return _ok(task)


@app.post("/tasks", response_model=ApiResponse, status_code=201)
async def create_task(body: CreateTaskRequest, _auth: str | None = Depends(require_auth)):
    """Create a new task with workspace validation (Issue #105, #153).

    For local workspaces: validates the directory is in the allowed list
    and verifies the PIN if the workspace is protected.
    For GitHub workspaces: validates the token can access the repo.
    """
    from .runner import task_runner

    ws_mgr = state_manager.workspace_manager

    if body.workspace_type == "github":
        # Issue #153: validate GitHub token can access the target repo
        from ..github_workspace import validate_github_token_async

        token_ok = await validate_github_token_async(body.github_repo)  # type: ignore[arg-type]
        if not token_ok:
            _error(
                f"GITHUB_TOKEN cannot access repository '{body.github_repo}'. "
                f"Verify the token has 'Contents' and 'Pull requests' scopes.",
                403,
            )
    else:
        # Local workspace validation (existing behaviour)
        if not ws_mgr.is_allowed(body.workspace):
            _error(
                f"Workspace '{body.workspace}' is not in the allowed directories list. "
                f"Add it via POST /workspaces first.",
                403,
            )
        if ws_mgr.is_protected(body.workspace):
            if not body.pin:
                _error(
                    f"Workspace '{body.workspace}' is protected. "
                    f"Provide 'pin' field for authorization.",
                    403,
                )
            if not ws_mgr.verify_pin(body.pin):
                _error("Invalid PIN for protected workspace.", 403)

    task_id = await state_manager.create_task(body.description, workspace=body.workspace)
    task_runner.submit(
        task_id,
        body.description,
        workspace=body.workspace,
        create_pr=body.create_pr,
        workspace_type=body.workspace_type,
        github_repo=body.github_repo,
        github_branch=body.github_branch,
        github_feature_branch=body.github_feature_branch,
    )
    return _ok(CreateTaskResponse(task_id=task_id))


@app.post("/tasks/{task_id}/cancel", response_model=ApiResponse)
async def cancel_task(task_id: str, _auth: str | None = Depends(require_auth)):
    if not await state_manager.cancel_task(task_id):
        task = state_manager.get_task(task_id)
        if not task:
            _error(f"Task '{task_id}' not found", 404)
        else:
            _error(f"Task '{task_id}' cannot be cancelled (status: {task.status})", 409)
    return _ok({"task_id": task_id, "status": "cancelled"})


@app.post("/tasks/{task_id}/retry", response_model=ApiResponse)
async def retry_task(task_id: str, _auth: str | None = Depends(require_auth)):
    """Retry a failed task. Re-validates workspace protection status."""
    if not await state_manager.retry_task(task_id):
        task = state_manager.get_task(task_id)
        if not task:
            _error(f"Task '{task_id}' not found", 404)
        else:
            _error(f"Task '{task_id}' is not in a retryable state (status: {task.status})", 400)
        return _ok({"task_id": task_id, "status": task.status if task else "not_found"})

    from .runner import task_runner
    task = state_manager.get_task(task_id)
    if task:
        # Re-validate protected workspace on retry
        ws_mgr = state_manager.workspace_manager
        if task.workspace and ws_mgr.is_protected(task.workspace):
            _error(
                f"Workspace '{task.workspace}' is now protected. "
                f"Create a new task with PIN authorization instead of retrying.",
                403,
            )
        task_runner.submit(task_id, task.description, workspace=task.workspace)
    return _ok({"task_id": task_id, "status": "queued"})


# -- Workspaces (Issue #105) --

@app.get("/workspaces", response_model=ApiResponse)
async def get_workspaces(_auth: str | None = Depends(require_auth)):
    """List all allowed workspace directories."""
    ws_mgr = state_manager.workspace_manager
    dirs = ws_mgr.list_directories()
    return _ok([WorkspaceInfo(**d) for d in dirs])


@app.post("/workspaces", response_model=ApiResponse, status_code=201)
async def add_workspace(body: AddWorkspaceRequest, _auth: str | None = Depends(require_auth)):
    """Add a directory to the allowed workspaces list."""
    ws_mgr = state_manager.workspace_manager
    if ws_mgr.add_directory(body.path):
        dirs = ws_mgr.list_directories()
        return _ok([WorkspaceInfo(**d) for d in dirs])
    _error(f"Cannot add workspace '{body.path}' -- does not exist or already added")


@app.delete("/workspaces", response_model=ApiResponse)
async def remove_workspace(path: str = Query(..., min_length=1, description="Absolute path to directory"), _auth: str | None = Depends(require_auth)):
    """Remove a directory from the allowed workspaces list."""
    ws_mgr = state_manager.workspace_manager
    if ws_mgr.remove_directory(path):
        dirs = ws_mgr.list_directories()
        return _ok([WorkspaceInfo(**d) for d in dirs])
    _error(f"Cannot remove workspace '{path}' -- not found or is the default root")


@app.post("/workspaces/verify-auth", response_model=ApiResponse)
async def verify_workspace_auth(body: VerifyWorkspaceAuthRequest, _auth: str | None = Depends(require_auth)):
    """Verify PIN for a protected workspace."""
    ws_mgr = state_manager.workspace_manager
    is_protected = ws_mgr.is_protected(body.workspace)
    authorized = False
    if is_protected:
        authorized = ws_mgr.verify_pin(body.pin)
    else:
        authorized = True
    return _ok(VerifyWorkspaceAuthResponse(
        workspace=body.workspace,
        authorized=authorized,
        is_protected=is_protected,
    ))


# -- Planner (Issue #106 Phase B) --

@app.post("/tasks/plan", response_model=ApiResponse, status_code=201)
async def start_planner_session(
    body: PlannerStartRequest,
    _auth: str | None = Depends(require_auth),
):
    """Start a new Planner conversation session.

    Validates workspace, runs auto-inference on project files,
    and creates a session with pre-populated languages/frameworks.
    Returns the session ID and initial checklist state.
    """
    from ..agents.planner import (
        create_planner_session,
        infer_workspace_stack,
        planner_sessions,
    )

    ws_mgr = state_manager.workspace_manager

    # Validate workspace
    if not ws_mgr.is_allowed(body.workspace):
        _error(
            f"Workspace '{body.workspace}' is not in the allowed directories list.",
            403,
        )

    # Protected workspace check
    if ws_mgr.is_protected(body.workspace):
        if not body.pin:
            _error(
                f"Workspace '{body.workspace}' is protected. Provide 'pin'.",
                403,
            )
        if not ws_mgr.verify_pin(body.pin):
            _error("Invalid PIN for protected workspace.", 403)

    # Auto-infer languages/frameworks
    try:
        workspace_path = ws_mgr.resolve_workspace(body.workspace)
        stack = infer_workspace_stack(workspace_path)
    except (ValueError, OSError) as e:
        logger.warning("Auto-inference failed for %s: %s", body.workspace, e)
        stack = {"languages": [], "frameworks": []}

    # Create session
    session = create_planner_session(
        workspace=body.workspace,
        languages=stack["languages"],
        frameworks=stack["frameworks"],
    )
    planner_sessions.create(session)

    # Build response
    resp = _planner_session_to_response(session)
    resp.message = session.messages[0].content if session.messages else ""

    logger.info(
        "Planner session started: %s (workspace=%s, languages=%s, frameworks=%s)",
        session.session_id,
        body.workspace,
        stack["languages"],
        stack["frameworks"],
    )

    return _ok(resp)


@app.post("/tasks/plan/{session_id}/message", response_model=ApiResponse)
async def send_planner_msg(
    session_id: str,
    body: PlannerMessageRequest,
    _auth: str | None = Depends(require_auth),
):
    """Send a message to the Planner agent in an existing session."""
    from ..agents.planner import planner_sessions, send_planner_message

    session = planner_sessions.get(session_id)
    if not session:
        _error(f"Planner session '{session_id}' not found or expired.", 404)
        return

    if session.submitted:
        _error("Session already submitted. Start a new session.", 409)
        return

    try:
        planner_resp = await send_planner_message(session, body.message)
    except Exception as e:
        logger.error("Planner LLM call failed: %s", e, exc_info=True)
        _error(f"Planner failed: {e}", 502)
        return

    # Emit SSE event for dashboard
    await event_bus.publish(SSEEvent(
        type=EventType.PLANNER_MESSAGE,
        data={
            "session_id": session_id,
            "message": planner_resp.message,
            "ready": planner_resp.ready,
            "warnings": planner_resp.warnings,
        },
    ))

    # Build response
    resp = _planner_session_to_response(session)
    resp.message = planner_resp.message
    resp.ready = planner_resp.ready
    resp.warnings = planner_resp.warnings

    return _ok(resp)


class PlannerSubmitRequest(PydanticBaseModel):
    """Optional body for planner submit with workspace/PR overrides."""
    create_pr: bool | None = None
    workspace_type: str = "local"
    github_repo: str | None = None
    github_branch: str | None = None
    github_feature_branch: str | None = None


@app.post("/tasks/plan/{session_id}/submit", response_model=ApiResponse)
async def submit_planner_session(
    session_id: str,
    body: PlannerSubmitRequest | None = Body(None),
    _auth: str | None = Depends(require_auth),
):
    """Submit a Planner session to the Architect.

    Converts the TaskSpec into a task description and creates a task
    via the normal POST /tasks flow. The Planner session is marked
    as submitted and cannot be reused.

    Re-validates workspace policy at submit time to prevent TOCTOU:
    a workspace removed or newly protected after session start will
    be caught here.
    """
    from ..agents.planner import build_checklist, planner_sessions
    from .runner import task_runner

    session = planner_sessions.get(session_id)
    if not session:
        _error(f"Planner session '{session_id}' not found or expired.", 404)
        return

    if session.submitted:
        _error("Session already submitted.", 409)
        return

    # Re-validate checklist
    checklist = build_checklist(session.task_spec)
    if not checklist.required_satisfied:
        missing = checklist.missing_required
        _error(
            f"Task is not ready. Missing required fields: {', '.join(missing)}",
            422,
        )
        return

    # Build description from TaskSpec
    description = session.task_spec.to_description()
    workspace = session.task_spec.workspace

    # Re-validate workspace policy (TOCTOU guard: workspace may have been
    # removed or newly protected since the session was started).
    ws_mgr = state_manager.workspace_manager
    if not ws_mgr.is_allowed(workspace):
        _error(
            f"Workspace '{workspace}' is no longer in the allowed directories list. "
            f"Start a new planner session.",
            403,
        )
        return
    if ws_mgr.is_protected(workspace):
        _error(
            f"Workspace '{workspace}' now requires re-authorization. "
            f"Start a new planner session with a PIN.",
            403,
        )
        return

    # Create task via existing flow
    task_id = await state_manager.create_task(description, workspace=workspace)
    task_runner.submit(
        task_id,
        description,
        workspace=workspace,
        create_pr=body.create_pr if body else None,
        workspace_type=body.workspace_type if body else "local",
        github_repo=body.github_repo if body else None,
        github_branch=body.github_branch if body else None,
        github_feature_branch=body.github_feature_branch if body else None,
        # Issue #193: Planner pre-fetched GitHub summaries flow into
        # the orchestrator so `gather_context_node` can reuse them
        # instead of refetching.
        prefetched_gathered_context=list(session.task_spec.github_context),
    )

    # Mark session as submitted
    session.submitted = True

    logger.info(
        "Planner session %s submitted as task %s (workspace=%s)",
        session_id,
        task_id,
        workspace,
    )

    return _ok(PlannerSubmitResponse(
        session_id=session_id,
        task_id=task_id,
        description=description,
    ))


def _planner_session_to_response(session) -> PlannerSessionResponse:
    """Convert internal PlannerSession to API response model."""
    from ..agents.planner import build_checklist

    checklist = build_checklist(session.task_spec)

    return PlannerSessionResponse(
        session_id=session.session_id,
        task_spec=PlannerTaskSpecResponse(
            workspace=session.task_spec.workspace,
            objective=session.task_spec.objective,
            languages=session.task_spec.languages,
            frameworks=session.task_spec.frameworks,
            output_type=session.task_spec.output_type,
            acceptance_criteria=session.task_spec.acceptance_criteria,
            constraints=session.task_spec.constraints,
            related_files=session.task_spec.related_files,
        ),
        checklist=PlannerChecklistResponse(
            items=[
                PlannerChecklistItemResponse(
                    field=item.field,
                    priority=item.priority.value,
                    satisfied=item.satisfied,
                    auto_inferred=item.auto_inferred,
                    value=item.value,
                )
                for item in checklist.items
            ],
            required_satisfied=checklist.required_satisfied,
            has_warnings=checklist.has_warnings,
            missing_required=checklist.missing_required,
            missing_recommended=checklist.missing_recommended,
        ),
    )


# -- Memory --

@app.get("/memory", response_model=ApiResponse)
async def get_memory(tier: MemoryTierEnum | None = Query(None), status: MemoryStatus | None = Query(None), _auth: str | None = Depends(require_auth)):
    return _ok(state_manager.get_memory(tier=tier, status=status))


@app.patch("/memory/{entry_id}", response_model=ApiResponse)
async def update_memory(entry_id: str, body: MemoryAction, _auth: str | None = Depends(require_auth)):
    if body.action == "approve":
        entry = await state_manager.approve_memory(entry_id)
    elif body.action == "reject":
        entry = await state_manager.reject_memory(entry_id)
    else:
        _error(f"Invalid action: {body.action}")
        return
    if not entry:
        _error(f"Memory entry '{entry_id}' not found", 404)
    return _ok(entry)


# -- Memory Audit Log --

@app.get("/memory/audit", response_model=ApiResponse)
async def get_memory_audit(limit: int = Query(100, ge=1, le=1000), _auth: str | None = Depends(require_auth)):
    return _ok(state_manager.get_audit_log(limit=limit))


# -- Pull Requests --

@app.get("/prs", response_model=ApiResponse)
async def get_prs(state: str = Query("all", pattern="^(open|closed|all)$"), _auth: str | None = Depends(require_auth)):
    prs = await state_manager.get_live_prs(state=state)
    return _ok(prs)


@app.get("/prs/{pr_number}", response_model=ApiResponse)
async def get_pr_detail(pr_number: int, _auth: str | None = Depends(require_auth)):
    pr = await state_manager.get_live_pr(pr_number)
    if not pr:
        _error(f"PR #{pr_number} not found", 404)
    return _ok(pr)


@app.get("/prs/{pr_number}/files", response_model=ApiResponse)
async def get_pr_files(pr_number: int, _auth: str | None = Depends(require_auth)):
    files = await state_manager.get_live_pr_files(pr_number)
    return _ok(files)


@app.get("/prs/{pr_number}/reviews", response_model=ApiResponse)
async def get_pr_reviews(pr_number: int, _auth: str | None = Depends(require_auth)):
    reviews = await state_manager.get_live_pr_reviews(pr_number)
    return _ok(reviews)


@app.get("/prs/{pr_number}/comments", response_model=ApiResponse)
async def get_pr_comments(pr_number: int, _auth: str | None = Depends(require_auth)):
    comments = await state_manager.get_live_pr_comments(pr_number)
    return _ok(comments)


@app.post("/prs", response_model=ApiResponse, status_code=201)
async def create_pr(body: CreatePRRequest, _auth: str | None = Depends(require_auth)):
    pr = await state_manager.create_live_pr(body.head, body.base, body.title, body.body)
    if not pr:
        _error("Failed to create PR -- check GITHUB_TOKEN and branch names", 502)
    return _ok(pr)


@app.post("/prs/{pr_number}/reviews", response_model=ApiResponse, status_code=201)
async def post_review(pr_number: int, body: PostReviewRequest, _auth: str | None = Depends(require_auth)):
    review = await state_manager.post_live_review(pr_number, body.event, body.body, body.comments or None)
    if not review:
        _error("Failed to post review -- check GITHUB_TOKEN and PR number", 502)
    return _ok(review)


@app.post("/prs/{pr_number}/comments", response_model=ApiResponse, status_code=201)
async def post_comment(pr_number: int, body: PostCommentRequest, _auth: str | None = Depends(require_auth)):
    comment = await state_manager.add_live_comment(pr_number, body.body)
    if not comment:
        _error("Failed to post comment -- check GITHUB_TOKEN and PR number", 502)
    return _ok(comment)


@app.post("/prs/{pr_number}/merge", response_model=ApiResponse)
async def merge_pr(pr_number: int, body: MergePRRequest, _auth: str | None = Depends(require_auth)):
    success = await state_manager.merge_live_pr(pr_number, body.method)
    if not success:
        _error(f"Failed to merge PR #{pr_number} -- check mergeable status and permissions", 502)
    return _ok({"pr_number": pr_number, "merged": True, "method": body.method})


# -- Filesystem Browse (Issue #106 Phase A) --

_PROJECT_MARKERS = frozenset({
    ".git", "package.json", "pyproject.toml", "Cargo.toml",
    "go.mod", "pom.xml", "build.gradle", "Makefile",
})


@app.get("/filesystem/browse", response_model=ApiResponse)
async def browse_filesystem(
    path: str = Query("", description="Directory to list. Defaults to user home."),
    show_hidden: bool = Query(False, description="Include hidden directories (.-prefix)"),
    _auth: str | None = Depends(require_auth),
):
    """Browse the local filesystem for directory selection.

    Issue #106 Phase A: directory picker for workspace selector.
    """
    target = Path(path).expanduser().resolve() if path else Path.home()

    if not target.is_dir():
        _error(f"Not a directory: {target}", 400)

    parent_path: str | None = None
    if target != target.parent:
        parent_path = str(target.parent)

    entries: list[BrowseDirectoryEntry] = []
    try:
        for child in sorted(target.iterdir(), key=lambda p: p.name.lower()):
            if not child.is_dir():
                continue
            if not show_hidden and child.name.startswith("."):
                continue
            try:
                child_names: set[str] = set()
                has_children = False
                for c in child.iterdir():
                    child_names.add(c.name)
                    if not has_children and c.is_dir() and (show_hidden or not c.name.startswith(".")):
                        has_children = True
                is_project = bool(child_names & _PROJECT_MARKERS)
            except (PermissionError, FileNotFoundError, NotADirectoryError):
                continue

            entries.append(BrowseDirectoryEntry(
                name=child.name,
                path=str(child),
                has_children=has_children,
                is_project=is_project,
            ))
    except PermissionError:
        _error(f"Permission denied: {target}", 403)
    except (FileNotFoundError, NotADirectoryError):
        _error(f"Not a directory: {target}", 404)

    entries.sort(key=lambda e: (not e.is_project, e.name.lower()))

    return _ok(BrowseDirectoryResponse(
        current_path=str(target),
        parent_path=parent_path,
        entries=entries,
    ))
