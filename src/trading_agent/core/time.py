"""Zeit-Hilfsfunktionen: UTC-Normalisierung, Timeframe-Alignment, DST-sichere Lokalzeit-Auflösung.

Grundregeln (verbindlich für das ganze System):

* Intern ist **jede** Zeit ein *timezone-aware* ``datetime`` in **UTC**.
* Naive ``datetime`` (ohne ``tzinfo``) werden **abgelehnt**, nie stillschweigend als UTC angenommen.
* Bars sind an Timeframe-Grenzen (UTC) ausgerichtet. Eine Bar mit Label ``t`` deckt
  ``[t, t + Δ)`` ab und **schließt** zu ``t + Δ``.
* Session-Zeiten werden in **Börsenlokalzeit** definiert und zur Laufzeit DST-korrekt nach UTC
  aufgelöst (mehrdeutige/nicht existierende Ortszeiten werden erkannt).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from trading_agent.core.enums import Timeframe

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
# 1970-01-01 war ein Donnerstag. Für W1-Alignment auf Montag 00:00 UTC brauchen wir 4 Tage Offset.
_W1_OFFSET_SECONDS = 4 * 86400


class TimeError(ValueError):
    """Ungültige/mehrdeutige Zeitangabe."""


def ensure_utc(dt: datetime) -> datetime:
    """Gibt ``dt`` als UTC zurück. Wirft ``TimeError`` bei naivem ``datetime``."""
    if dt.tzinfo is None:
        raise TimeError("naive datetime ist nicht erlaubt – tzinfo erforderlich (erwartet UTC)")
    return dt.astimezone(UTC)


def parse_timestamp(value: datetime | date | int | float | str) -> datetime:
    """Normalisiert diverse Eingaben zu einem UTC-``datetime``.

    * ``datetime`` – muss tz-aware sein, wird nach UTC konvertiert.
    * ``date`` – als Mitternacht UTC interpretiert.
    * ``int``/``float`` – Epoch-Sekunden, oder Millisekunden falls Betrag >= 1e11.
    * ``str`` – ISO-8601. ``...Z`` und Offsets werden verstanden; ein reines Datum wird zu
      Mitternacht UTC. Ein ISO-String **ohne** Offset wird abgelehnt (mehrdeutig).
    """
    if isinstance(value, datetime):
        return ensure_utc(value)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=UTC)
    if isinstance(value, bool):  # bool ist Subklasse von int – hier nie sinnvoll
        raise TimeError(f"bool ist kein Timestamp: {value!r}")
    if isinstance(value, (int, float)):
        if value != value:  # NaN
            raise TimeError("NaN ist kein Timestamp")
        seconds = value / 1000.0 if abs(value) >= 1e11 else float(value)
        return _EPOCH + timedelta(seconds=seconds)
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            raise TimeError("leerer String ist kein Timestamp")
        iso = raw.replace("Z", "+00:00") if raw.endswith("Z") else raw
        try:
            parsed = datetime.fromisoformat(iso)
        except ValueError as exc:
            raise TimeError(f"kein gültiger ISO-8601-Timestamp: {value!r}") from exc
        if parsed.tzinfo is None:
            # Ein reines Datum (YYYY-MM-DD) ist ok -> Mitternacht UTC.
            if len(raw) == 10 and raw.count("-") == 2:
                return parsed.replace(tzinfo=UTC)
            raise TimeError(f"ISO-Timestamp ohne Zeitzone ist mehrdeutig: {value!r}")
        return parsed.astimezone(UTC)
    raise TimeError(f"nicht unterstützter Timestamp-Typ: {type(value)!r}")


def to_epoch_ms(dt: datetime) -> int:
    """UTC-``datetime`` -> Epoch-Millisekunden (int, gerundet)."""
    return round((ensure_utc(dt) - _EPOCH).total_seconds() * 1000)


def from_epoch_ms(ms: int) -> datetime:
    return _EPOCH + timedelta(milliseconds=ms)


def _seconds_since_epoch(dt: datetime) -> float:
    return (ensure_utc(dt) - _EPOCH).total_seconds()


def is_aligned(dt: datetime, timeframe: Timeframe) -> bool:
    """True, wenn ``dt`` exakt auf einer Timeframe-Grenze (UTC) liegt."""
    secs = _seconds_since_epoch(dt)
    if secs != int(secs):
        return False
    step = timeframe.seconds
    if timeframe is Timeframe.W1:
        return (int(secs) - _W1_OFFSET_SECONDS) % step == 0
    return int(secs) % step == 0


def align_down(dt: datetime, timeframe: Timeframe) -> datetime:
    """Größte Timeframe-Grenze ``<= dt``."""
    secs = _seconds_since_epoch(dt)
    step = timeframe.seconds
    offset = _W1_OFFSET_SECONDS if timeframe is Timeframe.W1 else 0
    floored = ((int(secs) - offset) // step) * step + offset
    if floored > secs:  # bei negativen Sekunden (vor Epoch) korrigieren
        floored -= step
    return _EPOCH + timedelta(seconds=floored)


def align_up(dt: datetime, timeframe: Timeframe) -> datetime:
    """Kleinste Timeframe-Grenze ``>= dt``."""
    down = align_down(dt, timeframe)
    return down if down == dt else down + timedelta(seconds=timeframe.seconds)


def bar_close_time(open_time: datetime, timeframe: Timeframe) -> datetime:
    """Schlusszeitpunkt der Bar, die zu ``open_time`` beginnt."""
    return ensure_utc(open_time) + timedelta(seconds=timeframe.seconds)


def bar_open_time(close_time: datetime, timeframe: Timeframe) -> datetime:
    """Öffnungszeitpunkt der Bar, die zu ``close_time`` schließt."""
    return ensure_utc(close_time) - timedelta(seconds=timeframe.seconds)


def iter_bar_opens(start: datetime, end: datetime, timeframe: Timeframe) -> list[datetime]:
    """Alle Bar-Öffnungszeiten ``o`` mit ``start <= o < end`` (beide UTC, an Grenzen ausgerichtet)."""
    start = ensure_utc(start)
    end = ensure_utc(end)
    if not is_aligned(start, timeframe):
        start = align_up(start, timeframe)
    out: list[datetime] = []
    step = timedelta(seconds=timeframe.seconds)
    cur = start
    while cur < end:
        out.append(cur)
        cur += step
    return out


def load_zone(tz_name: str) -> ZoneInfo:
    try:
        return ZoneInfo(tz_name)
    except ZoneInfoNotFoundError as exc:  # pragma: no cover - abhängig von tzdata
        raise TimeError(f"unbekannte Zeitzone: {tz_name!r}") from exc


def resolve_local_time(day: date, wall: time, tz_name: str) -> datetime:
    """Löst eine Ortszeit (``day`` + ``wall`` in Zone ``tz_name``) DST-sicher nach UTC auf.

    Erkennt:
    * **nicht existierende** Ortszeiten (Frühjahrs-Umstellung, Uhr springt vor) und
    * **mehrdeutige** Ortszeiten (Herbst-Umstellung, Uhr springt zurück).

    In beiden Fällen wird ``TimeError`` geworfen – die aufrufende Session-Logik muss das
    behandeln (z. B. ``DST_AMBIGUOUS``-Datenqualitätsbefund), nicht raten.
    """
    zone = load_zone(tz_name)
    naive = datetime.combine(day, wall)
    aware = naive.replace(tzinfo=zone)

    # Reihenfolge wichtig: an einer DST-Grenze unterscheiden sich fold=0/fold=1 immer im
    # Offset – das allein sagt noch nicht, OB die Zeit fehlt oder doppelt ist.

    # (1) Nicht existent (Frühjahr, Uhr springt vor): Roundtrip über UTC ändert die Wanduhrzeit.
    roundtrip = aware.astimezone(UTC).astimezone(zone)
    if roundtrip.replace(tzinfo=None) != naive:
        raise TimeError(
            f"nicht existierende Ortszeit {naive.isoformat()} in {tz_name} (DST-Vorstellung)"
        )

    # (2) Mehrdeutig (Herbst, Uhr springt zurück): fold=0 und fold=1 sind beide gültig,
    #     ergeben aber unterschiedliche UTC-Zeitpunkte.
    alt = naive.replace(tzinfo=zone, fold=1)
    if aware.utcoffset() != alt.utcoffset():
        raise TimeError(f"mehrdeutige Ortszeit {naive.isoformat()} in {tz_name} (DST-Rückstellung)")

    return aware.astimezone(UTC)


def now_utc() -> datetime:
    """Nur für Stellen ohne injizierte Clock (Logging o. ä.). Strategie-/Datenlogik nutzt Clock."""
    return datetime.now(UTC)


__all__ = [
    "TimeError",
    "align_down",
    "align_up",
    "bar_close_time",
    "bar_open_time",
    "ensure_utc",
    "from_epoch_ms",
    "is_aligned",
    "iter_bar_opens",
    "load_zone",
    "now_utc",
    "parse_timestamp",
    "resolve_local_time",
    "to_epoch_ms",
]
