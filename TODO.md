# TODO / Baureihenfolge

Arbeitsprinzip: **bauen → testen → Fehler beheben → testen → erst dann nächste Komponente.**
Jede Komponente bekommt Tests. **Kein Live-Trading, keine echten API-Keys, keine Echtgeld-Orders.**

Die vollständige, begründete Reihenfolge steht in **`FINAL_IMPLEMENTATION_PLAN.md`** (14 Phasen).
Diese Datei ist die abhakbare Kurzfassung.

Legende: `[ ]` offen · `[~]` in Arbeit · `[x]` fertig (inkl. Tests)

---

## Phase 0 – Projekt-Setup & Spezifikation

- [x] README.md, ARCHITECTURE.md, TODO.md, Projektstruktur, `pyproject.toml`, Makefile, `.gitignore`, `.env.example`
- [x] `docs/ARCHITECTURE_GAP_AUDIT.md`, `docs/STRATEGY_LOGIC_AUDIT.md`
- [x] `docs/strategy/` — 12 Spezifikationsdokumente + `DECISIONS-0.1.0.md`
- [x] **Freeze `strategy_version 0.1.0`** (14 Nutzer-Entscheidungen eingearbeitet)
- [x] `ARCHITECTURE.md` / `TODO.md` / Config-Beispiele aktualisiert
- [x] `FINAL_IMPLEMENTATION_PLAN.md`
- [x] Toolchain: `uv` + Python 3.12, `pyproject.toml` (hatchling), `uv.lock` (gepinnt), `scripts/check.sh`
- [x] `.env.example`: `BYBIT_*`-Zeilen entfernt (nur Platzhalter)
- [ ] `docs/SECURITY.md` (Least Privilege, Read-only-Dev-Keys, Redaction, Trust Boundaries) — vor Phase 9
- [ ] `.pre-commit-config.yaml` (gitleaks + ruff + mypy), `pip-audit` in CI — braucht `git`
- [ ] `xcode-select --install` (oder Homebrew-git) → `git init` → erster Commit mit aktiven Hooks

---

## Phase 1 – Data Foundation  ✅ CODE-COMPLETE (193 Tests grün, ruff+mypy strict grün)
Status-Detail: `docs/PHASE_1_STATUS.md`
- [x] `core/enums.py` — Timeframe, AssetClass, Exchange, TradingPriority, Side, SessionName, DataKind, DataQualityCode/Severity, ProviderHealth, CorporateActionType, NewsImpact  (+ Tests)
- [x] `core/time.py` — UTC-Normalisierung, Timeframe-Alignment (inkl. W1), DST-sichere Lokalzeit-Auflösung (Lücke vs. Mehrdeutigkeit)  (+ Tests)
- [x] `core/clock.py` — `Clock`-Protocol, `SystemClock`, `SimClock`, `FixedClock`  (+ Tests)
- [x] `core/models.py` — OHLCV, Quote, Trade, OrderbookSnapshot, Funding, OpenInterest, NewsEvent, MacroEvent, SessionWindow, DataQualityIssue/Status; alle frozen + `extra=forbid` + `schema_version` + `available_time`  (+ Tests)
- [x] `core/version.py` — `SCHEMA_VERSION`, `REPOSITORY_LAYOUT_VERSION`
- [x] `config/loader.py` — YAML + Pydantic-v2-Schema, `schema_version`-Pflicht, `config_hash`, `DataFoundationConfig` (live-Mode abgelehnt)  (+ Tests)
- [x] `utils/logging.py` — JSON-Logging + Secret-Redaction (Keys + Key-artige Strings)  (+ Tests)
- [x] `refdata/models.py` — Instrument, SymbolMapping, SessionSpec, TradingCalendarSpec, HalfDay, CorporateAction, FeeSchedule, MarginTier  (+ Tests)
- [x] `refdata/instruments.py` — `InstrumentMaster` (Lookup, Asset-Klasse, Priorität, `scan_universe` Tier1+2 Point-in-Time)  (+ Tests)
- [x] `refdata/symbols.py` — `SymbolMapper` (kanonisch ↔ provider, Aliase, case-insensitive)  (+ Tests)
- [x] `refdata/calendar.py` — `TradingCalendar` (24/7, weekend_gap, reguläre Börse + Feiertage), `resolve_session` (DST-sicher), `active_sessions`  (+ Tests)
- [x] `refdata/seed.py` — eingebaute Seed-Daten: 7 Instrumente (BTC/ETH MVP + XAU/SOL/EUR/AAPL/SPY), 5 Kalender, 3 Sessions, Symbol-Mappings
- [x] `data/interfaces.py` — Provider-ABCs: Historical/Live OHLCV, Quote, Trade, Orderbook, Funding, OpenInterest, News, Macro; `ProviderStatus`  (+ Tests)
- [x] `data/quality.py` — `check_ohlcv_series`: fehlende/doppelte/unsortierte Bars, ungültige OHLC/Volumen, stale, Timestamp-in-Zukunft, Symbol-/TF-Mismatch, kalenderbewusste Lücken; `check_session_resolution` (DST); `blocks_trading`  (+ Tests)
- [x] `data/resample.py` — M1→…→D1, vollständigkeitsgeprüft, `horizon`-Point-in-Time  (+ Tests)
- [x] `data/repository.py` — Parquet (OHLCV, Funding) + SQLite (Meta, News, Macro), `as_of`-Reads, `dataset_fingerprint`, Ingestion-Log  (+ Tests)
- [x] `data/health.py` — `HealthTracker`/`HealthRegistry` → HEALTHY / DEGRADED / UNAVAILABLE mit Hysterese  (+ Tests)
- [x] `data/providers/mock_provider.py` — deterministischer synthetischer Provider (OHLCV/Quote/Trade/Funding/OI/Orderbook)  (+ Tests)
- [x] `data/providers/csv_provider.py` — CSV-OHLCV/News/Macro, UTC-Pflicht, Point-in-Time  (+ Tests)
- [x] Integrationstests: End-to-End-Pipeline BTC/ETH, Point-in-Time nie Zukunft, Resample look-ahead-frei, Fingerprint reproduzierbar
- [x] **Exit-Gate-Bestätigung durch Nutzer** (Testlauf gegengeprüft)
- [x] **`docs/FINAL_ARCHITECTURE_AUDIT.md`** (Architecture + Data + Execution Audit) — vor Phase 2

> **Architektur-Audit 2026-08-28** (`docs/FINAL_ARCHITECTURE_AUDIT.md`): ereignisgetriebener Kern
> (`runtime/`), Multi-Provider-Datenstrategie (`net/`, `data/registry`+`router`+`ingestion`),
> Trennung `portfolio/`↔`investment/`↔Trading, `viz/`→`chart/`, `allocation/`→`investment/`,
> zwei AI-Flächen, `execution/simulation.py`. **14 Phasen** (Dashboard neu als 12). Die Phasen
> unten sind entsprechend erweitert.

## Phase 2A – Historical / Research  ✅ CODE-COMPLETE (264 Tests grün) — docs/PHASE_2_STATUS.md
- [ ] Phase-0-Rest: `git init` (env hat kein `git`) → `.pre-commit-config.yaml` (gitleaks/ruff/mypy) → CI  ⟶ Phase 3
- [x] `runtime/events.py` + `runtime/bus.py`: async Event-Bus (in-process pub/sub)
- [x] `net/client.py` + `net/ratelimit.py`: httpx-Wrapper, Token-Bucket, Backoff, Circuit Breaker
- [x] `data/registry.py` + `data/router.py`: Provider-Fähigkeitsmatrix + Auswahl-Policy
- [x] `data/providers/kraken.py` (**primär**) + `data/providers/bybit_public.py` (**sekundär**): REST OHLC/Trades/Funding/OI, gegen Fixtures getestet
- [x] `scripts/fetch_history.py`: Nutzer-CLI (mit Netz) → echte BTC/ETH-Historie ins Repository
- [x] `execution/simulation.py`: CostModel (Fees/Spread/Slippage/Funding) + FillModel (Partial Fills/Latenz) + MarginModel + LiquidationModel (Kraken/Bybit linear perp, isolated)
- [x] `execution/brokers/base.py` (ABC) + `execution/router.py` (`BrokerRouter`) + `execution/brokers/paper.py`
- [x] `journal/ledger.py`: TradeLedger + DecisionLedger (SQLite, append-only, `trace_id`), `TradeRecord` mit MFE/MAE
- [x] `research/metrics.py`: Win-Rate, PF, Expectancy, Avg-R, Max-DD, MFE/MAE-Verteilung, Verlustserie
- [x] `research/dataset.py`: Point-in-Time-Features (nur expanding/rolling)
- [x] `engine/backtest.py`: event-getrieben über `runtime/bus` + `BacktestDriver`; Strategie = Callback (`ReferenceMAStrategy` nur Plumbing)
- [x] `research/validation.py`: 50/25/25-Split, Walk-Forward, Purge/Embargo, **Time-/Symbol-/Regime-Stability**
- [x] `research/robustness.py`: Monte-Carlo (Bootstrap/Dropout/Kosten-Stress/Jitter), Ruin-Wahrscheinlichkeit
- [x] `research/registry.py`: `RunManifest` (code_sha/config_hash/dataset_fingerprint/seed) → Output-Hash
- [x] Tests: Look-ahead-Immunität, Leakage, Survivorship, PIT, Reproduzierbarkeit, Kosten mit/ohne, Partial-Fill, Liquidation, Walk-Forward, Monte-Carlo, Provider gegen Fixtures (keine echten Calls)

## Phase 2B – Live Data / Paper-Live  ✅ CODE-COMPLETE (Backbone läuft 24/7, 0 Orders) — danach STOPP
- [x] `data/providers/kraken_ws.py` + `data/providers/bybit_ws.py`: WS-Clients, Reconnect+Backoff, Resubscribe, Heartbeat, injizierbare Verbindungsfactory
- [x] `data/aggregator.py`: Trades → OHLCV, laufende Bar, `is_final`
- [x] `data/ingestion/service.py`: Provider → normalize → `data/quality` → Bus + Repository, Gap-Erkennung + REST-Backfill
- [x] `runtime/drivers/live_driver.py` + `runtime/supervisor.py`: Daemon (Loop, Subscriber, Heartbeat, Graceful Shutdown, fail-safe Start)
- [x] `scanner/scanner.py` (**Shell**): subscribt `BarClosed`, platzhaltende `evaluate()`, loggt, Metriken → „24/7-Beobachtung"
- [x] `ops/metrics.py` + `ops/health.py` (minimal): Live-Status
- [x] `scripts/run_paper_live.py`: Daemon-Einstieg (Synthetic-Live in dieser Umgebung; Kraken/Bybit-Public-WS mit Netz)
- [~] Gold live via `PepperstoneMT5Adapter` — Adapter-Contract da; Impl. braucht Windows+MT5+Konto ⟶ dort
- [x] Tests: Ingestion normalisiert, Data-Quality-Veto stoppt schlechte Bars, Bus-Fan-out, Scanner-Shell reagiert, **kein Order-Pfad**, Graceful Shutdown, Reconnect (fake WS), Supervisor-Lebenszyklus
- [x] Exit-Gate 2B (Demonstration): `run_paper_live.py` stabil, Pipeline Provider→Ingestion→Quality→Bus→Scanner→Metrics, Paper-Execution verdrahtet aber idle, **keine Echtgeld-Order möglich**, sauberer Shutdown
- [x] **Echte Live-Data-Integration (Audit 16, 2026-08-30)** — Kraken + Bybit **public read-only** CONNECTED (REST Zeit/OHLCV/Bid-Ask + WebSocket-Trade-Stream). `scripts/live_connectivity_test.py`. `data/interfaces.AsyncQuoteSource` + `fetch_quote` (Kraken/Bybit). Kraken-WS-Symbol-Mapping-Bug behoben (`BTCUSDT`→`BTC/USD`). `runtime/live_pipeline.LivePipeline`: REST-Warmup→prime→WS confirmed M5→`MarketContext(cutoff=close_time)`→`PaperLiveRunner.feed()`→Decision→Signal→Alert→Paper (EventBus). `scripts/run_live_paper.py`. `orders_sent`=0 asserted. Decision = `NO_TRADE/SCANNING` (Regime-Gate, wie Backtest). `docs/LIVE-DATA-INTEGRATION-2026-08.md`. +11 Tests.
- [x] **M-01 24/7-Daemon** (`docs/M01-24-7-SUPERVISOR.md`): `runtime/supervisor.py::LiveSupervisor` fährt die echte `LivePipeline` dauerhaft — Recovery (Snapshot laden, Positionen wieder einhängen, `warmup(preserve_last_open)` + Gap-Backfill Bar-für-Bar), WS-Überwachung + zweistufiger Auto-Reconnect, stale-Detection, Watchdog → `SystemHealth`, Fehler-Isolation, SIGTERM/SIGINT graceful, Wall-Clock (Sleep-fest), `_fed_opens` + `_last_fed_cutoff` ⇒ keine Doppel-Events/-Positionen. `state/store.py` + `state/recovery.py` (waren Stubs). `scripts/run_live_daemon.py`. `orders_sent`=0. 18-min-Live-Test (Bybit+Kraken) + Restart-/Recovery-Test grün. +15 Tests · Audit 17
- [x] Höhere TFs rollierend: M15 aus dem M5-Strom resampelt (kein REST), H4/D1 REST bei 4h/1d-Kadenz — REST-Last für höhere TFs ~12× gesenkt · Audit 17
- [x] Bybit Funding + Open Interest im `DerivativesContext` (`--derivatives`, nur bei validen REST-Daten; echte Endpunkte verifiziert) · Audit 17
- [ ] `runtime/api.py` read-only `/health` `/status` `/positions` (kein Order-Pfad)

## Phase 3 – Strategy Engine (geteilt für Backtest/Paper/Live) — `strategy_version 0.1.1`
- **Prozess:** vor jedem größeren Strategy-Abschnitt → `docs/CONTINUOUS_IMPROVEMENT.md` (7-Fragen-Audit + Aufnahme-Gate G1–G7). Backlog dort (HIGH: tiefe Historie C12, News+PIT C10, Range-Bruch §2.3).
- [x] Spec-Audit + `DECISIONS-0.1.1.md` + `SPEC-ADDENDUM-0.1.1.md` (C1–C12), Docs auf 0.1.1 nachgezogen
- [ ] `scripts/fetch_history.py`: Bybit-Kline-Pagination → ≥ 180 Tage M5 BTC/ETH (C12)
- [x] `core/enums.py`: Strategy-Domänen-Enums (Direction/Polarity/Regime/SetupState/DecisionType/NoTradeReason/VetoId …)
- [x] `core/types.py`: `MarketContext` (Look-ahead-Guard) + `strategy/decision.py: Decision` (BUY/SELL/WAIT/NO_TRADE) + `PortfolioContext` (C6/C9/C11) — 19 Tests
- [x] `strategy/primitives/`: `primitives.md` §0–§13 vollständig + Golden-Tests (`session_range` §0.5 wartet auf Sessions-Modul)
  - [x] `atr` (§0.2), `swings` (§1), `structure` (§2/§3 BOS/CHoCH) — 12 Tests
  - [x] `liquidity` (§4 Level+Strength+State, §5 Equal H/L, §6 Sweep) — 15 Tests
  - [x] `imbalance` (§7 Displacement, §8 FVG, §9 IFVG, §11 Mitigation) — 20 Tests
  - [x] `blocks` (§10 Order Block + §12 Breaker) — 21 Tests
  - [x] `pd` (§13 Premium/Discount + §0.5 Dealing/Swept-Leg/Impulse-Leg Range) — 15 Tests
- [x] `analysis/regime.py`: Directional/Volatility/Phase (§2–§4), `RegimeTracker` (Hysterese §6), `merge_htf`+`regime_gate` (MTF-Konsens D1+H4 §7, NO_TRADE-Gate §8/§9) — 21 Tests
- [x] `analysis/sessions.py`: DST-Auflösung + London/NY-Overlap, `completed_sessions` (Session High/Low), `session_range` (PD-Referenz), `session_levels` (SESSION_HIGH/LOW Liquidität), `session_filter` (§18 Gate) — 10 Tests
- [x] `structure.py` §2.3 Range-Bruch: `range_breaks`/`range_break` (BOS `origin=RANGE`, `StructureOrigin`-Enum, `break_id`); `structure_breaks` bleibt rein gerichtet — 9 Tests
- [x] `strategy/price_action.py`: `find_confirmation(zone, direction, m1_bars)` / `confirmation_for_candidate(mtf, candidate, m1_bars)` — Engulfing/Pin/Minor-CHoCH-M1 (`SPEC-ADDENDUM-0.1.1.md` §2, C7) als **Gate** für `entry.mode=confirmation_market`; `EntryConfirmation` (stabile `confirmation_id`, `strength` 0..1, `zone_id`/`zone_kind`, `entry_ref_price`=next-M1-open), `ConfirmationScan` (früheste Bar = primary); PIT/look-ahead (`now`/`since`), M1↔MTF-Bindung (Instrument/Cutoff), M1-fehlt→kein Absturz. Kein Score-Faktor, kein Ersatz für §7. — 30 Tests
- [ ] `analysis/`: market_structure, liquidity, smc, support_resistance (= opposing-liquidity-Proxy C8)
- [x] `analysis/mtf.py`: `build_mtf_context` (M5-Basis → M15/H4/D1 via `resample_ohlcv`, `horizon=cutoff`), `TimeframeContext` (swings/structure/regime/bias/liquidity/premium_discount/quality/data_confidence je TF), `MtfContext` (HTF-`regime_gate`, `htf_bias`, min-`data_confidence`, `issues`, roher `MarketContext`); `MarketContext` um typisierte Leer-Slots `derivatives`/`cross_asset`/`news` erweitert (keine Fake-Daten) — 16 Tests
- [x] `analysis/news.py` (`assess_news`/`build_news_context` — PIT, asset-spezifische Relevanz via `news_relevance`, Blackout/Pre-Positioning/risk_off → `NewsContext`; in `MarketContextAssembler` verdrahtet) + `analysis/macro.py` (`assess_macro` → `MacroContext`: rate_cycle/inflation_trend/growth_trend/risk_sentiment aus FRED-Vintages, PIT, `UNKNOWN` statt Fake) — 17 Tests · Audit 15
- [x] `strategy/setup_detection.py`: `detect_setups(MtfContext) -> SetupScan` — kausale Kette `SMC-SWEEP-REV-01` §0/§24 FSM (`SCANNING→BIAS_SET→LIQUIDITY_IDENTIFIED→SWEPT→RECLAIMED→DISPLACED→STRUCTURE_SHIFTED→ARMED`), `SetupCandidate` (stabile `setup_id` + `revision`, gekoppelte Primitive-Refs sweep/displacement/structure_break/entry_fvg|ob), Kausalitäts-/Reihenfolgen-Prüfung, Kettenabbruch + Klasse-A-Invalidierung, Look-ahead-Clamp. Post-ARMED-Lifecycle nachgelagert. `TimeframeContext` um `displacements`/`order_blocks` erweitert — 27 Tests
- [x] `strategy/gates.py`: `location_gate` (§8, Zonen-Mitte vs. `swept_leg` → Discount/Premium, Veto V2, `ENTRY_WRONG_SIDE_OF_EQUILIBRIUM` + Zonenfilter Höhe/`UNMITIGATED`/stale) · `rr_gate` (§10 SL = ungünstigere von Sweep-Extrem/distale Zonenkante + `sl_buffer_atr`·ATR, Cap/Floor → V10; §12–§14 TP1/TP2 aus nächster/​signifikanter opposing Liquidität **oder** H4/M15-Swing-Level, R-Cap/Floor; §16 `RR_to_TP2`/`blended_RR`/`min_target_room` → V8) · `evaluate_gates` (kurzschließend) · `GateOutcome` ALLOW/BLOCK/WAIT (WAIT = Geometrie nicht bestimmbar, konservativ) · `EntryGeometry`. Rein geometrisch, look-ahead-frei, deterministisch, Long/Short-symmetrisch. — 23 Tests
- [x] `strategy/confluence.py`: `assess_confluence(mtf, candidate, *, gates=, confirmation=, session_names=) -> ConfluenceReport` — erklärbare Evidenz-Bilanz. `ConfluenceFactor` (factor/factor_group/role/direction/contribution/reason/timestamp/information_cutoff/data_quality/scored). **Kein Double-Counting:** korrelierte Faktoren teilen eine Gruppe, innerhalb = relevanz-gewichteter Durchschnitt (kein Sum); genau **ein** `structure_shift` (nie BOS+CHoCH getrennt). `net_confluence` = gewichtetes Mittel der **verfügbaren gescorten** Gruppen; `support_score` [0,1]; `agreement`; `contradiction_flags` (V1/V2/V3/V4/V6/V8/C9) für Veto; `unavailable`. Kontext (`scored=False`, → Confidence): mtf_disagreement/volatility/phase/session/data_confidence. Fehlende News/Derivatives/Cross-Asset = UNAVAILABLE (Beitrag 0, aus Nenner). Look-ahead-frei, deterministisch, symmetrisch. — 18 Tests
- [x] `strategy/veto.py`: `assess_vetoes(...) -> VetoReport` / `collect_vetoes(...) -> tuple[VetoId, ...]` — harte Barrieren V1–V10 (`contradictions.md` §4/§23), **VOR** dem Score. `VetoRecord` (veto_id/reason/severity/timestamp/information_cutoff/evidence/source/blocking/correlated_with). V2/V8/V10 aus den Gates, V1/V3/V4/V6/V7 aus `MtfContext`, V5 = objektive Re-Sweep-Prüfung auf M15, V9 = Portfolio (pass-through ohne `portfolio_context`, C9). Nutzt `confluence.contradiction_flags` als Korroboration. Fehlende Ext-Daten (Slippage/Depth/Spread-in-Paper) = `not_available`, **nicht** blockierend; fehlender News-Feed ⇒ V4 (Fail-safe C10, `require_news_feed` konfigurierbar). Severity CRITICAL/HARD/PORTFOLIO, Priorität für die UI, V6↔V7 / V8↔V10 `correlated_with`. Deterministisch, PIT, look-ahead-frei, asset-/TF-aware, symmetrisch. — 32 Tests
- [x] `strategy/confidence.py`: `assess_confidence(mtf, candidate, *, confirmation=, source_count=, ...) -> ConfidenceReport` (`confidence.md`). **3 Ebenen getrennt:** `data_confidence` = `min(completeness, freshness, consistency, source_term)` (aus `TimeframeContext.data_terms` — neu in `mtf.py` als `DataQualityTerms`); `analysis_confidence` = Σ wᵢ·termᵢ über die 6 Spec-Terme (swing_confirmation/structure_clarity/sweep_unambiguity/regime_clarity/htf_mtf_agreement/fvg_integrity); `setup_confidence` = `(0.4·data + 0.6·analysis)·floor_penalty` (0.5 bei schwacher Einzelkomponente < 0.60). `ConfidenceRecord` (value/limiting_factor/terms/evidence/information_cutoff/timestamp). Harte Floors: data < 0.50 → `blocks_data` (V6), setup < 0.60 → `blocks_setup` (CONFIDENCE_BELOW_MIN), unbestätigter beteiligter Swing → `unconfirmed_swing` (§5). Kein Double-Count (orthogonal zu Confluence/Score), look-ahead-frei, deterministisch, symmetrisch. — 25 Tests
- [x] `strategy/scoring.py`: `score_setup(mtf, candidate, *, confluence, confidence, gates, vetoed=) -> ScoreReport` (`scoring-rubric.md` §1–§4, §21). `raw = Σ wᵢ·fᵢ` über die **12 WEIGHTED-Faktoren**, `score_0_100 = 100·raw/Σwᵢ` (nur verfügbare), `final_score = clip(score_0_100 − Σ penalties, 0, 100)`. **MVP: alle wᵢ = 10, Penalties = 0** (C2). Jeder Faktorwert 1:1 aus vorhandenem `ConfluenceFactor` / `EntryGeometry` / MtfContext-Regime / `ConfidenceReport` — kein neuer Indikator. `ScoreFactor` (name/weight/value/contribution/source/available/reason). Tier A+/A/B aus `final_score × setup_confidence` (85/0.80, 75/0.70, 65/0.60), sonst `NO_TRADE(SCORE_BELOW_B)`; `vetoed` oder `blocks_data`/`unconfirmed_swing` → Tier NO_TRADE (Score wird trotzdem fürs Ledger berechnet). R-06: nur WEIGHTED-Faktoren, keiner doppelt. `correlated_factor_groups` weist korrelierte Faktoren aus (Kalibrierungs-Hinweis). Deterministisch, PIT, symmetrisch. — 16 Tests
- [ ] `ai/contract.py`: LLM-Output-Schema + Guardrails (Nutzung noch deaktiviert)
- [x] `strategy/contradictions.py`: `assess_contradictions(mtf, candidate, *, confluence, gates=, veto=, scan=, minutes_to_session_end=) -> ContradictionReport` (`contradictions.md` §4/§5). **HARD_CONFLICT** (BLOCK, je `NoTradeReason`): C1 `OPPOSING_LIQUIDITY_BREAKOUT` · C2 `MESSY_LIQUIDITY` · C9≥50% `ENTRY_INTO_OPPOSING_HTF_ZONE` · C11 `NO_STRUCTURE_SHIFT` · C12 `COUNTER_SETUP_CONFLICT`. **VETO_ECHO** (INFO, `covered_by_veto`): C3–C8/C10 = Restatements von V4/V6/V8/V9/V1/V3/V8 — nicht re-entschieden. **NEGATIVE_FACTOR** (PENALTY, gemeldet nicht angewandt): messy_sweep −8 / proximity_opposing_htf_zone −10 / stale_structure −5 / weak_displacement −6 / mtf_partial_disagreement −7 / wide_sl −5 / late_session −4. `contradiction_id`/`severity`/`reason`/`evidence`/`timestamp`. Kein Double-Count (C9 & proximity teilen **einen** Confluence-Faktor). 4 `NoTradeReason` angehängt. — 23 Tests
- [x] `strategy/no_trade.py`: `assess_no_trade(mtf, *, candidate=, confidence=, system=, portfolio=, instrument_history=, account_risk=, session_specs=, now=) -> NoTradeReport` / `check_no_trade(...) -> tuple[NoTradeReason, ...]` (`no-trade.md`, **erster** Pipeline-Schritt). 8 Gruppen SYSTEM/DATA/REGIME/TIME/NEWS/RISK/STRATEGY_STATE/EXECUTION; `NoTradeRecord` (reason/group/detail/evidence/timestamp/information_cutoff/requires_alert); `not_checked` für Gruppen ohne Konto-/Broker-State (blockieren nicht — Phase 4/9+). SYSTEM via `SystemState`, DATA via `mtf.quality`+`data_terms`, REGIME via `htf_regime_gate`, TIME via `sessions.session_filter`, NEWS via `market_context.news`, STRATEGY-STATE via `PortfolioContext`+`InstrumentHistory`, EXECUTION via spread/Datenalter. — 34 Tests
- [x] `strategy/evaluate.py`: `evaluate(MarketContext, ...) -> EvaluationResult` + `evaluate_from_mtf(...)` + `decide(...) -> Decision` — voller Orchestrator (`SPEC-ADDENDUM` §1.2): No-Trade→Regime→MTF→FSM→Veto→State→Location→RR→Confirmation→Confluence→Contradictions→Confidence→Score→Portfolio→Final. `EvaluationResult` = Decision + **alle** Zwischen-Reports (Explainability). `context_ref`/`score_detail`/`confidence_detail` fürs Ledger. — 17 Tests
- [x] **Schritt 4** `strategy/signal.py`: `SignalTracker.ingest(EvaluationResult, position_state=) -> SignalUpdate` — lebender Lifecycle (12 States WATCH…EXPIRED), append-only Revisionen, nie überschrieben; `_diff` erkennt STRENGTHENED/WEAKENED/ENTRY_/SL_/TP_CHANGED/STATE_CHANGED/TP_REACHED/EXIT_REQUIRED/INVALIDATED/EXPIRED; `DisplayAlias`-Mapping; `sweep()` altert abgestandene Signale. — 18 Tests
- [x] **Schritt 6** `strategy/position.py`: `PositionManager` (zustandslos) + `PaperPosition` (frozen) — Paper/Sim, **keine Echtgeld-Order**. Pending-Fill, Pending-Expiry, TP1/TP2/Runner, SL→Break-Even, Trail nach TP2, Stop-Loss, worst-case-Fill (SL vor TP), `on_reevaluation` → EXIT_REQUIRED, `request_exit`/`close`, MFE/MAE in R, `signal_state_for()`. — 18 Tests
- [x] **Schritt 5** `strategy/engine.py`: `ContinuousEvaluator.on_market_context(mc) -> EngineTick` — je `MarketContext` volle Pipeline neu, diff → Signal-Revision + Paper-Position-Event; `evaluate_fn`-DI-Hook; Auto-Paper (pending); M1-Fenster aus letztem ARMED-Kandidaten. — 9 Tests
- [x] **Schritt 7** `strategy/alerts.py`: `AlertEngine` — 15 Event-Typen, Dedup je (Signal, Typ), Cooldown (+ `always_deliver`-Bypass), Auto-Update/Auto-Dismiss bei Signal-Änderung, Gegensatz-Ablösung (strengthen↔weaken), Pipeline-Alerts aus `NoTradeReport` (DATA_STALE/DATA_QUALITY_FAILURE/RISK_LIMIT/BROKER_DISCONNECTED). — 15 Tests
- [x] **Schritt 8** `strategy/m1_feed.py`: `M1Source` (Protocol) + `RepositoryM1Source`/`InlineM1Source`/`NullM1Source` + `confirmation_window(...)` — PIT (`close_time <= as_of`), Fenster ab §7-Bruch − Puffer, **kein Fake** (leer statt erfunden). — 5 Tests
- [x] **Schritt 10** `strategy/paper_live.py`: `PaperLiveRunner.feed(mc) -> PaperLiveStep` — verdrahtet ContinuousEvaluator + AlertEngine; kein Broker, kein Order-Routing, Historie erklärbar. — 3 Tests
- [x] **Schritt 9/11/12/13** (Doku): `MarketContext` derivatives/cross_asset/news-Slots typisiert & `UNAVAILABLE` (kein Fake); `docs/CONTINUOUS_IMPROVEMENT.md` §6h Audit 11; `docs/CALIBRATION_BACKLOG.md` (alle `*Params` unkalibriert); `docs/HISTORICAL_DATA_PLAN.md` (≥180 T M5, PIT, deterministischer Replay).
- [x] `engine/replay.py`: `ReplayClock` (deterministisch, kein Wall-Clock) + `MarketContextAssembler` (Repo→MarketContext je cutoff, vorgeladen + `bisect`-Slice, strikt PIT) + `validate_dataset`/`DatasetRequirements`/`DatasetReport` (`raise_if_incomplete` → `DatasetIncompleteError`, **kein Fake**) + `ReplayHarness`. — 11 Tests
- [x] `engine/backtest.py` neu verdrahtet: `Backtest.run(BacktestConfig)` → ReplayClock → Assembler → `PaperLiveRunner.feed()` (echte `strategy.evaluate`-Pipeline) → `TradeRecord`s + `RunManifest`/`output_hash` + `Metrics` + `StrategyBacktestReport` + `RunTelemetry`. Multi-Asset, `evaluate_fn`-DI. Reference-MA-Pfad → `engine/reference_backtest.py` (nur Execution-Schicht). — 9 Integrationstests
- [x] `engine/backtest_metrics.py`: `StrategyBacktestReport` — TP1/2/3-Hit-Rate, Stop-/BE-/Trail-/Invalidated-/Expiry-Rate, Hold-Time, Exit-Effizienz (MFE-Ausnutzung), Segmente (Long/Short, Score-/Confidence-Tier, Exit-Grund, Asset), Score-/Confidence-/Confluence-vs-Ergebnis-Buckets + Pearson-Korrelation, Setup-State-vs-Ergebnis, `RunTelemetry` (Decision-Verteilung, No-Trade-Gründe, Veto-Häufigkeit, Signal-Revisionen, Alerts). — 9 Tests
- [x] **Bug gefunden & behoben:** `ContinuousEvaluator._advance_position` — `_seen_fill_bar` inkonsistent verschlüsselt (Kandidaten-`setup_id` vs. `Decision.setup_id`) + Position beim ersten Tick gegen die Warmup-Historie simuliert. Fix: `position_id`/`signal_id` an Kandidaten-`setup_id` binden + `_seen_fill_bar` beim Öffnen = `now`. `PaperPosition` um `entry_ts` + `tp_level_reached` erweitert; `ContinuousEvaluator.force_close` für END_OF_DATA.
- [x] `docs/CONTINUOUS_IMPROVEMENT.md` §6i Audit 12 (Leakage/Snooping/Survivorship/Look-ahead geprüft, Bug dokumentiert); `docs/HISTORICAL_DATA_PLAN.md` §1/§5 aktualisiert (Harness gebaut, DatasetRequirements-Defaults, Kosten=0-Backlog).
- [x] `data/providers/binance_vision.py` + `scripts/ingest_binance_vision.py`: **echte** M5-Historie BTCUSDT/ETHUSDT (Binance-Vision-Bulk, SHA-256, ms/µs-Norm, `close_time=open+tf`). 112 128 M5 + native M15/H4/D1 je Symbol, 2024-06-06 → 2025-06-30, **0 Quality-Issues**, `validate_dataset` grün. `docs/DATASET-BTC-ETH-M5.md`. — 9 Tests
- [x] `engine/replay.py`: `MarketContextAssembler` native höhere TF **fensterbegrenzt** (`higher_warmup_bars` M15:400/H4:260/D1:200, `bisect`-Slice statt „alles"); `DatasetRequirements.higher_min_bars` + Tiefen-Check in `validate_dataset`. `analysis/mtf.py::_analyze_tf` gegen leere Bar-Liste gehärtet (zu kurzer Warmup ⇒ NO_TRADE via data_confidence statt IndexError).
- [x] `engine/parity.py` + `tests/integration/test_parity.py`: `run_parity`/`compare_decisions` — vorgeladener Replay vs. streaming Kontext ⇒ **Look-ahead-Beweis** (Test grün, match_rate 1.0).
- [x] `scripts/run_backtest.py`: neu — deterministischer End-to-End-Lauf über die echte `strategy.evaluate`-Pipeline, voller `StrategyBacktestReport` als JSON.
- [x] **Erster realer Backtest** (2025 H1 BTC/ETH M5, echte Pipeline, alle PROPOSED DEFAULTS): **0 Trades** — Regime-Gate blockt 100 % (`regime_unclear` ~72 %, `vol_extreme` ~25 %; period-abhängig: 2024-Q4 passiert 4.4 %). Kein Leakage/Snooping/Survivorship. Score/Confidence-Informationswert **noch nicht messbar**. 2 reale Bugs behoben: Krypto-Wochenend-Block (`NoTradeParams.market_is_24_7`, in `EvaluateParams.__post_init__` aus asset_class); `_analyze_tf`-IndexError bei kurzem Warmup. `scripts/diag_backtest_gates.py`, `docs/CONTINUOUS_IMPROVEMENT.md` §6j Audit 13.
- [x] `strategy/costs.py` + Integration in `strategy/position.py`: `CostConfig` (Maker/Taker/Spread/Slippage/Impact/Funding, **alle Default 0.0**, bps→R), `leg_cost_r`/`funding_cost_r`/`from_fee_schedule`. `PaperPosition` → `gross_realized_r` (brutto) + `realized_r` (netto) + `fees_r`/`slippage_r`/`funding_r`/`entry_cost_r`/`exit_cost_r`. `EngineParams.cost` durchgereicht. — 9 Tests
- [x] **Phase 4 Risk** `risk/limits.py` (`RiskLimits` — hard_max 2.0 %, Bänder 1.00/0.65/0.40, alle Konto-/Portfolio-/Hebel-Limits) · `risk/position_sizing.py` (`size_position` — Risiko→Größe→Hebel, alle Deckel) · `risk/risk_engine.py` (`RiskEngine.review(Decision) -> APPROVED|REJECTED|PASS_THROUGH` — **strukturell nicht durch Score/Confidence überstimmbar**, NO_TRADE bleibt PASS_THROUGH) · `safety/kill_switch.py` (hierarchisch, persistiert, fail-safe) · `portfolio/engine.py` (`PortfolioLedger` → `AccountState` + `PortfolioContext`, Equity/Loss/DD/Heat/Cluster/Streak). — 20 Tests
- [x] **Phase 4 Live-Adapter** `data/providers/adapter_base.py` (`LiveDataAdapter`/`CredentialSpec` — nur ENV-Var-Namen, keine Werte; ohne Keys → UNAVAILABLE) · `news_calendar.py` (`CsvEconomicCalendar` PIT + `EconomicCalendarAdapter`-Vertrag, `CANONICAL_EVENTS` FOMC/CPI/PCE/NFP/ECB) · `cross_asset.py` (`build_cross_asset_context` aus Proxy-OHLCV, nur echte Felder) · `mt5.py` (FX/XAU-Vertrag, Symbol-Map, off-Windows inert) · `equities.py` (`adjust_for_actions` Split/Dividende + Look-ahead-Schutz). `docs/LIVE-DATA-ADAPTERS.md`. — 7 Tests
- [x] `RiskEngine` + `PortfolioLedger` + `KillSwitch` in `PaperLiveRunner` verdrahtet: `ContinuousEvaluator(risk_gate=…)` sitzt VOR dem Auto-Open (nur ablehnen, `EngineTick.risk` / `risk_blocked`); Ledger wird aus open/fill/close/armed gefüttert. Alles **optional** (Default off, Verhalten unverändert). — 4 Tests
- [x] Regime-Gate OOS-Kalibrierung: `scripts/regime_calibration.py` → **Baseline bleibt** (jede Lockerung OOS negativ), `docs/REGIME-CALIBRATION-2026-08.md` · Audit 14
- [x] **Multi-Symbol-Backtest** (BTC/ETH/SOL/BNB/XRP/DOGE, voller Pfad, 2023-08→2025-06, `scripts/run_multi_backtest.sh` + `scripts/analyze_multi_backtest.py`): **0 Trades über alle 6** — „mehr Krypto-Instrumente" als Hebel widerlegt (`regime_unclear` 66 % / `vol_extreme` 33 %, skaliert mit Asset-Vol). Keine Parameteränderung. `docs/MULTI-SYMBOL-BACKTEST-2026-08.md` · Audit 15
- [x] `validate_dataset`: `check_continuity` — interne M5-/höhere-TF-Lücken als `notes` (fand echte 2023-03-24-Lücke, ~80 min, alle Symbole) · Audit 15
- [ ] Nächste echte Hebel (Backlog): XAUUSD/FX (Dukascopy) · Struktur-Klassifikator auf H4 kalibrieren · 2. Setup-Typ · 2023-03-24 backfillen
- [ ] `engine/parity.py` in `PaperLiveRunner` + Backtest verdrahten (Diff-Report je Lauf)
- [ ] `RegimeGateParams` über `MtfParams`/`EvaluateParams` konfigurierbar machen (aktuell in `build_mtf_context` hardcodet)
- [ ] MTF-Analyse-Caching (Backtest ~31 ms/Tick → Ziel < 10 ms)

## Phase 4 – Risk + Portfolio
- [x] `risk/limits.py`: alle Limits + Verbote im Code erzwungen (100 % Coverage)
- [x] `risk/margin.py`: isolated-linear Liquidationspreis / `max_leverage_for_liq_distance` / `estimate_liquidation`; in `position_sizing` verdrahtet (`mmr=0` ⇒ identisch zur Heuristik) — 6 Tests · Audit 15
- [x] `risk/position_sizing.py`: Risiko→Größe→Hebel (dynamisch), alle Caps (`sizing.md` §2) — 87 % Coverage
- [x] `risk/risk_engine.py`: Vetorecht, `APPROVED/REJECTED/PASS_THROUGH`, strukturell nicht durch Score/Confidence überstimmbar — 98 % Coverage, Invarianten-Tests
- [x] `portfolio/engine.py`: `PortfolioLedger` → `AccountState`/`PortfolioContext` (Equity/Loss/DD/Heat/Cluster/Streak)
- [x] `safety/kill_switch.py`: hierarchisch, persistiert, fail-safe Start (94 % Coverage)
- [x] `state/store.py` (atomarer JSON-Snapshot, versioniert, fail-safe) + `state/recovery.py` (Gap-Rechnung, `PaperPosition`-Round-Trip) — für den 24/7-Supervisor (Audit 17). SQLite-Store bei echtem Bedarf.
- [ ] `safety/{audit_log,error_handling,monitoring}.py` — noch Stub (Phase 8/11)

## Phase 4 – Risk + Portfolio *(Ergänzung)*
- [ ] `risk/veto.py` (aus `strategy/veto.py`): V1–V10 + Emergency-Vetos (Data-Quality, **Broker-Health**, Manual-Stop)
- [ ] `portfolio/` (neues Top-Level, aus `execution/portfolio.py`): State + Exposure + Korrelation + Cluster + `simulate_add()`

## Phase 5 – Autonomous Market Scanner + Signal Engine
- [ ] `runtime/scheduler.py`: Scan-Orchestrierung (Bar-Close-Events), Tier-1-Priorität, nicht-blockierend
- [ ] `scanner/scanner.py`: Multi-Asset-Scan über `strategy.evaluate()`, nur relevante Ergebnisse
- [ ] `scanner/tracker.py`: Setup-Lebenszyklus WATCH→developing→armed→confirmed→expired, **persistiert in `state/`**
- [ ] `scanner/signal_engine.py`: voller `SignalReport` (§7 + Gewinn@TP1/2/3, Verlust@SL, `liq_distance_pct`, `trace_id`)
- [ ] `ops/notify.py` + `scanner/alerting.py`: Konsole/Datei → Telegram Bot API (offiziell)
- [ ] `journal/decision_ledger.py`: Ereignis-Trace DATA→…→EXIT, jede Entscheidung inkl. `NO_TRADE`
- [ ] `utils/tracing.py`: `trace_id`-Propagation (contextvars)

## Phase 6 – TradingView / Chart
- [ ] `chart/annotations.py`: Payloads Marker/PriceLine/Zone/TrendLine/Point/SessionBand/Label
      (BUY/SELL, Entry, SL, TP1–3, Liquidity, FVG, **IFVG**, OB, Breaker, BOS, CHoCH, Swing H/L,
      Equal H/L, Premium/Discount, Sessions, Setup-Zonen, Setup-ID, Strategy-Version)
- [ ] `ChartDataAPI` (Datafeed) + `ChartAnnotationsAPI` + WS-Update-Stream (Live-Aktualisierung bei Setup-Änderung)
- [ ] Referenz-Frontend mit **Lightweight Charts** (Apache-2.0, ungated); TradingView Charting Library später, gleiche API
- [ ] **Keine** Browser-/Click-Automation, kein Pine-Script als Datenquelle

## Phase 7 – Portfolio + Capital Allocation
- [ ] `portfolio/` erweitern: Multi-Asset (Stocks/ETF/Crypto/Alt/Derivate/Cash), Faktor-Exposure voll
- [ ] Portfolio-Import: Bybit (read-only API), Trade Republic **nur manueller Import** (`source=manual`)
- [x] `refdata/corporate_actions.py`: PIT-Backadjustment (`adjust_ohlcv`), `CorporateActionBook`, `resolve_symbol_at` (SYMBOL_CHANGE), Delisting — 6 Tests · Audit 15
- [ ] Polygon.io-Adapter (US-Aktien + **Corporate-Actions-API**) — Provider-Bewertung in `docs/MULTI-ASSET-READINESS.md`
- [ ] `investment/engine.py`: `INVEST/WAIT/HOLD/REDUCE` + Begründung, Monatsbudget ~200–400 €,
      berücksichtigt bestehende Positionen, **erzeugt NIE Trade-Signale**

## Phase 8 – Paper Trading
- [ ] `execution/oms.py`: Order-Lifecycle-State-Machine, Idempotenz (`client_order_id`), Duplikatschutz, Cancel/Replace
- [ ] `execution/brokers/sim_broker.py`: nutzt `execution/simulation.py` (aus Phase 2)
- [ ] `execution/trade_management.py`: TP/BE/Trail/Invalidierung (`invalidation.md`)
- [ ] `runtime/drivers/live_driver.py`: gegen Delayed-Daten, Fills simuliert
- [ ] `engine/parity.py`: Backtest-vs-Paper-Diff → Report
- [ ] `journal/trading_journal.py`, `journal/performance.py`
- [ ] ≥ 100 Paper-Trades, Kill-Switch- und Loss-Streak-Pfade geübt

## Phase 9 – Bybit Demo Trading (Testnet)
- [ ] `docs/SECURITY.md` finalisieren; `security/secrets.py` (Env → OS-Keychain, Demo/Live getrennt)
- [ ] `net/`-Bybit-Client: HMAC-Signing, per-Endpoint- + IP-Rate-Limits, WS-Auth + Heartbeat + Resubscribe, Server-Zeit-Sync
- [ ] `execution/brokers/bybit_broker.py`: **Testnet**, place/cancel/amend, Positions, Wallet, Executions, private WS
- [ ] Trade-only-Keys **ohne Withdrawal**; IP-Allowlist; isolated margin, one-way position mode

## Phase 10 – Execution + Reconciliation
- [ ] `execution/reconciliation.py`: Positions- + Balance-Abgleich, Orphan-Order-Erkennung, Unexpected-Position → Asset-Kill-Switch
- [ ] Order-Lifecycle vollständig: ACK/Reject/Cancel/Replace/Partial/Expire + Timeouts je Übergang
- [ ] `safety/kill_switch.emergency_flatten()` — getesteter Pfad, regelmäßige Drills auf Testnet
- [ ] `core/clock.py` + `net/`: NTP-/Drift-Check gegen Bybit-Serverzeit

## Phase 11 – Monitoring + Journal + Analytics
- [ ] `ops/metrics.py`: In-Process-Registry (Counter/Gauge/Histogram), snapshot-bar
- [ ] `ops/health.py`: `SystemHealth`-Aggregat (Provider + Broker + Data-Quality + Kill-Switch + Heartbeat)
- [ ] `ops/watchdog.py`: Heartbeat → Kill-Switch bei Event-Loop-Stall
- [ ] `safety/audit_log.py`: append-only, Hash-Chain (Config/Keys/Orders/Kill-Switch/Overrides/Approvals)
- [ ] `safety/error_handling.py`: Fehlerklassen, Degradations-Modus
- [ ] `journal/performance.py`: Kennzahlen je Setup/Regime/Session/Tier; Setup→Score-Feedback (governt, OOS)
- [ ] `ops/runbooks/`: Incident-Playbooks; Backup-Skript für `data/` + Restore-Test

## Phase 12 – Dashboard *(neu durch Audit)*
- [ ] `api/` (FastAPI): REST + WS — Scanner, Chart, Signale, Portfolio, Risk, Positionen, Performance,
      Journal, News, AI-Reasoning, Alerts, System-Health, Execution-Status
- [ ] `ChartDataAPI` + `ChartAnnotationsAPI` + WS-Update-Stream (Live-Chart mit Setup-Overlays)
- [ ] **Approval-Endpoint** „BUY bestätigen?" (Demo/Live; Backtest/Paper ohne manuelle Freigabe)
- [ ] Frontend (separates Projekt, React/Vue) mit Lightweight Charts
- [ ] „Jeder Trade nachvollziehbar": DATA→ANALYSIS→SETUP→SCORE→RISK→SIGNAL→APPROVAL→ORDER→FILL→MANAGEMENT→EXIT im Ledger, gemeinsame `trace_id`

## Phase 13 – Production Readiness
- [ ] Deployment (systemd/Docker + Restart-Policy), Deployment-/Strategie-/Config-Versionierung in jedem Ledger-/Audit-Eintrag
- [ ] Vollständige Kill-Switch-Hierarchie + Persistenz-Tests (global/broker/asset/strategy/data)
- [ ] State-Recovery-/Crash-Recovery-/Graceful-Shutdown-Tests
- [ ] Parity Backtest=Paper=Demo grün; Monte-Carlo-Bänder halten in Demo
- [ ] Alle Freigabe-Gates aus `anti-overfitting.md` §9 dokumentiert erfüllt

## Phase 14 – erst danach: begrenzter Live-Betrieb
- [ ] **Separate, ausdrückliche Nutzer-Entscheidung** — nicht Teil dieses Plans
- [ ] Kleinster Umfang, ein Instrument, strengste Limits, manueller Kill-Switch griffbereit
- [ ] Kapital-/Risiko-Freigabeprozess (Mensch-in-the-loop für jede Limit-Erhöhung)
- [ ] `risk_pct` (0.50/0.35/0.25 %) final durch Backtest/OOS/Walk-Forward/Drawdown bestätigt + freigegeben

---

## Querschnitt (laufend)
- [ ] **Eine Strategy Engine** für Backtest/Paper/Live — nie forken; erzwungen über `runtime/` (gleiche Subscriber)
- [ ] **`portfolio/` ↔ `investment/` ↔ Trading strikt getrennt** — beide lesen `portfolio/`, keine rechnet nach
- [ ] Testabdeckung > 80 % für `core`, `risk`, `strategy`
- [ ] Decision Ledger (Ereignis-Trace) & Audit Log ab Phase 4/5 bei jeder Entscheidung befüllt
- [ ] Reproduzierbare Backtests (`RunManifest` → deterministischer Output-Hash)
- [ ] LLM/AI-Guardrails in jedem relevanten Test mitgeprüft (kein Bypass von Risk/Veto/Limits/NO_TRADE)
- [ ] Provider-Adapter nie mit echten Netzwerk-Calls im Test (`respx`/`pytest-httpx`)
- [ ] Monolith-first: keine Message-Queue-/Microservice-/k8s-Infra ohne echten Multi-User-Bedarf

## Erledigte Design-Entscheidungen
- [x] Datenmodell: Pydantic v2 (Ränder) + `@dataclass(slots=True)` (heißer Kern)
- [x] Persistenz: Parquet (Candles) + SQLite (Events/Ledger/Journal/State/Registry)
- [x] Zeitzonen: intern UTC; Sessions in Börsenlokalzeit definiert, DST-korrekt aufgelöst
- [x] Backtest-Engine: schlanker Eigenbau (Event-getrieben) wegen SMC-Logik
- [x] Multi-Asset-Kalender: eigene `refdata/calendar.py` (später ggf. Bibliothek ergänzen)
- [x] MVP-Umfang: Crypto BTCUSDT + ETHUSDT, HTF D1/H4, Entry M15/M5, Setup `SMC-SWEEP-REV-01`
