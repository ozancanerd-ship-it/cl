# Dataset — BTC/ETH M5, Binance Vision (`binance-vision-spot-klines-v1`)

**Status:** ingested & validated 2026-08-29 · `scripts/ingest_binance_vision.py` · Repo `data/repository_real/`

Erste echte historische Datengrundlage für den Strategy-Backtest. **Keine synthetischen Bars,
keine stillschweigend fehlenden Bars** — die Ingest-Validierung war grün (siehe unten).

---

## 1. Quelle

| | |
|---|---|
| **Name** | Binance Vision — `https://data.binance.vision` |
| **Produkt** | Spot / *klines* (Candlesticks) |
| **Zugang** | öffentlicher Bulk-Download, **kein API-Key**, keine Rate-Limits, keine privaten Account-Daten |
| **URL-Muster** | `https://data.binance.vision/data/spot/monthly/klines/{SYMBOL}/5m/{SYMBOL}-5m-{YYYY-MM}.zip` |
| **Integritätsprüfung** | je Datei eine `…​.zip.CHECKSUM` (SHA-256), beim Ingest **verifiziert** |
| **Adapter** | `src/trading_agent/data/providers/binance_vision.py` (`BinanceVisionProvider`) |
| **Lokaler Cache** | `data/cache/binance_vision/` (ZIP + CHECKSUM, Re-Runs ohne Netz) |

## 2. Instrumente & Zeitraum

| Instrument | Bars (M5) | erste `open_time` | letzte `open_time` | Fingerprint (SHA-256, PIT bis `end`) |
|---|---:|---|---|---|
| **BTCUSDT** | 52 428 | 2024-12-30T23:00:00Z | 2025-06-30T23:55:00Z | `0294b09921815bae6ad478bb701a18646a51ab70e8f35fe470622a2cbf7966b6` |
| **ETHUSDT** | 52 428 | 2024-12-30T23:00:00Z | 2025-06-30T23:55:00Z | `71bed4b258156d44c2aa2c2e68c00dd3b11d97429d7e8c864dc3aadf652fd495` |

- **Backtest-Fenster:** `2025-01-01T00:00:00Z` … `2025-07-01T00:00:00Z` (exklusiv) — **181 Tage**.
- **Warmup:** 300 M5-Bars vor `start` (bis `2024-12-30T23:00:00Z`) — im Repo, nicht Teil des Replay-Grids.
- 52 428 Bars = 182 Tage × 288 Bars/Tag — **lückenlos** (keine fehlende M5-Bar, kein Gap).

## 3. Timezone & Timestamp-Konvention

- **Alle Zeitstempel UTC.**
- Quelle: Epoch-Integer, historisch **Millisekunden**, ab ~2025-01 **Mikrosekunden** — der Adapter
  erkennt das über die Größenordnung (`_norm_epoch_ms`, Schwelle `1e14`) und normalisiert auf ms.
- `open_time` = Intervallbeginn, **inklusiv**, an 5 Minuten ausgerichtet (beim Parsen geprüft — eine
  nicht ausgerichtete Bar wird abgelehnt, nicht „repariert").
- **`close_time` = `open_time + 5min`** (Projekt-Konvention `core.time.bar_close_time`). Binance
  liefert in Spalte 7 `open_time + interval − 1ms` — dieser Wert wird **verworfen**.

## 4. OHLCV-Definition

| Feld | Bedeutung |
|---|---|
| `open` | erster Trade-Preis im Intervall |
| `high` / `low` | Höchst-/Tiefstpreis im Intervall |
| `close` | letzter Trade-Preis im Intervall |
| `volume` | gehandeltes **Basis-Asset** (BTC bzw. ETH) |
| `quote_volume` | gehandeltes Quote-Asset (USDT) — mitgespeichert |
| `trades` | Anzahl Trades im Intervall — mitgespeichert |

`source = "binance_vision"` in jedem Record.

## 5. Dataset-Version & Fingerprint

- `dataset_version = "binance-vision-spot-klines-v1"` (im `RunManifest` des Backtests).
- **Fingerprint** = deterministischer SHA-256 über die (Point-in-Time bis `end` gefilterten) Bars
  je Instrument (`MarketDataRepository.dataset_fingerprint`). Siehe Tabelle §2.
- Der Backtest-`RunManifest` verkettet beide sortiert:
  `BTCUSDT:0294b099… | ETHUSDT:71bed4b2…`.

## 6. Ingest-Validierung (2026-08-29) — **grün**

`scripts/ingest_binance_vision.py` prüft je Instrument:

| Prüfung | BTCUSDT | ETHUSDT |
|---|---|---|
| `check_ohlcv_series` blockiert Handel? | **nein** | **nein** |
| Qualitäts-Issues (Duplikate, OOO, unmögliche OHLC, Gaps, Timestamp) | **keine** | **keine** |
| fehlende Bulk-Dateien | **0** | **0** |
| M5-Coverage deckt `start − 300 Warmup-Bars` … `end`? | **ja** | **ja** |

`engine.replay.validate_dataset(DatasetRequirements(min_days=180, warmup_bars=300))` →
**`ok: true`**, `missing: []`, `notes: []`.

Geprüfte Punkte (alle grün): M5-Coverage · 300 Warmup-Bars · keine Duplikate · keine unmöglichen
OHLC-Werte (`OHLCV`-Pydantic-Validator + `data.quality`) · korrekte Zeitreihenfolge · keine
fehlenden Bars · korrekte Instrumente (`BTCUSDT`/`ETHUSDT`) · korrekter Timeframe (M5).

## 7. Reproduktion

```bash
uv run python scripts/ingest_binance_vision.py \
  --symbols BTCUSDT ETHUSDT --start 2025-01-01 --end 2025-07-01 \
  --warmup-bars 300 --repo data/repository_real
```

Deterministisch: gleiche Binance-Vision-Dateien (CHECKSUM-verifiziert) ⇒ gleiche Bars ⇒ gleicher
Fingerprint. Der lokale Cache macht Re-Runs netzunabhängig.

## 8. Grenzen / bewusst nicht enthalten

- **Nur Spot-Preis.** Kein Funding / Open Interest / Basis (Derivatives-Slot bleibt leer → Confluence `UNAVAILABLE`).
- **Kein PIT-News/Makro-Dataset** → `news_feed_available=False` → V4-Fail-safe (`NEWS_FEED_UNAVAILABLE`).
- **Keine native M15/H4/D1-Serie** → aus M5 abgeleitet (`build_mtf_context`, look-ahead-frei).
- **Kein Orderbuch / keine echte Spanne** → `fixed_spread` konfigurierbar, Default `None`.
- Ausbaustufen (Funding → News → Cross-Asset) siehe `HISTORICAL_DATA_PLAN.md` §7.
