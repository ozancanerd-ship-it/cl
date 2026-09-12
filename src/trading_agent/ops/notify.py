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
import os
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


#: Der oeffentliche VAPID-Schluessel. Er gehoert in die Seite und darf oeffentlich sein —
#: das ist der Sinn des Verfahrens. Der zugehoerige private Schluessel steht
#: ausschliesslich in einem GitHub-Secret und niemals im Code.
VAPID_PUBLIC = (
    "BHIXvrID7IczXuTn_07q5OTCugGPSLHvduoLV-CFuxIKDD6bbBPRyrAkeeQc7jXFa7tM0yfhy1ZHRYDSegNgIwQ"
)


class WebPushSink(Sink):
    """Push aufs Geraet — auch wenn die App zu ist.

    WARUM DAS ANDERS IST ALS DIE BENACHRICHTIGUNG IN DER APP

    Die App kann nur melden, solange ein Tab offen ist. Genau das war Ozans Einwand:
    „Ich brauche die Alarme auch wenn die App aus ist." Web Push loest das, weil die
    Meldung nicht von der Seite kommt, sondern vom Push-Dienst des Browserherstellers
    an den Service Worker — der laeuft auch ohne offene Seite.

    WAS DAFUER NOETIG IST

    Zwei Dinge, beide einmalig:

    * ``VAPID_PRIVATE_KEY`` — der private Teil des Schluesselpaars, als GitHub-Secret.
    * ``PUSH_ABOS`` — die Abonnements der Geraete, als JSON-Liste. Die App erzeugt sie
      beim Klick auf „Alarme aufs Handy" und zeigt sie zum Kopieren an.

    Ohne beides ist die Senke schlicht nicht verfuegbar und meldet das auch so, statt
    stillschweigend nichts zu tun. Genau dieser stille Ausfall hat wochenlang dafuer
    gesorgt, dass niemand gemerkt hat, dass die Telegram-Secrets fehlten.
    """

    name = "webpush"

    def __init__(
        self,
        *,
        key_env: str = "VAPID_PRIVATE_KEY",
        abos_env: str = "PUSH_ABOS",
        kontakt: str = "mailto:alerts@example.invalid",
        min_severity: Severity = Severity.WARNING,
        transport: object | None = None,
    ) -> None:
        self._key = get_secret(key_env, allow_keychain=True)
        self._abos_roh = os.environ.get(abos_env, "").strip()
        self.kontakt = kontakt
        self.min_severity = min_severity
        self._transport = transport
        self.sent = 0
        self.fehler: list[str] = []

    def abos(self) -> list[dict[str, object]]:
        """Die Abonnements aus dem Secret. Tolerant gegenueber dem, was ein Mensch einfuegt.

        Erlaubt sind eine JSON-Liste, ein einzelnes JSON-Objekt oder mehrere Objekte
        untereinander. Wer ein Abo aus dem Browser kopiert, bekommt genau eines davon —
        und soll nicht daran scheitern, dass eckige Klammern fehlen.
        """
        text = self._abos_roh
        if not text:
            return []
        try:
            geladen = json.loads(text)
        except json.JSONDecodeError:
            aus: list[dict[str, object]] = []
            for zeile in text.splitlines():
                zeile = zeile.strip().rstrip(",")
                if not zeile:
                    continue
                try:
                    obj = json.loads(zeile)
                except json.JSONDecodeError:
                    continue
                if isinstance(obj, dict):
                    aus.append(obj)
            return aus
        if isinstance(geladen, dict):
            return [geladen]
        if isinstance(geladen, list):
            return [x for x in geladen if isinstance(x, dict)]
        return []

    def available(self) -> bool:
        return self._key.present and bool(self.abos())

    def deliver(self, note: Notification) -> None:
        if not self.available() or note.severity < self.min_severity:
            return
        nutzlast = json.dumps(
            {
                "titel": note.title,
                "text": note.body,
                "dringend": note.severity >= Severity.CRITICAL,
                "ts": note.ts.isoformat(),
                "dedup": note.dedup_key,
            },
            ensure_ascii=False,
        )
        if self._transport is not None:
            for abo in self.abos():
                self._transport(abo, nutzlast)  # type: ignore[operator]
            self.sent += 1
            return
        try:  # pragma: no cover - echter Netzwerk-Pfad
            from pywebpush import (  # type: ignore[import-not-found]
                WebPushException,
                webpush,
            )
        except ImportError:  # pragma: no cover
            self.fehler.append("pywebpush fehlt — pip install pywebpush")
            return
        for abo in self.abos():  # pragma: no cover - echter Netzwerk-Pfad
            try:
                webpush(
                    subscription_info=abo,
                    data=nutzlast,
                    vapid_private_key=self._key.reveal(),
                    vapid_claims={"sub": self.kontakt},
                    ttl=3600,
                )
                self.sent += 1
            except WebPushException as exc:
                # 404/410 heisst: das Geraet hat das Abo weggeworfen. Kein Fehler,
                # der wiederholt werden muesste — es muss neu abonniert werden.
                code = getattr(getattr(exc, "response", None), "status_code", None)
                self.fehler.append(f"{str(abo.get('endpoint', ''))[:40]}… → {code or exc}")


class GitHubIssueSink(Sink):
    """Alarm als GitHub-Issue — der einzige Kanal, der **ohne jede Einrichtung** ankommt.

    DAS PROBLEM, DAS ER LÖST

    Alle anderen Kanäle brauchen ein Geheimnis, das jemand einmal von Hand setzen muss:
    Telegram braucht Token und Chat-ID, Web Push braucht den privaten VAPID-Schlüssel und
    die Abos der Geräte. Solange das nicht passiert ist, kommt **nichts** an — und genau
    das war wochenlang der Fall, ohne dass es jemandem auffiel. Ozans Satz dazu:
    „heute kam kein einziges Signal an."

    In einem GitHub-Actions-Lauf liegt aber immer schon ein Token bereit: ``GITHUB_TOKEN``.
    Es wird für jeden Lauf frisch erzeugt, gilt nur für dieses Repository und läuft danach
    ab — niemand muss es anlegen, kopieren oder irgendwo hinterlegen. Damit lässt sich ein
    Issue öffnen, und ein Issue, in dem der Kontoinhaber erwähnt wird, erzeugt bei GitHub
    eine echte Benachrichtigung: E-Mail, und auf dem Handy eine Push-Meldung, wenn die
    GitHub-App installiert ist. Ohne offene Seite, ohne Einrichtung.

    WAS ER NICHT IST

    Kein Ersatz für Web Push. Die Meldung kommt über den Umweg einer Software-Plattform,
    sie ist langsamer als eine echte Push-Nachricht und sie landet in einer Liste, die
    eigentlich für Fehlerberichte gedacht ist. Das ist der Preis dafür, dass sie ohne
    einen einzigen Handgriff funktioniert. Sobald die Push-Secrets gesetzt sind, laufen
    beide Kanäle nebeneinander — doppelt gemeldet ist besser als gar nicht gemeldet, und
    der Dedup-Schlüssel verhindert ohnehin, dass dieselbe Lage zweimal aufschlägt.

    Schwelle bewusst hoch: nur ``CRITICAL``. Ein Issue je Kursbewegung wäre genau der
    Spam, den der Masterplan ausschließt.
    """

    name = "github-issue"

    def __init__(
        self,
        *,
        token_env: str = "GITHUB_TOKEN",
        repo_env: str = "GITHUB_REPOSITORY",
        erwaehnen: str = "",
        min_severity: Severity = Severity.CRITICAL,
        transport: object | None = None,
    ) -> None:
        self._token = get_secret(token_env, allow_keychain=False)
        self._repo = os.environ.get(repo_env, "").strip()
        #: Wer im Text erwähnt wird — ohne Erwähnung schickt GitHub nichts los.
        self.erwaehnen = erwaehnen or os.environ.get("ALARM_MENTION", "").strip()
        self.min_severity = min_severity
        self._transport = transport
        self.sent = 0
        self.fehler: list[str] = []

    def available(self) -> bool:
        return self._token.present and bool(self._repo)

    def _titel(self, note: Notification) -> str:
        roh = note.title.strip() or "Signal"
        return roh[:120]

    def _text(self, note: Notification) -> str:
        teile = []
        if self.erwaehnen:
            teile.append(f"@{self.erwaehnen.lstrip('@')}")
        teile.append(note.body or note.title)
        teile.append(
            "\n---\n*Automatisch aus dem Marktlauf. Nichts wird ausgeführt — das ist ein "
            "Hinweis, keine Order. Schließe das Issue, wenn du es gesehen hast.*"
        )
        return "\n\n".join(teile)

    def _kopf(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token.reveal()}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def _schon_offen(self, titel: str) -> bool:
        """Gibt es zu dieser Meldung schon ein offenes Issue?

        Ohne diese Pruefung entstand fuer dieselbe Lage in jedem Lauf ein neues Issue —
        „STOP FFUSDT" stand zweimal da, „STOP CRVUSDT" auch. Auf dem Telefon ist das
        kein Schoenheitsfehler: es ist genau die Wiederholung, die dazu fuehrt, dass man
        die Meldungen abschaltet. Ein Ereignis, ein Issue; solange es offen ist, kommt
        nichts Neues dazu.
        """
        try:  # pragma: no cover - echter Netzwerkpfad
            import httpx

            antwort = httpx.get(
                f"https://api.github.com/repos/{self._repo}/issues",
                params={"state": "open", "labels": "signal", "per_page": 100},
                headers=self._kopf(),
                timeout=15.0,
            )
            antwort.raise_for_status()
            daten = antwort.json()
        except Exception as exc:  # pragma: no cover
            # Im Zweifel melden: eine verpasste Meldung ist schlimmer als eine doppelte.
            self.fehler.append(f"Dubletten-Pruefung fehlgeschlagen: {type(exc).__name__}")
            return False
        return any(str(row.get("title") or "") == titel for row in daten if isinstance(row, dict))

    def deliver(self, note: Notification) -> None:
        if not self.available() or note.severity < self.min_severity:
            return
        titel = self._titel(note)
        if self._transport is None and self._schon_offen(titel):
            return
        url = f"https://api.github.com/repos/{self._repo}/issues"
        payload = {"title": titel, "body": self._text(note), "labels": ["signal"]}
        if self._transport is not None:
            self._transport(url, payload)  # type: ignore[operator]
            self.sent += 1
            return
        try:  # pragma: no cover - echter Netzwerkpfad
            import httpx

            antwort = httpx.post(url, json=payload, headers=self._kopf(), timeout=15.0)
            antwort.raise_for_status()
            self.sent += 1
        except Exception as exc:  # pragma: no cover - echter Netzwerkpfad
            self.fehler.append(f"{type(exc).__name__}: {exc}")


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
    "VAPID_PUBLIC",
    "ConsoleSink",
    "FileSink",
    "Notification",
    "Notifier",
    "Severity",
    "Sink",
    "TelegramSink",
    "WebPushSink",
]
