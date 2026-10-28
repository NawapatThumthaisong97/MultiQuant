"""
MultiQuant Event Bus Primitives.
Asynchronous publish-subscribe messaging for decoupled trading layers.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Coroutine
from typing import Any, TypeVar

T = TypeVar("T")
HandlerFunc = Callable[[Any], Coroutine[Any, Any, None]]

logger = logging.getLogger("multiquant.event_bus")


class EventBus:
    """In-process asynchronous event bus enabling loose coupling between layers."""

    def __init__(self) -> None:
        self._subscribers: dict[type[Any], list[HandlerFunc]] = {}
        self._queue: asyncio.Queue[Any] = asyncio.Queue()
        self._is_running: bool = False
        self._worker_task: asyncio.Task[None] | None = None

    def subscribe(self, event_type: type[T], handler: HandlerFunc) -> None:
        """Register an async handler function for a specific event type."""
        if event_type not in self._subscribers:
            self._subscribers[event_type] = []
        self._subscribers[event_type].append(handler)
        logger.debug("Subscribed %s to event %s", handler.__name__, event_type.__name__)

    async def publish(self, event: Any) -> None:
        """Enqueue an event to be processed asynchronously by subscribers."""
        await self._queue.put(event)

    async def publish_immediate(self, event: Any) -> None:
        """Directly dispatch event to subscribers without queuing (critical path)."""
        event_type = type(event)
        handlers = self._subscribers.get(event_type, [])
        for handler in handlers:
            try:
                await handler(event)
            except Exception as e:
                logger.error(
                    "Error in event handler %s for %s: %s",
                    handler.__name__,
                    event_type.__name__,
                    e,
                    exc_info=True,
                )

    async def start(self) -> None:
        """Start the background event dispatch loop."""
        self._is_running = True
        self._worker_task = asyncio.create_task(self._process_events())
        logger.info("EventBus dispatcher started.")

    async def stop(self) -> None:
        """Gracefully stop the event bus and wait for remaining events."""
        self._is_running = False
        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
        logger.info("EventBus stopped.")

    async def _process_events(self) -> None:
        while self._is_running:
            try:
                event = await self._queue.get()
                await self.publish_immediate(event)
                self._queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Unexpected error in event loop: %s", e, exc_info=True)
