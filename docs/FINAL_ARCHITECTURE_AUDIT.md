# Final Architecture Audit — Architecture + Data + Execution

**Datum:** 2026-08-28 · **Vor:** Phase 2 (Research / Backtesting)
**Geprüft:** Phase-1-Code (`src/trading_agent/`), `ARCHITECTURE.md`, `FINAL_IMPLEMENTATION_PLAN.md`,
`docs/strategy/*` (`strategy_version 0.1.0`, eingefroren), `docs/ARCHITECTURE_GAP_AUDIT.md`,
`docs/STRATEGY_LOGIC_AUDIT.md`, `config/*.example.yaml`.
**Ziel:** dauerhaft laufendes, autonomes Multi-Asset-AI-Trading-System. Nutzer lädt **keine**
Charts hoch. System empfängt Live-Daten, scannt Tier 1/2/3, analysiert Multi-Timeframe,
erkennt Setups, wartet auf Bestätigung, erzeugt `BUY/SELL/NO_TRADE`-Signale, holt vor Orders
die Freigabe des Nutzers ein.

> **Weiterhin gilt:** kein Live-Trading, keine echten API-Keys, keine Echtgeld-Orders,
> keine automatische Echtgeld-Ausführung.

---

## 0. Gesamturteil

Phase 1 (Data Foundation) ist **solide, getestet und richtig geschichtet**. Die Strategie-Spezifikation
(`0.1.0`) ist vollständig. **Aber:** die bisherige Architektur ist als **lineare "Pipeline pro Bar"**
gedacht — das passt zu einem Backtest, **nicht** zu einem 24/7-Multi-Asset-Daemon mit Dashboard.

**Eine strukturelle Korrektur ist nötig, bevor Phase 2 losgeht:** ein **ereignisgetriebener Kern**
(interner Event-Bus + Supervisor + austauschbare "Driver": Backtest replays Historie, Live reagiert
auf Streams — beide rufen **dieselbe** Strategy Engine). Alles Weitere sind gezielte Ergänzungen,
keine Neuerfindung.

**Nicht** aufgenommen werden (bewusst): Message-Queue-Infrastruktur (Kafka/RabbitMQ),
Microservices, Kubernetes, TimescaleDB/InfluxDB, ein externer Metrics-Server. Begründung: Einzelnutzer-
System → **Monolith-first, ein Prozess, SQLite, In-Process-Metrics, ein FastAPI-Backend**. Distributed
Infra käme erst bei echtem Multi-User-Betrieb in Frage — den gibt es nicht.

---

## 1. Vorhandene Komponenten

### Implementiert & getestet (Phase 1)
| Bereich | Status |
|---|---|
| `core/` — enums, time (UTC/DST/Alignment), clock (Sim/System/Fixed), models (12 frozen Pydantic-Modelle mit `available_time`-PIT-Marker), version | ✅ 100 % nutzbar |
| `config/loader.py` — YAML + Pydantic-Schema, `schema_version`-Pflicht, `config_hash`, `mode=live` abgelehnt | ✅ |
| `utils/logging.py` — JSON-Logging + Secret-Redaction | ✅ |
| `refdata/` — Instrument-Master, Symbol-Mapping, Trading-Calendar (24/7 / weekend_gap / reguläre Börse + Feiertage), DST-sichere Session-Auflösung, Seed (7 Instrumente, alle Assetklassen) | ✅ |
| `data/interfaces.py` — 9 Provider-ABCs (Historical/Live OHLCV, Quote, Trade, Orderbook, Funding, OpenInterest, News, Macro) + `ProviderStatus` | ✅ |
| `data/quality.py` — `check_ohlcv_series` (fehlende/doppelte/unsortierte Bars, ungültige OHLC/Volumen, stale, Timestamp-in-Zukunft, Symbol-/TF-Mismatch, kalenderbewusste Lücken, DST), `blocks_trading` | ✅ |
| `data/resample.py` — M1→…→D1, vollständigkeitsgeprüft, `horizon`-Point-in-Time | ✅ |
| `data/repository.py` — Parquet (OHLCV/Funding) + SQLite (Meta/News/Macro), `as_of`-Reads, `dataset_fingerprint`, Ingestion-Log | ✅ |
| `data/health.py` — `HealthTracker` → HEALTHY/DEGRADED/UNAVAILABLE mit Hysterese | ✅ |
| `data/providers/` — Mock (deterministisch), CSV (UTC-Pflicht, PIT) | ✅ |
| Tests: 193 grün, ruff + mypy strict grün, Coverage 89–100 % je Modul | ✅ |

### Spezifiziert, noch nicht implementiert (Stubs)
`analysis/*`, `strategy/*`, `risk/*`, `execution/*`, `engine/*`, `research/*`, `scanner/*`,
`journal/*`, `safety/*`, `state/*`, `ops/*`, `ai/*`, `allocation/*`, `viz/*` — alle als
Docstring-Platzhalter vorhanden. Die Verträge dafür stehen in `docs/strategy/` und `ARCHITECTURE.md`.

### Bewertung der vorhandenen Schichtung
`refdata → data → analysis → strategy → risk → execution → engine` mit `journal/safety/ops` quer:
**korrekt**. „Eine Strategy Engine für Backtest/Paper/Live": **richtiges Prinzip**, aber noch nicht
strukturell erzwungen (siehe §3).

---

## 2. Fehlende Komponenten

Nummerierung `M-xx`. Nur was **echten Mehrwert** hat.

### A) Ereignisgetriebener Kern & Laufzeit
| ID | Komponente | Warum kritisch |
|---|---|---|
| **M-01** | **`runtime/` — Supervisor + Event-Bus** | Der 24/7-Betrieb braucht einen langlebigen Prozess, der den Event-Loop besitzt, Komponenten an den Bus hängt, Health/Heartbeat/Watchdog führt und sauber herunterfährt. In-Process-Pub/Sub (async), nicht extern. |
| **M-02** | **`runtime/drivers/` — BacktestDriver / LiveDriver** | Der einzige Unterschied zwischen Backtest und Live: der Driver. BacktestDriver spielt historische Bars/News als Events ab (mit `SimClock`), LiveDriver leitet Stream-Events weiter (mit `SystemClock`). Beide speisen **denselben** Bus → **dieselbe** Strategy Engine. Erzwingt Parität. |
| **M-03** | **`runtime/scheduler.py` — Scan-Orchestrierung** | Was löst einen Scan aus? Bar-Close-Events je Instrument/Timeframe. Der Scheduler fächert Scan-Arbeit über das Universum auf, priorisiert Tier 1 (häufiger/mehr Compute), blockiert nicht. |

### B) Live-Daten
| ID | Komponente | Warum kritisch |
|---|---|---|
| **M-04** | **`data/ingestion/` — Live Ingestion Service** | Persistente WS-Verbindungen je Provider, Reconnect + Backoff, REST-Backfill bei Wiederverbindung, Trades→Bars-Aggregation, „laufende Bar", Snapshot-Cache, Gap-Erkennung, Publish `BarClosed`/`QuoteUpdate`/… auf den Bus + Schreiben ins Repository. **Kernstück der Autonomie.** |
| **M-05** | **`data/registry.py` + `data/router.py` — Provider-Auswahl** | `ProviderRegistry`: Fähigkeitsmatrix (welcher Provider liefert welche `DataKind` für welche Assetklasse/Instrumente, LIVE vs HISTORICAL, Tiefe, Kosten, Rate-Limits, Lizenz, Latenz). `ProviderRouter`: Policy → geordnete Provider-Liste + Fallback-Kette. |
| **M-06** | **`net/` — gemeinsamer Client-Layer** | Token-Bucket-Rate-Limiter je Provider, Backoff+Jitter, Circuit Breaker, Request-Signierung (Keys aus Secret-Manager), Server-Zeit-Sync/Drift-Check, Response-Logging (redigiert). Für alle HTTP/WS-Provider **und** später Broker. |
| **M-07** | **`data/aggregator.py` — Bar-Aggregator** | Trades/Ticks → OHLCV; „laufende" (nicht geschlossene) Bar bauen; nur `is_final`-Bars an die Analyse. |
| **M-08** | **`refdata/corporate_actions.py` (heute Stub) — Anwendung** | Backadjustment historischer Aktienkurse (Splits/Dividenden), as-of. Vor dem ersten Aktien-Backtest nötig. |

### C) Strategie / Setup-Verfolgung
| ID | Komponente | Warum kritisch |
|---|---|---|
| **M-09** | **`core/types.py` — `MarketContext`** | Aggregat aller Analyse-Outputs + `data_confidence`/`analysis_confidence`/`regime`. Spezifiziert, noch kein Code. |
| **M-10** | **`scanner/tracker.py` — Setup-Lebenszyklus (persistiert)** | `WATCH → developing → armed → confirmed → expired`, je Instrument, **über Neustarts hinweg**. Das ist „das System beobachtet weiter, während der Nutzer nichts tut". |
| **M-11** | **`scanner/signal_engine.py` — vollständiger `SignalReport`** | Setzt Risiko-Zahlen (braucht Risk Engine + Portfolio) zusammen: Entry/SL/TP1-3, RR, Risiko %/€, Positionsgröße, Margin, dynamischer Hebel, Liquidationsabstand, Score, Confidence, Begründung, Portfolio-Impact, **möglicher Gewinn TP1/TP2/TP3**, **möglicher Verlust bei SL**. |

### D) Risk / Portfolio / Investment
| ID | Komponente | Warum kritisch |
|---|---|---|
| **M-12** | **`risk/veto.py` (aus `strategy/veto.py`) — harte Vetos V1–V10** | Läuft **vor** dem Scoring, nicht überstimmbar. + Emergency-Vetos: Data-Quality-Veto (`blocks_trading` verdrahten), **Broker-Health-Veto**, Manual-Emergency-Stop. |
| **M-13** | **`risk/margin.py` — `MarginModel` (broker-spezifisch)** | Liquidationspreis-Formeln: Bybit Linear-USDT-Perp (isolated/cross, Maintenance-Margin-Tiers), OANDA-FX/Gold-Margin, Aktien-Margin. Abstrakt + je-Broker-Implementierung. |
| **M-14** | **`risk/position_sizing.py` — 12-Schritt-Algorithmus** | Vollständig in `docs/strategy/sizing.md` spezifiziert (Risiko→Größe→Hebel, dynamischer Hebel, `NO_TRADE` statt Kompromiss). Nur noch Code. |
| **M-15** | **`portfolio/` (neues Top-Level) — Accounting + Risiko-State** | Alle Positionen quellenübergreifend (Bybit, manueller Trade-Republic-Import, Cash). Exposure (Notional **und** 1R-Risiko), Korrelation (gemessen + statische Baseline), Cluster-/Klumpenrisiko, Allokation je Assetklasse, Drawdown, Portfolio-Heat, Faktor-Exposure (USD, Zinsen, Equity-Beta, Crypto-Beta, Gold). **Von Trading- UND Investment-Engine gelesen.** `simulate_add(candidate)` für Pre-Trade-Portfolio-Check. |
| **M-16** | **`investment/` (Umbenennung von `allocation/`) — Investment Engine** | Langfrist: `INVEST/WAIT/HOLD/REDUCE` je Instrument/Bucket + Begründung. Input: Monatsbudget (~200–400 €), Portfolio-State, Regime, Langfrist-Chancen, Limits. **Berücksichtigt bestehende Positionen, empfiehlt nicht blind neue Assets. Erzeugt NIE Trade-Signale.** Strikt getrennt von der Trading Engine. |

### E) Execution (simuliert, dann Testnet)
| ID | Komponente | Warum kritisch |
|---|---|---|
| **M-17** | **`execution/simulation.py` — vereinheitlichter Ausführungs-Simulator** | `FillModel` + `CostModel` + `MarginModel` + `LiquidationModel`, **identisch von Backtest UND Paper genutzt**, strukturgleich zum echten Broker. Strategie emittiert `OrderIntent` → Simulator **oder** echter Broker → beide erzeugen `Fill`-Events in gleicher Form. Modelliert: Fees, Spread, Slippage, Funding-Accrual, Partial Fills, Latenz, Liquidation. |
| **M-18** | **`execution/oms.py` — Order Management System (State Machine)** | `NEW→ACK→PARTIAL→FILLED/REJECTED/CANCELLED/EXPIRED`, Timeouts je Übergang, Idempotenz (`client_order_id`/`orderLinkId`), Duplicate-Order-Schutz, Cancel/Replace, Order-Historie. |
| **M-19** | **`execution/reconciliation.py` (heute Stub) — Reconciliation Engine** | Positions- + Balance-Abgleich Broker↔intern, Orphan-Order-Erkennung, Unexpected-Position-Behandlung (→ Alert + Asset-Kill-Switch), periodischer Loop + Startup-Reconcile. |
| **M-20** | **`execution/brokers/` — Adapter-Verträge** | `MarketDataAdapter` (lesen) vs `BrokerAdapter` (handeln) **getrennt**. `SimBroker` für Paper. `BybitPublicDataAdapter` (keine Keys, ab Phase 2). `BybitBrokerAdapter` (Testnet, Phase 9). |

### F) TradingView / Chart / Dashboard-API
| ID | Komponente | Warum kritisch |
|---|---|---|
| **M-21** | **`chart/` (ersetzt `viz/`) — Chart-Annotation-Payloads** | Python **rendert nichts**. `chart/` erzeugt strukturierte Annotationen: `Marker` (BUY/SELL), `PriceLine` (Entry/SL/TP1-3), `Zone/Box` (FVG, IFVG, OB, Breaker, Premium/Discount, Setup-Zone), `TrendLine` (BOS/CHoCH), `Point` (Swing/Equal H-L), `SessionBand`, `Label` (Setup-ID, Strategy-Version). |
| **M-22** | **`api/` — FastAPI-Backend (Phase 12)** | REST + WebSocket: Scanner-State, Signale, Portfolio, Risk, Positionen, Performance, Journal, News, AI-Reasoning, Alerts, System-Health, Execution-Status **und** die Chart-Datafeed-/Annotations-API + Live-Update-Stream. **Approval-Endpoint** („BUY bestätigen?"). |

### G) AI
| ID | Komponente | Warum kritisch |
|---|---|---|
| **M-23** | **`ai/reasoning/` — Text-Ausgabe, gated NICHTS** | Zusammenfassungen (News, Research), Begründungstexte für Signale, Interpretation unstrukturierter Infos. Output ist **immer nur Text**, geht ins Ledger/Dashboard, beeinflusst keine Entscheidung. |
| **M-24** | **`ai/advisor/` — optionaler, eng begrenzter Score-Modulator** | Läuft **strikt nach** No-Trade-Check + Regime-Gate + Ketten-Gates + Vetos. Schema-validierte Ausgabe. Darf den Score nur um ≤ `ai_guardrails.max_score_modulation_points` bewegen, **nie** ein Gate/Veto aufheben, **nie** eine Stufe nach oben erzwingen. **Default: AUS.** Timeout/Schemafehler → neutral, System läuft regelbasiert weiter. |
| **M-25** | **`ai/contract.py` — Schema + Guardrail-Enforcement** | JSON-Schema (Pydantic) je LLM-Call, Prompt-Versionierung, Modell-ID-Pinning (`temperature=0`), jede Anfrage/Antwort ins Audit-Log + Ledger. |

### H) Observability / Recovery / Security
| ID | Komponente | Warum kritisch |
|---|---|---|
| **M-26** | **`ops/metrics.py` — In-Process-Metrics-Registry** | Counter/Gauge/Histogram, snapshot-bar fürs Dashboard: `bars_ingested_total{provider,instrument}`, `provider_latency_ms{provider}`, `scan_duration_seconds{tier}`, `signals_emitted_total{decision,tier}`, `no_trade_reasons_total{reason}`, `open_positions`, `portfolio_heat_pct`, `equity`. |
| **M-27** | **`journal/decision_ledger.py` — kanonischer Ereignis-Trace** | Append-only (SQLite): `DATA_SNAPSHOT → ANALYSIS → SETUP_STATE_CHANGE → SCORE → RISK_DECISION → SIGNAL → APPROVAL → ORDER → FILL → MANAGEMENT_ACTION → EXIT`. Jeder Eintrag: `trace_id`, Zeit, Versionen (code-SHA / strategy_version / config_hash / dataset_version), Payload. **Auch `NO_TRADE` mit Gründen.** |
| **M-28** | **`utils/tracing.py` — Correlation-/Trace-ID-Propagation** | `contextvars`-basiert; eine Kette Scan→Setup→Signal→Approval→Order→Fill teilt eine `trace_id`; jede Log-Zeile + jeder Ledger-Eintrag trägt sie. |
| **M-29** | **`ops/health.py` — `SystemHealth`-Aggregat** | Kombiniert Provider-Health + Broker-Health + Data-Quality + Kill-Switch-State + letzter Heartbeat + Fehlerquoten. Speist Dashboard-Panel **und** den Broker-/Data-Health-Veto. |
| **M-30** | **`ops/watchdog.py` — Heartbeat + Watchdog** | Daemon schreibt Heartbeat; stockt der Event-Loop → Watchdog löst Kill-Switch (global) aus. |
| **M-31** | **`state/` — konkreter Persistenz-/Recovery-Entwurf** | SQLite `state.sqlite`: offene Positionen/Orders + Lifecycle-States, Kill-Switch-State je Ebene, Tages-/Wochen-Verlustzähler + Reset-Zeit, Equity-Hoch, `last_processed_bar_close` je Instrument, Scanner-Tracker-State, Signale-in-Approval, laufende Versionen. Fail-safe-Startsequenz + Reconcile + Gap-Backfill + Graceful Shutdown. |
| **M-32** | **`security/` — Secrets + Policy** | `secrets.py` (Abstraktion: Env → OS-Keychain → später Vault; loggt nie; Demo/Live-Credentials getrennt), Startup-Check „kein Secret in geladener Config". |
| **M-33** | **`docs/SECURITY.md`** | Threat-Model, Key-Handling (trade-only, **nie** Withdrawal, IP-Allowlist), Demo/Live-Trennung, Incident-Response, Audit-Log. |
| **M-34** | **`safety/audit_log.py` (heute Stub) — Hash-Chain** | Append-only, integritätsgesichert: Config-Änderungen, Key-Nutzung, Order-Submits, Kill-Switch-Events, manuelle Overrides, Approvals. |
| **M-35** | **`.pre-commit-config.yaml` + CI-Workflow** | gitleaks + ruff + mypy + `pip-audit` + `pytest`. Harmlos ohne git — bereit, sobald `git init` läuft. |

---

## 3. Architekturänderungen

Nummerierung `C-xx`. Diese ändern die bestehende Planung.

| ID | Änderung | Begründung |
|---|---|---|
| **C-01** | **Von "linearer Pipeline pro Bar" zu ereignisgetriebenem Kern.** `engine/pipeline.py` wird ersetzt durch `runtime/` (Bus + Supervisor) + `runtime/drivers/` (Backtest vs Live). Die Strategy Engine bleibt eine reine `evaluate(MarketContext) -> Decision`-Funktion, aufgerufen von einem Event-Handler. | Nur so passt „24/7 Multi-Asset autonom" **und** „gleiche Engine im Backtest" zusammen. |
| **C-02** | **`viz/` → `chart/`.** Python erzeugt Annotationen (Daten), nicht Grafik. Rendering ist Frontend-Sache. | Beseitigt die Fehlannahme „Python zeichnet". |
| **C-03** | **`allocation/` → `investment/`.** Plus neues `portfolio/` (Accounting, geteilt). Trennung: `portfolio/` (State) ↔ `investment/` (Langfrist-Empfehlungen) ↔ `scanner/`+`strategy/`+`risk/` (Trading). | Nutzer-Anforderung „Investment Engine und Trading Engine getrennt". |
| **C-04** | **`ai/` wird in zwei Flächen geteilt:** `ai/reasoning/` (nur Text, gated nichts) + `ai/advisor/` (optionaler, begrenzter Score-Modulator, Default AUS). | Klare Trennung „darf beschreiben" vs „darf minimal mitwirken". Kein LLM im harten Entscheidungspfad. |
| **C-05** | **Neuer `net/`-Layer** unter allen HTTP/WS-Providern **und** Brokern (Rate-Limit, Retry, Circuit Breaker, Signing, Zeit-Sync). | Ein Ort für API-Zuverlässigkeit statt N Kopien. |
| **C-06** | **`data/` bekommt `ingestion/`, `registry.py`, `router.py`, `aggregator.py`.** Provider-ABCs bleiben; darüber kommt Auswahl-Policy + Live-Service. | Multi-Provider-Anforderung; Autonomie. |
| **C-07** | **`execution/` bekommt `simulation.py` (Fill/Cost/Margin/Liquidation) und `oms.py` (Lifecycle-State-Machine).** `pipeline`-Fill-Logik entfällt. Backtest + Paper nutzen `simulation.py`; Live nutzt `BrokerAdapter`; **gleiche `Fill`-Events**. | Erzwingt „Backtest = Paper = Live" auf der Ausführungsseite. |
| **C-08** | **`api/` (FastAPI) als eigenes Paket** (Phase 12). Alle Dashboard-Panels + Chart-Datafeed + WS-Live-Updates + **Approval-Endpoint** dahinter. Frontend ist ein separates Projekt (nicht Python). | „Eigene professionelle Trading-App" braucht eine stabile Backend-API. |
| **C-09** | **`journal/decision_ledger.py` wird der zentrale Ereignis-Trace** (nicht nur Trades). `ops/metrics.py`, `utils/tracing.py`, `ops/health.py`, `ops/watchdog.py` neu. | „Jeder Schritt geloggt/versioniert" + Dashboard-Health. |
| **C-10** | **`state/` mit konkretem Schema + Startup-/Reconcile-/Shutdown-Sequenz** (Abschnitt 13 unten). | Recovery war nur „Stub". |
| **C-11** | **Bybit-**public-data**-Adapter schon in Phase 2** (keine Keys, read-only) — für echte BTC-Historie + Live-Kostprobe. Account/Trading-Adapter bleibt Phase 9 (Testnet). | Ohne echte Daten ist Phase 2 gehaltlos. Public Market Data ≠ Account-Anbindung. |
| **C-12** | **Phasen-Neunummerierung: Dashboard = Phase 12** (Nutzer-Vorgabe). Production Readiness → 13, Live → 14. | Nutzer-Vorgabe. |
| **C-13** | **`refdata` = bekanntes Universum; `config` = aktive Teilmenge je Tier; `scanner` liest die aktive Teilmenge.** Neues `refdata/sync/` (Struktur jetzt, Provider-Impl. je Provider später). | Klarer Universums-Begriff; Instrumentspezifikationen kommen später aus Provider-APIs statt Hardcode. |
| **C-14** | **`store/`-Sicht auf SQLite** (typisierte Repos: MarketDataStore, EventStore, JournalStore, StateStore, geteilter Connection-Pool). `data/repository.py` bleibt, wird eingeordnet. | Sauberere Grenzen, wenn Ledger/State/Journal wachsen. Keine neue Technologie. |

**Was NICHT geändert wird:** das Schichtprinzip, Pydantic-v2 + dataclasses, Parquet+SQLite,
`SimClock`/`SystemClock`, die eingefrorene Strategie-Spezifikation `0.1.0`, die
Risiko→Größe→Hebel-Reihenfolge, die Veto-Hierarchie, die 14 Nutzer-Entscheidungen.

---

## 4. Empfohlene Datenquellen

Auswahl nach: Datenqualität · Latenz · historische Tiefe · Kosten · API-Zuverlässigkeit ·
Lizenzierung · Rate-Limits · Bid/Ask · Trades · Orderbook · Funding · OI · Corporate Actions ·
News/Macro. **Startstrategie: überall Free/Demo, wo möglich; einen bezahlten Aktien-Feed erst, wenn
Aktien aktiv werden (Phase 7+).**

### Crypto — Tier 1 (BTC), Tier 2 (Altcoins)
| Rolle | Quelle | Warum |
|---|---|---|
| **Primär, live + historisch** | **Bybit v5 API** (REST + WS, kein Key für Market Data) | Wir handeln dort → **Daten-/Ausführungs-Parität**. Native klines, **Funding-Historie, Open Interest, Liquidations**, Orderbook L2, Trades, `instruments-info`, Announcements (Listings/Delistings/Wartung). Großzügige Limits, Testnet vorhanden, keine Kosten. |
| **Sekundär / Cross-Check + Marktkontext** | **Binance** (öffentliche Market Data, kein Key) | Tiefste Liquidität; Alts, die nicht auf Bybit sind; **BTC-Dominanz / Market-Cap-Kontext** über zusätzliche Endpunkte. Nur zur Validierung / für Nicht-Bybit-Assets. |
| **Optional später (institutionell)** | Kaiko / CoinAPI / CryptoCompare (paid) | Nur falls Cross-Exchange-Normalisierung + tiefe konsistente Historie wichtig werden. **Nicht für MVP.** |
| **Unlocks / On-Chain-Events** | Token-Unlock-Kalender (als **News**-Provider) | Behandelt als News/Event, nicht Market Data. Später. |

### Gold / XAUUSD — Tier 1
| Rolle | Quelle | Warum |
|---|---|---|
| **Primär, live + jüngere Historie** | **OANDA v20 API** (`XAU_USD`, REST + Streaming, Practice-Konto gratis) | Sauberes Bid/Ask-Streaming, handelbare Spreads, gute Doku, **deckt zusätzlich Forex ab** (eine Integration für Gold + FX). Kein einzelner „Exchange" für Spot-Gold — ein Broker-Feed ist die richtige Wahl. |
| **Tiefe Backtest-Historie** | **Dukascopy** (freie historische Tick-Daten XAUUSD + FX) | Sehr lange Historie, Tick-Auflösung — ideal für Backtests / Slippage-Modellierung. |
| **Fallback / Alternative** | Polygon.io (Forex/Metals) oder Twelve Data | Falls schon ein Polygon-Abo für Aktien existiert, deckt es XAU/USD mit ab. |
| **Makro-Treiber** (DXY, US-Realzinsen, Fed-Erwartungen) | **FRED / ALFRED** (St. Louis Fed, gratis) | **Vintage/First-Release-Werte = echtes Point-in-Time** für Backtests. Autoritativ für US-Makro-Zeitreihen. |

### US-Aktien — Tier 2 (ausgewählte liquide), ETFs — Tier 3
| Rolle | Quelle | Warum |
|---|---|---|
| **Primär: Daten + Corporate Actions + Historie in einem** | **Polygon.io** (Starter/Developer-Tier) | Real-time + historisch (Trades, Quotes, Aggregates, 15+ Jahre), **Corporate-Actions-API** (Splits, Dividenden, Ticker-Changes), Reference Data, WS-Streaming, solide Zuverlässigkeit, faire Limits. Ein Anbieter für fast alles. |
| **Sekundär / ausführungsnah** | **Alpaca Market Data** (Free IEX / Paid SIP) | Falls später Alpaca als Ausführungs-Broker für US-Aktien dazukommt: integriert, mit Corporate Actions. Free-Tier zum Start. |
| **Nur bei Microstructure-Bedarf** | Databento (pay-as-you-go, MBO/MBP-Orderbuch) | Institutionelle Qualität, teurer. Erst wenn Orderbuch-Mikrostruktur zentral wird. |
| **Halts / LULD, Earnings-Kalender** | Polygon (teilweise) + Finnhub (Earnings-Kalender) | |
| **Vermeiden** | IEX Cloud (eingestellt/geändert) | |

### Forex — Tier 3 (vorbereiten)
| Rolle | Quelle |
|---|---|
| **Primär** | **OANDA v20** (dieselbe Integration wie Gold) |
| **Tiefe Historie** | **Dukascopy** (Tick) |
| **Alternative** | Polygon.io Forex (falls Abo vorhanden) |
> Implementierung aufgeschoben (Tier 3), aber Provider-Adapter-Vertrag jetzt vorbereitet.

### News / Makro — separate Abstraktion, strikt Point-in-Time
| Rolle | Quelle | Warum |
|---|---|---|
| **Wirtschaftskalender** (CPI, NFP, FOMC, PCE, GDP, PMI, Zentralbank-Entscheide/Reden) | **Finnhub** (Free-Tier, breit) **oder** **Trading Economics API** (umfassender, paid) | Zeitplan + actual/forecast/previous + Revisionen. Start mit Finnhub-Free; Trading Economics nur falls Abdeckung nicht reicht. |
| **Point-in-Time-Makro-Zeitreihen** (Yields, DXY-Inputs, CPI-Historie) | **FRED / ALFRED** (gratis) | ALFRED liefert **First-Release-Werte** → Backtests nutzen nie revidierte Zahlen. |
| **Unternehmensnews / Headlines (Aktien)** | **Finnhub** (company news) + optional **Benzinga** (premium, niedrige Latenz) | |
| **Crypto-spezifische News** | **Bybit + Binance Announcement APIs** (Listings/Delistings/Wartung) + **CryptoPanic** | Announcement-APIs sind offiziell und strukturiert. |
| **Geopolitik / unplanmäßig** | manueller `risk_off`-Flag + (später) LLM-Headline-Monitor | Keine harte Abhängigkeit. |
| **Vermeiden** | Forex Factory / investing.com Scraping (keine offizielle API) | |

### Lizenz-Hinweis (im `ProviderRegistry` als Flag führen)
Polygon, Databento, Exchange-Daten beschränken teils die **Weiterverteilung**. Ein Chart für den
**Einzelnutzer/Eigentümer** ist i. d. R. „internal use" und zulässig. Wird die App je Multi-User/
öffentlich, muss das neu geprüft werden. `ProviderRegistry.redistribution_allowed` + die
Chart-/Datafeed-API dürfen rohe Ticks aus weiterverteilungs-beschränkten Quellen nur an den
Eigentümer-Kontext geben.

---

## 5. Empfohlene Integrationen

| Integration | Mechanismus | Phase | Hinweis |
|---|---|---|---|
| **Bybit Market Data** | v5 REST + public WS, `net/`-Client, kein Key | **2** | read-only, kein Account |
| **OANDA v20** (Gold + FX) | v20 REST + Streaming, Practice-Token | **3–4** | Practice-Konto, keine Echtgeld-Funktion genutzt |
| **Dukascopy** (Backtest-Historie Gold/FX) | Historischer Datei-/Tick-Download | **2–3** | einmaliger Import ins Repository |
| **FRED / ALFRED** (Makro PIT) | REST, API-Key gratis | **3** | vintage data = Point-in-Time |
| **Finnhub** (Kalender + News) | REST, Free-Tier-Key | **3** | über News-Provider-Abstraktion |
| **Polygon.io** (US-Aktien + Corporate Actions) | REST + WS, bezahltes Tier | **7** | erst wenn Aktien aktiv |
| **Bybit Testnet** (Account/Trading) | v5 private REST + WS, Testnet-Keys (trade-only, kein Withdrawal) | **9** | Secrets nur via Secret-Manager |
| **TradingView-Chart** | **Frontend-Komponente**, gespeist von unserer Datafeed-/Annotations-API | **6/12** | siehe §6 |
| **Alerts** (Signal an Nutzer) | `ops/notify`: Konsole/Datei → später **Telegram Bot API** / Webhook / Web-Push | **5** | offizielle APIs, kein Scraping |
| **Trade Republic** | **nur manueller Portfolio-Import** (CSV/Statement) | **7** | **keine** inoffizielle API (ToS/Sperre-Risiko) — read-only, `source=manual` |

---

## 6. TradingView-Architektur (Prüfergebnis)

**Grundsatz bestätigt:** unsere Data Engine liefert Daten, unsere Strategy Engine analysiert,
**TradingView visualisiert nur**. Keine Browser-Automation, keine Mouse/Click-Automation, kein
Pine-Script als Datenquelle, keine Webhooks *in* tradingview.com.

**Integrationsmechanismus:**
1. **TradingView Charting Library / Advanced Charts** — offizielle, lizenzierte JS-Komponente, die
   in **unsere** Web-App eingebettet wird. Sie konsumiert Daten über eine **Datafeed-API**, die
   **wir** implementieren (JS-Interface → unser Backend REST/WS). Zeichnungen über die
   Shape-API der Library (`createShape`, `createMultipointShape`).
   *Realität:* Zugang ist **gated** (kostenlose Registrierung + Lizenzvereinbarung bei
   tradingview.com/charting-library); Library-Dateien dürfen nicht öffentlich weiterverteilt werden.
2. **Lightweight Charts** (Apache-2.0, **ungated**, frei einbettbar) — Candles + Line-Series +
   Marker + Price-Lines + (ab v4) Primitives für eigene Zeichnungen.

**Empfehlung (C-02 + M-21 + M-22):**
- Frontend definiert ein **`ChartAdapter`-Interface**. Backend liefert **`ChartDataAPI`** (Datafeed)
  + **`ChartAnnotationsAPI`** (Overlays) + **WS-Update-Stream** (`annotation_added/updated/removed`,
  `bar`).
- **Zuerst mit Lightweight Charts** (keine Lizenz-Reibung, sofort lieferbar). **Später** Umstieg auf
  die TradingView Charting Library möglich — **gleiche Backend-APIs**. Das entkoppelt Phase 6/12
  vom TradingView-Lizenzprozess.
- **Backend-Annotation-Contract jetzt definieren** (in `chart/`), damit die Signal Engine (Phase 5)
  die richtigen Formen erzeugt: BUY/SELL · Entry · SL · TP1/2/3 · Liquidity · FVG · IFVG · Order
  Blocks · BOS · CHoCH · Swing H/L · Equal H/L · Premium/Discount · Sessions · Setup-Zonen ·
  Setup-ID · Strategy-Version. **Live-Aktualisierung**, wenn sich der Setup-State ändert
  (`scanner/tracker.py` publiziert → `chart/` diff't → WS an Frontend).

---

## 7. Bybit-Integration (Prüfergebnis)

| Aspekt | Entscheidung |
|---|---|
| **API** | Bybit **v5 unified** (REST + WS public/private). |
| **Phase 2** | `BybitPublicDataAdapter` — klines, funding-history, open-interest, orderbook, trades, tickers, `instruments-info`, announcements. **Keine Keys, kein Account.** Liefert echte BTC/ETH-Historie für Backtests + erste Live-Kostprobe. |
| **Phase 9** | `BybitBrokerAdapter` — **Testnet**, Keys (trade-only, **kein** Withdrawal), place/cancel/amend Orders, Positions, Wallet, Executions; private WS (`position`, `order`, `execution`, `wallet`). |
| **Client** (`net/` + `brokers/bybit/_client.py`) | HMAC-Signierung, per-Endpoint- + IP-Rate-Limits, `recv_window`, Server-Zeit-Sync (`/v5/market/time`) für Clock-Drift, WS-Auth + Heartbeat + Resubscribe bei Reconnect. |
| **Reconciliation** | private WS + periodischer REST-Snapshot-Abgleich. Orphan-Order = Order auf Bybit ohne OMS-Eintrag → Policy (adopt/cancel). Unexpected Position → Alert + Asset-Kill-Switch. Balance-Mismatch > Toleranz → Alert. |
| **Margin / Leverage** | Linear-USDT-Perp, **isolated margin** (MVP, begrenzter Verlust je Position), one-way position mode. Leverage je Symbol gesetzt aus dem **dynamisch berechneten** Wert (`risk/position_sizing.py`). Liquidationspreis aus Bybit-Formel (Maintenance-Margin-Tier). |
| **Idempotenz** | `orderLinkId` (Client-Order-ID) für Dedup + retry-sichere Wiederholung. |
| **Phase 14** | Mainnet, **separate** Keys, weiterhin trade-only-no-withdrawal, IP-Allowlist. |

---

## 8. Portfolio / Capital Allocation (Prüfergebnis)

**Strikte Dreiteilung (C-03):**

```
                 ┌───────────────────────────────────────────────┐
                 │  portfolio/  (Accounting + Risiko-State)       │
                 │  - alle Positionen (Bybit, manuell TR, Cash)   │
                 │  - Exposure (Notional + 1R-Risiko)             │
                 │  - Korrelation (gemessen + statische Baseline) │
                 │  - Cluster / Klumpenrisiko                     │
                 │  - Allokation je Assetklasse, Drawdown, Heat   │
                 │  - Faktor-Exposure (USD, Zinsen, Beta, ...)    │
                 │  - simulate_add(candidate) -> PortfolioState   │
                 └───────────────┬───────────────┬───────────────┘
                        liest    │               │   liest
                 ┌───────────────▼──────┐   ┌────▼─────────────────────────┐
                 │  investment/         │   │  scanner/ + strategy/ + risk/ │
                 │  (Langfrist)         │   │  (Trading, 24/7 taktisch)     │
                 │  INVEST/WAIT/HOLD/   │   │  BUY / SELL / NO_TRADE        │
                 │  REDUCE + Begründung │   │  je-Trade-Risiko-Sizing       │
                 │  Monatsbudget 200–400│   │  Nutzer-Freigabe vor Order    │
                 │  € · bestehende Pos. │   │                              │
                 │  berücksichtigen ·   │   │                              │
                 │  KEINE Trade-Signale │   │                              │
                 └──────────────────────┘   └──────────────────────────────┘
```

- **`portfolio/` ist die einzige Quelle der Wahrheit** für Positionen/Exposure — von beiden
  Engines gelesen, von keiner „nebenbei" nachgerechnet.
- **`investment/` erzeugt nie Trade-Signale.** Ausgabe = Empfehlung, die der Nutzer umsetzt
  (später ggf. ein separater langsamer Ausführungspfad — **nicht** der Scanner).
- **Trade Republic:** nur manueller Import ins `portfolio/` (`source=manual`, read-only). Keine
  inoffizielle API.
- **Bybit-Portfolio:** ab Phase 9 via private API; vorher manuell.

---

## 9. Risk / Sizing / Leverage (Prüfergebnis)

**Die Spezifikation (`docs/strategy/sizing.md`) ist korrekt und deckt sich mit den erneuerten
Anforderungen** (Equity → erlaubtes Risiko → SL-Distanz → Positionsgröße → Margin → zulässiger
Hebel; kein Hebel-Cap als Strategieparameter; dynamischer Hebel; `NO_TRADE` wenn nicht sicher
handelbar; kein Martingale / keine Verlustprogression; LLM überschreibt kein Veto). **Keine
Spezifikationsänderung nötig** — nur Implementierung + eine Klarstellung:

### ⚠️ Klarstellung: „50 € Equity → 200–300 € Position"
Bei **konservativem Risiko pro Trade** (Default A+/A/B = 0,50 / 0,35 / 0,25 %) und **realistischen
SMC-Stop-Distanzen** (Crypto ~0,5–2 % vom Preis) ergibt sich:

```
Risiko = 0,35 % × 50 €               = 0,175 €
Positionsgröße (Notional)            = 0,175 € / SL-Distanz
   bei SL 1,5 %:   0,175 / 0,015     ≈  11,7 € Notional
```

Eine **200–300 €-Position** bei 50 € Equity und 0,175 € Risiko bräuchte eine SL-Distanz von
~0,09 % — die vom **Mindest-SL-Abstand** abgelehnt wird. **Der Hebel ändert das nicht:** Hebel
senkt nur die nötige Margin, er darf die risiko-bestimmte Größe nicht vergrößern.

**Korrektes Systemverhalten:** Das System rechnet die risiko-korrekte Größe und den dafür nötigen
Hebel. Größere Positionen sind nur möglich, wenn der Nutzer `risk_pct` in der Config **bewusst**
erhöht (geloggte Entscheidung) — und die Risk Engine setzt weiterhin Tages-/Wochen-/Drawdown-/
Exposure-Grenzen durch. **Das System bläht die Größe niemals auf ein Notional-Ziel auf.**

### ⚠️ Mindest-Ordergröße bei Kleinkonten
Bybit BTC-Perp Mindest-Lot ≈ 0,001 BTC (~60 USDT Notional). Mit 50 € Equity und 0,35 % Risiko
liegt die risiko-korrekte BTC-Größe **unter dem Mindest-Lot** → `NO_TRADE (SIZE_BELOW_MIN)`.
**Tier-1 XAUUSD und BTC sind mit einem 50-€-Konto ggf. gar nicht handelbar.** Das System muss das
**klar melden** (Signal `NO_TRADE` mit Grund + Dashboard-Hinweis „Konto zu klein für dieses
Instrument"), nicht still nie signalisieren.

### Fehlend im Code/Design (bereits in §2 gelistet)
- `risk/margin.py` — broker-spezifische Liquidationsformeln (M-13)
- `risk/veto.py` — V1–V10 + Emergency-Vetos (Data-Quality, Broker-Health, Manual-Stop) (M-12)
- `portfolio/.simulate_add()` — Pre-Trade-Portfolio-Check (M-15)
- Cross vs isolated Margin — **isolated** für MVP, konfigurierbar

---

## 10. Backtest-Architektur (Prüfergebnis)

**Spezifikation (`backtest-labeling.md`, `anti-overfitting.md`) ist gründlich.** Ergänzungen:

| Punkt | Status |
|---|---|
| Fees, Spread, Slippage, Funding, Partial Fills, Latency, Sizing, Leverage, Margin, Liquidation realistisch | in `backtest-labeling.md` spezifiziert → **`execution/simulation.py`** (M-17) implementiert es einmal für Backtest **und** Paper |
| In-Sample / Out-of-Sample / Walk-Forward / Purge-Embargo / Monte-Carlo / Parameter-Sensitivity / **Regime-Stability** | in `anti-overfitting.md` |
| **Time-Stability** (Performance über verschiedene Zeitperioden stabil) | **NEU ergänzen** in `anti-overfitting.md` §4/§5 als eigene Validierungsachse |
| **Symbol-Stability** (Edge nicht von einem einzelnen Instrument getragen) | **NEU ergänzen** in `anti-overfitting.md` |
| Keine Optimierung auf OOS · keine Look-ahead-Daten · keine Survivorship-Bias | Regeln stehen; **Tests** dafür kommen in Phase 2 (Look-ahead: `information_cutoff`; Survivorship: `Instrument.is_tradeable_at`; PIT: `repository.read(as_of=…)` — Bausteine aus Phase 1 vorhanden) |
| **Backtest = Paper = Live über den Event-Bus** | C-01/C-07: BacktestDriver spielt historische Events ab, LiveDriver reale — **dieselben** Subscriber (strategy/risk/portfolio). Das ist die stärkste Paritäts-Garantie. |
| Reproduzierbarkeit | `RunManifest` (`research/registry.py`): `code_sha` (sobald git), `config_hash` ✅, `dataset_version` + `dataset_fingerprint` ✅, `seed`, Zeitraum, `strategy_version`. |
| **Echte Daten** | Phase 1 hat nur Mock. **Phase 2 muss** echte BTC/ETH-Historie (Bybit public) + Gold-Historie (OANDA/Dukascopy) ins Repository laden. |

---

## 11. Security (Prüfergebnis)

| Anforderung | Status | Maßnahme |
|---|---|---|
| Keine Secrets im Code | ✅ | Phase-1-Code hat keinen Key-Lesecode; `.env.example` nur Platzhalter |
| API-Keys nur über Secret-Management | ❌ fehlt | **M-32** `security/secrets.py` (Env → OS-Keychain → später Vault) |
| Minimale API-Rechte, nie Withdrawal | ❌ Policy fehlt | **M-33** `docs/SECURITY.md` |
| Getrennte Demo/Live-Credentials | ❌ | in `security/secrets.py` erzwingen (`env=demo|live` getrennte Namespaces) |
| Keine Keys loggen | ✅ (Ansatz) | Redaction in `utils/logging.py` vorhanden — zentralisieren, Test „Broker-Response-Body wird nie roh geloggt" |
| Audit-Log | ❌ Stub | **M-34** `safety/audit_log.py` Hash-Chain |
| Secret-Scanning / SBOM / Dependency-Audit | ❌ (kein git) | **M-35** `.pre-commit-config.yaml` (gitleaks) + CI (`pip-audit`) — bereit sobald `git init` |

---

## 12. Observability (Prüfergebnis)

| Anforderung | Status | Maßnahme |
|---|---|---|
| Strukturiertes Logging | ✅ | JSON + Redaction vorhanden |
| Trace über den ganzen Lebenszyklus (DATA→…→EXIT) | ❌ | **M-27** Decision Ledger als Ereignis-Trace + **M-28** `trace_id`-Propagation (`contextvars`) |
| Metriken | ❌ | **M-26** `ops/metrics.py` (In-Process Counter/Gauge/Histogram) |
| System-Health-Aggregat | ⚠️ nur Provider-Health | **M-29** `ops/health.py` (Provider + Broker + Data-Quality + Kill-Switch + Heartbeat) |
| Heartbeat / Watchdog | ❌ | **M-30** `ops/watchdog.py` → Kill-Switch bei Loop-Stall |
| Alerts an den Nutzer | ❌ Stub | `ops/notify.py`: Konsole/Datei → Telegram/Webhook/Push |
| Jeder Schritt versioniert | ⚠️ teils | code-SHA fehlt bis git; `config_hash`/`dataset_fingerprint`/`strategy_version` vorhanden |

---

## 13. Recovery / Reconciliation (Prüfergebnis)

**`state/` war nur ein Stub. Konkreter Entwurf (M-31):**

**Persistierter State** (`data/state/state.sqlite`):
- offene Positionen (unsere Sicht), offene Orders + OMS-Lifecycle-States
- Kill-Switch-State je Ebene (`global` / `broker` / `asset` / `strategy` / `data`)
- Tages-/Wochen-realisierter+offener Verlust + Reset-Zeitpunkte
- Equity-Hochwassermarke (für Drawdown)
- `last_processed_bar_close` je Instrument/Timeframe (Gap-Backfill beim Neustart)
- Scanner-Setup-Tracker-State je Instrument (State-Machine)
- Signale, die auf Nutzer-Freigabe warten
- laufende Versionen (`code_sha`, `strategy_version`, `config_hash`)

**Fail-safe-Startsequenz:**
1. Config laden, Schema + Hashes prüfen.
2. Persistierten State laden.
3. **Kill-Switch = ENGAGED**, bis Reconciliation fertig.
4. *(demo/live)* Broker-Reconcile: Positions/Orders/Balance holen, gegen persistierten State
   diffen. Orphan-Orders → Policy (cancel/adopt). Unexpected Position → Alert + Asset-Kill-Switch
   bleibt. Balance-Mismatch > Toleranz → Alert.
5. Marktdaten-Lücken backfillen (`last_processed_bar_close` → jetzt, via REST).
6. Portfolio-State neu berechnen.
7. Nur wenn alles sauber und Policy/Nutzer freigibt → Kill-Switch lösen.

**Laufender Reconcile-Loop** (demo/live): alle N s Broker-Snapshot, diffen, bei Drift Alert,
Auto-Korrektur nur nach expliziten Regeln.

**Graceful Shutdown:** keine neuen Signale annehmen, offene Orders settlen/canceln nach Policy,
State + Ledger flushen, WS sauber schließen.

**Crash-Recovery:** identisch zur Startsequenz; Watchdog + externer Supervisor
(systemd / pm2 / Docker-Restart-Policy) bringen den Prozess zurück.

---

## 14. Risiken

| # | Risiko | Schwere | Gegenmaßnahme |
|---|---|---|---|
| R-1 | **Kleinkonto (50 €) kann Tier-1 (BTC/XAUUSD) gar nicht handeln** (Mindest-Lot) | hoch (Erwartung ≠ Realität) | System meldet `NO_TRADE (SIZE_BELOW_MIN)` + Dashboard-Hinweis; Nutzer wählt kleinere Instrumente oder erhöht Equity |
| R-2 | **Erwartung „große gehebelte Positionen"** kollidiert mit kontrolliertem Risiko | mittel | §9-Klarstellung; größere Positionen nur per bewusster `risk_pct`-Erhöhung, weiterhin unter allen Limits |
| R-3 | **Provider-Ausfall im Live-Betrieb** | hoch | Multi-Provider + Router-Fallback + Broker-/Data-Health-Veto → `NO_TRADE` statt Blindflug |
| R-4 | **TradingView-Lizenzprozess blockiert Phase 6** | mittel | zuerst Lightweight Charts (ungated); gleiche Backend-API |
| R-5 | **Look-ahead / Survivorship / OOS-Overfitting im Backtest** | hoch | Event-Bus-Parität, `information_cutoff`-Test, `is_tradeable_at`, `as_of`-Reads, frozen ruleset, Time-/Symbol-Stability-Achsen |
| R-6 | **LLM „rutscht" in den Entscheidungspfad** | hoch | zwei getrennte AI-Flächen; Advisor läuft nach Vetos, ≤ N Punkte, Default AUS; Contract-Tests |
| R-7 | **State-Drift Broker↔intern** (verpasster Fill, Neustart) | hoch (ab Demo) | Reconciliation-Engine + fail-safe Start + `orderLinkId`-Idempotenz |
| R-8 | **Trade-Republic-Sperre durch inoffizielle API** | hoch | **nur** manueller Import, read-only |
| R-9 | **Kosten laufender bezahlter Feeds** | niedrig–mittel | Free/Demo zuerst; `data.cost_budget` im Router; Polygon erst Phase 7 |
| R-10 | **Komplexitäts-Explosion** (zu viele Komponenten) | mittel | Monolith-first, ein Prozess, SQLite; jede Komponente in §2 auf einen konkreten Bedarf abgebildet; keine Distributed Infra |
| R-11 | **`git` fehlt → keine Versionskontrolle, kein `code_sha`, keine pre-commit-Hooks** | mittel | Phase-0-Rest: `xcode-select --install` **oder** `brew install git`, dann `git init` + Hooks — **vor** Phase 2 |
| R-12 | **Datenlizenz-Weiterverteilung** (Chart an Dritte) | niedrig (Einzelnutzer) | `redistribution_allowed`-Flag; Chart-API nur Eigentümer-Kontext; bei Multi-User neu prüfen |

---

## 15. Technische Entscheidungen

| # | Entscheidung | Begründung |
|---|---|---|
| T-1 | **Ereignisgetriebener Monolith, ein Prozess, `asyncio`** | Einzelnutzer; kein Bedarf an Distributed Infra; einfachste zuverlässige Lösung |
| T-2 | **Interner In-Process-Event-Bus** (`runtime/bus.py`), später bei Bedarf austauschbar | entkoppelt Ingestion / Analyse / Scanner / Execution / API; Backtest = anderer Event-Producer |
| T-3 | **BacktestDriver vs LiveDriver, gleiche Subscriber** | einzige belastbare Garantie für „Backtest = Paper = Live" |
| T-4 | **`execution/simulation.py` = einziger Fill/Cost/Margin/Liquidation-Code für Backtest+Paper** | Parität auf der Ausführungsseite |
| T-5 | **SQLite für alles State-/Event-/Journal-artige; Parquet für Massen-Zeitreihen** | reicht für Einzelnutzer weit; transaktional; kein Server. TimescaleDB/Influx nur falls je Query-Latenz zum Problem wird — aktuell nicht |
| T-6 | **`net/`-Client-Layer für alle externen APIs** (Rate-Limit, Retry, Circuit Breaker, Signing, Zeit-Sync) | ein Ort für Zuverlässigkeit |
| T-7 | **Chart: Lightweight Charts zuerst, TradingView Charting Library später — gleiche Backend-API** | umgeht den Lizenz-Gate für den Start |
| T-8 | **Frontend = separates Projekt (React/Vue), Python-Backend = FastAPI** | Trennung Rendering/Logik; API-Vertrag ist das Bindeglied |
| T-9 | **Zwei AI-Flächen: `ai/reasoning/` (nur Text) + `ai/advisor/` (begrenzt, Default AUS)** | LLM nie im harten Entscheidungspfad |
| T-10 | **Bybit v5 public data ab Phase 2; Trading-Adapter ab Phase 9 (Testnet)** | echte Daten früh, Account-Risiko spät |
| T-11 | **OANDA v20 als eine Integration für Gold + Forex; Dukascopy für tiefe Historie** | weniger Integrationen, mehr Abdeckung |
| T-12 | **Polygon.io als Ein-Anbieter-Lösung für US-Aktien-Daten + Corporate Actions** (erst Phase 7) | reduziert Integrationsaufwand |
| T-13 | **FRED/ALFRED für Point-in-Time-Makro** | vintage data = echtes PIT, gratis, autoritativ |
| T-14 | **`orderLinkId` / `client_order_id` überall** | Idempotenz, retry-sicher, Dedup |
| T-15 | **isolated margin, one-way position mode (MVP)** | begrenzter Verlust je Position, einfachere Reconciliation |
| T-16 | **`docs/strategy/` `0.1.0` bleibt eingefroren** — dieser Audit ändert **keine** Strategie-Regel | Pre-Registration; nur Architektur/Infrastruktur ändert sich |

---

## 16. Abhängigkeiten

### Neue Laufzeit-Abhängigkeiten (schrittweise, alle mit Wheels)
| Paket | Ab Phase | Zweck |
|---|---|---|
| `httpx` | 2 | HTTP-Client (`net/`) |
| `websockets` | 2 (Bybit public WS) | WS-Client |
| `anyio` | 2 | strukturierte Nebenläufigkeit (mit `asyncio`) |
| `pandas` **oder** `polars` | 2 | Backtest-Vektorisierung / Analyse-Bequemlichkeit (Entscheidung Phase 2 — Vorschlag: **polars**, ein Wheel, schnell, kein NumPy-ABI-Ärger) |
| `numpy` | 2–3 | numerische Indikatoren (falls pandas) / Monte-Carlo |
| `fastapi` + `uvicorn` | 12 | Dashboard-Backend |
| `pydantic-settings` | 3 | Secret-/Config-Ergänzung |
| `keyring` | 9 | OS-Keychain (`security/secrets.py`) |
| `pyjwt` / `cryptography` | 9 | Request-Signierung (Bybit HMAC braucht nur `hmac` aus stdlib — evtl. gar nicht nötig) |
| (LLM-SDK) | wenn AI aktiviert | über `ai/contract.py` gekapselt |

### Dev
| Paket | Zweck |
|---|---|
| `pytest-asyncio` | async-Tests (Event-Bus, Ingestion) |
| `respx` / `pytest-httpx` | HTTP-Mocking für Provider-Adapter (keine echten Calls in Tests) |
| `pre-commit` | Hooks (nach git) |

### Externe Konten (alle Free/Demo zum Start)
Bybit (kein Konto für Market Data; Testnet-Konto Phase 9) · OANDA Practice · FRED API-Key ·
Finnhub Free-Key · (Polygon paid — Phase 7) · Telegram Bot Token (Phase 5, für Alerts).

### Blockierende Abhängigkeit
**`git`** (fehlt) — für `code_sha` im `RunManifest`, pre-commit-Hooks, Versionskontrolle.
`xcode-select --install` **oder** `brew install git`. **Vor Phase 2 erledigen.**

---

## 17. Finale Phasenreihenfolge

| Phase | Name | Kern-Deliverables (inkl. Audit-Ergänzungen) |
|---|---|---|
| **0** (Rest) | Setup abschließen | `git init` (+ `git` installieren), `.pre-commit-config.yaml` (gitleaks/ruff/mypy), CI-Workflow, `docs/SECURITY.md`, `security/secrets.py`-Skelett |
| **1** | Data Foundation | ✅ **abgeschlossen** (193 Tests, ruff+mypy grün) |
| **2** | Research / Backtesting | `runtime/` (Bus + Supervisor + BacktestDriver), `net/`, `data/registry.py` + `router.py`, `BybitPublicDataAdapter` + Dukascopy-Import (echte BTC/Gold-Historie), `data/ingestion/`-Skelett, `execution/simulation.py` (Fill/Cost/Margin/Liquidation), `engine/backtest.py`, `research/{dataset,validation,robustness,registry}.py`, Bias-Tests (look-ahead, survivorship, PIT), Time-/Symbol-Stability-Achsen |
| **3** | Strategy Engine | `strategy/primitives.py` + `analysis/*` + `analysis/regime.py`, `MarketContext`, `strategy/{setup_detection,confluence,confidence,scoring}.py`, `ai/contract.py` + Guardrails (Nutzung AUS), `strategy.evaluate()` + Invarianten-Tests |
| **4** | Risk + Portfolio | `risk/{limits,margin,position_sizing,veto}.py` (+ Emergency-Vetos), `portfolio/` (State, Exposure, Korrelation, Cluster, `simulate_add`), `safety/kill_switch.py` (hierarchisch, persistiert), `state/` (Store + Recovery + fail-safe Start) |
| **5** | Autonomous Scanner + Signal Engine | `runtime/scheduler.py`, `scanner/scanner.py`, `scanner/tracker.py` (WATCH→…→confirmed, persistiert), `scanner/signal_engine.py` (voller `SignalReport`), `ops/notify.py` (Konsole/Datei + Telegram), `journal/decision_ledger.py` (Ereignis-Trace), `utils/tracing.py` |
| **6** | TradingView / Chart | `chart/` (Annotation-Payloads: Marker/PriceLine/Zone/TrendLine/Point/SessionBand/Label), `ChartDataAPI` + `ChartAnnotationsAPI` + WS-Update-Stream, Referenz-Frontend mit **Lightweight Charts** |
| **7** | Portfolio + Capital Allocation | `investment/` (INVEST/WAIT/HOLD/REDUCE, Monatsbudget), manueller Trade-Republic-Import, Polygon.io-Adapter (US-Aktien + Corporate Actions), `refdata/corporate_actions.py`, Faktor-Exposure voll |
| **8** | Paper Trading | `execution/oms.py` (Lifecycle-State-Machine), `execution/brokers/sim_broker.py`, `execution/trade_management.py`, `runtime/drivers/live_driver.py` (gegen Delayed-Daten), `engine/parity.py` (Backtest-vs-Paper-Diff), ≥ 100 Paper-Trades |
| **9** | Bybit Demo | `security/secrets.py` fertig, `net/`-Bybit-Client (Signing, Rate-Limit, WS-Auth, Zeit-Sync), `BybitBrokerAdapter` (**Testnet**), private WS |
| **10** | Execution | `execution/reconciliation.py` (Position + Balance + Orphan + Unexpected), voller Order-Lifecycle (ACK/Reject/Cancel/Replace/Partial), `safety/kill_switch.emergency_flatten()` (getestet), Clock-Drift-Check |
| **11** | Monitoring | `ops/metrics.py`, `ops/health.py` (`SystemHealth`), `ops/watchdog.py`, `safety/audit_log.py` (Hash-Chain), `safety/error_handling.py`, `journal/performance.py` (Kennzahlen je Setup/Regime/Session/Tier), Runbooks, Backup |
| **12** | Dashboard | `api/` (FastAPI): Scanner, Signale, Portfolio, Risk, Positionen, Performance, Journal, News, AI-Reasoning, Alerts, System-Health, Execution-Status, Chart-Datafeed, **Approval-Endpoint**; Frontend-App (separat) |
| **13** | Production Readiness | Deployment (systemd/Docker + Restart-Policy), vollständige Kill-Switch-Hierarchie + Persistenz-Tests, State-/Crash-Recovery-Tests, Parity Backtest=Paper=Demo grün, alle Freigabe-Gates aus `anti-overfitting.md` §9 |
| **14** | begrenzter Live-Betrieb | **separate, ausdrückliche Nutzer-Freigabe.** Kleinster Umfang, ein Instrument, strengste Limits, manueller Kill-Switch griffbereit, `risk_pct` final bestätigt |

**Abhängigkeitskette:** 2 → 3 → 4 → 5 → {6, 7, 8}; 8 → 9 → 10 → 11 → 12 → 13 → (14).
6 und 7 sind parallel zu 8 machbar und **nicht** blockierend für Paper Trading.

---

## 18. Was dieser Audit NICHT ändert

- Die Strategie-Spezifikation `strategy_version 0.1.0` (`docs/strategy/*`, `DECISIONS-0.1.0.md`).
- Die 14 bestätigten Nutzer-Entscheidungen.
- Risiko → Größe → Hebel; kein Hebel-Cap als Strategieparameter; dynamischer Hebel.
- Die Veto-Hierarchie; LLM überschreibt kein Veto.
- Phase 1 (fertig, getestet).
- Kein Live-Trading / keine echten Keys / keine Echtgeld-Orders in Phasen 0–13.
