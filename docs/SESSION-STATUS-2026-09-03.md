# Session-Status 2026-09-03 — Regime-Gate · Alert-Emitter · Forward-Validierung

## DONE (implementiert **und** getestet)

### 2. Alert-Emitter — Kontext-Alerts verdrahtet
- **`src/trading_agent/strategy/context_alerts.py`** — `ContextAlertEmitter`: verwandelt
  `PortfolioIntelligenceReport`, `NewsAssessment` und `ReEntryAssessment`-Listen in
  `PORTFOLIO_RISK` / `HIGH_IMPACT_NEWS` / `RE_ENTRY_SETUP`-Alerts.
  - **Zwei-stufiges Anti-Spam:** (a) je `dedup_key` nur bei geändertem *Fingerprint* an die
    `AlertEngine` gereicht (semantische Änderungserkennung), (b) `AlertEngine` dedupliziert +
    Cooldown. Kippt der Zustand zurück (Health GREEN, Blackout vorbei, Watch weg, Verdikt HOLD),
    wird der aktive Alert **einmalig** verworfen — kein Dauerfeuer.
  - PIT/deterministisch (`now == information_cutoff`), kein Wall-Clock.
- **`AlertEngine.dismiss_context_alert(key, now)`** neu — verwirft genau einen aktiven
  Kontext-Alert, wenn sein Auslöser weg ist.
- **`scripts/run_live_daemon.py`** — `--economic-calendar` (Default `config/economic_calendar.csv`,
  80 Events). Bei jedem `DecisionMade` läuft `assess_news` je Instrument → `ContextAlertEmitter.on_news`
  → gelieferte Alerts gehen als `AlertRaised` auf den Bus (Audit-Log-verkettet, gezählt, geloggt).
  Smoke-Test (binance XAUUSDT, 45 s): Kalender geladen, Decision, kein Crash, `orders_sent=0`.
- **`tests/unit/test_context_alerts.py`** — 5 Tests: Portfolio-Risk nur einmal bis Änderung /
  Auto-Dismiss bei Health-Recovery; News-Alert im Anmarsch + Dismiss danach / kein Doppel-Alert
  im selben Zeit-Bucket; Re-Entry nur über Schwelle + Dismiss wenn Watch verschwindet.
- **Alle 1063+ bestehenden Tests bleiben grün**, ruff + ruff-format + mypy --strict (205 src) sauber.

### 1. Breakout-Regime-Gate — geprüft, **kein robuster Fortschritt** → nicht integriert
- **`docs/BREAKOUT-REGIME-GATE-2026-09.md`** — vollständige Analyse.
- Neue bench-Detektoren **S17** (Efficiency-Ratio ≥ 0.20) / **S18** (≥ 0.25) in
  `scripts/setup_research.py` — die *sanften* Varianten zwischen „kein Gate" (S9) und „zu streng"
  (S14 @ 0.30, hatte die Stichprobe 112 → 10 zerstört).
- Lauf `data/repository_real/research/setup_research_v11_regime_soft.json` (Teil-Panel, Split 2025-01):

  | | all n | OOS n | OOS exp | MC prob_positive | Symbol-Stabilität |
  |---|---|---|---|---|---|
  | **S9 (Baseline)** | 62 | 34 | +0.573 R | **0.93** | **1.00** |
  | S17 (ER ≥ 0.20) | 29 | 16 | +0.439 R | 0.58 | 0.60 |
  | S18 (ER ≥ 0.25) | 17 | 11 | +0.606 R | n/a | 0.50 |

  Jede Regime-Gate-Schwelle **halbiert die Stichprobe und senkt** Monte-Carlo-Wahrscheinlichkeit
  und Symbol-Stabilität. → **S9-Baseline bleibt unverändert**, `SETUP-BREAKOUT-RETEST-01` bleibt
  `IN_VALIDATION` / SHADOW. `config/setup_validation.json`-Notiz aktualisiert.

### 4. Validierungs-Kette
- **`scripts/validate_s4.py`** — H4-Fallback für die Coverage-Prüfung (die XAUUSD-M5-Parquet ist
  Thrift-defekt und crashte den Coverage-Check hart). Läuft jetzt durch:
  **`XAUUSD (echt): 17 Monate, größte Lücke 820 Tage → NOCH UNVOLLSTÄNDIG`**.
  Die echte Spot-Gold-Historie hat eine 820-Tage-Lücke (2024-01 → 2026-05) → Voll-Validierung
  weiter blockiert (siehe BLOCKED).
- OOS / Walk-Forward / Monte-Carlo / Symbol-Stabilität: über `setup_research.py` auf dem
  verfügbaren Panel gelaufen (v11). S9 übersteht alle Achsen (MC 0.93, symstab 1.0 auf dem
  7-Symbol-Panel; 0.79 / 0.83 auf dem vollen 12-Symbol-Panel v9). Regime-Stabilität: das einzige
  reale Spot-Gold-Jahr (2023, Range-Regime) bleibt negativ und liegt vollständig im IS-Fenster.

**Ehrliche Edge-Bewertung:** S9 ist der robusteste Setup-Typ im diversifizierten Panel
(FX + Crypto + Gold-Futures), aber die OOS-Edge ruht stark auf **indikativen** Yahoo-Daten +
FX-Proxy. Auf echtem Spot-Gold ist die Stichprobe zu klein und im einzigen Range-Jahr negativ.
**Kein Live-Freigabe-Signal. Kein Echtgeld. XAUUSDT bleibt SHADOW/PAPER.**

### 2b. Portfolio-Risk-Alerts im One-Shot-Pfad
- **`scripts/portfolio_hub.py`** — `--alerts-journal` (Default
  `data/repository_real/live/context_alerts.jsonl`). Nach `PortfolioIntelligenceEngine.assess`
  läuft `ContextAlertEmitter.on_portfolio_report` → gelieferte Alerts in `--json`-Ausgabe,
  Textausgabe (`🔔`) und JSONL-Journal.
  - **Verifiziert gegen die echten read-only Konten:** ein `PORTFOLIO_RISK`-Alert
    (Health YELLOW 69/100, „viel unallokiertes Cash 73 % · Aktien-Anteil 0 %"), kein Spam.

## PARTIAL

- **Alert-Emitter Portfolio-Risk / Re-Entry im Live-Daemon** — `on_portfolio_report` läuft jetzt
  im One-Shot (`portfolio_hub.py`). Im 24/7-Daemon fehlt noch: `on_reentry` braucht eine
  Live-Re-Entry-Registry (die `ReEntryEngine` wird bei Position-Close noch nicht bestückt), und
  die read-only Konto-Adapter im Daemon-Loop. NEXT: Daemon-Watch-Registrierung bei
  `PaperPositionChanged CLOSED`.
- **Forward-Paper-Trades (Ziel ≥ 100)** — die Aufzeichnungs-Kette ist geschlossen (Daemon →
  `signal_journal.jsonl` → `edge_health_check.py`), aber es sind **0 echte Forward-Trades**
  gesammelt: das braucht den Daemon 24/7 über Wochen, und XAUUSDT armt selten (Regime-Gate).
  Die vorhandenen „Trades" in `data/repository_real/live/xau_shadow_*.jsonl` (8 + 41) sind
  **historische Shadow-Replays**, keine Forward-Trades.

## BLOCKED (extern, unverändert)

1. **Vollständige Dukascopy-Spot-XAUUSD-Historie** — Umgebung killt Prozesse > ~2 min; der
   Voll-Ingest (Fetch + `.bi5`-Dekodierung, ~35 Monats-Chunks) braucht die Nutzer-Maschine
   (`scripts/ingest_dukascopy_full.sh`). Ohne sie: keine finale S4/S9-Real-Gold-Validierung.
2. **XAUUSD-M5-Parquet Thrift-defekt** — nur M5-Feinsimulation betroffen; H4/M15/D1 lesbar.
3. **Forward-Trades = Wall-Clock-Sammlung** — nicht in einer Session lösbar. Braucht den
   24/7-Daemon auf Dauerbetrieb.
4. `FRED_API_KEY`, Polygon/Finnhub (Aktien-Fundamentals), echter News/CPI/PCE/ECB-Feed,
   `TELEGRAM_BOT_TOKEN`/`CHAT_ID`, cTrader/OANDA-Demo-Token (FX-Live).

## NEXT (autonom, in dieser Reihenfolge)

1. **`ContextAlertEmitter.on_portfolio_report` + `on_reentry` in `build_dashboard.py` verdrahten**
   (One-Shot-Pfad hat die Konto-Daten schon) → Portfolio-Risk / Re-Entry-Alerts ins Dashboard.
2. **Re-Entry-Registry im Daemon** — bei `PaperPositionChanged CLOSED` mit intakter These eine
   `ReEntryWatch` registrieren, je Tick `assess` → `on_reentry`.
3. **Daemon-Dauerbetrieb** organisieren (Nutzer-Maschine / Cloud) → Forward-Trades sammeln.
4. Nach ≥ 100 Forward-Trades: `edge_health_check.py --write` + `validate_s4.py --write`.
5. **S17/S18 auf dem vollen 12-Symbol-Panel** mit lückenloser Spot-Gold-Historie final prüfen
   (Nutzer-Maschine).
6. Weitere Masterplan-Punkte: Portfolio-Zusammenführung, Position-Scoring-Feinschliff,
   Market-Scanner-Erweiterung, Live-Charts-Auto-Annotation, Aktien-Fundamentals.
