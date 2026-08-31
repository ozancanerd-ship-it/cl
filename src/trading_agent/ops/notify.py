"""Benachrichtigungs-Kanäle mit Severity, Dedup und Rate-Limit (Masterplan §56/§57).

`Notifier` nimmt `Notification`s entgegen und verteilt sie an registrierte `Sink`s.
Schutz gegen Spam (Masterplan: „kein Alert-Spam"):

* **Dedup** — identischer `dedup_key` innerhalb `dedup_window_s` wird verworfen.
* **Rate-Limit** — höchstens `max_per_window` Nachrichten je `rate_window_s`; darüber
  hinausgehende werden gezählt und als eine Sammelmeldung nachgereicht.

Sinks: `ConsoleSink`, `FileSink` (JSONL) jetzt; `TelegramSink` **UNAVAILABLE** ohne
`TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` (kein Fake-Versand).
"""

from __future__ import annotations

import json
from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import IntEnum
from pathlib import Path

from trading_agent.core.clock import Clock, SystemClock
from trading_agent.security.secrets import get_secret


class Severity(IntEnum):
    DEBUG = 10
    INFO = 20
    WARNING = 30
    CRITICAL = 40


@dataclass(frozen=True, slots=True)
class Notification:
    severity: Severity
    title: str
    body: str = ""
    dedup_key: str = ""
    ts: datetime = field(default_factory=lambda: datetime.now(UTC))

    def as_dict(self) -> dict[str, object]:
        return {
            "ts": self.ts.isoformat(),
            "severity": self.severity.name,
            "title": self.title,
            "body": self.body,
            "dedup_key": self.dedup_key,
        }

    def as_text(self) -> str:
        head = f"[{self.severity.name}] {self.title}"
        return f"{head}\n{self.body}" if self.body else head


class Sink:
    name = "sink"

    def available(self) -> bool:
        return True

    def deliver(self, note: Notification) -> None:  # pragma: no cover - interface
        raise NotImplementedError


class ConsoleSink(Sink):
    name = "console"

    def __init__(self, min_severity: Severity = Severity.INFO) -> None:
        self.min_severity = min_severity
        self.delivered: list[Notification] = []

    def deliver(self, note: Notification) -> None:
        if note.severity >= self.min_severity:
            self.delivered.append(note)
            print(note.as_text())


class FileSink(Sink):
    name = "file"

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def deliver(self, note: Notification) -> None:
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(note.as_dict(), default=str) + "\n")


class TelegramSink(Sink):
    """Realer Versand über die Telegram-Bot-API — nur wenn Token + Chat-ID gesetzt sind."""

    name = "telegram"

    def __init__(
        self,
        *,
        token_env: str = "TELEGRAM_BOT_TOKEN",
        chat_env: str = "TELEGRAM_CHAT_ID",
        min_severity: Severity = Severity.WARNING,
        transport: object | None = None,
    ) -> None:
        self._token = get_secret(token_env, allow_keychain=True)
        self._chat = get_secret(chat_env, allow_keychain=True)
        self.min_severity = min_severity
        self._transport = transport  # Callable(url, json) -> None; None ⇒ echtes httpx zur Laufzeit
        self.sent = 0

    def available(self) -> bool:
        return self._token.present and self._chat.present

    def deliver(self, note: Notification) -> None:
        if not self.available() or note.severity < self.min_severity:
            return
        url = f"https://api.telegram.org/bot{self._token.reveal()}/sendMessage"
        payload = {"chat_id": self._chat.reveal(), "text": note.as_text()}
        if self._transport is not None:
            self._transport(url, payload)  # type: ignore[operator]
        else:  # pragma: no cover - echter Netzwerk-Pfad
            import httpx

            httpx.post(url, json=payload, timeout=10.0).raise_for_status()
        self.sent += 1


class Notifier:
    def __init__(
        self,
        sinks: Iterable[Sink] = (),
        *,
        clock: Clock | None = None,
        dedup_window_s: float = 300.0,
        rate_window_s: float = 60.0,
        max_per_window: int = 8,
    ) -> None:
        self.sinks: list[Sink] = list(sinks)
        self.clock = clock or SystemClock()
        self.dedup_window_s = dedup_window_s
        self.rate_window_s = rate_window_s
        self.max_per_window = max_per_window
        self._recent_keys: dict[str, datetime] = {}
        self._sent_times: deque[datetime] = deque()
        self._suppressed = 0
        self.emitted = 0
        self.deduped = 0
        self.rate_limited = 0

    def add_sink(self, sink: Sink) -> None:
        self.sinks.append(sink)

    def notify(self, note: Notification) -> bool:
        now = self.clock.now()

        if note.dedup_key:
            last = self._recent_keys.get(note.dedup_key)
            if last is not None and (now - last).total_seconds() < self.dedup_window_s:
                self.deduped += 1
                return False
            self._recent_keys[note.dedup_key] = now

        while self._sent_times and (now - self._sent_times[0]).total_seconds() > self.rate_window_s:
            self._sent_times.popleft()

        if len(self._sent_times) >= self.max_per_window and note.severity < Severity.CRITICAL:
            self._suppressed += 1
            self.rate_limited += 1
            return False

        if self._suppressed:
            self._emit(
                Notification(
                    severity=Severity.INFO,
                    title=f"{self._suppressed} weitere Meldungen unterdrückt (Rate-Limit)",
                    dedup_key="rate-limit-summary",
                    ts=now,
                ),
                now,
            )
            self._suppressed = 0

        self._emit(note, now)
        return True

    def _emit(self, note: Notification, now: datetime) -> None:
        for sink in self.sinks:
            if sink.available():
                sink.deliver(note)
        self._sent_times.append(now)
        self.emitted += 1

    @property
    def active_sinks(self) -> list[str]:
        return [s.name for s in self.sinks if s.available()]


__all__ = [
    "ConsoleSink",
    "FileSink",
    "Notification",
    "Notifier",
    "Severity",
    "Sink",
    "TelegramSink",
]
