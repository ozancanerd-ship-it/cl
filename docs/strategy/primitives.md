# Strategy Primitives — objektive, programmierbare Definitionen

**Zweck:** Jede Primitive ist so definiert, dass zwei Entwickler unabhängig denselben Detektor
bauen und auf identischen Daten identische Objekte erhalten. **Keine Definition verwendet
unquantifizierte Begriffe** wie „stark", „sauber", „signifikant". Wo ein solcher Begriff nötig
wäre, steht stattdessen ein benannter Parameter mit `PROPOSED DEFAULT`.

> **`PROPOSED DEFAULT`** = ein Startwert, damit das System lauffähig/testbar ist. Er ist **nicht
> final**. Jeder solche Wert muss in Phase „Research/Backtest" empirisch validiert werden
> (Sensitivitätsanalyse, Walk-Forward, OOS — siehe `anti-overfitting.md`). Bis dahin gilt: der
> Wert ist eine Hypothese, kein Fakt.

**Geltung:** Multi-Asset (Stocks, ETFs, Crypto, Altcoins, Gold/XAUUSD, Forex). Alle Parameter
sind **pro Instrument und pro Timeframe** überschreibbar (`primitives.<name>.<param>` global,
`instruments.<symbol>.primitives.<name>.<param>` lokal). MVP-Kalibrierung: BTCUSDT, ETHUSDT,
HTF = D1/H4, Entry = M15/M5.

---

## 0. Gemeinsame Grundlagen

### 0.1 Kerze / Bar
Eine Bar `b` mit Zeitstempel `t` deckt das halboffene Intervall `[t, t+Δ)` ab und **schließt** zu
`t+Δ`. Felder: `open, high, low, close, volume`. Alle Zeitstempel in **UTC**.

- **`confirmed(b)`** ⇔ `now >= b.t + Δ` (die Bar ist geschlossen). **Jede Primitive arbeitet
  ausschließlich auf `confirmed`-Bars.** Nicht geschlossene Bars sind für die Erkennung unsichtbar
  (Look-ahead-Schutz, siehe `backtest-labeling.md`).

### 0.2 ATR (Average True Range)
`ATR(p, tf)` = Wilder-geglättetes Mittel der True Range über `p` Bars auf Timeframe `tf`.
- `TR = max(high−low, |high−prev_close|, |low−prev_close|)`
- Parameter `primitives.atr.period` — **PROPOSED DEFAULT `14`**.
  *Warum validieren:* 14 ist Konvention aus dem Tageschart-Kontext; für M5-Crypto kann ein
  kürzerer Wert reaktiver sein. Der Wert beeinflusst **alle** ATR-normierten Schwellen.
- ATR wird **je Timeframe getrennt** berechnet. „ATR(M15)" heißt: ATR der M15-Serie.

### 0.3 Tick / Preisauflösung
`tick_size(instrument)` kommt aus dem Reference-Data-Service (`refdata/`). Alle „um X Ticks"-
Schwellen sind alternativ als ATR-Anteil formulierbar; der jeweils **größere** der beiden Werte
gilt (schützt illiquide/teure Instrumente).

### 0.4 Timeframe-Hierarchie
`D1 > H4 > H1 > M30 > M15 > M5 > M1`. „HTF" (Higher Timeframe) und „LTF" (Lower Timeframe) sind
relativ zum jeweils betrachteten Kontext und werden je Setup explizit benannt.

### 0.5 Reference Range / Dealing Range
Viele Primitive brauchen einen Bezugsrahmen für „wo im Move ist der Preis".
**`dealing_range(tf)`** := das Intervall zwischen dem **letzten bestätigten Swing Low** und dem
**letzten bestätigten Swing High** auf `tf`, sofern beide zur selben zusammenhängenden
Preisbewegung gehören (kein dazwischenliegender BOS in Gegenrichtung).
- `range_low = min(letzter bestätigter SL, ...)`, `range_high = max(letzter bestätigter SH, ...)`.
- Position im Range: `pd_position = (price − range_low) / (range_high − range_low)` ∈ [0, 1].
- Parameter `primitives.dealing_range.reference_tf` — **PROPOSED DEFAULT: der HTF des jeweiligen
  Setups** (für SMC-SWEEP-REV-01: H4). *Warum validieren:* Die Wahl des Bezugsrahmens verschiebt
  Premium/Discount-Einordnungen erheblich; muss gegen realisierte Ergebnisse geprüft werden.

---

## 1. Swing High / Swing Low

### Definition (Fraktal-Methode, primär)
Bar `i` ist ein **Swing High**, wenn:
- `high[i] > high[i−k]` für alle `k ∈ 1..L`  **und**
- `high[i] > high[i+k]` für alle `k ∈ 1..R`
- (streng `>`; bei Gleichstand siehe „Equal High/Low").

**Swing Low** analog mit `low` und `<`.

### Parameter
| Parameter | PROPOSED DEFAULT | Bedeutung |
|-----------|------------------|-----------|
| `primitives.swing.left` (L) | `2` | Bars links |
| `primitives.swing.right` (R) | `2` | Bars rechts (⇒ 5-Bar-Fraktal) |
| `primitives.swing.method` | `fractal` | `fractal` \| `zigzag` |
| `primitives.swing.zigzag_reversal_atr` | `1.0` | nur bei `zigzag`: Mindest-Gegenbewegung in ATR |
| `primitives.swing.min_leg_atr` | `0.5` | Mindestabstand (in ATR des tf) zwischen aufeinanderfolgenden **gegensätzlichen** Swings; kleinere werden verworfen/zusammengefasst |

*Warum validieren:* `L=R=2` ist der kleinste Fraktal, der Rauschen kaum filtert. Zu große Werte
verpassen frühe Strukturbrüche. `min_leg_atr` ist der Hebel gegen „Micro-Swings"; sein Wert
entscheidet, wie viele Strukturpunkte überhaupt entstehen.

### Bestätigung
Ein Swing ist **`confirmed`**, sobald `R` weitere Bars nach `i` geschlossen sind. Vorher existiert
er nicht (auch nicht „vorläufig"). Attribut `bars_since_confirmation` wird geführt.

### Ausgabeobjekt
```
SwingPoint {
  type: SWING_HIGH | SWING_LOW
  timeframe: TF
  index: int              # Bar-Index
  timestamp: UTC
  price: float             # high[i] bzw. low[i]
  confirmed_at: UTC
  leg_size_atr: float      # Abstand zum vorherigen gegensätzlichen Swing, in ATR
}
```

### Labeling HH / HL / LH / LL
Gegeben die geordnete Folge bestätigter Swings gleichen Typs:
- aktueller Swing High > vorheriger Swing High ⇒ **HH**, sonst **LH**
- aktueller Swing Low  > vorheriger Swing Low  ⇒ **HL**, sonst **LL**
(Vergleich mit Toleranz `primitives.swing.equal_eps_atr` — **PROPOSED DEFAULT `0.05`** ATR;
innerhalb der Toleranz ⇒ „equal", siehe §5.)

---

## 2. BOS — Break of Structure

### Vorbedingung: Strukturzustand
BOS ist nur definiert, wenn auf `tf` ein **gerichteter Strukturzustand** vorliegt:
- **Uptrend-Struktur:** die letzten `primitives.structure.min_swings` (**PROPOSED DEFAULT `2`**)
  bestätigten Swing-Paare bilden HH **und** HL.
- **Downtrend-Struktur:** entsprechend LH **und** LL.
- sonst: `range`/`unclear` — dann gilt statt BOS die Range-Bruch-Regel (§2.3).

### 2.1 BOS in Uptrend (bullisch)
Sei `SH*` der **letzte bestätigte Swing High**, der den aktuellen Aufwärts-Leg begonnen hat
(d. h. das jüngste bestätigte SH vor dem letzten HL).
**Bullischer BOS** ⇔ eine `confirmed`-Bar schließt mit
`close > SH*.price + primitives.bos.buffer_atr × ATR(tf)`.

### 2.2 BOS in Downtrend (bearisch)
Analog: `close < SL*.price − primitives.bos.buffer_atr × ATR(tf)`.

### 2.3 Range-Bruch (wenn keine gerichtete Struktur)
Range-Grenzen `range_high/range_low` aus §0.5 bzw. der Range-Regime-Definition
(`regime.md` §Range). Bruch = `confirmed close` jenseits der Grenze um `≥ bos.buffer_atr × ATR`.
Ein Range-Bruch wird als BOS in die Bruchrichtung geführt (`origin = RANGE`).

### Parameter
| Parameter | PROPOSED DEFAULT | Bedeutung |
|-----------|------------------|-----------|
| `primitives.bos.confirmation` | `close` | `close` \| `wick` (welche Preisart den Level nehmen muss) |
| `primitives.bos.buffer_atr` | `0.0` | Zusatzabstand über/unter dem Level, in ATR(tf). `0.0` = jeder Schluss jenseits zählt |
| `primitives.structure.min_swings` | `2` | wie viele Swing-Paare eine Struktur definieren |

*Warum validieren:* `confirmation=close` verhindert Wick-Fakeouts, verzögert aber die Erkennung.
`buffer_atr=0` maximiert Sensitivität; ein kleiner Puffer (z. B. 0.1) reduziert Fehlsignale in
Rauschen. Beide Wahlen ändern BOS-Häufigkeit und damit jede darauf aufbauende Logik.

### Ausgabeobjekt
```
StructureBreak {
  kind: BOS
  direction: BULLISH | BEARISH
  timeframe: TF
  broken_level_price: float       # SH*.price bzw. SL*.price bzw. Range-Grenze
  broken_swing_ref: SwingPoint?    # null bei origin=RANGE
  origin: TREND | RANGE
  break_bar_timestamp: UTC
  break_close: float
  displacement_ref: Displacement?  # falls der Bruch Teil eines Displacements war (§6)
}
```

---

## 3. CHoCH — Change of Character

**CHoCH = der erste bestätigte Bruch _gegen_ den vorherrschenden Strukturzustand.**

### 3.1 Bullischer CHoCH (in Downtrend-Struktur)
Vorbedingung: `tf` ist in Downtrend-Struktur (LH + LL).
Sei `LH_last` der **letzte bestätigte Lower High**.
**Bullischer CHoCH** ⇔ `confirmed close > LH_last.price + primitives.choch.buffer_atr × ATR(tf)`.

### 3.2 Bearischer CHoCH (in Uptrend-Struktur)
Sei `HL_last` der letzte bestätigte Higher Low.
**Bearischer CHoCH** ⇔ `confirmed close < HL_last.price − primitives.choch.buffer_atr × ATR(tf)`.

### 3.3 Abgrenzung BOS ↔ CHoCH
- Bruch **in** Trendrichtung ⇒ BOS.
- Bruch **gegen** Trendrichtung ⇒ CHoCH (genau der erste; jeder weitere Bruch danach in der
  neuen Richtung ist wieder BOS).
- In `range`/`unclear`: **kein** CHoCH (es gibt keinen Charakter, der sich ändern könnte). Statt-
  dessen Range-Bruch (§2.3).

### Parameter
| Parameter | PROPOSED DEFAULT | Bedeutung |
|-----------|------------------|-----------|
| `primitives.choch.buffer_atr` | `0.0` | wie `bos.buffer_atr`, separat einstellbar |
| `primitives.choch.confirmation` | `close` | `close` \| `wick` |

*Warum validieren:* CHoCH ist der häufigste „Reversal-Trigger" in SMC und zugleich der
fehlanfälligste. Der Puffer und die `close`-Bedingung steuern direkt die False-Positive-Rate.

### Ausgabeobjekt
```
StructureBreak {
  kind: CHOCH
  direction: BULLISH | BEARISH
  timeframe: TF
  broken_level_price: float       # LH_last / HL_last
  broken_swing_ref: SwingPoint
  prior_state: TREND_UP | TREND_DOWN
  break_bar_timestamp: UTC
}
```

---

## 4. Liquidity Level

Ein **Liquidity Level** ist ein Preis, an dem mit hoher Wahrscheinlichkeit ruhende Orders
(Stops, Limit-Orders) liegen. Es ist **kein** Bauchgefühl, sondern die Vereinigung klar
definierter Level-Typen.

### 4.1 Erlaubte Level-Typen (`primitives.liquidity.enabled_types`)
| Typ | Konstruktion |
|-----|--------------|
| `swing_high` / `swing_low` | bestätigter SwingPoint (§1) auf `tf ∈ liquidity.swing_tfs` — **PROPOSED DEFAULT `[H1, H4, D1]`** |
| `equal_highs` / `equal_lows` | Cluster gemäß §5 |
| `pdh` / `pdl` | Previous Day High/Low: Extremwerte der letzten abgeschlossenen D1-Bar |
| `pwh` / `pwl` | Previous Week High/Low: Extremwerte der letzten abgeschlossenen W1-Bar |
| `session_high` / `session_low` | Extremwerte des letzten abgeschlossenen Session-Fensters je Session (Asia/London/NY) — Fenster aus `sessions` (DST-korrekt, Börsenlokalzeit → UTC) |
| `range_high` / `range_low` | Grenzen einer erkannten Range (`regime.md`) |

### 4.2 Stärke-Score (objektiv, 0..1)
`strength = clip( w1·touch_term + w2·age_term + w3·equal_term + w4·session_term + w5·htf_term , 0, 1)`

mit
- `touch_term = min(touch_count / primitives.liquidity.touch_saturation, 1)` —
  `touch_count` = Anzahl Bars, deren `high`/`low` den Level innerhalb `liquidity.touch_eps_atr`
  (**PROPOSED DEFAULT `0.10`** ATR) berührten, ohne ihn per `close` zu brechen.
  `touch_saturation` — **PROPOSED DEFAULT `4`**.
- `age_term = clip(age_bars / primitives.liquidity.age_saturation, 0, 1)` mit
  `age_saturation` — **PROPOSED DEFAULT `50`** (Bars auf `tf`). Älter = mehr aufgestaute Liquidität,
  bis Sättigung.
- `equal_term = 1` wenn der Level ein `equal_highs/lows`-Cluster ist, sonst `0`.
- `session_term = 1` für `pdh/pdl/pwh/pwl/session_*`, sonst `0`.
- `htf_term = (rank des tf in [M15..D1]) / 5` — höherer Timeframe ⇒ höher.
- Gewichte `primitives.liquidity.weights = {touch: 0.30, age: 0.15, equal: 0.25, session: 0.20,
  htf: 0.10}` — **PROPOSED DEFAULT**.

*Warum validieren:* Der Stärke-Score ist eine **Hypothese über Ordnungscluster**, die niemand
direkt beobachten kann. Gewichte und Sättigungswerte müssen daran gemessen werden, ob
„starke" Levels tatsächlich häufiger/heftiger gesweept werden und bessere Reversals liefern.

### 4.3 Zustand
`state ∈ { UNSWEPT, SWEPT, BROKEN }`:
- `UNSWEPT`: noch nicht durchstochen.
- `SWEPT`: gemäß §6 gesweept (durchstochen **und** reclaimed).
- `BROKEN`: `close` jenseits des Levels ohne Reclaim innerhalb `sweep.max_reclaim_bars` ⇒ echter
  Bruch, Level nicht mehr als Liquiditätsziel gültig.

### Ausgabeobjekt
```
LiquidityLevel {
  type: <einer der 4.1-Typen>
  side: BUY_SIDE | SELL_SIDE       # über Hochs = BUY_SIDE; unter Tiefs = SELL_SIDE
  price: float
  timeframe: TF
  formed_at: UTC
  strength: float                  # 0..1 (§4.2)
  touch_count: int
  state: UNSWEPT | SWEPT | BROKEN
  swept_at: UTC?
}
```

---

## 5. Equal High / Equal Low

**Equal Highs** = zwei oder mehr Swing Highs auf gleichem Preisniveau (innerhalb Toleranz),
getrennt durch einen zwischenliegenden Swing Low ausreichender Tiefe.

### Bedingungen
Für Swing Highs `SH_a`, `SH_b` (a vor b, beide `confirmed`):
1. `|SH_a.price − SH_b.price| ≤ max( primitives.equal.tol_atr × ATR(tf) ,
   primitives.equal.tol_pct × price , primitives.equal.tol_ticks × tick_size )`
2. `SH_b.index − SH_a.index ≥ primitives.equal.min_separation_bars`
3. es existiert zwischen `a` und `b` ein bestätigter Swing Low mit
   `leg_size_atr ≥ primitives.equal.min_intervening_depth_atr`

Mehr als zwei ⇒ Cluster; Referenzpreis `cluster_price = extremster` (höchstes der Highs / tiefstes
der Lows), weil dort die äußersten Stops liegen.

### Parameter
| Parameter | PROPOSED DEFAULT | Bedeutung |
|-----------|------------------|-----------|
| `primitives.equal.tol_atr` | `0.10` | Preisgleichheit in ATR(tf) |
| `primitives.equal.tol_pct` | `0.05 %` | alternativ relativ |
| `primitives.equal.tol_ticks` | `2` | alternativ absolut |
| `primitives.equal.min_separation_bars` | `3` | Mindestabstand der beiden Highs/Lows |
| `primitives.equal.min_intervening_depth_atr` | `0.5` | Tiefe des Zwischen-Swings |

*Warum validieren:* Die Toleranz entscheidet, ob „Doppeltops" erkannt werden oder nicht. Zu eng
⇒ fast nie; zu weit ⇒ überall. Muss pro Assetklasse geprüft werden (Forex-Pips ≠ Crypto-%).

### Ausgabeobjekt
```
EqualLevelCluster {
  side: BUY_SIDE | SELL_SIDE
  reference_price: float
  members: [SwingPoint]           # >= 2
  timeframe: TF
  spread_atr: float               # max-min der Member, in ATR
  state: UNSWEPT | SWEPT | BROKEN
}
```

---

## 6. Liquidity Sweep (inkl. Reclaim)

**Sweep = Durchstich eines Liquidity Levels _mit_ anschließendem Reclaim.** Ohne Reclaim ist es
ein Bruch, kein Sweep.

### Bedingungen (auf `sweep_tf`)
Gegeben `L` = LiquidityLevel im Zustand `UNSWEPT`.

**a) Penetration** — es existiert eine `confirmed`-Bar `p` mit:
- BUY_SIDE (`L` über Hochs): `high[p] ≥ L.price + primitives.sweep.min_penetration_atr × ATR(sweep_tf)`
  **und** `high[p] ≤ L.price + primitives.sweep.max_penetration_atr × ATR(sweep_tf)`
- SELL_SIDE analog mit `low[p]`.

**b) Reclaim** — es existiert eine `confirmed`-Bar `r` mit `0 ≤ r.index − p.index ≤
primitives.sweep.max_reclaim_bars`, so dass:
- BUY_SIDE: `close[r] < L.price − primitives.sweep.min_reclaim_atr × ATR(sweep_tf)`
- SELL_SIDE: `close[r] > L.price + primitives.sweep.min_reclaim_atr × ATR(sweep_tf)`
- (Bar `p` selbst darf `= r` sein, wenn sie unter/über dem Level schließt.)

**c) Optional: Wick-Form** — `primitives.sweep.require_wick = true` (**PROPOSED DEFAULT `true`**):
die Penetrationsbewegung hinterlässt auf der Penetrationsseite von Bar `p..r` einen Docht mit
`wick_len / max(body_len, tick) ≥ primitives.sweep.min_wick_ratio`.

**Abgrenzung Sweep ↔ Breakout:** Wird `L` per `close` jenseits genommen und **kein** Reclaim
innerhalb `max_reclaim_bars` ⇒ `L.state = BROKEN`, **kein** Sweep-Objekt.

### Parameter
| Parameter | PROPOSED DEFAULT | Bedeutung |
|-----------|------------------|-----------|
| `primitives.sweep.min_penetration_atr` | `0.05` | Mindesttiefe des Durchstichs (ATR sweep_tf) |
| `primitives.sweep.max_penetration_atr` | `1.00` | Obergrenze — darüber = Breakout, kein Sweep |
| `primitives.sweep.max_reclaim_bars` | `3` | Frist für den Reclaim |
| `primitives.sweep.min_reclaim_atr` | `0.10` | wie weit der Reclaim-Close zurück muss |
| `primitives.sweep.require_wick` | `true` | Docht-Bedingung an/aus |
| `primitives.sweep.min_wick_ratio` | `1.5` | Docht : Körper |

*Warum validieren:* Dies ist die **zentrale Primitive von SMC-SWEEP-REV-01**. `max_penetration_atr`
trennt „Stop-Hunt" von „echtem Ausbruch" — ein falscher Wert macht das Setup entweder blind oder
fängt jeden Ausbruch als vermeintlichen Sweep. `max_reclaim_bars` bestimmt, wie „schnell" die
Ablehnung sein muss.

### Ausgabeobjekt
```
LiquiditySweep {
  level_ref: LiquidityLevel
  side: BUY_SIDE | SELL_SIDE
  timeframe: TF                   # sweep_tf
  penetration_bar: UTC
  penetration_extreme: float      # höchstes high / tiefstes low
  penetration_depth_atr: float
  reclaim_bar: UTC
  reclaim_close: float
  bars_to_reclaim: int
  wick_ratio: float
}
```

---

## 7. Displacement

**Displacement = impulsive, gerichtete Bewegung, die eine Imbalance erzeugt.**

### Bedingungen (auf `tf`)
Ein Displacement ist eine Folge von `n` aufeinanderfolgenden `confirmed`-Bars,
`1 ≤ n ≤ primitives.displacement.max_bars`, für die gilt:
1. **Netto-Bewegung:** `|close[last] − open[first]| ≥ primitives.displacement.min_atr × ATR(tf)`
2. **Körperdominanz:** `Σ|close−open| / Σ(high−low) ≥ primitives.displacement.min_body_ratio`
   über die `n` Bars
3. **Gerichtetheit:** alle `n` Bars haben dasselbe Vorzeichen von `close−open`, **oder** die
   Anzahl gegenläufiger Bars `≤ primitives.displacement.max_counter_bars`
4. **Imbalance:** die Sequenz erzeugt mindestens **eine** FVG gemäß §8

`direction = sign(close[last] − open[first])`.

### Parameter
| Parameter | PROPOSED DEFAULT | Bedeutung |
|-----------|------------------|-----------|
| `primitives.displacement.max_bars` | `3` | max. Länge der Impulssequenz |
| `primitives.displacement.min_atr` | `1.5` | Netto-Move in ATR(tf) |
| `primitives.displacement.min_body_ratio` | `0.60` | Summe Körper / Summe Range |
| `primitives.displacement.max_counter_bars` | `0` | erlaubte Gegenkerzen in der Sequenz |

*Warum validieren:* `min_atr = 1.5` ist ein häufig genannter Richtwert, aber stark
regime-/assetabhängig. In Low-Vol-Phasen ist 1.5·ATR selten; in High-Vol trivial. Deshalb wird
Displacement im Regime-Kontext bewertet (siehe `regime.md`).

### Ausgabeobjekt
```
Displacement {
  direction: BULLISH | BEARISH
  timeframe: TF
  start_bar: UTC
  end_bar: UTC
  bars: int
  net_move_atr: float
  body_ratio: float
  fvgs: [FVG]                     # >= 1, in Reihenfolge
  caused_structure_break: StructureBreak?   # BOS/CHoCH, falls vorhanden
}
```

---

## 8. FVG — Fair Value Gap

**Bullische FVG:** 3 aufeinanderfolgende `confirmed`-Bars `(1, 2, 3)` mit `low[3] > high[1]`.
Die Zone ist `[high[1], low[3]]`. Bar 2 ist typischerweise die Displacement-Bar.
**Bearische FVG:** `high[3] < low[1]`. Zone `[high[3], low[1]]`.

### Gültigkeit
- **Größe:** `zone_height = |low[3] − high[1]|` (bzw. spiegelbildlich) `≥ max(
  primitives.fvg.min_size_atr × ATR(tf) , primitives.fvg.min_size_pct × price ,
  primitives.fvg.min_size_ticks × tick_size )`
- **Displacement-Kopplung (optional):** `primitives.fvg.require_displacement`
  (**PROPOSED DEFAULT `true` für Setup-Nutzung**) — die FVG muss innerhalb eines Displacement
  (§7) liegen, dessen Richtung = FVG-Richtung.
- **Alter:** `age_bars ≤ primitives.fvg.max_age_bars` (**PROPOSED DEFAULT `50`** auf `tf`), sonst
  `state = STALE` (nicht mehr handelbar, aber weiter als Level geführt).

### Zustand & Mitigation (siehe §11)
`state ∈ { UNMITIGATED, PARTIAL, MITIGATED, STALE, INVERTED }`
- `fill_fraction` = Anteil der Zonenhöhe, den der Preis seit Entstehung durchlaufen hat (0..1).
- `PARTIAL` ⇔ `0 < fill_fraction < primitives.mitigation.consumed_threshold`
- `MITIGATED` ⇔ `fill_fraction ≥ primitives.mitigation.consumed_threshold`
- `INVERTED` ⇔ Bedingung §9 erfüllt.

### Parameter
| Parameter | PROPOSED DEFAULT | Bedeutung |
|-----------|------------------|-----------|
| `primitives.fvg.min_size_atr` | `0.20` | Mindesthöhe in ATR(tf) |
| `primitives.fvg.min_size_pct` | `0.05 %` | alternativ relativ |
| `primitives.fvg.min_size_ticks` | `4` | alternativ absolut |
| `primitives.fvg.require_displacement` | `true` | Kopplung an §7 |
| `primitives.fvg.max_age_bars` | `50` | ab hier `STALE` |

*Warum validieren:* `min_size_atr` filtert Mikro-Gaps, die sofort gefüllt werden. Zu groß ⇒ zu
wenige Entries. Der Wert interagiert direkt mit `displacement.min_atr`.

### Ausgabeobjekt
```
FVG {
  direction: BULLISH | BEARISH
  timeframe: TF
  zone_low: float
  zone_high: float
  zone_mid: float
  created_bar: UTC                # Bar 3
  displacement_ref: Displacement?
  state: UNMITIGATED | PARTIAL | MITIGATED | STALE | INVERTED
  fill_fraction: float
}
```

---

## 9. IFVG — Inverse Fair Value Gap

Eine FVG wird zur **IFVG**, wenn der Preis sie **durchhandelt und auf der Gegenseite schließt**:
- bullische FVG ⇒ bearische IFVG, wenn `confirmed close < zone_low −
  primitives.ifvg.min_close_through_atr × ATR(tf)`
- bearische FVG ⇒ bullische IFVG, wenn `confirmed close > zone_high + … × ATR(tf)`

Die IFVG behält dieselbe Zone, aber invertierte Polarität (wirkt nun als Widerstand/Unterstützung
in Gegenrichtung). `flipped_at` wird gesetzt. Gültigkeit wie FVG (`max_age_bars` ab `flipped_at`).

### Parameter
| Parameter | PROPOSED DEFAULT | Bedeutung |
|-----------|------------------|-----------|
| `primitives.ifvg.min_close_through_atr` | `0.0` | Mindest-Durchbruch für den Flip |
| `primitives.ifvg.max_age_bars` | `50` | Lebensdauer nach Flip |

*Warum validieren:* Ob ein sofortiger Wick-Durchbruch schon einen Flip auslöst oder erst ein
klarer Schlusskurs, ändert, wie oft IFVGs entstehen und wie verlässlich sie halten.

### Ausgabeobjekt
```
IFVG {
  origin_fvg_ref: FVG
  direction: BULLISH | BEARISH    # invertiert ggü. origin
  timeframe, zone_low, zone_high, zone_mid
  flipped_at: UTC
  state: UNMITIGATED | PARTIAL | MITIGATED | STALE
}
```

---

## 10. Order Block

**Bullischer Order Block:** die **letzte Bar mit `close < open`** (Down-Close) unmittelbar vor
einem bullischen Displacement (§7), das einen BOS/CHoCH verursacht.
**Bearischer Order Block:** die letzte Bar mit `close > open` vor einem bearischen Displacement
mit Strukturbruch.

### Gültigkeitsbedingungen
1. **Strukturkopplung:** innerhalb `primitives.ob.max_bars_to_break` (**PROPOSED DEFAULT `5`**)
   Bars nach der OB-Bar tritt ein `StructureBreak` (§2/§3) in Displacement-Richtung auf.
2. **Displacement-Kopplung:** die Bewegung direkt nach der OB-Bar erfüllt §7.
3. **Unberührt:** `state = UNMITIGATED` (§11) zum Zeitpunkt der Betrachtung.

### Zone (`primitives.ob.zone`)
| Wert | Zone (bullischer OB) |
|------|----------------------|
| `full_range` (**PROPOSED DEFAULT**) | `[low, high]` der OB-Bar |
| `body` | `[min(open,close), max(open,close)]` |
| `open_to_extreme` | `[low, open]` |

*Warum validieren:* `full_range` füllt häufiger (frühere Fills, mehr Trades, aber schlechterer
Preis). `body`/`open_to_extreme` geben bessere Entries, werden aber öfter „verpasst". Klassischer
Trade-off, der nur empirisch entscheidbar ist.

### Ausgabeobjekt
```
OrderBlock {
  direction: BULLISH | BEARISH
  timeframe: TF
  zone_low: float
  zone_high: float
  ob_bar: UTC
  break_ref: StructureBreak
  displacement_ref: Displacement
  state: UNMITIGATED | PARTIAL | MITIGATED | STALE
  fill_fraction: float
}
```

---

## 11. Mitigation

**Mitigation = Rückkehr des Preises in eine Zone (FVG / OB / Breaker / IFVG) und deren
teilweiser oder vollständiger „Verbrauch".**

### Messung
Für eine Zone `[zl, zh]` (Höhe `H = zh − zl`) und die seit Entstehung beobachteten Extrempreise:
- bullische Zone (Support, Preis kommt von oben): `penetration = clip((zh − min_low_since) / H, 0, 1)`
- bearische Zone (Resistance, Preis kommt von unten): `penetration = clip((max_high_since − zl) / H, 0, 1)`

`fill_fraction = penetration`.

### Zustandsschwellen (`primitives.mitigation`)
| Parameter | PROPOSED DEFAULT | Bedeutung |
|-----------|------------------|-----------|
| `primitives.mitigation.touch_threshold` | `0.0` | > 0 ⇒ `PARTIAL` |
| `primitives.mitigation.consumed_threshold` | `0.5` | ≥ ⇒ `MITIGATED` (Zone „verbraucht", nicht mehr für Entries) |
| `primitives.mitigation.full_threshold` | `1.0` | Zone vollständig durchlaufen |

*Warum validieren:* `consumed_threshold = 0.5` ist die verbreitete „50 %-Regel". Ob eine Zone nach
30 %, 50 % oder 70 % Fill ihre Wirkung verliert, ist eine empirische Frage und vermutlich
regimeabhängig.

---

## 12. Breaker Block

**Ein Breaker ist ein Order Block, dessen schützende Struktur gebrochen wurde und der daraufhin
seine Polarität umkehrt.**

### Konstruktion (bullischer Breaker)
1. Es existierte ein **bearischer** Order Block `OB⁻` an einem Hoch (gebildet vor einem
   bearischen Displacement).
2. Der Preis bricht später per bullischem BOS (§2) über das Hoch, das `OB⁻` schützte
   (`close > OB⁻.zone_high + primitives.breaker.buffer_atr × ATR(tf)`).
3. `OB⁻` wird zum **bullischen Breaker**: dieselbe Zone `[OB⁻.zone_low, OB⁻.zone_high]` wirkt bei
   einem Retrace von oben nun als **Unterstützung**.

Bearischer Breaker spiegelbildlich.

### Parameter
| Parameter | PROPOSED DEFAULT | Bedeutung |
|-----------|------------------|-----------|
| `primitives.breaker.buffer_atr` | `0.0` | Mindest-Durchbruch der schützenden Struktur |
| `primitives.breaker.max_age_bars` | `50` | Lebensdauer nach dem Flip |

### Ausgabeobjekt
```
Breaker {
  origin_ob_ref: OrderBlock
  direction: BULLISH | BEARISH     # invertiert
  timeframe, zone_low, zone_high
  flip_break_ref: StructureBreak
  flipped_at: UTC
  state: UNMITIGATED | PARTIAL | MITIGATED | STALE
}
```

---

## 13. Premium / Discount

Gegeben eine **Reference Range** `[range_low, range_high]` (§0.5) mit
`equilibrium = range_low + 0.5 × (range_high − range_low)`:

`pd_position = (price − range_low) / (range_high − range_low)` ∈ [0, 1]

| Zone | Bedingung | Bedeutung |
|------|-----------|-----------|
| **DISCOUNT** | `pd_position ≤ primitives.pd.discount_max` | „billig" — bevorzugt für Longs |
| **EQUILIBRIUM** | `discount_max < pd_position < premium_min` | Niemandsland — **kein bevorzugter Entry** |
| **PREMIUM** | `pd_position ≥ primitives.pd.premium_min` | „teuer" — bevorzugt für Shorts |

### Parameter
| Parameter | PROPOSED DEFAULT | Bedeutung |
|-----------|------------------|-----------|
| `primitives.pd.discount_max` | `0.45` | Obergrenze der Discount-Zone |
| `primitives.pd.premium_min` | `0.55` | Untergrenze der Premium-Zone |
| `primitives.pd.reference` | `dealing_range` | `dealing_range` \| `last_impulse_leg` \| `session_range` \| `swept_leg` |
| `primitives.pd.reference_tf` | *= Setup-HTF* | Timeframe der Reference Range |

**`swept_leg`** (setup-spezifisch relevant): die Range vom Sweep-Extrem (§6) bis zum
Displacement-Extrem (§7). Für SMC-SWEEP-REV-01 wird die Entry-Location gegen `swept_leg` geprüft.

*Warum validieren:* Ein Equilibrium-Band von 0.45–0.55 ist willkürlich; die Breite steuert, wie
viele Entries als „Niemandsland" verworfen werden. Die **Wahl der Reference Range** ist der
größte Hebel überhaupt — dieselbe Kerze kann je nach Bezug „Discount" oder „Premium" sein.

### Ausgabeobjekt
```
PremiumDiscount {
  reference: dealing_range | last_impulse_leg | session_range | swept_leg
  reference_tf: TF
  range_low, range_high, equilibrium: float
  pd_position: float               # 0..1
  zone: DISCOUNT | EQUILIBRIUM | PREMIUM
}
```

---

## 14. Verbot subjektiver Begriffe — Mapping

| Verbotener Begriff | Ersetzt durch (Parameter) |
|--------------------|---------------------------|
| „starker Swing" | `swing.min_leg_atr` |
| „sauberer Bruch" | `bos.confirmation = close` + `bos.buffer_atr` |
| „signifikante Liquidität" | `LiquidityLevel.strength ≥ <Schwelle>` (§4.2) |
| „klarer Sweep" | `sweep.min/max_penetration_atr` + `sweep.max_reclaim_bars` + `sweep.min_wick_ratio` |
| „starkes Displacement" | `displacement.min_atr` + `displacement.min_body_ratio` (+ Regime-Kontext) |
| „gültige FVG" | `fvg.min_size_atr` + `fvg.require_displacement` + `fvg.max_age_bars` |
| „frischer Order Block" | `OrderBlock.state = UNMITIGATED` + `ob.max_bars_to_break` |
| „tiefer Discount" | `pd_position ≤ pd.discount_max` (+ engere setup-spezifische Schwelle) |
| „schnelle Ablehnung" | `sweep.max_reclaim_bars` |
| „gut ausgebildete Struktur" | `structure.min_swings` + `SwingPoint.confirmed` |

---

## 15. Parameter-Sammelverweis

Alle hier genannten `PROPOSED DEFAULT`-Werte werden später zentral in
`config/primitives.example.yaml` geführt (noch nicht erstellt — kein ausführbarer Code in dieser
Phase). Die vollständige Inventarliste inkl. Validierungsstatus steht in `anti-overfitting.md`.
