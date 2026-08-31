"""OHLCV-Resampling von niedrigen auf höhere Timeframes – strikt look-ahead-frei.

Regeln:

* Ziel-Timeframe muss ein ganzzahliges Vielfaches des Quell-Timeframes sein.
* Eine Ziel-Bar wird **nur** ausgegeben, wenn **alle** ihre Bausteine vorhanden sind
  (``require_complete=True``, Default). Unvollständige (noch laufende) Ziel-Bars werden
  weggelassen – so entsteht kein Blick in die Zukunft.
* ``open`` = erster Quell-Open, ``close`` = letzter Quell-Close, ``high``/``low`` = Extremwerte,
  ``volume`` = Summe.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from trading_agent.core.enums import Timeframe
from trading_agent.core.models import OHLCV
from trading_agent.core.time import align_down, bar_close_time, ensure_utc
from trading_agent.data.quality import sort_ohlcv


class ResampleError(ValueError):
    pass


def resample_ohlcv(
    bars: list[OHLCV],
    source: Timeframe,
    target: Timeframe,
    *,
    require_complete: bool = True,
    horizon: datetime | None = None,
    source_name: str = "resampled",
) -> list[OHLCV]:
    """Aggregiert ``bars`` (Timeframe ``source``) auf ``target``.

    ``horizon`` (falls gesetzt): Ziel-Bars, die nach ``horizon`` schließen, werden weggelassen
    (zusätzlicher Point-in-Time-Schutz).
    """
    if target.seconds <= source.seconds:
        raise ResampleError(f"Ziel-Timeframe {target} muss größer als Quelle {source} sein")
    if target.seconds % source.seconds != 0:
        raise ResampleError(
            f"{target} ({target.seconds}s) ist kein Vielfaches von {source} ({source.seconds}s)"
        )
    expected_per_group = target.seconds // source.seconds
    horizon = ensure_utc(horizon) if horizon is not None else None

    ordered = sort_ohlcv(bars)
    groups: dict[datetime, list[OHLCV]] = {}
    for bar in ordered:
        if bar.timeframe is not source:
            raise ResampleError(f"Bar hat Timeframe {bar.timeframe}, erwartet Quelle {source}")
        key = align_down(bar.open_time, target)
        groups.setdefault(key, []).append(bar)

    out: list[OHLCV] = []
    step = timedelta(seconds=source.seconds)
    for open_time in sorted(groups):
        members = groups[open_time]
        close_time = bar_close_time(open_time, target)

        if horizon is not None and close_time > horizon:
            continue

        # Vollständigkeit prüfen: alle erwarteten Slots belegt, keine Duplikate.
        member_opens = {m.open_time for m in members}
        expected_opens = {open_time + i * step for i in range(expected_per_group)}
        if require_complete and member_opens != expected_opens:
            continue

        members_sorted = sorted(members, key=lambda m: m.open_time)
        first, last = members_sorted[0], members_sorted[-1]
        qv = (
            sum(m.quote_volume for m in members_sorted)  # type: ignore[misc]
            if all(m.quote_volume is not None for m in members_sorted)
            else None
        )
        tr = (
            sum(m.trades for m in members_sorted)  # type: ignore[misc]
            if all(m.trades is not None for m in members_sorted)
            else None
        )
        out.append(
            OHLCV(
                instrument=first.instrument,
                timeframe=target,
                open_time=open_time,
                close_time=close_time,
                open=first.open,
                high=max(m.high for m in members_sorted),
                low=min(m.low for m in members_sorted),
                close=last.close,
                volume=sum(m.volume for m in members_sorted),
                quote_volume=qv,
                trades=tr,
                source=source_name,
            )
        )
    return out


__all__ = ["ResampleError", "resample_ohlcv"]
