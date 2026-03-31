"""Async event bus for real-time SSE streaming to the dashboard.

Issue #35: SSE Event System -- Real-Time Task Streaming

The EventBus is a singleton that LangGraph nodes publish events to.
Connected SSE clients each get their own asyncio.Queue for fan-out.
Events are structured JSON with a type, timestamp, and data payload.

Usage (publishing):
    from src.api.events import event_bus, SSEEvent

    await event_bus.publish(SSEEvent(
        type=EventType.AGENT_STATUS,
        data={"agent": "dev", "status": "coding", "task_id": "auth-rls"},
    ))

Usage (subscribing -- handled by the /stream endpoint):
    queue = event_bus.subscribe()
    try:
        while True:
            event = await asyncio.wait_for(queue.get(), timeout=15)
            yield event
    finally:
        event_bus.unsubscribe(queue)
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# Maximum events buffered per client before dropping.
# Prevents unbounded memory if a client is slow to consume.
DEFAULT_MAX_QUEUE_SIZE = 256


class EventType(str, Enum):
    """SSE event types streamed to the dashboard."""

    AGENT_STATUS = "agent_status"
    TASK_PROGRESS = "task_progress"
    TASK_COMPLETE = "task_complete"
    MEMORY_ADDED = "memory_added"
    LOG_LINE = "log_line"
    QA_ESCALATION = "qa_escalation"


class SSEEvent(BaseModel):
    """Structured event published to SSE clients.

    Matches the format defined in the issue spec:
    { "type": "...", "timestamp": "ISO8601", "data": { ... } }
    """

    type: EventType
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    data: dict = Field(default_factory=dict)


class EventBus:
    """Async pub/sub event bus for SSE fan-out.

    Each connected SSE client gets its own asyncio.Queue.
    Publishing an event copies it into every subscriber's queue.
    Designed to be used as a singleton.
    """

    def __init__(self, max_queue_size: int = DEFAULT_MAX_QUEUE_SIZE):
        self._subscribers: set[asyncio.Queue[SSEEvent]] = set()
        self._max_queue_size = max_queue_size
        self._event_counter = 0
        self._lock = asyncio.Lock()

    @property
    def subscriber_count(self) -> int:
        """Number of currently connected SSE clients."""
        return len(self._subscribers)

    @property
    def event_counter(self) -> int:
        """Total events published since startup."""
        return self._event_counter

    async def subscribe(self) -> asyncio.Queue[SSEEvent]:
        """Create a new subscriber queue for an SSE client.

        Returns:
            An asyncio.Queue that will receive all future events.
        """
        queue: asyncio.Queue[SSEEvent] = asyncio.Queue(maxsize=self._max_queue_size)
        async with self._lock:
            self._subscribers.add(queue)
        logger.debug("SSE client subscribed (total: %d)", len(self._subscribers))
        return queue

    async def unsubscribe(self, queue: asyncio.Queue[SSEEvent]) -> None:
        """Remove a subscriber queue on client disconnect.

        Args:
            queue: The queue previously returned by subscribe().
        """
        async with self._lock:
            self._subscribers.discard(queue)
        logger.debug("SSE client unsubscribed (total: %d)", len(self._subscribers))

    async def publish(self, event: SSEEvent) -> int:
        """Publish an event to all connected subscribers.

        Events are delivered best-effort: if a client's queue is full,
        the event is dropped for that client (logged as a warning)
        rather than blocking the publisher.

        Args:
            event: The SSEEvent to broadcast.

        Returns:
            Number of subscribers that received the event.
        """
        self._event_counter += 1
        delivered = 0

        async with self._lock:
            subscribers = set(self._subscribers)

        for queue in subscribers:
            try:
                queue.put_nowait(event)
                delivered += 1
            except asyncio.QueueFull:
                logger.warning(
                    "SSE client queue full -- dropping event %s (counter=%d)",
                    event.type.value,
                    self._event_counter,
                )

        if delivered > 0:
            logger.debug(
                "Published %s to %d/%d subscribers (counter=%d)",
                event.type.value,
                delivered,
                len(subscribers),
                self._event_counter,
            )

        return delivered

    async def clear(self) -> None:
        """Remove all subscribers. Used during shutdown."""
        async with self._lock:
            self._subscribers.clear()
        logger.info("EventBus cleared all subscribers")


# -- Singleton --

event_bus = EventBus()
