"""Phase 3/4 · Live-Daten-Adapter (``data.providers.*``) — Vertrag + PIT + kein Fake.

adapter_base (status / credentials) · CsvEconomicCalendar (PIT) · cross_asset (nur echte Felder) ·
equities Corporate-Action-Anpassung (Look-ahead-Schutz) · MT5-Stub inert ohne Terminal.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from trading_agent.core.enums import ProviderHealth, RegimeDirectional
from trading_agent.data.providers.adapter_base import AdapterInfo, CredentialSpec, LiveDataAdapter
from trading_agent.data.providers.cross_asset import build_cross_asset_context
from trading_agent.data.providers.equities import CorporateAction, adjust_for_actions
from trading_agent.data.providers.mt5 import MT5Adapter
from trading_agent.data.providers.news_calendar import CANONICAL_EVENTS, CsvEconomicCalendar

AS_OF = datetime(2025, 3, 1, tzinfo=UTC)


# --------------------------------------------------------------------------- adapter_base


def test_adapter_status_unavailable_without_credentials(monkeypatch) -> None:
    monkeypatch.delenv("SOME_KEY", raising=False)
    a = LiveDataAdapter(
        AdapterInfo(
            name="x",
            asset_classes=("crypto",),
            data_kinds=(),
            modes=("stream",),
            credentials=CredentialSpec("x", env_vars=("SOME_KEY",)),
        )
    )
    st = a.status()
    assert st.health is ProviderHealth.UNAVAILABLE and "SOME_KEY" in st.detail

    monkeypatch.setenv("SOME_KEY", "present")
    assert a.status().health is ProviderHealth.HEALTHY


def test_mt5_stub_inert_off_windows() -> None:
    a = MT5Adapter()
    st = a.status()
    # außerhalb Windows / ohne Terminal → UNAVAILABLE, aber kein Crash
    assert st.health is ProviderHealth.UNAVAILABLE
    assert a.to_broker_symbol("xauusd") == "XAUUSD"
    assert a.info.credentials.read_only is True


# --------------------------------------------------------------------------- economic calendar


_CAL = """event_id,event_type,impact,scheduled_time,available_time,actual,forecast,previous
cpi-2025-02,US_CPI,high,2025-02-12T13:30:00Z,2024-12-01T00:00:00Z,,0.3,0.4
cpi-2025-02r,US_CPI,high,2025-02-12T13:30:00Z,2025-02-12T13:30:05Z,0.5,0.3,0.4
fomc-2025-03,FOMC_RATE,high,2025-03-19T18:00:00Z,2025-01-01T00:00:00Z,,,
"""


def test_csv_calendar_pit(tmp_path: Path) -> None:
    p = tmp_path / "cal.csv"
    p.write_text(_CAL)
    cal = CsvEconomicCalendar(p)

    # vor der CPI-Veröffentlichung: nur der geplante Eintrag (actual=None) ist sichtbar
    before = cal.get_calendar(
        datetime(2025, 2, 1, tzinfo=UTC),
        datetime(2025, 3, 1, tzinfo=UTC),
        as_of=datetime(2025, 2, 10, tzinfo=UTC),
    )
    assert [e.event_id for e in before] == ["cpi-2025-02"]
    assert before[0].actual is None and before[0].impact is CANONICAL_EVENTS["US_CPI"]

    # nach der Veröffentlichung: beide Einträge (geplant + Ist)
    after = cal.get_calendar(
        datetime(2025, 2, 1, tzinfo=UTC),
        datetime(2025, 3, 1, tzinfo=UTC),
        as_of=datetime(2025, 2, 13, tzinfo=UTC),
    )
    assert {e.event_id for e in after} == {"cpi-2025-02", "cpi-2025-02r"}
    assert next(e for e in after if e.event_id == "cpi-2025-02r").actual == 0.5


def test_csv_calendar_missing_file_no_fake(tmp_path: Path) -> None:
    cal = CsvEconomicCalendar(tmp_path / "nope.csv")
    assert cal.get_calendar(AS_OF, AS_OF + timedelta(days=30)) == []
    assert cal.status().health is ProviderHealth.DEGRADED


# --------------------------------------------------------------------------- cross-asset


def _series(prices: list[float], start: datetime, tf_min: int = 1440):
    from trading_agent.core.enums import Timeframe
    from trading_agent.core.models import OHLCV
    from trading_agent.core.time import bar_close_time

    out = []
    t = start
    for px in prices:
        out.append(
            OHLCV(
                instrument="IDX",
                timeframe=Timeframe.D1,
                open_time=t,
                close_time=bar_close_time(t, Timeframe.D1),
                open=px,
                high=px + 0.5,
                low=px - 0.5,
                close=px,
                volume=1.0,
                source="t",
            )
        )
        t += timedelta(minutes=tf_min)
    return out


def test_cross_asset_only_real_fields() -> None:
    ctx = build_cross_asset_context(as_of=AS_OF)  # nichts übergeben
    assert ctx.dxy_trend is None and ctx.vix is None and ctx.as_of is None and not ctx.risk_off

    start = datetime(2025, 1, 1, tzinfo=UTC)
    dxy = _series([100 + i * 0.3 for i in range(30)], start)  # steigend
    vix = _series([15.0] * 25 + [40.0], start)  # Spike
    ctx = build_cross_asset_context(as_of=AS_OF, dxy=dxy, vix=vix)
    assert ctx.dxy_trend is RegimeDirectional.TREND_UP
    assert ctx.vix == 40.0 and ctx.risk_off is True
    assert ctx.real_yield_10y is None  # nicht übergeben → None (kein Fake)


def test_cross_asset_from_repo_reads_series_and_missing_is_none() -> None:
    from trading_agent.core.enums import Timeframe
    from trading_agent.data.providers.cross_asset import build_cross_asset_from_repo

    start = datetime(2025, 1, 1, tzinfo=UTC)
    dxy = [
        b.model_copy(update={"instrument": "DXY-YF", "timeframe": Timeframe.D1})
        for b in _series([100 + i * 0.4 for i in range(40)], start, tf_min=1440)
    ]

    class _Repo:
        def read_ohlcv(self, sym: str, tf: object, s: object, e: object) -> list[object]:
            if sym == "DXY-YF":
                return dxy
            raise FileNotFoundError(sym)  # VIX-YF / US10Y-YF fehlen → Feld bleibt None

    ctx = build_cross_asset_from_repo(_Repo(), as_of=datetime(2025, 2, 5, tzinfo=UTC))
    assert ctx.dxy_trend is RegimeDirectional.TREND_UP
    assert ctx.vix is None and ctx.real_yield_10y is None  # kein Fake für fehlende Reihen
    assert ctx.as_of is not None


def test_cross_asset_pit_filter() -> None:
    start = datetime(2025, 1, 1, tzinfo=UTC)
    vix = _series([15.0] * 10 + [50.0] * 10, start)  # Spike liegt NACH as_of
    ctx = build_cross_asset_context(as_of=datetime(2025, 1, 5, tzinfo=UTC), vix=vix)
    assert ctx.vix == 15.0 and ctx.risk_off is False  # der Spike ist noch nicht sichtbar


def test_cross_asset_from_macro_pit() -> None:
    from trading_agent.core.models import MacroEvent
    from trading_agent.data.providers.cross_asset import build_cross_asset_from_macro

    def _ev(day: int, val: float, avail_day: int) -> MacroEvent:
        return MacroEvent(
            series_id="VIX",
            reference_period=datetime(2025, 1, day, tzinfo=UTC),
            value=val,
            available_time=datetime(2025, 1, avail_day, 12, 30, tzinfo=UTC),
            revision=0,
            source="fred_alfred",
        )

    vix = [_ev(1, 15.0, 2), _ev(6, 45.0, 7)]  # zweiter Wert erst ab 07.01. bekannt
    ctx = build_cross_asset_from_macro(as_of=datetime(2025, 1, 5, tzinfo=UTC), vix=vix)
    assert ctx.vix == 15.0 and ctx.risk_off is False  # 45er-Wert noch nicht available
    ctx2 = build_cross_asset_from_macro(as_of=datetime(2025, 1, 10, tzinfo=UTC), vix=vix)
    assert ctx2.vix == 45.0 and ctx2.risk_off is True


# --------------------------------------------------------------------------- equities corp actions


def test_split_adjustment_and_lookahead() -> None:
    from trading_agent.core.enums import Timeframe
    from trading_agent.core.models import OHLCV
    from trading_agent.core.time import bar_close_time

    start = datetime(2025, 1, 1, tzinfo=UTC)
    bars = []
    t = start
    for px in [100.0, 100.0, 100.0, 50.0, 50.0]:  # 2:1 Split am Tag 4
        bars.append(
            OHLCV(
                instrument="AAA",
                timeframe=Timeframe.D1,
                open_time=t,
                close_time=bar_close_time(t, Timeframe.D1),
                open=px,
                high=px,
                low=px,
                close=px,
                volume=10.0,
                source="t",
            )
        )
        t += timedelta(days=1)
    split = CorporateAction("AAA", start + timedelta(days=3), "split", ratio=2.0)

    # as_of NACH dem Split → angepasst: Vor-Split-Preise halbiert
    adj = adjust_for_actions(bars, [split], as_of=start + timedelta(days=10))
    assert [round(b.close, 2) for b in adj] == [50.0, 50.0, 50.0, 50.0, 50.0]
    assert adj[0].volume == 20.0  # Volumen invers skaliert

    # as_of VOR dem Split → keine Anpassung (Look-ahead-Schutz)
    not_adj = adjust_for_actions(bars, [split], as_of=start + timedelta(days=1))
    assert [b.close for b in not_adj] == [100.0, 100.0, 100.0, 50.0, 50.0]
