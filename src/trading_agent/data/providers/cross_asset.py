"""Cross-Asset-Kontext — DXY / US-Renditen / VIX / Leitindizes → ``CrossAssetContext``.

**Nur integrieren, wenn die Datenqualität reicht.** Der Builder füllt **ausschließlich** Felder,
für die echte Bars übergeben wurden; alles andere bleibt ``None`` (kein Fake). Die Confluence
bewertet fehlende Felder als ``UNAVAILABLE``, nicht neutral-positiv.

Alle Reihen sind OHLCV — die Quelle (Yahoo, Stooq, Broker-CFD, …) ist über den generischen
``HistoricalOHLCVProvider`` austauschbar. Point-in-Time: nur Bars mit ``close_time <= as_of``.
"""

from __future__ import annotations

import statistics
from collections.abc import Sequence
from datetime import datetime

from trading_agent.core.enums import RegimeDirectional, Timeframe
from trading_agent.core.models import OHLCV, MacroEvent
from trading_agent.core.time import ensure_utc
from trading_agent.core.types import CrossAssetContext

# interne, quellen-agnostische Darstellung: (Zeitpunkt-ab-dem-bekannt, Wert)
_Point = tuple[datetime, float]


def _pit(points: Sequence[_Point], as_of: datetime) -> list[_Point]:
    return [p for p in points if p[0] <= as_of]


def _trend(points: Sequence[_Point], *, window: int = 20, eps: float = 0.005) -> RegimeDirectional:
    seg = list(points)[-window:]
    if len(seg) < 3:
        return RegimeDirectional.UNCLEAR
    first, last = seg[0][1], seg[-1][1]
    if first <= 0:
        return RegimeDirectional.UNCLEAR
    chg = (last - first) / first
    if chg > eps:
        return RegimeDirectional.TREND_UP
    if chg < -eps:
        return RegimeDirectional.TREND_DOWN
    return RegimeDirectional.RANGE


def _risk_off(vix: Sequence[_Point], *, high: float = 25.0, spike_z: float = 2.0) -> bool:
    seg = list(vix)[-60:]
    if not seg:
        return False
    cur = seg[-1][1]
    if cur >= high:
        return True
    if len(seg) >= 20:
        hist = [p[1] for p in seg[:-1]]
        mu = statistics.fmean(hist)
        sd = statistics.pstdev(hist) or 1e-9
        return (cur - mu) / sd >= spike_z
    return False


def _from_ohlcv(bars: Sequence[OHLCV]) -> list[_Point]:
    return [(b.close_time, b.close) for b in bars]


def _from_macro(events: Sequence[MacroEvent]) -> list[_Point]:
    """(available_time, value) — nur der erstbekannte Wert je Referenzperiode, nach
    available_time sortiert. ``available_time`` ist das echte PIT-Gate."""
    seen: set[datetime] = set()
    out: list[_Point] = []
    for e in sorted(events, key=lambda x: (x.available_time, x.revision)):
        if e.reference_period in seen:
            continue
        seen.add(e.reference_period)
        out.append((e.available_time, float(e.value)))
    return out


def _build(
    *,
    as_of: datetime,
    dxy: list[_Point] | None,
    us10y_yield: list[_Point] | None,
    vix: list[_Point] | None,
) -> CrossAssetContext:
    as_of = ensure_utc(as_of)
    dxy_trend: RegimeDirectional | None = None
    real_yield: float | None = None
    vix_level: float | None = None
    risk_off = False

    if dxy:
        d = _pit(dxy, as_of)
        if d:
            dxy_trend = _trend(d)
    if us10y_yield:
        y = _pit(us10y_yield, as_of)
        if y:
            real_yield = round(y[-1][1], 4)
    if vix:
        v = _pit(vix, as_of)
        if v:
            vix_level = round(v[-1][1], 3)
            risk_off = _risk_off(v)

    return CrossAssetContext(
        dxy_trend=dxy_trend,
        real_yield_10y=real_yield,
        vix=vix_level,
        risk_off=risk_off,
        as_of=as_of if (dxy or us10y_yield or vix) else None,
    )


def build_cross_asset_context(
    *,
    as_of: datetime,
    dxy: Sequence[OHLCV] | None = None,
    us10y_yield: Sequence[OHLCV] | None = None,
    vix: Sequence[OHLCV] | None = None,
) -> CrossAssetContext:
    return _build(
        as_of=as_of,
        dxy=_from_ohlcv(dxy) if dxy else None,
        us10y_yield=_from_ohlcv(us10y_yield) if us10y_yield else None,
        vix=_from_ohlcv(vix) if vix else None,
    )


def build_cross_asset_from_repo(
    repo: object,
    *,
    as_of: datetime,
    timeframe: Timeframe = Timeframe.D1,
    dxy_symbol: str = "DXY-YF",
    us10y_symbol: str = "US10Y-YF",
    vix_symbol: str = "VIX-YF",
    lookback_days: int = 400,
) -> CrossAssetContext:
    """``CrossAssetContext`` aus im Repo liegenden Cross-Asset-Reihen (z. B. keylos via
    ``scripts/ingest_yahoo.py`` als ``DXY-YF`` / ``US10Y-YF`` / ``VIX-YF``). Fehlt eine Reihe,
    bleibt ihr Feld ``None`` — kein Fake. PIT: nur Bars mit ``close_time <= as_of``."""
    from datetime import timedelta

    as_of = ensure_utc(as_of)
    start = as_of - timedelta(days=lookback_days)

    def _read(sym: str) -> list[OHLCV] | None:
        reader = getattr(repo, "read_ohlcv", None)
        if reader is None:
            return None
        try:
            bars = [b for b in reader(sym, timeframe, start, as_of) if b.close_time <= as_of]
        except Exception:  # fehlende Reihe ist kein Fehler
            return None
        return bars or None

    return build_cross_asset_context(
        as_of=as_of,
        dxy=_read(dxy_symbol),
        us10y_yield=_read(us10y_symbol),
        vix=_read(vix_symbol),
    )


def build_cross_asset_from_macro(
    *,
    as_of: datetime,
    dxy: Sequence[MacroEvent] | None = None,
    us10y_yield: Sequence[MacroEvent] | None = None,
    vix: Sequence[MacroEvent] | None = None,
) -> CrossAssetContext:
    """Wie ``build_cross_asset_context``, aber aus FRED-``MacroEvent``-Reihen
    (``data.providers.fred_alfred``). PIT bleibt exakt: ``available_time`` ist das Gate,
    der Builder filtert ``<= as_of``."""
    return _build(
        as_of=as_of,
        dxy=_from_macro(dxy) if dxy else None,
        us10y_yield=_from_macro(us10y_yield) if us10y_yield else None,
        vix=_from_macro(vix) if vix else None,
    )


__all__ = [
    "build_cross_asset_context",
    "build_cross_asset_from_macro",
    "build_cross_asset_from_repo",
]
