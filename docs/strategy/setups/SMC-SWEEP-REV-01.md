# Setup-Spezifikation: SMC-SWEEP-REV-01

**Setup-ID:** `SMC-SWEEP-REV-01`
**Klasse:** Liquidity-Sweep-Reversal / HTF-aligned Continuation-after-Stop-Hunt
**Strategy-Version:** `0.1.1` — **eingefroren am 2026-08-28** (siehe `../DECISIONS-0.1.1.md`;
`0.1.1`-Änderungen: §8 Location-Wortlaut C4 · §16.3 S/R-Proxy C8 · §21 Output `WAIT` C6 ·
§24 Anzeige-Aliase C5)
**MVP-Instrumente:** BTCUSDT, ETHUSDT · **HTF:** D1 + H4 · **Sweep-TF:** M15 · **Struktur/Entry-TF:** M5
**Multi-Asset:** die Spezifikation ist assetneutral; alle Zahlen sind pro Instrument
überschreibbar (`setups.SMC-SWEEP-REV-01.<param>` und `instruments.<symbol>.setups.<id>.<param>`).

> Alle Zahlen sind `PROPOSED DEFAULT` — Startwerte, keine finalen Werte. Validierung: siehe
> `anti-overfitting.md` und `backtest-labeling.md`.

---

## 0. Kausale Kette (die einzige zulässige Logik)

Dieses Setup ist **keine Summe unabhängiger Indikatoren**. Es ist eine **Ereigniskette mit
strikter Reihenfolge**. Jedes Glied ist Voraussetzung des nächsten. Fällt ein Glied aus, endet
die Kette — es gibt **keine** Kompensation durch andere Faktoren.

```
(1) HTF-Regime erlaubt + (2) HTF-Bias gerichtet
        │  definiert Handelsrichtung  D
        ▼
(3) Es existiert eine relevante Liquidität GEGEN Richtung D
    (bei Bias long: Sell-Side unter einem Tief; bei Bias short: Buy-Side über einem Hoch)
        │
        ▼
(4) Diese Liquidität wird gesweept  (Durchstich, begrenzt tief)
        │
        ▼
(5) Reclaim  (Schlusskurs zurück auf die Ursprungsseite, schnell)
        │   → die Bewegung gegen D ist gescheitert = Stop-Hunt bestätigt
        ▼
(6) Displacement in Richtung D  (impulsiv, erzeugt FVG)
        │
        ▼
(7) CHoCH/BOS in Richtung D auf dem Struktur-TF  (Strukturbeweis der Umkehr/Fortsetzung)
        │   → hinterlässt Origin-Imbalance: FVG (primär) oder Order Block (Fallback)
        ▼
(8) Preis retraced in die Origin-Zone, die im DISCOUNT (long) / PREMIUM (short)
    des gesweepten Legs liegt
        │
        ▼
(9) Entry  →  (10) SL hinter dem Sweep-Extrem
        │
        ▼
Ziel: gegenüberliegende Liquidität  (TP1 → TP2 → TP3)
```

**Handelsrichtung `D`:** immer = Richtung des HTF-Bias = Richtung von Reclaim/Displacement/CHoCH.
Die gesweepte Liquidität liegt **immer entgegen** `D` (sie ist der „Treibstoff", den der Markt vor
der Bewegung in Richtung `D` abholt).

---

## 1. Marktregime (Gate)

Quelle: `regime.md`. Freigabe nur, wenn **alle** gelten:

| Bedingung | Anforderung |
|-----------|-------------|
| HTF-merged `directional` | ∈ { `TREND_UP`, `TREND_DOWN`, `RANGE` } |
| `directional` ≠ | `UNCLEAR`, `CONFLICTING` |
| `volatility` (D1, H4, M15) | ∈ { `NORMAL`, `HIGH` }; **kein** `EXTREME`, **kein** `LOW` |
| `phase` | ∈ { `NEUTRAL`, `EXPANSION` }; **keine** reine/`coiled` `COMPRESSION` |
| Regime-Cooldown | nicht aktiv (`bars_in_state ≥ regime.cooldown_bars`) |

**Range-Variante:** bei HTF `RANGE` muss die gesweepte Liquidität eine **Range-Grenze** sein
(`range_high` für Short, `range_low` für Long) und der Trade zielt zurück zur Gegen-Grenze /
Equilibrium.

Verletzung ⇒ `NO_TRADE` mit Grund aus `RegimeNoTradeReason`.

---

## 2. HTF Bias (Gate + Score-Input)

Berechnet auf **D1 und H4** (Regime-States aus `regime.md`).

`bias` ∈ { `LONG`, `SHORT`, `NONE` }:
- `LONG` ⇔ `merged_htf.directional ∈ {TREND_UP}` **oder**
  (`{TREND_UP, RANGE}`-Mix **und** aktueller Preis in `DISCOUNT` der H4-`dealing_range`)
- `SHORT` ⇔ spiegelbildlich
- sonst `NONE` ⇒ `NO_TRADE` (`BIAS_NONE`)

**Bias-Stärke** `bias_strength` ∈ [0,1] =
`0.6 × trend_strength(merged) + 0.4 × (1 − disagreement(D1,H4))`
(`trend_strength`, `disagreement` aus `regime.md`).

Für die **Range-Variante**: `bias` = Richtung **weg von der gesweepten Range-Grenze**;
`bias_strength = range_maturity_score × (1 − disagreement)`.

| Parameter | PROPOSED DEFAULT | Bedeutung |
|-----------|------------------|-----------|
| `setups.SMC-SWEEP-REV-01.bias.min_strength` | `0.35` | unter diesem Wert ⇒ `NO_TRADE` (`BIAS_TOO_WEAK`) |

*Warum validieren:* Die Mix-Regel („Trend auf einem TF, Range auf dem anderen ⇒ Bias nur aus
Discount/Premium") ist eine Design-Entscheidung, die die Trade-Frequenz stark beeinflusst.
`min_strength = 0.35` ist ein grober Startfilter.

---

## 3. Relevante Liquidität (Gate)

Es muss **mindestens ein** `LiquidityLevel` (`primitives.md` §4) existieren, das **alle**
Bedingungen erfüllt:

| Bedingung | Anforderung |
|-----------|-------------|
| Seite | entgegen `D` (Bias `LONG` ⇒ `SELL_SIDE`; Bias `SHORT` ⇒ `BUY_SIDE`) |
| Typ | ∈ `setups.SMC-SWEEP-REV-01.liquidity.allowed_types` — **PROPOSED DEFAULT** `[equal_lows, equal_highs, session_low, session_high, pdl, pdh, pwl, pwh, swing_low, swing_high]` (Swings nur von `tf ∈ [H1, H4]`) |
| Stärke | `strength ≥ setups.SMC-SWEEP-REV-01.liquidity.min_strength` — **PROPOSED DEFAULT `0.40`** |
| Zustand | `state = UNSWEPT` |
| Frische | nicht innerhalb der letzten `liquidity.freshness_bars` (**PROPOSED DEFAULT `50`** H1-Bars) bereits gesweept/gebrochen |
| Distanz | `|price − level.price| ≤ liquidity.max_distance_atr × ATR(H1)` — **PROPOSED DEFAULT `5.0`** |
| Ziel-Raum | auf der **Gegenseite** von `level` existiert eine opposing-Liquidität in ≥ `min_target_room_r` (siehe §16), sonst kein sinnvolles Ziel |

Existiert kein solches Level ⇒ `NO_TRADE` (`NO_QUALIFYING_LIQUIDITY`).

*Warum validieren:* `min_strength`, `max_distance_atr` und die Typenliste bestimmen gemeinsam die
Setup-Häufigkeit. Zu permissiv ⇒ viele schwache Sweeps; zu streng ⇒ kaum Trades. Muss je
Instrument kalibriert werden.

---

## 4. Sweep-Bedingung (Kettenglied, Gate)

Auf **Sweep-TF = M15** (`setups.SMC-SWEEP-REV-01.sweep.timeframe`).
Gemäß `primitives.md` §6, mit **setup-spezifischen Grenzen**:

- **Penetration:** `min_penetration_atr ≤ Durchstichtiefe ≤ max_penetration_atr` (ATR M15)
- **Frist:** Reclaim innerhalb `sweep.max_reclaim_bars`
- **Docht:** `wick_ratio ≥ sweep.min_wick_ratio` (falls `require_wick`)
- **Kein Breakout:** hätte der Preis nach dem Durchstich per `close` jenseits gehalten
  (`> max_reclaim_bars` Bars) ⇒ **Kettenabbruch**, `NO_TRADE` (`SWEEP_BECAME_BREAKOUT`)

| Parameter | PROPOSED DEFAULT | Bedeutung |
|-----------|------------------|-----------|
| `sweep.timeframe` | `M15` | |
| `sweep.min_penetration_atr` | `0.05` | Mindest-Durchstich |
| `sweep.max_penetration_atr` | `1.00` | darüber = Breakout |
| `sweep.max_reclaim_bars` | `3` | Reclaim-Frist |
| `sweep.min_wick_ratio` | `1.5` | Docht:Körper |
| `sweep.require_wick` | `true` | |
| `sweep.max_pools_in_window` | `2` | mehr als 2 gesweepte Pools im Fenster ⇒ „messy", Negativfaktor (§21), ab `3` Veto |

*Warum validieren:* `max_penetration_atr = 1.0` ist **die** kritische Grenze des Setups. Sie
trennt Stop-Hunt von echtem Ausbruch. Der richtige Wert ist mit hoher Wahrscheinlichkeit
regime- und assetabhängig und muss über eine Sensitivitätskurve bestimmt werden.

---

## 5. Reclaim-Bedingung (Kettenglied, Gate)

Auf Sweep-TF M15:
- eine `confirmed`-Bar schließt zurück auf die Ursprungsseite des Levels um
  `≥ reclaim.min_close_beyond_atr × ATR(M15)`
- innerhalb `sweep.max_reclaim_bars` nach der Penetrationsbar
- Reclaim-Bar-Körperanteil `body/range ≥ reclaim.min_body_ratio`
- **optional** `reclaim.require_opposite_color = true`: Reclaim-Bar hat die Farbe von `D`

| Parameter | PROPOSED DEFAULT | Bedeutung |
|-----------|------------------|-----------|
| `reclaim.min_close_beyond_atr` | `0.10` | wie weit zurück der Close muss |
| `reclaim.min_body_ratio` | `0.50` | Körperanteil der Reclaim-Bar |
| `reclaim.require_opposite_color` | `true` | Reclaim-Bar in Richtung `D` |

Kein Reclaim in der Frist ⇒ `NO_TRADE` (`NO_RECLAIM`).

---

## 6. Displacement (Kettenglied, Gate)

Auf Sweep-TF M15 **oder** Struktur-TF M5 (`displacement.timeframe` — **PROPOSED DEFAULT `M15`**).
Gemäß `primitives.md` §7, Richtung = `D`, **beginnend spätestens**
`displacement.max_bars_after_reclaim` Bars nach der Reclaim-Bar.

- `net_move_atr ≥ primitives.displacement.min_atr`
- `body_ratio ≥ primitives.displacement.min_body_ratio`
- erzeugt **≥ 1 FVG** in Richtung `D`
- **muss** den Strukturbruch aus §7 verursachen (sonst Kettenabbruch)

| Parameter | PROPOSED DEFAULT | Bedeutung |
|-----------|------------------|-----------|
| `displacement.timeframe` | `M15` | |
| `displacement.max_bars_after_reclaim` | `3` | Frist bis zum Displacement |
| `displacement.min_atr` | `1.5` | (übernimmt `primitives.displacement.min_atr`, hier überschreibbar) |

Kein qualifizierendes Displacement in der Frist ⇒ `NO_TRADE` (`NO_DISPLACEMENT`).

---

## 7. CHoCH / BOS (Kettenglied, Gate)

Auf **Struktur-TF = M5** (`structure.timeframe`).

- **Wenn** der lokale (M5-)Zustand vor dem Sweep **gegen `D`** gerichtet war
  (typischer Fall: kurzfristiger Gegen-Trend-Move in die Liquidität hinein) ⇒ erwartet wird ein
  **CHoCH in Richtung `D`**: `confirmed close` jenseits des letzten gegen-`D`-Swings
  (`primitives.md` §3).
- **Wenn** der M5-Zustand bereits **in Richtung `D`** war (Continuation-Fall) ⇒ erwartet wird ein
  **BOS in Richtung `D`** (`primitives.md` §2).
- Der Bruch **muss** vom Displacement (§6) getragen sein (`caused_structure_break` gesetzt).
- **Distanzfilter:** der gebrochene Swing darf nicht weiter als
  `structure.max_break_distance_atr × ATR(M5)` entfernt gewesen sein (sonst „überdehnter" Bruch).

| Parameter | PROPOSED DEFAULT | Bedeutung |
|-----------|------------------|-----------|
| `structure.timeframe` | `M5` | |
| `structure.max_break_distance_atr` | `4.0` | max. Abstand zum gebrochenen Swing |
| `structure.confirmation` | `close` | `close` \| `wick` |

Kein Bruch in Richtung `D` innerhalb `structure.max_bars_after_displacement`
(**PROPOSED DEFAULT `3`**) ⇒ `NO_TRADE` (`NO_STRUCTURE_SHIFT`).

---

## 8. Entry-Zone (Gate)

**Primär:** die **jüngste unberührte FVG** in Richtung `D` aus dem Displacement (§6), auf
`entry.timeframe` (**PROPOSED DEFAULT `M5`**).
**Fallback:** wenn keine gültige FVG ⇒ der **Order Block** am Displacement-Ursprung
(`primitives.md` §10), wenn `entry.allow_ob_fallback = true` (**PROPOSED DEFAULT `true`**).
**Kein** gültiges Objekt ⇒ `NO_TRADE` (`NO_ENTRY_ZONE`).

**Location-Gate (hart, Veto V2 — `0.1.1` C4: numerischer Gate maßgeblich):** der **Mittelpunkt
der Entry-Zone**, gemessen gegen das **gesweepte Leg** (`primitives.pd.reference = swept_leg`),
muss liegen:
- Bias `LONG`: `pd_position(zone_mid) ≤ entry.max_pd_position` — **PROPOSED DEFAULT `0.50`**
  (d. h. **auf oder unter dem Equilibrium** in Trade-Richtung; strengerer Wert ⇒ echter Discount)
- Bias `SHORT`: `pd_position(zone_mid) ≥ 1 − entry.max_pd_position`

Verletzung ⇒ `NO_TRADE` (`ENTRY_WRONG_SIDE_OF_EQUILIBRIUM`).

**Weitere Zonenfilter:**
- `zone_height ≥ entry.min_zone_height_atr × ATR(entry.timeframe)` — **PROPOSED DEFAULT `0.15`**
  (zu dünne Zonen sind Fill-Rauschen)
- Zone `state = UNMITIGATED` (noch nicht über `mitigation.consumed_threshold` gefüllt)
- Zone nicht `STALE`

| Parameter | PROPOSED DEFAULT | Bedeutung |
|-----------|------------------|-----------|
| `entry.timeframe` | `M5` | |
| `entry.allow_ob_fallback` | `true` | OB als Ersatz für fehlende FVG |
| `entry.max_pd_position` | `0.50` | wie tief im Discount die Zone liegen muss |
| `entry.min_zone_height_atr` | `0.15` | Mindesthöhe der Entry-Zone |

*Warum validieren:* `max_pd_position = 0.50` erlaubt Entries bis genau Equilibrium. Ein strengerer
Wert (z. B. 0.35) verbessert das RR, senkt aber die Fill-Rate deutlich. Die Wahl der
`swept_leg`-Reference ist hier bewusst gesetzt und muss gegen `dealing_range` getestet werden.

---

## 9. Entry-Typ

**PROPOSED DEFAULT:** `limit_at_proximal_edge`.

| Modus (`entry.mode`) | Verhalten |
|----------------------|-----------|
| `limit_at_proximal_edge` (**DEFAULT**) | Limit-Order an der dem Preis zugewandten Kante der Zone |
| `limit_at_mid` | Limit an `zone_mid` (besserer Preis, geringere Fill-Rate) |
| `confirmation_market` | warte auf M1-Bestätigung **in** der Zone (Minor-CHoCH M1 **oder** Engulfing/Pin gegen die Zone), dann Market |

- Die Order wird **erst platziert**, wenn der Zustand `ARMED` erreicht ist (§ State Machine).
- Nur **eine** aktive Entry-Order pro Setup-Instanz.
- Die Order ist gültig, solange die Zone `UNMITIGATED` ist **und** die Trade-Expiry (§15) nicht
  erreicht ist.

| Parameter | PROPOSED DEFAULT | Bedeutung |
|-----------|------------------|-----------|
| `entry.mode` | `limit_at_proximal_edge` | |
| `entry.confirmation_tf` | `M1` | nur bei `confirmation_market` |

---

## 10. Stop-Loss

`SL` = der **ungünstigere** (weiter entfernte) der beiden Kandidaten, danach Cap:

1. **Sweep-basiert:** hinter dem Sweep-Extrem (`LiquiditySweep.penetration_extreme`) um
   `sl.buffer_atr × ATR(sweep.timeframe)` — **PROPOSED DEFAULT `0.50`**
2. **Zonen-basiert:** hinter der distalen Kante der Entry-Zone um denselben Puffer

Dann:
- **Cap:** `distance(entry, SL) ≤ sl.max_distance_atr × ATR(sweep.timeframe)` —
  **PROPOSED DEFAULT `3.0`**. Überschreitung ⇒ `NO_TRADE` (`SL_TOO_WIDE`).
- **Floor:** `distance(entry, SL) ≥ max( sl.min_distance_atr × ATR(sweep.timeframe) ,
  sl.min_spread_multiple × current_spread )` — **PROPOSED DEFAULTS `0.40` bzw. `5`**.
  Unterschreitung ⇒ `NO_TRADE` (`SL_TOO_TIGHT`).

**Der SL definiert den maximal zulässigen Verlust (`1R`).** Hebel/Margin ändern `1R` **nicht**
(siehe `sizing.md`).

| Parameter | PROPOSED DEFAULT | Bedeutung |
|-----------|------------------|-----------|
| `sl.buffer_atr` | `0.50` | Abstand hinter Extrem/Zone |
| `sl.max_distance_atr` | `3.0` | Cap |
| `sl.min_distance_atr` | `0.40` | Floor (ATR) |
| `sl.min_spread_multiple` | `5` | Floor (Spread) |

*Warum validieren:* `buffer_atr = 0.5` ist ein häufiger Richtwert gegen „Stop direkt am Wick".
Zu klein ⇒ Ausstoppen im Rauschen; zu groß ⇒ RR sinkt. Interagiert direkt mit `min_rr` (§16).

---

## 11. Strukturelle Invalidierung (≠ Stop-Loss)

Details & Präzedenz in `invalidation.md`. Für dieses Setup konkret:

### Pre-Entry (Kandidat `ARMED`, Order nicht gefüllt) ⇒ **Kandidat abbrechen**
| Auslöser | Bedingung |
|----------|-----------|
| Re-Sweep | `confirmed close` erneut jenseits des Sweep-Extrems |
| Gegen-Displacement | Displacement gegen `D` auf `displacement.timeframe` |
| Gegen-CHoCH | CHoCH gegen `D` auf `structure.timeframe` |
| Bias-Flip | `HTF bias` wechselt oder wird `NONE` |
| Zone verbraucht | Entry-Zone erreicht `MITIGATED` ohne Fill |
| Regime-Verlust | Regime-Gate (§1) nicht mehr erfüllt |
| Expiry | §15 |

### Post-Entry ⇒ **sofortiger Exit zum Markt** (auch wenn SL nicht getroffen)
| Auslöser | Bedingung |
|----------|-----------|
| Reclaim-These gebrochen | `confirmed close` auf `structure.timeframe` jenseits des Sweep-Extrems |
| Gegen-CHoCH | CHoCH gegen `D` auf `structure.timeframe` |
| HTF-Bias-Flip | `merged_htf.directional` dreht in Gegenrichtung (`confirmed`) |

`exit_reason = STRUCT_INVALIDATION`.

---

## 12. TP1

- **Ziel:** nächstgelegene **opposing** Liquidität (`primitives.md` §4) in Richtung `D`
  (Bias `LONG` ⇒ nächste `BUY_SIDE`-Liquidität über dem Entry), **begrenzt** durch
  `max(entry ± tp1.r_multiple × R)`.
- **Mindestens:** `tp1.min_r × R`.
- **Teilgröße:** `tp1.size_pct` der Position.
- **Bei TP1-Fill:** SL → Break-even `+ be.buffer_atr × ATR(entry.timeframe)`.

| Parameter | PROPOSED DEFAULT | Bedeutung |
|-----------|------------------|-----------|
| `tp1.r_multiple` | `1.5` | Cap in R |
| `tp1.min_r` | `1.0` | Mindest-Distanz in R |
| `tp1.size_pct` | `50` | Anteil, der bei TP1 geschlossen wird |
| `be.buffer_atr` | `0.10` | Break-even-Offset nach TP1 |

---

## 13. TP2

- **Ziel:** die **nächste signifikante opposing Liquidität** (`strength ≥ 0.5`) oder ein
  H4/M15-Struktur-Level in Richtung `D`, **begrenzt** durch `tp2.r_multiple × R`.
- **Teilgröße:** `tp2.size_pct`.
- **Nach TP2-Fill:** Rest wird **getrailt** (§14).

| Parameter | PROPOSED DEFAULT | Bedeutung |
|-----------|------------------|-----------|
| `tp2.r_multiple` | `3.0` | Cap in R |
| `tp2.min_r` | `2.0` | Mindest-Distanz in R |
| `tp2.size_pct` | `25` | |

---

## 14. TP3 (Runner)

- **Kein fester Preis.** Rest-Position (`100 − tp1.size_pct − tp2.size_pct`, **DEFAULT 25 %**).
- **Exit** durch **einen** von:
  - **Trailing-Stop:** Swing-Trailing auf `trail.timeframe` (**PROPOSED DEFAULT `M15`**) —
    SL folgt dem jeweils letzten bestätigten HL (long) / LH (short) `−/+ trail.buffer_atr`
    (**PROPOSED DEFAULT `0.30`** ATR).
  - **Strukturelle Invalidierung** (§11 Post-Entry).
  - **HTF-opposing-Liquidität** erreicht (D1/H4-Pool) ⇒ vollständiger Exit.
  - **Trade-Expiry** (§15).

| Parameter | PROPOSED DEFAULT | Bedeutung |
|-----------|------------------|-----------|
| `tp3.size_pct` | `25` | Runner-Anteil |
| `trail.timeframe` | `M15` | Timeframe der Trailing-Swings |
| `trail.buffer_atr` | `0.30` | Abstand hinter dem Trailing-Swing |
| `trail.activate_after` | `TP2` | Trailing beginnt nach TP2-Fill |

---

## 15. Trade Expiry

| Phase | Regel | PROPOSED DEFAULT |
|-------|-------|------------------|
| **Kandidat `ARMED`, kein Fill** | verfällt nach `expiry.armed_bars` Bars auf `entry.timeframe` | `12` (M5-Bars ≈ 1 h) |
| **Kandidat `ARMED`** | verfällt bei Session-Grenze, wenn `expiry.expire_at_session_end = true` | `true` (für Crypto: bei Wechsel des primären Session-Fensters) |
| **Offener Trade, nicht bei TP1** | „Dead-Trade-Exit" nach `expiry.max_holding_bars` Bars auf `entry.timeframe` ⇒ Market-Exit, `exit_reason = TIME_EXPIRY` | `96` (M5-Bars ≈ 8 h) |
| **Offener Trade, TP1 erreicht** | kein Zeit-Exit; nur Trailing/Invalidierung/HTF-Ziel | — |

*Warum validieren:* `max_holding_bars` begrenzt Opportunitätskosten und Overnight-Exposure.
Der Wert ist eng an den Entry-Timeframe gekoppelt und muss mit der MFE/MAE-Verteilung
(`backtest-labeling.md`) abgeglichen werden.

---

## 16. Mindest-RR (Gate)

Alle drei Bedingungen (sonst `NO_TRADE`, `RR_BELOW_MIN`):

1. `RR_to_TP2 = distance(entry, TP2) / distance(entry, SL) ≥ rr.min_to_tp2` — **PROPOSED DEFAULT `2.0`**
2. `blended_RR = Σ (size_pct_i × R_i) / 100 ≥ rr.min_blended` — **PROPOSED DEFAULT `1.3`**
   (mit `R_i` = R-Distanz zu TP_i; TP3 konservativ mit `rr.tp3_assumed_r` = **PROPOSED DEFAULT `2.5`**)
3. **Ziel-Raum:** Distanz vom Entry zur **ersten** opposing Liquidität (`primitives.md` §4 —
   dient auch als S/R-Proxy, `0.1.1` C8) `≥ rr.min_target_room_r × R` — **PROPOSED DEFAULT `1.5`**.
   (Kein Entry, wenn direkt über dem Einstieg schon eine starke Gegen-Liquidität klebt.)

| Parameter | PROPOSED DEFAULT |
|-----------|------------------|
| `rr.min_to_tp2` | `2.0` |
| `rr.min_blended` | `1.3` |
| `rr.tp3_assumed_r` | `2.5` |
| `rr.min_target_room_r` | `1.5` |

---

## 17. News-Filter (Veto V4)

Quelle: `news-rules.md`. Für BTCUSDT/ETHUSDT relevante Events (Routing dort definiert):
USD-Makro (FOMC, CPI, Core CPI, NFP, PCE, GDP) + Crypto-spezifisch (große Token-Unlocks,
Exchange-Incidents, Regulierungs-/ETF-Entscheidungen).

| Regel | PROPOSED DEFAULT |
|-------|------------------|
| Kein Entry im Blackout-Fenster eines `HIGH`-Impact-Events | pre `30` min / post `30` min (FOMC: `30`/`60` via Event-Override) |
| Kein Entry im Blackout eines `MEDIUM`-Events | pre `15` / post `15` |
| Kein Entry im Pre-Positioning-Ban vor `HIGH` | `120` min |
| Offener Trade in ein `HIGH`-Event | `news.open_position.action` — DEFAULT: bei ≥ `0.5R` Gewinn → SL auf BE; sonst auf `news.reduce_pct` (**50 %**) reduzieren; `news.flatten_high_impact = true` ⇒ **vollständig flat** `15` min vor Event |
| News-Feed fehlt/veraltet (> `12 h`) | `news.feed.failure_action = block_new_entries` |

Verletzung ⇒ `NO_TRADE` (`NEWS_BLACKOUT`) bzw. definierte Positionsaktion.

---

## 18. Session-Filter (Gate)

Quelle: `sessions` (DST-korrekt, Börsenlokalzeit → UTC). Für Crypto sind die Fenster über
UTC-Zeiten definiert (`config.example.yaml`).

| Regel | PROPOSED DEFAULT |
|-------|------------------|
| Entry nur in erlaubten Sessions | `session.allowed = [london, newyork, london_ny_overlap]` |
| Kein Entry in den ersten Minuten nach Session-Open | `session.avoid_first_min = 15` |
| Kein Entry am Wochenende (Sa/So UTC) | `session.avoid_weekend = true` (für Crypto konfigurierbar; Startannahme = meiden) |
| Kein Entry in der letzten Stunde vor Wochenendbeginn | `session.avoid_pre_weekend_min = 60` |

*Warum validieren:* Für 24/7-Crypto ist „Session" eine Liquiditäts-Approximation, keine
Börsenzeit. Ob Wochenend-Sweeps auf BTC/ETH systematisch schlechter sind, ist eine offene
empirische Frage — `avoid_weekend = true` ist die konservative Startannahme.

---

## 19. Spread / Execution-Filter (Veto V7)

| Filter | Bedingung | PROPOSED DEFAULT |
|--------|-----------|------------------|
| Spread absolut | `current_spread ≤ exec.max_spread_atr × ATR(entry.timeframe)` | `0.10` |
| Spread relativ | `current_spread / price ≤ exec.max_spread_pct` | `0.05 %` |
| Slippage-Schätzung | `est_slippage ≤ exec.max_slippage_r × R` | `0.10` |
| Order-Book-Tiefe (sobald verfügbar) | Tiefe an Entry ≥ `exec.min_depth_multiple × position_size` | `10` |
| Datenalter (live) | `age(last_bar) ≤ exec.max_data_age_s` | `5` s |

Verletzung ⇒ `NO_TRADE` (`EXECUTION_FILTER`).

---

## 20. Confidence (Gate)

Berechnung in `confidence.md`. Setup-spezifische Eingaben (jeweils 0..1):

| Eingabe | Quelle |
|---------|--------|
| `data_confidence` | Datenqualität (Vollständigkeit, Frische, Lücken) |
| `swing_confirmation` | `bars_since_confirmation` der beteiligten Swings vs. `R` |
| `sweep_clarity` | 1 einzelner Pool & Penetration mittig im erlaubten Band & Docht klar ⇒ hoch; mehrere Pools / Randlage ⇒ niedrig |
| `displacement_strength` | `net_move_atr` relativ zur Regime-typischen Verteilung |
| `fvg_quality` | Größe, `fill_fraction = 0`, Alter |
| `structure_clarity` | Bruch eindeutig (nicht knapp, nicht überdehnt) |
| `regime_clarity` | Abstand der Regime-Metriken von ihren Schwellen |
| `htf_bias_strength` | §2 |

`setup_confidence` = gewichtete Kombination (Gewichte in `confidence.md`),
**hart begrenzt** durch `min(data_confidence, …)`-Floors.

| Parameter | PROPOSED DEFAULT | Bedeutung |
|-----------|------------------|-----------|
| `setups.SMC-SWEEP-REV-01.min_confidence` | `0.60` | darunter ⇒ `NO_TRADE` (`CONFIDENCE_BELOW_MIN`) |

---

## 21. Setup Score & Risikostufe

Berechnung & Faktor-Rubriken in `scoring-rubric.md`. Ablauf **strikt**:

```
1. No-Trade-Checkliste (no-trade.md)      → bei Treffer: STOP, NO_TRADE
2. Regime-Gate (§1)                        → bei Verletzung: STOP, NO_TRADE
3. Veto-Prüfung (§23)                      → bei Veto: STOP, NO_TRADE
4. Kette lebt, aber State < ARMED?         → STOP, WAIT           (0.1.1 C6)
5. Ketten-Gates (§2–§10, §16, §18)         → bei Verletzung: STOP, NO_TRADE
6. ERST JETZT: gewichteten Score berechnen → 0..100
7. Risikostufe aus Score × Confidence      → A+ | A | B  (sonst NO_TRADE: SCORE_BELOW_B)
8. Portfolio-Constraints (nur mit portfolio_context, C9) → kann Tier auf NO_TRADE senken
   → BUY (D=LONG) | SELL (D=SHORT)
```

Der vollständige, WAIT-bewusste Entscheidungsbaum steht in `../SPEC-ADDENDUM-0.1.1.md` §1.2.

| Stufe | Bedingung (PROPOSED DEFAULT) |
|-------|------------------------------|
| **A+** | `score ≥ 85` **und** `setup_confidence ≥ 0.80` |
| **A**  | `score ≥ 75` **und** `setup_confidence ≥ 0.70` |
| **B**  | `score ≥ 65` **und** `setup_confidence ≥ 0.60` |
| **NO_TRADE** | sonst |

Die Risikostufe steuert die Positionsgröße (`sizing.md`). **Die Stufe kann durch Portfolio-/
Korrelations-Constraints weiter auf `NO_TRADE` gesenkt werden** (`sizing.md` §Korrelation).

*Warum validieren:* Die Score-Bänder (85/75/65) sind aus der Luft gegriffen. Sie werden erst
sinnvoll, wenn die realisierte Expectancy je Band (OOS) monoton mit dem Band steigt — bis dahin
sind sie Platzhalter (`anti-overfitting.md`, Kill-Kriterien).

---

## 22. No-Trade-Bedingungen (setup-spezifisch)

Zusätzlich zur globalen Liste in `no-trade.md`. **Reihenfolge = Prüfreihenfolge.**

| # | Grund (Enum) | Bedingung |
|---|--------------|-----------|
| 1 | `REGIME_*` | Regime-Gate §1 verletzt |
| 2 | `BIAS_NONE` / `BIAS_TOO_WEAK` | §2 |
| 3 | `NO_QUALIFYING_LIQUIDITY` | §3 (kein Pool / zu schwach / zu weit / schon gesweept) |
| 4 | `SWEEP_BECAME_BREAKOUT` | §4 (Close hielt jenseits) |
| 5 | `NO_RECLAIM` | §5 |
| 6 | `NO_DISPLACEMENT` | §6 |
| 7 | `NO_STRUCTURE_SHIFT` | §7 (kein CHoCH/BOS in Richtung `D`) |
| 8 | `NO_ENTRY_ZONE` | §8 (keine FVG **und** kein OB) |
| 9 | `ENTRY_WRONG_SIDE_OF_EQUILIBRIUM` | §8 Location-Gate (Veto V2) |
| 10 | `SL_TOO_WIDE` / `SL_TOO_TIGHT` | §10 |
| 11 | `RR_BELOW_MIN` | §16 (eine der drei Bedingungen) |
| 12 | `CONFIDENCE_BELOW_MIN` | §20 |
| 13 | `SCORE_BELOW_B` | §21 |
| 14 | `NEWS_BLACKOUT` | §17 (Veto V4) |
| 15 | `SESSION_FILTER` | §18 |
| 16 | `EXECUTION_FILTER` | §19 (Veto V7) |
| 17 | `PORTFOLIO_CORRELATION` | `sizing.md`: Hinzufügen bricht korrelierte Exposure (BTC↔ETH!) (Veto V9) |
| 18 | `DUPLICATE_EXPOSURE` | offene Position **oder** `ARMED`-Kandidat gleiche Richtung, gleiches Instrument |
| 19 | `COOLDOWN_AFTER_STOP` | letzter Stop-Out auf diesem Instrument < `cooldown_bars` (M15) her — **PROPOSED DEFAULT `12`** |
| 20 | `DATA_CONFIDENCE_FLOOR` | `data_confidence < veto.min_data_confidence` (**DEFAULT `0.50`**) (Veto V6) |

---

## 23. Veto-Regeln (hart — kein positiver Score überstimmt sie)

**Mechanik:** Die Score-Pipeline (§21) berechnet zuerst `vetoes = [...]`. Ist die Liste **nicht
leer**, ist die Entscheidung `NO_TRADE`, der Score wird **nicht** berechnet/ignoriert, und **jeder**
Veto-Grund wird ins Decision Ledger geschrieben. Positive Confluence zählt **ausschließlich**,
nachdem alle Vetos passiert sind. Details & vollständige Widerspruchslogik: `contradictions.md`.

| ID | Veto | Auslöser |
|----|------|----------|
| **V1** | HTF-Bias-Konflikt | D1/H4 gegensätzliche Trends (`regime CONFLICTING`) |
| **V2** | Entry-Location falsch | Zone nicht im Discount (long) / Premium (short) des `swept_leg` |
| **V3** | Regime untauglich | `EXTREME` Vol **oder** `UNCLEAR` **oder** verbotene `phase` |
| **V4** | News | `HIGH`-Impact-Blackout / Pre-Positioning-Ban / Feed-Ausfall |
| **V5** | Kein echter Sweep | Sweep wurde Breakout (Close hielt jenseits, kein Reclaim) |
| **V6** | Daten unsicher | `data_confidence < veto.min_data_confidence` (**0.50**) |
| **V7** | Ausführung untauglich | Spread/Slippage/Tiefe/Datenalter über Limit |
| **V8** | RR ungenügend | eine der drei §16-Bedingungen verletzt |
| **V9** | Korrelierte Exposure | Hinzufügen bricht `max_correlated_exposure` (`sizing.md`) |
| **V10** | Kein SL definierbar | Struktur erlaubt keinen regelkonformen SL (§10-Floor/Cap unlösbar) |

*Beispiel, warum das nötig ist:* Ein „perfekter" Sweep + Displacement + FVG (hoher Score) **direkt
vor einem FOMC-Entscheid** oder **im Premium statt Discount** ist trotzdem `NO_TRADE`. Der starke
positive Befund darf den harten Negativfaktor nicht kompensieren.

---

## 24. State Machine

```
SCANNING
  └─(Regime-Gate ok, Bias ≠ NONE)──────────────▶ BIAS_SET
BIAS_SET
  └─(qualifizierende Liquidität gefunden)───────▶ LIQUIDITY_IDENTIFIED
  └─(Bias verloren / Regime verloren)───────────▶ SCANNING
LIQUIDITY_IDENTIFIED
  └─(Penetration erkannt)───────────────────────▶ SWEPT
  └─(Level BROKEN ohne Reclaim)─────────────────▶ SCANNING  (NO_TRADE: SWEEP_BECAME_BREAKOUT)
  └─(Timeout liquidity.freshness)───────────────▶ SCANNING
SWEPT
  └─(Reclaim in Frist)──────────────────────────▶ RECLAIMED
  └─(kein Reclaim in max_reclaim_bars)──────────▶ SCANNING  (NO_TRADE: NO_RECLAIM)
RECLAIMED
  └─(Displacement Richtung D in Frist)──────────▶ DISPLACED
  └─(Frist überschritten)───────────────────────▶ SCANNING  (NO_TRADE: NO_DISPLACEMENT)
DISPLACED
  └─(CHoCH/BOS Richtung D auf Struktur-TF)──────▶ STRUCTURE_SHIFTED
  └─(Frist überschritten)───────────────────────▶ SCANNING  (NO_TRADE: NO_STRUCTURE_SHIFT)
STRUCTURE_SHIFTED
  └─(Entry-Zone gültig + Location + RR + Gates)─▶ ARMED  (Entry-Order platziert)
  └─(ein Gate verletzt)─────────────────────────▶ SCANNING  (NO_TRADE: <Grund>)
ARMED
  └─(Entry-Order gefüllt)───────────────────────▶ TRIGGERED
  └─(Pre-Entry-Invalidierung §11 / Expiry §15)──▶ SCANNING  (Kandidat verworfen)
TRIGGERED / MANAGED
  └─(SL / Struct-Invalidierung / TP3-Exit / Time-Exit)──▶ CLOSED
CLOSED
  └─(Trade-Record geschrieben, Review-Flag)─────▶ REVIEW ──▶ SCANNING
```

Jeder Übergang hat eine **objektive Bedingung** und (wo zutreffend) einen **Timeout**. Kein
Übergang basiert auf Ermessen.

### 24.1 Anzeige-Aliase (`0.1.1` C5)

Die **interne** State Machine ist die obige. Für Dashboard / Scanner-Lifecycle (Phase 5) gibt es
generische Anzeige-Namen — sie ändern die FSM **nicht**:

| Alias | interne Zustände |
|-------|------------------|
| `WATCH` | `SCANNING`, `BIAS_SET` |
| `DEVELOPING` | `LIQUIDITY_IDENTIFIED`, `SWEPT`, `RECLAIMED`, `DISPLACED`, `STRUCTURE_SHIFTED` |
| `ARMED` | `ARMED` |
| `CONFIRMED` | `TRIGGERED`, `MANAGED` |
| `INVALIDATED` | Kandidaten-Invalidierung (Klasse A) / strukturelle Invalidierung (Klasse B) |
| `EXPIRED` | `CANDIDATE_EXPIRY` / `TIME_EXPIRY` / `DEAD_TRADE` |

Die `evaluate()`-Ausgabe `WAIT` entspricht `WATCH` **oder** `DEVELOPING` (State < `ARMED`, kein
Veto) — siehe `../SPEC-ADDENDUM-0.1.1.md` §1.

---

## 25. Zusammenfassung: was dieses Dokument exakt festlegt

- Die **vollständige kausale Kette** (§0) und ihre 8 Pflicht-Kettenglieder.
- **22 nummerierte Spezifikationspunkte** mit objektiven Bedingungen und benannten Parametern.
- **10 harte Veto-Regeln** (§23) mit definierter Mechanik (Veto vor Score).
- Eine **State Machine** (§24) mit objektiven Übergängen.
- **Alle Zahlen als `PROPOSED DEFAULT`** mit Begründung, warum sie empirisch validiert werden müssen.

**Noch offen (Nutzer-Bestätigung):** siehe Sammelabschnitt am Ende der Antwort und `regime.md` §10.
