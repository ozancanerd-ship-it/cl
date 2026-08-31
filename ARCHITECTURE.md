# Architektur

Struktur, Datenflüsse und Verträge zwischen den Komponenten des AI Trading Agent.

> **Phase:** Development / Research / Paper / Demo. **Kein Live-Trading. Keine echten API-Keys.
> Keine Echtgeld-Orders.** Broker-/Exchange-Adapter existieren zunächst nur als abstrakte
> Schnittstellen (ABCs).
>
> **Strategie-Spezifikation:** vollständig in `docs/strategy/` — eingefroren als
> `strategy_version 0.1.0` (`docs/strategy/DECISIONS-0.1.0.md`). Diese Architektur setzt diese
> Spezifikation um.
>
> **Architektur-Audit (2026-08-28):** `docs/FINAL_ARCHITECTURE_AUDIT.md` — Umstellung auf einen
> **ereignisgetriebenen Kern** (`runtime/`: Event-Bus + Supervisor + austauschbare Driver),
> Multi-Provider-Datenstrategie (`data/registry` + `data/router` + `net/` + `data/ingestion`),
> strikte Trennung `portfolio/` ↔ `investment/` ↔ Trading, `viz/`→`chart/`, `allocation/`→`investment/`,
> zwei AI-Flächen (`ai/reasoning` + `ai/advisor`), vereinheitlichter `execution/simulation` für
> Backtest=Paper=Live. Details + Datenquellen-Empfehlungen dort.

---

## 1. Leitplanken

1. **Eine Strategy Engine für alles.** Backtest, Paper und (später) Live nutzen **denselben**
   Code für Analyse → Setup-Erkennung → Confluence/Veto → Scoring → Risk → Sizing. Unterschiede
   nur bei: Datenquelle, Zeitquelle (`SimClock` vs `SystemClock`), Ausführungs-Adapter.
2. **Brokerunabhängigkeit.** `refdata/`, `data/`, `analysis/`, `strategy/`, `risk/` kennen keinen
   konkreten Broker. Austausch über neutrale Typen (`core/types.py`).
3. **Determinismus & Testbarkeit.** Jede Engine = reine Funktion / Klasse mit klaren Ein-/Ausgaben.
   Zeit **immer** aus `core/clock.py`. Nur `confirmed` (geschlossene) Bars (Look-ahead-Schutz).
4. **Fail-safe.** Unvollständige/unsichere Daten, unklares Regime, Widerspruch ⇒ **kein Setup**.
   Beim Start: Zustand = *gestoppt*, bis Recovery + Reconciliation abgeschlossen.
5. **Risk Engine hat das letzte Wort.** Sie kann jede Order ablehnen (Veto V1–V10). **Nichts**
   überstimmt ein Veto — kein Score, kein Hebel, keine LLM-Ausgabe.
6. **Risiko → Größe → Hebel.** Sizing bestimmt zuerst das erlaubte Risiko in Kontowährung, dann
   die Positionsgröße, dann den nötigen Hebel. Hebel erhöht nie das erlaubte Verlustrisiko.
7. **Nachvollziehbarkeit.** Jede Entscheidung (auch `NO_TRADE`) ⇒ Decision Ledger; jede Order /
   jedes sicherheitsrelevante Ereignis ⇒ append-only Audit Log; jede Version (Code-SHA,
   `strategy_version`, `config_hash`, `dataset_version`) mitgeschrieben.
8. **LLM/AI ist beratend, nie entscheidend.** Siehe §9.
9. **Modular bauen:** bauen → testen → Fehler beheben → testen → nächste Komponente.
10. **Python ist der Kern.** Data/Research/Backtest/Strategy/Analysis/SMC/Scanner/Signal/Risk/
    Sizing/Leverage/Portfolio/AI/Paper/Orchestrierung/Broker-Routing = Python. Fremde Plattformen
    nur als **kleiner Adapter** (MT5 via `MetaTrader5`-Python; **MQL5 enthält nie Strategie-/Risk-/
    AI-Logik**).
11. **Sprache:** Code, Klassen, Variablen, technische Doku und Logs auf **Englisch**. Die
    Planungs-/Strategiedokumente (`docs/`) und die Kommunikation mit dem Nutzer bleiben Deutsch.
12. **Broker-Isolation:** `Strategy → Risk → BrokerRouter → BrokerAdapter`. Die Strategy Engine
    kennt keinen Broker.

---

## 2. Paket-Übersicht

```
src/trading_agent/
├── core/           Domänen-Typen, Enums, Events, Clock, Version, MarketContext
├── config/         YAML laden + Schema-Validierung + Versionierung
├── security/       Secrets (Env→Keychain→Vault), Demo/Live-Trennung, nie loggen
├── refdata/        Instrument-Master, Fees, Trading-Calendar, Symbol-Mapping, Corporate Actions, sync/
├── net/            gemeinsamer Client-Layer: Rate-Limit, Retry/Backoff, Circuit Breaker, Signing, Zeit-Sync
├── data/           Market Data:
│   ├── interfaces  Provider-ABCs (Historical/Live OHLCV/Quote/Trade/Orderbook/Funding/OI/News/Macro)
│   ├── registry    ProviderRegistry — Fähigkeitsmatrix (was, wo, LIVE/HIST, Tiefe, Kosten, Lizenz, Latenz)
│   ├── router      ProviderRouter — beste Quelle je (Instrument, DataKind, Modus) + Fallback-Kette
│   ├── ingestion/  Live Ingestion Service (WS, Reconnect+Backfill, Trades→Bars, Gap-Erkennung, Publish)
│   ├── aggregator  Trades/Ticks → OHLCV, laufende Bar
│   ├── quality     Data-Quality-Monitor → DataQualityStatus.blocks_trading
│   ├── resample    M1→…→D1, look-ahead-frei
│   ├── repository  Parquet (Zeitreihen) + SQLite (Meta/News/Macro), Point-in-Time `as_of`
│   └── providers/  mock · csv · bybit_public (Ph.2) · oanda · polygon · dukascopy-import · fred · finnhub
├── analysis/       market_structure · liquidity · smc · support_resistance · sessions · regime · mtf · news · macro
├── strategy/       primitives · setup_detection · confluence · confidence · scoring
│                   → EINE Strategy Engine: strategy.evaluate(MarketContext) -> Decision
├── risk/           veto (V1–V10 + Emergency) · limits · margin (MarginModel je Broker) · position_sizing (Risiko→Größe→Hebel)
├── portfolio/      Accounting + Risiko-State: Exposure (Notional+1R), Korrelation, Cluster, Drawdown, Heat,
│                   Faktor-Exposure, simulate_add() — von Trading UND Investment gelesen
├── investment/     Investment Engine (Langfrist): INVEST/WAIT/HOLD/REDUCE, Monatsbudget ~200–400€ — KEINE Trade-Signale
├── scanner/        Autonomer Multi-Asset-Scanner · tracker (WATCH→…→confirmed, persistiert) · signal_engine · alerting
├── execution/      simulation (Fill/Cost/Margin/Liquidation — Backtest UND Paper) · oms (Lifecycle-State-Machine) ·
│                   trade_management (TP/BE/Trail/Invalidierung) · reconciliation · brokers/ (MarketDataAdapter vs BrokerAdapter)
├── engine/         backtest · paper_trading · parity   (Ausführungs-Sim + Kennzahlen; nutzt runtime/)
├── research/       dataset (Point-in-Time) · validation (IS/OOS/Walk-Forward/Purge-Embargo) · robustness (Monte-Carlo) · registry (RunManifest)
├── runtime/        bus (interner async Event-Bus) · supervisor (Prozess, Lifecycle, Heartbeat, Shutdown) ·
│                   scheduler (Scan-Orchestrierung, Tier-Priorität) · drivers/ (BacktestDriver | LiveDriver)
├── chart/          Chart-Annotation-Payloads (Marker/PriceLine/Zone/TrendLine/Point/SessionBand/Label) — kein Rendering
├── api/            FastAPI-Backend fürs Dashboard (Phase 12): REST + WS, Chart-Datafeed, Approval-Endpoint
├── journal/        decision_ledger (Ereignis-Trace DATA→…→EXIT) · trading_journal · performance
├── ai/             reasoning (nur Text, gated nichts) · advisor (opt. Score-Modulator, Default AUS) · contract (Schema+Guardrails)
├── state/          Store (SQLite) + Recovery: Positionen, Orders, Kill-Switch je Ebene, Verlustzähler, Equity-Hoch,
│                   last_processed_bar_close, Scanner-Tracker-State, Signale-in-Approval — fail-safe Start
├── ops/            metrics (In-Process-Registry) · health (SystemHealth-Aggregat) · watchdog · notify · runbooks
├── safety/         kill_switch (hierarchisch, persistiert) · audit_log (Hash-Chain) · error_handling
└── utils/          logging (JSON, Redaction) · tracing (trace_id via contextvars) · Helfer
```

**Umbenannt/verschoben durch den Audit:** `allocation/`→`investment/`, `viz/`→`chart/`,
`execution/portfolio.py`→`portfolio/`. **Neu:** `runtime/`, `net/`, `security/`, `api/`,
`data/{registry,router,aggregator,ingestion/}`, `execution/{simulation,oms}`,
`ops/{metrics,health,watchdog}`, `utils/tracing`, `ai/{reasoning,advisor}`.

---

## 3. Ereignisgetriebener Kern (Audit C-01)

**Kein linearer „Pipeline-pro-Bar"-Ablauf.** Ein langlebiger Prozess (`runtime/supervisor`)
besitzt einen internen async **Event-Bus** (`runtime/bus`). Komponenten sind **Subscriber**.
Der einzige Unterschied zwischen Backtest und Live ist der **Driver**, der Events *produziert*:

| | Producer (Driver) | Zeitquelle | Datenquelle |
|---|---|---|---|
| **Backtest** | `BacktestDriver` — spielt historische Bars/News aus dem Repository als Events ab | `SimClock` | `data/repository` (Point-in-Time) |
| **Paper** | `LiveDriver` — leitet Stream-Events von `data/ingestion` weiter, Fills simuliert | `SystemClock` | Live-/Delayed-Feeds + `execution/simulation` |
| **Live** | `LiveDriver` — wie Paper, Fills vom echten `BrokerAdapter` | `SystemClock` | Live-Feeds + Broker |

**Dieselben Subscriber** (`analysis` → `strategy.evaluate` → `risk` → `execution`) laufen in allen
drei Modi → strukturell erzwungene Parität.

```
       ┌──────────── data/ingestion  (WS, Reconnect+Backfill, Trades→Bars) ────────────┐
       │             data/router → beste Quelle je (Instrument, DataKind, Modus)       │
       │             data/registry (Fähigkeits-/Lizenz-/Kosten-Matrix)  · net/ (Client)│
       └──────────────────────────────┬───────────────────────────────────────────────┘
                                      │ publish: BarClosed · QuoteUpdate · TradeTick ·
                                      │          FundingUpdate · OIUpdate · NewsEvent
                                      ▼
   ┌───────────────────────────  runtime/bus  (interner async Event-Bus)  ───────────────────────────┐
   │                                                                                                │
   ▼                          ▼                         ▼                        ▼                   ▼
 data/quality            analysis/ (bei BarClosed)   scanner/scheduler       ops/metrics         chart/ (bei
 → DataQualityStatus     → MarketContext             → fan-out je Instrument  ops/health          SetupStateChange)
   .blocks_trading                                     nach Tier-Priorität                        → Annotationen
                                │                             │
                                ▼                             ▼
                    strategy.evaluate(MarketContext)   scanner/tracker  (WATCH→developing→armed→
                    → Decision: SetupCandidate           confirmed→expired, persistiert in state/)
                      ODER NO_TRADE + NoTradeReason[]           │  bei "confirmed":
                                │                               ▼
                                ▼                        scanner/signal_engine → SignalReport
                    risk/veto (V1–V10 + Emergency-Vetos)         │  (Entry/SL/TP1-3/RR/Risiko%/€/Größe/
                    → bei Veto: NO_TRADE (Score NICHT berechnet) │   Margin/Hebel/Liq-Abstand/Score/
                                │  sonst:                        │   Confidence/Begründung/Portfolio-Impact/
                                ▼                                │   Gewinn@TP·Verlust@SL)
                    risk/position_sizing (Risiko→Größe→Hebel)    │
                    portfolio/.simulate_add() (Pre-Trade-Check)  ▼
                                │                        ops/notify → Nutzer-Alert
                                ▼                                │
                    OrderIntent {client_order_id, tif, type}     ▼
                                │                        api/ Approval-Endpoint  ("BUY bestätigen?")
                                ▼                                │  (nur Demo/Live; Backtest/Paper auto)
                    execution/oms (Lifecycle-State-Machine)  ◄───┘
                    → execution/simulation  ODER  BrokerAdapter
                                │
                                ▼  Fill-Events
              ┌─────────────────┼───────────────────────────┬──────────────────────┐
              ▼                 ▼                           ▼                      ▼
    execution/trade_management  portfolio/ (State-Update)  execution/reconciliation  journal/decision_ledger
    (TP/BE/Trail/Invalidierung)                            (Broker↔intern, Demo/Live)  (Ereignis-Trace + trace_id)
```

**Querschnitt:**
- `research/` nutzt `data/repository` (Point-in-Time) + den BacktestDriver für Validation/Robustness/Registry.
- `investment/` liest `portfolio/` + `analysis/regime` + Langfrist-Signale → `INVEST/WAIT/HOLD/REDUCE`.
  **Getrennter Pfad, erzeugt keine Trade-Signale.**
- `safety/kill_switch` (hierarchisch, persistiert) + `ops/watchdog` können den Bus jederzeit anhalten.
- `state/` persistiert alles Wiederherstellungsrelevante; fail-safe Start (Kill-Switch engaged bis Reconcile ok).

**Bar-Close-Gate:** Analyse/Strategie reagieren nur auf `BarClosed` (`is_final`). Entscheidungen für
`t+1` nutzen ausschließlich Daten mit `close_time ≤ t` (`information_cutoff`).

## 3a. Multi-Provider-Datenstrategie & Broker (verbindliche Nutzer-Entscheidungen 2026-08-28)

Für jede Assetklasse die beste verfügbare Quelle, ausgewählt vom `data/router` nach Datenqualität,
Latenz, historischer Tiefe, Kosten, API-Zuverlässigkeit, Lizenz, Rate-Limits.

| Assetklasse | Live-Daten | Backtest-Historie | Ausführung (später) | News/Makro |
|---|---|---|---|---|
| **Crypto** (BTC, Alts, Perps) | **Kraken Pro** (primär) + **Bybit v5 public** (sekundär/Fallback/Zusatzdaten) | Kraken + Bybit | **Kraken** (primär), **Bybit** (alternativ/Fallback) | Kraken/Bybit Announcements, CryptoPanic |
| **Gold / XAUUSD** | **Pepperstone via MT5** (`MetaTrader5` Python-Integration) | MT5-History-Export / Dukascopy | **Pepperstone/MT5** | FRED/ALFRED (DXY, US-Realzinsen — Point-in-Time) |
| **US-Aktien / ETFs** | professioneller Equity-Provider (**Polygon.io** empfohlen) — ab Phase 7 | Polygon | — (Anlage über Trade Republic, s. u.) | Finnhub (Earnings, Company News) |
| **Investments** (Aktien/ETF, Langfrist) | — | — | **Trade Republic** — nur offizielle/zulässige Wege; **manueller Import**, keine inoffizielle API | — |
| **Wirtschaftskalender** | **Finnhub** (Free) bzw. Trading Economics (paid) | — | — | — |
| **Makro-Zeitreihen (PIT)** | **FRED / ALFRED** (First-Release-Werte) | — | — | — |

Start: überall Free/Demo/Public. `ProviderRegistry` führt ein `redistribution_allowed`-Flag; die
Chart-/Datafeed-API gibt rohe Ticks aus weiterverteilungs-beschränkten Quellen nur an den
Eigentümer-Kontext.

### Broker Router (verbindlich)

**Die Strategy Engine spricht NIE direkt einen Broker an.** Der einzige Pfad ist:

```
Strategy  →  Risk  →  BrokerRouter  →  BrokerAdapter  →  Broker/Plattform
```

| Instrument / Zweck | Route |
|---|---|
| `XAUUSD` | `PepperstoneMT5Adapter` → MT5 → Pepperstone |
| `BTC` / Crypto (primär) | `KrakenAdapter` → Kraken Pro |
| Crypto (alternativ / Fallback) | `BybitAdapter` → Bybit |
| Investments (Aktien/ETF) | `TradeRepublicAdapter` (nur zulässige/offizielle Funktion; sonst manuell) |

- `BrokerRouter` wählt den Adapter je `Instrument`/`route`-Config, prüft `BrokerHealth`, macht
  Fallback (Crypto: Kraken → Bybit). Er reicht `OrderIntent` durch — **erst nachdem die Risk
  Engine freigegeben hat**.
- **MT5/MQL5:** Nur die offizielle `MetaTrader5`-**Python**-Integration. **MQL5 enthält NIEMALS
  Strategie-, Risk- oder AI-Logik** — nur, falls eine konkrete MT5-Funktion es zwingend erfordert,
  ein minimaler Expert-Advisor-Adapter. Python bleibt das Gehirn.
- `MetaTrader5` ist **Windows-only** und braucht das MT5-Terminal + Pepperstone-Konto → der
  Adapter importiert es **lazy**; auf Nicht-Windows-Systemen ist Gold-Live/-Execution deaktiviert
  (Backtest/Research laufen mit importierter/Dukascopy-Historie).

**TradingView** ist **nur Frontend-Visualisierung**, gespeist von unserer `ChartDataAPI` +
`ChartAnnotationsAPI`. Start mit **Lightweight Charts** (Apache-2.0, ungated), später Umstieg auf die
TradingView Charting Library möglich — gleiche Backend-API. **Keine Browser-/Click-Automation,
kein Pine-Script als Datenquelle.**

---

## 4. Zentrale Datentypen (`core/types.py` — Zielbild)

### Markt & Analyse
| Typ | Zweck |
|-----|-------|
| `Instrument`, `Timeframe`, `Candle`, `Series` | Basisdaten (Timeframe-Enum: D1,H4,H1,M30,M15,M5,M1) |
| `SwingPoint`, `StructureBreak` (BOS/CHoCH) | `primitives.md` §1–§3 |
| `LiquidityLevel`, `EqualLevelCluster`, `LiquiditySweep` | `primitives.md` §4–§6 |
| `Displacement`, `FVG`, `IFVG`, `OrderBlock`, `Breaker`, `PremiumDiscount` | `primitives.md` §7–§13 |
| `SessionWindow` | Fenster in Börsenlokalzeit → UTC (DST-korrekt) |
| `RegimeState` | `{directional, volatility, phase, scores}` je Timeframe (`regime.md`) |
| `NewsEvent` | Typ, Impact, `scheduled_time`, `available_at` (Point-in-Time), `affected_instruments` |
| `MacroContext` | DXY/Yields/Fed-Erwartungen; Crypto: Dominanz/Funding/OI/Liquidations |
| `MarketContext` | Aggregat + `data_confidence`, `analysis_confidence`, `regime` |

### Entscheidung
| Typ | Felder (Auszug) |
|-----|-----------------|
| `SetupCandidate` | `setup_id`, `strategy_version`, `direction`, `instrument`, `timeframe_set`, `chain_state`, `entry_zone {low,high,mode}`, `entry_price`, `initial_sl`, `structural_invalidation_price`, `tp1/tp2/tp3 {price, size_pct}`, `rr_to_tp2`, `blended_rr`, `armed_expiry`, `max_holding`, `causal_chain[]` (Kettenglied-Nachweise) |
| `VetoResult` | `vetoes: [VetoId]`, `passed: bool` (nicht leer ⇒ kein Score) |
| `ConfidenceRecord` | `data_confidence`, `analysis_confidence`, `setup_confidence`, `limiting_factor` |
| `SetupScore` | `factors{name:{value,weight,contribution}}`, `penalties`, `final_score`, `tier` (A+/A/B/NO_TRADE) |
| `RiskDecision` | `approve|reject`, `reasons: [NoTradeReason]`, `SizingRecord` |
| `SizingRecord` | `risk_budget_ccy`, `available_ccy`, `limiting_constraint`, `eff_sl_distance`, `final_size`, `realized_risk_ccy`, `leverage_used`, `required_margin`, `liquidation_price`, `liq_buffer_atr`, `est_funding_ccy`, `cluster_id` |
| `OrderIntent` | brokerunabhängig: `client_order_id`, `intent_hash`, `time_in_force`, `order_type`, `side`, `size`, `limit_price`, `sl_price`, `tp_prices[]` |
| `SignalReport` | siehe §7 (Scanner-Ausgabe) |

### Portfolio & Betrieb
| Typ | Zweck |
|-----|-------|
| `Position` | Entry, Ist-Größe (nach Fills), SL, TP, P/L, `cluster_id`, `liquidation_price`, Status |
| `PortfolioState` | offene Positionen, Exposure (Notional **und** Risiko), Faktor-Exposure, Korrelationsmatrix, Portfolio-Heat, Cluster |
| `AllocationPlan` | `per_bucket_target_weight`, `per_bucket_deposit_eur`, `recommendation: INVEST|WAIT|HOLD|REDUCE|DO_NOT_ADD`, `rationale` |
| `DecisionRecord` | vollständiger Decision-Ledger-Eintrag (Kontext-Snapshot + Version) |
| `TradeRecord` | `backtest-labeling.md` §2 (Pflichtfelder) |
| `KillSwitchState` | Ebene (`global/broker/asset/strategy/data`), aktiv?, Grund, gesetzt-von, persistiert |
| `RunManifest` | `code_sha`, `config_hash`, `dataset_version+hash`, `strategy_version`, `seed`, Zeitraum |

---

## 5. Komponenten im Detail

### refdata/ — Reference Data
- `instruments.py`: Tick-Size, Lot-Size, `min_notional`, `contract_multiplier`, `maintenance_margin`,
  `margin_tiers`, `max_leverage_broker`, `max_position_broker`, Quote/Settle-Währung, Fee-Schedule.
- `calendar.py`: Handelstage, Feiertage, Half-Days, Session-Zeiten je Instrument (Börsenlokalzeit,
  DST-korrekt); 24/7 für Crypto; Forex-Wochenendlücke.
- `symbols.py`: kanonisches Symbol ↔ broker-spezifisch (`BTCUSDT` ↔ `BTC-USD` ↔ `XBTUSD`).
- `corporate_actions.py`: Splits/Dividenden/Symboländerungen + Backadjustment (später, für Aktien).

### data/ — Market Data Engine
- `interfaces.py`: `MarketDataProvider` (ABC) — `get_candles`, `subscribe`.
- `providers/`: **Mock**, **CSV** (Phase 1); echte Provider erst Phase 8/9.
- `quality.py`: Data-Quality- & Integrity-Monitor — Stale/Duplikat/Timestamp/OHLC-Konsistenz/
  Spike; setzt `MarketContext.data_confidence`; triggert Daten-Kill-Switch.
- `resample.py`: M1 → M5…D1, look-ahead-frei.
- `repository.py`: Persistenz (Parquet für Candles, SQLite für Events/Funding), **Point-in-Time**-
  Abfragen (`as_of`).
- `health.py`: Data-Source-Health-Registry (letzter Fetch, Fehlerquote, Latenz, Primär→Sekundär).

### analysis/ — Analyse-Engines
- `market_structure.py`, `liquidity.py`, `smc.py`, `support_resistance.py`, `sessions.py` —
  implementieren die Detektoren aus `primitives.md` (Definitionen dort sind verbindlich).
- `regime.py` (**neu, MVP-Pflicht**): Directional/Volatility/Phase, MTF-Konsens **D1+H4**,
  Hysterese, `NO_TRADE`-Ausgänge (`regime.md`).
- `mtf.py`: Timeframe-Hierarchie, HTF-Bias-Propagation.
- `news.py`: Kalender (statische Fixture/CSV, Point-in-Time), Impact-Klassifikation,
  Event→Instrument-Routing, Blackout-Fenster (`news-rules.md`).
- `macro.py`: Makro-Regime; Gold: DXY/Yields/Fed; Crypto: Dominanz/Funding/OI/Liquidations.

### strategy/ — die geteilte Strategy Engine
- `primitives.py`: reine Detektor-Funktionen (Input `Series` → Primitive-Objekte).
- `setup_detection.py`: die **kausale Kette** von `SMC-SWEEP-REV-01` als State Machine
  (`SCANNING → … → ARMED → TRIGGERED`); erzeugt `SetupCandidate`.
- `confluence.py`: Confluence-Gate (Mindestglieder der Kette; **kein Einzelindikator-Trade**).
- `veto.py`: `collect_vetoes()` (V1–V10) — läuft **vor** dem Scoring; nicht leer ⇒ `NO_TRADE`.
- `confidence.py`: `data_confidence` + `analysis_confidence` → `setup_confidence` (`confidence.md`).
- `scoring.py`: gewichteter 0–100-Score (Start **gleichgewichtet**, Penalties 0) → Risikostufe
  (`scoring-rubric.md`).
- **Contract:** `evaluate(MarketContext) -> Decision` — `Decision` ist entweder
  `SetupCandidate + tier` oder `NO_TRADE + reasons[]`. Diese Funktion ist für Backtest, Paper
  und Live **identisch**.

### risk/ — Risikokontrolle & Sizing
- `risk_engine.py`: harte Prüfungen, **Vetorecht** (`RiskDecision`). Gründe: alle aus
  `no-trade.md` [6]/[8] + `contradictions.md`.
- `position_sizing.py`: 12-Schritt-Algorithmus (`sizing.md` §2) — **Risiko → Größe → Hebel**,
  Hebel **dynamisch** (keine statischen Caps), Vol-/Liquiditäts-/Margin-/Funding-Caps.
- `margin.py`: Liquidationspreis-Schätzung, Maintenance-Margin-Tiers, Mindest-Liquidationsabstand.
- `limits.py`: Tag/Woche/Drawdown/Trades/offene Positionen/Portfolio-Exposure/korrelierte
  Exposure/Portfolio-Heat; Verbote (kein Martingale / kein Averaging-down / keine Risikoerhöhung
  nach Verlusten) — **im Code erzwungen, mit Tests**.

### execution/ — Ausführung (simuliert, später real)
- `simulation.py`: `CostModel` (maker/taker Fees, Spread, Slippage, Funding-Accrual),
  `FillModel` (limit/market/stop, Partial Fills, Latenz), `MarginModel` + `LiquidationModel`
  (Kraken/Bybit Linear-Perp, isolated). **Von Backtest UND Paper identisch genutzt.**
- `trade_management.py`: TP1/2/3, Teilgrößen, Break-even nach TP1, Trailing (nach TP2),
  struktur-basierter Stop, Klasse-A/B/C/D-Invalidierung (`invalidation.md`), Bar-Auswertungsreihenfolge.
- `oms.py`: `OrderIntent` → Order-Lifecycle-State-Machine
  (`NEW→ACK→PARTIAL→FILLED/REJECTED/CANCELLED/EXPIRED`), Idempotenz (`client_order_id`),
  Duplicate-Order-Schutz, Order-Historie.
- `router.py`: **`BrokerRouter`** — wählt den `BrokerAdapter` je Instrument/Route-Config, prüft
  `BrokerHealth`, Fallback (Crypto: Kraken → Bybit). **Einziger Weg von der Logik zum Broker.**
  Nimmt `OrderIntent` **erst nach Risk-Freigabe** an.
- `reconciliation.py` (Demo/Live): periodischer Abgleich Broker-State ↔ interner State;
  Drift-Alarm; Recovery.
- `brokers/base.py`: `MarketDataAdapter` (lesen) und `BrokerAdapter` (handeln) getrennt.
- `brokers/paper.py`: `PaperBroker` — nimmt `OrderIntent`, füllt über `execution/simulation.py`,
  emittiert `Fill`-Events. **PAPER_LIVE: echte Live-Daten, aber NIE Echtgeld-Orders.**
- `brokers/kraken.py`, `brokers/bybit.py`, `brokers/pepperstone_mt5.py`, `brokers/trade_republic.py`:
  echte Adapter — **erst ab Phase 9** (Keys via `security/secrets`, Testnet/Demo zuerst). MT5-Adapter
  importiert `MetaTrader5` lazy (Windows-only).

### engine/ — Orchestrierung & Simulation
- `pipeline.py`: verdrahtet §3 je `confirmed` Bar. **Eine** Pipeline für Backtest/Paper/Live.
- `backtest.py`: Event-getrieben, look-ahead-frei; Kennzahlen & Buckets aus
  `backtest-labeling.md` §8; deterministisch über `RunManifest`.
- `paper_trading.py`: dieselbe Pipeline gegen Live-/Delayed-Daten, `SimAdapter`-Fills, kein Broker.
- `parity.py`: Backtest vs. Paper auf demselben Zeitfenster → Signal-für-Signal-Diff (Freigabe-Gate
  Paper→Demo).

### research/ — Methodik (getrennt von engine/backtest)
- `dataset.py`: Point-in-Time-Feature-/Datensatz-Bau; keine Full-Sample-Normalisierung.
- `validation.py`: Train/Validation/**unberührter** Test (chronologisch), Walk-Forward,
  optional Purged/Embargoed CV.
- `robustness.py`: Monte-Carlo (Trade-Bootstrap, Dropout, Kosten-Stress, Start-Jitter),
  Ruin-Wahrscheinlichkeit.
- `registry.py`: Experiment-/Parameter-Registry — jeder Lauf mit `RunManifest`, Ergebnissen,
  `N_configs_tested`.

### runtime/ — ereignisgetriebener Kern (Audit C-01)
- `bus.py`: interner async Event-Bus (in-process pub/sub). Kein externer Broker.
- `supervisor.py`: langlebiger Prozess; besitzt den Event-Loop, verdrahtet Subscriber, führt
  Heartbeat/Watchdog, Graceful Shutdown, fail-safe Start.
- `scheduler.py`: Scan-Orchestrierung — Bar-Close-Events fächern zu Scan-Arbeit auf; Tier 1
  häufiger/mehr Compute; nicht-blockierend.
- `drivers/backtest_driver.py`: spielt historische Bars/News aus dem Repository als Events ab
  (`SimClock`). `drivers/live_driver.py`: leitet reale Stream-Events weiter (`SystemClock`).
  **Gleiche Subscriber** in allen Modi.

### net/ — gemeinsamer Client-Layer (Audit C-05)
Token-Bucket-Rate-Limiter je Provider/Endpoint, Backoff+Jitter, Circuit Breaker,
Request-Signierung (Keys aus `security/secrets`), Server-Zeit-Sync/Drift-Check, redigiertes
Response-Logging. Für **alle** HTTP/WS-Provider und später Broker.

### scanner/ — autonomer Markt-Scanner + Signal Engine
- `scanner.py`: reagiert (über `runtime/scheduler`) auf Bar-Close je Instrument/Timeframe, ruft
  die geteilte `strategy.evaluate()` auf, meldet **nur relevante** Ergebnisse (Setup ab Stufe B
  **oder** bewusst `NO_TRADE`, wenn die Kette mind. `STRUCTURE_SHIFTED` erreichte).
- `tracker.py`: Setup-Lebenszyklus je Instrument `WATCH → developing → armed → confirmed →
  expired`, **persistiert in `state/`** (über Neustarts hinweg). Publiziert `SetupStateChange`
  auf den Bus → `chart/` diff't → Live-Chart-Update.
- `signal_engine.py`: bei `confirmed` — voller `SignalReport` (§7), inkl. Risiko-Zahlen aus
  `risk/` + `portfolio/`.
- `alerting.py`: Übergabe an `ops/notify`.

### portfolio/ — Accounting + Risiko-State (Audit C-03)
Einzige Quelle der Wahrheit für Positionen/Exposure. Alle Positionen quellenübergreifend (Bybit,
manueller Trade-Republic-Import `source=manual`, Cash). Berechnet: Exposure (Notional **und**
1R-Risiko), Korrelation (gemessen + statische Baseline), Cluster/Klumpenrisiko, Allokation je
Assetklasse, Drawdown, Portfolio-Heat, Faktor-Exposure (USD, Zinsen, Equity-Beta, Crypto-Beta,
Gold). `simulate_add(candidate) -> PortfolioState` für den Pre-Trade-Check der Risk Engine.
**Von Trading- UND Investment-Engine gelesen, von keiner nachgerechnet.**

### investment/ — Investment Engine, Langfrist (Audit C-03, PLATZHALTER bis Phase 7)
Input: Monatsbudget (~200–400 EUR), `portfolio/`-State, Regime, Langfrist-Chancen, Limits.
Output: `InvestmentPlan` mit `recommendation ∈ {INVEST, WAIT, HOLD, REDUCE, DO_NOT_ADD}` +
`rationale`. **Berücksichtigt bestehende Positionen, empfiehlt nicht blind neue Assets.**
**Erzeugt NIE Trade-Signale.** Strikt getrennt vom Scanner/Trading-Pfad. Bis Phase 7: Budget
manuell zugeteilt; Interface-Stub steht.

### chart/ — Chart-Annotation-Payloads (Audit C-02, Phase 6)
Python **rendert nichts.** Erzeugt strukturierte Annotationen aus `SignalReport` + `MarketContext`:
`Marker` (BUY/SELL), `PriceLine` (Entry/SL/TP1-3), `Zone/Box` (FVG, IFVG, Order Block, Breaker,
Premium/Discount, Setup-Zone), `TrendLine` (BOS/CHoCH), `Point` (Swing H/L, Equal H/L),
`SessionBand`, `Label` (Setup-ID, Strategy-Version). Geliefert über `api/` als
`ChartAnnotationsAPI` + WS-Update-Stream (`annotation_added/updated/removed`), damit der Chart
**live** mitläuft, wenn sich der Setup-State ändert. Frontend: **Lightweight Charts** zuerst,
TradingView Charting Library später — gleiche Backend-API.

### api/ — Dashboard-Backend (Audit C-08, Phase 12)
FastAPI: REST + WebSocket. Panels: Live-Scanner, Chart, BUY/SELL-Signale, Portfolio, Risk, offene
Positionen, Performance, Trade-Journal, News, AI-Reasoning, Alerts, System-Health,
Execution-Status. **Approval-Endpoint** („BUY bestätigen?") — nur Demo/Live; Backtest/Paper
laufen ohne manuelle Freigabe. Frontend ist ein separates Projekt (React/Vue), nicht Python.

### security/ — Secrets & Policy (Audit M-32)
`secrets.py`: Abstraktion Env → OS-Keychain (`keyring`) → später Vault. Demo/Live-Credentials in
**getrennten Namespaces**. Loggt niemals Secrets. Startup-Check „kein Secret in geladener Config".
Policy in `docs/SECURITY.md` (trade-only, **nie** Withdrawal, IP-Allowlist, Incident-Response).

### ai/ — zwei getrennte Flächen (Audit C-04)
- `reasoning.py`: LLM **nur für Text** — Zusammenfassungen (News, Research), Begründungstexte
  für Signale, Interpretation unstrukturierter Infos. Output geht ins Ledger/Dashboard,
  **beeinflusst keine Entscheidung**.
- `advisor.py`: **optionaler**, eng begrenzter Score-Modulator. Läuft **strikt nach** No-Trade-
  Check + Regime-Gate + Ketten-Gates + Vetos. Darf den Score nur um
  ≤ `ai_guardrails.max_score_modulation_points` bewegen, **nie** ein Gate/Veto aufheben, **nie**
  eine Stufe nach oben erzwingen. **Default: AUS.**
- `contract.py`: JSON-Schema (Pydantic) je LLM-Call, Prompt-Versionierung, Modell-ID-Pinning
  (`temperature=0`), jede Anfrage/Antwort ins Audit-Log + Ledger. Timeout/Schemafehler → neutral,
  System läuft regelbasiert weiter. Siehe §9.

### state/ — Persistenz & Recovery
- `store.py` (ABC) + SQLite-Impl: offene Positionen, Orders, `KillSwitchState`, Tages-/Wochen-
  Verlustzähler, Equity-Hoch, `strategy_version`.
- `recovery.py`: `recover()` beim Start (fail-safe = gestoppt), `graceful_shutdown()`.

### ops/ & safety/
- `ops/notify.py`: Kanal-Abstraktion (Konsole/Datei jetzt; Telegram Bot API / Webhook / Web-Push
  später — offizielle APIs), Severity, Dedup, Rate-Limit, Eskalation.
- `ops/metrics.py`: In-Process-Metrics-Registry (Counter/Gauge/Histogram), snapshot-bar fürs
  Dashboard (`bars_ingested_total`, `provider_latency_ms`, `scan_duration_seconds`,
  `signals_emitted_total`, `no_trade_reasons_total`, `open_positions`, `portfolio_heat_pct`, `equity`).
- `ops/health.py`: `SystemHealth`-Aggregat (Provider- + Broker-Health + Data-Quality +
  Kill-Switch-State + letzter Heartbeat). Speist Dashboard **und** den Broker-/Data-Health-Veto.
- `ops/watchdog.py`: Heartbeat; stockt der Event-Loop → Kill-Switch (global).
- `ops/runbooks/`: „Datenquelle tot", „Broker-Disconnect", „State-Drift", „Kill-Switch ausgelöst".
- `safety/kill_switch.py`: **hierarchisch** (`global/broker/asset/strategy/data`), persistiert,
  fail-safe beim Start, manuell überschreibbar; getesteter `emergency_flatten()`-Pfad (Demo/Live).
- `safety/audit_log.py`: append-only, integritätsgesichert (Hash-Chain), UTC.
- `safety/error_handling.py`: Fehlerklassen, Retry/Backoff+Jitter, Circuit Breaker, Rate-Limiter,
  Degradations-Modus.

### core/ & config/ & utils/
- `core/clock.py` (SystemClock/SimClock), `core/version.py` (Git-SHA, `strategy_version`,
  `config_hash`), `core/events.py`, `core/enums.py` (inkl. `NoTradeReason`, `VetoId`,
  `RegimeState`-Enums, `ExitReason`).
- `config/loader.py`: Pydantic-v2-Schema, `schema_version` in jeder YAML, Fail-fast bei
  ungültiger Config, `config_hash`.
- `utils/logging.py`: JSON-Logging, **Secret-Redaction**, Korrelation-IDs.

---

## 6. Konfiguration

| Datei | Inhalt |
|-------|--------|
| `config/config.example.yaml` | Modus, Instrumente, Timeframes, Datenquelle, Engine-Schalter, aktives Setup, Pfade |
| `config/primitives.example.yaml` | alle `primitives.*`-Parameter (`primitives.md`) |
| `config/regime.example.yaml` | alle `regime.*`-Parameter (`regime.md`) |
| `config/strategy.example.yaml` | `setups.SMC-SWEEP-REV-01.*`, `confidence.*`, `contradictions.*` |
| `config/scoring.example.yaml` | Faktor-Gewichte (Start gleichgewichtet), Penalties (0), Stufen-Bänder |
| `config/risk.example.yaml` | Risikostufen, Limits, Sizing-Parameter (dynamischer Hebel), Kill-Switch-Ebenen |
| `config/news.example.yaml` | Impact-Map, Routing, Blackout-Fenster |
| `config/anti_overfitting.example.yaml` | Splits, Walk-Forward, Monte-Carlo, TO-VALIDATE-Parameter |
| `config/providers.example.yaml` | Provider-Registry (Fähigkeiten, Kosten, Lizenz, Rate-Limits) + Router-Policy je Assetklasse *(ab Phase 2)* |

Nur `*.example.*` versioniert; lokale Kopien + `.env` via `.gitignore` ausgeschlossen.
**Keine Secrets** in Konfigdateien — Secrets ausschließlich über `security/secrets` (Env → OS-Keychain
→ später Vault), Demo/Live getrennt (`docs/SECURITY.md`).

---

## 7. Signalformat (Scanner-Ausgabe, `SignalReport`)

Der Scanner meldet **nur relevante** Signale. Jede Meldung enthält:

```
SignalReport {
  timestamp: UTC
  instrument: str
  decision: BUY | SELL | WAIT | NO_TRADE   # WAIT: Kette lebt, State < ARMED, kein Veto (0.1.1 C6)
  setup_id: "SMC-SWEEP-REV-01"
  strategy_version: "0.1.0"
  # nur bei BUY/SELL:
  entry: float
  sl: float
  tp1: float
  tp2: float
  tp3: str|float          # Runner / HTF-Ziel-Referenz
  rr_to_tp2: float
  blended_rr: float
  risk_ccy: float          # erlaubtes Risiko in Kontowährung
  risk_pct: float
  position_size: float
  leverage_used: float     # dynamisch berechnet
  required_margin: float
  liquidation_price: float
  score: float             # 0..100
  tier: A_PLUS | A | B
  confidence: float        # setup_confidence 0..1
  liq_distance_pct: float  # Abstand Entry → Liquidationspreis, in %
  potential_gain_tp1_ccy: float   # möglicher Gewinn bei TP1 (Kontowährung, nach Kosten)
  potential_gain_tp2_ccy: float
  potential_gain_tp3_ccy: float   # Runner: Schätzung mit rr.tp3_assumed_r
  potential_loss_sl_ccy: float    # möglicher Verlust bei SL (== risk_ccy, nach Slippage-Puffer)
  rationale: str           # kausale Kette in Worten + limitierende Faktoren
  ai_reasoning: str | null # optionaler Zusatztext aus ai/reasoning (beeinflusst NICHTS)
  portfolio_impact: {
    cluster_id, cluster_open_risk_pct_before, _after,
    total_open_risk_pct_before, _after,
    correlation_note, new_factor_exposure
  }
  trace_id: str            # verbindet Scan→Setup→Signal→Approval→Order→Fill im Decision Ledger
  # bei NO_TRADE / WAIT:
  no_trade_reasons: [NoTradeReason]   # leer bei WAIT
  chain_progress: str      # z.B. "STRUCTURE_SHIFTED erreicht, RR_BELOW_MIN" bzw. "SWEPT — warte auf Reclaim"
  setup_state: str         # interner FSM-Zustand (SMC-SWEEP-REV-01 §24)
}
```

---

## 8. Portfolio ↔ Investment ↔ Trading — strikte Trennung (Audit §8)

- **`portfolio/`** — Accounting/State, geteilt. Alle Positionen (**Bybit** via API ab Phase 9;
  **Trade Republic** nur **manueller Import**, `source=manual`, read-only — keine inoffizielle API;
  Stocks/ETFs/Crypto/Altcoins/Derivate/Cash). Berechnet Exposure (Notional + 1R-Risiko),
  Korrelation, Klumpenrisiko, Asset-Allocation, Drawdown, offene Risiken, Faktor-Exposure.
- **`investment/`** — Langfrist. `INVEST | WAIT | HOLD | REDUCE` je Instrument/Bucket +
  Begründung. Monatsbudget ~200–400 EUR. Berücksichtigt bestehende Positionen. **Erzeugt keine
  Trade-Signale.** Empfehlungen setzt der Nutzer um (später ggf. separater langsamer Pfad).
- **`scanner/` + `strategy/` + `risk/`** — 24/7-Trading. `BUY | SELL | NO_TRADE`. Je-Trade-Risiko-
  Sizing. Nutzer-Freigabe vor jeder Order (Demo/Live).

Investment- und Trading-Engine sind getrennte Codepfade; beide lesen `portfolio/`, keine
rechnet Exposure selbst nach.

---

## 9. AI / LLM — Guardrails (verbindlich)

`ai/` ist ein **beratender** Layer. Der Contract steht ab Phase 3, die Nutzung ist später und
optional.

**Ein LLM darf niemals:**
- die Risk Engine umgehen,
- Veto-Regeln (V1–V10) umgehen oder aufheben,
- Positionslimits erhöhen,
- einen Hebel erzwingen,
- ein `NO_TRADE` überschreiben.

**Zwei getrennte AI-Flächen (Audit C-04):**
- **`ai/reasoning.py`** — LLM erzeugt **nur Text** (News-/Research-Zusammenfassungen,
  Signal-Begründungen, Interpretation unstrukturierter Infos). Der Text landet im Ledger und im
  Dashboard-Feld `ai_reasoning`. Er **gated nichts** und geht in keine Berechnung ein.
- **`ai/advisor.py`** — **optionaler** Score-Modulator. Läuft **strikt nach** No-Trade-Check +
  Regime-Gate + Ketten-Gates + Vetos. Darf den Score nur um
  ≤ `ai_guardrails.max_score_modulation_points` bewegen, **nie** ein Gate/Veto aufheben, **nie**
  eine Risikostufe nach oben erzwingen. **Default: `engines.ai_reasoning: false` → AUS.**

**Technisch erzwungen durch:**
- `ai/contract.py`: jede LLM-Ausgabe wird gegen ein striktes JSON-Schema (Pydantic) validiert;
  Schemaverstoß ⇒ Ausgabe verworfen, System läuft rein regelbasiert weiter.
- Der `advisor` bekommt den Kontext **erst**, wenn `risk/veto` bereits `[]` zurückgab.
- `ai_confidence` ist **getrennt** von `setup_confidence` und fließt nicht in dessen Berechnung.
- Determinismus: `temperature=0`, gepinnte Modell-ID, Prompt-Templates versioniert
  (`prompt_version`).
- Jede Anfrage/Antwort + `prompt_version` + Modell-ID ⇒ Decision Ledger + Audit Log.
- Timeout / Nichtverfügbarkeit ⇒ Fallback auf reine Regeln (kein Blockieren, kein Raten).

---

## 10. Phasen & Reifegrade

**14 Phasen** — vollständige Liste + Deliverables in `FINAL_IMPLEMENTATION_PLAN.md` (aktualisiert
durch den Audit): 1 Data Foundation ✅ · 2 Research/Backtesting · 3 Strategy Engine · 4 Risk+Portfolio ·
5 Autonomous Scanner+Signal · 6 TradingView/Chart · 7 Portfolio+Capital Allocation · 8 Paper Trading ·
9 Bybit Demo · 10 Execution · 11 Monitoring · **12 Dashboard** · 13 Production Readiness ·
14 begrenzter Live-Betrieb.

| Stufe | Bedeutung | Gate (Details `anti-overfitting.md` §9) |
|-------|-----------|----------------------------------------|
| **MVP** | erster reproduzierbarer Backtest, BTC/ETH, `SMC-SWEEP-REV-01` | Docs `0.1.0` eingefroren, Datensatz-Version fixiert, 8 TO-VALIDATE-Parameter, echte BTC-Historie |
| **Paper** | simulierte Fills gegen Live-/Delayed-Daten | positiver Edge auf Validation **und** unberührtem Test; Sensitivität = Plateau; Monte-Carlo `ruin < 5 %`; Time-/Symbol-Stability |
| **Demo** | Bybit **Testnet**, echte API, kein Echtgeld | ≥ 100 Paper-Trades; Parity-Report grün; Reconciliation + State-Recovery + Kill-Switch-Drills |
| **Live (begrenzt)** | Echtgeld, kleinster Umfang | **separate, ausdrückliche Nutzer-Entscheidung** — Phase 14 |

---

## 11. Teststrategie

- Unit-Tests je Engine mit synthetischen, deterministischen Candle-Fixtures.
- Golden-Tests für Analyse-Engines: dokumentiertes Chartmuster → erwartete Primitive-Objekte.
- Property-Tests (Swings alternieren; `fill_fraction` monoton; …).
- **Invarianten-Tests** (Pflicht): Look-ahead-Immunität, Risk-Veto, kein-SL, unsichere-Daten,
  Kill-Switch-Persistenz, Determinismus, Risiko ≤ Stufen-Budget, Hebel ändert 1R nicht, Backtest=
  Paper-Parität, Kosten-Differenz.
- Integrationstests der Pipeline auf kleinen Datensätzen.
- Kein Merge ohne Tests.

---

## 12. Bewusst (noch) nicht enthalten

- Broker-Adapter-Implementierungen für Trading, Live-Ausführung, Order-Routing an echte Börsen
  (Bybit **public market data** ab Phase 2 ist erlaubt — read-only, keine Keys).
- Echte API-Keys, Secrets, Auth-Flows, Echtgeld-Orders, automatische Echtgeld-Ausführung.
- LLM-**Nutzung** in der Entscheidungs-Pipeline (nur Contract + Guardrails jetzt; `advisor` Default AUS).
- `investment/`-Logik (nur Interface-Platzhalter bis Phase 7).
- TradingView-/Chart-Integration (Phase 6), Dashboard-`api/` (Phase 12).
- Weitere Setup-Typen über `SMC-SWEEP-REV-01` hinaus (nach MVP-Validierung).
- Distributed Infra (Message-Queue, Microservices, k8s, TimescaleDB) — **bewusst nicht**, Monolith genügt.

## 13. Verweise

- **`docs/FINAL_ARCHITECTURE_AUDIT.md`** — dieser Architekturstand + Begründungen + Datenquellen + Risiken.
- `FINAL_IMPLEMENTATION_PLAN.md` — 14 Phasen mit Deliverables.
- `docs/strategy/` — eingefrorene Strategie-Spezifikation `0.1.0`.
- `docs/PHASE_1_STATUS.md` — Data-Foundation-Abschlussbericht.
