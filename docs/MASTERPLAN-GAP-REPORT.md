# Masterplan — Gap Report (2026-08-31)

Vergleich des **tatsächlichen Codestands** gegen den 71-Punkte-Masterplan.
Marker: ✅ DONE · 🟡 PARTIAL · 🟠 PREPARED · 🔴 MISSING · ⛔ BLOCKED.

> **Update 2026-08-31 (nach Stufen A–I + Governance):** 1040 Tests grün, mypy-strict clean.
> Stufen A–I sind code-seitig abgearbeitet (`docs/STAGE-*-2026-08.md`). Zusätzlich:
> - **Strategy-Edge-Investigation** (`docs/STRATEGY-EDGE-INVESTIGATION-2026-08.md`): 6 Setup-
>   Konstruktionen getestet, **keine mit robuster OOS-Edge** auf den verfügbaren Daten. Der
>   Engpass ist die Strategie-Edge selbst, nicht das Regime-Gate.
> - **Data-Roles / Live-Decision-Governance** (`docs/DATA-ROLES-LIVE-DECISION.md`): neues
>   `governance/`-Paket — Historical=Validierung, Live=Entscheidung, Recent=Edge-Check.
>   `ValidationRegistry` gated Live-Signale (Default: alles UNVALIDATED → SHADOW).
> - **Gold/FX-Daten**: Dukascopy wieder erreichbar, XAUUSD-3-Jahres-Ingest läuft. FX folgt.
> - **Weiterhin BLOCKED**: FRED-Key, Aktien-Datenquelle, News-Feed, Telegram-Token, FastAPI+
>   Frontend, cTrader/OANDA-Demo. Echtgeld ausgeschlossen (kein OMS-Order-Lifecycle gebaut).

## Gesamtbild in einem Satz

Die **Analyse-, Signal-, Scanner-, Portfolio-Intelligence-, Ops- und UI-Daten-Schicht** ist
gebaut und getestet. Die **Governance-Schicht** (welche Strategie darf live?) ist gebaut. Der
verbleibende Kern-Blocker: **keine Strategie hat eine belegte OOS-Edge** — daher laufen alle
Live-Signale als SHADOW. Fortschritt Richtung Echtgeld hängt an (A) mehr Daten + Re-Research,
(B) Hypothese neu fassen, und an vier externen Zugängen/Entscheidungen.

---

## 1. Endziel & Kernregeln (§1, §2, §65, §66, §67)

| § | Punkt | Status | Anmerkung |
|---|---|---|---|
| 1 | 24/7 AI Trading + **Portfolio Intelligence** | 🟡 | Analyse-/Trade-Seite gebaut; Portfolio-Intelligence-Seite fehlt komplett |
| 2 | Swing primär, Day sekundär, kein Scalping | 🟠 | MTF-Param-Sets für Swing/Day konzeptionell da; **kein `horizon`-Label je Setup** |
| 65 | No Blind AI — DATA→CONTEXT→EVIDENCE→SCORE→DECISION | ✅ | `EvaluationResult` = Decision + **alle** Zwischenreports; `reason_codes`; Explainability ist Design-Invariante |
| 66 | Noch kein Echtgeld | ✅ | `orders_sent == 0` überall asserted; kein Order-Code; Config lehnt live-Mode ab |
| 67 | 15 Kernregeln | ✅ | Alle im aktuellen Design respektiert (kein Zwang, NO_TRADE gültig, R:R+Risk Pflicht, keine ETFs, keine Echtgeldorders) |

## 2. Marktsegmente & Datenquellen (§3, §56, §57)

| § | Punkt | Status | Anmerkung |
|---|---|---|---|
| 56 | Provider-Architektur (Adapter → Normalized → MarketContext → Analyse → Regime → Ranking → Strategy → Risk → Portfolio → Paper → UI) | ✅ | Exakt diese Schichtung existiert. Neuer Provider = einfach (Binance diese Session in ~1 h ergänzt). Fehlt: der **Ranking**-Knoten und **UI**. |
| 3 / 57 | Crypto (BTC/ETH/+) | ✅ | 6 Symbole live + historisch (Kraken/Bybit/Binance public + Binance-Vision-Bulk) |
| 3 / 57 | Gold (XAUUSDT Binance) | ✅ | Live + 9-Monats-Historie ingestiert, durch die Engine gefahren |
| 3 / 57 | Forex (EURUSD/GBPUSD/USDJPY) | ⛔ | cTrader-App wartet auf Spotware-Freigabe; Yahoo nur indikativ; Dukascopy-Historie-Adapter da |
| 3 / 57 | **Einzelaktien** (keine ETFs) | 🔴 | Keine Aktien-Datenquelle verbunden (`equities.py` = nur Corporate Actions). Polygon/Finnhub in `providers.example.yaml` geplant, `enabled: false` |
| 57 | Kraken / Bybit EU Accounts | ✅ | Read-only verbunden + verifiziert (`cannot_trade`, `no withdraw`) |
| 57 | Binance Account | ✅ | Read-only verbunden + verifiziert (`enableReading` only) |
| 57 | Pepperstone / cTrader | ⛔ | Pausiert (App-Freigabe) — **nicht anfassen** |
| 57 | Trade Republic | 🔴 | Später, nur manueller Import geplant |

## 3. Analyse-Engine (§8–§14) — P0

| § | Punkt | Status | Anmerkung |
|---|---|---|---|
| 8 | MTF (D1/H4/H1/M15/M5) | 🟡 | `analysis/mtf.py`: M5-Basis → **M15/H4/D1**. **H1 nicht in der Kette.** HTF-Context/Mid-Structure/Entry-TF getrennt ✅. Swing- vs Day-TF-Set — 🟠 |
| 9 | Market Structure (HH/HL/LH/LL, BOS, CHoCH, intern/extern) | ✅ | `strategy/primitives/structure.py` + `swings.py` — 24 Tests. Range-Bruch → BOS `origin=RANGE`. |
| 10 | Liquidity (Equal H/L, Sweeps, Session-Level, Pools, Stop-Runs, False Breakouts) | ✅ | `strategy/primitives/liquidity.py` (436 Z.) + `analysis/sessions.py`. **PDH/PDL/PWH/PWL** — 🟡 (Session-High/Low ja, Vortag/Vorwoche als eigenes Level teilweise) |
| 11 | SMC / Price Action (FVG, IFVG, OB, Breaker, Displacement, Rejections, Momentum Shift) | ✅ | `primitives/imbalance.py` (400 Z., FVG+IFVG), `blocks.py` (OB+Breaker), `price_action.py` (Engulfing/Pin/Minor-CHoCH **nur als Gate**, nie allein) |
| 12 | Support / Resistance | 🟡 | Als **opposing-Liquidity-/Struktur-Level-Proxy** in `gates.py` (TP-Findung) + PD-Referenzen. **Kein eigenständiges S/R-Modul** (`analysis/support_resistance.py` = 4-Zeilen-Stub). „Support→Resistance"-Flip nicht explizit. |
| 13 | Volume / Order Flow (Volume, RVOL, Volume Profile, CVD, Delta, OI, Liquidations, Funding) | 🟡 | Volume in OHLCV ✅. **Funding + Open Interest** → `DerivativesContext` (Bybit + Binance, nur bei validen PIT-Daten) ✅. **CVD / Delta / Volume Profile / Liquidations / RVOL — 🔴** |
| 14 | Volatility Engine (ATR, Realized Vol, Implied Vol, Compression/Expansion) | 🟡 | ATR überall ✅ (`primitives/atr.py`), Regime-Vol-Klassifikation (Perzentile) ✅, Compression/Expansion implizit im Regime. **Kein dediziertes Volatility-Engine-Modul**, keine Realized/Implied-Vol-Zeitreihe. |

## 4. Market Regime (§7) — P0

| § | Punkt | Status | Anmerkung |
|---|---|---|---|
| 7 | Market Regime erkennen + beeinflusst Strategy/Entry/SL/TP/Size/Confidence/Ranking | 🟡 | `analysis/regime.py` (683 Z.): **Directional** (TREND_UP/DOWN/RANGE) + **Volatility** (Perzentil-Klassen) + **Phase** + `RegimeTracker` (Hysterese) + MTF-Konsens D1+H4 + `regime_gate` (NO_TRADE). **OOS-kalibriert, Baseline gesperrt.** Beeinflusst Strategy/Score ✅. Die Masterplan-States **BREAKOUT / MEAN_REVERSION / TRANSITION / EVENT_RISK** sind **nicht** als eigene Regime-Zustände modelliert (Breakout via Struktur, Event-Risk via News-No-Trade). Regime → Position-Size/Ranking noch nicht verdrahtet. |

## 5. Signal & Setup (§22–§32) — P2

> **Update 2026-08-31:** **2. Setup-Typ `SETUP-BREAKOUT-RETEST-01`** gebaut + integriert
> (`strategy/setups/breakout_retest.py`). H4-Konsolidierung → Ausbruch → haltender Retest →
> Einstieg in D1-Trend-Richtung. Eigene FSM/Geometrie/Confidence. `evaluate_from_mtf` fährt
> ihn parallel zur (unveränderten) SMC-Kette; greift nur, wenn SMC nicht actionable ist.
> Status `IN_VALIDATION` → Signale = SHADOW. §24 „Warum/Invalidation/Risiken" ist mit
> `strategy/signal_report.py` **erledigt** (Stufe D).

| § | Punkt | Status | Anmerkung |
|---|---|---|---|
| 22 | Signal-Engine-Zustände (NO_TRADE/WATCH/SETUP_FORMING/BUY_SETUP/…/EXIT_REQUIRED) | ✅ | `strategy/signal.py`: 12-State-Lifecycle, append-only Revisionen, `SetupState`-FSM `SCANNING→…→ARMED`. 2. Setup-Typ hat eigene FSM (`BreakoutState`). |
| 23 | Tiers A+/A/B/C/NO_TRADE, A/A+ prominent | 🟡 | `strategy/scoring.py` → **A+/A/B/NO_TRADE** (aus `final_score × setup_confidence`). **Kein „C"-Tier.** „prominent anzeigen" = UI, fehlt. |
| 24 | Konkretes strukturiertes BUY/SELL-Signal (Entry/SL/TP1-3/RR/Score/Conf/Risk/Setup/Warum/Invalidation/Risiken) | 🟡 | `Decision` trägt `direction/entry/sl/tp1/tp2/tp3_ref/rr_to_tp2/blended_rr/score/confidence/tier/reason_codes/setup_id/strategy_version`. **Es fehlt:** die Prosa-Felder „Warum / Invalidation / Risiken" als menschenlesbarer Text (Evidenz liegt als `ConfluenceReport`/`ContradictionReport` strukturiert vor, wird aber nicht zu Text gerendert). |
| 25 | Entry (Market/Limit/Confirmation) | ✅ | `EntryMode`: LIMIT_AT_PROXIMAL_EDGE / LIMIT_AT_MID / CONFIRMATION_MARKET |
| 26 | Stop Loss (Structure/Liquidity/ATR/Volatility) | ✅ | `gates.py`: SL = ungünstigere von (Sweep-Extrem, distale Zonenkante) + `sl_buffer_atr`·ATR, Cap/Floor (V10) |
| 27 | Take Profit TP1/TP2/TP3 (Liquidity/Structure/S-R/ATR/RR) | 🟡 | **TP1 + TP2** voll berechnet (nächste/​signifikante opposing Liquidität oder H4/M15-Swing, R-Cap/Floor). **TP3 = nur `tp3_ref`-String**, kein berechneter Preis. |
| 28 | **Dynamische 24/7 Signal-Anpassung** (KERNFEATURE) | ✅ | `SignalTracker.ingest` bewertet **jeden** MarketContext neu; `_diff` → STRENGTHENED/WEAKENED/ENTRY_/SL_/TP_CHANGED/INVALIDATED/EXPIRED; `ContinuousEvaluator` in der LivePipeline. Entry/SL/TP/Score/Confidence werden neu berechnet. |
| 29 | Aktive Trades permanent überwachen | ✅ | `PositionManager.on_reevaluation` → EXIT_REQUIRED; LivePipeline führt Positionen bar-für-bar fort |
| 30 | Trade Management (Entry→TP1→Partial→Runner→TP2→TP3→Exit; Structure/Liquidity/ATR/Trailing-SL) | ✅ | `PositionManager` (606 Z.): Pending-Fill, TP1/TP2/Runner, SL→BE, Trail nach TP2, worst-case-Fill, MFE/MAE in R. `execution/trade_management.py` = Stub (die Broker-Ausführungsschicht, Phase 8) |
| 31 | Signal Versioning (ID/Version/Timestamp/Score/Entry/SL/TP/Status/Reason/Context/StrategyVersion) | ✅ | `strategy/signal.py` append-only + `journal/ledger.py::record_decision` (Storage) |
| 32 | No-Trade-Engine | ✅ | `strategy/no_trade.py` (8 Gruppen, 34 Tests). Deckt **alle** Masterplan-Bedingungen: RR, unklare Struktur, fehlende Confluence, schlechtes Regime, Event-Risk, Spread, Volatilität, Datenprobleme, Portfolio-Risk |
| 63 | Trading Horizon (SWING/DAY, Swing-Priorität) | 🟠 | Kein `horizon`-Feld am Setup. Swing/Day über verschiedene MTF-Param-Sets denkbar, aber nicht implementiert/priorisiert |

## 6. Risk & Paper & Backtest (§44–§49) — P1/P2/P3

| § | Punkt | Status | Anmerkung |
|---|---|---|---|
| 44 | Zentrale Risk-Engine (Risk/Trade, Size, Max Exposure, Portfolio Risk, Correlation Risk, DD, Daily/Weekly Loss Limit, Kill-Switch) | 🟡 | `risk/`: `RiskLimits` (hard_max 2 %, Bänder 1.00/0.65/0.40), `size_position`, `RiskEngine.review` (**strukturell nicht durch Score überstimmbar** ✅), `safety/kill_switch.py` (hierarchisch, persistiert). **Daily/Weekly Loss Limit** — 🟡 (Bänder + Streak vorhanden, explizite Tages-/Wochen-Limits teilweise). **Correlation Risk** — 🟠 (nur `ClusterMap`-Näherung). |
| 45 | Paper Trading (Entry/SL/TP/Partial/P&L/R/MFE/MAE/Duration/ExitReason/Fees/Spread/Slippage) | 🟡 | Mechanik + alle Felder in `PaperPosition` **gebaut + getestet** (29 Tests). **Aber: 0 echte Paper-Trades** — jeder Backtest/Live-Lauf endet bei NO_TRADE (Regime-Gate). `execution/oms.py` (Order-Lifecycle-State-Machine) = Stub. |
| 46 | Backtesting (kein Lookahead/Leakage/Overfitting; Spread/Slippage/Fees/Funding; OOS/Walk-Forward/Monte-Carlo/Stress) | 🟡 | `engine/backtest.py`+`replay.py`: deterministisch, PIT, Look-ahead-Beweis (`engine/parity.py`), Continuity-Check, `chronological_split` (OOS). Kosten-Modell (`strategy/costs.py`, Default 0). **Monte-Carlo** (`research/robustness.py`, Bootstrap + Cost-Stress) ✅. **Walk-Forward** — 🟡 (Folds im Kalibrier-Harness, nicht generisch). **Stress-Tests** — 🟠. |
| 47 | Performance-Engine (WinRate/PF/Expectancy/AvgR/MaxDD/Sharpe/Sortino/MFE/MAE/HoldTime; aufgeteilt nach Asset/Strategy/Setup/Regime/TF/Score) | 🟡 | `engine/backtest_metrics.py`: WinRate/PF/Expectancy/AvgR/MedianR/StdevR/MaxDD-R/LossStreak/MFE/MAE/HoldTime + Segmente (Long/Short, Score-/Confidence-Tier, Exit-Grund, Asset) + Score-vs-Ergebnis-Buckets + Pearson-Korrelation. **Sharpe/Sortino/Calmar — 🔴**. **Nur Backtest, kein Live-Performance-Tracking.** |
| 48 | Trade Journal (Warum Entry/Exit, Setup, Score, Regime, Confluences, was lief gut/schlecht) | 🟠 | `journal/ledger.py` (`TradeRecord`, `record_decision`, `record_trade`) = **Storage-Schicht** ✅. `journal/trading_journal.py` (die Narrativ-/Review-Schicht) = 4-Zeilen-Stub. `journal/performance.py` = Stub. |
| 49 | Strategy Feedback (kontrolliert: Signal→Ergebnis→Statistik→Vorschlag→Test→neue Version) | 🟠 | `research/` (registry, validation, robustness, metrics) = die Werkzeuge. **Kein automatisierter Feedback-Loop.** Regime-/Struktur-Kalibrierung wurde **manuell** gemacht (Audits 14/17). |
| 64 | Professional Decision Log (Input/Context/Analysis/Score/Decision/Reason/Timestamp/DataVersion/StrategyVersion) | 🟡 | `journal/ledger.py::record_decision` speichert genau das. `EvaluationResult` trägt alle Zwischenreports. **Noch nicht in die LivePipeline verdrahtet** (`journal/decision_ledger.py` = Stub, `utils/tracing.py` = Stub). |

## 7. Scanner & Ranking (§4, §5, §6) — P1

| § | Punkt | Status | Anmerkung |
|---|---|---|---|
| 4 | **24/7 Market Scanner** (discovery, nicht nur Watchlist; sucht Trends/Breakouts/Sweeps/Reversals/Vol-Expansion/…) | 🔴 | `scanner/scanner.py` = `ScannerShell` (Phase-2B-**Platzhalter**, zählt nur Bars). `LivePipeline` fährt eine **feste Instrument-Liste** durch die Engine — **keine Markt-Entdeckung**, kein „scanne alle liquiden Symbole". `runtime/scheduler.py`, `scanner/signal_engine.py`, `scanner/alerting.py` = Stubs. |
| 5 | **TOP OPPORTUNITIES** (permanentes Cross-Asset-Ranking, dynamisch, erklärbar „warum #1") | 🔴 | **Existiert nicht.** Es gibt **kein** Modul, das Assets gegeneinander rankt. Die einzige „Rangfolge" ist Setup-Kandidaten-State *innerhalb* eines Instruments. |
| 6 | Opportunity Score 0–100 über ~30 Faktoren (HTF Bias, Structure, Liquidity, Sweeps, BOS/CHoCH, FVG/OB, S/R, Momentum, Volume, RVOL, Volatility, ATR, MTF Confluence, R:R, Regime, News, Macro, Event Risk, Fundamentals, Funding, OI, Liquidations, Spread, Correlation) | 🟡 | `strategy/scoring.py` liefert einen **Setup-Score 0–100** aus **12 gewichteten Faktoren** (htf_bias, liquidity_quality, sweep_clarity, displacement, structure_shift, risk_reward, entry_location, fvg_quality, reclaim, regime_alignment, session_context, data_confidence). Das ist **enger** als der Masterplan-Opportunity-Score: **News/Macro/Fundamentals/Volume/RVOL/OI/Funding/Liquidations/Correlation/Spread** fließen **nicht** ein. Und es ist **kein Asset-Ranking** — nur „wie gut ist *dieses* Setup". |

## 8. Portfolio-Intelligence (§33–§43) — P5 — **die größte Lücke**

| § | Punkt | Status |
|---|---|---|
| 33 | Portfolio Hub (alle Konten in einer Ansicht) | 🔴 (Account-Adapter read-only da, **keine Konsolidierungsschicht**) |
| 34 | Consolidated Portfolio (Gesamtwert/Cash/P&L/DD/Exposure/Allocation/Correlation/Concentration/Risk) | 🔴 |
| 35 | Einzelne Portfolios öffnen | 🟠 (per-Konto-Adapter, kein einheitliches Modell) |
| 36 | Portfolio Intelligence (bestehende Positionen permanent analysieren) | 🔴 |
| 37 | Position Rating (0–100 + STRONG HOLD/HOLD/WATCH/REDUCE/EXIT/RE-ENTRY WATCH) | 🔴 |
| 38 | Exit Intelligence (EXIT WATCH → EXIT SIGNAL, mit Begründung) | 🟡 (nur für **Paper-Positionen aus der Engine**: `EXIT_REQUIRED`; **keine** „analysiere mein echtes Depot") |
| 39 | Re-Entry Engine (Asset nach Exit weiter beobachten, RE-ENTRY WATCHLIST → RE-ENTRY SETUP) | 🔴 |
| 40 | Portfolio Ranking (Rangliste bestehender Positionen) | 🔴 |
| 41 | Portfolio vs New Opportunities (Rotation, mit Erklärung, kein Auto-Verkauf) | 🔴 |
| 42 | Portfolio Health 0–100 (Diversifikation/Konzentration/Correlation/Exposure/Risk/DD/Vol/Cash/Regime/Quality) | 🟠 (`portfolio/engine.py`: Equity/DD/Heat/Cluster-Risk/Streak → `PortfolioContext`; **kein 0–100-Score, keine Diversifikations-/Konzentrations-Metrik**) |
| 43 | Allocation 50/50 (nicht hartcodiert, aber Überwachung) | 🟠 (korrekt nicht hartcodiert; **keine Allocation-Überwachung**) |
| 20 | Correlation Engine (BTC↔ETH, Gold↔DXY, Stocks↔Nasdaq …) | 🟠 (nur `ClusterMap` = feste Cluster-Zuordnung; **keine berechnete Korrelation**) |

## 9. News / Macro / Fundamentals (§15–§19, §21) — P6

| § | Punkt | Status | Anmerkung |
|---|---|---|---|
| 15 | News Engine (24/7, PIT, Importance/Impact/Event-Risk, nie blind BUY/SELL) | 🟡 | `analysis/news.py` (278 Z.): PIT-Filter, asset-spezifische Relevanz, Blackout/Pre-Positioning-Ban, `risk_off` nur aus expliziten Event-Typen — **gebaut + getestet**. **Kein Live-News-Feed verbunden** — `CsvEconomicCalendar`-Vertrag + `no_feed_context()` Fail-safe. |
| 16 | Macro Engine (Fed/ECB/CPI/PPI/NFP/GDP/PMI/Yields/DXY/VIX/Geopolitics/Risk-On-Off; asset-spezifisch) | 🟡 | `analysis/macro.py` (288 Z.): rate_cycle/inflation_trend/growth_trend/risk_sentiment aus **FRED-Vintages** (echtes PIT), `UNKNOWN` statt Fake — **gebaut + getestet**. `data/providers/fred_alfred.py` **braucht `FRED_API_KEY`** (kostenlos), nicht verbunden. `cross_asset.py` (DXY/Yields/VIX-Proxy) da. |
| 17 | Economic Calendar (High-Impact-Events, EVENT RISK anzeigen, Setup blocken/warten) | 🟠 | `news_calendar.py::CsvEconomicCalendar` (PIT, `CANONICAL_EVENTS` FOMC/CPI/PCE/NFP/ECB) = Vertrag. News-Blackout im No-Trade-Gate ✅. **Kein Live-Kalender-Feed, kein `EVENT_RISK`-Anzeige-Objekt.** |
| 18 | Stock Fundamentals (Revenue/EPS/Earnings/Guidance/Margins/Debt/CashFlow/Valuation/P-E/Growth/Analyst/Insider/Sector) | 🔴 | **Nichts.** Keine Aktien-Fundamentaldaten-Quelle. |
| 19 | Earnings Engine (Upcoming/Surprise/EPS/Revenue/Guidance/Post-Reaction; Earnings-Risk ins Ranking) | 🔴 | **Nichts.** |
| 21 | Market Breadth (Advance/Decline, New Highs/Lows, Sector Strength, RS, Risk-On/Off) | 🔴 | **Nichts.** |

## 10. Charts, UI, Alerts, Reports (§50–§53, §58–§62) — P4/P7

| § | Punkt | Status |
|---|---|---|
| 50 | Live Chart (Candles + Swings/Liquidity/FVG/OB/BOS/CHoCH/S-R/Entry/SL/TP1-3, Echtzeit) | 🔴 (`chart/annotations.py` = 4-Zeilen-Stub) |
| 51 | Alert-System (A+ Setup / Entry / TP / Move-SL / Invalidation / Exit / Re-Entry / Portfolio-Risk / News / Vol-Spike) | 🟡 (`strategy/alerts.py`: 15 Event-Typen, Dedup, Cooldown, Auto-Update — **gebaut + getestet**. **Zustellung** → Telegram = 🔴, `ops/notify.py` Stub) |
| 52 | Daily / Weekly Report | 🔴 |
| 53 | System Health (APIs/WS/REST/Feed/Latency/StaleData/MissingCandles/DB/Strategy/PaperEngine/Portfolio; GREEN/YELLOW/RED) | 🟡 (`ops/health.py::SystemHealth`: Provider/Broker/Data-Block/Heartbeat/Kill-Switch, `ok()`/`snapshot()`. **Latency/Stale-Candle/DB/Paper-Engine-Checks fehlen**, GREEN/YELLOW/RED nur als `ok`-Bool + worst-provider) |
| 58–62 | Finale App (10 Bereiche) + Overview + Detail-Views + Action Center | 🔴 (`api/` leer, **keine UI**) |

## 11. 24/7-Betrieb, Watchdog, Security (§53–§56) — P7/P8

| § | Punkt | Status | Anmerkung |
|---|---|---|---|
| 54 | Watchdog / Recovery (Reconnect → Recover → State Restore; Zustand nach Neustart erhalten) | ✅ | `runtime/supervisor.py::LiveSupervisor` (347 Z.): Recovery aus atomarem Snapshot, 2-stufiger WS-Reconnect, Watchdog, REST-Gap-Backfill, Wall-Clock-Deadlines (Sleep-fest). `state/store.py` + `state/recovery.py`. 18-min-Live-Test + Restart verifiziert. |
| 55 | Security (keine Hardcodes, .env/Secrets, keine Secrets in Logs/Git, minimale Rechte, read-only) | ✅ | `security/secrets.py` (`Secret`-Redaction, ENV→Keychain), `.gitignore` deckt `.env`/`secrets/`/`*.key`, alle Account-Adapter read-only + `assert_read_only()`, Logging-Redaction. **`docs/SECURITY.md`** — 🔴 (nicht geschrieben). **Kein `git`-Repo** → keine gitleaks-Hooks. |
| 68 | Gap Report | ✅ (dieses Dokument) |

---

## Empfohlene Reihenfolge

Der Masterplan-Prioritätsbaum (§69) passt gut. **Angepasst an den realen Stand:**

### Stufe A — Fundament schließen (klein, entblockt alles Weitere)
1. **`git init` + erster Commit + gitleaks-pre-commit** — es gibt **keine** Versionskontrolle. Jede weitere Arbeit ist ohne das fragil. *(braucht `xcode-select --install` o. Homebrew-git)*
2. **`docs/SECURITY.md`** schreiben.
3. **Decision-Log in die LivePipeline verdrahten** (`journal/ledger.py` existiert, nur nicht verbunden) + `utils/tracing.py` (`trace_id`). Damit jede 24/7-Entscheidung nachvollziehbar persistiert wird (§64) — **Voraussetzung** für Ranking/Scanner-Debugging.

### Stufe B — Die Strategie überhaupt validieren (P1, der eigentliche Engpass)
4. **Dukascopy XAUUSD Spot 2 Jahre** ingestieren (Adapter steht) → langer Gold-Backtest.
5. **H4-Struktur-Klassifikator isoliert kalibrieren** (`derive_structure_state`) — `regime_unclear` ist über **alle** Assets die #1-Blockade (H4 `unclear` 93 %). Eigenes IS/OOS-Item, **Gate nicht lockern**.
6. Wenn dann Trades entstehen: **Entry/Exit-Qualität + Score/Confidence-Informationswert** messen. Ggf. **2. Setup-Typ** (Trend-Continuation) für die `unclear`-Phasen.
7. **Sharpe/Sortino** in `backtest_metrics.py` ergänzen; **Walk-Forward** generisch machen.

### Stufe C — Scanner + Ranking (P1, das Herzstück des Masterplans)
8. **`OpportunityScore`** bauen: erweitert `strategy/scoring.py` um die fehlenden Faktoren (News/Macro/Volume/RVOL/OI/Funding/Spread/Correlation), asset-übergreifend normiert, **erklärbar**.
9. **`MarketScanner`** (echtes `scanner/scanner.py`): `runtime/scheduler.py` (Bar-Close-getaktet, nicht-blockierend) → `strategy.evaluate` über ein **konfigurierbares Universum** (nicht nur Watchlist) → nur relevante Ergebnisse.
10. **`TopOpportunities`** — Cross-Asset-Ranking-Store, 24/7 aktualisiert, mit „warum #1"-Begründung aus dem `OpportunityScore`-Detail. Als Bus-Event + persistiert.
11. **`SignalReport`** (§24) vervollständigen: die Prosa-Felder (Warum/Invalidation/Risiken) aus `ConfluenceReport`/`ContradictionReport` rendern. TP3 als berechneten Preis.

### Stufe D — Paper Trading vollständig (P3)
12. **`execution/oms.py`** (Order-Lifecycle-State-Machine, Idempotenz), **`execution/brokers/sim_broker.py`**, **`journal/trading_journal.py`** + **`journal/performance.py`** (Live-Performance, nicht nur Backtest). Ziel: ≥ 100 echte Paper-Trades (setzt Stufe B voraus — ohne Setups keine Trades).

### Stufe E — Portfolio-Intelligence (P5, die größte fehlende Hälfte)
13. **`PortfolioHub`**: einheitliches `Holding`-Modell + Konsolidierung über die Read-only-Adapter (Kraken/Bybit/Binance) → `ConsolidatedPortfolio` (Wert/Cash/P&L/DD/Exposure/Allocation).
14. **`CorrelationEngine`** (echte rollierende Korrelation aus OHLCV) → ersetzt `ClusterMap`.
15. **`PositionIntelligence`**: jede bestehende Position durch die **gleiche** Analyse-Engine (MTF/Struktur/Liquidity/Regime/News) → `PositionRating` 0–100 + STRONG HOLD/…/EXIT + Begründung.
16. **`ExitIntelligence`** + **`ReEntryEngine`** + **`PortfolioRanking`** + **Rotation-Vergleich** (Portfolio vs. TopOpportunities).
17. **`PortfolioHealth`** 0–100.

### Stufe F — News/Macro/Fundamentals live (P6)
18. **FRED** verbinden (`FRED_API_KEY`, kostenlos) → `MacroContext` live.
19. **Live-News-/Economic-Calendar-Feed** wählen + verbinden (Finnhub free / andere) → `NewsContext` + `EVENT_RISK` live.
20. **Aktien-Datenquelle** (Polygon/Finnhub/andere) → Einzelaktien-OHLCV + **Fundamentals** + **Earnings** + **Market Breadth**. Danach Trade Republic (nur manueller Import).

### Stufe G — 24/7-Ops härten (P7)
21. `ops/watchdog.py` + `ops/notify.py` (Telegram) + `safety/audit_log.py` (Hash-Chain) + `ops/health.py` erweitern (Latency/Stale-Candle/DB) + **Daily/Weekly Report**.

### Stufe H — UI (P4, bewusst spät)
22. **`api/`** (FastAPI: REST + WS) → **`chart/annotations.py`** (Lightweight-Charts-Payloads) → Frontend (10 Bereiche, §58). Approval-Endpoint für später.

### Stufe I — Production Hardening → (später) Echtgeld (P8/P9)
23. Deployment, Kill-Switch-Drills, Parity Backtest≡Paper≡Demo, alle Freigabe-Gates. **Echtgeld-Execution = komplett separate Sicherheitsstufe, ganz am Ende.**

---

## Kürzeste Antwort auf „was fehlt am meisten"

1. **Cross-Asset Opportunity Ranking + echter Market Scanner** (§4–§6) — das definierende Feature des Masterplans, existiert praktisch nicht.
2. **Die komplette Portfolio-Intelligence-Hälfte** (§33–§43) — Hub, Position-Rating, Exit-/Re-Entry-Intelligence, Rotation.
3. **Eine validierte Strategie** — 0 Trades in jedem Backtest; ohne das ist alles Weitere Infrastruktur um eine unbewiesene Kernannahme.
4. **News/Macro/Fundamentals live** + **Einzelaktien-Datenquelle**.
5. **UI** (bewusst zuletzt) + **`git`** (sofort).
