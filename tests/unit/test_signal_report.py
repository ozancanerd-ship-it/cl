"""strategy.signal_report — konkretes BUY/SELL-Signal (Masterplan §24)."""

from __future__ import annotations

from datetime import UTC, datetime

from trading_agent.core.enums import DecisionType, Direction
from trading_agent.strategy.signal_report import build_signal_report

_CUT = datetime(2026, 8, 30, 12, 0, tzinfo=UTC)


class _E:
    def __init__(self, **kw: object) -> None:
        self.__dict__.update(kw)


def _factor(reason: str, contribution: float) -> _E:
    return _E(scored=True, contribution=contribution, reason=reason, factor=_E(value=reason))


def _buy_result() -> _E:
    d = _E(
        decision=DecisionType.BUY,
        instrument="XAUUSDT",
        information_cutoff=_CUT,
        setup_state=_E(value="armed"),
        setup_id="SMC-SWEEP-REV-01",
        strategy_version="0.1.1",
        direction=Direction.LONG,
        chain_progress="armed",
        reason_codes=(),
        entry=4480.0,
        sl=4460.0,
        tp1=4520.0,
        tp2=4560.0,
        tp3_ref="Runner: Trailing M15, aktiv nach TP2",
        rr_to_tp2=4.0,
        blended_rr=3.1,
        tier=_E(value="A+"),
        confidence=0.88,
    )
    mtf = _E(
        htf_directional=_E(value="trend_up"),
        per_tf={
            "H4": _E(liquidity=[_E(price=4600.0), _E(price=4650.0), _E(price=4400.0)]),
        },
    )
    conf = _E(
        factors=[
            _factor("D1 higher-lows bestätigt", 0.9),
            _factor("Sell-side sweep + Reclaim auf M15", 0.85),
            _factor("Displacement > 1.5·ATR", 0.7),
        ]
    )
    contra = _E(
        records=[
            _E(kind=_E(value="negative_factor"), reason="wide SL (1.4·ATR)"),
            _E(kind=_E(value="veto_echo"), reason="ignoriert"),
        ]
    )
    return _E(decision=d, mtf=mtf, confluence=conf, contradictions=contra)


def test_build_signal_report_full() -> None:
    r = build_signal_report(
        _buy_result(), opportunity=_E(score=91.0), risk_pct=1.0, trading_horizon="swing"
    )
    assert r is not None
    assert r.action == "BUY" and r.direction == "LONG" and r.tier == "A+"
    assert r.entry == 4480.0 and r.stop_loss == 4460.0
    assert r.tp1 == 4520.0 and r.tp2 == 4560.0
    assert "Runner" in r.tp3
    assert r.tp3_indicative == 4600.0  # nächste Liquidität > TP2 (LONG)
    assert r.rr_to_tp2 == 4.0 and r.opportunity_score == 91.0
    assert r.confidence_pct == 88.0 and r.risk_pct == 1.0
    assert any("higher-lows" in w for w in r.why)
    assert any("wide SL" in x for x in r.risks)
    assert any("news: nicht geprüft" in x for x in r.risks)
    assert "4460" in r.invalidation and "unter" in r.invalidation

    txt = r.as_text()
    assert "🔥 A+ BUY · XAUUSDT · LONG" in txt
    assert "Entry:        4480" in txt and "TP2:          4560" in txt
    dd = r.as_dict()
    assert dd["action"] == "BUY" and dd["tp3_indicative"] == 4600.0


def test_no_report_for_no_trade() -> None:
    r = _buy_result()
    r.decision.decision = DecisionType.NO_TRADE
    assert build_signal_report(r) is None


def test_no_report_when_geometry_missing() -> None:
    r = _buy_result()
    r.decision.tp2 = None
    assert build_signal_report(r) is None


def test_short_signal_invalidation_direction() -> None:
    r = _buy_result()
    r.decision.decision = DecisionType.SELL
    r.decision.direction = Direction.SHORT
    r.decision.entry, r.decision.sl = 4480.0, 4500.0
    r.decision.tp1, r.decision.tp2 = 4440.0, 4400.0
    r.mtf.per_tf["H4"].liquidity = [_E(price=4350.0), _E(price=4300.0), _E(price=4600.0)]
    rep = build_signal_report(r)
    assert rep is not None and rep.direction == "SHORT"
    assert "über 4500" in rep.invalidation
    assert rep.tp3_indicative == 4350.0  # nächste Liquidität < TP2 (SHORT)
