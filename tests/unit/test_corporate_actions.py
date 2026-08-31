"""Tests: ``refdata.corporate_actions`` — PIT-Backadjustment, Symbol-Ketten, Delisting, kein Fake."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from trading_agent.core.enums import CorporateActionType, Timeframe
from trading_agent.core.models import OHLCV
from trading_agent.refdata.corporate_actions import (
    CorporateActionBook,
    adjust_ohlcv,
    resolve_symbol_at,
)
from trading_agent.refdata.models import CorporateAction

D1 = Timeframe.D1


def _bar(day: datetime, price: float, vol: float = 1000.0) -> OHLCV:
    return OHLCV(
        instrument="AAPL",
        timeframe=D1,
        open_time=day,
        close_time=day + timedelta(days=1),
        open=price,
        high=price * 1.01,
        low=price * 0.99,
        close=price,
        volume=vol,
    )


def _series(start: datetime, prices: list[float]) -> list[OHLCV]:
    return [_bar(start + timedelta(days=i), p) for i, p in enumerate(prices)]


def test_split_backadjustment_pit() -> None:
    start = datetime(2024, 1, 1, tzinfo=UTC)
    bars = _series(start, [100.0, 100.0, 25.0, 25.0])  # 4:1 Split am Tag 2
    split = CorporateAction(
        symbol="AAPL",
        action_type=CorporateActionType.SPLIT,
        ex_date=start + timedelta(days=2),
        available_time=start + timedelta(days=1),
        ratio=4.0,
    )
    # as_of nach Bekanntgabe → Vor-Split-Bars durch 4 geteilt, Volumen ×4
    res = adjust_ohlcv(bars, [split], as_of=start + timedelta(days=3))
    closes = [round(b.close, 4) for b in res.bars]
    assert closes == [25.0, 25.0, 25.0, 25.0]
    assert res.bars[0].volume == 4000.0
    assert res.provenance == "split_adjusted"

    # as_of VOR Bekanntgabe → keine Anpassung (PIT)
    raw = adjust_ohlcv(bars, [split], as_of=start)
    assert [b.close for b in raw.bars] == [100.0, 100.0, 25.0, 25.0]
    assert raw.provenance == "raw"


def test_dividend_total_return_only_when_requested() -> None:
    start = datetime(2024, 1, 1, tzinfo=UTC)
    bars = _series(start, [100.0, 100.0, 98.0, 98.0])
    div = CorporateAction(
        symbol="AAPL",
        action_type=CorporateActionType.DIVIDEND,
        ex_date=start + timedelta(days=2),
        available_time=start - timedelta(days=5),
        cash_amount=2.0,
    )
    plain = adjust_ohlcv(bars, [div], as_of=start + timedelta(days=3))
    assert [b.close for b in plain.bars] == [100.0, 100.0, 98.0, 98.0]  # unverändert
    tr = adjust_ohlcv(bars, [div], as_of=start + timedelta(days=3), adjust_dividends=True)
    assert tr.provenance == "total_return"
    assert tr.bars[0].close < 100.0 and tr.bars[2].close == 98.0  # nur Bars vor ex_date skaliert


def test_multiple_actions_compound() -> None:
    start = datetime(2024, 1, 1, tzinfo=UTC)
    bars = _series(start, [200.0, 100.0, 50.0])  # 2:1 an Tag 1, 2:1 an Tag 2
    a1 = CorporateAction(
        symbol="AAPL",
        action_type=CorporateActionType.SPLIT,
        ex_date=start + timedelta(days=1),
        available_time=start,
        ratio=2.0,
    )
    a2 = CorporateAction(
        symbol="AAPL",
        action_type=CorporateActionType.SPLIT,
        ex_date=start + timedelta(days=2),
        available_time=start,
        ratio=2.0,
    )
    res = adjust_ohlcv(bars, [a1, a2], as_of=start + timedelta(days=3))
    assert [round(b.close, 4) for b in res.bars] == [50.0, 50.0, 50.0]
    assert len(res.applied) == 2


def test_resolve_symbol_change_chain() -> None:
    ch = CorporateAction(
        symbol="FB",
        action_type=CorporateActionType.SYMBOL_CHANGE,
        ex_date=datetime(2022, 6, 9, tzinfo=UTC),
        available_time=datetime(2022, 6, 1, tzinfo=UTC),
        new_symbol="META",
    )
    assert resolve_symbol_at("FB", [ch], datetime(2023, 1, 1, tzinfo=UTC)) == "META"
    assert resolve_symbol_at("FB", [ch], datetime(2022, 1, 1, tzinfo=UTC)) == "FB"  # vor ex_date


def test_book_queries() -> None:
    start = datetime(2024, 1, 1, tzinfo=UTC)
    split = CorporateAction(
        symbol="aapl",
        action_type=CorporateActionType.SPLIT,
        ex_date=start,
        available_time=start,
        ratio=2.0,
    )
    delist = CorporateAction(
        symbol="XYZ",
        action_type=CorporateActionType.DELISTING,
        ex_date=start + timedelta(days=10),
        available_time=start + timedelta(days=5),
    )
    book = CorporateActionBook([split, delist])
    assert book.symbols() == ("AAPL", "XYZ")
    assert not book.is_delisted("XYZ", start)  # vor ex_date
    assert book.is_delisted("XYZ", start + timedelta(days=20))
    bars = _series(start - timedelta(days=2), [10.0, 10.0, 5.0, 5.0])
    res = book.adjust("AAPL", bars, as_of=start + timedelta(days=1))
    assert res.provenance == "split_adjusted"


def test_deterministic_and_empty() -> None:
    start = datetime(2024, 1, 1, tzinfo=UTC)
    bars = _series(start, [10.0, 10.0])
    assert adjust_ohlcv(bars, [], as_of=start).provenance == "raw"
    assert adjust_ohlcv([], [], as_of=start).bars == ()
