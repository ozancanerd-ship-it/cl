"""Strategy-Backtest — historische Daten laufen durch **dieselbe** Pipeline wie später Live.

```
MarketDataRepository → ReplayClock → MarketContextAssembler → MarketContext
                     → PaperLiveRunner.feed()  (strategy.evaluate → Signal → Paper-Position → Alerts)
                     → Trade Ledger + Metriken + Signal-Analyse
```

**Keine zweite Strategie-Logik.** Der Backtest ruft ``strategy.evaluate`` über den
``PaperLiveRunner`` auf — identisch zu Paper/Demo/Live. Der MA-Crossover-Referenzpfad
(``engine.reference_backtest``) validiert nur die Execution-Schicht und ist strikt getrennt.

**Deterministisch & PIT.** ``ReplayClock`` (kein Wall-Clock), ``MarketContextAssembler`` mit
``as_of = cutoff`` (kein Look-ahead), ``RunManifest`` + ``output_hash`` für Reproduzierbarkeit.

**Keine Fake-Daten.** Fehlt die geforderte Historie, bricht der Lauf mit
``DatasetIncompleteError`` ab (``validate_dataset`` meldet exakt, was fehlt).

**Noch keine Broker, keine Echtgeld-Orders.**
"""

from __future__ import annotations

import dataclasses
import json
from collections import Counter
from collections.abc import Callable
from datetime import datetime

from trading_agent.core.enums import AssetClass, Direction, Side, Timeframe
from trading_agent.core.version import STRATEGY_VERSION
from trading_agent.data.repository import MarketDataRepository
from trading_agent.engine.backtest_metrics import (
    RunTelemetry,
    StrategyBacktestReport,
    TradeOutcome,
    build_strategy_report,
    confidence_tier_of,
)
from trading_agent.engine.parity import ParityReport
from trading_agent.engine.replay import (
    AssemblerConfig,
    DatasetReport,
    DatasetRequirements,
    MarketContextAssembler,
    ReplayClock,
    validate_dataset,
)
from trading_agent.journal.ledger import Ledger, TradeRecord
from trading_agent.research.metrics import Metrics, compute_metrics, equity_curve_r
from trading_agent.research.registry import RunManifest, output_hash
from trading_agent.strategy.alerts import AlertParams
from trading_agent.strategy.engine import EngineParams
from trading_agent.strategy.evaluate import EvaluationResult
from trading_agent.strategy.paper_live import PaperLiveRunner, PaperLiveStep
from trading_agent.strategy.position import ExitReason, PaperPosition
from trading_agent.strategy.signal import SignalState


@dataclasses.dataclass(frozen=True, slots=True)
class BacktestConfig:
    instruments: tuple[str, ...]
    start: datetime
    end: datetime
    base_timeframe: Timeframe = Timeframe.M5
    asset_class: AssetClass = AssetClass.CRYPTO  # steuert 24/7-Gate, News-Relevanz, Kalender
    warmup_bars: int = 300
    min_days: int = 180
    starting_equity: float = 1000.0
    risk_per_trade_pct: float = 1.0
    scratch_r: float = 0.1
    fixed_spread: float | None = 0.5
    news_feed_available: bool = False
    read_native_higher: bool = True
    require_native_higher: bool = False
    require_m1: bool = False
    seed: int = 0
    dataset_version: str = "local"
    engine_params: EngineParams | None = None
    alert_params: AlertParams | None = None
    parity_check: bool = False  # nach dem Lauf: vorgeladen ≡ streaming? (Look-ahead-Beweis)
    parity_sample: int = 300  # so viele cutoffs für den Parity-Vergleich (der Live-Pfad ist teuer)


@dataclasses.dataclass(frozen=True, slots=True)
class BacktestResult:
    run_id: str
    manifest: RunManifest
    dataset_report: DatasetReport
    trades: list[TradeRecord]
    outcomes: list[TradeOutcome]
    metrics: Metrics
    strategy_report: StrategyBacktestReport
    equity_curve_r: list[float]
    telemetry: RunTelemetry
    bars_processed: int
    output_hash: str
    parity: ParityReport | None = None

    @property
    def ok(self) -> bool:
        return self.dataset_report.ok

    def parity_summary(self) -> str:
        if self.parity is None:
            return "parity: nicht geprüft (parity_check=False)"
        from trading_agent.engine.parity import render_parity

        return render_parity(self.parity)


class Backtest:
    def __init__(
        self,
        repository: MarketDataRepository,
        *,
        ledger_path: str | None = None,
        evaluate_fn: Callable[..., EvaluationResult] | None = None,
    ) -> None:
        self.repo = repository
        self.ledger = Ledger(ledger_path or str(repository.root / "strategy_ledger.sqlite"))
        # DI: nur für Tests / alternative Pipelines. None ⇒ echte strategy.evaluate.
        self._evaluate_fn = evaluate_fn

    # ------------------------------------------------------------------ run
    def run(self, cfg: BacktestConfig) -> BacktestResult:
        req = DatasetRequirements(
            instruments=cfg.instruments,
            base_timeframe=cfg.base_timeframe,
            min_days=cfg.min_days,
            warmup_bars=cfg.warmup_bars,
            require_native_higher=cfg.require_native_higher,
            require_m1=cfg.require_m1,
            require_news_feed=cfg.news_feed_available,
        )
        report = validate_dataset(self.repo, req, start=cfg.start, end=cfg.end)
        report.raise_if_incomplete()  # KEINE Fake-Daten — harter Stopp

        manifest = self._manifest(cfg)
        run_id = manifest.manifest_hash()[:16]

        all_trades: list[TradeRecord] = []
        all_outcomes: list[TradeOutcome] = []
        bars_processed = 0
        tele = _TelemetryAccumulator()

        for inst in cfg.instruments:
            trades, outcomes, n_bars = self._run_instrument(inst, cfg, run_id, tele)
            all_trades.extend(trades)
            all_outcomes.extend(outcomes)
            bars_processed += n_bars

        all_trades.sort(key=lambda t: (t.entry_ts, t.instrument))
        all_outcomes.sort(key=lambda o: (o.entry_ts, o.instrument))

        parity: ParityReport | None = None
        if cfg.parity_check:
            parity = self._parity_check(cfg)

        metrics = compute_metrics(all_trades)
        telemetry = tele.finalize()
        strategy_report = build_strategy_report(all_outcomes, telemetry)
        oh = output_hash(
            [
                {
                    "i": t.instrument,
                    "d": t.direction.value,
                    "e": round(t.entry_price, 6),
                    "x": round(t.exit_price, 6),
                    "r": round(t.realized_r, 6),
                    "reason": t.exit_reason,
                    "held": t.bars_held,
                }
                for t in all_trades
            ]
        )
        return BacktestResult(
            run_id=run_id,
            manifest=manifest,
            dataset_report=report,
            trades=all_trades,
            outcomes=all_outcomes,
            metrics=metrics,
            strategy_report=strategy_report,
            equity_curve_r=equity_curve_r(all_trades),
            telemetry=telemetry,
            bars_processed=bars_processed,
            output_hash=oh,
            parity=parity,
        )

    # ------------------------------------------------------------------ parity
    def _parity_check(self, cfg: BacktestConfig) -> ParityReport:
        """Vorgeladener Assembler ≡ pro-cutoff frisch gebauter Kontext? (Look-ahead-Beweis)"""
        from trading_agent.engine.parity import run_parity

        base_ep = (cfg.engine_params or EngineParams()).evaluate
        ep = dataclasses.replace(base_ep, asset_class=cfg.asset_class)
        return run_parity(
            self.repo,
            instruments=cfg.instruments,
            start=cfg.start,
            end=cfg.end,
            base_timeframe=cfg.base_timeframe,
            warmup_bars=cfg.warmup_bars,
            read_native_higher=cfg.read_native_higher,
            max_cutoffs=cfg.parity_sample,
            evaluate_params=ep,
        )

    # ------------------------------------------------------------------ per instrument
    def _run_instrument(
        self,
        inst: str,
        cfg: BacktestConfig,
        run_id: str,
        tele: _TelemetryAccumulator,
    ) -> tuple[list[TradeRecord], list[TradeOutcome], int]:
        assembler = MarketContextAssembler(
            self.repo,
            AssemblerConfig(
                instrument=inst,
                base_timeframe=cfg.base_timeframe,
                warmup_bars=cfg.warmup_bars,
                read_native_higher=cfg.read_native_higher,
                news_feed_available=cfg.news_feed_available,
                asset_class=cfg.asset_class,
                fixed_spread=cfg.fixed_spread,
                account_equity=cfg.starting_equity,
            ),
        )
        engine_params = dataclasses.replace(
            cfg.engine_params or EngineParams(),
            evaluate=dataclasses.replace(
                (cfg.engine_params or EngineParams()).evaluate, asset_class=cfg.asset_class
            ),
        )
        assembler.bind(cfg.start, cfg.end)
        grid_bars = self.repo.read_ohlcv(
            inst, cfg.base_timeframe, cfg.start, cfg.end, as_of=cfg.end
        )
        if not grid_bars:
            return [], [], 0
        clock = ReplayClock.from_bars(grid_bars)
        runner = PaperLiveRunner(
            engine_params=engine_params,
            alert_params=cfg.alert_params,
            evaluate_fn=self._evaluate_fn,
        )

        # Analyse-Schnappschuss je Position beim Öffnen festhalten (für die Signal-Analyse).
        entry_snap: dict[str, _EntrySnapshot] = {}
        closed: list[PaperPosition] = []

        for cutoff in clock:
            mc = assembler.at(cutoff)
            step = runner.feed(mc)
            tick = step.tick
            tele.observe(step)

            if tick.opened is not None:
                entry_snap[tick.opened.position_id] = _snapshot(tick.result)
            if tick.closed is not None:
                closed.append(tick.closed)

        # offene Positionen am Ende der Historie zum letzten Close schließen
        last_close = grid_bars[-1].close
        for pos in runner.engine.force_close(
            price=last_close, at=grid_bars[-1].close_time, reason=ExitReason.END_OF_DATA
        ):
            closed.append(pos)

        trades: list[TradeRecord] = []
        outcomes: list[TradeOutcome] = []
        # nur tatsächlich gefüllte Positionen sind Trades — nie getriggerte PENDING-Limits
        # (EXPIRED, entry_ts is None) werden verworfen, nicht als Trade gewertet.
        filled = [p for p in closed if p.entry_ts is not None]
        for idx, pos in enumerate(filled):
            snap = entry_snap.get(pos.position_id, _EntrySnapshot.unknown())
            rec, out = self._build_trade(pos, snap, cfg, run_id, inst, idx)
            trades.append(rec)
            outcomes.append(out)
            self.ledger.record_trade(rec)

        return trades, outcomes, len(clock)

    # ------------------------------------------------------------------ trade record
    def _build_trade(
        self,
        pos: PaperPosition,
        snap: _EntrySnapshot,
        cfg: BacktestConfig,
        run_id: str,
        inst: str,
        idx: int,
    ) -> tuple[TradeRecord, TradeOutcome]:
        side = Side.BUY if pos.direction is Direction.LONG else Side.SELL
        entry_ts = pos.entry_ts or pos.opened_at
        exit_ts = pos.closed_at or entry_ts
        realized_r = round(pos.realized_r, 6)
        risk_ccy = cfg.starting_equity * cfg.risk_per_trade_pct / 100.0
        pnl_ccy = realized_r * risk_ccy
        wl = (
            "WIN"
            if realized_r > cfg.scratch_r
            else "LOSS"
            if realized_r < -cfg.scratch_r
            else "SCRATCH"
        )
        exit_reason = pos.close_reason.value if pos.close_reason is not None else "unknown"
        trade_id = f"{run_id}-{inst}-{idx:04d}"

        rec = TradeRecord(
            trade_id=trade_id,
            run_id=run_id,
            trace_id=pos.signal_id,
            instrument=inst.upper(),
            direction=side,
            setup_id=pos.signal_id,
            strategy_version=pos.strategy_version,
            signal_ts=pos.information_cutoff,
            information_cutoff=pos.information_cutoff,
            entry_ts=entry_ts,
            entry_price=pos.entry,
            qty=round(risk_ccy / pos.r_unit, 8) if pos.r_unit else 0.0,
            initial_sl=pos.initial_sl,
            initial_tp=pos.tp1,
            exit_ts=exit_ts,
            exit_price=pos.last_price,
            exit_reason=exit_reason,
            gross_r=realized_r,
            realized_r=realized_r,
            pnl_ccy=round(pnl_ccy, 6),
            mfe_r=round(pos.mfe_r, 4),
            mae_r=round(pos.mae_r, 4),
            bars_held=pos.bars_held,
            win_loss=wl,
        )
        out = TradeOutcome(
            trade_id=trade_id,
            instrument=inst.upper(),
            timeframe=cfg.base_timeframe.value,
            direction=side,
            setup_id=pos.signal_id,
            entry_ts=entry_ts.isoformat(),
            exit_ts=exit_ts.isoformat(),
            realized_r=realized_r,
            gross_r=realized_r,
            mfe_r=round(pos.mfe_r, 4),
            mae_r=round(pos.mae_r, 4),
            bars_held=pos.bars_held,
            exit_reason=exit_reason,
            tp_level=pos.tp_level_reached,
            win_loss=wl,
            score=snap.score,
            score_tier=snap.score_tier,
            confidence=snap.confidence,
            confidence_tier=confidence_tier_of(snap.confidence),
            confluence_net=snap.confluence_net,
            confluence_support=snap.confluence_support,
            setup_state_at_entry=snap.setup_state,
        )
        return rec, out

    # ------------------------------------------------------------------ manifest
    def _manifest(self, cfg: BacktestConfig) -> RunManifest:
        fps = []
        for inst in cfg.instruments:
            try:
                fps.append(
                    f"{inst}:{self.repo.dataset_fingerprint(inst, cfg.base_timeframe, as_of=cfg.end)}"
                )
            except Exception:  # Repo ohne dieses Dataset — von validate_dataset bereits gemeldet
                fps.append(f"{inst}:missing")
        params = {
            "risk_per_trade_pct": cfg.risk_per_trade_pct,
            "warmup_bars": cfg.warmup_bars,
            "starting_equity": cfg.starting_equity,
            "fixed_spread": cfg.fixed_spread,
            "news_feed_available": cfg.news_feed_available,
            "read_native_higher": cfg.read_native_higher,
            "asset_class": cfg.asset_class.value,
            "engine_params": _params_repr(cfg.engine_params),
        }
        return RunManifest(
            strategy_version=STRATEGY_VERSION,
            config_hash=_hash_obj(params),
            dataset_version=cfg.dataset_version,
            dataset_fingerprint="|".join(sorted(fps)),
            instrument=",".join(sorted(i.upper() for i in cfg.instruments)),
            timeframe=cfg.base_timeframe.value,
            start=cfg.start.isoformat(),
            end=cfg.end.isoformat(),
            seed=cfg.seed,
            params=params,
        )


# --------------------------------------------------------------------------------- helpers


@dataclasses.dataclass(frozen=True, slots=True)
class _EntrySnapshot:
    score: float | None
    score_tier: str
    confidence: float | None
    confluence_net: float | None
    confluence_support: float | None
    setup_state: str

    @classmethod
    def unknown(cls) -> _EntrySnapshot:
        return cls(None, "?", None, None, None, "?")


def _snapshot(result: EvaluationResult) -> _EntrySnapshot:
    d = result.decision
    return _EntrySnapshot(
        score=d.score,
        score_tier=d.tier.value if d.tier is not None else "?",
        confidence=d.confidence,
        confluence_net=result.confluence.net_confluence if result.confluence else None,
        confluence_support=result.confluence.support_score if result.confluence else None,
        setup_state=d.setup_state.value,
    )


class _TelemetryAccumulator:
    def __init__(self) -> None:
        self.steps = 0
        self.decisions: Counter[str] = Counter()
        self.no_trade_reasons: Counter[str] = Counter()
        self.veto_frequency: Counter[str] = Counter()
        self.signal_revisions = 0
        self.signals_created = 0
        self.signals_invalidated = 0
        self.signals_expired = 0
        self.exit_required_events = 0
        self.alerts_raised = 0

    def observe(self, step: PaperLiveStep) -> None:
        tick = step.tick
        self.steps += 1
        self.decisions[tick.result.decision.decision.value] += 1
        for rec in tick.result.no_trade.records:
            self.no_trade_reasons[rec.reason.value] += 1
        for v in tick.result.decision.vetoes:
            self.veto_frequency[v.value] += 1
        sig = tick.signal
        if sig is not None:
            if sig.is_new:
                self.signals_created += 1
            if sig.changed:
                self.signal_revisions += 1
            st = sig.signal.state
            if st is SignalState.INVALIDATED:
                self.signals_invalidated += 1
            elif st is SignalState.EXPIRED:
                self.signals_expired += 1
            elif st is SignalState.EXIT_REQUIRED:
                self.exit_required_events += 1
        self.alerts_raised += sum(1 for a in step.alerts if a.delivered)

    def finalize(self) -> RunTelemetry:
        return RunTelemetry(
            steps=self.steps,
            decisions=self.decisions,
            no_trade_reasons=self.no_trade_reasons,
            veto_frequency=self.veto_frequency,
            signal_revisions=self.signal_revisions,
            signals_created=self.signals_created,
            signals_invalidated=self.signals_invalidated,
            signals_expired=self.signals_expired,
            exit_required_events=self.exit_required_events,
            alerts_raised=self.alerts_raised,
        )


def _params_repr(p: EngineParams | None) -> str:
    if p is None:
        return "default"
    try:
        return _hash_obj(dataclasses.asdict(p))
    except (TypeError, ValueError):
        return "unhashable"


def _hash_obj(obj: object) -> str:
    import hashlib

    blob = json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


__all__ = ["Backtest", "BacktestConfig", "BacktestResult"]
