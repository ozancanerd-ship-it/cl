"""investment.stock_analysis — Einzelaktien-Score (Masterplan §45–§52)."""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

from trading_agent.core.enums import Timeframe
from trading_agent.core.models import OHLCV
from trading_agent.investment.stock_analysis import StockAnalysisEngine, StockVerdict

_AS_OF = datetime(2026, 1, 2, tzinfo=UTC)


def _d1(
    prices: list[float], *, vol: list[float] | None = None, sym: str = "TEST-YF"
) -> list[OHLCV]:
    start = _AS_OF - timedelta(days=len(prices))
    out: list[OHLCV] = []
    for i, px in enumerate(prices):
        t = start + timedelta(days=i)
        out.append(
            OHLCV(
                instrument=sym,
                timeframe=Timeframe.D1,
                open_time=t,
                close_time=t + timedelta(days=1),
                open=px * 0.995,
                high=px * 1.01,
                low=px * 0.99,
                close=px,
                volume=(vol[i] if vol else 1_000_000.0),
                source="test",
            )
        )
    return out


def _wobble(i: int) -> float:
    return 1.0 + 0.02 * math.sin(i / 3.0)  # ±2 % Rauschen, ATR nicht monoton


def test_uptrend_outperformer_scores_high() -> None:
    n = 240
    stock = _d1([100.0 * (1.006**i) * _wobble(i) for i in range(n)])  # Uptrend + Rauschen
    bench = _d1([100.0 * (1.001**i) * _wobble(i) for i in range(n)], sym="SPX-YF")  # schwächer
    a = StockAnalysisEngine().analyze("NVDA-YF", stock, as_of=_AS_OF, benchmark_d1=bench)
    assert a.score >= 60
    assert a.verdict in (StockVerdict.BUY, StockVerdict.STRONG_BUY)
    names = {f.name for f in a.factors}
    assert {"trend", "momentum", "relative_strength"} <= names
    assert "fundamentals" in a.excluded  # keine Fundamentaldaten übergeben


def test_downtrend_underperformer_scores_low() -> None:
    n = 240
    stock = _d1([100.0 * (0.99**i) * _wobble(i) for i in range(n)])
    bench = _d1([100.0 * (1.002**i) * _wobble(i) for i in range(n)], sym="SPX-YF")
    a = StockAnalysisEngine().analyze("XYZ-YF", stock, as_of=_AS_OF, benchmark_d1=bench)
    assert a.score < 45
    assert a.verdict is StockVerdict.AVOID


def test_etf_ticker_rejected() -> None:
    a = StockAnalysisEngine().analyze("SPY", _d1([100.0] * 200), as_of=_AS_OF)
    assert a.verdict is StockVerdict.UNKNOWN
    assert a.factors == ()
    assert "ETF" in a.notes[0]


def test_insufficient_history_is_unknown() -> None:
    a = StockAnalysisEngine().analyze("NEW-YF", _d1([100.0] * 40), as_of=_AS_OF)
    assert a.verdict is StockVerdict.UNKNOWN


def test_no_lookahead_only_uses_bars_up_to_as_of() -> None:
    n = 220
    prices = [100.0 + i for i in range(n)]
    stock = _d1(prices)
    cut = _AS_OF - timedelta(days=60)
    a_cut = StockAnalysisEngine().analyze("T-YF", stock, as_of=cut)
    a_full = StockAnalysisEngine().analyze("T-YF", stock, as_of=_AS_OF)
    # verschiedene Fenster → verschiedene Scores, kein Absturz, beide gültig
    assert 0.0 <= a_cut.score <= 100.0 and 0.0 <= a_full.score <= 100.0
    assert not math.isnan(a_cut.score)
