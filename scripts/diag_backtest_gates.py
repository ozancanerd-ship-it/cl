#!/usr/bin/env python
"""Schnelldiagnose: wo im Pipeline-Trichter bleiben die Bars hängen?

Sampelt jeden N-ten M5-cutoff über das Backtest-Fenster, ruft die **echte** ``strategy.evaluate``
(News-Gate optional aus) und zählt: Decision-Typ, No-Trade-Grund, Regime-Zustand, wie weit die
Kette kommt (Setup gefunden? ARMED? Veto? Tier?).

Reine Analyse — verändert **keine** Parameter, schreibt nichts.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
from collections import Counter

from trading_agent.core.enums import Timeframe
from trading_agent.core.time import parse_timestamp
from trading_agent.data.repository import MarketDataRepository
from trading_agent.engine.replay import AssemblerConfig, MarketContextAssembler, ReplayClock
from trading_agent.strategy.evaluate import (
    EvaluateParams,
    _build_mtf,
    evaluate_from_mtf,
)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", default="data/repository_real")
    ap.add_argument("--symbols", nargs="+", default=["BTCUSDT", "ETHUSDT"])
    ap.add_argument("--start", default="2025-01-01")
    ap.add_argument("--end", default="2025-07-01")
    ap.add_argument("--every", type=int, default=48)  # jeden 48. M5 = alle 4 h
    ap.add_argument("--news-gate", choices=["on", "off"], default="off")
    args = ap.parse_args()

    ep = EvaluateParams()
    if args.news_gate == "off":
        ep = dataclasses.replace(
            ep,
            no_trade=dataclasses.replace(ep.no_trade, require_news_feed=False),
            veto=dataclasses.replace(ep.veto, require_news_feed=False),
        )

    repo = MarketDataRepository(args.repo)
    start, end = parse_timestamp(args.start), parse_timestamp(args.end)

    out: dict[str, dict] = {}
    for sym in args.symbols:
        asm = MarketContextAssembler(
            repo,
            AssemblerConfig(instrument=sym, warmup_bars=300, read_native_higher=True),
        )
        asm.bind(start, end)
        grid = repo.read_ohlcv(sym, Timeframe.M5, start, end, as_of=end)
        cutoffs = list(ReplayClock.from_bars(grid))[:: args.every]

        from trading_agent.analysis.regime import RegimeGateParams, regime_gate

        relaxed = RegimeGateParams(
            allow_unclear_htf=True, forbid_low_vol=False, forbid_compression=False
        )

        dec = Counter()
        nt = Counter()
        d1_dir = Counter()
        d1_vol = Counter()
        h4_dir = Counter()
        regime_ok = 0
        both_htf_directional = 0
        regime_ok_relaxed = 0
        setups_found = 0
        armed = 0
        tiers = Counter()
        for c in cutoffs:
            mc = asm.at(c)
            mtf = _build_mtf(mc, ep)
            if mtf.d1 is not None:
                d1_dir[mtf.d1.regime.directional.value] += 1
                d1_vol[mtf.d1.regime.volatility.value] += 1
            if mtf.h4 is not None:
                h4_dir[mtf.h4.regime.directional.value] += 1
            if mtf.d1 is not None and mtf.h4 is not None:
                from trading_agent.core.enums import RegimeDirectional

                if RegimeDirectional.UNCLEAR not in (
                    mtf.d1.regime.directional,
                    mtf.h4.regime.directional,
                ):
                    both_htf_directional += 1
                g = regime_gate(
                    mtf.d1.regime,
                    mtf.h4.regime,
                    mtf.m15.regime if mtf.m15 is not None else None,
                    params=relaxed,
                )
                if g.ok:
                    regime_ok_relaxed += 1
            if mtf.regime_ok:
                regime_ok += 1
            r = evaluate_from_mtf(mtf, spread=mc.spread, params=ep)
            dec[r.decision.decision.value] += 1
            for rc in r.decision.reason_codes:
                nt[rc.value] += 1
            if r.candidate is not None:
                setups_found += 1
                if r.candidate.is_armed:
                    armed += 1
            if r.score is not None:
                tiers[r.score.tier.value] += 1

        out[sym] = {
            "sampled": len(cutoffs),
            "decisions": dict(dec),
            "no_trade_reasons": dict(nt.most_common()),
            "regime_ok_count": regime_ok,
            "both_htf_directional": both_htf_directional,
            "regime_ok_relaxed_count": regime_ok_relaxed,
            "d1_directional": dict(d1_dir),
            "d1_volatility": dict(d1_vol),
            "h4_directional": dict(h4_dir),
            "setups_found": setups_found,
            "armed_candidates": armed,
            "score_tiers": dict(tiers),
        }

    print(json.dumps(out, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
