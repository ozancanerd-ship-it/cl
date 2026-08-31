# Final Implementation Plan

**Stand:** 2026-08-28 (aktualisiert durch `docs/FINAL_ARCHITECTURE_AUDIT.md`)
**Strategy-Version:** `0.1.0` (eingefroren, `docs/strategy/DECISIONS-0.1.0.md`)
**Gilt zusammen mit:** `ARCHITECTURE.md`, `docs/FINAL_ARCHITECTURE_AUDIT.md`,
`docs/ARCHITECTURE_GAP_AUDIT.md`, `docs/STRATEGY_LOGIC_AUDIT.md`, `docs/strategy/*`.

> **Maßgeblich für die Phasen-/Komponenten-Zuordnung ist `docs/FINAL_ARCHITECTURE_AUDIT.md` §17.**
> **14 Phasen** (Dashboard neu als Phase 12): 1 Data Foundation ✅ · 2 Research/Backtesting ·
> 3 Strategy Engine · 4 Risk+Portfolio · 5 Autonomous Scanner+Signal · 6 TradingView/Chart ·
> 7 Portfolio+Capital Allocation · 8 Paper Trading · 9 Bybit Demo · 10 Execution · 11 Monitoring ·
> **12 Dashboard** · 13 Production Readiness · 14 begrenzter Live-Betrieb.
> Die Detailabschnitte unten behalten die alte Nummerierung dort, wo der Audit sie nicht ändert;
> „Phase 12 — Production Readiness" heißt jetzt **Phase 13**, „Phase 13 — Live" jetzt **Phase 14**.

---

## 0. Unverrückbare Rahmenbedingungen (für den gesamten Plan)

- **Kein Live-Trading. Keine echten API-Keys. Keine Echtgeld-Orders. Keine automatische
  Echtgeld-Ausführung.** Bis einschließlich Phase 13. (Bybit **public market data** ab Phase 2:
  read-only, keine Keys — erlaubt.)
- **Eine Strategy Engine.** Backtest, Paper und Live rufen **denselben** `strategy.evaluate(MarketContext) -> Decision`
  auf. Wird nie geforkt. Unterschiede nur bei Datenquelle, `Clock` und Ausführungs-Adapter.
- **Risk Engine hat Vetorecht.** Kein Score, kein Hebel, keine LLM-Ausgabe überstimmt ein Veto (V1–V10).
- **Risiko → Größe → Hebel.** In dieser Reihenfolge. Hebel dynamisch, keine starren Caps, erhöht
  nie das erlaubte Verlustrisiko.
- **LLM/AI darf niemals:** Risk Engine umgehen · Veto-Regeln umgehen · Positionslimits erhöhen ·
  Hebel erzwingen · `NO_TRADE` überschreiben. (Contract + Guardrails ab Phase 3, Nutzung optional/später.)
- **Bar-Close-Gate:** Entscheidungen für `t+1` nur aus Daten mit `close_time ≤ t`.
- **Pro Komponente:** bauen → testen → Fehler beheben → testen → nächste. Kein Merge ohne Tests.
- **Pre-Registration:** Parameteränderungen an `0.1.0` ⇒ neue Version + Registry-Eintrag + OOS-Prüfung.

**Reifegrade:** MVP (erster reproduzierbarer Backtest) → Paper → Demo (Bybit Testnet) → Live (separate Nutzer-Entscheidung).

---

## Phase 0 — Setup & Spezifikation  *(größtenteils erledigt)*

**Ziel:** Vollständige, eingefrorene Strategie-Spezifikation + Sicherheits-Grundausstattung, bevor
Code entsteht.

| Deliverable | Status |
|-------------|--------|
| `docs/strategy/` (12 Docs) + `DECISIONS-0.1.0.md`, `strategy_version 0.1.0` eingefroren | ✅ |
| `ARCHITECTURE.md`, `TODO.md`, Config-Beispiele aktualisiert; `FINAL_IMPLEMENTATION_PLAN.md` | ✅ |
| `docs/SECURITY.md` (Least Privilege, Read-only-Dev-Keys, Redaction, Trust Boundaries) | ⬜ |
| `.pre-commit-config.yaml` (gitleaks, ruff, mypy), `pip-audit` in CI | ⬜ |
| Dependency-Lockfile (hash-gepinnt); `.env.example` `BYBIT_*` entfernen/verschärfen | ⬜ |
| `xcode-select --install` → `git init` → erster Commit mit aktiven Hooks; venv; `make test` grün | ⬜ |

**Exit-Gate:** Spezifikation eingefroren; Secret-Hooks + Lockfile aktiv; Repo unter Versionskontrolle.

---

## Phase 1 — Data Foundation

**Ziel:** Deterministische, qualitätsgeprüfte, look-ahead-freie Marktdaten für BTCUSDT/ETHUSDT
(D1, H4, M15, M5, M1), Multi-Asset-fähig.

**Komponenten**
- `core/`: `enums.py` (Timeframe, AssetClass, Direction, `NoTradeReason`, `VetoId`, `ExitReason`,
  `RegimeDirectional/Volatility/Phase`), `types.py` (Instrument, Candle, Series, …), `clock.py`
  (`SystemClock`, `SimClock`), `events.py`, `version.py` (Git-SHA, `strategy_version`, `config_hash`).
- `config/loader.py`: Pydantic-v2-Schema je YAML, `schema_version`-Pflicht, Fail-fast, `config_hash`.
- `utils/logging.py`: JSON-Logging, **Secret-Redaction**, Korrelation-IDs.
- `refdata/`: `instruments.py` (Tick/Lot/`min_notional`/`contract_multiplier`/`maintenance_margin`/
  `margin_tiers`/`max_leverage_broker`/Fees), `calendar.py` (Sessions in Börsenlokalzeit → UTC,
  DST-korrekt; 24/7 Crypto), `symbols.py` (kanonisch ↔ broker-spezifisch).
- `data/`: `interfaces.py` (`MarketDataProvider` ABC), `providers/mock_provider.py` (deterministisch),
  `providers/csv_provider.py` (UTC-Normalisierung), `resample.py` (M1→D1, look-ahead-frei),
  `quality.py` (Stale/Duplikat/Timestamp/OHLC-Konsistenz/Spike → `data_confidence`),
  `repository.py` (Parquet Candles + SQLite Events, Point-in-Time `as_of`), `health.py`.

**Tests**
- Golden: bekannte Candle-Fixtures → erwartete Series nach Resampling.
- Quality: künstliche Lücken/Duplikate/kaputte Timestamps → korrekte Flags + `data_confidence`.
- Point-in-Time: `repository.get(as_of=t)` liefert nie Daten mit `close_time > t`.
- Determinismus: gleicher Seed + Mock-Config → bit-identische Serie.

**Nicht in dieser Phase:** echte Datenprovider, Corporate Actions, Order-Book-Tiefe.

**Exit-Gate:** Für BTC/ETH steht ein versionierter, lückengeprüfter Datensatz (`dataset_version`
+ Hash) im Repository; alle Data-Quality-Checks getestet.

---

## Phase 2 — Research / Backtesting + Live Data / Paper-Live

**Aufgeteilt (Nutzer-Entscheidung 2026-08-28):** **2A = Historical / Research**, danach
**2B = Live Data / Paper-Live**. Nach 2B: **STOPP**, nicht Phase 3.

---

### Phase 2A — Historical / Research

**Ziel:** Ein reproduzierbarer, bias-freier Backtest-Rahmen auf **echten** historischen Daten,
**bevor** die Strategie-Engine existiert.

**Komponenten**
- `runtime/events.py` + `runtime/bus.py` — async Event-Bus (in-process pub/sub).
- `net/client.py` + `net/ratelimit.py` — httpx-Wrapper: Token-Bucket, Backoff+Jitter, Circuit
  Breaker. Getestet ohne Netzwerk (`respx`).
- `data/registry.py` + `data/router.py` — Provider-Fähigkeitsmatrix + Auswahl-Policy.
- `data/providers/kraken.py` (**primär**) + `data/providers/bybit_public.py` (**sekundär/Fallback**)
  — REST OHLC/Trades/Funding/OI; Normalisierung → Kern-Modelle. Getestet gegen aufgezeichnete
  JSON-Fixtures, **keine** echten Calls.
- `scripts/fetch_history.py` — CLI, die der **Nutzer mit Netzwerk** ausführt: lädt echte
  BTC/ETH-Historie von Kraken/Bybit ins Repository, vergibt `dataset_version`.
- `execution/simulation.py` — `CostModel` (Fees maker/taker, Spread, Slippage, **Funding-Accrual**),
  `FillModel` (limit/market/stop, **Partial Fills**, Latenz), `MarginModel` + `LiquidationModel`
  (Kraken/Bybit Linear-Perp, isolated).
- `execution/brokers/base.py` (`BrokerAdapter`/`MarketDataAdapter` ABC) + `execution/router.py`
  (`BrokerRouter`) + `execution/brokers/paper.py` (`PaperBroker` über `simulation.py`).
- `journal/ledger.py` — `TradeLedger` + `DecisionLedger` (SQLite, append-only, `trace_id`);
  `TradeRecord` mit MFE/MAE, R-Multiple, Kosten-Breakdown (`backtest-labeling.md` §2).
- `research/metrics.py` — Win-Rate, Profit-Factor, Expectancy, Avg-R, Max-DD, MFE/MAE-Verteilung,
  längste Verlustserie, Trade-Frequenz, Netto-vs.-Brutto.
- `research/dataset.py` — Point-in-Time-Feature-Bau; nur expanding/rolling Statistiken.
- `engine/backtest.py` — event-getrieben über `runtime/bus` + `BacktestDriver`; Strategie ist ein
  **Callback** (Phase 2A: `ReferenceMAStrategy` = SMA-Crossover, **nur Plumbing-Fixture, NICHT die
  echte Strategie**); Fills über `execution/simulation.py`.
- `research/validation.py` — chronologischer Train/Validation/**unberührter** Test (50/25/25),
  Walk-Forward, Purge/Embargo, **Time-/Symbol-/Regime-Stability** (`anti-overfitting.md` §4a).
- `research/robustness.py` — Monte-Carlo (Trade-Bootstrap ≥ 1000, Dropout, Kosten-Stress,
  Start-Jitter, Skipped-Signal); Ruin-Wahrscheinlichkeit.
- `research/registry.py` — `RunManifest` (`code_sha` falls git, `config_hash`, `dataset_version`
  + `dataset_fingerprint`, Seed, Zeitraum, `strategy_version`) → deterministischer Output-Hash.

**Tests (Pflicht):** Look-ahead-Immunität · Leakage-Assertion · Survivorship · Point-in-Time ·
Reproduzierbarkeit (`RunManifest` → bit-identisch) · Kosten mit/ohne · Provider-Adapter gegen
Fixtures · Partial-Fill · Liquidation · Walk-Forward-Folds · Monte-Carlo-Verteilung.

**Exit-Gate 2A:** Backtest läuft deterministisch (auf Mock **und** — sobald `fetch_history.py`
lief — echter BTC-Historie); alle Bias-Tests grün; `execution/simulation.py` mit Fees/Funding/
Partial-Fills/Liquidation getestet; Walk-Forward + Monte-Carlo + `RunManifest` reproduzierbar.

---

### Phase 2B — Live Data / Paper-Live

**Ziel:** Das System empfängt **echte Live-Daten** und läuft **24/7 autonom**, beobachtet den
Markt — **ohne jede Echtgeld-Order**.

**Live-Pipeline:** `Provider → Ingestion → Normalization → Data Quality → Event Bus → Strategy →
Risk → Signal → Paper Execution`

**Komponenten**
- `data/providers/kraken_ws.py` + `data/providers/bybit_ws.py` — WS-Clients (Kraken
  `wss://ws.kraken.com/v2`, Bybit `wss://stream.bybit.com/v5/public/linear`), Reconnect+Backoff,
  Resubscribe, Heartbeat. Verbindungsfactory injizierbar (Test ohne Netz).
- `data/aggregator.py` — Trades → OHLCV, „laufende" Bar, `is_final` bei Bar-Close.
- `data/ingestion/service.py` — Ingestion-Loop: Provider → normalisieren → `data/quality` →
  `BarClosed`/`QuoteUpdate` auf den Bus + Repository-Schreiben; Gap-Erkennung + REST-Backfill.
- `runtime/drivers/live_driver.py` + `runtime/supervisor.py` — Daemon: besitzt den Loop, verdrahtet
  Subscriber, Heartbeat, Graceful Shutdown, fail-safe Start.
- `scanner/scanner.py` (**Shell**) — subscribt `BarClosed`, ruft die (in 2B noch platzhaltende)
  `evaluate()` auf, loggt „would evaluate", zählt Metriken. Das ist die „24/7-Beobachtung".
- `ops/metrics.py` + `ops/health.py` (minimal) — Live-Status.
- `scripts/run_paper_live.py` — Daemon-Einstieg. In dieser Umgebung gegen eine **Synthetic-Live-
  Quelle** (spielt Repository-Bars zeitgerafft ab); mit Netz gegen Kraken/Bybit-Public-WS.

**Gold live:** via `PepperstoneMT5Adapter` — **Windows + MT5-Terminal + Konto erforderlich**;
auf Nicht-Windows deaktiviert. 2B-Live-Backbone konzentriert sich auf **Crypto** (Tier 1: BTC).

**Tests (Pflicht):** Ingestion normalisiert korrekt · Data-Quality-Veto stoppt schlechte Bars ·
Bus liefert Events an alle Subscriber · Scanner-Shell reagiert auf `BarClosed` · **kein** Pfad
sendet eine Order · Graceful Shutdown · Reconnect-Logik (fake WS) · Supervisor-Lebenszyklus.

**Exit-Gate 2B:** `run_paper_live.py` läuft stabil (synthetic + — mit Netz — Kraken/Bybit),
ingestet → quality → bus → scanner-shell → metrics; Paper-Execution-Pfad verdrahtet, aber idle
(keine Strategie); **nachweislich keine Echtgeld-Order möglich**; sauberer Shutdown.

**Danach: STOPP. Nicht Phase 3.**

---

## Phase 3 — Strategy Engine  *(die geteilte Engine)*

**Ziel:** `strategy.evaluate(MarketContext, portfolio_context=None) -> Decision` gemäß
`docs/strategy/` (`strategy_version 0.1.1`) — identisch für Backtest/Paper/Live.

**`0.1.1`-Vorgaben (Spec-Audit C1–C12, `DECISIONS-0.1.1.md`):** `Decision ∈ {BUY, SELL, WAIT,
NO_TRADE}` (C6); Scoring gleichgewichtet (C2); Risiko-Tiers 1.00/0.65/0.40 % (C1, wirkt erst
Phase 4); MTF aus **M5-Basis** per `resample.py` (C11); Price Action = Displacement +
Engulfing/Pin/Minor-CHoCH-M1 (`SPEC-ADDENDUM-0.1.1.md` §2, C7); PIT-News-Fixture FOMC/CPI/NFP/PCE
(C10); Veto V9 pass-through ohne `portfolio_context` (C9); S/R = opposing LiquidityLevels (C8).
**Vorarbeit:** `fetch_history.py` Bybit-Pagination → ≥ 180 Tage M5 BTC/ETH (C12).

**Komponenten (Reihenfolge)**
1. `strategy/primitives.py` — 13 Detektoren aus `primitives.md` (Swing/BOS/CHoCH/Liquidity/
   EqualH-L/Sweep/Displacement/FVG/IFVG/OB/Breaker/Mitigation/Premium-Discount). **Golden-Tests
   je Primitive** (dokumentiertes Chartmuster → erwartetes Objekt).
2. `analysis/market_structure.py`, `liquidity.py`, `smc.py`, `support_resistance.py`, `sessions.py`.
3. `analysis/regime.py` — Directional/Volatility/Phase, MTF-Konsens **D1+H4**, Hysterese,
   `NO_TRADE`-Ausgänge (`regime.md`). **UNCLEAR/CONFLICTING/EXTREME/LOW-Vol ⇒ NO_TRADE** (Nutzer-Festlegung).
4. `analysis/mtf.py`, `analysis/news.py` (Fixture, Impact-Map, Routing, Blackout aus `news.example.yaml`),
   `analysis/macro.py`.
5. `core/types.py: MarketContext` — Aggregat + `data_confidence`, `analysis_confidence`, `regime`.
6. `strategy/setup_detection.py` — kausale Kette `SMC-SWEEP-REV-01` als State Machine
   (`SCANNING → LIQUIDITY_IDENTIFIED → SWEPT → RECLAIMED → DISPLACED → STRUCTURE_SHIFTED → ARMED`);
   erzeugt vollständigen `SetupCandidate` (`backtest-labeling.md` §2 Feldliste).
7. `strategy/confluence.py` — Confluence-Gate (alle Kettenglieder Pflicht; kein Einzelindikator-Trade).
8. `strategy/veto.py` — `collect_vetoes()` V1–V10, **läuft vor dem Score**; nicht leer ⇒ `NO_TRADE`,
   Score wird nicht berechnet.
9. `strategy/confidence.py` — `data_confidence = min(...)`, `analysis_confidence` (6 Terme),
   `setup_confidence` mit Floor-Penalty (`confidence.md`).
10. `strategy/scoring.py` — 12 WEIGHTED-Faktoren mit 0..1-Rubriken (`scoring-rubric.md`), **Start
    gleichgewichtet, Penalties 0**; Stufe A+/A/B (`scoring.example.yaml`).
11. `ai/contract.py` — LLM-Output-JSON-Schema (Pydantic) + Guardrails; **`engines.ai_reasoning: false`**
    im MVP (Nutzung deaktiviert, Contract & Fallback-Pfad trotzdem getestet).
12. `strategy/__init__.py: evaluate()` — verdrahtet 1–11 in der `resolve()`-Reihenfolge aus
    `contradictions.md` §6.

**Tests (Strategie-Invarianten — Pflicht)**
- Risk-Veto: maximaler positiver Kontext + genau ein Veto ⇒ `NO_TRADE`, Score-Funktion nicht aufgerufen.
- Kein-SL: `SetupCandidate` ohne regelkonformen SL ⇒ `NO_TRADE`.
- Unsichere Daten: `data_confidence < 0.50` ⇒ kein Setup.
- Location: Long im Premium ⇒ `NO_TRADE` (V2), unabhängig vom Score.
- Regime: `UNCLEAR`/`CONFLICTING`/`EXTREME`/`LOW`-Vol ⇒ `NO_TRADE`.
- Kausale Kette: jedes fehlende Kettenglied ⇒ korrekter `NoTradeReason`, keine Kompensation.
- LLM-Guardrail: manipulierte LLM-Ausgabe kann kein Gate/Veto aufheben, keine Stufe erzwingen.
- Golden-Setup: ein handkonstruierter Chart, der die volle Kette erfüllt ⇒ erwarteter `SetupCandidate`.

**Exit-Gate:** `evaluate()` läuft über den Phase-1-Datensatz im Phase-2-Backtest; alle 8
Invarianten-Tests grün; Golden-Setup reproduziert.

---

## Phase 4 — Risk + Portfolio

**Ziel:** Vetorecht + dynamisches, risikobasiertes Sizing + Portfolio-Zustand.

**Komponenten**
- `risk/limits.py` — Tag/Woche/Drawdown/Trades/offene Positionen/Portfolio-Exposure/korrelierte
  Exposure/Portfolio-Heat/Loss-Streak; **Verbote im Code erzwungen** (kein Martingale, kein
  Averaging-down, keine Risikoerhöhung nach Verlusten, Größe nie aus Gewinn, Hebel umgeht kein Veto).
- `risk/margin.py` — Liquidationspreis-Schätzung, Maintenance-Margin-Tiers, Mindest-Liquidationsabstand.
- `risk/position_sizing.py` — 12-Schritt-Algorithmus (`sizing.md` §2): **Risiko → Größe → Hebel**,
  Vol-/Liquiditäts-/Margin-/Funding-Caps, `NO_TRADE` statt Kompromissgröße.
- `risk/risk_engine.py` — `RiskDecision`, alle `NoTradeReason` aus `no-trade.md` [6]/[8].
- `execution/portfolio.py` — `PortfolioState`: Exposure (Notional **und** Risiko), Faktor-Exposure
  (MVP: `CRYPTO_BETA`, `USD`), Korrelation (statisch `BTC↔ETH = 0.80` + gemessen), Cluster, Heat.
- `safety/kill_switch.py` — hierarchisch (`global/broker/asset/strategy/data`), persistiert,
  fail-safe beim Start.
- `state/` — Store (SQLite): Positionen, Orders, `KillSwitchState`, Verlustzähler, Equity-Hoch;
  `recover()`, `graceful_shutdown()`.

**Tests (Sizing-Invarianten — Pflicht)**
- `realized_risk_ccy ≤ risk_budget_ccy × 1.05` — immer.
- Hebel ändert 1R nicht: `max_leverage_broker` 5 vs 25 ⇒ identisches `realized_risk_ccy`.
- Reihenfolge Risiko→Größe→Hebel: `leverage_used` ist reine Funktion von (Größe, Margin-Budget, Broker-Grenze).
- Größe unabhängig von TP-Distanzen.
- Kein Martingale/Averaging-down: kein Code-Pfad erhöht Größe nach Verlust.
- Beispiel-Fall: Equity 50, sinnvolle Größe 200–300 ⇒ Hebel wird berechnet, wenn Constraints ok; sonst `NO_TRADE`.
- Kill-Switch-Persistenz: aktiv → Neustart → weiterhin aktiv, keine Orders.
- Loss-Streak: n Verluste ⇒ Pause + Alert, **kein** Auto-Resume, **keine** Größenänderung.

**Exit-Gate:** Backtest läuft mit vollständiger Risk Engine; alle Sizing-Invarianten grün;
State-Recovery getestet.

---

## Phase 5 — Autonomous Market Scanner + Signal Engine

**Ziel:** Das System scannt autonom die aktivierten Märkte und meldet **nur relevante** Signale.

**Komponenten**
- `engine/pipeline.py` — die geteilte Pipeline (§3 ARCHITECTURE), Bar-Close-getaktet.
- `scanner/scanner.py` — iteriert Instrumente/Timeframes, ruft `strategy.evaluate()` + `risk_engine`
  + `position_sizing`; meldet Setups ab Stufe **B** sowie bewusste `NO_TRADE`, wenn die Kette
  mindestens `STRUCTURE_SHIFTED` erreichte (konfigurierbar, `config.example.yaml: scanner`).
- `scanner/signal_engine.py` — `SignalReport` im **festen Format**:
  `BUY | SELL | NO_TRADE` · Entry · SL · TP1/TP2/TP3 · RR (`rr_to_tp2`, `blended_rr`) · Risiko
  (`risk_ccy`, `risk_pct`) · Positionsgröße · Hebel (`leverage_used`) · `required_margin` ·
  `liquidation_price` · Score · Confidence · Begründung (`rationale` = kausale Kette in Worten +
  limitierende Faktoren) · Portfolio-Impact (`cluster_open_risk_pct` vorher/nachher,
  `total_open_risk_pct` vorher/nachher, Korrelations-Notiz, neue Faktor-Exposure) ·
  bei `NO_TRADE`: `no_trade_reasons[]` + `chain_progress`.
- `ops/notify.py` + `scanner/alerting.py` — Konsole/Datei jetzt; Telegram/E-Mail später;
  Severity, Dedup, Rate-Limit.
- `journal/decision_ledger.py` — jede Entscheidung (auch `NO_TRADE`) mit Kontext-Snapshot + Version.

**Tests**
- Scanner meldet identische Entscheidungen wie ein direkter `evaluate()`-Aufruf (keine Divergenz).
- `SignalReport` enthält alle Pflichtfelder; `NO_TRADE`-Reports tragen Gründe + `chain_progress`.
- Relevanzfilter: kein Alert unter Stufe B, kein `NO_TRADE`-Alert unter erreichter Kettenstufe.

**Nicht in dieser Phase:** externe Alert-Kanäle, TradingView.

**Exit-Gate:** Scanner läuft über den Datensatz und produziert einen nachvollziehbaren
Signal-Stream + vollständiges Decision Ledger.

---

## Phase 6 — TradingView / Chart

**Ziel:** Signale und SMC-Kontext **read-only** visualisieren. Unsere Engine ist das Gehirn,
TradingView (bzw. Lightweight Charts) nur die Anzeige. **Keine** Order-Funktion, **keine**
Browser-/Click-Automation, **kein** Pine-Script als Datenquelle.

**Komponenten**
- `chart/annotations.py` — erzeugt aus `SignalReport` + `MarketContext` **strukturierte
  Annotation-Payloads** (Daten, kein Rendering): `Marker` (BUY/SELL), `PriceLine` (Entry/SL/TP1-3),
  `Zone/Box` (FVG, **IFVG**, Order Block, Breaker, Premium/Discount, Setup-Zone), `TrendLine`
  (BOS/CHoCH), `Point` (Swing H/L, Equal H/L), `SessionBand`, `Label` (**Setup-ID**, **Strategy-Version**).
- `api/`-Endpunkte: `ChartDataAPI` (Datafeed) + `ChartAnnotationsAPI` + **WS-Update-Stream**
  (`annotation_added/updated/removed`, `bar`) → Chart läuft **live** mit, wenn sich der Setup-State
  ändert (`scanner/tracker` publiziert → `chart/` diff't → WS).
- Referenz-Frontend: **Lightweight Charts** (Apache-2.0, ungated). Umstieg auf die TradingView
  Charting Library (gated, lizenziert) später möglich — **gleiche Backend-API**.

**Tests**
- Annotation-Snapshot-Test: gegebener `SignalReport` → erwartete Annotation-Liste.
- Kein Pfad in `chart/` ruft `execution/*` oder einen Broker-Adapter.
- WS-Update: Setup wechselt `armed → confirmed` ⇒ genau die geänderten Annotationen im Stream.

**Exit-Gate:** Ein Beispiel-Signal wird vollständig auf dem Chart markiert (inkl. Setup-ID +
Strategy-Version); Live-Update bei Setup-Änderung funktioniert.

---

## Phase 7 — Portfolio + Capital Allocation

**Ziel:** Reales Gesamtportfolio abbilden; die **Investment Engine** (getrennt von Trading) bauen.

**Komponenten**
- `portfolio/engine.py` erweitern: Multi-Asset (Stocks, ETFs, Crypto, Altcoins, Derivate, Cash),
  Klumpenrisiken, Faktor-Exposure voll (USD, Rates, Equity-Beta, Crypto-Beta, Gold), `simulate_add()`.
- Portfolio-Import:
  - **Bybit** — read-only Positions-/Balance-Import (via API ab Phase 9; hier Datenmodell + Mock).
  - **Trade Republic** — **nur manueller Import** (CSV/Statement), `source=manual`, read-only.
    **Kein inoffizieller API-Zugriff** (ToS-/Sperre-Risiko).
- `data/providers/polygon_io.py` — US-Aktien-Daten + **Corporate-Actions-API**;
  `refdata/corporate_actions.py` — Backadjustment historischer Kurse.
- `investment/engine.py` — Langfrist-Empfehlung `INVEST | WAIT | HOLD | REDUCE` + `rationale`.
  Eingaben: Monatsbudget (~200–400 EUR), `portfolio/`-State, Regime, Langfrist-Chancen, Limits.
  **Berücksichtigt bestehende Positionen, empfiehlt nicht blind neue Assets. Erzeugt NIE
  Trade-Signale.** Strikt getrennt vom Scanner/Trading-Pfad. Budget bis dahin manuell zugeteilt.

**Tests**
- Portfolio-Aggregation (Notional + 1R-Risiko + Faktor-Exposure) auf konstruiertem Multi-Asset-Portfolio.
- Cluster/Klumpenrisiko: BTC-Long + ETH-Long ⇒ ein Cluster, `max_correlated_exposure` greift.
- `investment/engine` gibt strukturell gültigen `InvestmentPlan` zurück; **kein** Codepfad von
  `investment/` erzeugt einen `OrderIntent`/`SignalReport`.
- Corporate Action (Split) → historische Aktienkurse korrekt backadjustiert (as-of).

**Exit-Gate:** Gesamtportfolio (inkl. manuell importierter Positionen) korrekt aggregiert;
`INVEST/WAIT/HOLD/REDUCE` nachvollziehbar; Trennung Investment ↔ Trading testbar bewiesen.

---

## Phase 8 — Paper Trading

**Ziel:** Die geteilte Pipeline gegen Live-/Delayed-Daten mit **simulierten** Fills. Kein Broker.

**Komponenten**
- `execution/brokers/sim_adapter.py` — Fills gegen Bar-Daten, Fill-/Cost-Model identisch zum
  Backtest (`backtest-labeling.md`).
- `execution/trade_management.py` — TP1/2/3, Teilgrößen 50/25/25, Break-even nach TP1, Trailing
  nach TP2, Klasse-A/B/C/D-Invalidierung, verbindliche Bar-Auswertungsreihenfolge (`invalidation.md`).
- `execution/order_management.py` — Lifecycle-State-Machine (`NEW→ACK→PARTIAL→FILLED/REJECTED/
  CANCELLED/EXPIRED`), Idempotenz (`client_order_id`, `intent_hash`), Duplicate-Order-Schutz,
  Order-Historie.
- `engine/paper_trading.py` — geteilte Pipeline, `SystemClock`, Delayed-Daten.
- `engine/parity.py` — Backtest vs. Paper auf demselben Fenster → Signal-für-Signal-Diff-Report.
- `journal/trading_journal.py`, `journal/performance.py` — Kennzahlen je Setup/Regime/Session/Tier.

**Tests**
- Parität: Backtest und Paper auf demselben Fenster ⇒ identische Entscheidungen (Toleranz dokumentiert).
- Trade-Management: jeder Exit-Grund (`TP/SL/STRUCT_INVALIDATION/TIME_EXPIRY/DEAD_TRADE/NEWS_FLATTEN`)
  wird korrekt ausgelöst; Klasse-B liefert `hypothetical_sl_outcome_r`.
- Order-Lifecycle: Reject/Cancel/Partial werden deterministisch behandelt.
- Kill-Switch- und Loss-Streak-Pfade in Paper geübt.

**Exit-Gate (Paper → Demo, `anti-overfitting.md` §9):**
positiver Edge auf Validation **und** unberührtem Test; Sensitivität = Plateau; Monte-Carlo
`ruin < 5 %`; **≥ 100 Paper-Trades**; Paper-Expectancy innerhalb `kill.max_live_gap_r` des
Backtests; Parity-Report grün.

---

## Phase 9 — Bybit Demo Trading (Testnet)

**Ziel:** Erste echte API-Anbindung — **Testnet, kein Echtgeld**.

**Vorarbeit**
- `docs/SECURITY.md` finalisieren: Read-only-Keys in Entwicklung, Trade-only **ohne Withdrawal**
  in Demo, IP-Allowlist wo möglich, Rotation, Credential-Failure-Handling, Log-Redaction.
- Secret-Handling: ausschließlich Umgebungsvariablen / OS-Keychain. **Nichts im Repo.**

**Komponenten**
- `execution/brokers/_client/` — REST + WebSocket, Reconnect mit Backoff+Jitter, **Token-Bucket-
  Rate-Limiter**, Circuit Breaker, Request-Signierung.
- `execution/brokers/bybit_adapter.py` — Testnet: zuerst **nur Marktdaten** (`MarketDataAdapter`),
  dann Paper-Orders auf Testnet (`BrokerAdapter`).
- `refdata/` mit realen Bybit-Instrumentdaten (Fees, Margin-Tiers, `max_leverage_broker`).

**Tests**
- Adapter gegen aufgezeichnete Testnet-Responses (VCR-Style), keine Live-Calls im Testlauf.
- Rate-Limit-Handling: simulierter 429 ⇒ Backoff, kein Sturm.
- WS-Disconnect ⇒ Reconnect + REST-Reconciliation der verpassten Ereignisse.

**Exit-Gate:** Demo läuft stabil auf Testnet über einen definierten Zeitraum; Adapter-Fehlerpfade getestet.

---

## Phase 10 — Execution + Reconciliation

**Ziel:** Broker-State ist die Wahrheit; interner State bleibt synchron; sauberer Not-Aus.

**Komponenten**
- `execution/reconciliation.py` — periodischer Abgleich Positionen/Orders/Balance (Broker ↔ intern);
  Drift-Alarm; Auto-Korrektur **nur nach Regeln**; Recovery nach Neustart.
- Order-Lifecycle vollständig: ACK/Reject/Cancel/Partial/Expire mit Timeouts je Übergang.
- `safety/kill_switch.emergency_flatten()` — **getesteter** Pfad (alle Positionen schließen +
  Orders stornieren); regelmäßige Drills auf Testnet.
- `core/clock.py` — NTP-/Drift-Check gegen Bybit-Serverzeit; Degradation bei Drift > Schwelle.

**Tests**
- Reconciliation: verpasster Fill / Teilfüllung / Neustart ⇒ interner State wird korrekt nachgezogen,
  Drift-Alarm bei echtem Widerspruch.
- Idempotenz: Retry nach Timeout erzeugt keine Doppelorder (Status-Abfrage vor Retry).
- Emergency-Flatten: aus beliebigem Zustand ⇒ flach, verifiziert.

**Exit-Gate:** Reconciliation- und Flatten-Drills auf Testnet mehrfach erfolgreich; Clock-Drift-Schutz aktiv.

---

## Phase 11 — Monitoring + Journal + Analytics

**Ziel:** Unbeaufsichtigter Betrieb ist beobachtbar, auditierbar, wiederherstellbar.

**Komponenten**
- `ops/monitoring.py` — Health-Checks, Heartbeats, Schwellen-Alerts, Data-Source-Health-Registry.
- `safety/audit_log.py` — append-only, **Hash-Chain**-Integrität, alle Orders + Kill-Switch +
  Config-Änderungen + Key-Nutzung.
- `safety/error_handling.py` — Fehlerklassen, Retry/Backoff, Connection-/API-Failure,
  Degradations-Modus.
- `journal/performance.py` — Kennzahlen je Setup/Regime/Session/Tier; **Setup-/Regime-Statistik-
  Feedback** in die Scoring-Gewichte (governt, **OOS**, mit Registry-Verweis; kein In-Sample-Tuning).
- `ops/runbooks/` — „Datenquelle tot", „Broker-Disconnect", „State-Drift", „Kill-Switch ausgelöst";
  Backup-Skript für `data/` + Restore-Test.

**Tests**
- Alert wird bei jeder definierten Schwellenverletzung genau einmal ausgelöst (Dedup).
- Audit-Log-Integrität: Manipulation eines Eintrags wird erkannt (Hash-Chain bricht).
- Backup → Restore → System läuft mit identischem State weiter.

**Exit-Gate:** Monitoring + Audit + Backup/Restore nachweislich funktionsfähig; Performance-Report
je Bucket verfügbar.

---

## Phase 12 — Dashboard  *(neu durch Audit C-08)*

**Ziel:** Eine eigene professionelle Trading-App. Der Nutzer sieht alles, muss aber nichts tun —
das System analysiert dauerhaft im Hintergrund.

**Komponenten**
- **`api/` (FastAPI)** — REST + WebSocket. Panels: Live-Scanner, Chart, BUY/SELL-Signale,
  Portfolio, Risk, offene Positionen, Performance, Trade-Journal, News, AI-Reasoning, Alerts,
  System-Health, Execution-Status.
- **`api/` Chart-Endpunkte** — `ChartDataAPI` (Datafeed) + `ChartAnnotationsAPI` (Overlays aus
  `chart/`) + WS-Update-Stream (`annotation_added/updated/removed`, `bar`) für **Live-Chart**.
- **`api/` Approval-Endpoint** — „BUY bestätigen?": Dashboard zeigt Pending-Signal, Nutzer
  approve/reject, Entscheidung ins Ledger, **dann** (Demo/Live) geht die Order raus. Backtest/
  Paper laufen ohne manuelle Freigabe.
- **Frontend** — separates Projekt (React/Vue), **nicht Python**. Chart via **Lightweight Charts**
  (aus Phase 6), Umstieg auf TradingView Charting Library später möglich.

**Tests**
- API-Contract-Tests (Schema jeder Response), WS-Reconnect, Approval-Flow (approve/reject →
  korrekter Ledger-Eintrag → Order erst nach approve).
- „Jeder Trade nachvollziehbar": ein Trade → vollständige Kette `DATA→ANALYSIS→SETUP→SCORE→RISK→
  SIGNAL→APPROVAL→ORDER→FILL→MANAGEMENT→EXIT` im Decision Ledger, alle mit gemeinsamer `trace_id`.

**Exit-Gate:** Dashboard zeigt Scanner + Live-Chart mit Setup-Overlays + Portfolio + Health;
Approval-Flow funktioniert; jeder simulierte Trade ist end-to-end im Ledger nachvollziehbar.

---

## Phase 13 — Production Readiness

**Ziel:** Alle Freigabe-Gates dokumentiert erfüllt; das System ist betrieblich robust.

**Checkliste**
- Deployment-/Strategie-/Config-Versionierung in **jedem** Ledger-/Audit-Eintrag.
- Vollständige Kill-Switch-Hierarchie + Persistenz-Tests (global/broker/asset/strategy/data).
- State-Recovery-/Crash-Recovery-/Graceful-Shutdown-Tests bestanden.
- Parity Backtest = Paper = Demo grün; Monte-Carlo-Bänder halten in Demo-Realität.
- Alle Bias-Invarianten (Phase 2) + Strategie-Invarianten (Phase 3) + Sizing-Invarianten (Phase 4)
  in CI.
- `docs/SECURITY.md` vollständig; Secret-Scans + `pip-audit` grün; keine offene „Hoch"-Schwere aus
  `ARCHITECTURE_GAP_AUDIT.md` §5 für den Demo-Umfang.
- LLM/AI-Guardrails: falls `ai_reasoning` aktiviert werden soll — Contract-Tests + Fallback-Tests +
  „kein Bypass von Risk/Veto/Limits/NO_TRADE" in CI.

**Exit-Gate:** Alle Gates aus `anti-overfitting.md` §9 bis einschließlich „Demo" dokumentiert
erfüllt; Betriebs-Runbooks geprobt.

---

## Phase 14 — erst danach: begrenzter Live-Betrieb

**Nicht Teil dieses Plans.** Erfordert eine **separate, ausdrückliche Entscheidung des Nutzers**.

Wenn überhaupt:
- kleinster Umfang, **ein** Instrument, strengste Limits, manueller Kill-Switch griffbereit.
- Kapital-/Risiko-Freigabeprozess mit Mensch-in-the-loop für **jede** Limit-Erhöhung.
- Risikostufen-Prozente (`0.50 / 0.35 / 0.25 %`) vorher final durch Backtest/OOS/Walk-Forward/
  Drawdown-Analyse bestätigt und vom Nutzer freigegeben.
- Vollständige Secret-Rotation + Credential-Failure-Automatik.
- Erste Live-Phase = verlängerte Beobachtung mit reduzierter Größe.

---

## Abhängigkeits-Übersicht

```
Phase 1 ✅ ─▶ Phase 2 ─▶ Phase 3 ─▶ Phase 4 ─▶ Phase 5 ─┬─▶ Phase 6 ┐
                                                        ├─▶ Phase 7 ┤ (parallel zu 8)
                                                        │           │
                                              Phase 5 ──┴─▶ Phase 8 ─▶ Phase 9 ─▶ Phase 10 ─▶ Phase 11 ─▶ Phase 12 ─▶ Phase 13 ─▶ (Phase 14)
```

- Phase 6 (TradingView/Chart) und Phase 7 (Portfolio/Investment) hängen an Phase 5, sind aber
  **parallel zu Phase 8** machbar und **nicht** blockierend für Paper Trading. Phase 12 (Dashboard)
  nutzt 6 + 7 + 11.
- Phasen 9–14 sind strikt sequenziell.

---

## Was in KEINER Phase bis 13 passiert

- Echtgeld-Orders · automatische Echtgeld-Ausführung · echte API-Keys außerhalb von Bybit
  **Testnet** · Live-Broker-Routing · Trade Republic über inoffizielle APIs.
- LLM entscheidet Trades (nur beratender Input mit Guardrails, standardmäßig deaktiviert).
- Automatische Erhöhung von Risiko/Limits/Hebel · Auto-Resume nach Loss-Streak/Drawdown-Stop.
- Positionsgröße aus gewünschtem Gewinn.
- Trade Republic über inoffizielle Schnittstellen.
- Weitere Setup-Typen über `SMC-SWEEP-REV-01` hinaus, bevor dieses OOS validiert ist.
