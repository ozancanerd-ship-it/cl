"""Tests: ``state.store`` (atomarer, versionierter Snapshot) + ``state.recovery`` (Round-Trip,
Lücken-Rechnung). Kein Netz, keine Fake-Daten."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from trading_agent.core.enums import Direction, Timeframe
from trading_agent.state.recovery import (
    backfillable,
    clamp_backfill_start,
    gap_bars,
    paper_position_from_dict,
    paper_position_to_dict,
)
from trading_agent.state.store import SNAPSHOT_SCHEMA_VERSION, SnapshotStore
from trading_agent.strategy.position import ExitReason, PaperPosition, PositionLeg, PositionState

M5 = Timeframe.M5


def test_snapshot_roundtrip_atomic(tmp_path) -> None:
    store = SnapshotStore(tmp_path / "state")
    payload = {"exchange": "bybit", "n": 3, "nested": {"a": [1, 2]}}
    path = store.save("live", payload)
    assert path.exists()
    loaded = store.load("live")
    assert loaded is not None
    assert loaded["exchange"] == "bybit" and loaded["n"] == 3
    assert "_saved_at" in loaded  # der Store hängt den Speicherzeitpunkt an


def test_snapshot_missing_returns_none(tmp_path) -> None:
    assert SnapshotStore(tmp_path).load("nope") is None


def test_snapshot_schema_mismatch_is_discarded(tmp_path) -> None:
    p = tmp_path / "state" / "live.json"
    p.parent.mkdir(parents=True)
    p.write_text('{"schema_version": 999, "payload": {"x": 1}}')
    assert SnapshotStore(tmp_path / "state").load("live") is None


def test_snapshot_corrupt_is_discarded(tmp_path) -> None:
    p = tmp_path / "state" / "live.json"
    p.parent.mkdir(parents=True)
    p.write_text("{ not json")
    assert SnapshotStore(tmp_path / "state").load("live") is None
    assert SNAPSHOT_SCHEMA_VERSION == 1


def test_gap_and_backfillable() -> None:
    now = datetime(2025, 3, 1, 12, 0, tzinfo=UTC)
    assert gap_bars(now - timedelta(minutes=30), now, M5) == 5  # 6 Slots − 1 (die letzte offen)
    assert gap_bars(now, now, M5) == 0
    assert backfillable(50)
    assert not backfillable(0)
    assert not backfillable(100_000)


def test_clamp_backfill_start_respects_rest_history() -> None:
    now = datetime(2025, 3, 1, 12, 0, tzinfo=UTC)
    # 30 Tage Lücke → clamp auf ~700 M5-Bars zurück (nicht 30 Tage)
    start = clamp_backfill_start(now - timedelta(days=30), now, M5)
    assert start > now - timedelta(days=3)
    # kleine Lücke bleibt unangetastet
    small = now - timedelta(minutes=20)
    assert clamp_backfill_start(small, now, M5) == small


def _pos(sid: str = "sig-1") -> PaperPosition:
    t = datetime(2025, 3, 1, 10, 0, tzinfo=UTC)
    return PaperPosition(
        position_id=sid,
        signal_id=sid,
        instrument="BTCUSDT",
        direction=Direction.LONG,
        opened_at=t,
        information_cutoff=t,
        entry=100.0,
        initial_sl=98.0,
        tp1=104.0,
        tp2=108.0,
        tp3_ref="swing",
        state=PositionState.PARTIAL,
        effective_sl=100.0,
        open_fraction=0.5,
        realized_r=0.7,
        legs=(PositionLeg(fraction=0.5, price=104.0, r_multiple=1.0, reason=ExitReason.TP1, at=t),),
        bars_pending=0,
        bars_held=12,
        mfe_r=1.4,
        mae_r=-0.3,
        last_price=105.0,
        tp1_done=True,
        tp2_done=False,
        tp3_done=False,
        sl_at_be=True,
        entry_ts=t,
    )


def test_paper_position_roundtrip() -> None:
    orig = _pos()
    d = paper_position_to_dict(orig)
    back = paper_position_from_dict(d)
    assert back is not None
    assert back == orig  # frozen dataclass ⇒ Wertgleichheit


def test_paper_position_from_broken_dict_is_none() -> None:
    assert paper_position_from_dict({"position_id": "x"}) is None
