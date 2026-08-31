# Historical Data Plan — deterministischer, look-ahead-freier Replay

**Status:** Architektur-Vorgabe · Stand 2026-08-29 · Voraussetzung für Kalibrierung
(`CALIBRATION_BACKLOG.md`) und den ersten OOS-Backtest.

Ziel: **≥ 180 Tage** hochwertige **M5**-Historie für **BTC** und **ETH**, später **M1**, plus
**Point-in-Time News/Makro**. Kein Survivorship Bias, keine Zukunftsdaten, deterministischer
Replay (gleiche Eingabe → bit-genau gleiche Decision-Folge).

---

## 1. Was schon steht (Phase 1/2)

- `data/repository.py` — `MarketDataRepository` mit `read_ohlcv(instrument, tf, start, end, as_of=)`;
  `as_of` filtert hart auf `close_time <= as_of` (PIT). `dataset_fingerprint()` = deterministischer
  SHA-256 über die (PIT-gefilterten) Bars → Grundlage für `RunManifest`/Reproduzierbarkeit.
- `data/resample.py` — M5 → M15/H4/D1, alignment-geprüft.
- `data/quality.py` + `OHLCV`-Validator — Vollständigkeit, Lücken, Duplikate, OHLC-Konsistenz,
  Timestamp-Alignment.
- `core/types.MarketContext` — erzwingt `close_time <= information_cutoff` für **jede** Serie und
  `available_time <= cutoff` für News/Derivatives/Cross-Asset. Der Look-ahead-Schutz ist im
  Kern-Datentyp, nicht optional.
- `strategy/m1_feed.py` — `RepositoryM1Source` / `InlineM1Source` / `NullM1Source`; das
  Confirmation-Fenster wird PIT-korrekt gezogen (Schritt 8).
- **`engine/replay.py`** — `ReplayClock` (deterministischer Schrittgeber, kein Wall-Clock),
  `MarketContextAssembler` (Repo → `MarketContext` je `cutoff`, einmal vorgeladen + `bisect`-
  Slice, strikt `close_time/available_time <= cutoff`), `validate_dataset` / `DatasetRequirements`
  / `DatasetReport` (meldet fehlende Historie eindeutig, `raise_if_incomplete()`), `ReplayHarness`.
- **`engine/backtest.py`** — `Backtest.run(BacktestConfig)` fährt den Replay über
  `PaperLiveRunner` (identische `strategy.evaluate`-Pipeline wie Live), baut `TradeRecord`s +
  `RunManifest` + `output_hash` + `research.metrics.Metrics` + `StrategyBacktestReport`.
- **`engine/backtest_metrics.py`** — erweiterte Kennzahlen (TP-Hit-Rates, Exit-Struktur,
  Segmente, Score-/Confidence-vs-Ergebnis-Buckets, Korrelationen, Lauf-Telemetrie).
- **`data/providers/binance_vision.py`** — `BinanceVisionProvider`: offizieller Bulk-Dienst
  `data.binance.vision` (Spot-Klines), SHA-256-verifiziert, lokal gecacht, ms/µs-Normalisierung,
  `close_time = open_time + tf`. **Stufe 1 umgesetzt** — siehe `docs/DATASET-BTC-ETH-M5.md`.
- **`scripts/ingest_binance_vision.py`** — lädt M5 + resampled M15/H4/D1 nativ in die Repo,
  fährt `check_ohlcv_series` + `validate_dataset`, gibt ein vollständiges Dataset-Manifest aus.
- **`engine/parity.py`** — `run_parity` / `compare_decisions`: vorgeladener Replay vs. streaming
  aufgebauter Kontext ⇒ Look-ahead-Beweis.

## 2. Datenbeschaffung — Anforderungen

| Aspekt | Vorgabe |
|---|---|
| Instrumente (Start) | BTC-USD, ETH-USD (Spot-Referenz, eine feste Quelle je Instrument) |
| Timeframe | M5 nativ; M1 nativ in Ausbaustufe 2; höhere TF **nur** per Resample |
| Zeitraum | ≥ 180 Tage zusammenhängend, dokumentiertes Start-/Enddatum |
| Quelle | **offizielle** Exchange-REST/Bulk-Dumps; **keine** inoffiziellen APIs, kein Scraping |
| Zeitzone | alles UTC, `open_time` an TF-Grenze ausgerichtet, `close_time = open_time + tf` |
| Revisionen | Bars gelten als final ab `close_time`; spätere Korrekturen als neue Ingestion mit `ingested_at` |
| Ablage | `MarketDataRepository` (Datei + `meta.sqlite`), ein Ordner je `(instrument, timeframe)` |

## 3. Survivorship / Selection Bias

- **Kein** nachträgliches Aussortieren von Instrumenten nach Performance. Die Instrument-Liste
  wird **vorab** festgelegt und versioniert (`refdata/`), nicht aus dem Backtest-Ergebnis.
- Delistings/Halts bleiben als Datenlücke sichtbar (`DATA_GAP_RECENT` / `MARKET_CLOSED`), werden
  nicht „geglättet".
- Nur eine Quelle je Instrument über den gesamten Zeitraum — kein Zusammenstückeln der jeweils
  „besten" Quelle je Abschnitt.

## 4. Look-ahead / Leakage — Kontrollen

1. **Datentyp-Ebene:** `MarketContext.__post_init__` wirft bei jeder Bar/News nach `cutoff`.
2. **Repository-Ebene:** jeder Lesezugriff im Replay geht über `as_of = cutoff`.
3. **Resample-Ebene:** eine höhere TF-Bar ist erst ab ihrer `close_time` sichtbar (nie die
   „laufende" Kerze).
4. **News/Makro:** jedes Event trägt `available_time` (Veröffentlichungs-, nicht Ereigniszeit);
   Vorab-Konsensus und tatsächlicher Wert sind getrennte Records mit eigener `available_time`.
5. **Feature-Ebene:** die Primitive-Detektoren (`strategy/primitives/`) markieren Swings erst
   `confirmed_at = close_time` der Bestätigungs-Bar — kein „Blick nach rechts".
6. **Test:** Long/Short-Spiegelpreis-Symmetrie + „cutoff um 1 Bar zurückziehen ⇒ Decision darf
   sich nur monoton ändern" als Replay-Invariante.

## 5. Deterministischer Replay-Harness (**gebaut** — `engine/replay.py` + `engine/backtest.py`)

```
ReplayClock.from_bars(m5_grid)          # cutoffs = close_times der M5-Bars im [start, end)
  └─ für jeden cutoff t:
       MarketContextAssembler.at(t)     # vorgeladen, bisect-Slice, close/available_time <= t
         → MarketContext(information_cutoff=t)
       PaperLiveRunner.feed(mc)         # strategy.evaluate → Signal → Paper-Position → Alerts
  └─ Backtest sammelt: TradeRecords, TradeOutcomes (Analyse-Schnappschuss), RunTelemetry
```

- **Reproduzierbarkeit:** `RunManifest` = { Dataset-Fingerprints je Instrument, `strategy_version`,
  `config_hash` (Params), Instrumente, TF, Start/Ende, Seed, Code-Commit }. `output_hash` über
  Richtung / Entry / Exit / R / Grund / Haltedauer je Trade. Zwei Läufe mit gleichem Manifest
  liefern bit-gleiche Hashes (`test_strategy_backtest::test_deterministic_output_hash`).
- **Kein Wall-Clock, kein RNG** im Strategy-Pfad. `ReplayClock.now()` = aktueller Replay-Zeitpunkt.
- **Dataset-Anforderungen** (`DatasetRequirements`, Defaults): `min_days=180`, `warmup_bars=300`
  M5-Vorlauf, optional `require_native_higher` / `require_m1` / `require_news_feed`. `validate_dataset`
  wird **vor** dem Replay ausgeführt; fehlt etwas ⇒ `DatasetIncompleteError` mit exakter Liste.
- **Ergebnis-Artefakte:** `journal.Ledger` (SQLite) je Trade; `BacktestResult` hält zusätzlich
  `Metrics` + `StrategyBacktestReport` + `RunTelemetry` + `equity_curve_r`.
- **Kosten:** der Paper-Positions-Sim rechnet aktuell **ohne** Gebühren/Slippage (`gross_r ==
  realized_r`). Ein Kostenaufschlag (analog `execution/simulation.CostParams`) ist Backlog —
  bis dahin sind alle R-Zahlen brutto.

## 6. News / Makro PIT (Ausbaustufe)

- Schema: `event_id`, `revision_key`, `event_type`, `impact`, `scheduled_time`, `available_time`,
  `actual`, `consensus`, `previous`. Bereits als `news`-Tabelle im Repository angelegt.
- Kalender-Events (FOMC, CPI, PCE, NFP, ECB): `scheduled_time` vorab bekannt (→ Pre-Positioning-
  Ban), `actual` erst ab `available_time`.
- Quelle: offizieller Wirtschaftskalender-Anbieter mit Zeitstempel-Historie; **keine** Twitter/
  Influencer-Feeds als Signal (nur `NewsContext.risk_off_flag` als grober Marktzustand).

## 7. Ausbaustufen

| Stufe | Inhalt | Status |
|---|---|---|
| 1 | M5 BTC/ETH ≥ 180 T (Binance Vision) + native M15/H4/D1 + Replay-Harness + Ledger | **erledigt** 2026-08-29 — `DATASET-BTC-ETH-M5.md`, `binance-vision-spot-klines-v1` |
| 2 | M1 BTC/ETH nativ | `confirmation_market`-Modus, feinere Fill-Sim |
| 3 | News/Makro PIT | V4-Kalibrierung, News-Confluence-Faktor scharf |
| 4 | Funding/OI (Derivatives-Slot) | Derivatives-Confluence scharf |
| 5 | Cross-Asset (DXY/Yields/VIX) | Cross-Asset-Confluence scharf |

Jede Stufe ändert nur die **Datenquelle** — die Strategy Engine (`evaluate` → `ContinuousEvaluator`
→ `PaperLiveRunner`) bleibt unverändert.
