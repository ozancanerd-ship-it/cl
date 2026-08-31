# Stufe C — Market Scanner + Opportunity Ranking (2026-08-31)

Das definierende Herzstück des Masterplans (§4/§5/§6): ein 24/7-Scanner, der **alle** Instrumente
gegeneinander rankt, das Ranking bei jeder Marktveränderung neu berechnet und erklären kann,
warum ein Asset auf Platz 1 steht.

## Gebaut

| Datei | Inhalt |
|---|---|
| `scanner/opportunity.py` — `score_opportunity(EvaluationResult) -> OpportunityScore` | **Asset-übergreifend vergleichbarer 0–100-Score.** Kein neuer Indikator — verdichtet vorhandene Reports. |
| `scanner/market_scanner.py` — `MarketScanner` + `TopOpportunities` | Score-Map je Instrument · dynamische Rangliste · `explain()` · Bus-Events |
| `runtime/events.py` | `OpportunityScored`, `RankingUpdated` |
| `scripts/run_live_daemon.py` | Scanner an die LivePipeline gekoppelt; `_top_opportunities` im Status-JSON |
| `tests/unit/test_market_scanner.py` | 5 Tests |

## Opportunity Score — Formel

```
score = 100 · ( 0.6·Kontext  +  0.4·(Setup-Reife · Strategie-Score) )
```

**Kontext-Faktoren** (immer bewertbar, auch ohne Setup — 0..1, gewichtet, normiert):

| Faktor | Gew. | Quelle |
|---|---:|---|
| htf_bias_clarity | 12 | `mtf.htf_directional` + D1-Regime-Score (klar vs. `unclear`) |
| liquidity_event | 12 | Confluence-Gruppe `LIQUIDITY_EVENT` (Sweep + Reclaim + Qualität) |
| regime_alignment | 12 | `mtf.htf_regime_gate` (ok + Disagreement) |
| structure_shift | 10 | Confluence `MOMENTUM_STRUCTURE` |
| momentum · entry_location · mtf_coherence · risk_reward · volatility_regime | 8 je | Confluence-Gruppen bzw. `rr_to_tp2` bzw. H4-Vol-Regime |
| derivatives | 6 | `DerivativesContext` (Funding nicht extrem + OI steigend) — nur bei validen PIT-Daten |
| data_confidence | 6 | `confidence.data` |
| spread_quality | 4 | Spread / ATR |

**Setup-Reife** ∈ [0,1] aus dem FSM-State: `scanning` 0.05 → `swept` 0.50 → `structure_shifted` 0.90
→ `armed` 1.00. **Strategie-Score** = `ScoreReport.final_score/100` (0, wenn kein Kandidat).

**Noch nicht bewertet** (explizit `unavailable`, fällt aus dem Nenner — kein Fake):
`news`, `macro`, `event_risk`, `fundamentals`, `liquidations`, `correlation` (Stufe F).

## TopOpportunities

- `ranking()` / `top(n)` — nach Score, dann Setup-Reife, absteigend. Veraltete Bewertungen
  (> `stale_after_s`) fallen raus.
- `rank_of(instrument)` · `explain(instrument)` — Faktor-Bilanz (Top-5 Beiträge), fehlende
  Faktoren, Vergleich zu #2 („warum #1").
- `RankingUpdated`-Event bei jedem Wechsel von #1 oder der Reihenfolge.

## Verifiziert (Live, 2026-08-31)

```
scripts/run_live_daemon.py --exchange bybit --symbols BTCUSDT ETHUSDT SOLUSDT XRPUSDT
  → 4 Instrumente gescannt + gerankt, RankingUpdated-Events, orders_sent=0
  → alle Score 26.8 / SCANNING (ruhiger Markt, regime_unclear überall — korrektes Verhalten:
    ohne echtes Setup rankt nichts über die anderen; sobald ein ARMED-Setup entsteht, springt
    dessen Score über den Setup-Term nach oben)
```

Test-Fall `test_armed_a_plus_scores_high`: ARMED + A+ + Confluence 0.8–0.9 → Score ≥ 80,
`is_actionable=True`.

## Integration mit Stufe B

Der Scanner ist **unabhängig davon, ob die Strategie eine Edge hat** — er rankt den *Kontext*.
Bei 0 tradebaren Setups (Stufe-B-Befund) zeigt das Ranking trotzdem, *welches* Asset dem
besten Kontext am nächsten ist. Sobald ein 2. Setup-Typ (Stufe B / P1) Kandidaten erzeugt,
speist deren `final_score` direkt den Opportunity-Score.

## Offen (Stufe C, für später)

- **`runtime/scheduler.py`** (echter Bar-Close-getakteter Multi-Asset-Scan über ein großes
  Universum statt der Watchlist) — aktuell läuft der Scan über die LivePipeline-Instrumentliste.
- Score-**Kalibrierung** (Faktor-Gewichte sind PROPOSED DEFAULT, `ScoreWeights`-Platzhalter da).
- News/Macro/Fundamentals/Correlation als echte Faktoren (Stufe F).
