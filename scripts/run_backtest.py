#!/usr/bin/env python
"""Deterministischer End-to-End Strategy-Backtest über die **echte** ``strategy.evaluate``-Pipeline.

    python scripts/run_backtest.py --repo data/repository_real \
        --symbols BTCUSDT ETHUSDT --start 2025-01-01 --end 2025-07-01

MarketDataRepository → ReplayClock → MarketContextAssembler → PaperLiveRunner → strategy.evaluate()
→ Dynamic Signal → Paper Position → Trade Ledger → StrategyBacktestReport.

Keine Parameteroptimierung. Alle PROPOSED DEFAULTS bleiben unverändert. Kein Broker,
keine Echtgeld-Orders. ``--json`` gibt den vollständigen Report maschinenlesbar aus.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from datetime import UTC, datetime

from trading_agent.core.enums import AssetClass, Timeframe
from trading_agent.core.time import parse_timestamp
from trading_agent.data.repository import MarketDataRepository
from trading_agent.engine.backtest import Backtest, BacktestConfig
from trading_agent.research.metrics import compute_metrics
from trading_agent.research.robustness import monte_carlo
from trading_agent.research.validation import (
    chronological_split,
    fraction_positive_windows,
    symbol_stability,
    time_stability,
    walk_forward_folds,
)
from trading_agent.strategy.cost_profiles import estimate_profile
from trading_agent.strategy.costs import CostConfig
from trading_agent.strategy.engine import EngineParams
from trading_agent.strategy.evaluate import EvaluateParams
from trading_agent.utils.logging import configure_logging


def _seg_row(s: object) -> dict:
    return {
        "label": s.label,  # type: ignore[attr-defined]
        "n": s.n,  # type: ignore[attr-defined]
        "win_rate": round(s.win_rate, 4),  # type: ignore[attr-defined]
        "expectancy_r": round(s.expectancy_r, 4),  # type: ignore[attr-defined]
        "median_r": round(s.median_r, 4),  # type: ignore[attr-defined]
        "total_r": round(s.total_r, 4),  # type: ignore[attr-defined]
        "profit_factor": round(s.profit_factor, 3) if s.profit_factor != float("inf") else "inf",  # type: ignore[attr-defined]
    }


def _validation_block(trades: list, cfg: BacktestConfig) -> dict:
    """OOS-Split · Walk-Forward · Monte-Carlo · Zeit-/Symbol-Stabilität — nur wenn Trades da."""
    if not trades:
        return {"note": "keine Trades — Validierung nicht möglich (Bottleneck ist upstream)"}
    split = chronological_split(trades, train=0.5, validation=0.25)
    folds = walk_forward_folds(cfg.start, cfg.end, train_days=180, test_days=60, step_days=60)
    wf = []
    for f in folds:
        tr = f.test_trades(trades)
        if tr:
            mm = compute_metrics(tr)
            wf.append(
                {
                    "fold": f.index,
                    "test_window": [f.test_start.date().isoformat(), f.test_end.date().isoformat()],
                    "n": mm.n_trades,
                    "expectancy_r": round(mm.expectancy_r, 4),
                    "profit_factor": round(mm.profit_factor, 3)
                    if mm.profit_factor != float("inf")
                    else "inf",
                    "total_r": round(mm.total_r, 4),
                }
            )
    mc = monte_carlo(trades, runs=2000)
    ts = time_stability(trades, window_days=90, step_days=30)
    ss = symbol_stability(trades)
    return {
        "chronological_split": {
            "train": len(split.train),
            "validation": len(split.validation),
            "test": len(split.test),
        },
        "walk_forward": wf,
        "monte_carlo": {
            "runs": mc.runs,
            "final_equity_r_p05": round(mc.final_equity_r_p05, 3),
            "final_equity_r_p50": round(mc.final_equity_r_p50, 3),
            "final_equity_r_p95": round(mc.final_equity_r_p95, 3),
            "max_dd_r_p95": round(mc.max_dd_r_p95, 3),
            "bootstrap_fraction_positive": round(mc.bootstrap_fraction_positive, 4),
            "ruin_probability": round(mc.ruin_probability, 4),
        },
        "time_stability": {
            "windows": len(ts),
            "fraction_positive": round(fraction_positive_windows(ts), 4),
        },
        "symbol_stability": {
            "per_symbol_total_r": ss.per_symbol_total_r,
            "fraction_positive": ss.fraction_positive,
            "total_r_without_best_symbol": ss.total_r_without_best,
        },
    }


def _bucket_row(b: object) -> dict:
    return {
        "band": b.label,  # type: ignore[attr-defined]
        "n": b.n,  # type: ignore[attr-defined]
        "avg_realized_r": round(b.avg_realized_r, 4),  # type: ignore[attr-defined]
        "win_rate": round(b.win_rate, 4),  # type: ignore[attr-defined]
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", default="data/repository_real")
    ap.add_argument("--symbols", nargs="+", default=["BTCUSDT", "ETHUSDT"])
    ap.add_argument(
        "--asset-class",
        default="crypto",
        choices=["crypto", "altcoin", "gold", "forex", "equity", "etf"],
        help="steuert 24/7-Gate, News-Relevanz, Session-Kalender",
    )
    ap.add_argument("--start", default="2025-01-01")
    ap.add_argument("--end", default="2025-07-01")
    ap.add_argument("--warmup-bars", type=int, default=300)
    ap.add_argument("--risk-pct", type=float, default=1.0)
    ap.add_argument(
        "--news-gate",
        choices=["on", "off"],
        default="on",
        help="'off' = News-Fail-safe deaktiviert (Research-Modus, NICHT live-repräsentativ; "
        "erfindet KEINE News, protokolliert News als 'not_checked')",
    )
    ap.add_argument(
        "--cost-profile",
        choices=["zero", "estimate"],
        default="zero",
        help="'zero' = BRUTTO (Default). 'estimate' = konservatives Asset-Klassen-Schätzprofil "
        "(Fees/Spread/Slippage; Funding=0) aus strategy.cost_profiles — als ANNAHME markiert, "
        "keine gemessene Historie.",
    )
    ap.add_argument(
        "--require-native-higher",
        choices=["on", "off"],
        default="on",
        help="'on' (Default) verlangt tiefen nativen M15/H4/D1-Vorlauf vor --start (200 D1-Bars). "
        "'off' für junge Instrumente ohne so viel Historie: native höhere TFs werden genutzt "
        "wo vorhanden, der Rest PIT-sauber aus M5 abgeleitet (kein Fake, kein Look-ahead).",
    )
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    configure_logging("WARNING")
    repo = MarketDataRepository(args.repo)

    ep = EvaluateParams()
    if args.news_gate == "off":
        sys.stderr.write(
            "\n*** RESEARCH-MODUS: News-Gate AUS ***\n"
            "Der News-Fail-safe (V4 / NEWS_FEED_UNAVAILABLE) ist deaktiviert. Ergebnisse sind\n"
            "NICHT live-repräsentativ — live blockt der Gate ohne PIT-News-Feed jeden Entry.\n"
            "Es werden KEINE News-Daten erfunden; News wird als 'not_checked' protokolliert.\n\n"
        )
        ep = dataclasses.replace(
            ep,
            no_trade=dataclasses.replace(ep.no_trade, require_news_feed=False),
            veto=dataclasses.replace(ep.veto, require_news_feed=False),
        )

    cost = CostConfig()
    cost_provenance = "zero"
    if args.cost_profile == "estimate":
        prof = estimate_profile(AssetClass(args.asset_class))
        cost, cost_provenance = prof.config, prof.provenance
        sys.stderr.write(
            f"\n*** KOSTEN: {cost_provenance} ***\n{prof.note}\n"
            "brutto (gross_realized_r) und netto (realized_r) werden getrennt ausgewiesen.\n\n"
        )

    engine_params = EngineParams(evaluate=ep, cost=cost)

    cfg = BacktestConfig(
        instruments=tuple(args.symbols),
        start=parse_timestamp(args.start),
        end=parse_timestamp(args.end),
        base_timeframe=Timeframe.M5,
        asset_class=AssetClass(args.asset_class),
        warmup_bars=args.warmup_bars,
        min_days=180,
        risk_per_trade_pct=args.risk_pct,
        fixed_spread=None,  # keine erfundene Spanne — V4/Execution nutzt dann was da ist
        news_feed_available=False,  # kein PIT-News-Dataset
        read_native_higher=True,  # native M15/H4/D1 (beim Ingest aus M5 resampled, PIT)
        require_native_higher=args.require_native_higher == "on",
        engine_params=engine_params,
        dataset_version="binance-vision-spot-klines-v1",
    )
    res = Backtest(repo, ledger_path=f"{args.repo}/strategy_ledger.sqlite").run(cfg)

    m = res.metrics
    rep = res.strategy_report
    tel = res.telemetry

    gross_total = round(sum(t.gross_r for t in res.trades), 4)
    net_total = round(sum(t.realized_r for t in res.trades), 4)
    out = {
        "run_id": res.run_id,
        "manifest_hash": res.manifest.manifest_hash(),
        "output_hash": res.output_hash,
        "dataset_fingerprint": res.manifest.dataset_fingerprint,
        "code_sha": res.manifest.code_sha,
        "bars_processed": res.bars_processed,
        "dataset_ok": res.dataset_report.ok,
        "asset_class": args.asset_class,
        "news_gate": args.news_gate,
        "cost": {
            "profile": args.cost_profile,
            "provenance": cost_provenance,
            "gross_total_r": gross_total,
            "net_total_r": net_total,
            "cost_drag_r": round(gross_total - net_total, 4),
            "note": "profile=zero ⇒ brutto == netto. estimate ⇒ konservative ANNAHME, nicht gemessen.",
        },
        "base_metrics": {
            "n_trades": m.n_trades,
            "win_rate": round(m.win_rate, 4),
            "win_rate_excl_scratch": round(m.win_rate_excl_scratch, 4),
            "profit_factor": round(m.profit_factor, 3)
            if m.profit_factor != float("inf")
            else "inf",
            "expectancy_r": round(m.expectancy_r, 4),
            "avg_r": round(m.avg_r, 4),
            "median_r": round(m.median_r, 4),
            "stdev_r": round(m.stdev_r, 4),
            "max_drawdown_r": round(m.max_drawdown_r, 4),
            "sharpe_r": m.sharpe_r,
            "sortino_r": m.sortino_r,
            "calmar_r": m.calmar_r,
            "longest_loss_streak": m.longest_loss_streak,
            "avg_mfe_r": round(m.avg_mfe_r, 4),
            "avg_mae_r": round(m.avg_mae_r, 4),
            "total_r": round(m.total_r, 4),
        },
        "exit_structure": {
            "tp1_hit_rate": round(rep.tp1_hit_rate, 4),
            "tp2_hit_rate": round(rep.tp2_hit_rate, 4),
            "tp3_hit_rate": round(rep.tp3_hit_rate, 4),
            "stop_rate": round(rep.stop_rate, 4),
            "breakeven_rate": round(rep.breakeven_rate, 4),
            "trail_rate": round(rep.trail_rate, 4),
            "invalidated_exit_rate": round(rep.invalidated_exit_rate, 4),
            "expiry_rate": round(rep.expiry_rate, 4),
            "avg_hold_bars": round(rep.avg_hold_bars, 2),
            "exit_efficiency": round(rep.exit_efficiency, 4),
            "avg_give_back_r": round(rep.avg_give_back_r, 4),
        },
        "segments": {
            "by_direction": [_seg_row(s) for s in rep.by_direction],
            "by_instrument": [_seg_row(s) for s in rep.by_instrument],
            "by_score_tier": [_seg_row(s) for s in rep.by_score_tier],
            "by_confidence_tier": [_seg_row(s) for s in rep.by_confidence_tier],
            "by_exit_reason": [_seg_row(s) for s in rep.by_exit_reason],
        },
        "signal_analysis": {
            "score_vs_outcome": [_bucket_row(b) for b in rep.score_vs_outcome],
            "confidence_vs_outcome": [_bucket_row(b) for b in rep.confidence_vs_outcome],
            "confluence_vs_outcome": [_bucket_row(b) for b in rep.confluence_vs_outcome],
            "setup_state_vs_outcome": [_seg_row(s) for s in rep.setup_state_vs_outcome],
            "score_outcome_correlation": (
                round(rep.score_outcome_correlation, 4)
                if rep.score_outcome_correlation is not None
                else None
            ),
            "confidence_outcome_correlation": (
                round(rep.confidence_outcome_correlation, 4)
                if rep.confidence_outcome_correlation is not None
                else None
            ),
        },
        "telemetry": {
            "decisions": dict(tel.decisions),
            "no_trade_reasons_top": dict(tel.no_trade_reasons.most_common(12)),
            "veto_frequency": dict(tel.veto_frequency),
            "signals_created": tel.signals_created,
            "signals_invalidated": tel.signals_invalidated,
            "signals_expired": tel.signals_expired,
            "exit_required_events": tel.exit_required_events,
            "alerts_raised": tel.alerts_raised,
        },
        "validation": _validation_block(res.trades, cfg),
    }
    print(json.dumps(out, indent=2, default=str))
    print(f"\n# generated {datetime.now(UTC).isoformat()}  ·  run_id={res.run_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
