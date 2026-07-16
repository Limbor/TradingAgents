"""Process-level asynchronous event bus.

Used for decoupled communication between the Skill execution layer
(LangGraph callbacks emit events) and the API layer (WebSocket endpoints
forward them to the frontend).
"""

import asyncio
from collections import defaultdict
from collections.abc import Awaitable, Callable
from typing import Any

EventHandler = Callable[[dict[str, Any]], Awaitable[None]]


class EventBus:
    """In-process async event bus.

    Skills emit events via ``emit()``, and subscribers (typically WebSocket
    endpoints) receive them via handlers registered with ``on()``.

    Example:
        bus = EventBus()
        bus.on("agent_status", lambda data: print(data))
        await bus.emit("agent_status", {"agent": "Market Analyst", "status": "running"})
    """

    def __init__(self) -> None:
        self._handlers: dict[str, list[EventHandler]] = defaultdict(list)

    def on(self, event_type: str, handler: EventHandler) -> Callable[[], None]:
        """Subscribe to an event type. Returns an unsubscribe callable."""
        self._handlers[event_type].append(handler)
        return lambda: self._handlers[event_type].remove(handler)

    async def emit(self, event_type: str, data: dict[str, Any]) -> None:
        """Emit an event, notifying all registered handlers concurrently."""
        handlers = self._handlers.get(event_type, [])
        if handlers:
            await asyncio.gather(*(h(data) for h in handlers))

    def clear(self) -> None:
        """Remove all subscriptions."""
        self._handlers.clear()
