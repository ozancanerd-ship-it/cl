# Market Regime — objektive Definitionen

**Zweck:** Der Regime-Zustand ist ein **Gate vor jeder Setup-Bewertung**. Er entscheidet, ob
überhaupt gehandelt wird und mit welchem Parameter-Satz. Bei **unklarem oder widersprüchlichem
Regime gibt das System `NO_TRADE` aus** — das ist eine Kernanforderung, kein Randfall.

Alle Schwellen sind `PROPOSED DEFAULT` (siehe Erklärung in `primitives.md` §0). Alle Parameter
sind pro Instrument und Timeframe überschreibbar. MVP: BTCUSDT/ETHUSDT, Regime auf **D1, H4, M15**.

---

## 1. Modell: drei orthogonale Achsen + kombinierter Zustand

| Achse | Werte |
|-------|-------|
| **Directional** | `TREND_UP` · `TREND_DOWN` · `RANGE` · `UNCLEAR` · `CONFLICTING` |
| **Volatility** | `LOW` · `NORMAL` · `HIGH` · `EXTREME` |
| **Phase** | `EXPANSION` · `COMPRESSION` · `NEUTRAL` |

Der **kombinierte Regime-Zustand** je Timeframe ist das Tripel `(directional, volatility, phase)`
plus Scores. Zusätzlich gibt es einen **Multi-Timeframe-Regime-Konsens** (§7).

Alle Achsen werden **nur auf `confirmed`-Bars** berechnet (Look-ahead-Schutz).

---

## 2. Directional Regime

### 2.1 `TREND_UP` / `TREND_DOWN`
Beide Bedingungen müssen erfüllt sein:

**(A) Strukturbedingung:** die letzten `regime.trend.min_swings` (**PROPOSED DEFAULT `2`**)
bestätigten Swing-Paare (aus `primitives.md` §1) bilden
- `TREND_UP`: durchgängig HH **und** HL
- `TREND_DOWN`: durchgängig LH **und** LL

**(B) Slope-Bedingung:** normierte Steigung einer linearen Regression über `log(close)` der
letzten `regime.trend.slope_window` Bars:
`slope_norm = regression_slope_per_bar / ATR(tf) × price`
- `TREND_UP`: `slope_norm ≥ regime.trend.min_slope`
- `TREND_DOWN`: `slope_norm ≤ −regime.trend.min_slope`

**Trend-Stärke-Score** (0..1):
`trend_strength = clip( 0.5·structure_term + 0.3·slope_term + 0.2·pullback_term , 0, 1)`
- `structure_term` = Anteil der letzten `4` Swing-Paare, die die Trendrichtung bestätigen
- `slope_term = clip(|slope_norm| / regime.trend.slope_saturation, 0, 1)`
- `pullback_term` = `1 −` (tiefster Retracement-Anteil des letzten Legs, gemessen als Fib);
  flache Pullbacks ⇒ starker Trend

| Parameter | PROPOSED DEFAULT | Bedeutung |
|-----------|------------------|-----------|
| `regime.trend.min_swings` | `2` | Swing-Paare für Strukturbestätigung |
| `regime.trend.slope_window` | `50` | Bars für die Regression |
| `regime.trend.min_slope` | `0.05` | Mindest-\|slope_norm\| |
| `regime.trend.slope_saturation` | `0.20` | \|slope_norm\|, ab der `slope_term = 1` |

*Warum validieren:* Struktur **und** Slope zu fordern reduziert Fehlklassifikation, verzögert aber
die Trenderkennung. `min_slope = 0.05` ist ein Platzhalter ohne empirische Basis; er muss je
Assetklasse/Timeframe kalibriert werden (Crypto-M15 ≠ FX-D1).

### 2.2 `RANGE`
Alle Bedingungen:
1. **Kein** BOS (`primitives.md` §2) in den letzten `regime.range.window` Bars.
2. **Envelope:** `(range_high − range_low) / ATR(tf) ≤ regime.range.max_height_atr`, wobei
   `range_high/low` = höchstes High / tiefstes Low der letzten `regime.range.window` Bars.
3. **Flachheit:** `|slope_norm| ≤ regime.range.max_slope`.
4. **Berührungen:** je Grenze `≥ regime.range.min_touches` Bars, deren High/Low die Grenze
   innerhalb `regime.range.touch_eps_atr` berührten, mit Mindestabstand
   `regime.range.min_touch_separation_bars` zwischen Berührungen.

**Range-Grenzen** = die beiden getesteten Levels; werden als `LiquidityLevel` vom Typ
`range_high/range_low` publiziert.

**Range-Reife-Score** (0..1): `clip(min(touches_top, touches_bottom) / 4 , 0, 1) × (1 −
height_atr / regime.range.max_height_atr)`.

| Parameter | PROPOSED DEFAULT | Bedeutung |
|-----------|------------------|-----------|
| `regime.range.window` | `40` | Beobachtungsfenster |
| `regime.range.max_height_atr` | `8` | maximale Range-Höhe in ATR(tf) |
| `regime.range.max_slope` | `0.03` | Flachheitsgrenze |
| `regime.range.min_touches` | `2` | Berührungen je Grenze |
| `regime.range.touch_eps_atr` | `0.15` | Berührungstoleranz |
| `regime.range.min_touch_separation_bars` | `3` | Abstand zwischen Berührungen |

*Warum validieren:* `max_height_atr` und `window` bestimmen zusammen, ob z. B. eine
mehrtägige Konsolidierung als eine Range oder als mehrere Mikro-Trends gilt. Diese Wahl wirkt
direkt auf die Setup-Auswahl (Reversal-Setups leben in Ranges / an Trend-Extremen).

### 2.3 `UNCLEAR`
Weder `TREND_*` noch `RANGE` erfüllt. **Ergebnis am Regime-Gate: `NO_TRADE`.**

### 2.4 `CONFLICTING`
Nur auf Multi-Timeframe-Ebene (§7): zwei betrachtete Timeframes liefern gegensätzliche gerichtete
Regime (`TREND_UP` vs `TREND_DOWN`) **und** der Konfliktscore überschreitet
`regime.conflict.max_disagreement` (**PROPOSED DEFAULT `0.0`**, d. h. jede echte Gegenrichtung
zählt). **Ergebnis am Regime-Gate: `NO_TRADE`.**

---

## 3. Volatility Regime

**Metrik:** `vol_pct(tf)` = Perzentil-Rang von `ATR(regime.vol.atr_period, tf)` innerhalb der
letzten `regime.vol.lookback` Werte. Zusätzlich absolute Kennzahl `atr_ratio = ATR / price`.

| Zustand | Bedingung |
|---------|-----------|
| `LOW` | `vol_pct ≤ regime.vol.low_pct` |
| `NORMAL` | dazwischen |
| `HIGH` | `vol_pct ≥ regime.vol.high_pct` |
| `EXTREME` | `atr_ratio ≥ regime.vol.extreme_atr_ratio[asset_class]` **oder** `vol_pct ≥ regime.vol.extreme_pct` |

**`EXTREME` ⇒ `NO_TRADE`** (Stops unbrauchbar, Slippage/Spread unkontrollierbar).

| Parameter | PROPOSED DEFAULT | Bedeutung |
|-----------|------------------|-----------|
| `regime.vol.atr_period` | `14` | ATR-Periode für die Vol-Messung |
| `regime.vol.lookback` | `100` | Fenster für den Perzentil-Rang |
| `regime.vol.low_pct` | `20` | Untergrenze `LOW` |
| `regime.vol.high_pct` | `80` | Obergrenze `HIGH` |
| `regime.vol.extreme_pct` | `97` | Perzentil für `EXTREME` |
| `regime.vol.extreme_atr_ratio.crypto` | `0.08` | ATR/Preis-Grenze Crypto (Tages-Äquiv.) |
| `regime.vol.extreme_atr_ratio.gold` | `0.04` | Gold |
| `regime.vol.extreme_atr_ratio.forex` | `0.02` | Forex |
| `regime.vol.extreme_atr_ratio.equity` | `0.06` | Aktien/ETF |

*Warum validieren:* Perzentil-Grenzen (20/80) sind Konvention. Ob `HIGH`-Vol für ein
Sweep-Reversal förderlich (mehr Displacement) oder schädlich (mehr Fehl-Sweeps) ist, muss pro
Setup gemessen werden. Die `EXTREME`-Absolutgrenzen sind grobe Schätzungen und **müssen**
pro Instrument aus der historischen ATR/Preis-Verteilung gesetzt werden.

---

## 4. Phase: Expansion / Compression

Beschreibt die **Volatilitäts-Dynamik** (Ableitung), nicht das Niveau.

### 4.1 `EXPANSION`
Alle Bedingungen:
1. `ATR(tf)_now / ATR(tf)_{now − regime.phase.window} ≥ regime.phase.expansion_atr_ratio`
2. `range(last k bars) > range(prior k bars)` mit `k = regime.phase.window`
3. mindestens ein Displacement (`primitives.md` §7) innerhalb der letzten `regime.phase.window` Bars

`EXPANSION` ist **gerichtet**, wenn im selben Fenster ein BOS/CHoCH auftrat
(`expansion_direction ∈ {UP, DOWN}`), sonst `expansion_direction = NONE`.

### 4.2 `COMPRESSION`
Alle Bedingungen:
1. `ATR(tf)_now / ATR(tf)_{now − regime.phase.window} ≤ regime.phase.compression_atr_ratio`
2. Bandbreite (`bollinger_bandwidth` mit Periode `regime.phase.bandwidth_period`, oder
   High-Low-Envelope) `≤ regime.phase.bandwidth_pct`-Perzentil über `regime.vol.lookback`
3. `≥ regime.phase.min_compression_bars` aufeinanderfolgende Bars mit
   `range(bar) ≤ regime.phase.narrow_bar_atr × ATR(tf)`

`COMPRESSION` wird als **`coiled`** markiert, wenn sie `≥ regime.phase.coiled_bars` andauert
(erhöhte Wahrscheinlichkeit einer folgenden Expansion → für Breakout-Setups relevant, **nicht**
für SMC-SWEEP-REV-01).

### 4.3 `NEUTRAL`
Weder Expansion noch Compression.

| Parameter | PROPOSED DEFAULT | Bedeutung |
|-----------|------------------|-----------|
| `regime.phase.window` | `10` | Vergleichsfenster für die ATR-Dynamik |
| `regime.phase.expansion_atr_ratio` | `1.30` | ATR-Anstieg für Expansion |
| `regime.phase.compression_atr_ratio` | `0.80` | ATR-Rückgang für Compression |
| `regime.phase.bandwidth_period` | `20` | Bollinger-Periode |
| `regime.phase.bandwidth_pct` | `25` | Perzentil für „schmal" |
| `regime.phase.min_compression_bars` | `5` | Mindestdauer Compression |
| `regime.phase.narrow_bar_atr` | `0.7` | „schmale" Bar in ATR |
| `regime.phase.coiled_bars` | `10` | ab hier `coiled` |

*Warum validieren:* Die ATR-Ratio-Schwellen (1.3 / 0.8) sind symmetrische Platzhalter. Ihre
Wirkung ist stark timeframe-abhängig und interagiert mit `regime.vol.lookback`.

---

## 5. Kombinierter Regime-Zustand (je Timeframe)

```
RegimeState {
  timeframe: TF
  directional: TREND_UP | TREND_DOWN | RANGE | UNCLEAR | CONFLICTING
  directional_score: float          # trend_strength bzw. range_maturity, 0..1
  volatility: LOW | NORMAL | HIGH | EXTREME
  volatility_pct: float             # 0..100
  phase: EXPANSION | COMPRESSION | NEUTRAL
  expansion_direction: UP | DOWN | NONE
  computed_at: UTC
  bars_in_state: int                # für Hysterese
}
```

---

## 6. Hysterese (Anti-Flattern)

Ein Regime-Wechsel wird erst übernommen, wenn die neue Bedingung `regime.hysteresis.min_bars`
(**PROPOSED DEFAULT `3`**) aufeinanderfolgende `confirmed`-Bars erfüllt ist.

Zusätzlich **asymmetrische Schwellen** (Schmitt-Trigger):
- `HIGH`-Vol betreten bei `vol_pct ≥ 80`, verlassen erst bei `vol_pct < regime.hysteresis.vol_exit_pct`
  (**PROPOSED DEFAULT `70`**).
- `TREND` betreten bei `|slope_norm| ≥ min_slope`, verlassen erst bei
  `|slope_norm| < regime.hysteresis.trend_exit_slope` (**PROPOSED DEFAULT `0.03`**).

**Regime-Cooldown:** Nach jedem übernommenen Wechsel des `directional`-Zustands gilt für
`regime.cooldown_bars` (**PROPOSED DEFAULT `3`**) Bars auf dem betroffenen Timeframe:
`regime_gate = NO_TRADE` (frisch gewechselte Regime sind am unzuverlässigsten).

*Warum validieren:* Ohne Hysterese oszilliert die Klassifikation an den Schwellen und erzeugt
Cooldown-Sperren im Sekundentakt. `min_bars = 3` ist ein Kompromiss zwischen Reaktionszeit und
Stabilität — der optimale Wert hängt vom Entry-Timeframe ab.

---

## 7. Multi-Timeframe-Regime-Konsens

Betrachtete Timeframes je Setup (SMC-SWEEP-REV-01: `D1, H4` als HTF; `M15` als Kontext-LTF).

**Regeln:**
1. **`CONFLICTING`** wenn `directional(D1)` und `directional(H4)` gegensätzliche `TREND_*` sind
   ⇒ `regime_gate = NO_TRADE`.
2. **`UNCLEAR`** wenn `directional(D1) = UNCLEAR` **oder** `directional(H4) = UNCLEAR`
   ⇒ `regime_gate = NO_TRADE` (**PROPOSED DEFAULT**; über `regime.mtf.allow_unclear_htf`
   abschaltbar, aber Standard = streng).
3. **`EXTREME`** auf einem der betrachteten Timeframes ⇒ `regime_gate = NO_TRADE`.
4. Sonst: `htf_regime = merge(D1, H4)` nach folgender Tabelle:

| D1 | H4 | merged HTF directional |
|----|----|------------------------|
| TREND_UP | TREND_UP | `TREND_UP` (stark) |
| TREND_UP | RANGE | `TREND_UP` (moderat) — Longs nur aus Discount |
| RANGE | TREND_UP | `TREND_UP` (schwach) — kleinere Größe |
| RANGE | RANGE | `RANGE` |
| TREND_UP | TREND_DOWN | `CONFLICTING` ⇒ NO_TRADE |
| * | UNCLEAR | `UNCLEAR` ⇒ NO_TRADE |

(spiegelbildlich für `TREND_DOWN`.)

**Konfliktscore** (für Logging/Confidence):
`disagreement = |dir_num(D1) − dir_num(H4)| / 2` mit `dir_num: TREND_UP=+1, RANGE=0, TREND_DOWN=−1`
(`UNCLEAR/CONFLICTING` ⇒ Score `1.0`).

---

## 8. Regime → Setup-Freigabe (Matrix)

| Setup | erlaubte `directional` (HTF-merged) | erlaubte `volatility` | erlaubte `phase` | verbotene Zustände |
|-------|-------------------------------------|-----------------------|------------------|--------------------|
| **SMC-SWEEP-REV-01** | `TREND_UP`, `TREND_DOWN`, `RANGE` | `NORMAL`, `HIGH` | `NEUTRAL`, `EXPANSION` | `UNCLEAR`, `CONFLICTING`, `EXTREME`, `LOW`-Vol, reine `COMPRESSION` (`coiled`) |
| *(künftige Breakout-Setups)* | `RANGE` → Bruch, `EXPANSION` | `NORMAL`, `HIGH` | `COMPRESSION`→`EXPANSION` | `UNCLEAR`, `EXTREME` |
| *(künftige Trend-Continuation)* | `TREND_*` | `LOW`, `NORMAL`, `HIGH` | alle außer `COMPRESSION` | `RANGE`, `UNCLEAR`, `CONFLICTING`, `EXTREME` |

**Begründung SMC-SWEEP-REV-01:** Das Setup braucht (a) ein Liquiditätsziel, das entweder eine
Range-Grenze oder ein Gegen-Trend-Extrem ist, und (b) genug Volatilität, um nach dem Sweep ein
Displacement zu erzeugen. In `LOW`-Vol / `COMPRESSION` entsteht kein qualifizierendes
Displacement; in `EXTREME` ist der Reclaim nicht verlässlich vom Fortlauf zu unterscheiden.

`LOW`-Vol ist **PROPOSED DEFAULT verboten** für dieses Setup — das ist eine bewusst konservative
Startannahme, die per Backtest gelockert werden kann, falls Low-Vol-Sweeps sich als profitabel
erweisen.

---

## 9. `NO_TRADE`-Ausgänge des Regime-Gates (Zusammenfassung)

Das Regime-Gate gibt `NO_TRADE` mit einem dieser Gründe aus (Enum `RegimeNoTradeReason`):

| Grund | Auslöser |
|-------|----------|
| `REGIME_UNCLEAR` | `directional = UNCLEAR` auf D1 oder H4 |
| `REGIME_CONFLICTING` | D1/H4 gegensätzliche Trends |
| `REGIME_VOL_EXTREME` | `volatility = EXTREME` auf einem betrachteten TF |
| `REGIME_VOL_TOO_LOW` | `volatility = LOW` und Setup verbietet Low-Vol |
| `REGIME_COMPRESSION` | reine/`coiled` Compression und Setup verbietet sie |
| `REGIME_COOLDOWN` | `directional` wechselte innerhalb `regime.cooldown_bars` |
| `REGIME_NOT_ALLOWED_FOR_SETUP` | Zustand nicht in der Matrix §8 |

Diese Gründe fließen in `no-trade.md` (globale Liste) und ins Decision Ledger ein.

---

## 10. Status der Festlegungen (Nutzer-Bestätigung 2026-08-28 — `strategy_version 0.1.0`)

| Punkt | Festlegung |
|-------|------------|
| Streng-Modus HTF `UNCLEAR` (`regime.mtf.allow_unclear_htf = false`) | **bestätigt** — D1 **oder** H4 = `UNCLEAR` ⇒ `NO_TRADE` |
| `LOW`-Vol-Verbot für SMC-SWEEP-REV-01 | **bestätigt** — im MVP verboten; Lockerung nur nach empirischer Validierung |
| Regime-Konsens-Timeframes | **bestätigt** — **D1 + H4**; M15 nur Kontext (kein H1 im Konsens) |
| Absolute `EXTREME`-Grenzen je Assetklasse (§3) | **offen** — grobe Schätzwerte; **vor dem ersten Backtest** aus der historischen ATR/Preis-Verteilung je Instrument neu setzen |
| MTF-Serien (D1/H4/M15) | **`0.1.1` C11:** im MVP aus **M5-Basis** per `data/resample.py` abgeleitet (nicht nativ geladen). Warmup: `vol.lookback = 100` D1-Bars ⇒ ≥ ~100 Tage M5 nötig, bevor das Regime-Gate nicht-`UNCLEAR` liefern kann. |

Weiterhin empirisch zu validieren: alle Slope-/Range-/Vol-/Phase-Schwellen in §2–§4,
`hysteresis.min_bars`, `cooldown_bars` (siehe `anti-overfitting.md`).
