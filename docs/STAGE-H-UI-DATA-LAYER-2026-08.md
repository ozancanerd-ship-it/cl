# Stufe H — UI (Daten-Layer) (2026-08-31)

Masterplan §58 (Chart-Annotationen) + §63–§70 (10-Tab-App). Bewusst als vorletzte Stufe.

## Entscheidung: Daten-Layer jetzt, HTTP-Server + Frontend als eigener Schritt

Das Ziel-UI braucht **FastAPI + uvicorn** (neue schwere Dependencies) und ein Deployment-Ziel.
Beides ist eine bewusste Entscheidung, die mit dir abzustimmen ist — und ein Frontend lässt sich
aus dieser Umgebung nicht sinnvoll bauen/prüfen. Deshalb liefert Stufe H den **framework-freien
Daten-Layer**: die exakten JSON-Objekte, die ein späterer FastAPI-Layer (oder ein statisches
HTML-Dashboard) nur noch serialisiert. Kein Wegwerf-Code — genau diese Assembler bleiben.

## Gebaut

| Datei | Masterplan | Inhalt |
|---|---|---|
| `chart/annotations.py` — `build_chart_annotations()` (war 4-Zeilen-Stub) | §58 | Aus `SignalReport` (+ MTF-Kontext) → Lightweight-Charts-Payloads: `PriceLine` (Entry/SL/TP1/TP2/TP3-indikativ, mit Farbe + Linienstil), `Marker` (arrowUp/belowBar für LONG, arrowDown/aboveBar für SHORT), `Zone` (Liquiditäts-Bänder aus `mtf.per_tf[*].liquidity`, max 6). `as_dict()` = fertige Frontend-Payload. Reine Datenübernahme, keine Analyse. |
| `api/dashboard.py` — `build_dashboard_state()` | §63–§70 | `DashboardInputs` (alle Engine-Outputs) → `DashboardState` mit den **10 Tabs** (`overview`, `market_scanner`, `top_opportunities`, `chart_analysis`, `signals`, `my_portfolios`, `paper_trading`, `performance`, `news_macro`, `system_health`). Jeder Tab trägt `available: true/false` — fehlt ein Baustein, sagt der Tab das, statt Zahlen zu erfinden (NO BLIND AI). `overview` verdichtet zu Headline + bester Opportunity + Anzahl actionable Setups + Portfolio-Health + Breadth-Regime + Blocker-Liste. `paper_trading.validated` erst bei ≥ 100 Trades (Masterplan §44). |
| `tests/unit/test_stage_h_ui.py` | | 4 Tests |

## Verifiziert

```
uv run pytest -q            → 1026 passed
uv run mypy --strict src/   → Success: no issues found in 194 source files
uv run ruff check / format  → clean
```

Chart: BUY-Signal → 5 Preislinien + 1 arrowUp-Marker belowBar + 2 Liquiditäts-Zonen; SELL →
arrowDown aboveBar. Dashboard: leerer Input → 10 Tabs, datengetriebene unavailable,
`signals`-Tab nennt den NO-TRADE-Zeitraum; voller Input → `overview` mit bester Opportunity,
`paper_trading.validated=True` bei 120 Trades, `my_portfolios` mergt die Portfolio-Intelligence.

## Status

**DONE**
- Chart-Annotation-Payloads (§58) — implementiert, getestet.
- Dashboard-State-Assembler für alle 10 Tabs (§63–§70) — implementiert, getestet, `available`-Marker pro Tab.

**PARTIAL / offen (eigener Schritt)**
- **FastAPI-Server** (`api/` REST-Endpunkte + WebSocket-Bridge über den EventBus): braucht `fastapi`+`uvicorn` in `pyproject.toml` — **Dependency-Entscheidung durch dich**. Der Server wäre dünn: er ruft `build_dashboard_state()` / `build_chart_annotations()` auf und pusht EventBus-Events über WS.
- **Frontend** (10-Tab-SPA, Lightweight-Charts): eigenes Projekt, eigenes Build-Setup, eigenes Deployment-Ziel. Nicht aus dieser Umgebung baubar.
- Ein einfaches **statisches HTML-Dashboard** (ein File, `fetch()` auf ein vom Live-Daemon geschriebenes `dashboard.json`) wäre ein dependency-freier Zwischenschritt — auf Wunsch.

**NEXT**
- Stufe I — Production: Deployment-Setup, Kill-Switch-Drills, Parität **Backtest ≡ Paper ≡ Demo**, Watchdog-Recovery-Drills. Hängt an einer Deployment-Entscheidung. Echtgeld weiterhin ausgeschlossen (Nutzer-Vorgabe).
- Kleinere offene Verdrahtungen aus D–G in den Live-Daemon (Signal→Audit→Notify, periodischer Report-Tick, `dashboard.json`-Dump).
