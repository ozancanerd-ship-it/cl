# Daten-Rollen & Live-Decision-Engine — bindendes Architektur-Prinzip (2026-08-31)

Festgelegt vom Nutzer. **Historische Daten sagen NICHT den aktuellen Preis voraus.** Sie sind
ausschließlich Validierungs-Grundlage. Die finale Trading-Entscheidung basiert **immer** auf dem
aktuellen Live-Markt.

## Die vier Daten-Rollen

| Rolle | Datenquelle | Wofür | Wo im Code |
|---|---|---|---|
| **VALIDIERUNG** | Historische OHLCV (Backtest-Fenster) | Strategien testen, robuste Patterns finden, Regimes untersuchen, OOS-Validierung, Overfitting erkennen, R:R / Trade-Verhalten messen | `engine/backtest.py`, `engine/replay.py` (PIT-Proof `engine/parity.py`), `research/validation.py` + `research/robustness.py`, `scripts/setup_research.py`, `scripts/run_backtest.py` |
| **ENTSCHEIDUNG** | **Live-Markt** (aktuelle Candles, Struktur, Liquidität, Trend, Regime, MTF, Momentum, Volume, Volatilität, News, Macro, Event-Risk, Portfolio, Risk) | „Wie sieht der Markt **JETZT** aus?" → BUY / SELL / WAIT / NO_TRADE | `strategy.evaluate(MarketContext)` — alles hängt am `information_cutoff` = *jetzt*. `runtime/live_pipeline.py` füttert echte WS-M5-Bars. |
| **ADAPTATION / REGIME-CHECK** | Recent-Daten (jüngste geschlossene Trades: Forward/Paper + jüngstes Backtest-Fenster) | Trägt die historisch gefundene Edge **aktuell noch**? Hat sich das Regime geändert? | `governance/edge_health.py` — `assess_edge_health(baseline, recent_trades)` → INTACT / WEAKENING / BROKEN |
| **LAUFENDE VALIDIERUNG** | Paper-/Forward-Trades im Live-Betrieb | Bestätigt die historische Edge sich vorwärts? (≥ N Trades, Masterplan §44) | `strategy/paper_live.py` + `journal/ledger.py` → speist `edge_health` + den Übergang IN_VALIDATION → VALIDATED |

## Recency / Adaptation

- Die Analyse-Engine ist **von Natur aus recent-basiert**: `slope_window=50`, Range-`window=40`,
  Vol-`lookback=100`, Swings/Struktur auf den letzten Bars. Keine Dekaden-Historie fließt in die
  aktuelle Bewertung — nur in die Kalibrierung der Schwellen.
- `RegimeState.bars_in_state` = Regime-Alter. Ein frisch gewechseltes Regime ist bekannt.
- **Edge-Health prüft die aktuelle Marktphase gegen die Baseline.** Verschlechtert sich die
  Performance auf Recent-Daten → `WEAKENING` (enger beobachten) bzw. `BROKEN` (Live-Signale aus).

## Trennung: Strategie-Entscheidung ↔ Freigabe-Entscheidung

Zwei **orthogonale** Fragen:

1. **`strategy.evaluate` → `Decision`**: *„Zeigt der Live-Markt JETZT ein gültiges ARMED-Setup
   mit sinnvollem R:R und akzeptablem Risiko, ohne harte Gegenargumente?"* (Regeln 1, 2, 4, 5, 6a)
2. **`governance.evaluate_live_gate` → `LiveGateReport`**: *„Darf daraus ein **actionable**
   🔥 BUY/SELL werden — ist diese Strategie dafür validiert und trägt die Edge aktuell?"*
   (Regel 3 + 6b)

`EvaluationResult.live_gate` hält (2). `EvaluationResult.is_actionable` = (1);
`is_actionable_live` = (1) **und** (2).

| `LiveEligibility` | Bedingung | Ausgabe |
|---|---|---|
| **LIVE** | Setup `VALIDATED` **und** Edge-Health nicht `BROKEN` | 🔥 BUY / 🎯 / • — echtes Signal |
| **SHADOW** | Setup `UNVALIDATED` / `IN_VALIDATION`, oder Edge-Health unbekannt | ⚠️ SHADOW-SIGNAL — volle Analyse + Forward-Tracking, **kein** actionable Signal |
| **BLOCKED** | Setup `EDGE_DEGRADED` / `RETIRED`, oder Edge-Health `BROKEN` | 🚨 BLOCKED — auch SHADOW unterdrückt |

### `ValidationRegistry` (`config/setup_validation.json`)

Einzige Autorität für Regel 3. **Konservativer Default: alles `UNVALIDATED`.** Ein Setup wird
`VALIDATED`, wenn **(a)** historische OOS-Edge belegt ist (Backtest → OOS → Walk-Forward →
Monte-Carlo) **und (b)** ≥ `forward_trades_required` Paper-/Forward-Trades die Baseline
bestätigen. Bricht die Edge auf Recent-Daten → `edge_degraded` (`registry.degrade(...)`).

**Stand 2026-08-31:** `SMC-SWEEP-REV-01 @ 0.1.1` = `UNVALIDATED` (keine belegte OOS-Edge, siehe
`docs/STRATEGY-EDGE-INVESTIGATION-2026-08.md`). Live-Signale laufen als SHADOW.

## Konkrete Signal-Ausgabe (Masterplan §24)

`strategy/signal_report.py::build_signal_report(gated_result)` rendert:

```
🔥 A+ BUY · XAUUSDT · LONG          ← oder: ⚠️ SHADOW-SIGNAL · Setup nicht validiert
Entry / Stop Loss / TP1 / TP2 / TP3(Runner) · R:R · Opp.Score · Confidence · Risk
Setup · Warum · Invalidation · Risiken
```

- `⏳ WAIT` — Kette lebt, aber < ARMED (`Decision.wait`).
- `NO_TRADE` — kein gültiges Setup / hartes Veto / harter No-Trade-Grund.
- `🚨 INVALIDATED` — bestehendes Setup verliert die Gültigkeit (`DynamicSignal` → `SignalState.INVALIDATED`, Event `SignalRevised`).

## 24/7 Live-Re-Evaluation

`LivePipeline` bewertet bei **jeder bestätigten M5-Bar** neu (nicht „alle paar Stunden"):
`strategy.evaluate` → `SignalTracker.observe` → `SignalUpdate`. Abgedeckte Aktionen
(`SignalChangeKind`): `ENTRY_CHANGED`, `SL_CHANGED`, `TP_CHANGED`, `STRENGTHENED` / `WEAKENED`
(Score-Δ), R:R + Score + Confidence neu berechnet, `TP_REACHED` (Teilgewinn-Empfehlung),
`EXIT_REQUIRED`, `INVALIDATED`, `EXPIRED`. Publiziert als `SignalRevised` / `AlertRaised` /
`PaperPositionChanged` auf dem EventBus.

## „Nicht zwanghaft jeden Tag ein Signal"

`Decision` wird nur BUY/SELL, wenn **alle** harten Gates bestanden sind (Regime, Setup-FSM bis
ARMED, Veto, Location, RR, Confluence, Contradictions, Confidence-Floor, Score ≥ B). Das
`live_gate` fügt hinzu: **+ validiert + Edge intakt**. Ohne das alles → WAIT / NO_TRADE.
„Lieber kein Trade als ein schlechter Trade" ist damit an mehreren Stellen strukturell verankert.

## Neu in diesem Schritt

| Datei | Inhalt |
|---|---|
| `governance/validation.py` | `ValidationStatus`, `SetupValidation`, `ValidationRegistry` (builtin + `from_file`) |
| `governance/edge_health.py` | `BaselineMetrics`, `EdgeHealth`, `assess_edge_health(baseline, recent_trades)` |
| `governance/live_gate.py` | `LiveEligibility`, `LiveGateReport`, `evaluate_live_gate(...)` |
| `governance/apply.py` | `apply_live_gate(EvaluationResult, registry, recent_trades=…) -> EvaluationResult` |
| `strategy/evaluate.py` | `EvaluationResult.live_gate` + `.is_actionable_live` |
| `strategy/signal_report.py` | `SignalReport.live_eligibility` + SHADOW/BLOCKED-Kopfzeile |
| `scripts/run_live_daemon.py` | `--validation-config`; Signale → `_signals_emitted` (LIVE) / `_shadow_signals` (SHADOW); Audit-Log-Eintrag `signal_emitted` vs `shadow_signal` |
| `config/setup_validation.json` | Registry-Datei (SMC-SWEEP-REV-01 = unvalidated) |
| `tests/unit/test_governance.py` | 11 Tests |
