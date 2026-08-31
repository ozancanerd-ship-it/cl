"""Phase 3 — Sessions: DST-Auflösung, London/NY-Overlap, Session High/Low, Session-Range,
Session-Levels, §18 Entry-Gate. Look-ahead-Schutz (nur abgeschlossene Sessions).
"""

from __future__ import annotations

from datetime import timedelta

from trading_agent.analysis.sessions import (
    SessionFilterParams,
    active_session_names,
    completed_sessions,
    last_completed_session,
    resolve_sessions,
    session_filter,
    session_levels,
    session_range,
)
from trading_agent.core.enums import (
    LiquidityType,
    MarketSide,
    NoTradeReason,
    SessionName,
    Timeframe,
)
from trading_agent.core.models import OHLCV
from trading_agent.core.time import bar_close_time, parse_timestamp
from trading_agent.refdata.seed import seed_sessions

SPECS = seed_sessions()
MON = parse_timestamp("2024-06-03T00:00:00Z")  # Montag (Sommerzeit)


def _at(iso: str):
    return parse_timestamp(iso)


def _h1_bars(start: str, n: int, *, base: float = 100.0) -> list[OHLCV]:
    out: list[OHLCV] = []
    t = parse_timestamp(start)
    for i in range(n):
        p = base + i * 0.5
        out.append(
            OHLCV(
                instrument="BTCUSDT",
                timeframe=Timeframe.H1,
                open_time=t,
                close_time=bar_close_time(t, Timeframe.H1),
                open=p,
                high=p + 1.0,
                low=p - 1.0,
                close=p + 0.2,
                volume=1.0,
                source="t",
            )
        )
        t += timedelta(hours=1)
    return out


# --------------------------------------------------------------------------- Auflösung


def test_resolve_sessions_summer_day_with_overlap() -> None:
    ws = {w.name: w for w in resolve_sessions(SPECS, MON)}
    assert set(ws) == {
        SessionName.ASIA,
        SessionName.LONDON,
        SessionName.NEW_YORK,
        SessionName.LONDON_NY_OVERLAP,
    }
    # Sommerzeit: London BST 08:00 -> 07:00 UTC, NY EDT 09:30 -> 13:30 UTC
    assert ws[SessionName.LONDON].start == _at("2024-06-03T07:00:00Z")
    assert ws[SessionName.NEW_YORK].start == _at("2024-06-03T13:30:00Z")
    ov = ws[SessionName.LONDON_NY_OVERLAP]
    assert ov.start == max(ws[SessionName.LONDON].start, ws[SessionName.NEW_YORK].start)
    assert ov.end == min(ws[SessionName.LONDON].end, ws[SessionName.NEW_YORK].end)
    assert ov.start == _at("2024-06-03T13:30:00Z") and ov.end == _at("2024-06-03T15:30:00Z")


def test_resolve_sessions_weekend_is_empty() -> None:
    assert resolve_sessions(SPECS, parse_timestamp("2024-06-08T00:00:00Z")) == []  # Samstag


def test_active_session_names() -> None:
    assert active_session_names(SPECS, _at("2024-06-03T14:00:00Z")) == {
        SessionName.LONDON,
        SessionName.NEW_YORK,
        SessionName.LONDON_NY_OVERLAP,
    }
    assert active_session_names(SPECS, _at("2024-06-03T04:00:00Z")) == {SessionName.ASIA}
    assert active_session_names(SPECS, _at("2024-06-03T22:00:00Z")) == set()


# --------------------------------------------------------------------------- §18 Gate


def test_session_filter_reasons() -> None:
    assert session_filter(_at("2024-06-08T12:00:00Z"), SPECS) is NoTradeReason.WEEKEND  # Sa
    assert session_filter(_at("2024-06-09T12:00:00Z"), SPECS) is NoTradeReason.WEEKEND  # So
    assert session_filter(_at("2024-06-07T23:40:00Z"), SPECS) is NoTradeReason.PRE_WEEKEND_BUFFER
    assert session_filter(_at("2024-06-03T05:00:00Z"), SPECS) is NoTradeReason.SESSION_NOT_ALLOWED
    assert session_filter(_at("2024-06-03T07:05:00Z"), SPECS) is NoTradeReason.SESSION_OPEN_BUFFER
    assert session_filter(_at("2024-06-03T14:00:00Z"), SPECS) is None  # Overlap, alles frei


def test_session_filter_params() -> None:
    # avoid_weekend abgeschaltet -> Samstag durchlässt (dann greift nur SESSION_NOT_ALLOWED)
    r = session_filter(
        _at("2024-06-08T12:00:00Z"), SPECS, params=SessionFilterParams(avoid_weekend=False)
    )
    assert r is NoTradeReason.SESSION_NOT_ALLOWED
    # Asia in allowed -> Montag 05:00 ok
    assert (
        session_filter(
            _at("2024-06-03T05:00:00Z"),
            SPECS,
            params=SessionFilterParams(allowed=(SessionName.ASIA,), avoid_first_min=0),
        )
        is None
    )


# --------------------------------------------------------------------------- abgeschlossene Sessions


def test_completed_sessions_high_low() -> None:
    bars = _h1_bars("2024-06-03T00:00:00Z", 36)  # Mo 00:00 .. Di 12:00 UTC
    lon = last_completed_session(bars, SPECS, SessionName.LONDON)
    assert lon is not None and lon.start == _at("2024-06-03T07:00:00Z")  # Montags-London
    inside = [b for b in bars if lon.start <= b.open_time < lon.end]
    assert lon.high == max(b.high for b in inside)
    assert lon.low == min(b.low for b in inside)
    # Dienstags-London (endet 15:30) ist noch NICHT abgeschlossen (Bars enden Di 12:00)
    names_days = [(w.name, w.start.date()) for w in completed_sessions(bars, SPECS)]
    assert (SessionName.LONDON, _at("2024-06-04T00:00:00Z").date()) not in names_days


def test_completed_sessions_lookahead_immune() -> None:
    short = _h1_bars("2024-06-03T00:00:00Z", 30)
    long = _h1_bars("2024-06-03T00:00:00Z", 60)
    a = last_completed_session(short, SPECS, SessionName.LONDON)
    b = last_completed_session(long, SPECS, SessionName.LONDON)
    assert a is not None and b is not None
    # das Montags-London-Fenster darf sich durch spätere Bars nicht ändern
    a_mon = completed_sessions(short, SPECS, name=SessionName.LONDON)[0]
    b_mon = completed_sessions(long, SPECS, name=SessionName.LONDON)[0]
    assert (a_mon.start, a_mon.end, a_mon.high, a_mon.low) == (
        b_mon.start,
        b_mon.end,
        b_mon.high,
        b_mon.low,
    )


def test_session_range() -> None:
    bars = _h1_bars(
        "2024-06-03T00:00:00Z", 30
    )  # NY endet 20:00; Bars bis Mo 06:00? nein: 30h -> Di 06:00
    rng = session_range(bars, SPECS, name=SessionName.NEW_YORK)
    assert rng is not None
    lo, hi = rng
    ny = last_completed_session(bars, SPECS, SessionName.NEW_YORK)
    assert ny is not None and (lo, hi) == (ny.low, ny.high) and hi > lo


def test_session_range_none_when_no_completed_session() -> None:
    bars = _h1_bars("2024-06-03T00:00:00Z", 3)  # nur 3 Bars, keine Session abgeschlossen
    assert session_range(bars, SPECS, name=SessionName.NEW_YORK) is None


def test_session_levels() -> None:
    bars = _h1_bars("2024-06-03T00:00:00Z", 36)
    windows = completed_sessions(bars, SPECS, name=SessionName.LONDON)
    levels = session_levels(windows, timeframe=Timeframe.H4)
    highs = [x for x in levels if x.type is LiquidityType.SESSION_HIGH]
    lows = [x for x in levels if x.type is LiquidityType.SESSION_LOW]
    assert highs and lows
    assert highs[0].side is MarketSide.BUY_SIDE and lows[0].side is MarketSide.SELL_SIDE
    assert highs[0].price == windows[0].high and highs[0].formed_at == windows[0].end
    assert highs[0].timeframe is Timeframe.H4
