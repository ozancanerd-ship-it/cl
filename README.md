# AI Trading Agent

Professionelles, modulares Multi-Asset-AI-Trading-System.

**App: https://ozancanerd-ship-it.github.io/cl/** — Gesamtmarkt-Scan, eingezeichnete
Chartanalyse, Portfolio-Cockpit. Läuft stündlich über GitHub Actions.
Betriebsanleitung: [`docs/BETRIEB.md`](docs/BETRIEB.md).

> ⚠️ **Status: DEVELOPMENT / PAPER / DEMO TRADING**
>
> - Keine echten Orders.
> - Keine Echtgeld-Broker-Anbindung.
> - Keine API-Keys / Secrets im Repo.
> - Ziel dieser Phase: Architektur, Datenmodell, Analyse-Engines, Backtesting, Paper-Trading.

---

## Ziel

Ein profit-orientiertes Trading-System mit bewusstem, aber **streng kontrolliertem** Risiko.
Die Handelslogik ist vollständig **brokerunabhängig**. Broker/Börsen werden später über
separate Adapter angebunden (Bybit für Crypto, Trade Republic für Aktien nur über zulässige
Schnittstellen, TradingView als Analyse-/Chartquelle).

## Asset-Klassen

| Klasse | Beispiele | Ausführung (später) |
|--------|-----------|---------------------|
| Aktien | US Large Caps, **keine ETFs** | Trade Republic (nur offizielle/zulässige APIs) |
| Crypto | BTC, ETH, liquide Altcoins | Bybit |
| Gold   | XAUUSD | Forex/CFD-Broker (später) |
| Forex  | Majors | Forex-Broker (später) |

## Kernprinzipien

1. **Brokerunabhängige Strategie.** Die Analyse- und Setup-Engines kennen keinen Broker.
2. **Multi-Timeframe.** Kein Handeln isoliert auf einem einzelnen Timeframe (D1 → M1).
3. **Confluence statt Einzelindikator.** Kein einzelner Indikator löst allein einen Trade aus.
4. **Risk Engine hat Vetorecht.** Sie kann jede Order ablehnen, auch wenn die Strategie einen
   Trade empfiehlt.
5. **Kein Martingale**, kein Nachkaufen von Verlusten, keine Risikoerhöhung nach Verlustserien.
6. **Keine Order ohne gültigen Stop-Loss** und ausreichendes Chance/Risiko-Verhältnis.
7. **Keine Trades bei unvollständigen/unsicheren Daten.**
8. **Alles nachvollziehbar.** Decision Ledger + Audit Log für jede Entscheidung.
9. **Modulare Entwicklung.** bauen → testen → Fehler beheben → testen → nächste Komponente.

## Projektstruktur (Kurzüberblick)

```
AI-Trading-Agent/
├── README.md              # Dieses Dokument
├── TODO.md                # Aufgaben & Baureihenfolge
├── ARCHITECTURE.md        # Detaillierte Architektur, Datenflüsse, Verträge
├── pyproject.toml         # Projekt-/Tooling-Konfiguration (Python)
├── requirements.txt       # Laufzeit-Abhängigkeiten (noch minimal)
├── requirements-dev.txt   # Dev-/Test-Abhängigkeiten
├── Makefile               # Bequeme Befehle (test, lint, format)
├── .gitignore
├── .env.example           # NUR Platzhalter-Namen, KEINE Werte
├── config/                # Beispiel-Konfigurationen (YAML), versioniert als *.example.*
├── docs/                  # Audits, Glossar, Workflow
│   └── strategy/          # Vollständige Strategie-Spezifikation (eingefroren: strategy_version 0.1.0)
├── FINAL_IMPLEMENTATION_PLAN.md   # Die 13 Phasen (verbindliche Reihenfolge)
├── src/trading_agent/     # Quellcode (Python-Package)
│   ├── core/              # Domänen-Typen, Enums, Events, Clock, Version
│   ├── config/            # Konfig-Laden, Schema-Validierung, Versionierung
│   ├── refdata/           # Instrument-Master, Trading-Calendar, Symbol-Mapping, Corporate Actions
│   ├── data/              # Market Data Engine + Quality-Monitor + Repository (Point-in-Time)
│   ├── analysis/          # Structure, Liquidity, SMC, S/R, Sessions, Regime, MTF, News, Macro
│   ├── strategy/          # DIE geteilte Strategy Engine: primitives, setup_detection, confluence, veto, confidence, scoring
│   ├── risk/              # Risk Engine (Veto), Position Sizing (dynamischer Hebel), Limits, Margin
│   ├── execution/         # Trade Management, Portfolio, Order Management, Reconciliation + Broker-ABCs
│   ├── engine/            # Pipeline (Bar-Close), Backtest, Paper Trading, Parity
│   ├── research/          # Point-in-Time-Dataset, Validation (Walk-Forward), Robustness (Monte-Carlo), Registry
│   ├── scanner/           # Autonomer Multi-Asset-Scanner + Signal Engine + Alerting
│   ├── allocation/        # Capital Allocation Engine (PLATZHALTER — 3 Horizonte, INVEST/WAIT/HOLD/REDUCE)
│   ├── viz/               # TradingView-Integration: Zeichnungen & Setup-Overlays (später)
│   ├── journal/           # Trading Journal, Decision Ledger, Performance Analytics
│   ├── ai/                # LLM-Reasoning-Layer: Output-Contract + Guardrails (Nutzung deaktiviert)
│   ├── state/             # Persistenz + Recovery (Positionen, Orders, Kill-Switch, Zähler)
│   ├── ops/               # Notify/Alerting, Monitoring, Data-Source-Health, Runbooks
│   ├── safety/            # Kill Switch (hierarchisch), Audit Log, Error Handling
│   └── utils/             # Logging (JSON, Redaction), Helfer
├── tests/                 # Unit- & Integrationstests (pytest)
├── scripts/               # Ausführbare Hilfsskripte (Backtest-Runs etc.)
└── data/                  # Lokale Daten – nicht versioniert
```

Ausführliche Komponentenbeschreibung: [ARCHITECTURE.md](ARCHITECTURE.md).
Vollständige Baureihenfolge: [FINAL_IMPLEMENTATION_PLAN.md](FINAL_IMPLEMENTATION_PLAN.md).
Strategie-Regeln: [docs/strategy/](docs/strategy/).

## Setup (Entwicklung)

Voraussetzung: Python 3.11+.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
pip install -e .
make test
```

> Hinweis: In dieser Umgebung sind die Xcode Command Line Tools (und damit `git`) noch nicht
> installiert. Für Versionskontrolle: `xcode-select --install` ausführen, dann `git init`.

## Konfiguration

Alle Laufzeitparameter kommen aus YAML-Dateien in `config/`. Im Repo liegen nur
`*.example.yaml`-Dateien. Lokale Kopien (`config/config.yaml` etc.) sind über `.gitignore`
ausgeschlossen. Es werden **keine Secrets** in Konfigdateien gespeichert – Secrets kommen
später ausschließlich aus Umgebungsvariablen / einem Secret-Manager.

## Roadmap

Verbindliche Reihenfolge: [FINAL_IMPLEMENTATION_PLAN.md](FINAL_IMPLEMENTATION_PLAN.md).
Abhakbare Kurzfassung: [TODO.md](TODO.md). Die 13 Phasen:

1. **Data Foundation** – core, refdata, Market Data Engine, Quality-Monitor, Repository.
2. **Research / Backtesting** – Backtest-Rahmen, Validation/Walk-Forward, Robustness, Registry, Bias-Tests.
3. **Strategy Engine** – primitives, Analyse-Engines, regime, setup_detection, confluence, veto, confidence, scoring. *(eine geteilte Engine)*
4. **Risk + Portfolio** – Risk Engine (Veto), dynamisches Sizing, Limits, Margin, Portfolio, Kill Switch, State.
5. **Autonomous Market Scanner + Signal Engine** – autonomer Scan, `SignalReport`, Alerting, Decision Ledger.
6. **TradingView-Integration** – automatische Zeichnungen (BUY/SELL, Entry, SL, TP1–3, Liquidity, FVG, OB, BOS/CHoCH, …).
7. **Portfolio Sync + Capital Allocation** – Multi-Asset-Portfolio, INVEST/WAIT/HOLD/REDUCE, `allocation/`-Stub.
8. **Paper Trading** – SimAdapter, Trade Management, Order-Lifecycle, Parity.
9. **Bybit Demo Trading** – Testnet, echte API, kein Echtgeld.
10. **Execution + Reconciliation** – Broker-State ↔ intern, Emergency-Flatten, Clock-Drift.
11. **Monitoring + Journal + Analytics** – Monitoring, Audit Log (Hash-Chain), Performance, Runbooks, Backups.
12. **Production Readiness** – Versionierung, Kill-Switch-Hierarchie, Recovery, alle Freigabe-Gates.
13. **Begrenzter Live-Betrieb** – erst nach separater, ausdrücklicher Nutzer-Entscheidung.

> **Status:** Spezifikation abgeschlossen (`strategy_version 0.1.0`, MVP-Setup `SMC-SWEEP-REV-01`,
> MVP-Instrumente BTCUSDT/ETHUSDT). Implementierung noch nicht begonnen.

## Lizenz

Noch nicht festgelegt (privates Entwicklungsprojekt).
