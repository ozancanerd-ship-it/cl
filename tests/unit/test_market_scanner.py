"""scanner.opportunity + scanner.market_scanner — Opportunity Score + Top-Ranking (Masterplan §5/§6)."""

from __future__ import annotations

from datetime import UTC, datetime

from trading_agent.core.enums import Direction, RegimeDirectional, RegimeVolatility, Timeframe
from trading_agent.runtime.bus import EventBus
from trading_agent.runtime.events import DecisionMade, RankingUpdated
from trading_agent.scanner.market_scanner import MarketScanner, ScannerConfig
from trading_agent.scanner.opportunity import score_opportunity

_CUT = datetime(2026, 8, 30, 12, 0, tzinfo=UTC)


# --- leichte Fakes, die die vom Score gelesene Attribut-Struktur nachbilden ---
class _E:  # generischer Attributträger
    def __init__(self, **kw: object) -> None:
        self.__dict__.update(kw)


def _regime(directional: RegimeDirectional, vol: RegimeVolatility, dscore: float = 0.8) -> _E:
    return _E(directional=directional, directional_score=dscore, volatility=vol)


def _group(name: str, net: float) -> _E:
    return _E(group=_E(name=name), scored=True, net=net)


def _result(
    *,
    instrument: str,
    setup_state: str,
    htf_dir: RegimeDirectional,
    h4_vol: RegimeVolatility,
    gate_ok: bool,
    disagreement: float = 0.0,
    strat_score: float | None = None,
    tier: str | None = None,
    direction: Direction | None = None,
    rr: float | None = None,
    conf_groups: list[_E] | None = None,
    data_conf: float = 0.9,
) -> _E:
    d = _E(
        instrument=instrument,
        information_cutoff=_CUT,
        setup_state=_E(value=setup_state),
        direction=direction,
        tier=_E(value=tier) if tier else None,
        rr_to_tp2=rr,
        blended_rr=rr,
    )
    per_tf = {
        Timeframe.D1: _E(regime=_regime(htf_dir, RegimeVolatility.NORMAL)),
        Timeframe.H4: _E(regime=_regime(htf_dir, h4_vol)),
    }
    mtf = _E(
        instrument=instrument,
        information_cutoff=_CUT,
        per_tf=per_tf,
        htf_directional=htf_dir,
        htf_regime_gate=_E(ok=gate_ok, disagreement=disagreement),
        data_confidence=data_conf,
        market_context=_E(derivatives=None, spread=None),
    )
    conf = _E(groups=tuple(conf_groups or [])) if conf_groups is not None else None
    return _E(
        decision=d,
        mtf=mtf,
        confluence=conf,
        confidence=_E(data=data_conf),
        score=_E(final_score=strat_score, tier=_E(value=tier) if tier else None),
        scan=None,
    )


# --------------------------------------------------------------------------- opportunity score


def test_scanning_asset_still_scores_from_context() -> None:
    r = _result(
        instrument="XAUUSDT",
        setup_state="scanning",
        htf_dir=RegimeDirectional.TREND_UP,
        h4_vol=RegimeVolatility.NORMAL,
        gate_ok=True,
    )
    opp = score_opportunity(r, asset_class="gold")
    assert 0.0 < opp.score < 60.0  # Kontext da, aber kein Setup → moderat
    assert opp.setup_state == "scanning" and opp.setup_readiness < 0.1
    assert opp.strategy_score is None
    assert "news" in opp.unavailable and "correlation" in opp.unavailable
    assert not opp.is_actionable


def test_armed_a_plus_scores_high() -> None:
    r = _result(
        instrument="XAUUSDT",
        setup_state="armed",
        htf_dir=RegimeDirectional.TREND_UP,
        h4_vol=RegimeVolatility.NORMAL,
        gate_ok=True,
        strat_score=92.0,
        tier="A+",
        direction=Direction.LONG,
        rr=4.0,
        conf_groups=[
            _group("LIQUIDITY_EVENT", 0.9),
            _group("MOMENTUM_STRUCTURE", 0.85),
            _group("LOCATION", 0.8),
        ],
    )
    opp = score_opportunity(r)
    assert opp.score >= 80.0
    assert opp.is_actionable and opp.tier == "A+"
    assert "armed long" in opp.headline.lower()


def test_unclear_regime_and_extreme_vol_drag_score_down() -> None:
    good = score_opportunity(
        _result(
            instrument="BTC",
            setup_state="bias_set",
            htf_dir=RegimeDirectional.TREND_UP,
            h4_vol=RegimeVolatility.NORMAL,
            gate_ok=True,
        )
    )
    bad = score_opportunity(
        _result(
            instrument="BTC",
            setup_state="bias_set",
            htf_dir=RegimeDirectional.UNCLEAR,
            h4_vol=RegimeVolatility.EXTREME,
            gate_ok=False,
            disagreement=0.8,
        )
    )
    assert bad.score < good.score


# --------------------------------------------------------------------------- scanner + ranking


async def test_scanner_ranks_across_assets_and_emits_on_change() -> None:
    bus = EventBus(raise_on_handler_error=False)
    scanner = MarketScanner(ScannerConfig(asset_class={"XAUUSDT": "gold", "BTCUSDT": "crypto"}))
    top = scanner.attach(bus)

    ranking_events: list[RankingUpdated] = []
    bus.subscribe(RankingUpdated, lambda e: ranking_events.append(e))

    async def decision(instrument: str, **kw: object) -> None:
        await bus.publish(
            DecisionMade(
                ts=_CUT,
                instrument=instrument,
                decision_type="no_trade",
                setup_state=str(kw.get("setup_state", "scanning")),
                result=_result(instrument=instrument, **kw),  # type: ignore[arg-type]
            )
        )

    await decision(
        "BTCUSDT",
        setup_state="scanning",
        htf_dir=RegimeDirectional.RANGE,
        h4_vol=RegimeVolatility.NORMAL,
        gate_ok=True,
    )
    await decision(
        "XAUUSDT",
        setup_state="armed",
        htf_dir=RegimeDirectional.TREND_UP,
        h4_vol=RegimeVolatility.NORMAL,
        gate_ok=True,
        strat_score=88.0,
        tier="A",
        direction=Direction.LONG,
        rr=4.0,
        conf_groups=[_group("LIQUIDITY_EVENT", 0.9), _group("MOMENTUM_STRUCTURE", 0.8)],
    )

    ranked = top.top(5)
    assert ranked[0].instrument == "XAUUSDT" and ranked[0].rank == 1
    assert ranked[0].asset_class == "gold"
    assert top.rank_of("BTCUSDT") == 2
    assert any(e.top_instrument == "XAUUSDT" for e in ranking_events)

    ex = top.explain("XAUUSDT")
    assert ex["rank"] == 1 and ex["tier"] == "A"
    assert ex["top_factors"] and ex["vs_runner_up"]["instrument"] == "BTCUSDT"
    assert "news" in ex["not_yet_evaluated"]


def test_stale_scores_drop_out_of_ranking() -> None:
    class _Clock:
        t = _CUT

        def now(self) -> datetime:
            return self.t

    clk = _Clock()
    scanner = MarketScanner(ScannerConfig(stale_after_s=60.0), clock=clk)
    scanner.feed(
        "BTCUSDT",
        _result(
            instrument="BTCUSDT",
            setup_state="scanning",
            htf_dir=RegimeDirectional.TREND_UP,
            h4_vol=RegimeVolatility.NORMAL,
            gate_ok=True,
        ),
    )
    top = TopOpportunities_for(scanner)
    assert len(top.ranking()) == 1
    from datetime import timedelta

    clk.t = _CUT + timedelta(seconds=120)
    assert len(top.ranking()) == 0


def TopOpportunities_for(scanner: MarketScanner):  # kleine Helfer-Fabrik
    from trading_agent.scanner.market_scanner import TopOpportunities

    return TopOpportunities(scanner)
