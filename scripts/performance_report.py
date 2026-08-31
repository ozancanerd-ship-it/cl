#!/usr/bin/env python
"""Performance-/Paper-Report — verdichtet die Shadow-/Paper-Trade-Journale (JSONL) zu
Kennzahlen für den Dashboard-Tab **Performance** (Masterplan §44/§62).

Liest ``signal``- + ``trade``-Zeilen (SignalJournal / xau_shadow), paart Signal → Fill →
Exit, rekonstruiert ``TradeRecord`` (MFE/MAE + Haltedauer aus H4, wo im Repo vorhanden),
und rechnet ``compute_metrics`` **gesamt** und **gruppiert** nach Asset / Setup / Richtung /
R:R-Bucket / Score-Bucket / Freigabe (live|shadow).

Schreibt ``data/repository_real/live/performance.json`` (+ Text). ``build_dashboard.py``
liest die JSON in ``paper_performance``.

    uv run python scripts/performance_report.py
    uv run python scripts/performance_report.py --journals 'data/repository_real/live/*.jsonl'
"""

from __future__ import annotations

import argparse
import glob
import json
import statistics
from datetime import UTC, datetime
from pathlib import Path

from trading_agent.core.enums import Side, Timeframe
from trading_agent.data.repository import MarketDataRepository
from trading_agent.journal.ledger import TradeRecord
from trading_agent.research.metrics import compute_metrics

_TERMINAL = {"SL", "TP2", "TP3", "BE_EXIT", "MAX_HOLD_EXIT", "EXIT", "TP1_ONLY_EXIT"}


def _load_trades(paths: list[str], repo: MarketDataRepository) -> list[TradeRecord]:
    out: list[TradeRecord] = []
    h4_cache: dict[str, list] = {}

    def h4_of(sym: str) -> list:
        if sym not in h4_cache:
            try:
                h4_cache[sym] = repo.read_ohlcv(
                    sym,
                    Timeframe.H4,
                    datetime(2000, 1, 1, tzinfo=UTC),
                    datetime(2100, 1, 1, tzinfo=UTC),
                )
            except Exception:
                h4_cache[sym] = []
        return h4_cache[sym]

    for path in paths:
        rows = [json.loads(x) for x in Path(path).read_text().splitlines() if x.strip()]
        pending: dict | None = None
        fill_ts: datetime | None = None
        realized = 0.0
        src = Path(path).stem
        for r in rows:
            kind = r.get("kind")
            if kind == "signal":
                pending = r
                fill_ts = None
                realized = 0.0
                continue
            if kind != "trade" or pending is None:
                continue
            ch = r.get("change")
            if ch == "FILLED":
                fill_ts = _dt(r.get("ts"))
                continue
            if "realized_r" in r:
                realized = float(r["realized_r"])
            if ch in _TERMINAL:
                rep = pending.get("report", {})
                tr = _build(rep, pending, fill_ts, _dt(r.get("ts")), realized, ch, src, h4_of)
                if tr is not None:
                    out.append(tr)
                pending = None
    out.sort(key=lambda t: t.entry_ts)
    return out


def _dt(s: object) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s))
    except ValueError:
        return None


def _build(
    rep: dict,
    sig: dict,
    fill_ts: datetime | None,
    exit_ts: datetime | None,
    realized: float,
    exit_reason: str,
    src: str,
    h4_of,
) -> TradeRecord | None:
    entry = rep.get("entry")
    sl = rep.get("stop_loss")
    if entry is None or sl is None:
        return None
    entry, sl = float(entry), float(sl)
    r_unit = abs(entry - sl)
    if r_unit <= 0:
        return None
    sym = str(rep.get("instrument") or sig.get("instrument") or "?")
    is_long = str(rep.get("direction", "")).upper() in ("LONG", "BUY")
    d = 1 if is_long else -1
    sig_ts = _dt(sig.get("ts")) or datetime.now(UTC)
    fill_ts = fill_ts or sig_ts
    exit_ts = exit_ts or fill_ts

    # MFE/MAE + bars_held aus H4 (wo vorhanden), sonst konservativ aus realized
    mfe = max(realized, 0.0)
    mae = min(realized, 0.0)
    bars_held = 0
    bars = h4_of(sym)
    if bars and fill_ts and exit_ts:
        seg = [b for b in bars if fill_ts <= b.open_time <= exit_ts]
        bars_held = len(seg)
        for b in seg:
            fav = (b.high - entry if d > 0 else entry - b.low) / r_unit
            adv = (entry - b.low if d > 0 else b.high - entry) / r_unit
            mfe = max(mfe, fav)
            mae = min(mae, -adv)

    wl = "WIN" if realized > 0.02 else "LOSS" if realized < -0.02 else "SCRATCH"
    return TradeRecord(
        trade_id=f"{src}:{sym}:{sig_ts.isoformat()}",
        instrument=sym,
        direction=Side.BUY if d > 0 else Side.SELL,
        setup_id=str(rep.get("setup_id") or "?"),
        strategy_version=str(rep.get("strategy_version") or "0.0.0"),
        signal_ts=sig_ts,
        information_cutoff=sig_ts,
        entry_ts=fill_ts,
        entry_price=entry,
        qty=1.0,
        initial_sl=sl,
        initial_tp=float(rep["tp2"]) if rep.get("tp2") is not None else None,
        exit_ts=exit_ts,
        exit_price=entry + d * realized * r_unit,
        exit_reason=exit_reason,
        gross_r=round(realized, 4),
        realized_r=round(realized, 4),
        pnl_ccy=round(realized, 4),
        mfe_r=round(mfe, 4),
        mae_r=round(mae, 4),
        bars_held=bars_held,
        win_loss=wl,
        # Extra-Kontext im trace_id-Slot missbrauchen wäre unsauber → separate Gruppierung unten
        run_id=None,
        trace_id=json.dumps(
            {
                "score": rep.get("opportunity_score"),
                "conf": rep.get("confidence_pct"),
                "rr": rep.get("rr_to_tp2"),
                "elig": sig.get("eligibility") or rep.get("live_eligibility"),
            }
        ),
    )


def _hours(t: TradeRecord) -> float:
    return (t.exit_ts - t.entry_ts).total_seconds() / 3600.0


def _meta(t: TradeRecord) -> dict:
    try:
        return json.loads(t.trace_id or "{}")
    except (json.JSONDecodeError, TypeError):
        return {}


def _block(trades: list[TradeRecord]) -> dict[str, object]:
    if not trades:
        return {"n_trades": 0}
    m = compute_metrics(trades)
    hold = [_hours(t) for t in trades]
    return {
        "n_trades": m.n_trades,
        "win_rate": round(m.win_rate, 4),
        "profit_factor": round(m.profit_factor, 3) if m.profit_factor != float("inf") else "inf",
        "expectancy_r": round(m.expectancy_r, 4),
        "avg_r": round(m.avg_r, 4),
        "total_r": round(m.total_r, 3),
        "max_drawdown_r": round(m.max_drawdown_r, 3),
        "avg_mfe_r": round(m.avg_mfe_r, 3),
        "avg_mae_r": round(m.avg_mae_r, 3),
        "sharpe_r": round(m.sharpe_r, 3),
        "sortino_r": round(m.sortino_r, 3),
        "longest_loss_streak": m.longest_loss_streak,
        "avg_hold_h": round(statistics.mean(hold), 1) if hold else 0.0,
        "median_hold_h": round(statistics.median(hold), 1) if hold else 0.0,
    }


def _grouped(trades: list[TradeRecord], key) -> dict[str, object]:
    buckets: dict[str, list[TradeRecord]] = {}
    for t in trades:
        buckets.setdefault(str(key(t)), []).append(t)
    return {k: _block(v) for k, v in sorted(buckets.items())}


def _score_bucket(t: TradeRecord) -> str:
    s = _meta(t).get("score")
    if s is None:
        return "n/a"
    s = float(s)
    return "<60" if s < 60 else "60-75" if s < 75 else "75-85" if s < 85 else ">=85"


def _rr_bucket(t: TradeRecord) -> str:
    rr = _meta(t).get("rr")
    if rr is None:
        return "n/a"
    rr = float(rr)
    return "<2" if rr < 2 else "2-3" if rr < 3 else ">=3"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", default="data/repository_real")
    ap.add_argument("--journals", default="data/repository_real/live/*.jsonl")
    ap.add_argument("--out", default="data/repository_real/live/performance.json")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    repo = MarketDataRepository(args.repo)
    paths = sorted(glob.glob(args.journals))
    trades = _load_trades(paths, repo)

    report = {
        "as_of": datetime.now(UTC).isoformat(),
        "journals": [Path(p).name for p in paths],
        "trades": _block(trades),
        "by_asset": _grouped(trades, lambda t: t.instrument),
        "by_setup": _grouped(trades, lambda t: t.setup_id),
        "by_direction": _grouped(trades, lambda t: t.direction.value),
        "by_rr_bucket": _grouped(trades, _rr_bucket),
        "by_score_bucket": _grouped(trades, _score_bucket),
        "by_eligibility": _grouped(trades, lambda t: _meta(t).get("elig") or "n/a"),
        "trade_list": [
            {
                "instrument": t.instrument,
                "setup": t.setup_id,
                "dir": t.direction.value,
                "entry_ts": t.entry_ts.isoformat(),
                "exit_ts": t.exit_ts.isoformat(),
                "entry": t.entry_price,
                "exit": round(t.exit_price, 4),
                "realized_r": t.realized_r,
                "mfe_r": t.mfe_r,
                "mae_r": t.mae_r,
                "hold_h": round(_hours(t), 1),
                "reason": t.exit_reason,
                **{k: _meta(t).get(k) for k in ("score", "conf", "rr", "elig")},
            }
            for t in trades
        ],
    }
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(report, indent=2, default=str) + "\n")

    if args.json:
        print(json.dumps(report, indent=2, default=str))
        return 0

    t = report["trades"]
    print(
        f"\n{'=' * 64}\n  PERFORMANCE  ·  {len(trades)} Trades  ·  {len(paths)} Journale\n{'=' * 64}"
    )
    if not trades:
        print("  keine abgeschlossenen Trades in den Journalen.")
        return 0
    for k, v in t.items():  # type: ignore[union-attr]
        print(f"  {k:20} {v}")
    for grp in ("by_asset", "by_setup", "by_direction", "by_score_bucket", "by_eligibility"):
        print(f"\n  {grp}:")
        for k, v in report[grp].items():  # type: ignore[union-attr]
            print(
                f"    {k:22} n={v.get('n_trades')} exp={v.get('expectancy_r')} "
                f"PF={v.get('profit_factor')} WR={v.get('win_rate')} totalR={v.get('total_r')}"
            )
    print(f"\n  → {args.out}\n{'=' * 64}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
