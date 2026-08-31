"""portfolio_intel — Hub, Correlation, PositionIntelligence, Exit, ReEntry, Health, Rotation (Masterplan §33–§43)."""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

from trading_agent.core.enums import AssetClass, DecisionType, Direction
from trading_agent.portfolio_intel import (
    AccountPortfolio,
    CorrelationEngine,
    ExitIntelligence,
    Holding,
    PortfolioHealth,
    PortfolioHub,
    PositionIntelligence,
    PositionVerdict,
    ReEntryEngine,
    RotationEngine,
)
from trading_agent.portfolio_intel.exit_intel import ExitKind
from trading_agent.portfolio_intel.health import PortfolioRanking

_NOW = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)


class _E:
    def __init__(self, **kw: object) -> None:
        self.__dict__.update(kw)


def _holding(
    inst: str, ac: AssetClass, acc: str, qty: float, entry: float, mark: float, **kw: object
) -> Holding:
    return Holding(
        instrument=inst,
        asset_class=ac,
        account=acc,
        direction=kw.get("direction", Direction.LONG),  # type: ignore[arg-type]
        quantity=qty,
        avg_entry_price=entry,
        mark_price=mark,
        stop_ref=kw.get("stop_ref"),  # type: ignore[arg-type]
        opened_at=kw.get("opened_at"),  # type: ignore[arg-type]
    )


# --------------------------------------------------------------------------- Hub


def test_hub_consolidates_across_accounts_and_asset_classes() -> None:
    a = AccountPortfolio(
        account="binance",
        as_of=_NOW,
        cash=1000.0,
        holdings=(
            _holding("BTCUSDT", AssetClass.CRYPTO, "binance", 0.01, 50_000, 60_000),
            _holding("XAUUSDT", AssetClass.GOLD, "binance", 1.0, 4000, 4200),
        ),
    )
    b = AccountPortfolio(
        account="kraken",
        as_of=_NOW,
        cash=500.0,
        holdings=(_holding("BTCUSDT", AssetClass.CRYPTO, "kraken", 0.01, 55_000, 60_000),),
    )
    cp = PortfolioHub().consolidate([a, b], as_of=_NOW)

    btc = cp.holding("BTCUSDT")
    assert btc is not None and btc.quantity == 0.02
    assert btc.avg_entry_price == 52_500.0  # (500+550)/0.02
    assert btc.account == "binance+kraken"
    assert cp.equity == a.equity + b.equity
    alloc = cp.allocation()
    assert alloc[AssetClass.CRYPTO] > 0 and alloc[AssetClass.GOLD] > 0
    assert math.isclose(sum(alloc.values()) + cp.cash_pct, 1.0, rel_tol=1e-6)


def test_hub_keeps_hedged_positions_separate() -> None:
    a = AccountPortfolio(
        account="a",
        as_of=_NOW,
        cash=0.0,
        holdings=(_holding("ETHUSDT", AssetClass.CRYPTO, "a", 1.0, 3000, 3100),),
    )
    b = AccountPortfolio(
        account="b",
        as_of=_NOW,
        cash=0.0,
        holdings=(
            _holding("ETHUSDT", AssetClass.CRYPTO, "b", 1.0, 3000, 3100, direction=Direction.SHORT),
        ),
    )
    cp = PortfolioHub().consolidate([a, b], as_of=_NOW)
    assert len(cp.net_holdings) == 2
    assert {h.direction for h in cp.net_holdings} == {Direction.LONG, Direction.SHORT}


# --------------------------------------------------------------------------- Correlation


def test_correlation_engine_detects_lockstep_and_independent() -> None:
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    lock_a, lock_b, indep = [], [], []
    pa = pb = pc = 100.0
    for i in range(60):
        step = math.sin(i / 3.0) * 0.5
        pa *= 1 + step / 100
        pb *= 1 + step / 100  # identische Schritte → ρ≈1
        pc *= 1 + math.cos(i / 2.0) * 0.4 / 100  # andere Frequenz
        ts = t0 + timedelta(hours=i)
        lock_a.append((ts, pa))
        lock_b.append((ts, pb))
        indep.append((ts, pc))

    m = CorrelationEngine(window=60, min_overlap=10).compute(
        {"AAA": lock_a, "BBB": lock_b, "CCC": indep}, as_of=_NOW
    )
    assert m.correlation("AAA", "BBB") > 0.98
    assert abs(m.correlation("AAA", "CCC")) < 0.9
    clusters = m.clusters(threshold=0.9)
    assert any({"AAA", "BBB"} <= c for c in clusters)


def test_correlation_insufficient_overlap_is_zero_not_fake() -> None:
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    short = [(t0 + timedelta(hours=i), 100.0 + i) for i in range(5)]
    m = CorrelationEngine(window=60, min_overlap=20).compute({"X": short, "Y": short})
    assert m.correlation("X", "Y") == 0.0
    assert m.samples("X", "Y") < 20


# --------------------------------------------------------------------------- PositionIntelligence


def _eval(
    htf: str,
    *,
    decision: DecisionType = DecisionType.NO_TRADE,
    d_dir: Direction | None = None,
    struct_net: float = 0.6,
) -> _E:
    return _E(
        decision=_E(decision=decision, direction=d_dir, setup_state=_E(value="scanning")),
        mtf=_E(htf_directional=_E(value=htf)),
        confluence=_E(
            groups=(_E(group=_E(name="MOMENTUM_STRUCTURE"), scored=True, net=struct_net),)
        ),
        contradictions=_E(records=()),
    )


def test_position_rating_strong_hold_for_winner_in_trend() -> None:
    h = _holding("BTCUSDT", AssetClass.CRYPTO, "binance", 0.02, 50_000, 56_000, stop_ref=48_000)
    r = PositionIntelligence().rate(h, evaluation=_eval("trend_up"), portfolio_weight=0.10)
    assert r.verdict in (PositionVerdict.STRONG_HOLD, PositionVerdict.HOLD)
    assert r.unrealized_r is not None and r.unrealized_r > 1.0
    assert any("HTF-Trend trägt" in x for x in r.reasons)


def test_position_rating_exit_on_opposing_signal() -> None:
    h = _holding("BTCUSDT", AssetClass.CRYPTO, "binance", 0.02, 50_000, 51_000, stop_ref=48_000)
    r = PositionIntelligence().rate(
        h, evaluation=_eval("trend_down", decision=DecisionType.SELL, d_dir=Direction.SHORT)
    )
    assert r.verdict is PositionVerdict.EXIT
    assert r.hard_override is not None


def test_position_rating_exit_when_price_through_stop() -> None:
    h = _holding("ETHUSDT", AssetClass.CRYPTO, "kraken", 1.0, 3000, 2790, stop_ref=2800)
    r = PositionIntelligence().rate(h, evaluation=_eval("range"))
    assert r.verdict is PositionVerdict.EXIT
    assert "Invalidierung" in (r.hard_override or "")


def test_concentration_drags_rating() -> None:
    h = _holding("BTCUSDT", AssetClass.CRYPTO, "binance", 1.0, 50_000, 52_000, stop_ref=48_000)
    lo = PositionIntelligence().rate(h, evaluation=_eval("trend_up"), portfolio_weight=0.10)
    hi = PositionIntelligence().rate(h, evaluation=_eval("trend_up"), portfolio_weight=0.45)
    assert hi.score < lo.score
    assert any("Klumpenrisiko" in x for x in hi.reasons)


# --------------------------------------------------------------------------- Exit


def test_exit_plan_full_on_exit_verdict() -> None:
    h = _holding("ETHUSDT", AssetClass.CRYPTO, "kraken", 1.0, 3000, 2790, stop_ref=2800)
    r = PositionIntelligence().rate(h, evaluation=_eval("range"))
    plan = ExitIntelligence().plan(h, r)
    assert plan.kind is ExitKind.FULL and plan.size_fraction == 1.0


def test_exit_plan_trails_stop_for_runner() -> None:
    h = _holding("BTCUSDT", AssetClass.CRYPTO, "binance", 0.02, 50_000, 58_000, stop_ref=48_000)
    r = PositionIntelligence().rate(h, evaluation=_eval("trend_up"), portfolio_weight=0.08)
    plan = ExitIntelligence().plan(h, r)
    assert plan.kind in (ExitKind.TRAIL_STOP, ExitKind.NONE)
    if plan.kind is ExitKind.TRAIL_STOP:
        assert plan.suggested_stop is not None and plan.suggested_stop > h.avg_entry_price


# --------------------------------------------------------------------------- ReEntry


def test_reentry_watch_after_trailing_exit_then_ready_on_fresh_signal() -> None:
    eng = ReEntryEngine()
    w = eng.register_exit(
        instrument="BTCUSDT",
        direction=Direction.LONG,
        exited_at=_NOW,
        exit_price=55_000,
        exit_reason="trail_stop hit in profit",
        level_to_reclaim=56_000,
    )
    assert w is not None and eng.watches

    not_ready = eng.assess("BTCUSDT", evaluation=_eval("range"), price=54_000)
    assert not_ready is not None and not not_ready.ready

    ev = _E(
        decision=_E(
            decision=DecisionType.BUY, direction=Direction.LONG, setup_state=_E(value="armed")
        ),
        mtf=_E(htf_directional=_E(value="trend_up")),
        confluence=_E(groups=()),
        contradictions=_E(records=()),
    )
    ready = eng.assess("BTCUSDT", evaluation=ev, price=56_500)
    assert ready is not None and ready.ready
    assert ready.verdict is PositionVerdict.RE_ENTRY_WATCH


def test_reentry_not_registered_on_thesis_break() -> None:
    eng = ReEntryEngine()
    w = eng.register_exit(
        instrument="ETHUSDT",
        direction=Direction.LONG,
        exited_at=_NOW,
        exit_price=2800,
        exit_reason="invalidation: structure broke down",
    )
    assert w is None and not eng.watches


# --------------------------------------------------------------------------- Health + Ranking + Rotation


def _cp_two_holdings() -> object:
    acc = AccountPortfolio(
        account="binance",
        as_of=_NOW,
        cash=2000.0,
        holdings=(
            _holding(
                "BTCUSDT", AssetClass.CRYPTO, "binance", 0.05, 50_000, 55_000, stop_ref=48_000
            ),
            _holding("XAUUSDT", AssetClass.GOLD, "binance", 1.0, 4000, 3950, stop_ref=3900),
        ),
    )
    return PortfolioHub().consolidate([acc], as_of=_NOW)


def test_portfolio_health_grades_and_flags() -> None:
    cp = _cp_two_holdings()
    ratings = [
        PositionIntelligence().rate(
            h, evaluation=_eval("trend_up"), portfolio_weight=cp.weight_of(h.instrument)
        )  # type: ignore[attr-defined]
        for h in cp.net_holdings  # type: ignore[attr-defined]
    ]
    rep = PortfolioHealth().evaluate(cp, ratings=ratings)  # type: ignore[arg-type]
    assert 0.0 <= rep.score <= 100.0
    assert rep.grade in ("GREEN", "YELLOW", "RED")
    assert "position_quality" in rep.components
    assert any("Streuung" in f or "Position" in f for f in rep.flags)


def test_ranking_and_rotation_suggestion() -> None:
    cp = _cp_two_holdings()
    strong = PositionIntelligence().rate(
        cp.holding("BTCUSDT"),
        evaluation=_eval("trend_up"),
        portfolio_weight=0.2,  # type: ignore[arg-type]
    )
    weak = PositionIntelligence().rate(
        cp.holding("XAUUSDT"),  # type: ignore[arg-type]
        evaluation=_eval("trend_down", struct_net=-0.7),
        portfolio_weight=0.2,
    )
    ranked = PortfolioRanking.rank(cp, [strong, weak])  # type: ignore[arg-type]
    assert ranked[0].instrument == "BTCUSDT" and ranked[0].rank == 1
    assert PortfolioRanking.weakest([strong, weak]) is weak

    opp = _E(instrument="SOLUSDT", score=88.0, is_actionable=True)
    sugg = RotationEngine(min_edge=15.0).suggest([strong, weak], [opp])
    if weak.verdict in (PositionVerdict.WATCH, PositionVerdict.REDUCE, PositionVerdict.EXIT):
        assert sugg is not None
        assert sugg.sell_instrument == "XAUUSDT" and sugg.buy_instrument == "SOLUSDT"
        assert sugg.edge >= 15.0


def test_no_rotation_when_all_holdings_healthy() -> None:
    cp = _cp_two_holdings()
    ratings = [
        PositionIntelligence().rate(h, evaluation=_eval("trend_up"), portfolio_weight=0.1)  # type: ignore[arg-type]
        for h in cp.net_holdings  # type: ignore[attr-defined]
    ]
    ratings = [
        r for r in ratings if r.verdict in (PositionVerdict.STRONG_HOLD, PositionVerdict.HOLD)
    ]
    opp = _E(instrument="SOLUSDT", score=99.0, is_actionable=True)
    assert RotationEngine().suggest(ratings, [opp]) is None


# --------------------------------------------------------------------------- facade


def test_engine_end_to_end_report() -> None:
    from trading_agent.portfolio_intel import PortfolioIntelligenceEngine

    acc = AccountPortfolio(
        account="binance",
        as_of=_NOW,
        cash=3000.0,
        holdings=(
            _holding(
                "BTCUSDT", AssetClass.CRYPTO, "binance", 0.05, 50_000, 55_000, stop_ref=48_000
            ),
            _holding("XAUUSDT", AssetClass.GOLD, "binance", 1.0, 4000, 3960, stop_ref=3900),
        ),
    )
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    series = {
        "BTCUSDT": [(t0 + timedelta(hours=i), 50_000 + i * 30) for i in range(40)],
        "XAUUSDT": [(t0 + timedelta(hours=i), 4000 - i * 0.5) for i in range(40)],
    }
    eng = PortfolioIntelligenceEngine()
    rep = eng.assess(
        [acc],
        as_of=_NOW,
        evaluations={"BTCUSDT": _eval("trend_up"), "XAUUSDT": _eval("trend_down", struct_net=-0.5)},
        price_series=series,
        opportunities=[_E(instrument="SOLUSDT", score=90.0, is_actionable=True)],
    )
    assert len(rep.ratings) == 2 and len(rep.exit_plans) == 2
    assert rep.ranking[0].rank == 1
    assert rep.health.grade in ("GREEN", "YELLOW", "RED")
    assert rep.correlation is not None
    d = rep.as_dict()
    assert "health" in d and "ranking" in d and "allocation" in d
