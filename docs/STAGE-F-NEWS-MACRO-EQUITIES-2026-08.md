# Stufe F — News / Macro / Aktien (2026-08-31)

Masterplan §12–§21 (News-, Macro-, Kalender-, Fundamental-, Earnings-, Breadth-Engine).

## Ausgangslage (bereits vorhanden)

| Baustein | Datei | Status |
|---|---|---|
| News-Engine (PIT, asset-spezifisch, `UNKNOWN` ohne Feed) | `analysis/news.py` | ✅ |
| Macro-Engine (Rate-Cycle / Inflation / Growth / Risk-Sentiment) | `analysis/macro.py` | ✅ |
| Cross-Asset-Kontext aus Macro/OHLCV | `data/providers/cross_asset.py` | ✅ |
| **FRED/ALFRED-Adapter** (real, Vintage-korrekt, `UNAVAILABLE` ohne `FRED_API_KEY`) | `data/providers/fred_alfred.py` | ✅ |
| Economic-Calendar-Vertrag + CSV-Implementierung | `data/providers/news_calendar.py` | ✅ (Live-Fetch = Vertrag) |
| Aktien-Daten-Vertrag + Corporate-Action-Anpassung (Split/Dividende, PIT) | `data/providers/equities.py` | ✅ (Fetch = Vertrag) |
| News-Relevanz-Gate | `strategy/news_relevance.py` | ✅ |

## Neu gebaut in Stufe F

| Datei | Masterplan | Inhalt |
|---|---|---|
| `analysis/breadth.py` — `compute_market_breadth()` | §21 | **Market Breadth aus vorhandenen Multi-Asset-OHLCV — kein externer Feed nötig.** Advancers/Decliners, % über SMA20/SMA50, neue Hochs/Tiefs über Lookback → `breadth_score` −1..1 + Regime RISK_ON / NEUTRAL / RISK_OFF / **UNKNOWN** (< 5 Instrumente mit Historie). Point-in-Time (`close_time <= as_of`). |
| `analysis/fundamentals.py` — `assess_fundamentals()` | §19 | **Nur Einzelaktien.** Provider-neutrale `StockFundamentals` (KGV/PEG/EV-EBITDA, Umsatz-/EPS-Wachstum, Margen/ROE/FCF, Verschuldung/Liquidität) → vier 0–1-Sub-Scores (valuation/growth/quality/health) + Composite + Verdikt STRONG/SOLID/MIXED/WEAK. Fehlende Kennzahl fließt **nicht** ein; gar nichts → `UNKNOWN`. |
| `analysis/earnings.py` — `assess_earnings()` | §20 | `EarningsEvent`-Kalender → `EarningsContext`: **Blackout** (kein neuer Swing-Einstieg ≤ 5 Handelstage vor bestätigtem Termin, `blocks_new_entry=True`), **Post-Earnings-Drift** (starke EPS-Überraschung < 3 Tage her → `drift_bias` ±1, unterstützend nicht auslösend), `UNKNOWN` ohne Kalender. |
| `tests/unit/test_stage_f_analysis.py` | | 11 Tests |

Alle drei Engines folgen dem Projekt-Prinzip **NO BLIND AI**: fehlt die Datenquelle, ist das
Ergebnis explizit `UNKNOWN` / `unavailable` — nie ein geratener neutraler Wert.

## Verifiziert

```
uv run pytest -q            → 1011 passed
uv run mypy --strict src/   → Success: no issues found in 192 source files
uv run ruff check / format  → clean
```

## Status

**DONE**
- Market Breadth (§21) — voll funktionsfähig auf vorhandenen Daten, getestet.
- Stock Fundamentals (§19) + Earnings Engine (§20) — Bewertungslogik + PIT + `UNKNOWN`-Pfade, getestet.
- FRED-Adapter, Economic-Calendar-Vertrag, Aktien-Vertrag + Corporate-Actions: bereits vorhanden, bestätigt.

**PARTIAL**
- **Breadth / Fundamentals / Earnings noch nicht in den Opportunity-Score / die Strategy-Assembler verdrahtet.** Die Engines liefern Kontext-Objekte; die Einbindung als gewichtete Faktoren (heute in `scanner/opportunity.py` als `_KNOWN_UNAVAILABLE` gelistet) erfolgt, sobald echte Feeds laufen — sonst würde der Score-Nenner mit Test-Daten verfälscht.
- Konkreter Economic-Calendar-Live-Fetch (`news_calendar.py::get_calendar` = `NotImplementedError`): braucht eine Quelle. CSV-Pfad funktioniert heute.

**BLOCKED (fehlende Zugangsdaten / Quellen — Nutzer-Entscheidung nötig)**
- **`FRED_API_KEY`** (kostenlos, https://fred.stlouisfed.org/docs/api/api_key.html) → Macro-Engine mit echten Vintages statt `UNKNOWN`. Adapter steht.
- **Aktien-Datenquelle** (Polygon / Finnhub / Tiingo / EODHD — Key nötig): OHLCV + Corporate Actions + Fundamentals + Earnings-Kalender für Einzelaktien. Ohne diese Quelle bleibt die gesamte Aktien-Seite (Watchlist, Scanner-Universe, Portfolio-Konsolidierung Trade Republic) datenlos. `equities.py` ist der Anschlusspunkt.
- **News- / Wirtschaftskalender-Provider** (z. B. Finnhub, TradingEconomics, MarketAux): Live-News-Feed + Termine mit Impact-Bewertung. `news_calendar.py` + `analysis/news.py` sind die Anschlusspunkte.
- **Trade Republic** bleibt per Vorgabe unverbunden — Aktien-Holdings kommen nur über einen manuell befüllten `AccountPortfolio` (Stufe E) ins konsolidierte Bild.

**NEXT**
- Stufe G — 24/7-Operations: `ops/watchdog.py`, `ops/notify.py` (Telegram — Bot-Token nötig → PARTIAL), `safety/audit_log.py` (Hash-Chain), `ops/health.py`-Erweiterungen (Latenz / Stale-Candle / DB), Daily/Weekly-Report-Generator (bündelt Signal-, Portfolio-Intelligence-, Breadth-, Scanner-Output). Danach H (UI), I (Production).
- Sobald ein Aktien-Key da ist: konkreten `EquityDataAdapter` implementieren (Muster: `fred_alfred.py` — `UNAVAILABLE` ohne Key, Circuit-Breaker, ENV-only), dann Breadth/Fundamentals/Earnings als Score-Faktoren verdrahten.
