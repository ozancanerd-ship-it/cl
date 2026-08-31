# Stufe I — Production Readiness (2026-08-31)

Masterplan §71 + „später Echtgeld". Diese Stufe schließt die A→I-Runde ab: sie verdrahtet die
losen Enden aus D–H in den Live-Daemon und hält fest, **was vor Produktion / Echtgeld noch
fehlt** — und was davon eine Entscheidung von dir braucht.

## Gebaut / verdrahtet

| Änderung | Datei |
|---|---|
| Live-Daemon schreibt ein **Hash-Chain-Audit-Log** (startup / signal_emitted / alert / shutdown), verifiziert die Kette beim Herunterfahren (`_audit_log_ok` im Status-JSON) | `scripts/run_live_daemon.py` (`--audit-log`, Default `data/repository_real/live/audit.jsonl`) |
| Live-Daemon dumpt am Ende den **Dashboard-State** (10 Tabs) als JSON | `scripts/run_live_daemon.py` (`--dashboard-json`) |
| Verifiziert im 45-s-Lauf: 2 Audit-Einträge, `_audit_log_ok=true`, Dashboard mit allen 10 Tabs, `orders_sent=0` | — |

Damit sind die „PARTIAL: live-daemon wiring"-Punkte aus Stufe D/G/H erledigt (bis auf Telegram-
Versand — Token-Blocker — und den periodischen Report-Tick, der einen Scheduler braucht).

## Parität Backtest ≡ Paper ≡ Demo

- **Backtest ↔ Paper**: `engine/parity.py` (`run_parity`, `compare_decisions`, `render_parity`) +
  `BacktestConfig.parity_check` — getestet in `tests/integration/test_parity.py`
  (`match_rate == 1.0`). ✅
- **Paper ↔ Demo (Broker)**: `PaperLiveRunner.parity_against(reference)` steht; ein echter
  Demo-Decision-Strom fehlt (braucht cTrader/OANDA-Demo-Token). ⏳ BLOCKED auf Broker-Demo.

## Production-Readiness-Checkliste

| Punkt | Status |
|---|---|
| Deterministische Engine (eine `strategy.evaluate`-Pipeline für BT/Paper/Live) | ✅ |
| Kein Look-ahead (PIT-Cutoffs, `engine/parity.py` Beweis) | ✅ |
| 24/7-Supervisor: Recovery, WS-Reconnect, Stale-Backfill, Watchdog, Wall-Clock | ✅ (M-01) |
| `orders_sent == 0` überall asserted; keine `submit`/`cancel`-Pfade | ✅ |
| Read-only Account-Adapter (Kraken/Bybit/Binance), Secrets red+ENV/Keychain | ✅ |
| Hash-Chain-Audit-Log | ✅ (Stufe G/I) |
| Alert-System mit Anti-Spam | ✅ Code; ⏳ Telegram-Versand (Token) |
| Opportunity-Scanner + Ranking + Signal-Report | ✅ (Stufe C/D) |
| Portfolio-Intelligence (Hub/Correlation/Rating/Exit/ReEntry/Health/Rotation) | ✅ Code; ⏳ Broker→`AccountPortfolio`-Mapper |
| Market Breadth / Fundamentals / Earnings | ✅ Code; ⏳ Aktien-/Macro-Feeds |
| Dashboard-State (10 Tabs) | ✅ Daten-Layer; ⏳ HTTP-Server + Frontend |
| ≥ 100 Paper-Trades „validated" | ❌ BLOCKED durch Stufe B (keine ARMED-Setups auf verfügbaren Daten) |
| Deployment (Container, Prozess-Manager, Scheduler, Log-Aggregation) | ❌ offen — **Ziel-Umgebung ist deine Entscheidung** |
| Kill-Switch-Drills / Recovery-Drills unter Last | ❌ offen (nach Deployment) |
| Echtgeld-Anbindung | ❌ ausgeschlossen (Nutzer-Vorgabe) — kein OMS-Order-Lifecycle gebaut |

## Status

**DONE**
- Audit-Log + Dashboard-Dump im Live-Daemon verdrahtet und verifiziert.
- Parität Backtest ≡ Paper bewiesen und getestet.
- Production-Readiness-Checkliste erstellt — der Code-seitige Teil der Stufen A–H steht.

**BLOCKED — braucht eine Entscheidung / Zugangsdaten von dir**
1. **Strategie-Edge (Stufe B)** — der Kernblocker. Ohne einen 2. Setup-Typ mit belegter OOS-Edge
   erzeugt das System praktisch keine Trades. Kein UI, kein Deployment, kein Echtgeld ändert das.
   Nächster echter Schritt: 2. Setup-Typ entwerfen + wie Stufe B validieren.
2. **Deployment-Ziel** — Cloud-VM / Container-Host / lokaler Dauerbetrieb? Bestimmt Scheduler,
   Secrets-Handling, Log-/Alert-Routing, Restart-Policy.
3. **Zugangsdaten** — `FRED_API_KEY` (Macro), Aktien-Datenquelle (Polygon/Finnhub),
   News-/Kalender-Provider, `TELEGRAM_BOT_TOKEN`/`CHAT_ID`, ggf. cTrader/OANDA-Demo (FX + Demo-Parität).
4. **FastAPI + Frontend** — neue Dependencies + eigenes Frontend-Projekt (Stufe H).

**NICHT gebaut (bewusst)**
- `execution/oms.py` Order-Lifecycle — Masterplan Phase 8, verfrüht ohne belegte Edge und ohne Echtgeld-Freigabe.
- Trade-Republic-Anbindung — Nutzer-Vorgabe.

## Fazit der A→I-Runde

Der **Analyse-, Signal-, Portfolio-Intelligence-, Ops- und UI-Daten-Layer** ist code-seitig
vollständig, getestet (1026 Tests, mypy-strict-clean) und dokumentiert. Der Fortschritt Richtung
Echtgeld hängt jetzt an **einer inhaltlichen Frage** (hat die Strategie eine Edge? — heute: nicht
nachweisbar) und **vier externen Entscheidungen/Zugängen**, nicht mehr an fehlendem Code.
