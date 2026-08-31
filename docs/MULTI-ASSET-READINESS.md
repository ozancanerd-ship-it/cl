# Multi-Asset-Readiness — Stocks/ETF · Gold · Forex

**Stand:** 2026-08-29 · Ergänzung zu `LIVE-DATA-ADAPTERS.md` (Daten-Adapter) und
`ARCHITECTURE-MULTI-ASSET-AGENT-CLOUD.md` (Ziel-Architektur).

> **Leitsatz (Nutzer-Vorgabe):** *Nicht für jede Assetklasse eine getrennte Strategy bauen.*
> **Ein** zentraler Decision Engine (`strategy/evaluate.py`). Asset-Unterschiede ausschließlich
> über **Asset Metadata · Session Rules · Provider Adapter · Costs · Market Hours · Instrument
> Specs** — nie über Verzweigungen in der Strategie-Logik.

Diese Datei ist die verbindliche Checkliste, um eine Asset-Klasse in den **vollen
Strategiepfad** (nicht nur Daten-Ingest) zu heben. Sie ändert **kein** Laufzeitverhalten für
Crypto.

---

## 0. Was schon steht (nicht neu bauen)

| Baustein | Ort | Status |
|---|---|---|
| Asset-Klassen-Enum | `core/enums.py::AssetClass` | CRYPTO/ALTCOIN/GOLD/FOREX/EQUITY/ETF |
| Instrument-Master (tick/lot/precision/fees/margin/listed/delisted) | `refdata/models.py::Instrument` + `refdata/seed.py` | XAUUSD, EURUSD, AAPL, SPY **architektonisch angelegt** |
| PIT-Universe / Survivorship | `Instrument.is_tradeable_at(moment)` | ✅ (listed_at/delisted_at/is_active) |
| Handelskalender (Feiertage, Half-Days, weekend_gap, RTH) | `refdata/models.py::TradingCalendarSpec` + `refdata/calendar.py` | `us_equity`, `xau_spot`, `fx_weekday_24h` geseedet |
| Sessions (DST-sicher, Börsenlokalzeit → UTC) | `refdata/models.py::SessionSpec` + `analysis/sessions.py` | ASIA/LONDON/NEW_YORK + Overlap |
| Session-Entry-Gate §18 | `analysis/sessions.py::session_filter` | ✅ (greift für Nicht-24/7) |
| Corporate Actions (PIT-Backadjustment, Symbol-Ketten, Delisting) | `refdata/corporate_actions.py` | ✅ **neu 2026-08-29** (`adjust_ohlcv`, `CorporateActionBook`, `resolve_symbol_at`) |
| Split/Dividenden auf Provider-Ebene | `data/providers/equities.py::adjust_for_actions` | ✅ (leichtgewichtig, `ex_date`-basiert) |
| FX/Gold-Adapter-Vertrag | `data/providers/mt5.py::MT5Adapter` | Stub, off-Windows inert |
| Aktien-Adapter-Vertrag | `data/providers/equities.py::EquityDataAdapter` | Stub, Provider offen |
| Cost-Profile je Asset-Klasse | `strategy/cost_profiles.py` | ✅ (zero / exchange_schedule / estimate_conservative / measured) |
| News-Relevanz je Asset-Klasse | `strategy/news_relevance.py` + `analysis/news.py` | ✅ **neu 2026-08-29** |
| Makro-Regime | `analysis/macro.py` | ✅ **neu 2026-08-29** |

**Fazit:** die *Struktur* ist vorhanden. Was fehlt, ist (a) **Daten** (echte historische PIT-
Serien je Asset-Klasse), (b) die **Verdrahtung von `asset_class`** durch den Backtest-/Live-Pfad,
(c) **Session-/Kalender-Feinheiten** (Pre/Post-Market), (d) die **Provider-Wahl** für Aktien.

---

## 1. `asset_class` durch den Strategiepfad ziehen (Kern-Task, asset-agnostisch)

Heute ist der Pfad crypto-hart: `EvaluateParams.asset_class = CRYPTO` (Default),
`MarketContextAssembler` kennt die Asset-Klasse nicht (jetzt: neues Feld `asset_class`, Default
CRYPTO), `BacktestConfig` hat kein `asset_class`.

**Soll:** die Asset-Klasse kommt **aus dem Instrument-Master** und wird an genau drei Stellen
konsumiert:

1. `EvaluateParams.asset_class` → treibt `market_is_24_7`, Session-Gate, Vol-Perzentile
   (`RegimeParams` hat bereits `asset_class`-abhängige `extreme_atr_ratio`).
2. `AssemblerConfig.asset_class` → News-Relevanz (`analysis/news.build_news_context`), Kalender.
3. `strategy/cost_profiles.for_asset_class(...)` → Fees/Spread/Slippage.

**Konkreter Schritt (klein, testbar, kein Verhalten für Crypto geändert):**

```
BacktestConfig(..., asset_class: AssetClass = AssetClass.CRYPTO)
  └─ _run_instrument: AssemblerConfig(asset_class=cfg.asset_class, ...)
  └─ EngineParams(evaluate=EvaluateParams(asset_class=cfg.asset_class, ...))
```

Optional Helfer: `refdata/instruments.py::InstrumentMaster.asset_class_of(symbol)` (existiert
implizit über `.get(symbol).asset_class`). Für den Backtest reicht ein Argument.

---

## 2. Aktien / ETF

### 2.1 Datenprovider-Bewertung (die einzige echte offene Entscheidung)

Anforderungen (Nutzer-Vorgabe): Börsenzeiten, Pre-/After-Hours, Splits, Dividenden, Corporate
Actions, Symbol-Changes, **Survivorship-Bias-frei**, **Point-in-Time-Universe**, Delisted
Securities, Zeitzonen. Intraday (M1/M5) für ≥ 2 Jahre.

| Provider | PIT-Fundamentals / CA | Intraday-Tiefe | Delisted / PIT-Universe | Pre/Post | Kosten | Bewertung für uns |
|---|---|---|---|---|---|---|
| **Polygon.io** | Splits + Dividenden API, Ticker-Events (Symbol-Change) | M1 ab 2003 (Aktien) | delisted Tickers via `/v3/reference/tickers?active=false`; **kein** echtes PIT-Universe-Snapshot | ja (Trades mit Bedingungs-Codes) | ~$29–199/mo | **Erste Wahl.** Gute CA-API, tiefe Intraday-Historie, ein Vendor für US-Aktien+ETF. PIT-Universe muss aus `listed/delisted`-Daten selbst rekonstruiert werden (machbar mit `Instrument.is_tradeable_at`). |
| **Databento** | Referenz + CA (OPRA/XNAS) | Tick/M1, sauber normalisiert | ja, historisch akkurat | ja | Pay-as-you-go | **Stärkste Datenqualität**, teurer, mehr Integrationsaufwand. Kandidat, wenn Polygon-CA-Lücken stören. |
| **Tiingo** | EOD-Splits/Divs solide, Intraday begrenzt | IEX-Intraday ab 2017, **nur IEX-Volumen** | schwach | eingeschränkt | ~$10–50/mo | Nur EOD-tauglich. Intraday-Volumen nicht repräsentativ ⇒ **ungeeignet** für M5-SMC. |
| **EODHD** | breite Abdeckung, CA vorhanden | M1 ~1–2 Jahre | mittelmäßig | teilweise | günstig | Backup / Nicht-US-Märkte. CA-Qualität schwankt. |
| **Alpaca** | Splits/Divs ok | M1 ab 2016 (SIP) | schwach | ja (SIP) | Marktdaten im Broker-Plan | Attraktiv, **wenn** wir später ohnehin Alpaca-Paper nutzen (ein Vendor Data+Paper). PIT-Universe schwach. |
| **IBKR** | über TWS-API, mühsam | ja, aber Ratelimit-hart | — | ja | Konto nötig | Nur wenn IBKR-Broker-Anbindung sowieso kommt. Nicht für Bulk-Backtest-Ingest. |
| Yahoo/Stooq (frei) | Splits ok, Divs lückenhaft, **rückdatiert** | EOD / grob | nein (survivorship-behaftet) | nein | frei | **Nur** für Cross-Asset-Proxys (DXY/VIX), **nicht** für handelbare Aktien. |

**Empfehlung:** **Polygon.io** als primärer Aktien-/ETF-Provider; `refdata/corporate_actions.py`
konsumiert Polygon-Splits/Dividenden/Ticker-Events; PIT-Universe aus Polygons `list_tickers`
(active + delisted) + `ticker_events` in den `Instrument`-Master materialisieren. **Databento**
als dokumentierte Upgrade-Option. Keine Anbindung ohne echten Bedarf — zuerst Crypto-Analyse
abschließen.

### 2.2 Adapter-Vertrag (steht, `data/providers/equities.py`)

Ergänzen bei Provider-Wahl:
- `EquityDataAdapter.fetch_ohlcv(symbol, tf, start, end, session="rth"|"ext")` → `list[OHLCV]`
  (RTH-only default; erweitertes Fenster explizit).
- `fetch_corporate_actions(symbol, start, end)` → `list[refdata.models.CorporateAction]`
  (mit `available_time` = Ankündigungszeit, **nicht** `ex_date`).
- `fetch_ticker_events(symbol)` → Symbol-Change-Historie → `CorporateAction(SYMBOL_CHANGE)`.
- `list_universe(as_of)` → aktive + delisted Ticker zum Stichtag → `Instrument`-Master.

### 2.3 Sessions / Market Hours (Feinschliff nötig)

`us_equity`-Kalender hat `regular_open=09:30`, `regular_close=16:00` (America/New_York),
3 Feiertage geseedet. **Lücken:**
- Vollständige NYSE/Nasdaq-Feiertagsliste + Half-Days (Black Friday, Heiligabend 13:00) →
  `TradingCalendarSpec.holidays` / `half_days` befüllen (Quelle: Polygon `/v1/marketstatus/upcoming`
  + statische Historie).
- **Pre-Market** (04:00–09:30) / **After-Hours** (16:00–20:00): neues optionales Feld
  `TradingCalendarSpec.premarket_open` / `postmarket_close` **oder** zwei zusätzliche
  `SessionSpec` (`EQUITY_PREMARKET`, `EQUITY_AFTERHOURS`). Empfehlung: `SessionSpec`, weil
  `analysis/sessions.py` schon Session-Fenster auflöst. Entry-Policy: **RTH-only** für den
  ersten Aktien-Lauf (`SessionFilterParams.allowed`), erweiterte Sessions nur als Kontext
  (dünne Liquidität, breitere Spreads).
- Opening-Auktion / Closing-Auktion: die erste/letzte M5-Bar trägt Auktionsvolumen →
  `SessionFilterParams.avoid_first_min` (bereits vorhanden, Default 15) auf Aktien anwenden.

### 2.4 Corporate Actions im Backtest (jetzt verdrahtbar)

`refdata/corporate_actions.adjust_ohlcv(bars, actions, as_of=cutoff, adjust_dividends=?)`:
- **Split-adjusted** (Default): Kontinuität der Preisreihe, keine künstlichen Gaps/Swings.
- **Total-Return** (`adjust_dividends=True`): nur wenn wir Dividenden-Drift bewerten wollen;
  für SMC-Struktur eher **aus** (Dividenden-Gaps sind reale Liquiditätsereignisse — offen, ob
  wir sie glätten oder als Events behandeln). **Entscheidung offen → Backlog.**
- PIT: `as_of = information_cutoff` ⇒ eine erst später angekündigte Maßnahme ist im Replay
  unsichtbar. `AdjustmentResult.provenance` (`raw`/`split_adjusted`/`total_return`) gehört ins
  `RunManifest`.
- Verdrahtungspunkt: `MarketContextAssembler.bind()` lädt CA-Book je Symbol; `at(cutoff)` ruft
  `adjust_ohlcv(..., as_of=cutoff)` vor dem `MarketContext`-Bau. **Nur** für EQUITY/ETF.

### 2.5 Survivorship / PIT-Universe

- Scanner (Phase 5) iteriert nie über eine „heutige" Symbolliste, sondern über
  `InstrumentMaster` gefiltert mit `is_tradeable_at(cutoff)`.
- Delisted Namen bleiben im Master (`is_active=False` + `delisted_at`), damit historische
  Backtests sie sehen und der Scanner sie zum Delisting-Datum fallen lässt.
- `CorporateActionBook.is_delisted(symbol, at)` als zusätzliche Laufzeitprüfung.

---

## 3. Gold / XAUUSD

**Datenlage:** kein echter XAUUSD-Ingest vorhanden (`data/repository_real/` = nur Crypto).
MT5/Pepperstone braucht Windows → Adapter bleibt Vertrag, **nichts simulieren**.

### 3.1 Provider-Optionen für die **Historie** (backtestfähig, ohne Windows)

| Quelle | XAUUSD M5-Tiefe | Bid/Ask / Spread | PIT | Hinweis |
|---|---|---|---|---|
| **Dukascopy** (frei, Bulk) | Tick ab ~2003 | ✅ echtes Bid/Ask | ✅ (Tick-Zeitstempel) | **Erste Wahl** für Gold-/FX-Historie. Bulk-Download wie Binance-Vision, kein Konto. Spot-CFD-Preise (nicht Futures). |
| **HistData.com** (frei) | M1 ab 2000 | nur Bid (Ask ≈ Bid+fix) | ✅ | Backup, gröber, kein echtes Ask. |
| **MT5/Pepperstone** (live) | Broker-abhängig | ✅ | Live-Feed, kein Bulk-Backfill | Nur Paper/Read-only, Phase 15. |
| Polygon FX/Metals | M1, ok | mid, kein echtes Ask | ✅ | wenn wir Polygon ohnehin für Aktien nehmen. |

**Empfehlung:** **Dukascopy-Bulk** analog `binance_vision.py` → neuer
`data/providers/dukascopy.py` (Tick → M5/M15/H4/D1 Resample, echtes Bid/Ask → Spread-Serie).
Gilt für Gold **und** FX (§4).

### 3.2 Sessions / Kalender

- Kalender `xau_spot` (weekend_gap, UTC) vorhanden. Ergänzen: Metall-Feiertage
  (US-Feiertage dünn, kein voller Handelsstopp außer Weihnachten/Neujahr), tägliche
  CME-Wartungspause (~22:00–23:00 UTC bzw. 17:00–18:00 ET) als `half_days`-analoges Fenster
  oder `SessionSpec`-Lücke.
- Sessions: **Asia (Sydney/Tokyo) · London · New York** — für Gold ist der London-Fix
  (10:30 / 15:00 London) ein Liquiditätsanker → als zusätzliches `SessionSpec` (`LONDON_FIX_AM/PM`)
  ergänzbar; Entry-Gate erlaubt London + NY + Overlap (wie jetzt).
- `RegimeParams`: eigene Vol-Perzentile für GOLD (nicht die Crypto-`extreme_atr_ratio`).

### 3.3 Kosten

`cost_profiles.for_asset_class(GOLD)`: typ. Spread 15–30 ¢ (0.15–0.30 $) bei XAUUSD ≈
1.5–3 bps; Kommission 0 (im Spread) oder ~$3/Lot RAW-Konto; **kein Funding** (Spot-CFD hat
Swap/Rollover statt Funding — §4.3). Werte als `estimate_conservative` markieren bis Dukascopy-
Spread-Serie gemessen ist.

---

## 4. Forex

**Datenlage:** kein FX-Ingest. `fx_weekday_24h`-Kalender + `EURUSD`-Instrument geseedet.

### 4.1 Symbole (Start)

EURUSD, GBPUSD, USDJPY (Majors), dann AUDUSD, USDCHF, USDCAD, NZDUSD. Alle als
`AssetClass.FOREX`, `calendar_id="fx_weekday_24h"`.

### 4.2 Daten

**Dukascopy-Bulk** (§3.1) deckt alle Majors mit Tick + echtem Bid/Ask ab. Ein
`data/providers/dukascopy.py` bedient Gold **und** FX. Alternativ HistData (M1, nur Bid).

### 4.3 Instrument-Spezifika (Modell-Ergänzungen)

`refdata/models.py::Instrument` hat `tick_size`, `lot_size`, `contract_multiplier`,
`max_leverage`. **Fehlt für FX/CFD:**

| Größe | heute | Vorschlag |
|---|---|---|
| **Pip-Definition** | implizit `tick_size` | `pip_size` (z. B. 0.0001; JPY-Paare 0.01) — ableitbar aus `price_precision`, aber explizit sauberer |
| **Pip-Value** | — | dynamisch: `pip_value = pip_size * contract_multiplier * lot_size / quote_fx_rate` → Helfer in `risk/position_sizing.py` (schon lot-basiert; braucht Quote-FX für Cross-Paare) |
| **Swap / Rollover** | — | neues `SwapSpec(long_points_per_day, short_points_per_day, triple_day=WED)` am Instrument **oder** in `cost_profiles`; wirkt wie Funding (`strategy/costs.py::funding_cost_r` verallgemeinern zu `carry_cost_r`) |
| **Margin** | `max_leverage` + `margin_tiers` (Crypto-Perp-Stil) | FX: fixe `margin_rate` je Paar (z. B. 3.33 % = 30:1). `MarginTier` reicht (ein Tier). |
| **Lot-Konvention** | `lot_size` = Einheiten | Standard-Lot 100 000, Mini 10 000, Micro 1 000 — als `contract_multiplier` + `lot_size`; UI zeigt Lots, Engine rechnet Einheiten |

**Wichtig:** keine dieser Größen kommt in die Strategy Engine. Sie leben in `refdata` / `risk` /
`cost_profiles`. Die Engine sieht nur `MarketContext` (Preise) + `EvaluateParams`.

### 4.4 Sessions

`fx_weekday_24h`: Handel So 22:00 UTC → Fr 22:00 UTC. Sessions Asia/London/NY vorhanden.
Entry-Gate: London + NY + Overlap (bestehende Default-Policy passt für FX gut). Freitag-
Nachmittag-Puffer (`avoid_pre_weekend_min=60`) greift bereits.

### 4.5 Kosten

`cost_profiles.for_asset_class(FOREX)`: EURUSD Spread ~0.1–0.6 pip (0.1–0.6 bps),
RAW-Kommission ~$3.5/Lot/Seite ≈ 0.35 bps; Swap paarabhängig, Vorzeichen richtungsabhängig,
Mittwoch 3×. `estimate_conservative` bis Dukascopy-Spread gemessen.

---

## 5. Cross-Asset & Derivatives (Punkte 7 / 8 — Kurzstatus)

- **Cross-Asset** (`data/providers/cross_asset.py` + `analysis/macro.py`): Builder fertig,
  PIT-sauber, füllt nur echte Felder. Quelle DXY/US10Y/US02Y/VIX = FRED (`fred_alfred.py`,
  `CROSS_ASSET_SERIES`) — braucht `FRED_API_KEY` (frei). Ohne Key: `UNAVAILABLE`, kein Fake.
  **Kein harter Korrelations-Code** — nur `RegimeDirectional`-Trend + Level + `risk_off`-Flag,
  von Confluence als Evidence (`UNAVAILABLE` wenn leer) bewertet.
- **Derivatives** (`core/types.py::DerivativesContext`): Slots für Funding / OI / OI-Δ / Basis /
  CVD-Divergenz. Assembler füllt **Funding** bereits aus `repository.read_funding` (PIT), sonst
  leer. Bybit-REST liefert Funding + OI (`bybit_public.py`). Liquidationen / Orderbook / CVD =
  Phase 9+ (`LiquidationProvider` neu). **Nichts erfunden.**

---

## 6. Reihenfolge (empfohlen, nicht bindend)

1. Crypto-Multi-Symbol-Analyse abschließen (läuft).
2. `asset_class` durch `BacktestConfig` → Assembler → EvaluateParams ziehen (§1). Klein, testbar.
3. `data/providers/dukascopy.py` (Bulk Tick → OHLCV + Spread) für **Gold + FX** gemeinsam.
4. XAUUSD ingest + erster Gold-Backtest (research-Modus, News-Gate wie Crypto).
5. FX-Majors ingest + Backtest.
6. Polygon-Aktien-Adapter + PIT-Universe-Materialisierung + CA-Book-Verdrahtung im Assembler.
7. Aktien-Backtest (RTH-only).
8. Live: Kraken/Bybit public → dann MT5-Paper (Gold/FX) → dann Aktien-Paper.

**Keine Echtgeld-Execution in irgendeinem Schritt dieser Liste.**

---

## 7. Offene Entscheidungen (für den Nutzer)

- **Aktien-Provider:** Polygon.io (Empfehlung) vs. Databento (teurer, beste Qualität).
- **Dividenden im Backtest:** glätten (`total_return`) vs. als reale Gap-/Liquiditätsereignisse
  behandeln.
- **Gold-/FX-Historie:** Dukascopy-Bulk (Empfehlung) vs. auf MT5-Live warten (blockiert Backtest).
- **Erweiterte Aktien-Sessions:** RTH-only (Empfehlung) vs. Pre/Post als handelbar.
