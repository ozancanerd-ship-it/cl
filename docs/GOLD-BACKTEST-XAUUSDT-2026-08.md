# Erster Gold-Backtest — XAUUSDT (Binance USD-M Futures), 2026-08-31

**Status:** abgeschlossen. **Ergebnis: 0 Trades** — das Regime-Gate blockt XAUUSDT im
verfügbaren Zeitraum zu ~100 % (dominant `regime_unclear`), genau wie bei Crypto.

## 1. Daten

| | |
|---|---|
| Quelle | Binance USD-M Futures REST (`fapi.binance.com/fapi/v1/klines`), public, kein Key, paginiert 1500/Request |
| Instrument | `XAUUSDT` — `TRADIFI_PERPETUAL`, **Handelsstart 2025-12-11** (das ist die gesamte existierende Historie — keine 2 Jahre möglich) |
| Ingest | `scripts/ingest_binance_futures.py` |
| M5-Bars | **75 630**  (2025-12-11 08:05 → 2026-08-30 22:30 UTC, ≈ 262 Tage) |
| M15 / H4 / D1 | 25 209 / 1 574 / 261 — aus M5 abgeleitet (`require_complete=True`, PIT, kein Look-ahead) + nativ gespeichert |
| Interne Lücken | **0** — `completeness 100.00 %` (24/7-Perp, keine Wochenend-Löcher im Feed) |
| Data-Quality | `check_ohlcv_series` mit Kalender `xau_spot` → **0 Issues**, `blocks_trading=false` |
| Fingerprint (M5) | im Manifest + `RunManifest` |
| Look-ahead | `engine/parity.py` (Replay ≡ Streaming) deckt den Pfad ab; höhere TFs `require_complete`; `MarketContext(information_cutoff=…)` wirft bei Zukunftsdaten |

## 2. Backtest-Setup

| | |
|---|---|
| Pipeline | **unveränderte** zentrale `strategy.evaluate` (`SMC-SWEEP-REV-01`, `strategy_version 0.1.1`) |
| Fenster | 2026-02-01 → 2026-08-30 (≈ 210 Tage; ~52 D1-Bars Vorlauf am Fensterbeginn) |
| `asset_class` | `gold` → Session-Kalender `xau_spot` (London/NY), 24/7-Flag aus, News-Relevanz Gold |
| News-Gate | **off (Research-Modus)** — der V4-Fail-safe (`NEWS_FEED_UNAVAILABLE`) ist aus, damit die *tatsächlich blockierende* Ebene sichtbar wird. **Keine News-Daten erfunden**; News = `not_checked`. Live-repräsentativ würde V4 zusätzlich jeden Entry blocken (kein PIT-News-Feed). |
| Kosten | `estimate_conservative` (gold) — **Annahme, nicht gemessen**; Funding=0. Brutto + Netto getrennt. |
| Parameter | **alle PROPOSED DEFAULTS unverändert.** Keine Optimierung. |

## Begründete Setup-Abweichung (dokumentiert, keine Strategy-Logik geändert)

`--require-native-higher off`: Der Default verlangt **200 native D1-Bars *vor* dem
Fensterbeginn**. XAUUSDT hat insgesamt nur 261 D1-Bars (9 Monate alt) → der Default würde das
Testfenster auf ~2 Monate schrumpfen. Mit `off` werden native höhere TFs genutzt, wo vorhanden,
und der Rest **PIT-sauber aus M5 abgeleitet** (kein Fake, kein Look-ahead; `read_native_higher`
bleibt an). Der Regime-/Struktur-Klassifikator wärmt sich über den frühen Fensterbereich selbst
auf. **Keine Analyse-Parameter geändert.**

## 3. Ergebnis (`run_id=0ce5b18899ebf6e4`, `dataset_ok=True`)

| Kennzahl | Wert |
|---|---|
| Verarbeitete M5-Bars | **60 480** |
| Entscheidungen | 60 355 NO_TRADE · **125 WAIT** · 0 BUY · 0 SELL |
| **Signale erzeugt** | **605** (FSM erreichte einen Signal-Zustand) — davon **573 invalidiert**, 28 expired |
| Vetos ausgelöst | **V3** (HTF-Bias-Konflikt) 273× · **V5** (Re-Sweep M15) 506× |
| Alerts (Pipeline) | 7 628 (v. a. Data-Quality im frühen Fenster) |
| **Trades** | **0** |
| Winrate / Profit Factor / Expectancy / Max DD / Ø R | **n/a — keine Trades** |
| Score/Confidence-Informationswert | **nicht messbar** (kein Trade erreicht) |

## 4. Regime-Analyse (No-Trade-Gründe, Bars — mehrfach je Bar möglich)

| Grund | Bars | Anteil | Deutung |
|---|---:|---:|---|
| `regime_unclear` | 50 024 | 82,8 % | MTF-Konsens D1+H4 nicht eindeutig — `derive_structure_state` liefert für Gold-D1/H4 überwiegend `unclear`, **identisch zum Crypto-Befund** |
| `data_incomplete` | 19 295 | 31,9 % | **Artefakt des jungen Instruments** — im frühen Fenster (Feb–Mär) hat die aus M5 abgeleitete D1/H4-Reihe noch nicht genug Vorlauf. **Kein Strategie-Signal.** |
| `regime_vol_extreme` | 9 315 | 15,4 % | ATR-Perzentil-Ausreißer — Gold hatte 2026 H1 starke Bewegungen (2700 → 4500 USD) |
| `data_confidence_floor` | 2 591 | 4,3 % | dito frühes Fenster (data_confidence < 0,50) |
| `regime_conflicting` | 241 | 0,4 % | D1 und H4 gegenläufig |
| `regime_vol_too_low` | 174 | 0,3 % | seltene Ruhephasen |

**Welche Regime „funktionieren":** Keins bis zu einem Trade. **Aber:** anders als bei Crypto
erreichte die FSM **605×** einen Signal-Zustand — Gold-Struktur produzierte mehr
Setup-Kandidaten. Alle wurden danach von **Location-/RR-Gate, Confidence-Floor oder Veto
V3/V5** abgeräumt (573 invalidiert), bzw. die Geometrie war nicht bestimmbar (125 WAIT).

**Welche Regime blockiert werden:** `regime_unclear` (dominant), `regime_vol_extreme`,
`regime_conflicting`, `regime_vol_too_low` — plus DATA-Gate im ersten Drittel des Fensters.

**Fazit:** „Anderes Vol-Regime durch Gold" als Hebel ist im 9-Monats-Fenster **widerlegt** —
der Engpass bleibt der Regime-/Struktur-Klassifikator (`regime_unclear` bei Gold-D1/H4), nicht
die Anlageklasse. Das deckt sich mit `docs/REGIME-CALIBRATION-2026-08.md` (H4 = `unclear` 93 %)
und `docs/MULTI-SYMBOL-BACKTEST-2026-08.md`.

## 5. Nächste Schritte

1. **Dukascopy XAUUSD Spot (2+ Jahre)** ingestieren — längeres, variantenreicheres Gold-Fenster
   (2024–2026, verschiedene Regime). Adapter (`data/providers/dukascopy.py`) steht + verifiziert.
   → sauberer Backtest ohne den `data_incomplete`-Frühfenster-Effekt.
2. **H4-Struktur-Klassifikator isoliert kalibrieren** (`derive_structure_state`) — der
   Kalibrier-Backlog nennt das als **den** offenen Hebel (H4 `unclear` 93 %). Ziel: die
   `unclear`-Quote objektiv senken, ohne das Gate zu lockern. Eigenes IS/OOS-Item.
3. **Späterer Backtest-Start (2026-05-01)** auf XAUUSDT — eliminiert den DATA-Gate-Anteil,
   zeigt das reine Regime-Bild für ~4 Monate sauber.
4. Erst wenn Trades entstehen: **Entry/Exit-Qualität + Score/Confidence-Informationswert**
   messen (bisher unmöglich).
