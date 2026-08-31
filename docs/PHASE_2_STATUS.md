# Phase 2 Status — 2A (Historical / Research) + 2B (Live Data / Paper-Live)

**Date:** 2026-08-28 · **Result:** 2A **and** 2B code-complete, all checks green.
**Tests:** 264 passed · ruff + `mypy --strict` clean · 132 source files.

> Still: no real-money orders, no private API keys, no automatic live execution.
> Network **is** available in this environment — real public market data was fetched from
> Kraken and Bybit (no keys). MetaTrader5 (Pepperstone / gold) is Windows-only and not runnable here.

---

## 1. Binding decisions applied to the plan

| # | Decision | Where |
|---|----------|-------|
| Python core; MT5 = Python integration + adapter; MQL5 never holds strategy/risk/AI logic | `ARCHITECTURE.md` §1 (Leitplanken 10–12), §3a |
| Gold: Pepperstone + MT5 (`MetaTrader5` python, lazy import, Windows-only) | `config/brokers.example.yaml`, `config/providers.example.yaml` |
| Crypto: **Kraken Pro** primary, **Bybit** secondary/fallback | `data/providers/kraken.py`, `data/providers/bybit_public.py`, `config/providers.example.yaml` router |
| Investments: Trade Republic — official/permitted only, **manual import**, no unofficial API | `config/brokers.example.yaml` (`integration: manual_import`) |
| BrokerRouter: `Strategy → Risk → BrokerRouter → BrokerAdapter`; strategy never touches a broker | `execution/router.py` |
| Controlled-aggressive risk; leverage never overrides the loss budget; dynamic size multiplier | `docs/strategy/sizing.md` §1/§1a, `config/risk.example.yaml` (`risk_tiers` 1.0/0.65/0.40 %, `hard_max_risk_pct`, `size_multiplier`) |
| Phase 2 split 2A → 2B; after 2B **stop** | `FINAL_IMPLEMENTATION_PLAN.md` Phase 2 |
| Code / classes / vars / technical docs / logs in **English**; planning docs + user chat stay German | applied to all Phase-2 code |
| Live pipeline: Provider → Ingestion → Normalization → Data Quality → Event Bus → Strategy → Risk → Signal → Paper Execution | `data/ingestion/service.py` + `runtime/` |

---

## 2. Phase 2A — Historical / Research (what was built)

| Module | Purpose |
|--------|---------|
| `runtime/events.py` + `runtime/bus.py` | In-process async event bus — ordered, depth-first completion, sync/async handlers. |
| `net/ratelimit.py` + `net/client.py` | Token-bucket limiter; `HttpClient` with retry+backoff+jitter, circuit breaker, redacted logging. |
| `data/registry.py` + `data/router.py` | `ProviderRegistry` (capability/cost/license matrix) + `ProviderRouter` (best source per asset-class/kind/mode + health fallback). Loads `config/providers.example.yaml`. |
| `data/providers/kraken.py` | Kraken Pro public REST — OHLC + Trades, normalized to core models, **only confirmed bars**. |
| `data/providers/bybit_public.py` | Bybit v5 public REST — kline (newest-first reversed) + funding history + open interest. |
| `execution/brokers/base.py` | `OrderIntent`, `Fill`, `BrokerHealth`; `MarketDataAdapter` vs `BrokerAdapter` (separated). |
| `execution/simulation.py` | `CostModel` (maker/taker fees, spread, slippage, funding), `FillModel` (limit/market/stop, partial fills, latency), `MarginModel` + `LiquidationModel` (linear perp, isolated). |
| `execution/router.py` | `BrokerRouter` — routes by instrument, health fallback (Kraken→Bybit), **refuses to register a live-capable adapter in sim modes**. |
| `execution/brokers/paper.py` | `PaperBroker` — simulated fills via `execution/simulation`, positions, PnL, funding, liquidation. `is_live_capable = False`. |
| `journal/ledger.py` | `Ledger` (SQLite, append-only): `DecisionLedger` trace (`SIGNAL→ORDER→EXIT` …) + `TradeRecord` (R multiple, MFE/MAE, cost breakdown). |
| `research/metrics.py` | Win rate, profit factor, expectancy, avg/median R, max DD (R), loss streak, MFE/MAE, **net vs gross + cost drag**. |
| `research/validation.py` | Chronological 50/25/25 split, walk-forward folds, purge/embargo, **time-stability**, **symbol-stability**. |
| `research/robustness.py` | Monte-Carlo (trade bootstrap ≥ 1000, dropout, cost stress, start jitter), **ruin probability**, deterministic per seed. |
| `research/registry.py` | `RunManifest` (`code_sha` via git, `config_hash`, `dataset_fingerprint`, seed, range, strategy_version) → deterministic `manifest_hash`; `RunRegistry`; `output_hash` over trade decisions. |
| `strategy/reference.py` | `ReferenceMAStrategy` — SMA crossover. **Plumbing fixture only.** Not the real strategy; the real one (spec `0.1.0`) slots in at Phase 3 via the same callback. |
| `runtime/drivers/backtest_driver.py` | Replays repository bars as `BarClosed` events on the bus, driving a `SimClock`. |
| `engine/backtest.py` | Event-driven backtest: `BacktestDriver` → bus → `_Session` (strategy + `PaperBroker` + trade builder) → `BacktestResult` (trades, metrics, equity curve, manifest, output hash). |
| `scripts/fetch_history.py` | User-run CLI: pulls **real** OHLCV from Kraken/Bybit into the repository (public data, no key). |
| `scripts/run_backtest.py` | Run a backtest + print metrics + Monte-Carlo + stability. |

### 2A demonstration (real data)

```
$ python scripts/fetch_history.py BTCUSDT ETHUSDT --tf M15 --days 30 --provider kraken
  BTCUSDT: 720 bars, blocks_trading=false, issues=[]   coverage 2026-08-21 .. 2026-08-28
  ETHUSDT: 720 bars, blocks_trading=false, issues=[]
$ python scripts/fetch_history.py BTCUSDT --tf H1 --days 60 --provider bybit_public
  BTCUSDT: 999 bars, blocks_trading=false, issues=[]   coverage 2026-07-17 .. 2026-08-28

$ python scripts/run_backtest.py BTCUSDT --tf H1 --days 41         # real Bybit BTC H1
  bars_processed 983 · trades 1 · cost_drag_r 0.0178 · MFE 1.88R / MAE -0.62R
  monte_carlo runs=1000 · ruin_probability 0.0
```

(The reference MA strategy is a fixture — its P&L is not meaningful. What matters: fees/funding/
slippage/partial-fills/liquidation/MFE-MAE/walk-forward/Monte-Carlo/reproducibility all work on
real bars.)

### 2A exit-gate

| Criterion | Status | Evidence |
|-----------|--------|----------|
| Real historical data BTC/ETH | ✅ | `fetch_history.py` pulled real Kraken + Bybit bars; quality checks pass |
| Backtest with fees/spread/slippage/funding/partial fills/margin/liquidation | ✅ | `execution/simulation.py` + `test_execution.py` (16 tests) |
| Trade ledger, metrics, MFE/MAE | ✅ | `journal/ledger.py`, `research/metrics.py`, integration test |
| Walk-forward, OOS, purge/embargo, Monte-Carlo | ✅ | `research/validation.py`, `research/robustness.py` + tests |
| Anti-overfitting axes (regime/time/symbol stability) | ✅ | `research/validation.py`; `anti-overfitting.md` §4a |
| Reproducibility | ✅ | `test_reproducible_output_hash`: same `RunManifest` → identical `output_hash` |
| No look-ahead | ✅ | `test_look_ahead_immunity`: corrupting post-cutoff bars does not change decisions |
| No survivorship | ✅ | `Instrument.is_tradeable_at` (Phase 1) + point-in-time repo reads |
| Deterministic event-bus (backtest = future live) | ✅ | `test_runtime_bus.py`, `BacktestDriver` uses `SimClock` |
| Provider adapters tested without network | ✅ | `respx` fixtures — `test_providers_rest.py`, `test_net.py` |

---

## 3. Phase 2B — Live Data / Paper-Live (what was built)

| Module | Purpose |
|--------|---------|
| `data/aggregator.py` | `BarAggregator` — trades/ticks → timeframe-aligned OHLCV; emits only confirmed bars; `poll()` finalizes a stale bar; `forming` bar kept internal. |
| `data/providers/exchange_ws.py` | `KrakenWSSource` (`wss://ws.kraken.com/v2`) + `BybitWSSource` (`wss://stream.bybit.com/v5/public/linear`). Parse `trade` / `publicTrade` messages → `BarAggregator`. Reconnect with capped exponential backoff. Connection is injectable (tests use a fake). |
| `data/ingestion/sources.py` | `SyntheticLiveSource` — replays a bar list as if live (this environment / offline demo). |
| `data/ingestion/service.py` | `IngestionService` — source → rolling **data-quality check** → publish `BarClosed` (or `DataQualityAlert` + block) → write repository → metrics. `now` = bar close (honest for live and replay). |
| `runtime/drivers/live_driver.py` | Wires a live source into the bus via `IngestionService` (mirror of `BacktestDriver`). |
| `runtime/supervisor.py` | `Supervisor` — owns the loop; **fail-safe start** (kill switch engaged until startup checks pass), heartbeat loop, graceful shutdown, `ShutdownRequested`. Invariant asserted: `orders_sent == 0`. |
| `scanner/scanner.py` | `ScannerShell` — subscribes `BarClosed`, rolling per-instrument history, calls a **placeholder** `evaluate()` (real engine = Phase 3, same hook), publishes `MarketObserved`, tier-labeled metrics. Never produces an order. |
| `ops/metrics.py` | `MetricsRegistry` — counters / gauges / histograms, `snapshot()` for the dashboard. |
| `ops/health.py` | `SystemHealth` — provider + broker health, heartbeat staleness, data-block set, kill-switch state; `ok` / `snapshot()`. |
| `scripts/run_paper_live.py` | PAPER-LIVE daemon. `--synthetic` (offline replay) or `--live kraken|bybit` (real public WS). Prints supervisor status + scanner observations + metrics. Asserts no order was sent. |

### 2B demonstration

```
$ python scripts/run_paper_live.py --synthetic BTCUSDT ETHUSDT --tf M15 --max-bars 1000
  started: true   stopped: true   orders_sent: 0
  bars_ingested: 1000   bars_blocked: 0
  health.ok: true   kill_switch_engaged: false   heartbeat_stale: false
  scanner_observations: 1000
  counters:
    bars_ingested_total{instrument=BTCUSDT,provider=synthetic_live} = 720
    market_observed_total{instrument=BTCUSDT,tier=1}                = 720
    bars_ingested_total{instrument=ETHUSDT,provider=synthetic_live} = 280
    market_observed_total{instrument=ETHUSDT,tier=2}                = 280
```

Pipeline runs: **Provider → Ingestion → Normalization → Data Quality → Event Bus → Scanner (shell)**.
Data-quality veto works (`test_data_quality_veto_blocks_bad_bar`): an out-of-order bar is caught,
a `DataQualityAlert` is published, and the bad bar is **not** emitted as a tradeable `BarClosed`.

### 2B exit-gate

| Criterion | Status | Evidence |
|-----------|--------|----------|
| Real live data (crypto: Kraken + Bybit public) | ✅ code + real REST verified; WS clients implemented & unit-tested (fake conn). Live WS run needs a long-running session. |
| Live pipeline Provider→Ingestion→Normalization→DataQuality→EventBus→Strategy→Risk→Signal→PaperExecution | ✅ pipeline wired; Strategy/Risk/Signal are placeholders until Phase 3; PaperExecution path present but idle |
| PAPER_LIVE uses live data, **never** sends a real-money order | ✅ `Supervisor.orders_sent == 0` asserted; `BrokerRouter` refuses live adapters in sim modes; `PaperBroker.is_live_capable = False` |
| System runs 24/7 and observes the market autonomously | ✅ `run_paper_live.py` daemon; `Supervisor` + heartbeat + graceful shutdown; scanner observes every bar |
| Data-quality veto stops bad bars | ✅ `test_data_quality_veto_blocks_bad_bar` |
| Fail-safe start (kill switch engaged until ready) | ✅ `test_full_paper_live_pipeline` |
| Gold live via Pepperstone/MT5 | ⚠️ deferred — `MetaTrader5` is Windows-only + needs the terminal + a Pepperstone account. Adapter contract defined; 2B live backbone runs on **crypto** (Tier 1: BTC). |

---

## 4. Architecture changes this phase

- New packages/modules: `runtime/{events,bus,supervisor,drivers/backtest_driver,drivers/live_driver}`,
  `net/{client,ratelimit}`, `data/{registry,router,aggregator}`, `data/ingestion/{service,sources}`,
  `data/providers/{kraken,bybit_public,exchange_ws}`, `execution/{simulation,router}`,
  `execution/brokers/{base,paper}`, `journal/ledger`, `research/{metrics,validation,robustness,registry}`,
  `engine/backtest`, `ops/{metrics,health}`, `scanner/scanner`, `strategy/reference`.
- `data/interfaces.py`: added **async** source ABCs (`AsyncOHLCVSource`, `AsyncTradeSource`, …)
  for network providers, distinct from the sync ABCs (mock/csv).
- `config/`: `providers.example.yaml` rewritten (Kraken primary, Bybit secondary, Pepperstone/MT5
  for gold), new `brokers.example.yaml` (BrokerRouter). `risk.example.yaml`: controlled-aggressive
  tiers + `hard_max_risk_pct` + `size_multiplier`. `config.example.yaml` + loader: `paper_live` mode.
- New deps: `httpx`, `websockets` (runtime); `respx`, `pytest-asyncio` (dev). `MetaTrader5` is
  **not** a dependency (lazy import, Windows-only).
- Docs: `ARCHITECTURE.md` (§1, §3a, execution section), `FINAL_IMPLEMENTATION_PLAN.md` (Phase 2 → 2A/2B),
  `TODO.md`, `docs/strategy/sizing.md` (§1/§1a), `docs/strategy/anti-overfitting.md` (§4a).

---

## 5. Open problems / carried to Phase 3+

| # | Item |
|---|------|
| P3-1 | `git init` still pending (env has no `git`). `RunManifest.code_sha` falls back to `nogit`. Needed for full reproducibility + pre-commit hooks (gitleaks). |
| P3-2 | Kraken OHLC REST returns only ~720 recent bars regardless of `since` — for deep history use Bybit (kline paginates) or Dukascopy import. Add pagination/backfill loop for Bybit in Phase 3. |
| P3-3 | `strategy/reference.py` is a fixture. Phase 3 replaces the backtest/scanner strategy callback with the real `strategy.evaluate(MarketContext) -> Decision` (spec `0.1.0`). |
| P3-4 | Live WS run not exercised end-to-end here (needs a long-lived session). WS clients are unit-tested against a fake connection; a smoke run against real Kraken/Bybit WS should be done when a session allows. |
| P3-5 | Gold: `PepperstoneMT5Adapter` needs Windows + MT5 terminal + Pepperstone demo account. Build + test it there; keep it disabled elsewhere. |
| P3-6 | Reference-data (fees, margin tiers, `max_leverage_broker`) is still the Phase-1 seed. Pull real Kraken/Bybit instrument specs in Phase 4 (risk) via `/0/public/AssetPairs`, `/v5/market/instruments-info`. |
| P3-7 | `data/repository/` currently holds real bars fetched during this demo (timestamps track the env clock era). Gitignored. Re-fetch cleanly when starting real research. |
| P3-8 | The event-bus `IngestionService` staleness check uses `now = bar.close_time`; a real 24/7 run should also feed wall-clock staleness (a feed that stops sending must eventually be flagged) — add a periodic `poll` + wall-clock check in Phase 5 monitoring. |

---

## 6. Reproduce locally

```bash
cd ~/AI-Trading-Agent
export PATH="$HOME/.local/bin:$PATH"
uv pip install -e ".[dev]"
./scripts/check.sh                                   # ruff + mypy + 264 tests
python scripts/fetch_history.py BTCUSDT --tf H1 --days 60 --provider bybit_public
python scripts/run_backtest.py  BTCUSDT --tf H1 --days 41
python scripts/run_paper_live.py --synthetic BTCUSDT ETHUSDT --tf M15 --max-bars 1000
# real live WS (long-running):
python scripts/run_paper_live.py --live kraken BTCUSDT ETHUSDT --tf M1
```
