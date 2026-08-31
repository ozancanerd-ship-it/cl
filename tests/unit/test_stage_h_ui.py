"""Stufe H — Chart-Annotationen + Dashboard-State-Assembler (Masterplan §58/§63–§70)."""

from __future__ import annotations

from datetime import UTC, datetime

from trading_agent.api.dashboard import TABS, DashboardInputs, build_dashboard_state
from trading_agent.chart.annotations import build_chart_annotations

_NOW = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)


class _E:
    def __init__(self, **kw: object) -> None:
        self.__dict__.update(kw)


def _signal() -> _E:
    return _E(
        instrument="XAUUSDT",
        information_cutoff=_NOW,
        action="BUY",
        direction="LONG",
        tier="A+",
        entry=4480.0,
        stop_loss=4460.0,
        tp1=4520.0,
        tp2=4560.0,
        tp3_indicative=4600.0,
    )


# --------------------------------------------------------------------------- chart annotations


def test_chart_annotations_from_signal() -> None:
    mtf = _E(
        per_tf={
            "H4": _E(
                liquidity=[_E(price=4600.0, kind="swing_high"), _E(price=4400.0, kind="swing_low")]
            )
        }
    )
    ann = build_chart_annotations(_signal(), mtf=mtf)
    titles = {ln.title for ln in ann.price_lines}
    assert {"Entry", "SL", "TP1", "TP2", "TP3 (indikativ)"} <= titles
    assert (
        ann.markers and ann.markers[0].shape == "arrowUp" and ann.markers[0].position == "belowBar"
    )
    assert len(ann.zones) == 2
    d = ann.as_dict()
    assert d["instrument"] == "XAUUSDT" and len(d["price_lines"]) == 5


def test_chart_annotations_short_marker_points_down() -> None:
    sr = _signal()
    sr.action, sr.direction = "SELL", "SHORT"
    ann = build_chart_annotations(sr)
    assert ann.markers[0].shape == "arrowDown" and ann.markers[0].position == "aboveBar"
    assert ann.zones == []


# --------------------------------------------------------------------------- dashboard


def test_dashboard_has_all_ten_tabs() -> None:
    state = build_dashboard_state(DashboardInputs(as_of=_NOW))
    assert set(state.tabs) == set(TABS)
    assert len(TABS) == 10
    # ohne Daten: die datengetriebenen Tabs sind unavailable, die Struktur-Tabs available
    assert state.tab("my_portfolios")["available"] is False
    assert state.tab("signals")["available"] is True
    assert "NO-TRADE" in state.tab("signals")["note"]


def test_dashboard_populated() -> None:
    inp = DashboardInputs(
        as_of=_NOW,
        top_opportunities=[
            {
                "rank": 1,
                "instrument": "XAUUSDT",
                "score": 82.0,
                "tier": "A",
                "setup_state": "armed",
            },
            {
                "rank": 2,
                "instrument": "BTCUSDT",
                "score": 30.0,
                "tier": None,
                "setup_state": "scanning",
            },
        ],
        scanner_evaluations=2,
        signals=[{"action": "BUY", "instrument": "XAUUSDT"}],
        portfolio={"equity": 10000, "health": {"score": 71, "grade": "GREEN"}, "available": True},
        paper_performance={"trades": 120, "win_rate": 0.55},
        breadth={"regime": "risk_on"},
        system_health={"grade": "GREEN"},
        blockers=["FRED_API_KEY fehlt"],
    )
    state = build_dashboard_state(inp)
    ov = state.tab("overview")
    assert ov["best_opportunity"]["instrument"] == "XAUUSDT"
    assert ov["actionable_setups"] == 1
    assert ov["blockers"] == ["FRED_API_KEY fehlt"]
    assert state.tab("top_opportunities")["actionable"][0]["instrument"] == "XAUUSDT"
    assert state.tab("my_portfolios")["available"] is True
    assert state.tab("my_portfolios")["equity"] == 10000
    assert state.tab("paper_trading")["validated"] is True
    assert state.tab("system_health")["strategy_version"] == "0.1.1"
    assert isinstance(state.as_dict()["tabs"], dict)
