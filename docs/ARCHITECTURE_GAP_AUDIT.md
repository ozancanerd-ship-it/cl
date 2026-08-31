# Architecture Gap Audit

**Datum:** 2026-08-28
**Umfang:** README.md, ARCHITECTURE.md, TODO.md, `config/*.example.yaml`, Package-Struktur `src/trading_agent/`
**Zweck:** Vor Beginn von Phase 1 prüfen, ob eine wesentliche Komponente für ein professionelles,
robustes, profit-orientiertes algorithmisches Multi-Asset-System fehlt.
**Status:** Audit abgeschlossen. **Phase 1 darf nach Einarbeitung der P0/MVP-Punkte beginnen.**

---

## Legende: „Benötigt ab"

| Stufe | Bedeutung |
|-------|-----------|
| **MVP** | Erster reproduzierbarer End-to-End-Backtest, 1 Asset, rein regelbasiert |
| **Paper** | Simulierte Fills gegen Live-/Delayed-Daten, kein Broker |
| **Demo** | Broker-Testnet/Demo-Konto (z. B. Bybit Testnet), echte API, kein Echtgeld |
| **Live** | Echtgeld |

Ein Punkt, der „ab MVP" markiert ist, muss auch in allen späteren Stufen vorhanden sein.

---

## 1. Bereits abgedeckt

Diese Punkte sind in Architektur/Struktur/Config bereits sinnvoll verankert (Konzept, noch nicht Code):

### Fundament & Architektur
- Brokerunabhängige Schichtung (`analysis`/`strategy`/`risk` kennen keinen Broker).
- Zentrale, brokerunabhängige Domänen-Typen als „Vertrag" (`core/types.py` – Zielbild dokumentiert).
- Injizierbare Zeitquelle `core/clock.py` (SystemClock vs. SimClock) – Grundlage für Determinismus.
- src-Layout, pytest/ruff/mypy, `make check`, dokumentierter „bauen→testen"-Workflow inkl. Definition of Done.
- Konfiguration in YAML, nur `*.example.yaml` versioniert, lokale Kopien + `.env` via `.gitignore` ausgeschlossen.

### Market Data (Konzept)
- UTC-Normalisierung intern, Timezone-Referenz in Config.
- Lückenerkennung + Qualitäts-Flags (`is_complete`, `has_gaps`), `reject_on_gaps`/`block_on_incomplete_data`.
- Resampling M1 → höhere Timeframes.
- Provider-Interface (ABC) + Mock/CSV vor echten Quellen.

### Analyse
- Market Structure (HH/HL/LH/LL, BOS, CHoCH, trend/range/consolidation/expansion/displacement).
- Liquidity (BSL/SSL, Equal Highs/Lows, Swing H/L, PDH/PDL, PWH/PWL, Session-Highs/Lows, Sweeps, Stop Hunts, False Breakouts).
- SMC (FVG, Inverse FVG, Order Blocks, Breaker Blocks, Mitigation, Imbalances, Premium/Discount, Displacement, Rejection, Engulfing, Pin Bars, Failed Breakouts).
- Support/Resistance, Sessions (Asia/London/Overlap/NY), News (Kalender + Sperrfenster), Macro (Regime, für Gold DXY/Yields/Fed, für Crypto Dominanz/Funding/OI/Liquidations).
- Multi-Timeframe-Kontext mit HTF-Bias-Propagation (D1→M1).

### Strategie
- Setup Detection → Confluence (Mindestzahl Faktoren, „kein Einzelindikator-Trade") → Scoring.
- 0–100-Score mit konfigurierbaren Faktor-Gewichten und Schwellen je Asset-Klasse (`scoring.example.yaml`).
- Pflichtfaktoren (`required_factors`).

### Risk (Konzept + Config)
- Vetorecht der Risk Engine gegenüber der Strategie.
- Limits: Risiko/Trade, Tagesverlust, Wochenverlust, Gesamt-Drawdown, max. Trades/Tag, max. offene Positionen, Portfolio-Exposure, korrelierte Exposure, max. Positionsgröße.
- `require_stop_loss`, `min_risk_reward`, `block_on_incomplete_data`, News-Blackout-Fenster.
- Verbote im Code erzwungen: kein Martingale, kein Nachkaufen von Verlusten, keine Risikoerhöhung nach Verlustserien.
- Slippage- und Spread-Schutz (Config-Schwellen).

### Execution (Konzept)
- Trade Management: TP1/2/3, Teilgewinn, Break-even, Trailing Stop, struktur-basierter Stop, Exit bei Invalidierung.
- Portfolio: offene Positionen, Exposure, Korrelationsmatrix, Gesamtportfolio-Risiko.
- Order Management: `OrderIntent`→Adapter, Duplicate-Order-Schutz, Order-Historie.
- `BrokerAdapter` als ABC, **keine** Implementierung in dieser Phase.

### Simulation & Auswertung (Konzept)
- Pipeline-Orchestrierung, Event-getriebener Backtest „look-ahead-frei" (als Anforderung benannt).
- Backtest-Kennzahlen: Win Rate, Profit Factor, Expectancy, Average R, Max Drawdown, MFE, MAE, Consecutive Losses, Performance je Setup/Session/Regime.
- Paper Trading über dieselbe Pipeline.
- Decision Ledger (vollständige Feldliste inkl. abgelehnter Entscheidungen), Trading Journal, Performance Analytics.
- Querschnitt-TODO: Testabdeckung, reproduzierbare Backtests (fixe Seeds, versionierte Config-Snapshots).

### Safety & Betrieb (Konzept)
- Monitoring (Health-Checks, Heartbeats, Schwellen-Alerts), append-only Audit Log, Error Handling (Retry/Backoff, Connection-/API-Failure, Degradations-Modus), globaler Kill Switch.

### Security (Konzept)
- Keine Secrets im Repo, `.env`/`config/*.yaml` gitignored, `.env.example` nur Platzhalter, Broker-Keys erst Phase 9.

---

## 2. Fehlt (nicht vorhanden – Konzept und Struktur)

Nummerierung `G-xx` = Gap. In Klammern die Kategorie aus dem Prüfauftrag.

| ID | Fehlende Komponente / Fähigkeit | Kategorie | Benötigt ab |
|----|--------------------------------|-----------|-------------|
| G-01 | **Reference-Data / Instrument-Master-Service** (Tick-Size, Lot-Size, Min-Notional, Margin-Tiers, Fee-Schedule, Kontraktspezifikation, Quote/Settle-Währung) – aktuell nur verstreut in `config` | Market Data / Execution | MVP |
| G-02 | **Symbol-Mapping-Layer** (kanonisches Symbol ↔ broker-spezifisch, z. B. `BTCUSDT`/`BTC-USD`/`XBTUSD`; `XAUUSD`/`GOLD`) | Market Data | Paper |
| G-03 | **Trading-Calendar / Market-Hours-Engine** (Handelstage, Feiertage, Half-Days, Aktien-Sessions Pre/Regular/After, 24/7-Crypto, Forex-Wochenendlücke) – getrennt von der intraday Session Engine | Market Data / Asset-specific | MVP (Aktien/Gold), Paper (alle) |
| G-04 | **Corporate-Actions-Service** (Splits, Reverse Splits, Dividenden, Symboländerungen, Mergers) inkl. **Backadjustment** historischer Kurse | Market Data / Stocks | MVP (falls Aktien im MVP), sonst Paper |
| G-05 | **Cost-Model als eigene Komponente** (Kommission/Fees, Maker/Taker, Funding, Borrow/Leihe, Spread, Slippage-Modell, Latenz, Market-Impact/Partizipation) – von **Backtest UND Live identisch** genutzt | Backtesting / Execution | MVP |
| G-06 | **Market-Data-Storage / Repository** (Persistenz roher & resampleter Candles, Funding-/OI-Historie, News-Historie; Point-in-Time-Abfrage) – offene Frage in TODO, aber kein Komponentenkonzept | Market Data / Research | MVP |
| G-07 | **Data-Quality- & Integrity-Monitor** (Stale-Data-Erkennung nach Alter, Duplikat-Candles, ungültige/rückläufige Timestamps, Preis-Ausreißer/Spike-Filter, OHLC-Konsistenz `low≤open,close≤high`, Volumen=0) + **Data-Quality-Kill-Switch** | Market Data / Risk | MVP (Checks), Paper (Kill-Switch) |
| G-08 | **DST-/Zeitzonen-Behandlung für Sessions** – Session-Fenster in `config.example.yaml` sind **statische UTC-Zeiten**; London/New-York verschieben sich mit Sommerzeit. Nötig: Sessions in Börsen-Lokalzeit definieren, zur Laufzeit nach UTC auflösen | Market Data / Strategy | MVP |
| G-09 | **Market-Regime-Engine als eigene Komponente** (Trend/Range-Klassifikation, Volatilitäts-Regime, Liquiditäts-Regime, für Crypto BTC-Regime/Dominanz-Regime) – heute nur impliziter Scoring-Faktor + Macro-Vermischung | Market Regime | MVP |
| G-10 | **Regime-abhängiges Strategie-/Risikoverhalten** (Setup-Filter, Positionsgrößen-Skalierung, Score-Schwellen je Regime; „Regime → aus/an") | Market Regime / Strategy | MVP |
| G-11 | **Feature-/Research-Dataset-Builder mit Point-in-Time-Korrektheit** (keine revidierten Makrodaten, keine Normalisierung mit Full-Sample-Statistiken, Feature-Lag) | Backtesting / Research | MVP |
| G-12 | **Validation-Harness** (Train/Test-Split, Out-of-Sample-Holdout, Walk-Forward, Purged & Embargoed CV für überlappende Label, Multiple-Testing-Bewusstsein) | Backtesting / Research | MVP |
| G-13 | **Robustness-/Monte-Carlo-Suite** (Trade-Reihenfolge-Bootstrap, Parameter-Perturbation, Slippage-/Fee-Stress, Start-Datum-Sensitivität, Equity-Curve-Konfidenzbänder) | Backtesting / Research | MVP |
| G-14 | **Experiment- & Parameter-Registry** (jeder Backtest-Run: Code-Version, Config-Hash, Datenbereich, Datensatz-Version, Seeds, Ergebnisse; kein „Cherry-Picking" ohne Historie) | Backtesting / Operations | MVP |
| G-15 | **Historical/Live-Data-Parity- & Shadow-Mode** (Backtest auf denselben Bars, die live entstehen; Bar-Close-Semantik; Parity-Report Signal-für-Signal) | Backtesting / Execution | Paper |
| G-16 | **AI / Reasoning-Layer (LLM) – vollständig undefiniert** trotz Projektname. Nötig: klare Grenze deterministische Regeln ↔ LLM, **LLM darf Risk Engine niemals umgehen**, strukturierte Outputs + Schema-Validierung, Halluzinations-/Unsicherheits-Handling, Confidence ≠ Score, Prompt-/Modell-Versionierung, Reproduzierbarkeit (temp=0, gepinnte Modell-ID), Modell-Ausfall → Fallback auf reine Regeln, volle Auditierbarkeit jeder LLM-Ein-/Ausgabe | AI / LLM | MVP (Grenzen + Schema), Paper (Fallback), Demo (Audit) |
| G-17 | **Reconciliation-Engine** (Order-Reconciliation und Position-Reconciliation: Broker-State vs. interner State; periodischer Abgleich; Drift-Alarm; Auto-Korrektur nur nach Regeln) | Execution | Demo |
| G-18 | **Broker/Exchange-Client-Layer** unter dem Adapter (REST + WebSocket, Reconnect mit Backoff, REST-Fallback bei WS-Ausfall, **Rate-Limit-Handling/Token-Bucket**, Circuit Breaker, Idempotenz-Keys/`clientOrderId`, Request-Signierung) | Execution | Demo |
| G-19 | **Order-Lifecycle-State-Machine** (NEW→ACK→PARTIALLY_FILLED→FILLED / REJECTED / CANCELLED / EXPIRED; Timeouts je Übergang; Wiederaufnahme nach Neustart) | Execution | Demo |
| G-20 | **State-Store & Recovery** (persistenter Zustand: offene Positionen, Orders, Kill-Switch-Status, Tages-/Wochen-Verlustzähler, Equity-Hoch; Restart-Recovery, Crash-Recovery, Graceful Shutdown) | Operations | Paper |
| G-21 | **Exposure-/Faktor-Risikomodell** (nicht nur Preis-Korrelation: USD-Exposure, Zins-/Yield-Beta, Aktien-Beta zu SPX/NDX, Crypto-Beta zu BTC, Gold↔USD; „doppelte ökonomische Exposure" erkennen; Portfolio-Risiko-Budget je Faktor/Strategie/Asset-Klasse) | Portfolio / Risk | Paper |
| G-22 | **Margin- & Leverage-Manager** (Leverage-Limit, Margin-Auslastung, **Liquidationsdistanz** und Mindestabstand, Maintenance-Margin je Tier) – für Bybit-Perps essenziell | Risk | Demo (Paper simuliert) |
| G-23 | **Volatilitäts-adjustierte Positionsgröße** (ATR-/Realized-Vol-basiert, Vol-Targeting) – `position_sizing` nutzt heute nur Risiko% × SL-Distanz | Risk | MVP |
| G-24 | **Granulare Kill-Switch-Hierarchie** (Strategie-Ebene, Asset-Ebene, Broker-Ebene, Daten-Ebene) zusätzlich zum globalen Not-Aus; jeweils persistiert und manuell überschreibbar | Risk / Safety | Paper (Strategie/Daten), Demo (Broker/Asset) |
| G-25 | **Concentration-Limits über Preis-Korrelation hinaus** (max. Exposure je Asset-Klasse, je Sektor bei Aktien, je Basiswert; „nicht 5 Altcoin-Longs = 1 BTC-Long") | Risk / Portfolio | Paper |
| G-26 | **Rejection-Reason-Taxonomie** (enumerierte, stabile Ablehnungsgründe für Risk Engine & Order Management; Auswertung „warum werden Setups verworfen") | Strategy / Risk / Analytics | MVP |
| G-27 | **Setup-/Regime-Statistik-Feedback-Loop** (aus Journal/Ledger zurück in Scoring-Gewichte/Schwellen; nicht in-sample tunen) | Strategy / Analytics | Paper |
| G-28 | **Config-Schema-Validierung & -Versionierung** (typisiertes Schema, Pflichtfelder, Wertebereiche, `schema_version`, Migrations-Hinweis, Fail-fast bei ungültiger Config) | Operations | MVP |
| G-29 | **Secrets-Management-Policy + technische Guards** (`docs/SECURITY.md`: Least Privilege, Read-only-Keys in Entwicklung, IP-Restriktion wo möglich, Rotation, Credential-Failure-Handling; **gitleaks/trufflehog Pre-Commit-Hook**, `pip-audit`, Log-Redaction) | Security | MVP (Policy + Hooks), Demo (Key-Handling) |
| G-30 | **Dependency-Lockfile / Hash-Pinning** (heute nur `>=`-Ranges → nicht reproduzierbar, Supply-Chain-Risiko) | Security / Research | MVP |
| G-31 | **Notification-/Alerting-Layer** (Kanal-Abstraktion: Konsole/Datei jetzt, später E-Mail/Telegram/Slack; Severity, Dedup, Rate-Limit, Eskalation) – Monitoring nennt „Alerts", aber kein Zustellweg | Operations | Paper |
| G-32 | **Incident-Runbooks & Backups** (`docs/runbooks/`: „Datenquelle tot", „Broker-Disconnect", „State-Drift", „Kill-Switch ausgelöst"; Backup von Ledger/Journal/State/Config; Restore-Test) | Operations | Demo |
| G-33 | **Deployment-/Strategie-/Config-Versionierung als Schema** (jede laufende Instanz kennt: Git-SHA, Strategie-Version, Config-Version; im Ledger & Audit-Log mitgeschrieben) | Operations | Paper |
| G-34 | **Asset-spezifische Event-Gates Aktien** (Earnings-Datum, Pre-/After-Hours-Verhalten, **Trading Halts / LULD**, Illiquiditäts-Filter, Gap-Handling) – `news.py` nennt nur „Earnings" | Stocks | Paper (falls Aktien), sonst Demo |
| G-35 | **Asset-spezifische Gates Crypto** (Token-Unlocks/Vesting-Kalender, Listing/Delisting, Exchange-Liquiditäts-/Depth-Check, Funding-Extreme, Wartungsfenster der Exchange) | Crypto | Paper |
| G-36 | **Partial-Fill- & Fill-Simulationsmodell im Backtest/Paper** (Teilfüllungen, Queue-Position-Näherung, kein Fill zum Signalpreis, Fill nur wenn Bar den Preis handelt, konservative Annahmen) | Backtesting / Execution | MVP |
| G-37 | **Clock-Synchronisation / Drift-Check** (NTP-Offset-Messung, Warnung/Degradation bei Drift; Server-Zeit des Brokers als Referenz) | Market Data / Execution | Demo |
| G-38 | **Data-Source-Health-Registry** (pro Quelle: letzter erfolgreicher Fetch, Fehlerquote, Latenz; Umschalten Primär→Sekundär; in Monitoring integriert) | Market Data / Operations | Paper |
| G-39 | **Kosten-/Funding-/Borrow-Historie als Datsatz** (für realistischen Crypto-Perp-Backtest: Funding-Rate-Historie; für Aktien-Shorts: Leihgebühren/Short-Verfügbarkeit) | Backtesting | MVP (Crypto), Paper (Aktien-Short) |
| G-40 | **„Flatten-All" / Emergency-Shutdown-Pfad, getestet** (kontrolliertes Schließen aller Positionen + Order-Stornierung; als expliziter, getesteter Code-Pfad, nicht nur Kill-Switch-Flag) | Safety | Demo (Paper simuliert) |

---

## 3. Teilweise abgedeckt

| ID | Thema | Was vorhanden ist | Was fehlt |
|----|-------|-------------------|-----------|
| P-01 | Look-ahead-Bias | Als Anforderung in ARCHITECTURE.md benannt, SimClock vorhanden | Kein technischer Mechanismus (Bar-Close-Gate, „nur abgeschlossene Kerzen", Feature-Lag-Prüfung, Test der Engine gegen Zukunftsdaten) |
| P-02 | Reproduzierbarkeit | „fixe Seeds, versionierte Config-Snapshots" im TODO | Kein Lockfile (G-30), kein Run-Manifest/Registry (G-14), keine Datensatz-Versionierung (G-06) |
| P-03 | Realistische Kosten | `max_slippage_pct`, `max_spread_pct` als **Schutzschwellen** | Kein **Kostenmodell** für Backtest-P&L (Fees/Funding/Borrow/Impact) – Schwellen ≠ Abzug (G-05, G-39) |
| P-04 | Stale/fehlende/doppelte Candles | `has_gaps`, `is_complete`, `reject_on_gaps` | Staleness nach Alter, Duplikate, ungültige Timestamps, OHLC-Konsistenz, Spike-Filter (G-07) |
| P-05 | Timezone/DST | „intern immer UTC" – korrekt für Speicherung | Session-Fenster statisch UTC statt Börsenlokalzeit → DST-Bug (G-08) |
| P-06 | Market Regime | Scoring-Faktor `market_regime`, `volatility`; Backtest „Regime-Performance"; `macro.py` | Kein eigenständiger Klassifikator, keine Definitionen, keine regime-abhängige Steuerung (G-09, G-10) |
| P-07 | Order Lifecycle | `order_management` nennt Duplicate-Schutz + Historie | Keine State-Machine, keine ACK/Reject/Cancel/Partial-Behandlung, keine Idempotenz-Keys (G-18, G-19) |
| P-08 | Reconciliation | „Broker-State" im ARCHITECTURE-Text erwähnt | Kein Abgleichprozess, kein Drift-Alarm, kein Recovery (G-17) |
| P-09 | Kill Switch | Globaler Kill Switch als Komponente | Keine Persistenz über Neustart, keine granulare Hierarchie (Strategie/Asset/Broker/Daten), kein getesteter Flatten-Pfad (G-20, G-24, G-40) |
| P-10 | Korrelierte Exposure | `max_correlated_exposure_pct`, `correlation_threshold` in `risk.example.yaml` | Nur rollierende Preis-Korrelation; keine ökonomische/Faktor-Exposure (USD, Yields, Beta, BTC-Beta) (G-21, G-25) |
| P-11 | Position Sizing | Risiko% × SL-Distanz × Tick-Value | Keine Vol-Adjustierung, kein Vol-Targeting, keine Margin-/Liquidationsprüfung (G-22, G-23) |
| P-12 | Error Handling | Retry/Backoff, Connection-/API-Failure, Degradations-Modus als Konzept | Kein Rate-Limit-Handling, kein Circuit Breaker, keine WS-Reconnect-/Backfill-Strategie (G-18) |
| P-13 | Monitoring/Alerts | Health-Checks, Heartbeats, Schwellen-Alerts | Kein Zustellkanal, kein Severity/Dedup/Eskalation (G-31); keine Data-Source-Health-Registry (G-38) |
| P-14 | Audit Log | append-only, „alle Orders & Sicherheitsereignisse" | Kein Format/Schema, keine Aufbewahrung/Integritätssicherung (Hash-Chain), keine Version/SHA-Referenz (G-33) |
| P-15 | News-Handling | Kalender + Sperrfenster (Blackout 15/15 min) | Datenquelle offen; keine Behandlung von **Daten-Revisionen** (Point-in-Time); keine Impact-Klassifikation dokumentiert |
| P-16 | Backtest = Paper Parität | „dieselbe Pipeline" als Ziel | Kein Parity-Test, keine gemeinsame Fill-/Cost-Engine erzwungen, keine Shadow-Mode-Stufe (G-15) |
| P-17 | Survivorship/Selection Bias | Nicht erwähnt | Keine Regel „Instrument-Universum Point-in-Time", keine Delisting-/Index-Constituent-Historie (G-06, G-11) |
| P-18 | Overfitting / Over-Optimization | Nicht erwähnt | Kein OOS/Walk-Forward/CV, keine Parameter-Sensitivität, kein Bewusstsein für Multiple Testing (G-12, G-13) |
| P-19 | Aktien-Assets | `instruments` enthält `AAPL` (disabled); `news.py` nennt Earnings | Keine Splits/Dividenden/Corporate-Actions/Halts/Kalender (G-03, G-04, G-34) |
| P-20 | Security | Keine Secrets im Repo, gitignore korrekt | Keine Policy, keine Pre-Commit-Secret-Scans, kein Lockfile, keine Least-Privilege-/Rotation-Regeln (G-29, G-30) |

---

## 4. Redundante / überlappende Komponenten

Keine davon ist „falsch" – aber die Grenzen müssen **vor** Phase 1 scharf definiert werden, sonst
entstehen doppelte Logik, widersprüchliche Ergebnisse und schwer testbare Zuständigkeiten.

| # | Überlappung | Problem | Empfehlung |
|---|-------------|---------|------------|
| R-01 | **Support/Resistance (6) ↔ Liquidity (4) ↔ SMC (5)** | Alle drei beschreiben „Preis-Levels/Zonen mit erwarteter Reaktion" (S/R-Zonen, Liquiditätspools, Order Blocks/FVGs) | Ein gemeinsames Zonen-Modell `Zone{typ, range, herkunft, status}` in `core/types.py`. S/R bleibt „klassische horizontale Reaktionszonen", Liquidity „Stop-/Pool-Level", SMC „Imbalance-/OB-/Breaker-Zonen". Alle schreiben in dieselbe Zonenliste des `MarketContext`. |
| R-02 | **Sessions (7) ↔ Liquidity (4)** | Session-Highs/Lows (Asian High/Low, London High/Low …) sind in **beiden** Listen | Sessions Engine besitzt die **Zeitfenster** und liefert die rohen Session-High/Low-Werte; Liquidity Engine **konsumiert** diese und markiert sie als Liquiditätspools. Keine Doppelberechnung. |
| R-03 | **Sessions (7) ↔ neue Trading-Calendar-Engine (G-03)** | „Wann ist Markt offen" vs. „welche intraday Liquiditäts-Session" | Trennen: Calendar = Handelstage/Feiertage/Marktöffnung je Instrument. Sessions = intraday Liquiditätsfenster (Asia/London/NY) innerhalb offener Zeit. |
| R-04 | **News/Fundamental (8) ↔ Macro Context (9)** | Beide behandeln Zentralbanken / Zinsen | News = **diskrete, terminierte Ereignisse** + Blackout-Fenster + Überraschungswert (actual vs. forecast). Macro = **kontinuierlicher Regime-Zustand** (DXY-Trend, Yields, Fed-Erwartung, Risk-on/off). |
| R-05 | **Macro (9) ↔ neue Market-Regime-Engine (G-09)** | „Regime" taucht in beiden auf | Macro liefert **makroökonomische Inputs** (Yields, DXY, Dominanz). Regime-Engine klassifiziert daraus + aus Preis/Vola den **handelbaren Zustand** (Trend/Range/Vol-hoch/…), den Strategy & Risk konsumieren. |
| R-06 | **Setup Detection (10) ↔ Confluence (11) ↔ Scoring (12)** | Alle drei „sammeln und bewerten Signale" | Detection = erzeugt **Kandidaten** (Richtung, Entry-Zone, SL, TP) aus Mustern. Confluence = **boolescher Gate** (Mindestfaktoren, Pflichtfaktoren, Ausschlusskriterien). Scoring = **numerische Gewichtung 0–100** der bereits gültigen Kandidaten. Reihenfolge Detection→Confluence→Scoring, kein Rücksprung. Erwägen: Confluence als Sub-Modul von Scoring, wenn die Trennung künstlich wirkt. |
| R-07 | **Risk Engine (13) ↔ Portfolio Engine (16)** | Beide berechnen Exposure/Korrelation/Portfolio-Risiko | Portfolio = **Zustand & Aggregation** (Positionen, Exposure, Faktor-Exposure, Korrelation) – keine Entscheidung. Risk = wendet **Limits** auf den Portfolio-Zustand an und hat das **Veto**. Risk berechnet nichts selbst neu, was Portfolio schon liefert. |
| R-08 | **Trade Management (15) ↔ Order Management (17)** | Stop-/TP-Anpassung erzeugt Orders | Trade Management = **Entscheidung** „Stop auf BE", „50 % Teilgewinn", „Exit" → erzeugt `OrderIntent`. Order Management = **Ausführung/Lifecycle** dieser Intents. Trade Management ruft nie den Adapter direkt. |
| R-09 | **Backtesting (18) ↔ Paper Trading (19)** | Getrennte Engines → Divergenzrisiko | Eine **gemeinsame Ausführungs-Simulation** (Fill-Modell + Cost-Model, G-05/G-36), die Backtest und Paper teilen. Unterschied nur: Datenquelle (historisch vs. live) und Zeitquelle (SimClock vs. SystemClock). |
| R-10 | **Monitoring (23) ↔ Performance Analytics (22)** | Beide „Metriken" | Performance = **Trading-KPIs** (Win Rate, PF, Expectancy, R …) aus Journal/Ledger, offline. Monitoring = **System-Gesundheit** (Latenz, Fehlerquote, Heartbeat, Datenalter), online. Getrennte Speicher, getrennte Alarme. |
| R-11 | **Decision Ledger (21) ↔ Trading Journal (20) ↔ Audit Log (24)** | Drei „Aufzeichnungs"-Stores | Ledger = **maschinenlesbarer Entscheidungs-Record je Kandidat** (inkl. abgelehnter), Input für Analytics. Journal = **Trade-Sicht für Menschen** (ein Eintrag je Trade, Ergebnis, Notizen, Chart-Referenz). Audit = **append-only, integritätsgesichert** (Orders, Kill-Switch, Config-Änderungen, Login/Key-Nutzung) – nie überschrieben. |
| R-12 | **Error Handling (25) ↔ Kill Switch (26) ↔ Monitoring (23)** | Reaktion auf Störungen | Error Handling = **lokale** Behandlung (Retry, Backoff, Fallback). Kill Switch = **globale/granulare Blockade** bei überschrittenen Schwellen. Monitoring = **Beobachtung + Alarm**, löst Kill Switch aus, führt ihn nicht selbst aus. |

---

## 5. Sicherheitsrisiken

| ID | Risiko | Schwere | Maßnahme | Benötigt ab |
|----|--------|---------|----------|-------------|
| S-01 | `.env.example` enthält (auskommentierte) `BYBIT_API_KEY`/`_SECRET`-Zeilen → Muster-Nähe, jemand füllt & committet `.env` oder kopiert in `*.example.yaml` | Hoch | **gitleaks/trufflehog als Pre-Commit-Hook ab Commit 1**; CI-Secret-Scan; Kommentar „NIEMALS echte Werte" verschärfen; Live-Keys gar nicht über `.env`, sondern OS-Keychain/Secret-Manager | MVP |
| S-02 | Kein Dependency-Lockfile (`requirements*.txt` nur `>=`) → Supply-Chain-/Reproduzierbarkeitsrisiko | Mittel | Hash-gepinnter Lockfile (`pip-compile`/`uv lock`); `pip-audit` in CI; Dependabot/Renovate | MVP |
| S-03 | Keine Log-Redaction → API-Keys/Tokens/Signaturen aus Broker-Responses landen in Logs/Audit | Hoch (ab Demo) | Redaction-Filter in `utils/logging.py`; Allowlist statt Blocklist für geloggte Felder; Broker-Response-Bodies nie roh loggen | Demo (Konzept ab MVP) |
| S-04 | Keine Least-Privilege-/Read-only-Regel dokumentiert → Entwicklung mit Trade-Berechtigung, unnötige Withdraw-Rechte | Hoch (ab Demo) | `docs/SECURITY.md`: Entwicklung nur Read-only-Keys; Demo Trade-only **ohne** Withdrawal; IP-Allowlist wo Broker es unterstützt; getrennte Keys je Umgebung | Demo (Policy ab MVP) |
| S-05 | Keine Secret-Rotation / Credential-Failure-Behandlung | Mittel | Rotationsintervall + Runbook; bei 401/403: Kill-Switch (Broker-Ebene) statt Retry-Sturm | Demo |
| S-06 | `data/journal/` (P&L, Kontostände, Strategie-Verhalten) – gitignored, aber keine Backup-/Zugriffs-/Verschlüsselungsregel | Mittel | Backup-Policy (G-32); bei Cloud-Ablage Verschlüsselung; kein Teilen von Journal-Dumps mit Dritten/Services | Paper |
| S-07 | `trade_republic_adapter` (Phase 9) – inoffizielle/App-APIs können AGB verletzen & Kontosperre riskieren | Hoch | Vor Implementierung rechtlich/AGB prüfen; nur offiziell zulässige Schnittstellen; im Zweifel read-only Portfolio-Import per manuellem Export | Live (Recherche ab jetzt) |
| S-08 | Kein Threat-Model / keine Trust-Boundary-Doku (welche Komponente darf Orders senden, welche nur lesen) | Mittel | Abschnitt in `SECURITY.md`; nur `order_management` darf den Broker-Trade-Endpunkt aufrufen; alles andere read-only | Paper |
| S-09 | Kill-Switch-Zustand nicht persistiert → nach Crash/Neustart „vergisst" das System, dass gestoppt war → ungewollte Orders | Hoch (ab Demo) | Kill-Switch-Status in State-Store (G-20), beim Start **fail-safe = gestoppt**, bis explizit freigegeben | Paper |
| S-10 | Keine Idempotenz bei Orderabsendung → Retry nach Timeout = Doppelorder = ungewollte Doppel-Exposure | Hoch (ab Demo) | Client-generierte `clientOrderId`/Idempotency-Key je `OrderIntent`; Broker-seitige Dedup nutzen; vor Retry immer erst Order-Status abfragen (G-18/G-19) | Demo |

---

## 6. Research- / Backtesting-Risiken

| ID | Risiko | Wirkung | Gegenmaßnahme | Benötigt ab |
|----|--------|---------|---------------|-------------|
| B-01 | **Look-ahead über Bar-Semantik** – Signal auf noch nicht geschlossener Kerze; Indikator sieht Kerze `t` bei Entscheidung für `t` | Backtest deutlich zu gut, live nicht reproduzierbar | Harte Regel „Entscheidung für `t+1` nur aus Daten ≤ `t` (abgeschlossen)"; Bar-Close-Gate in der Pipeline; Test: Engine gegen absichtlich „durchgereichte" Zukunftsdaten | MVP |
| B-02 | **Look-ahead über revidierte Daten** – Makro/Fundamental-Werte werden nachträglich korrigiert (CPI, GDP, Earnings-Restatements) | Strategie „weiß" den echten Wert vor Veröffentlichung | Point-in-Time-Datensatz (G-11): jeder Datenpunkt mit `available_at`-Zeitstempel; nur nutzen, was zum Entscheidungszeitpunkt publiziert war | MVP (Makro), Paper (Fundamentals) |
| B-03 | **Data Leakage über Normalisierung** – Z-Score/Min-Max/Skalierung mit Statistiken des gesamten Zeitraums | Optimistische Feature-Verteilung | Rolling-/Expanding-Window-Statistiken; Skalierer nur auf Train fitten (G-11/G-12) | MVP |
| B-04 | **Survivorship Bias** – nur heute existierende Coins/Aktien; delistete BTC-Altcoins & Pleiten fehlen | Renditen systematisch überschätzt, Tail-Risiko unterschätzt | Instrument-Universum **Point-in-Time** (was war damals liquide/gelistet); Delisting-/Halt-Historie im Repository (G-06) | MVP |
| B-05 | **Selection Bias** – Instrumente/Zeiträume gewählt, weil sie „gut aussehen" | Nicht generalisierbar | Universum & Zeitraum **vor** dem Test festlegen und im Run-Manifest einfrieren (G-14) | MVP |
| B-06 | **Overfitting / Parameter-Over-Optimization** – Scoring-Gewichte/Schwellen auf demselben Datensatz getunt, auf dem berichtet wird | Live-Enttäuschung | Train/Test-Split + OOS-Holdout, Walk-Forward, Parameter-Sensitivitäts-Heatmap; wenige robuste Parameter statt vieler feiner (G-12/G-13) | MVP |
| B-07 | **Multiple Testing** – viele Setups/Parameter probiert, bester „gewinnt" per Zufall | Scheinbarer Edge | Anzahl Trials protokollieren (G-14); Deflated Sharpe / härtere Signifikanzschwelle; OOS als finaler Schiedsrichter | MVP |
| B-08 | **Purged/Embargoed CV fehlt** – überlappende Trade-Label (Halte­dauer > Bar) lecken zwischen Train/Test | CV zu optimistisch | Purging um die Label-Horizonte + Embargo-Fenster nach jedem Test-Block (G-12) | MVP (wenn CV genutzt) |
| B-09 | **Unrealistische Fills** – Fill zum Signalpreis, keine Teilfüllung, kein Spread, keine Queue | P&L systematisch zu hoch | Fill-Modell G-36: Fill nur wenn Bar den Preis handelt, Entry = schlechtere Seite des Spreads, konservative Slippage, optional Teilfüllung | MVP |
| B-10 | **Fehlende Kosten** – keine Kommission, kein Funding (Crypto-Perps!), keine Leihgebühr (Aktien-Short) | Perp-Strategien wirken profitabel, sind es netto nicht | Cost-Model G-05 + Funding-/Borrow-Historie G-39, immer von der P&L abgezogen | MVP |
| B-11 | **Latenz/Market-Impact ignoriert** | Zu gut bei großem Size / schnellen Signalen | Latenz-Offset zwischen Signal und Fill; Impact-/Partizipationsmodell ab realistischer Ordergröße (G-05) | Paper (Konzept ab MVP) |
| B-12 | **Historical ≠ Live Data** – Backtest auf sauberen Resample-Candles, live auf Stream mit Nachzüglern/Korrekturen | Andere Signale live | Parity-/Shadow-Mode G-15; Backtest auf denselben Bar-Aggregaten, die der Live-Pfad erzeugt | Paper |
| B-13 | **Nicht reproduzierbar** – keine gepinnten Libs, keine Datensatz-Version, Seeds unvollständig | Ergebnisse nicht nachstellbar | Lockfile (G-30) + Run-Manifest (G-14) + versionierter Datensatz (G-06); ein Run = ein Hash | MVP |
| B-14 | **Regime-Kennzahlen in-sample** – „Regime-Performance" auf demselben Zeitraum berechnet, in dem optimiert wurde | Falsche Sicherheit | Regime-Auswertung nur auf OOS; Regime-Definition vorab fixieren (G-09/G-12) | MVP |
| B-15 | **DST-Bug verzerrt Session-Features** (G-08) zwischen Backtest & Live unterschiedlich | Session-basierte Setups driften | Sessions in Börsenlokalzeit, DST-korrekt auflösen; Test über einen DST-Wechsel | MVP |
| B-16 | **Monte-Carlo/Robustheit fehlt** – nur eine Equity-Kurve, keine Streuung | Drawdown-/Ruin-Risiko unbekannt | Trade-Bootstrap, Reihenfolge-Shuffle, Parameter-/Kosten-Perturbation; Konfidenzbänder & Max-DD-Verteilung (G-13) | MVP |

---

## 7. Execution-Risiken

| ID | Risiko | Wirkung | Gegenmaßnahme | Benötigt ab |
|----|--------|---------|---------------|-------------|
| E-01 | **State-Drift Broker ↔ intern** – verpasster Fill, Teilfüllung, Neustart | „Naked" Position ohne Stop, oder Doppel-Exposure | Reconciliation-Engine G-17: periodischer Abgleich Positionen/Orders/Balance; bei Drift → Kill-Switch (Broker-Ebene) + Alarm | Demo |
| E-02 | **Doppelorder durch Retry** nach Timeout ohne Idempotenz | Ungewollte doppelte Position | `clientOrderId`/Idempotency-Key; vor jedem Retry Order-Status abfragen (G-18/G-19) | Demo |
| E-03 | **Rejected/Cancelled Order nicht behandelt** – Pipeline nimmt Fill an, der nie kam | Interner State falsch, Folge-Logik (Stop/TP) auf Phantom-Position | Order-Lifecycle-State-Machine G-19; nur `FILLED`/`PARTIALLY_FILLED` erzeugen/ändern Positionen | Demo |
| E-04 | **API-Rate-Limit / Ban** durch Reconnect-Sturm oder Polling | Kein Zugriff in kritischem Moment | Token-Bucket-Limiter, exponentieller Backoff mit Jitter, Circuit Breaker, WS statt Polling (G-18) | Demo |
| E-05 | **WebSocket-Disconnect ohne Backfill** – Lücke in Fills/Kursen unbemerkt | Fehlende Fills, veraltete Kurse → falsche Entscheidungen | Reconnect + REST-Reconciliation der verpassten Ereignisse; Daten-Kill-Switch bei zu langer Lücke (G-07/G-18) | Demo |
| E-06 | **Execution-Timeout nicht definiert** – Order „hängt" | Blockierte Pipeline, unklare Exposure | Timeout je Lifecycle-Übergang; bei Timeout: Status abfragen, dann definierter Cancel/Replace-Pfad (G-19) | Demo |
| E-07 | **Slippage/Spread nur als Schwelle, keine Pre-Trade-Liquiditätsprüfung** | Fill weit vom erwarteten Preis, besonders Altcoins / dünne Zeiten | Order-Book-Depth-/Spread-Check unmittelbar vor Absendung; Ordergröße ≤ x % der sichtbaren Tiefe (G-05/G-35) | Paper (Sim), Demo (real) |
| E-08 | **Kill-Switch-Zustand nicht persistent** – nach Neustart „an" statt „aus" | System handelt trotz vorheriger Notabschaltung | Persistenter State-Store, Start = fail-safe gestoppt (G-20, S-09) | Paper |
| E-09 | **Kein getesteter Flatten-Pfad** – im Ernstfall unklar, ob „alles zu" funktioniert | Positionen bleiben im Crash offen | `emergency_flatten()` als expliziter, in Paper/Demo regelmäßig geübter Pfad (G-40) | Demo (Sim ab Paper) |
| E-10 | **Trade-Management-Race** – Stop-Update trifft gleichzeitig mit Fill/Invalidierung ein | Widersprüchliche Orders, doppelte Exits | Single-Writer je Position; Intents serialisiert durch Order Management; Idempotenz (G-19) | Demo |
| E-11 | **Clock-Drift** – lokale Zeit ≠ Broker-Zeit | News-/Session-Fenster & Order-Timestamps verschoben, evtl. Signatur-Ablehnung | NTP-Offset-Messung, Broker-Server-Zeit als Referenz, Degradation bei Drift > Schwelle (G-37) | Demo |
| E-12 | **Partial Fills im Sizing/Risk nicht berücksichtigt** – 60 % gefüllt, System denkt 100 % | Risiko-/Exposure-Rechnung falsch | Positionsgröße = tatsächlich gefüllte Menge; Rest-Order definiert behandeln (nachlegen/canceln) (G-19/G-36) | Demo (Sim ab MVP) |
| E-13 | **Margin/Liquidation ignoriert** (Bybit-Perps) | Zwangsliquidation vor dem eigentlichen Stop | Margin-Manager G-22: Mindest-Liquidationsdistanz als Risk-Veto-Kriterium, Leverage-Cap | Demo (Sim ab Paper) |
| E-14 | **Backtest-/Paper-Fills unrealistisch → Fehlvertrauen** vor Demo | Strategie „funktioniert" nur in der Simulation | Gemeinsame konservative Fill-/Cost-Engine (R-09, G-05, G-36); Parity-Report vor Demo-Freigabe (G-15) | Paper |

---

## 8. Empfohlene Ergänzungen (mit Begründung, Verortung, Stufe)

> Format je Punkt: **Was** — *Warum* — *Wo* — **Benötigt ab**.
> IDs verweisen auf Abschnitt 2/3. Reihenfolge = grobe Bau-Priorität.

### 8.1 Für MVP erforderlich (blockiert den ersten seriösen Backtest)

1. **Instrument-Master / Reference-Data-Service** (G-01)
   *Warum:* Positionsgröße, Rundung, Fees, Min-Notional, P&L-Rechnung brauchen verlässliche
   Kontraktdaten; verstreut in `config` führt zu Fehlern.
   *Wo:* neues Paket `src/trading_agent/refdata/` (Instrumente, Fees, Kalender-Verknüpfung); von `data`, `risk`, `execution` konsumiert.
   **Benötigt ab: MVP.**

2. **Cost-Model** (G-05, G-39)
   *Warum:* Ohne Fees/Funding/Borrow/Slippage ist jeder Backtest systematisch zu optimistisch –
   besonders Crypto-Perps (Funding) und Aktien-Shorts (Leihe).
   *Wo:* `src/trading_agent/execution/costs.py`; **verpflichtend** von Backtest und Paper genutzt (R-09).
   **Benötigt ab: MVP.**

3. **Fill-/Execution-Simulation** (G-36)
   *Warum:* Fills zum Signalpreis erzeugen Scheinrendite; Teilfüllungen ändern Risiko.
   *Wo:* `src/trading_agent/execution/simulation.py`, gemeinsam von Backtest & Paper.
   **Benötigt ab: MVP.**

4. **Market-Data-Storage / Repository** (G-06)
   *Warum:* Reproduzierbarkeit, Point-in-Time-Abfragen, Survivorship-Vermeidung, Funding-/News-Historie.
   *Wo:* `src/trading_agent/data/repository.py` + Speicherformat (Vorschlag: Parquet für Candles, JSONL→SQLite für Events); Entscheidung TODO-Design-Frage jetzt treffen.
   **Benötigt ab: MVP.**

5. **Data-Quality- & Integrity-Monitor** (G-07)
   *Warum:* Stale/doppelte/kaputte Candles erzeugen falsche Signale; „garbage in" muss **vor** der Analyse gestoppt werden.
   *Wo:* `src/trading_agent/data/quality.py`; setzt Flags im `MarketContext`, triggert später den Daten-Kill-Switch.
   **Benötigt ab: MVP (Checks), Paper (Kill-Switch).**

6. **DST-korrekte Sessions** (G-08)
   *Warum:* Statische UTC-Fenster verschieben London/NY im Sommer um eine Stunde → alle session-basierten Setups falsch, Backtest ≠ Live.
   *Wo:* `analysis/sessions.py` + `config`: Sessions in `Europe/London` / `America/New_York` definieren, zur Laufzeit auflösen.
   **Benötigt ab: MVP.**

7. **Market-Regime-Engine** (G-09, G-10)
   *Warum:* SMC-Setups verhalten sich in Trend vs. Range vs. Vol-Spike fundamental anders; „ein Modell für alle Regime" ist die häufigste Ursache für instabile Live-Performance.
   *Wo:* neues Paket `src/trading_agent/analysis/regime.py`; Output im `MarketContext`; konsumiert von `strategy` (Filter/Score) und `risk` (Sizing-Skalierung).
   **Benötigt ab: MVP.**

8. **Point-in-Time Feature/Dataset-Builder** (G-11)
   *Warum:* verhindert Look-ahead über revidierte Daten und Normalisierungs-Leakage.
   *Wo:* `src/trading_agent/research/dataset.py`.
   **Benötigt ab: MVP.**

9. **Validation-Harness** (G-12)
   *Warum:* Ohne OOS/Walk-Forward ist jede Kennzahl in-sample und nicht aussagekräftig.
   *Wo:* `src/trading_agent/research/validation.py` (Split, Walk-Forward, optional Purged/Embargoed CV).
   **Benötigt ab: MVP.**

10. **Robustness-/Monte-Carlo-Suite** (G-13)
    *Warum:* Eine einzelne Equity-Kurve sagt nichts über Drawdown-Streuung/Ruin-Wahrscheinlichkeit.
    *Wo:* `src/trading_agent/research/robustness.py`.
    **Benötigt ab: MVP.**

11. **Experiment- & Parameter-Registry / Run-Manifest** (G-14)
    *Warum:* Reproduzierbarkeit, Schutz vor Cherry-Picking und Multiple-Testing-Selbsttäuschung.
    *Wo:* `src/trading_agent/research/registry.py`; schreibt `data/research/runs/<hash>.json` (Code-SHA, Config-Hash, Datensatz-Version, Seeds, Metriken).
    **Benötigt ab: MVP.**

12. **Volatilitäts-adjustierte Positionsgröße** (G-23)
    *Warum:* Fixes Risiko% ohne Vol-Bezug führt zu stark schwankender Konto-Volatilität und zu großen Positionen in ruhigen Phasen vor Ausbrüchen.
    *Wo:* `risk/position_sizing.py` (ATR-/Realized-Vol-Option, Vol-Targeting).
    **Benötigt ab: MVP.**

13. **Rejection-Reason-Taxonomie** (G-26)
    *Warum:* „Warum wurde nicht getradet" ist für Strategie-Verbesserung so wichtig wie die Trades selbst; braucht stabile Enums für Auswertung.
    *Wo:* `core/enums.py` (`RejectionReason`), genutzt von `risk`, `strategy`, `execution`, geschrieben ins Decision Ledger.
    **Benötigt ab: MVP.**

14. **Config-Schema-Validierung & -Versionierung** (G-28)
    *Warum:* Fehlkonfiguration ist eine Hauptursache für stille Fehlfunktion; Fail-fast statt Fehlverhalten.
    *Wo:* `config/loader.py` mit typisiertem Schema (Pydantic v2 empfohlen), `schema_version`-Feld in allen YAMLs.
    **Benötigt ab: MVP.**

15. **AI/Reasoning-Layer – Spezifikation + harte Grenzen** (G-16)
    *Warum:* Das Projekt heißt „AI Trading Agent", aber die Architektur hat keinen Platz dafür und keine Leitplanken. Die Grenzen müssen **jetzt** stehen, bevor Code entsteht, sonst wandert LLM-Logik unkontrolliert in Entscheidungspfade.
    *Wo:* neues Paket `src/trading_agent/ai/` mit:
    - `contract.py` – JSON-Schema für jede LLM-Ausgabe (Pydantic), strikte Validierung, Reject bei Schemaverstoß.
    - Regel (dokumentiert **und** im Code erzwungen): LLM-Ausgabe ist **nur ein zusätzlicher Input für Setup Detection / Confluence**, niemals ein Bypass von Confluence, Scoring oder Risk Engine.
    - `confidence` des LLM ist **getrennt** vom Setup-Score und darf ihn nur innerhalb enger, konfigurierbarer Grenzen modulieren.
    - Determinismus: `temperature=0`, gepinnte Modell-ID, Prompt-Templates versioniert in `src/trading_agent/ai/prompts/` mit `prompt_version`.
    - Jede LLM-Anfrage/-Antwort + Prompt-Version + Modell-ID ins Decision Ledger und Audit Log.
    - Fallback: bei Timeout/Schemafehler/Nichtverfügbarkeit → System läuft rein regelbasiert weiter (kein Blockieren, kein Raten).
    **Benötigt ab: MVP (Schema + Grenzen + Fallback als Konzept/Contract), Paper (Fallback-Code), Demo (volle Auditierung).**
    *Hinweis:* Für den ersten MVP-Backtest kann der AI-Layer deaktiviert bleiben – aber der Vertrag und die „darf-nicht"-Regeln gehören in Phase 1, nicht später nachgerüstet.

16. **Trading-Calendar / Market-Hours-Engine** (G-03)
    *Warum:* Aktien/Gold haben Handelszeiten, Feiertage, Half-Days; Forex hat Wochenendlücken; ohne das entstehen Phantom-Bars und falsche „Previous Day"-Level.
    *Wo:* `src/trading_agent/refdata/calendar.py`; von `data`, `analysis/sessions`, `strategy` konsumiert.
    **Benötigt ab: MVP für Aktien/Gold; Paper für alle.**

17. **Dependency-Lockfile + Secret-Scan-Hooks** (G-30, G-29 Teil 1)
    *Warum:* Reproduzierbarkeit & Verhinderung von Secret-Leaks ab dem ersten Commit.
    *Wo:* `requirements.lock`/`uv.lock`, `.pre-commit-config.yaml` (gitleaks, ruff, mypy), `pip-audit` in CI.
    **Benötigt ab: MVP.**

### 8.2 Für Paper Trading erforderlich

18. **State-Store & Recovery** (G-20)
    *Warum:* Sobald ein Prozess über Stunden/Tage läuft, muss er Neustart/Crash überstehen, ohne Positionen, Verlustzähler oder Kill-Switch-Status zu „vergessen".
    *Wo:* `src/trading_agent/state/` (Store-Interface + SQLite-Impl), `graceful_shutdown()`/`recover()` in `engine/`.
    **Benötigt ab: Paper.**

19. **Exposure-/Faktor-Risikomodell** (G-21, G-25)
    *Warum:* Preis-Korrelation allein erkennt nicht, dass Long BTC + Long ETH + Long SOL + Short DXY-Proxy alle dieselbe „Risk-on/USD-schwach"-Wette sind.
    *Wo:* `execution/portfolio.py` erweitern um Faktor-Exposure (USD, US-Yields, Equity-Beta, BTC-Beta, Gold↔USD); `risk/limits.py` um Faktor- und Klassen-/Sektor-Limits.
    **Benötigt ab: Paper.**

20. **Granulare Kill-Switch-Hierarchie** (G-24)
    *Warum:* „Eine kaputte Datenquelle für ETH" oder „eine Strategie läuft Amok" darf nicht das ganze System stoppen – und umgekehrt muss ein Broker-Problem alle Assets dieses Brokers stoppen.
    *Wo:* `safety/kill_switch.py` → Ebenen `global`/`broker`/`asset`/`strategy`/`data`, jeweils persistiert (G-20).
    **Benötigt ab: Paper (Strategie/Daten-Ebene), Demo (Broker/Asset-Ebene).**

21. **Notification-/Alerting-Layer** (G-31)
    *Warum:* Paper Trading läuft unbeaufsichtigt; ohne Zustellkanal bleiben Alarme im Logfile.
    *Wo:* `src/trading_agent/ops/notify.py` (Kanal-Abstraktion: stdout/file jetzt, Telegram/E-Mail später), Severity + Dedup + Rate-Limit.
    **Benötigt ab: Paper.**

22. **Data-Source-Health-Registry** (G-38)
    *Warum:* Automatisches Erkennen „Quelle degradiert/tot" und Umschalten/Stoppen.
    *Wo:* `data/health.py`, gespeist aus jedem Provider-Call, sichtbar im Monitoring.
    **Benötigt ab: Paper.**

23. **Backtest↔Paper-Parity / Shadow-Mode** (G-15)
    *Warum:* Beweist vor Demo, dass dieselben Signale in Simulation und Live-Datenpfad entstehen.
    *Wo:* `engine/parity.py` – lässt Backtest und Paper auf demselben Zeitfenster laufen und diffed Entscheidungen.
    **Benötigt ab: Paper.**

24. **Deployment-/Strategie-/Config-Versionierung** (G-33)
    *Warum:* Jeder Ledger-/Audit-Eintrag muss der exakten Code+Config-Version zuordenbar sein.
    *Wo:* `core/version.py` (liest Git-SHA, Strategie-Version, Config-Hash), in Ledger/Audit/Journal mitgeschrieben.
    **Benötigt ab: Paper.**

25. **Setup-/Regime-Statistik-Feedback** (G-27)
    *Warum:* Score-Gewichte sollen aus realer (OOS-)Performance lernen, nicht aus In-Sample-Tuning.
    *Wo:* `journal/performance.py` → Report je Setup-Typ/Regime; manuelle, dokumentierte Anpassung von `scoring.yaml` mit Registry-Verweis.
    **Benötigt ab: Paper.**

26. **Asset-spezifische Gates Crypto** (G-35)
    *Warum:* Token-Unlocks, Delistings, Funding-Extreme, Exchange-Wartung sind reale Verlustquellen.
    *Wo:* `analysis/` (Crypto-Submodul) + `risk` (Veto-Kriterien).
    **Benötigt ab: Paper.**

27. **Asset-spezifische Gates Aktien** (G-34) – *falls Aktien in Paper aktiv*
    *Warum:* Earnings-Überraschungen, Halts, Pre/After-Hours-Illiquidität.
    *Wo:* `analysis/` (Equity-Submodul) + `refdata/calendar` + `risk`.
    **Benötigt ab: Paper, wenn Aktien aktiv; sonst Demo.**

### 8.3 Für Demo Trading erforderlich (erste echte Broker-API, Testnet)

28. **Broker/Exchange-Client-Layer** (G-18) — *Warum:* Rate-Limits, Reconnects, Idempotenz, Circuit Breaker. — *Wo:* `execution/brokers/_client/` unter den Adaptern. — **Benötigt ab: Demo.**
29. **Order-Lifecycle-State-Machine** (G-19) — *Warum:* deterministische Behandlung von ACK/Reject/Cancel/Partial/Expire. — *Wo:* `execution/order_management.py`. — **Benötigt ab: Demo.**
30. **Reconciliation-Engine** (G-17) — *Warum:* Broker-State ist die Wahrheit; Drift = Gefahr. — *Wo:* `execution/reconciliation.py`, Loop im `engine/`. — **Benötigt ab: Demo.**
31. **Margin- & Leverage-Manager** (G-22) — *Warum:* Liquidation vor Stop bei Perps. — *Wo:* `risk/margin.py`, Veto-Kriterium in `risk_engine`. — **Benötigt ab: Demo (in Paper simuliert).**
32. **Getesteter Emergency-Flatten-Pfad** (G-40) — *Warum:* Im Ernstfall zählt, dass „alles zu" nachweislich funktioniert. — *Wo:* `safety/` + `execution/`. — **Benötigt ab: Demo (Sim ab Paper).**
33. **Clock-Sync / Drift-Check** (G-37) — *Warum:* Signatur-/Timestamp-Fehler, falsche Fensterausrichtung. — *Wo:* `core/clock.py` + Monitoring. — **Benötigt ab: Demo.**
34. **Secrets-Handling technisch** (G-29 Teil 2) — *Warum:* Read-only-Keys in Dev, Trade-only ohne Withdrawal in Demo, IP-Allowlist, Rotation, Redaction. — *Wo:* `docs/SECURITY.md` + `config`-Loader (Secrets nur aus Env/Keychain), `utils/logging.py` (Redaction). — **Benötigt ab: Demo (Policy ab MVP).**
35. **Incident-Runbooks & Backups** (G-32) — *Warum:* Unbeaufsichtigter Betrieb braucht geprobte Wiederherstellung. — *Wo:* `docs/runbooks/`, Backup-Skript für `data/`. — **Benötigt ab: Demo.**
36. **Symbol-Mapping-Layer** (G-02) — *Warum:* kanonisch ↔ broker-spezifisch, sonst falsche Instrumente. — *Wo:* `refdata/symbols.py`. — **Benötigt ab: Demo (Konzept ab Paper).**

### 8.4 Für Live Trading erforderlich

37. **Corporate-Actions-Service** (G-04) — *falls Aktien live* — *Wo:* `refdata/corporate_actions.py` + Backadjustment im Repository. — **Benötigt ab: Live (MVP falls Aktien im MVP-Backtest).**
38. **Trade-Republic-Anbindung – rechtliche/AGB-Prüfung** (S-07) — *Warum:* inoffizielle APIs riskieren Kontosperre. — *Wo:* Vorab-Recherche, dann ggf. nur read-only Portfolio-Import. — **Benötigt ab: Live (Recherche jetzt).**
39. **Vollständige Secret-Rotation + Credential-Failure-Automatik** (S-05) — **Benötigt ab: Live.**
40. **Kapital-/Risiko-Freigabeprozess** (Mensch-in-the-loop für Limit-Erhöhungen, dokumentiert) — **Benötigt ab: Live.**

---

## 9. Empfohlene Architektur-Änderungen

| # | Änderung | Begründung |
|---|----------|------------|
| A-01 | **Neues Paket `refdata/`** (Instrument-Master, Fees, Kalender, Symbol-Mapping, Corporate Actions) | Referenzdaten sind heute in `config` verstreut; sie sind eine eigene Domäne mit eigener Lebensdauer und eigenen Quellen. |
| A-02 | **Neues Paket `research/`** (dataset, validation, robustness, registry) getrennt von `engine/backtest` | Backtest = Ausführungs-Simulation. Research = Methodik (Splits, CV, Monte Carlo, Experiment-Tracking). Vermischung führt zu In-Sample-Selbsttäuschung. |
| A-03 | **Neues Paket `ai/`** mit striktem Output-Contract und dokumentierter „darf-nicht"-Regel | Ohne feste Grenze wandert LLM-Logik in Entscheidungs-/Risikopfade. Der Contract muss vor dem ersten AI-Code stehen. |
| A-04 | **Neues Paket `state/`** (Persistenz-Interface + Impl) und `ops/` (notify, health, version) | Betriebszustand & Betriebswerkzeuge sind Querschnitt; gehören nicht in `safety` oder `journal`. |
| A-05 | **`analysis/regime.py` als eigene Komponente**, Regime-Output fester Teil des `MarketContext` | Regime ist kein Scoring-Detail, sondern steuert Strategie **und** Risiko. |
| A-06 | **Gemeinsame Execution-Simulation** (`execution/simulation.py` + `execution/costs.py`), von Backtest **und** Paper zwingend genutzt | Verhindert Backtest≠Paper-Divergenz (R-09); ein Fill-/Kostenpfad, ein Ort für Realismus. |
| A-07 | **`MarketContext` bekommt ein `data_quality`- und ein `confidence`-Feld**; Pipeline bricht Setup-Erzeugung ab, wenn Qualität < Schwelle | „Keine Trades bei unsicheren Daten" braucht einen technischen Träger, nicht nur eine Absichtserklärung. |
| A-08 | **`OrderIntent` bekommt `client_order_id`, `time_in_force`, `order_type`, `intent_hash`** von Anfang an | Idempotenz und Lifecycle lassen sich nicht nachträglich sauber einführen. |
| A-09 | **Kill-Switch von Singleton-Flag zu hierarchischem, persistiertem Zustand** (`global/broker/asset/strategy/data`) | Granularität + Crash-Sicherheit; Start immer fail-safe „gestoppt". |
| A-10 | **Risk Engine liest ausschließlich aus Portfolio-State + RefData + Limits, rechnet Exposure nicht selbst** | Klare Zuständigkeit (R-07), eine Quelle der Wahrheit für Exposure. |
| A-11 | **Pipeline explizit in Phasen mit Bar-Close-Gate**: `ingest → quality-gate → analysis → regime → detection → confluence → scoring → risk-veto → sizing → intent → execution → journal` | Macht Look-ahead-Freiheit strukturell erzwingbar und testbar (B-01). |
| A-12 | **Alle Zeitangaben in Config mit expliziter Zeitzone** (`tz: Europe/London`), interne Auflösung nach UTC zur Laufzeit | Behebt DST-Klasse von Fehlern (G-08, B-15). |
| A-13 | **Persistenz-Entscheidung jetzt treffen:** Candles → Parquet; Events/Ledger/Journal/State → SQLite (eine Datei, transaktional, gut testbar), JSONL nur als Export | Offene TODO-Frage blockiert Repository (G-06), State-Store (G-20) und Registry (G-14). |
| A-14 | **Datenmodell-Entscheidung jetzt treffen:** Pydantic v2 für alle externen Ein-/Ausgaben (Config, LLM-Output, Broker-Responses), `@dataclass(slots=True)` für heiße interne Pfade | Schema-Validierung (G-28, G-16) braucht Pydantic; Performance-kritische Analyse profitiert von dataclasses. |
| A-15 | **`docs/SECURITY.md` + `.pre-commit-config.yaml` + Lockfile in Phase 0**, nicht Phase 9 | Secret-Hygiene und Reproduzierbarkeit müssen ab dem ersten Commit greifen. |
| A-16 | **Adapter-Schnittstelle trennen in `MarketDataAdapter` und `BrokerAdapter`** (Lesen vs. Handeln), plus `_client/`-Ebene darunter | Least-Privilege (nur `order_management` darf handeln), sauberere Tests, Testnet-Marktdaten ohne Trade-Rechte. |

---

## 10. Finale Checkliste vor Phase 1

### Entscheidungen, die jetzt getroffen sein müssen (sonst blockieren sie Phase 1)
- [ ] **Persistenz:** Parquet (Candles) + SQLite (Events/Ledger/Journal/State/Registry) — Vorschlag A-13 bestätigen oder ändern.
- [ ] **Datenmodell:** Pydantic v2 (Ränder) + dataclasses (Kern) — Vorschlag A-14 bestätigen.
- [ ] **MVP-Umfang festlegen:** welche **eine** Asset-Klasse + welche Instrumente + welcher Zeitraum (Point-in-Time eingefroren, B-05)? Empfehlung: Crypto (BTCUSDT + ETHUSDT), da 24/7, keine Corporate Actions, Funding-Historie öffentlich verfügbar.
- [ ] **Timeframe-Fokus MVP:** z. B. HTF D1/H4 + Entry M15/M5 — reduziert Umfang, ohne das MTF-Prinzip aufzugeben.
- [ ] **AI-Layer im MVP:** deaktiviert, aber Contract + „darf-nicht"-Regeln in Phase 1 geschrieben (G-16).
- [ ] **Regime-Definitionen:** konkrete, vorab fixierte Regeln für Trend/Range/Vol-Regime dokumentieren (G-09, B-14).

### Dokumente/Setup, die vor der ersten Code-Zeile ergänzt werden
- [ ] `docs/SECURITY.md` (Least Privilege, Read-only-Dev-Keys, Rotation, Redaction, Trust Boundaries) — G-29, S-04, S-08.
- [ ] `.pre-commit-config.yaml` mit gitleaks + ruff + mypy; `pip-audit` in CI — G-29, S-01.
- [ ] Dependency-Lockfile (hash-gepinnt) — G-30, S-02.
- [ ] `.env.example`: `BYBIT_*`-Zeilen entfernen oder mit deutlichem „NIEMALS ECHTE WERTE / NUR ENV, NIE COMMIT" versehen — S-01.
- [ ] `git init` + erster Commit mit Pre-Commit-Hooks aktiv.
- [ ] ARCHITECTURE.md um die neuen Pakete (`refdata/`, `research/`, `ai/`, `state/`, `ops/`, `analysis/regime.py`) und die Pipeline-Phasen (A-11) ergänzen.
- [ ] TODO.md: neue Phase **1a „Fundament-Ergänzungen"** einfügen (RefData, Cost-Model, Data-Quality, Repository, Regime, Research-Harness, Rejection-Enums, Config-Schema, AI-Contract) **vor** den Analyse-Engines.
- [ ] `config.example.yaml`: Sessions auf Börsenlokalzeit + `tz`-Feld umstellen (A-12); `schema_version` in alle YAMLs.
- [ ] `risk.example.yaml`: Felder für `leverage_max`, `min_liquidation_distance_pct`, Konzentrationslimits je Asset-Klasse/Sektor, `vol_target` ergänzen (Werte, noch kein Code).
- [ ] `scoring.example.yaml`: `regime`-Faktor konkretisieren; klarstellen, dass LLM-`confidence` **kein** Score-Faktor mit freiem Gewicht ist.

### Architektur-Invarianten, die als Tests verankert werden (ab Phase 1)
- [ ] **Look-ahead-Test:** Analyse-Engine mit „durchgereichten" Zukunftsdaten füttern → Ergebnis muss identisch zu ohne sein (B-01).
- [ ] **Risk-Veto-Test:** Strategie empfiehlt Trade, Limit verletzt → Order wird abgelehnt, Grund im Ledger (Kernanforderung).
- [ ] **Kein-SL-Test:** `OrderIntent` ohne gültigen SL → Reject.
- [ ] **Unsichere-Daten-Test:** `data_quality < Schwelle` → kein Setup erzeugt (A-07).
- [ ] **Kill-Switch-Persistenz-Test:** Kill-Switch aktiv → Neustart → weiterhin aktiv, keine Orders (S-09).
- [ ] **Determinismus-Test:** gleicher Input + gleicher Seed + gleiche Config → bit-identischer Backtest-Report (B-13).
- [ ] **Backtest/Paper-Parity-Test:** gleiche Bars → gleiche Entscheidungen (G-15) — spätestens vor Paper.
- [ ] **Kosten-Test:** Backtest ohne Kosten vs. mit Kosten → messbare, dokumentierte Differenz (B-10).

### Freigabe-Gate
- [ ] Alle „jetzt"-Entscheidungen oben getroffen und in ARCHITECTURE.md/TODO.md eingetragen.
- [ ] Security-Grundausstattung (SECURITY.md, Hooks, Lockfile) steht.
- [ ] Kein offener Punkt aus Abschnitt 5 mit Schwere „Hoch" für die MVP-Stufe.

**Erst wenn dieses Gate grün ist, beginnt Phase 1 (bzw. die vorgezogene Phase 1a).**

---

## Anhang: Gap-Übersicht nach Stufe

| Benötigt ab | Gap-IDs |
|-------------|---------|
| **MVP** | G-01, G-03(Aktien/Gold), G-05, G-06, G-07(Checks), G-08, G-09, G-10, G-11, G-12, G-13, G-14, G-16(Contract), G-23, G-26, G-28, G-29(Hooks), G-30, G-36, G-39(Crypto) |
| **Paper** | G-02, G-03(alle), G-07(Kill-Switch), G-15, G-16(Fallback), G-20, G-21, G-24(Strategie/Daten), G-25, G-27, G-31, G-33, G-34(falls Aktien), G-35, G-38, G-39(Aktien-Short) |
| **Demo** | G-04(falls nicht MVP), G-16(Audit), G-17, G-18, G-19, G-22, G-24(Broker/Asset), G-29(Keys), G-32, G-37, G-40 |
| **Live** | G-04(Aktien), S-07(Trade Republic), vollständige Rotation, Kapital-Freigabeprozess |
