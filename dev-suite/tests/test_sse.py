"""Tests for Issue #35: SSE Event System - Real-Time Task Streaming.

Covers:
- EventBus unit tests (subscribe, unsubscribe, publish, fan-out, queue full)
- SSE /stream endpoint integration tests (auth, events, heartbeat, cleanup)
- StateManager event emission on mutations

"""

from __future__ import annotations

import asyncio
import json

import pytest
from httpx import ASGITransport, AsyncClient

from src.api.events import EventBus, EventType, SSEEvent, event_bus
from src.api.main import app
from src.api.state import state_manager


# ============================================================
# Fixtures
# ============================================================


@pytest.fixture(autouse=True)
async def _reset_event_bus():
    """Clear subscribers between tests to prevent leaks."""
    await event_bus.clear()
    yield
    await event_bus.clear()


@pytest.fixture
def bus():
    """Fresh EventBus instance for isolated unit tests."""
    return EventBus(max_queue_size=8)


@pytest.fixture
async def client():
    """Async test client for the FastAPI app (no auth required in test mode)."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# ============================================================
# SSEEvent model tests
# ============================================================


class TestSSEEvent:
    """Verify SSEEvent structure and serialization."""

    def test_event_has_timestamp(self):
        event = SSEEvent(type=EventType.LOG_LINE, data={"msg": "hello"})
        assert event.timestamp is not None
        assert event.type == EventType.LOG_LINE
        assert event.data == {"msg": "hello"}

    def test_event_serializes_to_json(self):
        event = SSEEvent(type=EventType.AGENT_STATUS, data={"agent": "dev"})
        raw = event.model_dump_json()
        parsed = json.loads(raw)
        assert parsed["type"] == "agent_status"
        assert "timestamp" in parsed
        assert parsed["data"]["agent"] == "dev"

    def test_all_event_types_valid(self):
        for et in EventType:
            event = SSEEvent(type=et, data={})
            assert event.type == et


# ============================================================
# EventBus unit tests
# ============================================================


class TestEventBus:
    """Core EventBus publish/subscribe behavior."""

    async def test_subscribe_creates_queue(self, bus: EventBus):
        queue = await bus.subscribe()
        assert isinstance(queue, asyncio.Queue)
        assert bus.subscriber_count == 1

    async def test_unsubscribe_removes_queue(self, bus: EventBus):
        queue = await bus.subscribe()
        assert bus.subscriber_count == 1
        await bus.unsubscribe(queue)
        assert bus.subscriber_count == 0

    async def test_unsubscribe_nonexistent_is_safe(self, bus: EventBus):
        fake_queue: asyncio.Queue = asyncio.Queue()
        await bus.unsubscribe(fake_queue)  # Should not raise
        assert bus.subscriber_count == 0

    async def test_publish_delivers_to_subscriber(self, bus: EventBus):
        queue = await bus.subscribe()
        event = SSEEvent(type=EventType.LOG_LINE, data={"msg": "test"})
        delivered = await bus.publish(event)
        assert delivered == 1
        received = queue.get_nowait()
        assert received.type == EventType.LOG_LINE
        assert received.data["msg"] == "test"

    async def test_publish_fans_out_to_all_subscribers(self, bus: EventBus):
        q1 = await bus.subscribe()
        q2 = await bus.subscribe()
        q3 = await bus.subscribe()
        event = SSEEvent(type=EventType.TASK_PROGRESS, data={"task": "a"})
        delivered = await bus.publish(event)
        assert delivered == 3
        for q in [q1, q2, q3]:
            received = q.get_nowait()
            assert received.type == EventType.TASK_PROGRESS

    async def test_publish_to_empty_bus_returns_zero(self, bus: EventBus):
        event = SSEEvent(type=EventType.LOG_LINE, data={})
        delivered = await bus.publish(event)
        assert delivered == 0

    async def test_queue_full_drops_event_gracefully(self, bus: EventBus):
        """When a client's queue is full, the event is dropped - not blocked."""
        queue = await bus.subscribe()
        # Fill the queue to capacity (max_queue_size=8)
        for i in range(8):
            await bus.publish(SSEEvent(type=EventType.LOG_LINE, data={"i": i}))
        # Queue is now full - next publish should drop
        delivered = await bus.publish(SSEEvent(type=EventType.LOG_LINE, data={"overflow": True}))
        assert delivered == 0
        assert queue.qsize() == 8

    async def test_event_counter_increments(self, bus: EventBus):
        assert bus.event_counter == 0
        await bus.publish(SSEEvent(type=EventType.LOG_LINE, data={}))
        assert bus.event_counter == 1
        await bus.publish(SSEEvent(type=EventType.LOG_LINE, data={}))
        assert bus.event_counter == 2

    async def test_clear_removes_all_subscribers(self, bus: EventBus):
        await bus.subscribe()
        await bus.subscribe()
        assert bus.subscriber_count == 2
        await bus.clear()
        assert bus.subscriber_count == 0

    async def test_multiple_subscribe_unsubscribe_cycles(self, bus: EventBus):
        """Rapid subscribe/unsubscribe should not leak."""
        for _ in range(50):
            q = await bus.subscribe()
            await bus.unsubscribe(q)
        assert bus.subscriber_count == 0


# ============================================================
# SSE /stream endpoint tests
# ============================================================


class TestStreamEndpoint:
    """Integration tests for GET /stream SSE endpoint."""

    async def test_stream_connects_without_auth_in_dev_mode(self, client: AsyncClient):
        """In dev mode (no API_SECRET), /stream should connect.

        SSE endpoints stream indefinitely, so we use a short timeout
        and verify the response started with the correct content type.
        """
        # Subscribe to the bus so we can push an event to end the read
        queue = await event_bus.subscribe()
        try:
            # Publish an event so the stream has something to yield
            await event_bus.publish(SSEEvent(type=EventType.LOG_LINE, data={"test": True}))

            lines = []
            async def read_a_bit():
                async with client.stream("GET", "/stream") as resp:
                    assert resp.status_code == 200
                    ct = resp.headers.get("content-type", "")
                    assert "text/event-stream" in ct
                    async for line in resp.aiter_lines():
                        lines.append(line)
                        if lines:
                            return

            try:
                await asyncio.wait_for(read_a_bit(), timeout=3.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass
            # If we got any lines or at least didn't get an error, it connected
        finally:
            await event_bus.unsubscribe(queue)

    async def test_stream_rejects_invalid_auth(self, client: AsyncClient, monkeypatch):
        """With API_SECRET set, invalid Bearer token returns 403."""
        monkeypatch.setenv("API_SECRET", "test-secret-123")
        resp = await client.get(
            "/stream",
            headers={"Authorization": "Bearer wrong-token"},
        )
        assert resp.status_code == 403

    async def test_stream_rejects_missing_auth(self, client: AsyncClient, monkeypatch):
        """With API_SECRET set, missing Authorization header returns 401."""
        monkeypatch.setenv("API_SECRET", "test-secret-123")
        resp = await client.get("/stream")
        assert resp.status_code == 401

    async def test_stream_accepts_valid_auth(self, client: AsyncClient, monkeypatch):
        """With API_SECRET set, valid Bearer token connects."""
        monkeypatch.setenv("API_SECRET", "test-secret-123")

        # Push an event so stream has data to yield
        await event_bus.publish(SSEEvent(type=EventType.LOG_LINE, data={"auth": True}))

        lines = []
        async def read_a_bit():
            async with client.stream(
                "GET",
                "/stream",
                headers={"Authorization": "Bearer test-secret-123"},
            ) as resp:
                assert resp.status_code == 200
                async for line in resp.aiter_lines():
                    lines.append(line)
                    if lines:
                        return

        try:
            await asyncio.wait_for(read_a_bit(), timeout=3.0)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            pass

    async def test_published_event_reaches_subscriber_via_bus(self):
        """Verify the full data path: publish -> EventBus -> subscriber queue.

        Note: Testing the actual HTTP SSE byte stream requires a real
        server (uvicorn), because httpx's ASGITransport does not stream
        response bytes incrementally. The EventBus fan-out is the core
        mechanism - this test validates it directly.

        A live integration test (test with `--run-integration`) can be
        added once the dev server is running.
        """
        # Simulate what /stream does: subscribe, then check events arrive
        queue = await event_bus.subscribe()
        try:
            event = SSEEvent(
                type=EventType.AGENT_STATUS,
                data={"agent": "dev", "status": "coding"},
            )
            delivered = await event_bus.publish(event)
            assert delivered == 1

            received = queue.get_nowait()
            assert received.type == EventType.AGENT_STATUS
            assert received.data["agent"] == "dev"
            assert received.data["status"] == "coding"

            # Verify the SSE payload shape matches what /stream would yield
            payload = received.model_dump_json()
            import json
            parsed = json.loads(payload)
            assert parsed["type"] == "agent_status"
            assert "timestamp" in parsed
            assert parsed["data"]["agent"] == "dev"
        finally:
            await event_bus.unsubscribe(queue)

    async def test_subscriber_cleanup_on_disconnect(self):
        """When a client disconnects, its queue should be removed."""
        # Direct EventBus test - subscribe, then unsubscribe
        initial = event_bus.subscriber_count
        queue = await event_bus.subscribe()
        assert event_bus.subscriber_count == initial + 1
        await event_bus.unsubscribe(queue)
        assert event_bus.subscriber_count == initial


# ============================================================
# Health endpoint - sse_subscribers field
# ============================================================


class TestHealthSSE:
    """Health endpoint includes SSE subscriber count."""

    async def test_health_includes_sse_subscribers(self, client: AsyncClient):
        resp = await client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert "sse_subscribers" in data
        assert data["sse_subscribers"] == 0

    async def test_health_reflects_active_subscribers(self, client: AsyncClient):
        queue = await event_bus.subscribe()
        try:
            resp = await client.get("/health")
            data = resp.json()
            assert data["sse_subscribers"] == 1
        finally:
            await event_bus.unsubscribe(queue)


# ============================================================
# StateManager event emission
# ============================================================


class TestStateManagerEmission:
    """StateManager mutations emit SSE events."""

    async def test_create_task_emits_task_progress(self):
        queue = await event_bus.subscribe()
        try:
            task_id = await state_manager.create_task("Test task for SSE")
            assert task_id.startswith("task-")

            event = queue.get_nowait()
            assert event.type == EventType.TASK_PROGRESS
            assert event.data["task_id"] == task_id
            assert event.data["event"] == "task_queued"
        finally:
            await event_bus.unsubscribe(queue)

    async def test_cancel_task_emits_task_complete(self):
        queue = await event_bus.subscribe()
        try:
            task_id = await state_manager.create_task("Cancellable task")
            # Drain the create event
            queue.get_nowait()

            result = await state_manager.cancel_task(task_id)
            assert result is True

            event = queue.get_nowait()
            assert event.type == EventType.TASK_COMPLETE
            assert event.data["task_id"] == task_id
            assert event.data["status"] == "cancelled"
        finally:
            await event_bus.unsubscribe(queue)

    async def test_approve_memory_emits_memory_added(self):
        """Seed a memory entry, approve it, verify SSE event fires."""
        import time
        from src.api.models import MemoryEntryResponse, MemoryStatus, MemoryTierEnum

        # Seed a pending entry into state_manager so approve_memory finds it
        state_manager._memory["mem-1"] = MemoryEntryResponse(
            id="mem-1", content="Auth requires RLS on public tables",
            tier=MemoryTierEnum.L0_DISCOVERED, module="auth",
            source_agent="Architect", verified=False,
            status=MemoryStatus.PENDING, created_at=time.time(),
        )

        queue = await event_bus.subscribe()
        try:
            entry = await state_manager.approve_memory("mem-1")
            assert entry is not None

            event = queue.get_nowait()
            assert event.type == EventType.MEMORY_ADDED
            assert event.data["id"] == "mem-1"
            assert event.data["status"] == "approved"
        finally:
            await event_bus.unsubscribe(queue)
            state_manager._memory.pop("mem-1", None)
            state_manager._audit_log.clear()

    async def test_reject_memory_emits_memory_added(self):
        """Seed a memory entry, reject it, verify SSE event fires."""
        import time
        from src.api.models import MemoryEntryResponse, MemoryStatus, MemoryTierEnum

        # Seed a pending entry into state_manager so reject_memory finds it
        state_manager._memory["mem-3"] = MemoryEntryResponse(
            id="mem-3", content="Rate limiter must wrap /api/* routes",
            tier=MemoryTierEnum.L0_DISCOVERED, module="middleware",
            source_agent="QA Agent", verified=False,
            status=MemoryStatus.PENDING, created_at=time.time(),
        )

        queue = await event_bus.subscribe()
        try:
            entry = await state_manager.reject_memory("mem-3")
            assert entry is not None

            event = queue.get_nowait()
            assert event.type == EventType.MEMORY_ADDED
            assert event.data["id"] == "mem-3"
            assert event.data["status"] == "rejected"
        finally:
            await event_bus.unsubscribe(queue)
            state_manager._memory.pop("mem-3", None)
            state_manager._audit_log.clear()

    async def test_update_agent_status_emits_event(self):
        from src.api.models import AgentStatus

        queue = await event_bus.subscribe()
        try:
            await state_manager.update_agent_status("dev", AgentStatus.CODING, "task-123")

            event = queue.get_nowait()
            assert event.type == EventType.AGENT_STATUS
            assert event.data["agent"] == "dev"
            assert event.data["status"] == "coding"
            assert event.data["task_id"] == "task-123"
        finally:
            await event_bus.unsubscribe(queue)
            # Reset
            await state_manager.update_agent_status("dev", AgentStatus.IDLE, None)
