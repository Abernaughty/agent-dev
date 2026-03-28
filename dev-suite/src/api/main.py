"""FastAPI application exposing orchestrator state to the dashboard.

Issue #34: FastAPI Bootstrap -- API Layer for Orchestrator
Issue #35: SSE Event System -- Real-Time Task Streaming
Issue #50: Full GitHub PR endpoints (read + write)
Issue #51: Removed mock_data from startup log
Issue #19: Memory audit log endpoint

Run with:
    uv run --group api uvicorn src.api.main:app --reload --port 8000
"""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from sse_starlette import EventSourceResponse

from .auth import require_auth
from .events import EventType, SSEEvent, event_bus
from .models import (
    ApiResponse,
    CreatePRRequest,
    CreateTaskRequest,
    CreateTaskResponse,
    HealthResponse,
    MemoryAction,
    MemoryStatus,
    MemoryTierEnum,
    MergePRRequest,
    PostCommentRequest,
    PostReviewRequest,
)
from .state import state_manager

load_dotenv()

logger = logging.getLogger(__name__)

SSE_HEARTBEAT_SECONDS = 15


@asynccontextmanager
async def lifespan(app: FastAPI):
    secret_set = bool(os.getenv("API_SECRET"))
    logger.info("Dev Suite API starting | auth=%s | cors=%s", "enabled" if secret_set else "disabled (dev mode)", _allowed_origins)
    yield
    from .runner import task_runner
    logger.info("Dev Suite API shutting down | running_tasks=%d | sse_subscribers=%d | events_published=%d", task_runner.running_count, event_bus.subscriber_count, event_bus.event_counter)
    await task_runner.shutdown()
    await event_bus.clear()
    from .github_prs import github_pr_provider
    await github_pr_provider.close()


_allowed_origins = os.getenv("CORS_ORIGINS", "http://localhost:5173,http://localhost:4173").split(",")

app = FastAPI(title="Dev Suite API", description="Exposes orchestrator state to the SvelteKit dashboard", version="0.2.0", docs_url="/docs", redoc_url=None, lifespan=lifespan)

app.add_middleware(CORSMiddleware, allow_origins=[o.strip() for o in _allowed_origins], allow_credentials=True, allow_methods=["GET", "POST", "PATCH", "OPTIONS"], allow_headers=["Authorization", "Content-Type"])


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
    task_id = await state_manager.create_task(body.description)
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
    if not await state_manager.retry_task(task_id):
        task = state_manager.get_task(task_id)
        if not task:
            _error(f"Task '{task_id}' not found", 404)
        else:
            _error(f"Task '{task_id}' is not in a retryable state (status: {task.status})", 400)
    return _ok({"task_id": task_id, "status": "queued"})


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
