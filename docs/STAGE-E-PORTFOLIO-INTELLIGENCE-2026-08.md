# Stufe E — Portfolio Intelligence (2026-08-31)

Masterplan §33–§43. Die größte offene Lücke aus dem Gap-Report. Neues Paket
`src/trading_agent/portfolio_intel/` — der bestehende Accounting-Layer `portfolio/engine.py`
(`PortfolioLedger`, Risiko-State für die Strategy-Vetos) bleibt unangetastet.

## Gebaut

| Datei | Masterplan | Inhalt |
|---|---|---|
| `portfolio_intel/models.py` | §33–§35 | `Holding` (normalisierte Position, PnL / PnL-% / **PnL in R**), `AccountPortfolio` (Einzelkonto-Snapshot), `ConsolidatedPortfolio` (Equity, Cash-%, Allokation je Asset-Klasse, Gewicht je Instrument), `PositionVerdict`-Enum |
| `portfolio_intel/hub.py` — `PortfolioHub` | §33/§34 | Konsolidiert mehrere **read-only** Account-Snapshots → Netto-Holdings je Instrument. Gehedgte Gegenpositionen bleiben **getrennt** sichtbar. `to_portfolio_context_positions()` speist die V9-/Duplikat-Vetos der Strategy-Engine. |
| `portfolio_intel/correlation.py` — `CorrelationEngine` | §41 | **Echte** rollierende Pearson-Korrelation aus überlappenden OHLCV-Log-Returns. Ersetzt die statische `ClusterMap`. Union-Find-Cluster per Schwelle. < 20 gemeinsame Returns → ρ = 0.0 (nicht bewertbar, **kein Fake**). `static_correlations()` → Format für `PortfolioContext`. |
| `portfolio_intel/position_intel.py` — `PositionIntelligence` / `PositionRating` | §36 | Je offene Position: Score **0–100** + Verdikt **STRONG_HOLD / HOLD / WATCH / REDUCE / EXIT**. 7 gewichtete, **erklärbare** Faktoren: PnL-Zustand, HTF-Trend-Alignment, Struktur-Support (Confluence-Vorzeichen relativ zur Positionsrichtung), Abstand zur Invalidierung, frisches Gegen-Signal, Konzentration, Korrelations-Hitze. **Harte Overrides** → sofort EXIT: Kurs durch SL, oder Strategy-Engine gibt Gegen-Signal. Zieht **denselben** `EvaluationResult` wie die Strategy-Engine (NO BLIND AI). |
| `portfolio_intel/exit_intel.py` — `ExitIntelligence` / `ExitPlan` | §37 | Verdikt → umsetzbarer Plan: `NONE` / `TRAIL_STOP` (mit konkretem neuen Stop) / `PARTIAL` (50 %) / `FULL`. Break-even-Stop ab +1R, Trailing mit 1R Give-back, zieht Stops nur enger, nie weiter. |
| `portfolio_intel/reentry.py` — `ReEntryEngine` | §38 | Registriert einen `ReEntryWatch` **nur** wenn der Exit die These *nicht* gebrochen hat (Trailing/Teil-Exit/Shakeout — nicht bei „invalidation"). `assess()` prüft 5 Bedingungen (These intakt, HTF-Trend gleiche Richtung, Level zurückerobert, frisches Setup gleiche Richtung, kein Gegen-Signal) → Readiness 0–1 + Verdikt `RE_ENTRY_WATCH`. |
| `portfolio_intel/health.py` | §39/§40/§42 | `PortfolioHealth` → **0–100** + GREEN/YELLOW/RED aus 7 Komponenten (Diversifikation via inverse HHI, Konzentration, Korrelations-Hitze, mittlere Positions-Qualität, Cash-Puffer, Allokations-Drift ggü. ~50/50-Zielband, offener Verlust) + Flags. `PortfolioRanking` (Holdings nach Rating). `RotationEngine` → `RotationSuggestion` (schwächstes Holding ↔ beste freie actionable Opportunity, nur bei Score-Vorsprung ≥ 20). **Kein Auto-Verkauf.** |
| `portfolio_intel/report.py` — `PortfolioIntelligenceEngine` | §33–§43 | Fassade: ein `assess(accounts, evaluations, price_series, opportunities)` → ein `PortfolioIntelligenceReport` (`as_dict()` für UI/API). |
| `tests/unit/test_portfolio_intel.py` | | 17 Tests |

## Formeln

**PositionRating** = `100 · Σ(faktor·gewicht) / Σ(gewicht)`, Gewichte: trend_alignment 0.22,
position_quality-relevante je 0.15, concentration 0.10, correlation_heat 0.08.
Schwellen: ≥78 STRONG_HOLD · ≥62 HOLD · ≥45 WATCH · ≥30 REDUCE · sonst EXIT. Harte Overrides
umgehen die Schwellen.

**PortfolioHealth** = `100 · Σ(komponente·gewicht)`, Gewichte: position_quality 0.22,
concentration 0.18, diversification 0.16, correlation_heat 0.14, cash_buffer / allocation_drift
/ drawdown je 0.10.

## Verifiziert

```
uv run pytest -q            → 1000 passed
uv run mypy --strict src/   → Success: no issues found in 189 source files
uv run ruff check / format  → clean
```

Korrelations-Test: zwei synthetische Serien mit identischen Return-Schritten → ρ > 0.98 +
gemeinsamer Cluster; unabhängige Serie fällt aus dem Cluster. Zu wenig Overlap → ρ = 0.0.
End-to-End: `PortfolioIntelligenceEngine.assess()` über 2 Holdings + Preisreihen + Opportunity
→ Ratings, Exit-Pläne, Health-Grade, Ranking, Korrelationsmatrix, `as_dict()`.

## Status

**DONE**
- Alle Kern-Bausteine §33–§43 implementiert, getestet, mypy-strict-clean, in einer Fassade gebündelt.
- `CorrelationEngine` ersetzt die statische `ClusterMap` funktional (Anbindung an `PortfolioContext.static_correlations` über `CorrelationMatrix.static_correlations()`).

**PARTIAL**
- **Account-Adapter → `AccountPortfolio`**: Die read-only Adapter (Kraken/Bybit/Binance) existieren und liefern Balances/Positionen. Ein dünner Mapper `adapter-Rohdaten → Holding/AccountPortfolio` fehlt noch pro Broker (Feld-Mapping, Asset-Klassen-Zuordnung, Mark-Preis-Beschaffung). Bewusst getrennt gehalten, damit `portfolio_intel` broker-agnostisch bleibt.
- Verdrahtung in den Live-Daemon / eine periodische `PortfolioIntelligenceReport`-Ausgabe: offen (kommt mit Stufe G — Reports/Scheduler).
- Aktien-Holdings (Trade Republic) fließen erst ein, wenn eine Aktien-Datenquelle steht (Stufe F) — bis dahin trägt der Nutzer sie manuell als `AccountPortfolio` bei, oder sie fehlen im konsolidierten Bild.

**BLOCKED**
- **Trade-Republic-Konsolidierung** — bewusst nicht verbunden (Nutzer-Vorgabe: „Trade Republic NICHT verbinden"). `PortfolioHub` nimmt einen manuell befüllten `AccountPortfolio` entgegen; keine API-Anbindung.
- Live-Aktienkurse für Mark-Preis / Korrelation von Einzelaktien → Stufe F (Datenquelle fehlt).

**NEXT**
- Stufe F — News / Macro / Aktien: FRED-Adapter (Contract bauen, `FRED_API_KEY` fehlt → BLOCKED), Economic-Calendar-Contract, Aktien-Datenquelle (Polygon/Finnhub — Key fehlt → BLOCKED), Market Breadth, Earnings-Engine-Contract. Wo kein Key: Adapter + Vertrag + „nicht verfügbar"-Pfad bauen, damit der Opportunity-Score die Faktoren sauber ausweist.
