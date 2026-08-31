"""Tests: async event bus — ordering, depth-first completion, sync/async handlers, errors."""

from __future__ import annotations

from trading_agent.core.time import parse_timestamp
from trading_agent.runtime.bus import EventBus
from trading_agent.runtime.events import BarClosed, Event, Heartbeat

TS = parse_timestamp("2024-06-01T00:00:00Z")


async def test_sync_and_async_handlers_receive_event() -> None:
    bus = EventBus()
    seen: list[str] = []
    bus.subscribe(Heartbeat, lambda e: seen.append("sync"))

    async def ahandler(e: Event) -> None:
        seen.append("async")

    bus.subscribe(Heartbeat, ahandler)
    await bus.publish(Heartbeat(ts=TS))
    assert seen == ["sync", "async"]


async def test_subclass_matching() -> None:
    bus = EventBus()
    got: list[type] = []
    bus.subscribe(Event, lambda e: got.append(type(e)))
    await bus.publish(BarClosed(ts=TS, instrument="BTCUSDT"))
    assert got == [BarClosed]


async def test_depth_first_completion_and_order() -> None:
    bus = EventBus()
    order: list[str] = []

    async def on_bar(e: Event) -> None:
        order.append("bar-start")
        await bus.publish(Heartbeat(ts=TS))
        order.append("bar-end")

    bus.subscribe(BarClosed, on_bar)
    bus.subscribe(Heartbeat, lambda e: order.append("heartbeat"))

    await bus.publish(BarClosed(ts=TS))
    # heartbeat published during handling is processed AFTER the current handler returns
    assert order == ["bar-start", "bar-end", "heartbeat"]
    assert bus.published_count == 2
    assert bus.handled_count == 2


async def test_handler_error_raises_by_default() -> None:
    bus = EventBus(raise_on_handler_error=True)

    def boom(e: Event) -> None:
        raise RuntimeError("x")

    bus.subscribe(Heartbeat, boom)
    try:
        await bus.publish(Heartbeat(ts=TS))
    except RuntimeError:
        pass
    else:  # pragma: no cover
        raise AssertionError("expected RuntimeError")


async def test_handler_error_suppressed_when_configured() -> None:
    bus = EventBus(raise_on_handler_error=False)
    bus.subscribe(Heartbeat, lambda e: (_ for _ in ()).throw(RuntimeError("x")))
    await bus.publish(Heartbeat(ts=TS))  # no raise


async def test_unsubscribe() -> None:
    bus = EventBus()
    calls: list[int] = []
    h = lambda e: calls.append(1)  # noqa: E731
    bus.subscribe(Heartbeat, h)
    bus.unsubscribe(Heartbeat, h)
    await bus.publish(Heartbeat(ts=TS))
    assert calls == []
