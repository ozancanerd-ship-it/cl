# Gold / FX — READ-ONLY / Paper-Integration (2026-08-30)

Fortsetzung von `docs/GOLD-FX-DATA-SOURCES.md` (Quellen-Bewertung + Empfehlung). Hier: was
konkret gebaut, verdrahtet und **live getestet** wurde. Keine Order, keine Keys, keine
Windows-Abhängigkeit für die zentrale Engine.

## 1. Neue Provider-Adapter (`data/providers/`)

| Adapter | Zweck | Zustand | Test |
|---|---|---|---|
| `ctrader.py` — `CTraderAdapter` | **empfohlene produktive** FX/XAUUSD-Live-Quelle (Open API, Protobuf/JSON-WS, cloud-tauglich) | **Vertrag** — ohne `CTRADER_*` ENV → `status()==UNAVAILABLE`, `fetch_*` werfen `CTraderUnavailable`. Kein Fake. | 3 Unit-Tests |
| `dukascopy.py` — `DukascopyProvider` | **historische** Tick-Daten FX + XAUUSD (`.bi5`, LZMA, 20-Byte `>IIIff`) → Mid-M5 + Spread-Statistik | **echt & live verifiziert** — EURUSD + XAUUSD 2024-06-04/05 real geladen, dekodiert, zu M5/M15/H4 aggregiert, ins Repo geschrieben, Quality clean | 6 Unit-Tests (synthetische `.bi5`) + realer Ingest-Lauf |
| `yahoo_finance.py` — `YahooFinanceProvider` | keyless OHLCV FX/Gold — **nur indikativ / verzögert / kein Bid/Ask** — Pipeline-Durchstich | funktionsfähig, jede Bar `source="yahoo_indicative"` | 2 Unit-Tests + Live-Probe |

`mt5.py` bleibt unverändert als **gekapselter Windows-Fallback** (`platform_only="windows"`,
`status()==UNAVAILABLE` außerhalb Windows). `grep -r "import MetaTrader5"
src/trading_agent/{strategy,runtime,risk,engine}` = **leer** — die zentrale Engine hängt nicht
an MT5.

## 2. Referenzdaten (`refdata/`)

* `Instrument` erweitert (additiv): `pip_size`, `swap_long_points`, `swap_short_points`,
  `swap_basis`. Deskriptiv — die Strategy Engine liest sie nicht.
* `TradingCalendarSpec` erweitert (additiv): `daily_break_start` / `daily_break_end`
  (in `timezone`), honoriert in `TradingCalendar.is_open` (auch für `weekend_gap`-Kalender).
* Seed:
  * `xau_spot`-Kalender: **CME-Gold-Tagespause 21:00–22:00 UTC** ergänzt.
  * neue Instrumente **GBPUSD**, **USDJPY** (FOREX, `fx_weekday_24h`, pip/swap gesetzt).
  * **XAUUSD** + **EURUSD** um pip_size/swap ergänzt.
  * Symbol-Mappings: `XAUUSD → bybit XAUTUSDT / kraken XAUT/USD` (Tokengold-Proxy),
    `oanda`/`ctrader`/`dukascopy`/`yahoo`-Schreibweisen für alle vier Paare.
* Kraken-WS-Namensmap: `XAUUSD/XAUTUSDT → XAUT/USD`.

## 3. Pipeline-Verdrahtung (`runtime/live_pipeline.py`)

* `LivePipelineConfig.session_specs: tuple[SessionSpec, ...]` (neu, additiv). Für Nicht-24/7-
  Assets (Gold/FX/Aktien) wird es in `_feed` an `PaperLiveRunner.feed(mc, session_specs=…)`
  durchgereicht → die **gleiche** zentrale `strategy.evaluate` wendet den Session-Filter an.
* `scripts/run_live_paper.py`: bei `--asset-class` ≠ crypto/altcoin werden automatisch
  `seed_sessions()` als `session_specs` gesetzt.
* **Kein neuer Entscheidungspfad.** XAUUSD/FX nutzen `SMC-SWEEP-REV-01`,
  `strategy_version 0.1.1`, identisch zu Crypto. Asset-Unterschiede rein über
  Metadaten (Sessions, Kalender, Kosten, Instrument-Specs, Provider-Adapter).

## 4. Live-Tests (real, read-only) — 2026-08-30 ~00:25 UTC (Samstag)

### 4a. Quotes (`scripts/gold_fx_readonly_test.py`)

| Symbol | Quelle | Live | Bid/Ask | Spread | Data-Age | Health |
|---|---|---|---|---|---|---|
| **XAUUSD** | Bybit `XAUTUSDT` (Tokengold) | **JA** | 4455.3 / 4455.4 | 0.1 | 0.2 s | healthy |
| EURUSD | Yahoo (indikativ) | indikativ | — (kein Bid/Ask) | — | ~26 h (Fr.-Close, Wochenende) | healthy |
| GBPUSD | Yahoo (indikativ) | indikativ | — | — | ~26 h | healthy |
| USDJPY | Yahoo (indikativ) | indikativ | — | — | ~27 h | healthy |
| cTrader | Open API | **NOT_AVAILABLE** (keine ENV) | — | — | — | — |

Bybit-WS für `XAUTUSDT`: **verbindet** (0 Reconnects); confirmed M5-Bar folgt im 5-min-Takt.
FX ist am Wochenende geschlossen → Yahoo liefert nur den Freitag-Stand, **nichts wird
interpoliert**.

### 4b. Paper-Signal (`scripts/run_live_paper.py --asset-class gold`, 4 min)

```
warmup:  XAUTUSDT M5=401 M15=451 H4=301 D1=221
prime →  DecisionMade 00:20:00  NO_TRADE / scanning
WS M5 →  BarClosed 00:25:00 close=4455.3  →  DecisionMade  NO_TRADE / scanning
orders_sent = 0   ws_restarts = 0   quality_blocks = 0   stale = False
```

NO_TRADE-Reason-Codes: **`REGIME_UNCLEAR` + `WEEKEND` + `SPREAD_TOO_WIDE`**.
→ Der Session-/Wochenend-Filter greift (aus `session_specs`), der Regime-Gate greift wie bei
Crypto (gleiche Engine), der Spread-Gate greift. **NO_TRADE ist das korrekte Ergebnis** —
kein Setup wurde künstlich erzeugt.

## 5. Historische Daten

**Vorher:** keine Gold/FX-Historie im Repo (nur 6 Crypto-Symbole).
**Jetzt:** `scripts/ingest_dukascopy.py` gebaut (analog `ingest_binance_vision.py`) —
Tick → Mid-M5 → M15/H4/D1, Quality- + Replay-Validierung, vollständiges Dataset-Manifest
(Quelle, PIT-Konvention, OHLCV-Definition, Point-Faktoren, Spread je Symbol, Fingerprint).

Verifizierter Probelauf (2 Tage, XAUUSD + EURUSD):

| Symbol | M5-Bars | mittl. Spread | max. Spread | Quality |
|---|---|---|---|---|
| XAUUSD | 552 | 0.39 USD | 4.02 USD (News-Spike) | clean |
| EURUSD | 576 | 0.24 pip | 1.04 pip | clean |

Die Continuity-Prüfung meldete für XAUUSD korrekt eine 12-Bar-Lücke 20:55→22:00 UTC — das ist
**exakt die CME-Tagespause**, die der neue `xau_spot`-Kalender modelliert. Der volle Ingest
2023→2025 ist ein längerer Batch-Job (stündliche Dateien) — der Mechanismus steht.

**Dukascopy vs. bestehende Architektur:** passt sauber ein — gleiche
`HistoricalOHLCVProvider`-Schnittstelle wie Binance Vision, gleicher Repo-Schreibpfad, gleiche
`validate_dataset`-Gate. Einziger Unterschied: Aggregation Tick→Bar (Mid) statt fertige Klines;
der mittlere/max. Spread je Bar wird zusätzlich als `BarSpread` bereitgestellt.

## 6. Sicherheit

* Keine Order, keine Trading-/Withdraw-Rechte, `orders_sent == 0` asserted.
* Alle Broker-Credentials nur über ENV (`CTRADER_*`, `MT5_*`) — **nichts im Repo**.
* Keyless-Quellen (Bybit public, Dukascopy, Yahoo) berühren keine Account-Daten.

## 7. Gates

`pytest` **912 passed** · `ruff` clean · `ruff format --check` clean · `mypy --strict` clean
(173 Dateien).
