"""In-process async event bus (pub/sub).

Guarantees:

* **Order preserved.** Events are dispatched in publication order.
* **Depth-first completion.** ``await bus.publish(e)`` returns only after ``e`` and every event
  transitively published while handling ``e`` has been fully processed. This makes a backtest
  bar deterministic: analysis -> strategy -> risk -> order -> fill -> ledger all complete before
  the next bar.
* **Sync or async handlers.** Both are supported.

Not a message queue. No external broker. Upgradeable later if ever needed.
"""

from __future__ import annotations

import contextlib
import inspect
import logging
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable
from typing import TypeVar

from trading_agent.runtime.events import Event

_log = logging.getLogger("trading_agent.runtime.bus")

E = TypeVar("E", bound=Event)
Handler = Callable[[Event], Awaitable[None] | None]


class EventBus:
    def __init__(self, *, raise_on_handler_error: bool = True) -> None:
        self._handlers: dict[type[Event], list[Handler]] = defaultdict(list)
        self._pending: deque[Event] = deque()
        self._dispatching = False
        self._raise = raise_on_handler_error
        self.published_count = 0
        self.handled_count = 0

    def subscribe(
        self, event_type: type[E], handler: Callable[[E], Awaitable[None] | None]
    ) -> None:
        """Register ``handler`` for ``event_type`` and its subclasses."""
        self._handlers[event_type].append(handler)  # type: ignore[arg-type]

    def unsubscribe(
        self, event_type: type[E], handler: Callable[[E], Awaitable[None] | None]
    ) -> None:
        with contextlib.suppress(ValueError):
            self._handlers[event_type].remove(handler)  # type: ignore[arg-type]

    def _matching(self, event: Event) -> list[Handler]:
        out: list[Handler] = []
        for etype, handlers in self._handlers.items():
            if isinstance(event, etype):
                out.extend(handlers)
        return out

    async def publish(self, event: Event) -> None:
        self._pending.append(event)
        self.published_count += 1
        if self._dispatching:
            return
        self._dispatching = True
        try:
            while self._pending:
                current = self._pending.popleft()
                for handler in self._matching(current):
                    try:
                        result = handler(current)
                        if inspect.isawaitable(result):
                            await result
                        self.handled_count += 1
                    except Exception:
                        _log.exception(
                            "event handler failed", extra={"event": type(current).__name__}
                        )
                        if self._raise:
                            raise
        finally:
            self._dispatching = False

    def subscriber_count(self, event_type: type[Event]) -> int:
        return len(self._handlers.get(event_type, []))


__all__ = ["EventBus", "Handler"]
