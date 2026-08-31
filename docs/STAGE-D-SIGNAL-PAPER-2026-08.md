# Stufe D — Signal Engine + Paper Trading (2026-08-31)

Masterplan §22–§32 (Signal-Engine, konkretes BUY/SELL-Signal, 24/7-Signal-Anpassung,
Trade-Monitoring, Paper Trading) + §44 (≥100 Paper-Trades vor „validated").

## Ausgangslage (bereits vorhanden, NICHT neu gebaut)

| Baustein | Datei | Status |
|---|---|---|
| Signal-FSM (`scanning → bias_set → swept → structure_shifted → armed`) | `strategy/` FSM | ✅ vorhanden |
| Decision mit Geometrie (Entry/SL/TP1/TP2/TP3-Ref, RR, Blended-RR, Tier, Confidence) | `strategy/decision.py` | ✅ vorhanden |
| Dynamische Signal-Anpassung (`SignalRevised`) + Versionierung | `strategy/dynamic_signal.py` | ✅ vorhanden |
| Aktives Trade-Monitoring / Trade-Management | `strategy/trade_management.py`, `execution/trade_management.py` | ✅ vorhanden |
| Paper-Trading-Runner (Decision → Risk-Gate → PaperPosition, Ledger, Parity) | `strategy/paper_live.py` — `PaperLiveRunner` | ✅ vorhanden |
| Paper-Broker + Simulation | `execution/brokers/paper.py`, `execution/simulation.py` | ✅ vorhanden |
| Live-Pipeline verdrahtet Paper an WS-M5 | `runtime/live_pipeline.py` | ✅ vorhanden |
| No-Trade-Engine | `strategy/no_trade.py` | ✅ vorhanden |

## Neu gebaut in Stufe D

| Datei | Inhalt |
|---|---|
| `strategy/signal_report.py` — `SignalReport` + `build_signal_report()` | **Das konkrete, menschenlesbare BUY/SELL-Signal aus Masterplan §24.** Kein neuer Analyse-Schritt — rendert `decision` + `confluence` + `contradictions` + `mtf` + optional `OpportunityScore`. |
| `tests/unit/test_signal_report.py` | 4 Tests (Full-BUY, kein Report bei NO_TRADE, kein Report bei fehlender Geometrie, SHORT-Invalidation-Richtung) |
| `scripts/run_live_daemon.py` | `_on_tradeable(DecisionMade)` → `build_signal_report(...)` → Log `SIGNAL\n…` + `_signals_emitted[]` im Status-JSON; `--risk-pct` Flag |

### `SignalReport` — die §24-Struktur

```
🔥 A+ BUY · XAUUSDT · LONG

Entry:        4480
Stop Loss:    4460
TP1:          4520
TP2:          4560
TP3:          Runner: Trailing M15, aktiv nach TP2  (~4600)
R:R (→TP2):   4.00
Blended R:R:  3.10
Opp. Score:   91/100
Confidence:   88%
Risk:         1.00%

Setup:        SMC-SWEEP-REV-01  (swing)
Warum:        D1 higher-lows bestätigt; Sell-side sweep + Reclaim auf M15; Displacement > 1.5·ATR
Invalidation: Close unter 4460 (SL, 20 = 1R) ⇒ Setup ungültig
Risiken:      wide SL (1.4·ATR); news: nicht geprüft (kein Feed); macro: nicht geprüft (kein Feed); event_risk: nicht geprüft (kein Feed)
```

- **`why`** = Top-5-Confluence-Faktoren nach |Beitrag| (aus `confluence.factors`, keine Erfindung).
- **`risks`** = negative/harte Contradiction-Records + explizit „news/macro/event_risk: nicht geprüft (kein Feed)" (Masterplan: NO BLIND AI — fehlende Evidenz wird benannt, nicht verschwiegen).
- **`tp3_indicative`** = nächste signifikante opposing-Liquidität jenseits TP2 aus `mtf.per_tf[*].liquidity` — **nur Anzeige, kein Hard-TP** (die Runner-Spec bleibt „Trailing").
- `build_signal_report()` gibt **`None`** zurück, sobald `decision` ≠ BUY/SELL **oder** Entry/SL/TP1/TP2 fehlen. Kein Signal ohne vollständige Geometrie.

## Verifiziert

```
uv run pytest -q            → 984 passed
uv run mypy --strict src/   → Success: no issues found in 180 source files
uv run ruff check / format  → clean (284 files)

scripts/run_live_daemon.py --exchange bybit --symbols BTCUSDT ETHUSDT --max-seconds 55
  → _decision_ledger_rows=2, _scanner_evaluations=2, _signals_emitted=[]  (NO_TRADE-Markt → korrekt kein Signal)
  → orders_sent = 0
```

## Status

**DONE**
- §24 strukturiertes BUY/SELL-Signal (`SignalReport`) implementiert, getestet, in den Live-Daemon verdrahtet.
- Signal-Engine, dynamische Anpassung, Trade-Monitoring, Paper-Runner, No-Trade-Engine — bereits vorhanden und grün.

**PARTIAL**
- Signal wird aktuell nur **geloggt** + ins Status-JSON geschrieben. Alert-Versand (Telegram) folgt in Stufe G (`ops/notify.py`).
- `execution/oms.py` Order-Lifecycle bleibt **bewusst zurückgestellt** (Masterplan Phase 8 / Stufe I) — kein Echtgeld-Pfad, solange keine belegte Edge (Stufe-B-Befund). Kein vorzeitiger Bau.

**BLOCKED**
- **§44 „≥100 Paper-Trades vor validated" — blockiert durch Stufe B.** Die Regime-Gate + der einzige Setup-Typ (`SMC-SWEEP-REV-01`) erzeugen auf den verfügbaren Daten praktisch keine ARMED-Setups → keine Paper-Trades zum Zählen. Das ist **kein Bug**: „lieber keinen Trade als einen schlechten Trade". Der Zähler startet, sobald Stufe B einen 2. Setup-Typ mit belegter OOS-Edge liefert.
- Dukascopy 2-Jahres-XAUUSD-Historie aus dieser Umgebung nicht ladbar (Rate-Limit/Proxy) — nur 121 Tage mit kritischen Lücken erreichbar. Siehe `docs/STAGE-B-STRATEGY-VALIDATION-2026-08.md`. Backtests laufen auf der sauberen Binance-XAUUSDT-M5-Reihe (75 630 Bars).

**NEXT**
- Stufe E — Portfolio Intelligence (`PortfolioHub`, `CorrelationEngine`, `PositionIntelligence`, `ExitIntelligence`, `ReEntryEngine`, `PortfolioHealth`). Architektonisch unabhängig von der Signal-Edge, größte offene Lücke.
- Später (Stufe G): `SignalReport` an `ops/notify.py` → Telegram, Spam-Schutz.
