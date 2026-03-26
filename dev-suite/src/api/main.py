"""FastAPI application exposing orchestrator state to the dashboard.

Issue #34: FastAPI Bootstrap -- API Layer for Orchestrator
Issue #35: SSE Event System -- Real-Time Task Streaming

Run with:
    uv run --group api uvicorn src.api.main:app --reload --port 8000

Or via the CLI convenience command (once wired):
    dev-suite serve
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
    CreateTaskRequest,
    CreateTaskResponse,
    HealthResponse,
    MemoryAction,
    MemoryStatus,
    MemoryTierEnum,
)
from .state import state_manager

load_dotenv()

logger = logging.getLogger(__name__)

# Heartbeat interval in seconds -- keeps SSE connections alive
# through proxies and load balancers that drop idle connections.
SSE_HEARTBEAT_SECONDS = 15


# -- Lifespan --

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/shutdown lifecycle for the API."""
    secret_set = bool(os.getenv("API_SECRET"))
    logger.info(
        "Dev Suite API starting | auth=%s | cors=%s | mock_data=%s",
        "enabled" if secret_set else "disabled (dev mode)",
        _allowed_origins,
        os.getenv("API_SEED_MOCK_DATA", "true"),
    )
    yield
    # Shutdown: cancel running tasks, then clean up SSE subscribers
    from .runner import task_runner

    logger.info(
        "Dev Suite API shutting down | running_tasks=%d | sse_subscribers=%d | events_published=%d",
        task_runner.running_count,
        event_bus.subscriber_count,
        event_bus.event_counter,
    )
    await task_runner.shutdown()
    await event_bus.clear()


# -- App Configuration --

# CORS: allow SvelteKit dev server and configurable origins
_allowed_origins = os.getenv(
    "CORS_ORIGINS",
    "http://localhost:5173,http://localhost:4173",
).split(",")

app = FastAPI(
    title="Dev Suite API",
    description="Exposes orchestrator state to the SvelteKit dashboard",
    version="0.2.0",
    docs_url="/docs",
    redoc_url=None,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in _allowed_origins],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)


# -- Helpers --

def _ok(data) -> ApiResponse:
    """Wrap data in the standard API response envelope."""
    # Convert Pydantic models / lists to serializable form
    if isinstance(data, list):
        serialized = [d.model_dump() if hasattr(d, "model_dump") else d for d in data]
    elif hasattr(data, "model_dump"):
        serialized = data.model_dump()
    else:
        serialized = data
    return ApiResponse(data=serialized)


def _error(message: str, status: int = 400) -> None:
    """Raise an HTTP error with a consistent message."""
    raise HTTPException(status_code=status, detail=message)


# -- Health --

@app.get("/health", response_model=HealthResponse)
async def health():
    """Health check -- no auth required."""
    return HealthResponse(
        uptime_seconds=state_manager.get_uptime(),
        active_tasks=state_manager.get_active_task_count(),
        sse_subscribers=event_bus.subscriber_count,
    )


# -- SSE Stream --

@app.get("/stream")
async def stream(
    request: Request,
    _auth: str | None = Depends(require_auth),
):
    """Server-Sent Events endpoint for real-time dashboard updates."""
    return EventSourceResponse(
        _sse_generator(request),
        media_type="text/event-stream",
    )


async def _sse_generator(request: Request):
    """Async generator that yields SSE events from the EventBus."""
    queue = await event_bus.subscribe()
    event_id = event_bus.event_counter

    logger.info(
        "SSE client connected (subscribers: %d)",
        event_bus.subscriber_count,
    )

    try:
        while True:
            if await request.is_disconnected():
                logger.debug("SSE client disconnected (detected via request)")
                break

            try:
                event: SSEEvent = await asyncio.wait_for(
                    queue.get(),
                    timeout=SSE_HEARTBEAT_SECONDS,
                )
                event_id += 1

                yield {
                    "event": event.type.value,
                    "data": event.model_dump_json(),
                    "id": str(event_id),
                }

            except asyncio.TimeoutError:
                yield {"comment": "keepalive"}

    except asyncio.CancelledError:
        logger.debug("SSE generator cancelled (client disconnect)")
    finally:
        await event_bus.unsubscribe(queue)
        logger.info(
            "SSE client cleaned up (subscribers: %d)",
            event_bus.subscriber_count,
        )


# -- Agents --

@app.get("/agents", response_model=ApiResponse)
async def get_agents(_auth: str | None = Depends(require_auth)):
    """Get current status of all agents."""
    return _ok(state_manager.get_agents())


# -- Tasks --

@app.get("/tasks", response_model=ApiResponse)
async def get_tasks(_auth: str | None = Depends(require_auth)):
    """Get all tasks with timeline and budget info."""
    return _ok(state_manager.get_tasks())


@app.get("/tasks/{task_id}", response_model=ApiResponse)
async def get_task(task_id: str, _auth: str | None = Depends(require_auth)):
    """Get full detail for a single task including blueprint."""
    task = state_manager.get_task(task_id)
    if not task:
        _error(f"Task '{task_id}' not found", 404)
    return _ok(task)


@app.post("/tasks", response_model=ApiResponse, status_code=201)
async def create_task(
    body: CreateTaskRequest,
    _auth: str | None = Depends(require_auth),
):
    """Queue a new task for the orchestrator."""
    task_id = await state_manager.create_task(body.description)
    return _ok(CreateTaskResponse(task_id=task_id))


@app.post("/tasks/{task_id}/cancel", response_model=ApiResponse)
async def cancel_task(task_id: str, _auth: str | None = Depends(require_auth)):
    """Cancel a running task."""
    if not await state_manager.cancel_task(task_id):
        task = state_manager.get_task(task_id)
        if not task:
            _error(f"Task '{task_id}' not found", 404)
        else:
            _error(f"Task '{task_id}' cannot be cancelled (status: {task.status})", 409)
    return _ok({"task_id": task_id, "status": "cancelled"})


@app.post("/tasks/{task_id}/retry", response_model=ApiResponse)
async def retry_task(task_id: str, _auth: str | None = Depends(require_auth)):
    """Retry a failed task."""
    if not await state_manager.retry_task(task_id):
        task = state_manager.get_task(task_id)
        if not task:
            _error(f"Task '{task_id}' not found", 404)
        else:
            _error(f"Task '{task_id}' is not in a retryable state (status: {task.status})", 400)
    return _ok({"task_id": task_id, "status": "queued"})


# -- Memory --

@app.get("/memory", response_model=ApiResponse)
async def get_memory(
    tier: MemoryTierEnum | None = Query(None, description="Filter by memory tier"),
    status: MemoryStatus | None = Query(None, description="Filter by approval status"),
    _auth: str | None = Depends(require_auth),
):
    """Get memory entries with optional filters."""
    return _ok(state_manager.get_memory(tier=tier, status=status))


@app.patch("/memory/{entry_id}", response_model=ApiResponse)
async def update_memory(
    entry_id: str,
    body: MemoryAction,
    _auth: str | None = Depends(require_auth),
):
    """Approve or reject a memory entry."""
    if body.action == "approve":
        entry = await state_manager.approve_memory(entry_id)
    elif body.action == "reject":
        entry = await state_manager.reject_memory(entry_id)
    else:
        _error(f"Invalid action: {body.action}")
        return  # unreachable, satisfies type checker

    if not entry:
        _error(f"Memory entry '{entry_id}' not found", 404)
    return _ok(entry)


# -- Pull Requests --

@app.get("/prs", response_model=ApiResponse)
async def get_prs(_auth: str | None = Depends(require_auth)):
    """Get all pull requests."""
    return _ok(state_manager.get_prs())
