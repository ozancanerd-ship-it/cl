# Binance-Integration — READ-ONLY Marktdaten + Account (2026-08-30)

## Ausgangslage im Projekt

Datenquellen-Architektur: async ABCs (`data/interfaces.py` — `AsyncOHLCVSource`/`AsyncQuoteSource`/
`AsyncFundingSource`/`AsyncOpenInterestSource`), `LiveDataAdapter` + `CredentialSpec` für
credential-basierte Adapter, `security.secrets` (ENV → Keychain, Redaction), `exchange_ws.py`
(`_WSBase` + Exchange-WS-Sources), `RestMarketData`-Protocol + `build_rest_provider()` als
Pipeline-Einstieg, `ProviderRouter`/`Registry` für die Quellenauswahl.

**Vorhandener Binance-Code:** `data/providers/binance_vision.py` — das ist der **Bulk-Datei-
Import** (`data.binance.vision`, SHA-256-verifiziert, tiefe Backtest-Historie). **Kein** REST-/
WS-API-Adapter, **kein** Account-Adapter. → Beide neu gebaut, `binance_vision` bleibt für die
Historie.

## Neu gebaut

| Datei | Inhalt |
|---|---|
| `data/providers/binance.py` — `BinancePublicDataProvider` | **kein Key.** `market="spot"` (`api.binance.com`) oder `"futures_usdm"` (`fapi.binance.com`). `fetch_ohlcv`, `fetch_quote` (bookTicker), `fetch_ticker_24h`, `fetch_mark_price`, `fetch_funding`, `fetch_open_interest`, `list_symbols`/`has_symbol`, `server_time`. Futures-only-Methoden werfen auf Spot einen klaren Fehler. |
| `data/providers/binance_account.py` — `BinanceAccountAdapter` | **READ-ONLY**, HMAC-SHA256 (Signierer gegen Binances offiziellen Testvektor geprüft). `get_api_permissions` (`/sapi/v1/account/apiRestrictions`), `get_spot_balances`, `get_open_orders`, `server_time`, `assert_read_only`. Pfad-Whitelist, **kein `submit`/`cancel`**, kein `BrokerAdapter`. Ohne ENV → `UNAVAILABLE`. |
| `exchange_ws.py` — `BinanceWSSource` | `wss://fstream.binance.com/ws`, `@aggTrade` → `BarAggregator` → confirmed Bars. |
| `runtime/live_pipeline.py` | `build_rest_provider("binance" | "binance_futures" | "binance_spot")`, `_new_ws` + `_maybe_refresh_derivatives` kennen jetzt Binance. |
| `refdata/seed.py` | `XAUUSDT`-Instrument (Binance, GOLD, Perp, Kalender `xau_spot`) + Symbol-Mappings (`XAUUSDT`, `XAUUSD→binance XAUUSDT`, `XAUUSD→binance_spot PAXGUSDT`). |
| `scripts/binance_market_test.py` | public Marktdaten-Test + Pipeline-Durchstich (kein Key). |
| `scripts/binance_account_test.py` | READ-ONLY Account-/Verbindungstest. |
| `tests/unit/test_binance.py` (7) · `tests/unit/test_binance_account.py` (10) | +17 Tests → 969 grün. |

## XAUUSDT auf Binance — geklärt

| Frage | Antwort |
|---|---|
| Spot | **XAUUSDT existiert NICHT** (`-1121 Invalid symbol`). Spot hat `PAXGUSDT` (PAX Gold). |
| **USD-M-Futures** | **XAUUSDT existiert** — `TRADIFI_PERPETUAL`, aktiv gehandelt (24h-Vol ~42 k), Quote USDT. Ebenso `PAXGUSDT`, `XAUTUSDT`. |
| Live Bid/Ask | ✅ `bookTicker` — z. B. 4481.93 / 4481.94, Spread 0.01 |
| Historische Candles | ✅ M1/M5/M15/H1/H4/D1 (`/fapi/v1/klines`, bis 1500 Bars/Request) |
| Mark Price | ✅ `/fapi/v1/premiumIndex` |
| Funding | ✅ `/fapi/v1/fundingRate` — Endpunkt liefert Daten; **Rate ist 0.0** (TradiFi-Perp-Klasse, nächster Funding-Zeitpunkt trotzdem gesetzt) |
| Open Interest | ✅ `/fapi/v1/openInterest` + `/futures/data/openInterestHist` (~90 k Kontrakte) |

## Verifizierter Live-Durchstich (2026-08-30, Wochenende)

```
Binance → XAUUSDT → BinancePublicDataProvider → LivePipeline
  warmup: M5=401  M15=451  H4=301  D1=221
  MarketContext → evaluate() → decision = no_trade / scanning
  reason_codes = [regime_vol_extreme, weekend]
  DerivativesContext: funding_rate=0.0, open_interest=90112.9, as_of=2026-08-30T18:00Z
  orders_sent = 0
```

NO_TRADE ist korrekt: Wochenende (Session-Filter für Gold) + Vol-Regime EXTREME. Die **zentrale**
`strategy.evaluate`-Pipeline lief unverändert auf echten Binance-XAUUSDT-Daten.

## ENV-Variablen (für den Account-Test)

```
BINANCE_API_KEY=<dein Key>
BINANCE_API_SECRET=<dein Secret>
```
Ablage: `.env` (chmod 600, in `.gitignore`) oder macOS-Keychain
(`security add-generic-password -s trading-agent -a BINANCE_API_KEY -w`).
**Öffentliche Marktdaten inkl. XAUUSDT brauchen keinen Key.**

Key-Rechte: **nur „Enable Reading"**. NICHT: Spot/Margin Trading, Futures, Withdrawals,
Universal/Internal Transfer.

## Nächste Schritte für die Gold-Analyse

Siehe Bericht-Ende der Session.
