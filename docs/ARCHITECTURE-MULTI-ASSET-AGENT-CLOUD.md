# Architektur-Vorbereitung — Multi-Asset · Multi-Agent · 24/7-Cloud

**Stand:** 2026-08-29 · **Nur Architektur/Design. Kein neuer Ausführungs-Code, keine Broker-Keys,
keine Echtgeld-Orders.** Ergänzt `FINAL_ARCHITECTURE_AUDIT.md` (Begründungen dort) um die
konkrete Modul-/Interface-Form.

---

## 1. Multi-Asset

### 1.1 Prinzip

**Eine Strategy Engine, viele Instrumente.** Assetklassen-Unterschiede werden über **Daten**
(Asset-Metadaten + Provider-Adapter) modelliert, **nie** über kopierte Strategien. Es gibt genau
eine `evaluate(MarketContext) -> EvaluationResult`.

### 1.2 Was schon trägt

| Baustein | Ort | Deckt |
|---|---|---|
| `Instrument` (frozen) | `refdata/models.py` | `asset_class`, `tick_size`, `lot_size`, `min_notional`, `contract_size`, `margin_tiers` |
| `AssetClass` | `core/enums.py` | CRYPTO · ALTCOIN · GOLD · FOREX · EQUITY · ETF |
| `TradingCalendarSpec` | `refdata/models.py` | `is_24_7` (Crypto) · `weekend_gap` (FX/Gold) · reguläre Börse + Feiertage + DST |
| Seed | `refdata/seed.py` | 7 Instrumente über **alle** Assetklassen (BTC, ETH, SOL, XAUUSD, EURUSD, 1 Equity, 1 ETF) |
| Assetklassen-Parameter | `analysis/regime.py` | `extreme_atr_ratio` je Assetklasse; `RegimeParams` |
| `market_is_24_7` | `strategy/evaluate.py::EvaluateParams.__post_init__` | Crypto ⇒ kein Wochenend-/Session-Block |
| Symbol-Mapping | `refdata/symbols.py` | kanonisch ↔ Exchange/Broker |

### 1.3 Konkrete Prep-Items (kein Verhaltensänderung jetzt)

1. **`EvaluateParams.for_instrument(inst: Instrument) -> EvaluateParams`** — Factory, die
   `asset_class`, `mtf.tick_size`, `no_trade.market_is_24_7` und (später) assetklassen-spezifische
   Regime-/Vol-Schwellen aus dem `Instrument` + seiner `TradingCalendarSpec` ableitet. Heute
   setzt der Aufrufer `tick_size` noch manuell.
2. **`AssetProfile`** (neu, `refdata/`) — pro Assetklasse: typische ATR/Preis-Ratio-Bänder,
   Session-Verhalten (Overnight-Gap ja/nein), Funding (nur Perp), Corporate Actions (nur
   Equity), Mindest-Tick/Lot. Die `*Params`-Defaults referenzieren ein `AssetProfile` statt
   Magic Numbers. → `CALIBRATION_BACKLOG` (pro Assetklasse getrennt kalibrieren).
3. **Provider-Fähigkeiten je Assetklasse** in `data/registry.yaml` deklarieren (OHLCV / Funding /
   OI / Corp-Actions / Quotes) — der Router wählt anhand `Instrument.asset_class`.
4. **Gap-Handling** für nicht-24/7: der Assembler muss den Wochenend-/Overnight-Gap als
   *erwartete* Lücke kennen (`TradingCalendar`), damit `quality` ihn nicht als `GAP_CRITICAL`
   meldet. Interface steht (`_detect_gap(..., calendar)`), Verdrahtung für FX/Equity fehlt.
5. **Portfolio-Cluster** (`portfolio/engine.py::ClusterMap`) um Assetklassen-Cluster erweitern
   (Crypto-Beta, USD-Legs, Gold, Equity-Beta) — Korrelations-Deckel greift dann klassenübergreifend.

### 1.4 Nicht-Ziele

Keine assetklassen-spezifischen Strategie-Zweige. Keine getrennten `evaluate_crypto` /
`evaluate_fx`. Ein Setup-Katalog (SMC), parametrisiert pro Instrument.

---

## 2. Multi-Agent

### 2.1 Prinzip

**Eine zentrale Decision Engine entscheidet. Agenten liefern nur Informationen.** Kein Agent löst
eigenständig einen (Paper- oder echten) Trade aus. Keine konkurrierenden Trading-Gehirne.

```
        ┌──────────── Informations-Agenten (read-only, parallel) ────────────┐
        │  Market   News/Macro   Portfolio   Risk   Research   Validation    │
        │  Monitoring                                                        │
        └───────────────┬───────────────────────────────────────────────────┘
                        │  strukturierte Signale + Konfidenz + Zeitstempel
                        ▼
              ┌───────────────────────────┐
              │  CENTRAL TRADING ENGINE   │   ← einzige Instanz, die entscheidet
              │  strategy.evaluate(...)   │
              │  + RiskEngine.review(...) │   ← harte, letzte Schranke
              └───────────┬───────────────┘
                          ▼
             PaperLiveRunner  (Phase < 14: nur Paper)
```

### 2.2 Agent-Vertrag (neu: `agents/`)

```python
class AgentReport(Protocol):  # frozen, PIT
    agent: str
    as_of: datetime  # information_cutoff des Reports
    horizon: str  # "intraday" | "swing" | "macro"
    findings: tuple[Finding, ...]  # jeweils: claim, evidence, confidence[0..1], severity
    data_quality: float  # 0..1 — wie belastbar ist die Grundlage


class Agent(Protocol):
    def observe(self, ctx: AgentContext) -> AgentReport: ...  # reine Funktion, kein Seiteneffekt
```

| Agent | Input | Liefert (Beispiele) | Bindet an |
|---|---|---|---|
| **Market** | OHLCV MTF, Orderbook, Trades | Struktur/Regime-Zusammenfassung, Liquiditäts-Landkarte | `analysis/mtf.py` (schon da) |
| **News/Macro** | `NewsContext`, `MacroEvent` | Event-Fenster, Pre-Position-Ban, Überraschungs-Score | `data/providers/news_calendar.py` |
| **Portfolio** | `PortfolioLedger` | offene Heat, Cluster-Auslastung, Korrelations-Warnungen | `portfolio/engine.py` |
| **Risk** | `AccountState`, Limits | verbleibendes Tagesbudget, DD-Abstand, Kill-Switch-Status | `risk/risk_engine.py` |
| **Research** | Historie, Kalibrier-Reports | „dieses Setup in diesem Regime: OOS-Erwartung" | `data/repository_real/*.json` |
| **Validation** | Decision + alle Sub-Reports | Konsistenz-Checks, Leakage-Verdacht, Kontra-Indikation | `engine/parity.py`, Contradictions |
| **Monitoring** | Laufzeit-Telemetrie | Feed-Health, Latenz, Drift, „Engine still healthy" | `data/health.py` |

### 2.3 Wie die zentrale Engine Agenten konsumiert

- Agenten-Reports gehen als **Kontext** in `MarketContext` / `EvaluateParams` (z. B.
  `NewsContext`, `CrossAssetContext`, künftig `ResearchContext`) — **nicht** als direkter
  Score-Override.
- **Harte Vetos bleiben hart:** ein Agent kann `data_quality` senken oder ein `Contradiction`
  beisteuern; er kann einen `NO_TRADE` / `Veto` **nicht** aufheben. (Projekt-Constraint.)
- Die `RiskEngine` sieht **keine** Agenten-Confidence — sie prüft nur Konto/Portfolio/Limits.
- Aggregation der Agenten-Findings: Log-Odds/Bayes bleibt Backlog (`CALIBRATION_BACKLOG §1.5`),
  bis OOS-Daten die Trefferraten schätzbar machen.

### 2.4 Prep-Items

1. `agents/base.py` — `Agent` / `AgentReport` / `Finding` (frozen, PIT), + eine
   `AgentRunner`, die Agenten parallel über **denselben** `as_of` fährt (Determinismus).
2. Bestehende Analyse-Bausteine hinter die Agent-Fassade legen (kein Rewrite: `MarketAgent`
   ruft `build_mtf_context`).
3. `ResearchContext` / `ResearchAgent` an die Kalibrier-Reports hängen (read-only).
4. `Validation`-Agent = Heimat für die Continuous-Improvement-Checks (Leakage/Snooping).

---

## 3. 24/7-Cloud-Backend

### 3.1 Prinzip

Ein **langlebiger Prozess** (Supervisor + In-Process-Event-Bus), der Feeds, Engine, Agenten,
Alerts, Persistenz und Health besitzt und sauber herunterfährt. **Noch kein finales UI** — nur
eine schmale read-only API + strukturierte Logs/Alerts.

### 3.2 Zielbild (`runtime/`, M-01 im Audit)

```
runtime/
  supervisor.py     Prozess-Lebenszyklus, Health/Heartbeat/Watchdog, graceful shutdown
  bus.py            async Pub/Sub in-process (BarClosed, DecisionMade, SignalRevised, AlertRaised, …)
  drivers/
    backtest.py     ReplayClock → Bus         (deterministisch — schon als engine/backtest.py da)
    live.py         WS/REST feeds → Bus       (Phase 9+)
  scheduler.py      Cron-artige Jobs (Ingestion-Backfill, tägliche Reports, Ledger-Roll)
  persistence.py    Append-only Event-Log + Snapshots (Recovery)
  api.py            read-only HTTP: /health /positions /signals /alerts /decisions  (kein Trade-Endpoint)
```

Die Strategy Engine bleibt die **reine Funktion** — vom Bus-Handler aufgerufen, nicht umgekehrt.
Backtest und Live teilen sich Bus + Handler; nur der Driver unterscheidet sich (Audit C-01).

### 3.3 Betriebseigenschaften

| Thema | Ansatz |
|---|---|
| **Recovery** | Append-only Event-Log + periodische Snapshots (`PortfolioLedger`, `SignalTracker`, `KillSwitchState` sind schon JSON-serialisierbar). Neustart = Snapshot laden + Events nachspielen. |
| **Idempotenz** | Jede Bar hat `close_time`; `_seen_fill_bar` / Dedup verhindern Doppelverarbeitung nach Reconnect. |
| **Kill-Switch** | `safety/kill_switch.py` — hierarchisch (global/broker/asset/strategy/data), fail-safe (korrupte Datei ⇒ `global_=True`), atomar persistiert. Der Supervisor prüft ihn vor jedem Auto-Open. |
| **Zeit** | Alles UTC, `information_cutoff` getrieben von Bar-`close_time` (nicht Wall-Clock) — Backtest/Live identisch. |
| **Config** | `config/*.yaml` (Provider, Limits, Instrumente). Secrets **nur** ENV. |
| **Deployment** | Ein Container, ein Prozess. Persistenz auf Volume. Kein Kubernetes nötig für Phase < 14. |
| **Observability** | strukturierte JSON-Logs + `data/health.py` + tägliche Report-Artefakte. |
| **Sicherheit** | keine eingehenden Trade-Endpunkte; API read-only; ENV-Secrets; kein Broker-SDK im Image bis Phase 14. |

### 3.4 Prep-Items

1. `runtime/bus.py` + `runtime/supervisor.py` als dünnes Gerüst (async, in-process) — Handler
   rufen die vorhandenen `PaperLiveRunner` / `AlertEngine`.
2. `runtime/persistence.py` — Event-Log-Schema + Snapshot-Roundtrip-Test (alle State-Objekte
   sind schon frozen/serialisierbar).
3. `runtime/api.py` — FastAPI/Starlette, **nur GET**. Kein Order-Pfad.
4. `scheduler.py` — den Ingestion-Backfill (`scripts/ingest_binance_vision.py`) als Job kapseln.
5. Graceful-Shutdown-Test: SIGTERM → offene Signale einfrieren, Snapshot, Exit 0.

---

## 4. Was diese Vorbereitung NICHT tut

- Keine Broker-/Exchange-Order-Anbindung, kein Testnet-Routing (Phase 14, separate Entscheidung).
- Kein finales Dashboard/UI.
- Keine neuen Laufzeit-Abhängigkeiten ohne Wheels.
- Keine Änderung an der Strategy Engine, am Risk-Constraint-Modell oder an den Defaults.
- Kein Agenten-Code, der selbst entscheidet oder handelt.
