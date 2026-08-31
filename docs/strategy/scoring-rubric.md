# Setup Scoring — Rubrik

**Zweck:** Jeder Score-Faktor bekommt ein **objektives Mapping** von messbaren Eingaben auf einen
Wert in `[0, 1]`. Es gibt drei Faktor-Rollen: **GATE** (hart, muss bestehen), **WEIGHTED**
(gewichteter Beitrag), **VETO** (siehe `contradictions.md`). Ein Faktor hat **genau eine** Rolle
— nie „Gate und gewichtet zugleich" (behebt die Redundanz aus dem Strategy-Audit R-06).

Alle Zahlen `PROPOSED DEFAULT`. Konfig unter `scoring.SMC-SWEEP-REV-01.*`.

---

## 1. Score-Formel

```
# nur erreichbar, wenn no-trade.md + regime-gate + chain-gates + Vetos alle bestanden

raw = Σ_i ( w_i · f_i )                       # f_i ∈ [0,1], nur WEIGHTED-Faktoren
score_0_100 = 100 · raw / Σ_i w_i
score = clip( score_0_100 − Σ negative_penalties , 0 , 100 )     # Abzüge aus contradictions.md §5
```

Kein Faktor kann `> 1` beitragen; kein negativer `f_i`. Widersprüche werden **ausschließlich**
über Vetos (harte Ausschlüsse) und die Penalty-Liste abgebildet, nicht über negative Faktoren im
`Σ w_i f_i`.

**Risikostufe** (`SMC-SWEEP-REV-01` §21):

| Stufe | `score ≥` | **und** `setup_confidence ≥` |
|-------|-----------|------------------------------|
| A+ | `85` | `0.80` |
| A  | `75` | `0.70` |
| B  | `65` | `0.60` |
| NO_TRADE | sonst | — |

---

## 2. GATE-Faktoren (binär: bestehen oder `NO_TRADE`)

| Faktor | Bedingung | `NoTradeReason` bei Fehlschlag |
|--------|-----------|-------------------------------|
| `regime_allowed` | Regime-Matrix (`regime.md` §8) erfüllt | `REGIME_NOT_ALLOWED_FOR_SETUP` |
| `htf_bias_present` | Bias ∈ {LONG, SHORT} und `bias_strength ≥ 0.35` | `BIAS_NONE` / `BIAS_TOO_WEAK` |
| `chain_complete` | alle 8 Kettenglieder (`SMC-SWEEP-REV-01` §0) erfüllt | jeweiliger Ketten-Grund |
| `entry_location_ok` | Lokations-Sanity (`contradictions.md` §3) | `ENTRY_WRONG_SIDE_OF_EQUILIBRIUM` (= Veto V2) |
| `rr_ok` | alle drei RR-Bedingungen (`SMC-SWEEP-REV-01` §16) | `RR_BELOW_MIN` (= Veto V8) |
| `sl_definable` | SL innerhalb Floor/Cap (`§10`) | `SL_TOO_WIDE` / `SL_TOO_TIGHT` (= Veto V10) |
| `session_ok` | Session-Filter (`§18`) | `SESSION_*` |
| `confidence_ok` | `setup_confidence ≥ min_confidence` (0.60) | `CONFIDENCE_BELOW_MIN` |

---

## 3. WEIGHTED-Faktoren — Rubriken

> `ATR` bezieht sich, wenn nicht anders genannt, auf den jeweils relevanten Timeframe des Faktors.
> Alle `clip(x,0,1)`.

### 3.1 `htf_bias_strength` — Gewicht **20**
`f = bias_strength` (aus `SMC-SWEEP-REV-01` §2) — bereits in `[0,1]`.
Interpretation: 0 = gerade so vorhanden, 1 = D1+H4 klar gleichgerichteter Trend, kein Disagreement.

### 3.2 `liquidity_quality` — Gewicht **14**
`f = 0.6 · level.strength + 0.4 · type_bonus`
- `level.strength` aus `primitives.md` §4.2
- `type_bonus`: `equal_highs/lows → 1.0`, `session_*/pdh/pdl/pwh/pwl → 0.8`, `swing (H4) → 0.6`,
  `swing (H1) → 0.4`

### 3.3 `sweep_clarity` — Gewicht **13**
`f = mean( pen_term , reclaim_speed_term , wick_term , single_pool_term )`
- `pen_term = 1 − |pen_depth_atr − mid_band| / half_band` mit Band `[min_pen, max_pen]` — Penetration
  mittig im erlaubten Band ⇒ 1, an den Rändern ⇒ 0
- `reclaim_speed_term = 1 − (bars_to_reclaim − 1) / max_reclaim_bars`
- `wick_term = clip((wick_ratio − 1) / (min_wick_ratio·2 − 1), 0, 1)`
- `single_pool_term = 1` bei genau 1 gesweepten Pool im Fenster, `0.5` bei 2, sonst Veto (`MESSY_LIQUIDITY`)

### 3.4 `reclaim_quality` — Gewicht **8**
`f = mean( body_term , close_beyond_term , color_term )`
- `body_term = clip((reclaim_body_ratio − 0.3) / 0.5, 0, 1)`
- `close_beyond_term = clip(close_beyond_atr / (min_reclaim_atr · 3), 0, 1)`
- `color_term = 1` wenn Reclaim-Bar in Richtung `D`, sonst `0.4`

### 3.5 `displacement_strength` — Gewicht **12**
`f = mean( atr_term , body_term , fvg_count_term )` — **regime-normiert**
- `atr_term = clip((net_move_atr − disp.min_atr) / (disp.min_atr · 2), 0, 1)`
- `body_term = clip((disp_body_ratio − disp.min_body_ratio) / (1 − disp.min_body_ratio), 0, 1)`
- `fvg_count_term = clip(fvg_count / 2, 0, 1)`
- **Regime-Normierung:** wenn `volatility = HIGH`, wird `atr_term` mit `0.85` multipliziert
  (in High-Vol ist großes Displacement „billiger" → weniger Aussagekraft). `PROPOSED DEFAULT`.

### 3.6 `structure_shift_quality` — Gewicht **10**
`f = mean( clean_term , distance_term , type_term )`
- `clean_term`: `close`-Abstand zum gebrochenen Level in `(0, buffer + 1·ATR]` ⇒ 1; knapp (Wick-nah)
  ⇒ 0.3; `> max_break_distance` ⇒ Gate-Fail
- `distance_term = 1 − break_distance_atr / structure.max_break_distance_atr`
- `type_term`: `CHoCH` im Reversal-Kontext ⇒ `1.0`; `BOS` im Continuation-Kontext ⇒ `0.9`

### 3.7 `fvg_quality` — Gewicht **8** (Fallback: OB-Qualität)
`f = mean( size_term , freshness_term , age_term )`
- `size_term = clip((zone_height_atr − fvg.min_size_atr) / (fvg.min_size_atr · 3), 0, 1)`
- `freshness_term = 1 − fill_fraction / mitigation.consumed_threshold`
- `age_term = 1 − age_bars / fvg.max_age_bars`
- bei OB-Fallback: analog mit OB-Parametern, dann `f *= 0.9` (OB etwas schwächer gewichtet als FVG)

### 3.8 `entry_location_depth` — Gewicht **9**
`f = clip((max_pd_position − pd_position_zone_mid) / max_pd_position, 0, 1)` für Long
(tiefer im Discount ⇒ höher). Spiegelbildlich für Short. (Das reine Bestehen der Lokation ist ein
GATE §2; hier wird die **Tiefe** belohnt.)

### 3.9 `risk_reward` — Gewicht **10**
`f = clip((RR_to_TP2 − rr.min_to_tp2) / rr.min_to_tp2, 0, 1)`
(RR = `min_to_tp2` ⇒ 0; RR = `2 × min_to_tp2` ⇒ 1). Das Bestehen des Minimums ist GATE §2.

### 3.10 `session_context` — Gewicht **6**
`f`: `london_ny_overlap → 1.0`, `newyork → 0.85`, `london → 0.8`, `asia → 0.4`,
außerhalb erlaubter Sessions ⇒ GATE-Fail.

### 3.11 `regime_alignment` — Gewicht **7**
`f = mean( directional_score , 1 − distance_to_vol_threshold_penalty , phase_term )`
- `directional_score` aus `RegimeState`
- Nähe zu einer Vol-Schwelle (knapp `NORMAL`/`HIGH`-Grenze) ⇒ Abschlag
- `phase_term`: `EXPANSION` in Richtung `D` ⇒ `1.0`; `NEUTRAL` ⇒ `0.7`; `EXPANSION` gegen `D` ⇒ `0.4`

### 3.12 `data_confidence_bonus` — Gewicht **5**
`f = clip((data_confidence − veto.min_data_confidence) / (1 − veto.min_data_confidence), 0, 1)`
(unterhalb des Floors ist es Veto V6; oberhalb wird höhere Qualität leicht belohnt).

---

## 4. Gewichts-Tabelle (PROPOSED DEFAULT)

> **`strategy_version 0.1.1` (C2):** Im **MVP / Phase 3** gilt **nicht** diese Staffelung, sondern
> **Gleichgewichtung** — jeder WEIGHTED-Faktor `= 10`, alle Penalties `= 0` (`scoring.example.yaml`,
> `DECISIONS-0.1.0.md` #4). Die Tabelle unten ist das **spätere Kalibrierungsziel** (erst nach
> OOS-Edge-Nachweis, dann ≤ `antioverfit.max_free_params` Gewichte gleichzeitig). **Keine
> Gewichts-Optimierung in Phase 3.**

| Faktor | Gewicht | Rolle |
|--------|--------:|-------|
| `htf_bias_strength` | 20 | WEIGHTED |
| `liquidity_quality` | 14 | WEIGHTED |
| `sweep_clarity` | 13 | WEIGHTED |
| `displacement_strength` | 12 | WEIGHTED |
| `structure_shift_quality` | 10 | WEIGHTED |
| `risk_reward` | 10 | WEIGHTED |
| `entry_location_depth` | 9 | WEIGHTED |
| `fvg_quality` | 8 | WEIGHTED |
| `reclaim_quality` | 8 | WEIGHTED |
| `regime_alignment` | 7 | WEIGHTED |
| `session_context` | 6 | WEIGHTED |
| `data_confidence_bonus` | 5 | WEIGHTED |
| **Σ Gewichte** | **122** | |

Negativ-Penalties (aus `contradictions.md` §5): `messy_sweep −8`, `proximity_opposing_htf_zone −10`,
`stale_structure −5`, `weak_displacement −6`, `mtf_partial_disagreement −7`, `wide_sl −5`,
`late_session −4`.

---

## 5. Warum diese Zahlen validiert werden müssen

1. **Gewichte sind eine Hypothese über Wichtigkeit.** Es gibt keinen theoretischen Grund, warum
   `htf_bias_strength` genau `20` und `session_context` genau `6` sein soll. Der einzige gültige
   Test: **korreliert ein höherer Faktorwert OOS mit besserer realisierter Expectancy?**
2. **Die Bänder (85/75/65) sind erst sinnvoll, wenn die Monotonie gilt:** A+ muss OOS besser
   abschneiden als A, A besser als B. Solange das nicht gemessen ist, sind die Bänder Etiketten
   ohne Inhalt.
3. **Gefahr der Überanpassung:** 12 Gewichte + 7 Penalties = 19 Knöpfe. `anti-overfitting.md`
   begrenzt, wie viele davon im MVP frei optimiert werden dürfen (Rest bleibt auf Default).
4. **Kalibrierungs-Startpunkt:** Für die erste Validierungsrunde werden **alle WEIGHTED-Faktoren
   gleich gewichtet** (jeder `= 10`) und **alle Penalties = 0** gesetzt. Erst wenn die
   gleichgewichtete Version einen positiven OOS-Edge zeigt, werden Gewichte vorsichtig und
   dokumentiert angepasst (nie mehr als `anti-overfit.max_free_params` gleichzeitig).

---

## 6. Ausgabeobjekt (ins Decision Ledger)

```
SetupScore {
  setup_id: "SMC-SWEEP-REV-01"
  strategy_version: semver
  gates_passed: [str]
  vetoes: []                      # leer, sonst wäre kein Score berechnet worden
  factors: { name: {value: float, weight: float, contribution: float} }
  penalties: { name: points }
  raw_score_0_100: float
  penalties_total: float
  final_score: float
  setup_confidence: float
  tier: A_PLUS | A | B | NO_TRADE
  tier_reason: str
}
```

---

## 7. Zu bestätigen / zu validieren

- **Rollen-Zuordnung** (was ist GATE, was WEIGHTED): bestätigen. Insbesondere `risk_reward` und
  `entry_location` sind je **einmal** GATE (Minimum/Seite) und **einmal** WEIGHTED (Tiefe/Höhe) —
  das ist Absicht, keine Redundanz.
- **Start mit Gleichgewichtung** (§5.4): empfohlen — bestätigen.
- **Regime-Normierung von `displacement_strength`** (×0.85 in High-Vol): Startannahme.
- **Bänder 85/75/65 + Confidence 0.80/0.70/0.60**: reine Platzhalter bis zur Monotonie-Prüfung.
