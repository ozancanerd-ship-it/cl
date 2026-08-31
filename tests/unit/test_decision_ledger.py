"""utils/tracing (contextvars) + journal/decision_ledger (Bus → SQLite)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from trading_agent.journal.decision_ledger import DecisionLedgerRecorder
from trading_agent.journal.ledger import Ledger
from trading_agent.runtime.bus import EventBus
from trading_agent.runtime.events import DecisionMade, PaperPositionChanged, SignalRevised
from trading_agent.utils.tracing import current_trace_id, new_trace_id, trace

# --------------------------------------------------------------------------- tracing


def test_trace_context_sets_and_restores() -> None:
    assert current_trace_id() is None
    with trace("abc") as tid:
        assert tid == "abc" and current_trace_id() == "abc"
        with trace() as inner:
            assert inner != "abc" and current_trace_id() == inner
        assert current_trace_id() == "abc"
    assert current_trace_id() is None


def test_new_trace_id_unique() -> None:
    ids = {new_trace_id("scan") for _ in range(100)}
    assert len(ids) == 100
    assert all(i.startswith("scan-") for i in ids)


async def test_trace_is_task_local() -> None:
    seen: list[str | None] = []

    async def worker(tid: str) -> None:
        with trace(tid):
            await asyncio.sleep(0)
            seen.append(current_trace_id())

    await asyncio.gather(worker("a"), worker("b"), worker("c"))
    assert sorted(x for x in seen if x) == ["a", "b", "c"]


# --------------------------------------------------------------------------- decision ledger


class _FakeDecision:
    decision = type("D", (), {"value": "buy"})()
    setup_state = type("S", (), {"value": "armed"})()
    direction = type("Dir", (), {"value": "long"})()
    setup_id = "SMC-SWEEP-REV-01"
    tier = type("T", (), {"value": "A"})()
    score = 84.0
    confidence = 0.79
    entry = 4480.0
    sl = 4460.0
    tp1 = 4520.0
    tp2 = 4560.0
    tp3_ref = "next_htf_liquidity"
    rr_to_tp2 = 4.0
    blended_rr = 3.2
    reason_codes = ()
    vetoes = ()
    chain_progress = "armed"


class _FakeResult:
    decision = _FakeDecision()


async def test_recorder_persists_decision_signal_position(tmp_path) -> None:
    ledger = Ledger(str(tmp_path / "dl.sqlite"))
    rec = DecisionLedgerRecorder(ledger, strategy_version="0.1.1")
    bus = EventBus(raise_on_handler_error=False)
    rec.attach(bus)
    ts = datetime(2026, 8, 30, 12, 0, tzinfo=UTC)

    await bus.publish(
        DecisionMade(
            ts=ts,
            instrument="XAUUSDT",
            decision_type="buy",
            setup_state="armed",
            score=84.0,
            confidence=0.79,
            result=_FakeResult(),
        )
    )
    await bus.publish(
        SignalRevised(
            ts=ts, instrument="XAUUSDT", signal_id="s1", state="active", change="new", signal=None
        )
    )
    await bus.publish(
        PaperPositionChanged(
            ts=ts, instrument="XAUUSDT", change="OPENED", realized_r=None, position=None
        )
    )

    assert rec.rows_written == 3
    rows = ledger.decisions_for("XAUUSDT-2026-08-30")
    steps = [r["step"] for r in rows]
    assert steps == ["DECISION", "SIGNAL", "POSITION"]
    import json

    dec_payload = json.loads(rows[0]["payload"])
    assert dec_payload["decision"] == "buy" and dec_payload["entry"] == 4480.0
    assert dec_payload["tier"] == "A" and dec_payload["tp3_ref"] == "next_htf_liquidity"
    assert rows[0]["strategy_version"] == "0.1.1"
