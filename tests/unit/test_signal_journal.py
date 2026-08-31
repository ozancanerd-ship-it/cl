"""runtime.signal_journal — persistiert jedes Signal + jede Revision + jeden Shadow-Trade-Schritt."""

from __future__ import annotations

from datetime import UTC, datetime

from trading_agent.runtime.bus import EventBus
from trading_agent.runtime.events import (
    AlertRaised,
    DecisionMade,
    PaperPositionChanged,
    SignalRevised,
)
from trading_agent.runtime.signal_journal import SignalJournal

_T0 = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)


class _E:
    def __init__(self, **kw: object) -> None:
        self.__dict__.update(kw)


async def test_journal_writes_signal_revision_trade_alert(tmp_path) -> None:
    bus = EventBus(raise_on_handler_error=False)
    j = SignalJournal(tmp_path / "sj.jsonl")
    j.attach(bus)

    await bus.publish(
        DecisionMade(
            ts=_T0, instrument="XAUUSDT", decision_type="buy", setup_state="armed", result=_E()
        )
    )
    await bus.publish(
        DecisionMade(
            ts=_T0,
            instrument="XAUUSDT",
            decision_type="no_trade",
            setup_state="scanning",
            result=_E(),
        )
    )
    await bus.publish(
        SignalRevised(
            ts=_T0,
            instrument="XAUUSDT",
            signal_id="s1",
            state="armed",
            change="revised",
            signal=_E(
                is_new=False,
                revision=_E(
                    snapshot={"revision": 2, "score": 74.0, "entry": 4480.0},
                    change_kind=_E(value="sl_changed"),
                    changes=("sl 4460 -> 4465",),
                ),
            ),
        )
    )
    await bus.publish(
        PaperPositionChanged(
            ts=_T0,
            instrument="XAUUSDT",
            change="TP1",
            realized_r=0.5,
            position=_E(position_id="SETUP-BREAKOUT-RETEST-01", state=_E(value="tp1_reached")),
        )
    )
    await bus.publish(
        AlertRaised(ts=_T0, instrument="XAUUSDT", alert_type="signal_revised", message="SL moved")
    )

    rows = j.read()
    kinds = [r["kind"] for r in rows]
    assert kinds == ["signal", "revision", "trade", "alert"]  # no_trade nicht journalisiert
    assert j.counts == {"signal": 1, "revision": 1, "trade": 1, "alert": 1}
    assert rows[0]["decision"] == "BUY" and "ts" in rows[0] and "logged_at" in rows[0]
    assert rows[1]["revision"]["score"] == 74.0 and rows[1]["change_kind"] == "sl_changed"
    assert rows[2]["change"] == "TP1" and rows[2]["realized_r"] == 0.5
    assert rows[2]["position_id"] == "SETUP-BREAKOUT-RETEST-01"


async def test_journal_applies_live_gate_and_renders_report(tmp_path) -> None:
    from trading_agent.core.enums import DecisionType, Direction
    from trading_agent.governance import ValidationRegistry, apply_live_gate
    from trading_agent.strategy.signal_report import build_signal_report

    d = _E(
        decision=DecisionType.BUY,
        instrument="XAUUSDT",
        information_cutoff=_T0,
        setup_state=_E(value="armed"),
        setup_id="SETUP-BREAKOUT-RETEST-01",
        strategy_version="0.1.1",
        direction=Direction.LONG,
        chain_progress="Ausbruch long → Retest hält",
        reason_codes=(),
        entry=4480.0,
        sl=4460.0,
        tp1=4520.0,
        tp2=4560.0,
        tp3_ref="Runner",
        rr_to_tp2=4.0,
        blended_rr=2.1,
        tier=_E(value="B"),
        confidence=0.62,
    )
    result = _E(
        decision=d,
        mtf=_E(per_tf={}, htf_directional=_E(value="trend_up")),
        confluence=None,
        contradictions=None,
        live_gate=None,
    )
    bus = EventBus(raise_on_handler_error=False)
    j = SignalJournal(tmp_path / "sj.jsonl", build_report=build_signal_report)
    j.configure(apply_live_gate=apply_live_gate, registry=ValidationRegistry.default())
    j.attach(bus)

    await bus.publish(
        DecisionMade(
            ts=_T0, instrument="XAUUSDT", decision_type="buy", setup_state="armed", result=result
        )
    )
    rows = j.read()
    assert len(rows) == 1
    r = rows[0]
    assert r["eligibility"] == "shadow"  # IN_VALIDATION → SHADOW
    assert r["report"]["entry"] == 4480.0 and r["report"]["tp2"] == 4560.0
    assert r["report"]["setup_id"] == "SETUP-BREAKOUT-RETEST-01"
    assert r["live_gate"]["eligibility"] == "shadow"
