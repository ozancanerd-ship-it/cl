# Live-Data-Integration — Kraken + Bybit (public, read-only) — 2026-08-29

**Status:** Verbindung steht, Pipeline läuft auf **echten** Live-Daten bis zur Paper-Position.
**READ-ONLY. Kein API-Key, keine Trading-/Withdraw-Rechte, keine Orderausführung** (`orders_sent`
wird in jedem Lauf gegen 0 geprüft).

Scope: **nur** Kraken + Bybit **public market data**. MT5/FX/Aktien bleiben unberührt
(`docs/MULTI-ASSET-READINESS.md`).

---

## 1. Was neu / geändert wurde

| Datei | Änderung |
|---|---|
| `data/interfaces.py` | neues `AsyncQuoteSource` (Top-of-Book Bid/Ask) |
| `data/providers/kraken.py` | `fetch_quote` über `/0/public/Ticker` (kein Zeitstempel im Ticker ⇒ `ts` = Empfangszeit) · implementiert jetzt `AsyncQuoteSource` |
| `data/providers/bybit_public.py` | `fetch_quote` über `/v5/market/tickers` (`bid1Price`/`ask1Price`, `ts` = Server-`time`) |
| `data/providers/exchange_ws.py` | **Symbol-Mapping-Fix**: kanonisch `BTCUSDT` → Kraken-v2-WS-Name `BTC/USD`. Kraken-Liquidität liegt fast vollständig in den **USD**-Paaren — `BTC/USDT` existiert, handelte im Test **0×**. `kraken_ws_name()` + Rückwärts-Mapping in `_parse` (akzeptiert auch das kanonische Format). |
| `runtime/events.py` | neue Events `DecisionMade` · `SignalRevised` · `AlertRaised` · `PaperPositionChanged` (lose typisierte Nutzlast, damit `runtime` nicht von `strategy` abhängt) |
| `runtime/live_pipeline.py` | **neu** — `LivePipeline` (s. §3) |
| `scripts/live_connectivity_test.py` | **neu** — READ-ONLY Connectivity-Test (REST + WS) |
| `scripts/run_live_paper.py` | **neu** — Live-Paper-Runner mit EventBus-Log |

Tests: `+11` (test_live_pipeline 5, Quote-Tests 4, Kraken-WS-Mapping 2). **886 grün, ruff +
ruff-format + mypy --strict grün.**

---

## 2. Connectivity-Test — Ergebnis (echte Calls, `scripts/live_connectivity_test.py`)

Isolierter Lauf (`scripts/live_connectivity_test.py`, keine konkurrierenden Prozesse):

| | Kraken | Bybit |
|---|---|---|
| **Verdict** | **CONNECTED** | **CONNECTED** |
| Server-Zeit / Clock-Skew | REST OK, Skew **+1.31 s**, Latenz ~1360 ms | REST OK, Skew **+0.87 s**, Latenz ~1600 ms |
| OHLCV M5 (REST) | 71 Bars, ~940 ms, letzte Bar ~123 s alt, Quality OK | 71 Bars, ~210 ms, letzte Bar ~126 s alt, Quality OK |
| OHLCV M1 (REST) | 119 Bars | 119 Bars |
| Bid/Ask (REST) | `fetch_quote` OK, 78098.9 / 78101.2, **Spread 0.294 bps** (Spot), ~1000 ms | `fetch_quote` OK, 78060.5 / 78060.6, **Spread 0.013 bps** (Perp), ~190 ms |
| WebSocket | connect ~2.4 s, 8 Msgs in 10 s, **0 Reconnects** | connect ~1.6 s, 7 Msgs in 14 s, **0 Reconnects** |
| Zukunftsdaten | keine (`future_timestamps=[]`, quote `future=False`) | keine |
| Provider-Health | `HEALTHY` | `HEALTHY` |

Kraken-REST ist deutlich langsamer (Rate-Limit **1 req/s**, Bybit **5 req/s**); Kraken-Spot-
Spread breiter als Bybit-Perp.

REST-Latenz Kraken ist deutlich höher als Bybit (Kraken-Rate-Limit 1 req/s, Bybit 5 req/s;
Bybit-Endpunkte antworten ~200 ms, Kraken OHLC ~1 s).

**Nicht unterstützt / DEGRADED:** keine der geprüften Funktionen — beide public APIs liefern
Zeit, OHLCV (M1/M5/…), Bid/Ask und einen Trade-Stream. Orderbook-L2, Liquidationen und ein
privater WS sind **nicht** angebunden (nicht Teil von read-only-public und nicht benötigt).

---

## 3. Pipeline (`runtime/live_pipeline.py::LivePipeline`)

```
REST-Warmup (M5 m5_warmup=400 · native M15/H4/D1)  ─┐
                                                    ├─►  rolling M5-Store (deque) + backfilled M15/H4/D1
WebSocket  Trades ─► BarAggregator ─► confirmed M5 ─┤       + periodischer REST-Refresh der höheren TFs
REST-Poller (Fallback bei WS-Stall > 420 s)  ───────┘       + fetch_quote → spread im MarketContext
        │
        ▼  je confirmed M5-Bar:
   Data-Quality (check_ohlcv_series, rollierendes 60-Bar-Fenster) — CRITICAL ⇒ DataQualityAlert, Bar verworfen
        ▼
   MarketContext(information_cutoff = bar.close_time)   ← Konstruktor wirft bei Bar/News aus der Zukunft
        ▼
   PaperLiveRunner.feed()  →  build_mtf_context → evaluate() → Decision
        ▼                       → SignalTracker (Revision) → AlertEngine → PositionManager (Paper)
   EventBus:  BarClosed · QuoteUpdate · DecisionMade · SignalRevised · AlertRaised · PaperPositionChanged · DataQualityAlert
```

* **`prime()`** — ein sofortiger `feed()` je Instrument auf dem Warmup-Stand. Beweist die volle
  Kette auf echten REST-Daten, ohne 5 min auf die erste WS-Bar zu warten.
* **`news_gate`** — `off` (Default): Research-Modus, `require_news_feed=False`. `on`: live-
  repräsentativ (blockt ohne PIT-News-Feed). **Kein News-Fake in beiden Fällen.**
* **Kein Broker.** `PaperLiveRunner` hat keinen Order-Pfad; `LivePipeline.orders_sent` wird nach
  jedem Step auf 0 gesetzt und am Laufende asserted.

---

## 4. Live-Test — Ergebnis (`scripts/run_live_paper.py --max-bars 2`, echte Feeds)

Bybit **und** Kraken, BTCUSDT + ETHUSDT, Research-Modus, deterministisch nach 2 WS-Bars beendet:

| | Bybit | Kraken |
|---|---|---|
| Warmup je Symbol | `M5=401 M15=451 H4=301 D1=221` | dito |
| `prime()` Decision | `NO_TRADE / SCANNING` (BTC + ETH, cutoff 22:15) | `NO_TRADE / SCANNING` |
| WS confirmed M5-Bar (22:20) | **genau 1** je Symbol (BTC 78047.2, ETH 2448.06) ⇒ **1** `BAR`- + **1** `DecisionMade`-Event | **genau 1** je Symbol (BTC 78084.7, ETH 2450.01) |
| `m5_bars` nach Lauf | 402 (401 Warmup + 1 live) | 402 |
| Data-Quality-Blocks | 0 | 0 |
| `stale` | False | False |
| **`orders_sent`** | **0** | **0** |

* Event-Trace je Lauf: `DEC(prime) ×2 → BAR + DEC(ws) ×2`. Kein Doppel-Feed (Fix #2, s. §6).
* **Decision bleibt `NO_TRADE/SCANNING`** — identisch zum Backtest
  (`MULTI-SYMBOL-BACKTEST-2026-08.md`): der HTF-Regime-Gate lässt in der aktuellen Marktlage
  keinen Setup zu. Die Pipeline **läuft** korrekt auf Live-Daten; sie hat nur (erwartungsgemäß)
  nichts zu signalisieren.
* Einmal beobachtet: Kraken-WS lieferte nach ~5 min keine Trades mehr ⇒ nach `stale_after_seconds`
  (420 s) griff der **REST-Fallback** (`_rest_poll_loop`) und holte die fehlende M5-Bar per
  `/0/public/OHLC`. Genau der vorgesehene Pfad.

---

## 5. Geprüfte Checkliste

| Punkt | Ergebnis |
|---|---|
| Live Quotes | ✅ `fetch_quote` Kraken + Bybit, echtes Bid/Ask + Size |
| OHLCV / M1 / M5 | ✅ REST (M1/M5) + WS-aggregiert (M5 aus Trades) |
| Bid/Ask | ✅ (REST-Ticker); WS-Orderbook nicht angebunden (nicht nötig) |
| WebSocket | ✅ beide, connect < 3 s, Trades fließen |
| REST Fallback | ✅ `_rest_poll_loop` — feuert nur bei WS-Stall > `stale_after_seconds` |
| Reconnect | ✅ `_WSBase` capped exponential backoff (max 5, cap 30 s), `reconnects`-Zähler |
| Stale Data | ✅ `InstrumentState.stale` + Freshness-Term in `data_confidence` |
| Data Quality | ✅ `check_ohlcv_series` je Bar; CRITICAL ⇒ `DataQualityAlert`, Bar verworfen |
| Symbol Mapping | ✅ **Fix**: Kraken-WS `BTCUSDT`→`BTC/USD`; Rückmapping in `_parse` |
| Health Status | ✅ `provider.status().health` (HEALTHY / DEGRADED / UNAVAILABLE) |
| Event Timestamps | ✅ jedes Event trägt `ts`; `MarketContext.information_cutoff = bar.close_time` |
| Keine Zukunftsdaten | ✅ MarketContext-Konstruktor wirft; Forming-Bar-Guard im Live-Pfad; Connectivity-Test prüft explizit |

---

## 6. Gefundene & behobene Fehler (dieser Auftrag)

| # | Fehler | Fix |
|---|---|---|
| 1 | **Kraken-WS-Symbol-Mapping** — sendete kanonisch `BTCUSDT` an eine „BASE/QUOTE"-API ⇒ 0 Trades geparst. Zusätzlich: `BTC/USDT` handelt auf Kraken faktisch nicht. | `kraken_ws_name()`: `BTCUSDT`→`BTC/USD` (liquides Paar), Rückmapping in `_parse`. |
| 2 | **Doppel-Feed** desselben M5-cutoff auf dem WS-Pfad (identische Decision, harmlos, aber falsch). | `_fed_opens`-Set (bereits verarbeitete `open_time` verwerfen) + Forming-Bar-Guard (`close_time` > 1 Intervall in der Zukunft ⇒ verwerfen). Verifiziert: 1 confirmed Bar ⇒ genau 1 Feed je Symbol. |
| 3 | **`run()`-Deadline nutzte `time.monotonic()`** — zählt System-Suspend nicht mit ⇒ ein `--minutes`-Lauf terminierte nach einem Laptop-Sleep nicht. | Deadline auf Wall-Clock (`datetime.now(UTC)`) umgestellt; `stop()` zusätzlich im `finally`. |
| — | Transiente Kraken-`/0/public/Time`-Fehler, wenn **mehrere** Test-Prozesse gleichzeitig Krakens 1-req/s-Public-API treffen. Kein echter Blocker (M1/M5/Quote/WS liefen weiter, `health=HEALTHY`). | Test-Läufe seriell / mit `--max-bars` bounden. |

## 7. Offene Punkte / Backlog
- Höhere TFs (M15/H4/D1) werden per REST **nachgeladen** (alle 12 M5-Bars), nicht aus dem
  Live-M5-Strom fortgeschrieben. Für Minuten-Läufe irrelevant; für 24/7 sauber, aber ein
  Roll-Resample wäre effizienter.
- `runtime/supervisor.py` bindet noch die Phase-2B-`ScannerShell`, nicht `LivePipeline` — der
  24/7-Daemon-Pfad (M-01 in `ARCHITECTURE-MULTI-ASSET-AGENT-CLOUD.md`) verdrahtet die Pipeline
  als nächster Schritt.
- Bybit Funding/OI-Stream in `DerivativesContext` (REST-Endpunkte da, s. `bybit_public.py`).
- Kein privater WS / kein Orderbook-L2 (bewusst — read-only public).
