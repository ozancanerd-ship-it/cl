"""Reference backtest (MA-crossover) — **validates the execution layer**, not the strategy.

Exercises the event-bus, ``PaperBroker``, the cost model and look-ahead immunity. The *strategy*
backtest — the one that runs the real ``strategy.evaluate`` pipeline through ``PaperLiveRunner`` —
lives in ``engine/backtest.py``. Kept separate so the two never share decision logic.

Wiring: ``BacktestDriver`` publishes ``BarClosed`` on the ``EventBus``; ``_Session`` subscribes,
runs the (pluggable) strategy callback, sizes via a risk-first rule, submits to ``PaperBroker``,
and turns closed positions into ``TradeRecord``s with MFE/MAE.

Deterministic: same ``ReferenceBacktestConfig`` + same dataset  ->  same trades  ->  same output hash.
Look-ahead-free: the strategy only ever sees bars whose ``close_time <= now``; market orders
fill on the *next* bar's open.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from trading_agent.core.enums import Side, Timeframe
from trading_agent.core.models import OHLCV
from trading_agent.data.repository import MarketDataRepository
from trading_agent.execution.brokers.base import Fill, OrderIntent, OrderType
from trading_agent.execution.brokers.paper import PaperBroker
from trading_agent.execution.simulation import CostParams, FillParams
from trading_agent.journal.ledger import Ledger, TradeRecord
from trading_agent.refdata.instruments import InstrumentMaster
from trading_agent.research.metrics import Metrics, compute_metrics, equity_curve_r
from trading_agent.research.registry import RunManifest, output_hash
from trading_agent.runtime.bus import EventBus
from trading_agent.runtime.drivers.backtest_driver import BacktestDriver
from trading_agent.runtime.events import BarClosed
from trading_agent.strategy.reference import Flat, ReferenceMAStrategy, ReferenceSignal


@dataclass(frozen=True, slots=True)
class ReferenceBacktestConfig:
    instrument: str
    timeframe: Timeframe
    start: datetime
    end: datetime
    starting_equity: float = 1000.0
    risk_per_trade_pct: float = 1.0
    scratch_r: float = 0.1
    seed: int = 0
    fill: FillParams = field(default_factory=FillParams)
    cost: CostParams = field(default_factory=CostParams)


@dataclass(frozen=True, slots=True)
class ReferenceBacktestResult:
    run_id: str
    manifest: RunManifest
    trades: list[TradeRecord]
    metrics: Metrics
    equity_curve_r: list[float]
    bars_processed: int
    output_hash: str


@dataclass(slots=True)
class _Trade:
    trace_id: str
    side: Side
    signal_ts: datetime
    qty: float
    sl_price: float
    tp_price: float
    entry_pnl_snapshot: float
    entered: bool = False
    entry_ts: datetime | None = None
    entry_price: float = 0.0
    r_unit: float = 0.0
    mfe_r: float = 0.0
    mae_r: float = 0.0
    bars_held: int = 0


class _Session:
    def __init__(
        self,
        cfg: ReferenceBacktestConfig,
        instruments: InstrumentMaster,
        strategy: ReferenceMAStrategy,
        ledger: Ledger,
        run_id: str,
    ) -> None:
        self.cfg = cfg
        self.strategy = strategy
        self.ledger = ledger
        self.run_id = run_id
        self.broker = PaperBroker(
            instruments, starting_equity=cfg.starting_equity, fill_params=cfg.fill
        )
        self.history: list[OHLCV] = []
        self.trades: list[TradeRecord] = []
        self._t: _Trade | None = None
        self._seq = 0
        self.bars_processed = 0

    async def on_bar(self, ev: BarClosed) -> None:
        bar = ev.bar
        assert bar is not None
        self.history.append(bar)
        self.bars_processed += 1
        inst = self.cfg.instrument.upper()

        had_position = self.broker.open_position(inst) is not None
        fills = self.broker.on_bar(bar)
        has_position = self.broker.open_position(inst) is not None

        if self._t is not None and not self._t.entered and has_position:
            self._sync_entry(bar)
        if self._t is not None and self._t.entered:
            self._update_excursion(bar)
        if self._t is not None and self._t.entered and had_position and not has_position:
            price, reason = self._infer_exit(fills, bar)
            self._close(bar.close_time, price, reason)
            has_position = False

        decision = self.strategy.evaluate(self.history, has_position)
        if isinstance(decision, Flat) and self._t is not None:
            await self._submit_close()
        elif isinstance(decision, ReferenceSignal) and self._t is None:
            await self._submit_entry(decision, bar)

    # ------------------------------------------------------------------ entry / exit

    async def _submit_entry(self, sig: ReferenceSignal, bar: OHLCV) -> None:
        r_unit = abs(bar.close - sig.sl_price)
        if r_unit <= 0:
            return
        risk_ccy = self.broker.equity * self.cfg.risk_per_trade_pct / 100.0
        qty = risk_ccy / r_unit
        if qty <= 0:
            return
        self._seq += 1
        trace_id = f"{self.run_id}-t{self._seq:04d}"
        inst = self.cfg.instrument.upper()
        exit_side = Side.SELL if sig.side is Side.BUY else Side.BUY

        await self.broker.submit(
            OrderIntent(
                client_order_id=f"{trace_id}-e",
                instrument=inst,
                side=sig.side,
                order_type=OrderType.MARKET,
                qty=qty,
                trace_id=trace_id,
            )
        )
        await self.broker.submit(
            OrderIntent(
                client_order_id=f"{trace_id}-sl",
                instrument=inst,
                side=exit_side,
                order_type=OrderType.STOP,
                qty=qty,
                stop_price=sig.sl_price,
                reduce_only=True,
                trace_id=trace_id,
            )
        )
        await self.broker.submit(
            OrderIntent(
                client_order_id=f"{trace_id}-tp",
                instrument=inst,
                side=exit_side,
                order_type=OrderType.LIMIT,
                qty=qty,
                limit_price=sig.tp_price,
                reduce_only=True,
                trace_id=trace_id,
            )
        )
        self._t = _Trade(
            trace_id=trace_id,
            side=sig.side,
            signal_ts=bar.close_time,
            qty=qty,
            sl_price=sig.sl_price,
            tp_price=sig.tp_price,
            entry_pnl_snapshot=self.broker.realized_pnl,
        )
        self.ledger.record_decision(
            "SIGNAL",
            trace_id=trace_id,
            instrument=inst,
            payload={"side": sig.side.value, "sl": sig.sl_price, "tp": sig.tp_price},
        )

    async def _submit_close(self) -> None:
        t = self._t
        if t is None or not t.entered:
            return
        exit_side = Side.SELL if t.side is Side.BUY else Side.BUY
        await self.broker.submit(
            OrderIntent(
                client_order_id=f"{t.trace_id}-flat",
                instrument=self.cfg.instrument.upper(),
                side=exit_side,
                order_type=OrderType.MARKET,
                qty=t.qty,
                reduce_only=True,
                trace_id=t.trace_id,
            )
        )

    def _sync_entry(self, bar: OHLCV) -> None:
        t = self._t
        assert t is not None
        pos = self.broker.open_position(self.cfg.instrument)
        assert pos is not None
        t.entered = True
        t.entry_ts = bar.close_time
        t.entry_price = pos.entry_price
        t.r_unit = abs(pos.entry_price - t.sl_price) or abs(bar.close - t.sl_price) or 1e-9
        self.ledger.record_decision(
            "ORDER",
            trace_id=t.trace_id,
            instrument=self.cfg.instrument.upper(),
            payload={"entry_price": pos.entry_price, "qty": t.qty, "liq": pos.liq_price},
        )

    def _update_excursion(self, bar: OHLCV) -> None:
        t = self._t
        assert t is not None and t.r_unit > 0
        t.bars_held += 1
        if t.side is Side.BUY:
            fav, adv = (bar.high - t.entry_price) / t.r_unit, (bar.low - t.entry_price) / t.r_unit
        else:
            fav, adv = (t.entry_price - bar.low) / t.r_unit, (t.entry_price - bar.high) / t.r_unit
        t.mfe_r = max(t.mfe_r, fav)
        t.mae_r = min(t.mae_r, adv)

    def _infer_exit(self, fills: list[Fill], bar: OHLCV) -> tuple[float, str]:
        for f in fills:
            if f.is_liquidation:
                return f.price, "LIQUIDATION"
        t = self._t
        assert t is not None
        if t.side is Side.BUY:
            if bar.low <= t.sl_price:
                return t.sl_price, "SL"
            if bar.high >= t.tp_price:
                return t.tp_price, "TP"
        else:
            if bar.high >= t.sl_price:
                return t.sl_price, "SL"
            if bar.low <= t.tp_price:
                return t.tp_price, "TP"
        return bar.close, "MANUAL"

    def _close(self, ts: datetime, exit_price: float, reason: str) -> None:
        t = self._t
        assert t is not None and t.entry_ts is not None
        d = exit_price - t.entry_price
        price_move = d if t.side is Side.BUY else -d
        gross_r = price_move / t.r_unit if t.r_unit else 0.0
        net_pnl_ccy = self.broker.realized_pnl - t.entry_pnl_snapshot
        risk_ccy = t.r_unit * t.qty
        net_r = net_pnl_ccy / risk_ccy if risk_ccy else 0.0
        wl = (
            "WIN"
            if net_r > self.cfg.scratch_r
            else "LOSS"
            if net_r < -self.cfg.scratch_r
            else "SCRATCH"
        )
        rec = TradeRecord(
            trade_id=f"{self.run_id}-{self._seq:04d}",
            run_id=self.run_id,
            trace_id=t.trace_id,
            instrument=self.cfg.instrument.upper(),
            direction=t.side,
            signal_ts=t.signal_ts,
            information_cutoff=t.signal_ts,
            entry_ts=t.entry_ts,
            entry_price=t.entry_price,
            qty=t.qty,
            initial_sl=t.sl_price,
            initial_tp=t.tp_price,
            exit_ts=ts,
            exit_price=exit_price,
            exit_reason=reason,
            gross_r=round(gross_r, 6),
            realized_r=round(net_r, 6),
            pnl_ccy=round(net_pnl_ccy, 6),
            fees_ccy=round(self.broker.fees_paid, 6),
            funding_ccy=round(self.broker.funding_paid, 6),
            mfe_r=round(t.mfe_r, 4),
            mae_r=round(t.mae_r, 4),
            bars_held=t.bars_held,
            win_loss=wl,
        )
        self.trades.append(rec)
        self.ledger.record_trade(rec)
        self.ledger.record_decision(
            "EXIT",
            trace_id=t.trace_id,
            instrument=rec.instrument,
            payload={"reason": reason, "realized_r": net_r},
        )
        self._t = None

    def finalize(self, last_bar: OHLCV) -> None:
        inst = self.cfg.instrument.upper()
        if self._t is not None and self._t.entered and self.broker.open_position(inst) is not None:
            self._close(last_bar.close_time, last_bar.close, "END_OF_DATA")


class ReferenceBacktest:
    def __init__(
        self,
        repository: MarketDataRepository,
        instruments: InstrumentMaster,
        *,
        ledger_path: str | None = None,
    ) -> None:
        self.repo = repository
        self.im = instruments
        self.ledger = Ledger(ledger_path or str(repository.root / "ledger.sqlite"))

    async def run(
        self, cfg: ReferenceBacktestConfig, strategy: ReferenceMAStrategy | None = None
    ) -> ReferenceBacktestResult:
        strategy = strategy or ReferenceMAStrategy()
        # fingerprint only the bars this run can actually see (point-in-time)
        fp = self.repo.dataset_fingerprint(cfg.instrument, cfg.timeframe, as_of=cfg.end)
        manifest = RunManifest(
            strategy_version="reference-0",
            config_hash="",
            dataset_version="local",
            dataset_fingerprint=fp,
            instrument=cfg.instrument.upper(),
            timeframe=cfg.timeframe.value,
            start=cfg.start.isoformat(),
            end=cfg.end.isoformat(),
            seed=cfg.seed,
            params={
                "risk_per_trade_pct": cfg.risk_per_trade_pct,
                "fast": strategy.fast,
                "slow": strategy.slow,
                "sl_atr": strategy.sl_atr,
                "tp_atr": strategy.tp_atr,
            },
        )
        run_id = manifest.manifest_hash()[:16]

        bus = EventBus(raise_on_handler_error=True)
        session = _Session(cfg, self.im, strategy, self.ledger, run_id)
        bus.subscribe(BarClosed, session.on_bar)

        driver = BacktestDriver(bus, self.repo)
        bars = driver.load(cfg.instrument, cfg.timeframe, cfg.start, cfg.end)
        await driver.run(cfg.instrument, cfg.timeframe, cfg.start, cfg.end)
        if bars:
            session.finalize(bars[-1])

        metrics = compute_metrics(session.trades)
        # hash the *decisions*, not identifiers: direction, entry/exit, R, reason, hold
        oh = output_hash(
            [
                {
                    "d": t.direction.value,
                    "e": round(t.entry_price, 6),
                    "x": round(t.exit_price, 6),
                    "r": round(t.realized_r, 6),
                    "reason": t.exit_reason,
                    "held": t.bars_held,
                }
                for t in session.trades
            ]
        )
        return ReferenceBacktestResult(
            run_id=run_id,
            manifest=manifest,
            trades=session.trades,
            metrics=metrics,
            equity_curve_r=equity_curve_r(session.trades),
            bars_processed=session.bars_processed,
            output_hash=oh,
        )


__all__ = ["ReferenceBacktest", "ReferenceBacktestConfig", "ReferenceBacktestResult"]
