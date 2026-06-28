"""Unit tests for the Event Bus."""

import asyncio

from tradingagents.core.event_bus import EventBus


def test_emit_and_receive():
    bus = EventBus()
    results = []

    async def handler(data):
        results.append(data)

    async def run():
        bus.on("test_event", handler)
        await bus.emit("test_event", {"key": "value"})
        assert len(results) == 1
        assert results[0] == {"key": "value"}

    asyncio.run(run())


def test_multiple_handlers():
    bus = EventBus()
    results = []

    async def handler1(data):
        results.append(("h1", data))

    async def handler2(data):
        results.append(("h2", data))

    async def run():
        bus.on("event", handler1)
        bus.on("event", handler2)
        await bus.emit("event", {"x": 1})
        assert len(results) == 2

    asyncio.run(run())


def test_unsubscribe():
    bus = EventBus()
    results = []

    async def handler(data):
        results.append(data)

    async def run():
        unsub = bus.on("event", handler)
        await bus.emit("event", {"first": True})
        unsub()
        await bus.emit("event", {"second": True})
        assert len(results) == 1

    asyncio.run(run())


def test_clear():
    bus = EventBus()
    bus.on("event", lambda data: asyncio.sleep(0))
    bus.clear()
    asyncio.run(bus.emit("event", {"data": "test"}))


def test_no_handlers():
    bus = EventBus()
    asyncio.run(bus.emit("unknown_event", {"data": "test"}))
