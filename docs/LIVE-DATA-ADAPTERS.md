# Live-Daten-Adapter — Status & Vertrag

**Stand:** 2026-08-29 · **Noch KEINE Echtgeld-Verbindungen, keine Broker-Keys, keine Order-Ausführung.**

Alle Adapter sind **read-only Marktdaten**. Die Strategy Engine (`strategy/evaluate.py` …
`strategy/paper_live.py`) enthält **keinen** provider-spezifischen Code — Adapter füttern das
`MarketDataRepository` bzw. den `MarketContext`-Bau über die ABCs in `data/interfaces.py` und die
gemeinsame Basis `data/providers/adapter_base.py` (`LiveDataAdapter`, `CredentialSpec`,
`AdapterInfo`).

`CredentialSpec` deklariert **nur die Namen** der benötigten ENV-Variablen — **niemals Werte**.
Fehlen sie, meldet `status()` `UNAVAILABLE` und der Adapter ist inert (kein Fehler).

---

## 1. Status-Matrix

| Adapter | Modul | Asset-Klassen | Reife | Auth |
|---|---|---|---|---|
| **Binance Vision** (Bulk-Historie) | `data/providers/binance_vision.py` | Krypto Spot | **produktiv** (M5 BTC/ETH ingested) | keine |
| **Kraken** (REST public) | `data/providers/kraken.py` | Krypto Spot | REST-Teil vorhanden | keine |
| **Bybit** (REST public v5) | `data/providers/bybit_public.py` | Krypto Spot + Perp (Funding/OI) | REST-Teil vorhanden | keine |
| **Kraken/Bybit WebSocket** | `data/providers/exchange_ws.py` | Krypto (confirmed bars) | Stream-Gerüst vorhanden | keine |
| **MT5 / Pepperstone** | `data/providers/mt5.py` | FX-Majors, XAUUSD | **Vertrag** (Stub) | `MT5_LOGIN/PASSWORD/SERVER` (Demo/RO) |
| **Aktien / ETF** | `data/providers/equities.py` | Equity, ETF | **Vertrag** (Stub) + Corp-Action-Anpassung | provider-abhängig |
| **Economic Calendar / News** | `data/providers/news_calendar.py` | alle | **CSV-Impl** (PIT) + Live-Vertrag | Live-Quelle-abhängig |
| **Cross-Asset** (DXY/Yields/VIX) | `data/providers/cross_asset.py` | Kontext | **Builder** aus Proxy-OHLCV | keine (nutzt beliebigen OHLCV-Provider) |
| **TradingView** | — | — | **nur Interface-Platzhalter** | — |

„Reife": *produktiv* = echte Daten fließen · *REST-Teil* = ein Teil der Endpunkte implementiert ·
*Vertrag/Stub* = Signatur + Guards + Symbol-Mapping stehen, `fetch_*` wirft `NotImplementedError`
bis Phase 9+ · *Builder* = fertige reine Funktion.

---

## 2. Crypto — Kraken / Bybit (Phase 9+ Ausbau)

Vorbereiten (Interfaces existieren in `data/interfaces.py`):

| Datenart | Kraken | Bybit | ABC |
|---|---|---|---|
| Spot OHLCV M1/M5 | REST vorhanden | REST vorhanden | `AsyncOHLCVSource` / `HistoricalOHLCVProvider` |
| Perpetuals OHLCV | via Bybit linear | ✅ | dito |
| Order Book (L2) | zu ergänzen | zu ergänzen | `OrderbookProvider` |
| Public Trades | Kraken REST vorhanden | zu ergänzen | `HistoricalTradeProvider` / `AsyncTradeSource` |
| Funding | — | ✅ REST | `FundingProvider` / `AsyncFundingSource` |
| Open Interest | — | ✅ REST | `OpenInterestProvider` / `AsyncOpenInterestSource` |
| Liquidationen | falls verfügbar | falls verfügbar | neu (`LiquidationProvider`) — Phase 9+ |

Symbol-Mapping: `refdata/symbols.py::SymbolMapper` (kanonisch ↔ `XXBTZUSD`/`BTCUSDT`).
Routing/Fallback: `data/registry.py` + `data/router.py` (Capability + Health).

---

## 3. Forex / Gold — MT5 / Pepperstone

`data/providers/mt5.py::MT5Adapter`:

* **Voraussetzung:** Windows + `MetaTrader5`-Paket + laufendes MT5-Terminal mit **Demo/Read-only**-
  Login. `status()` → `UNAVAILABLE` sonst (getestet: Stub ist off-Windows inert).
* Vorbereitet: `to_broker_symbol()` (kanonisch → Broker-Symbol), `DEFAULT_SYMBOL_MAP` (EURUSD,
  GBPUSD, USDJPY, AUDUSD, USDCHF, USDCAD, XAUUSD), Timeframe-Map.
* Zu ergänzen (Phase 9+): `get_ohlcv` (M1/Tick → Bars), `get_quotes` (Bid/Ask), Spread-Serie,
  Trading-Session-Fenster je Symbol, Broker-Symbol-Suffixe (`.a`, `.raw`, …).
* **Keine Order-Funktionen hier** — Ausführung ist Phase 14, strikt getrennt.

---

## 4. Aktien / ETF

`data/providers/equities.py`:

* **Nicht** an einen Feed hartverdrahtet — `EquityDataAdapter` ist die Basis; ein konkreter
  Provider (Polygon / Tiingo / EODHD / IBKR …) wird später gewählt.
* **Fertig & getestet:** `adjust_for_actions(bars, actions, as_of=)` — rückwirkende Split-/
  Dividenden-Anpassung mit **Look-ahead-Schutz** (nur `ex_date <= as_of`).
* Prüfpunkte bei der Provider-Wahl: Echtzeit vs. verzögert, historische Tiefe, Corporate
  Actions / Splits / Dividenden, Market Hours, Pre-/Post-Market (RTH vs. erweitert).
* Market-Hours über `refdata/SessionSpec` + Trading-Kalender.

---

## 5. News / Macro — Point-in-Time zwingend

`data/providers/news_calendar.py`:

* **Fertig & getestet:** `CsvEconomicCalendar` — liest eine lokal gepflegte Kalender-CSV,
  strikt PIT (jede Zeile trägt `available_time`), erzeugt `core.models.NewsEvent`. **Nichts wird
  erfunden**; fehlt die Datei → `status()` `DEGRADED`, leeres Ergebnis.
* Zwei-Phasen-Modell: geplanter Termin (`actual=None`, `available_time` = Kalender-
  Veröffentlichung) → Pre-Positioning-Ban; Ist-Wert nach `scheduled_time` als neuer Event.
* `CANONICAL_EVENTS`: FOMC-Zins/Minutes, US-CPI/Core-CPI, US-PCE, US-NFP, US-Unemployment,
  US-GDP, US-Retail-Sales, ECB-Zins/Press-Conf.
* Speicher + PIT-Read: `MarketDataRepository.write_news/read_news` (bereits vorhanden).
* Live-Fetch (`EconomicCalendarAdapter.get_calendar`) = Phase 9+ — braucht eine offizielle Quelle
  mit **Zeitstempel-Historie** (kein Feed, der stillschweigend rückdatiert).
* **Keine Influencer-/Twitter-Feeds als Signal** — nur `NewsContext.risk_off_flag` als grober
  Marktzustand.

---

## 6. Cross-Asset

`data/providers/cross_asset.py::build_cross_asset_context(as_of=, dxy=, us10y_yield=, vix=)`:

* Reine Funktion über Proxy-OHLCV-Serien (die Quelle ist ein beliebiger `HistoricalOHLCVProvider`).
* **Füllt nur Felder, für die echte Bars übergeben wurden** — Rest bleibt `None`
  (`CrossAssetContext`). Die Confluence bewertet `None` als `UNAVAILABLE`.
* PIT: nur Bars mit `close_time <= as_of`. Getestet: ein VIX-Spike **nach** `as_of` ist unsichtbar.
* Aktuell abgeleitet: DXY-Trend (20-Bar-Change), 10y-Yield-Level, VIX-Level + `risk_off`
  (Level ≥ 25 **oder** ≥ 2σ-Spike).
* Gold/Dollar/Rates-Beziehungen: als weitere abgeleitete Felder ergänzbar, **nur** bei
  ausreichender Datenqualität (Nutzer-Vorgabe).

---

## 7. TradingView

**Constraint (unverändert):** keine Browser-Automation, keine inoffizielle API, keine Live-
Integration bis zur separaten Nutzer-Entscheidung (Phase 14). Aktuell: **kein Adapter**, nur der
dokumentierte Platzhalter. Höchstens manueller Chart-Ideen-Import als Text.

---

## 8. Multi-Agent-Einordnung

Die Adapter sind die Datenzuträger der **Agents** (Market / News / Cross-Asset). Die Agents
liefern **Informationen** an die zentrale Decision Engine; kein Agent löst eigenständig Trades
aus. Keine konkurrierenden Trading-Gehirne (siehe `FINAL_ARCHITECTURE_AUDIT.md`).

## 9. Audit-Checkliste (2026-08-29)

Systematisch geprüft gegen die Vorgabe. `✅` = vorhanden & getestet · `◑` = Gerüst/Vertrag steht
· `☐` = Phase 9+.

| Kriterium | Stand | Ort / Nachweis |
|---|---|---|
| **Interfaces** — eine ABC je Datenart, Adapter implementieren nur sie | ✅ | `data/interfaces.py` (`AsyncOHLCVSource`, `HistoricalOHLCVProvider`, `AsyncFundingSource`, `AsyncOpenInterestSource`, `AsyncTradeSource`, …); Strategy Engine importiert **keinen** Provider |
| **Datenmodelle** — ein kanonisches, frozen Modell, kein Roh-JSON nach oben | ✅ | `core/models.py` `OHLCV` / `Trade` / `Funding` / `NewsEvent` (Pydantic v2 frozen); Adapter mappen Roh-Rows → Modell an der Grenze |
| **Symbol-Mapping** — kanonisch ↔ Broker/Exchange, zentral | ✅ | `refdata/symbols.py::SymbolMapper`; `mt5.DEFAULT_SYMBOL_MAP`; `kraken._pair`; kein Symbol-String hartkodiert in der Engine |
| **Point-in-Time** — jede Leseoperation `as_of`, nichts aus der Zukunft | ✅ | `repository.read_ohlcv/read_news/read_funding/read_macro` filtern `available_time <= as_of`; News: neueste Revision ≤ `as_of`; getestet (`test_data_pipeline`, `test_parity`) |
| **Stale Data** — Alter der letzten Bar → Befund | ✅ | `quality.check_ohlcv_series` `STALE_DATA` (`stale_after_bars=2.5`, CRITICAL bei offenem Markt); `MtfParams.stale_factor` senkt `data_confidence` |
| **Datenqualität** — Lücken / Duplikate / OHLC-Invarianten / TZ-DST | ✅ | `quality.py`: `GAP_*`, `DUPLICATE_BAR`, `HIGH_LOW_INVERTED`, `CLOSE_OUT_OF_RANGE`, `NON_UTC`, `MISALIGNED_TIMESTAMP`; `blocks_trading` ⇒ Pipeline blockt sauber |
| **Reconnect** — WS-Abriss → Wiederverbindung + Gap-Backfill | ◑ | `exchange_ws.py`: capped exponential backoff (`max_reconnects=5`, `backoff_base_s=0.5`, cap 30 s), `reconnects`-Zähler; Caller backfillt Lücken über REST |
| **Fehlerbehandlung** — kein Absturz, definierter Fehlerzustand | ✅ | `net/client.py::NetError` / `CircuitOpen`; Adapter `status()` → `DEGRADED`/`UNAVAILABLE`; `HealthTracker` (`data/health.py`) |
| **Rate Limits** — Token-Bucket je Client, konfiguriert je Exchange | ✅ | `net/ratelimit.py::TokenBucket`; Kraken `rate_per_sec=1.0`, Bybit `5.0`; Retry-Set `(429,500,502,503,504)` mit Backoff+Jitter |
| **WebSocket-Lifecycle** — sauberes Öffnen/Schließen, kein Leak | ◑ | `exchange_ws.py` async-context; `aclose()` an allen REST-Adaptern; nur „confirmed" Bars werden emittiert |
| **Credentials nur über ENV** — nie Werte im Repo | ✅ | `adapter_base.CredentialSpec.env_vars` deklariert **nur Namen**; `os.environ.get` an genau einer Stelle; `present()/missing()` steuern `status()` |
| **Keine Secrets im Repository** | ✅ | grep über `data/providers/` findet keine Keys/Tokens/Passwörter; `.env.example` nur Platzhalter; alle produktiven Adapter (Binance Vision / Kraken / Bybit public) brauchen **keine** Auth |
| **Keine Fake-Live-Daten** | ✅ | Stubs werfen `NotImplementedError` statt zu erfinden; `MockMarketDataProvider` ist **nur** in Tests importiert, nie im Adapter-/Engine-Pfad |

**Ergebnis:** Der Vertrag steht vollständig; produktiv fließen nur echte Bulk-/Public-Daten.
Die `◑`-Punkte (WS-Reife, MT5/Equity/News-Live-Fetch) sind bewusst Phase 9+ und blockieren die
Strategie-Arbeit nicht (Backtest läuft auf `data/repository_real/`).

## 10. Offene Punkte

- Kraken/Bybit: Order Book + Liquidationen + Bybit-Trades ergänzen.
- MT5: `get_ohlcv`/`get_quotes` gegen ein reales Demo-Terminal implementieren.
- Aktien-Provider auswählen und `EquityDataAdapter` konkretisieren.
- Offiziellen Economic-Calendar-Feed mit Zeitstempel-Historie anbinden.
- `data/registry.yaml` um die neuen Adapter erweitern (Capability-Deklaration).
- WS-Lifecycle: Heartbeat/Ping-Timeout + Resubscribe-nach-Reconnect explizit testen.
