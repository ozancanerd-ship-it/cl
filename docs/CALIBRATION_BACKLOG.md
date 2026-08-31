# Calibration Backlog — alle PROPOSED DEFAULTS sind unkalibriert

**Status:** verbindlich · Stand 2026-08-29 · Phase 3 (`strategy_version 0.1.1`)

> **Grundregel.** Jeder Zahlenwert in den `*Params`-Dataclasses der Strategy Engine ist ein
> **PROPOSED DEFAULT** — eine plausible Erstschätzung, **nicht** ein optimierter Wert. Kein
> Wert wurde bisher gegen Out-of-Sample-Daten geprüft. Bis dahin gilt: **nicht als „richtig"
> behandeln**, keine Feature-Entscheidung auf einen dieser Werte stützen, keine Gewichts-
> optimierung „nach Gefühl".

Kalibrierung passiert **erst**, wenn (a) ≥ 180 Tage PIT-saubere M5-Historie für BTC/ETH da sind
(`HISTORICAL_DATA_PLAN.md`) und (b) ein deterministischer Replay-Harness steht. Vorher wäre jede
Anpassung Overfitting auf synthetische Test-Daten.

---

## 1. Methodik (wenn Daten da sind)

1. **Split:** Train/Validation/Test zeitlich getrennt (kein Shuffle). Test-Fenster wird nie
   für Tuning angefasst.
2. **Sensitivität zuerst, Optimierung später:** je Parameter einen Ein-Faktor-Sweep über einen
   plausiblen Bereich; Kennzahl = Robustheit (flache Plateaus), nicht Spitzen-Performance.
3. **Kennzahlen:** Trefferquote je Tier, Erwartungswert in R, MFE/MAE-Verteilung, Anteil
   `NO_TRADE`/`WAIT`, Alert-Rate, Zeit bis Invalidierung. Keine einzelne Zielgröße.
4. **Anti-Overfitting-Gate:** `docs/strategy/anti-overfitting.md` — Parameterzahl begrenzt,
   jede Änderung braucht eine ökonomische Begründung, nicht nur einen besseren Backtest-Wert.
5. **Log-Odds / Bayesian Aggregation:** bleibt Backlog, bis OOS-Daten die Faktor-
   Trefferwahrscheinlichkeiten überhaupt schätzbar machen (Audit 6).

---

## 2. Registry der unkalibrierten Parameter

### 2.1 Confidence (`strategy/confidence.py` · `confidence.md`)
| Parameter | Default | Bereich für Sweep | Notiz |
|---|---|---|---|
| `wd` / `wa` (Daten/Analyse-Gewicht) | 0.40 / 0.60 | 0.3–0.5 / 0.5–0.7 | Kern-Split, zuerst testen |
| `soft_floor` / `floor_penalty` | 0.60 / 0.50 | 0.5–0.7 / 0.3–0.7 | Schwache-Komponente-Strafe |
| `data_hard_floor` | 0.50 | 0.4–0.6 | → V6, blockierend |
| `min_setup_confidence` | 0.60 | 0.5–0.7 | → `CONFIDENCE_BELOW_MIN` |
| `single_source_value` | 0.80 | 0.6–0.9 | Ein-Quellen-Datenlage |
| `source_disagree_atr` | 0.30 | 0.1–0.5 | Quellen-Divergenz in ATR |
| `analysis_weights` (6 Terme, Σ=1) | je ≈0.167 | Dirichlet-Sweep | swing/structure/sweep/regime/htf-mtf/fvg |
| `structure_min/max_dist_atr` | 0.5 / 4.0 | — | Struktur-Klarheit |
| `regime_settle_bars` | 3 | 2–6 | Regime-Beruhigung |

### 2.2 Scoring (`strategy/scoring.py` · `scoring-rubric.md`)
| Parameter | Default | Notiz |
|---|---|---|
| `weights` (12 WEIGHTED-Faktoren) | **alle 10.0** | MVP-Gleichgewicht (C2). Datengetriebene Gewichte = Backlog |
| `penalties` | **{}** (0) | §5-Negativfaktoren werden gemeldet, **nicht** angewandt |
| `tier_score_min` | A+ 85 / A 75 / B 65 | Tier-Schwellen |
| `tier_confidence_min` | A+ 0.80 / A 0.70 / B 0.60 | Confidence-40/60-Grenzen |
| `correlated_factor_groups` | Report-only | korrelierte-Faktoren-Cap = Backlog |

### 2.3 Contradictions (`strategy/contradictions.py` · `contradictions.md` §8)
| Parameter | Default | Notiz |
|---|---|---|
| `messy_sweep_points` … `late_session_points` | −8 / −10 / −5 / −6 / −7 / −5 / −4 | Penalty-Punkte, im MVP nicht angewandt |
| `c1_freshness_bars` | 20 | Frische des gegnerischen Bruchs |
| `c2_window_bars` | 3 | „beide Seiten gesweept"-Fenster |
| `opposing_zone_overlap_veto` | 0.50 | C9-Schwelle (≥ 50 % = hart) |
| `displacement_min_atr` / `weak_displacement_factor` | 1.5 / 1.2 | |
| `mtf_partial_band` | (0.33, 0.66) | Teil-Uneinigkeits-Band |

### 2.4 Veto (`strategy/veto.py`)
| Parameter | Default | Notiz |
|---|---|---|
| `min_data_confidence` | 0.50 | V6 |
| `max_spread_atr` / `max_spread_pct` | 0.10 / 0.0005 | V7 |
| `max_data_age_periods` | 3.0 | V7 |
| `portfolio_heat_cap_pct` | 3.0 | V9 (pass-through ohne `portfolio_context`) |
| OOS-Priorität | V3 / V6 / V7 / V9 | Audit 7 |

### 2.5 Gates — Location / RR (`strategy/gates.py` · `SMC-SWEEP-REV-01` §8/§10/§12–16)
| Parameter | Default | Notiz |
|---|---|---|
| `max_pd_position` | 0.50 | Premium/Discount-Tiefe (V2) |
| `sl_buffer_atr` | 0.50 | ATR-Puffer hinter Sweep-Extrem |
| `sl_max_distance_atr` / `sl_min_distance_atr` | 3.0 / 0.40 | → V10 |
| `sl_min_spread_multiple` | 5.0 | SL ≥ n × Spread |
| `tp1_r_multiple` / `tp2_r_multiple` | 1.5 / 3.0 | |
| `rr_min_to_tp2` | 2.0 | → V8 |
| `rr_min_blended` | 1.3 | gewichtetes RR |
| `rr_min_target_room_r` | 1.5 | Ziel-Raum bis Gegenliquidität |

### 2.6 Confirmation (`strategy/price_action.py` · `SPEC-ADDENDUM` §2)
Engulfing-/Pin-/Minor-CHoCH-Schwellen (Body-Ratio, Docht-Anteil, ATR-Bezug), Fenstergrößen.
Alle unkalibriert — **plus** die grundsätzliche Frage `limit_at_proximal_edge` vs.
`confirmation_market` (Audit 8, braucht native M1 + Decision-Invariante-Lockerung).

### 2.7 Setup-FSM (`strategy/setup_detection.py`)
`liquidity_min_strength` 0.40, `displacement_max_bars_after_reclaim` 3,
`structure_max_bars_after_displacement` 3, `structure_max_break_distance_atr` 4.0,
`entry_min_zone_height_atr` 0.15, `armed_bars` 12, `atr_period` 14.

### 2.8 No-Trade (`strategy/no_trade.py` · `no-trade.md`)
`min_completeness` 0.98, `min_freshness` 0.50, `min_data_confidence` 0.50,
`pre_positioning_ban_min` 120, `cooldown_after_stop_bars` 12, `cooldown_after_sweep_fail_bars` 6,
`max_spread_atr` 0.10, `max_data_age_periods` 3.0 sowie **alle** Risk-/Portfolio-Limits
(`max_daily_loss_pct` 3.0, `max_weekly_loss_pct` 6.0, `max_drawdown_pct` 10.0,
`max_trades_today` 6, `loss_streak_review` 4) — letztere zusätzlich **noch nicht scharf**
(`not_checked`, Phase 4 `risk/`).

### 2.9 Regime-Gate (`analysis/regime.py`) — **OOS-KALIBRIERT 2026-08-29 → Baseline bleibt**

**Ergebnis der OOS-Kalibrierung:** siehe `docs/REGIME-CALIBRATION-2026-08.md`. Kurz:

- Das Gate **ist informativ** — `gate_ok`-Probes: Expectancy +0.382 R / PF 1.71 vs. Bias-only
  +0.008 R / PF 1.01 (IS). Trennschärfe ist da; der Engpass ist **Abdeckung** (IS 1.0 %, OOS
  **0.0 %**), stark period-abhängig.
- **Jede** geprüfte Lockerung (V1–V5, inkl. „M15-Vol nicht hart blockend") zieht den
  Erwartungswert IS zurück auf ~Bias-only-Niveau und ist **OOS netto verlierend**
  (−0.03 bis −0.11 R, PF 0.84–0.95). Walk-Forward: nur 1 von 4 Test-Folds positiv, Fold 2 für
  alle Varianten negativ.
- **Entscheidung: konservative Baseline bleibt zu 100 % unverändert.** Kein Default angefasst.
  `context_vol_is_hard_block` bleibt `True`.
- **Erledigt (Architektur, keine Verhaltensänderung):** `RegimeGateParams` ist jetzt über
  `MtfParams.regime_gate` konfigurierbar; `RegimeGateParams.context_vol_is_hard_block` (Default
  `True`) existiert. Damit kann eine spätere Kalibrierung greifen.
- **Verschoben (echte Hebel, nicht „Gate lockern"):** (a) mehr Instrumente (SOL, weitere Coins,
  XAUUSD) → mehr Gelegenheiten fürs informative Gate; (b) niedrige Trade-Frequenz als korrekt
  akzeptieren; (c) Struktur-Klassifikator (`derive_structure_state`, #2) **isoliert** gegen
  einen vollen Marktzyklus prüfen — separat vom Gate.

Ursprüngliche Tabelle (bleibt als Referenz für die isolierte Struktur-Klassifikator-Kalibrierung):

| Parameter | Default | Ort | Wirkung im Test |
|---|---|---|---|
| `TrendParams.min_swings` | 2 | verlangt 3 mon. HH **und** 3 mon. HL auf D1 **und** H4 | H4 = `unclear` 93 % |
| `TrendParams.min_slope` | 0.05 | Steigungsschwelle (ATR/Bar) | koppelt mit min_swings |
| `TrendParams.slope_window` | 50 | Regressionsfenster | |
| `RegimeGateParams.allow_unclear_htf` | False | ein `unclear` HTF ⇒ NO_TRADE | Haupt-Blocker |
| `VolParams.extreme_pct` / `extreme_atr_ratio[crypto]` | 97 / 0.08 | EXTREME-Grenze | ~25 % der Bars |
| `RegimeGateParams.forbid_low_vol` | True | LOW-Vol ⇒ NO_TRADE | blockt Low-Vol-Trends |
| **M15 als `context` im Gate** | ja (hard EXTREME) | `build_mtf_context` → `regime_gate(..., m15)` | ein M15-Vol-Spike vetot ein sauberes D1/H4-Setup — als **Design-Frage** markiert |
| `RegimeGateParams` überhaupt **konfigurierbar** machen | — | `build_mtf_context` hardcodet `regime_gate()` mit Default-Params | Architektur-Ergänzung nötig, damit Kalibrierung greift |

Außerdem: ADX-/Kompressions-Perzentile, DataQuality-Term-Gewichte, `RegimeTracker`-Hysterese im
MTF-Pfad (aktuell `raw_regime`, `bars_in_state=0` — bewusst? oder soll der MTF-Kontext
akkumulieren?).

### 2.10 Dynamic Signal / Exit / Alerts (Schritte 4–7 — **neu, Audit 11**)
| Modul | Parameter | Default | Notiz |
|---|---|---|---|
| `signal.py` | `score_change_eps` | 3.0 | Δ Score für STRENGTHENED/WEAKENED |
| | `stale_ticks` | 40 | Alterung (Ticks, nicht Zeit) |
| `position.py` | `tp1_close_fraction` / `tp2_close_fraction` | 0.5 / 0.3 | Runner = 0.2 |
| | `be_offset_r` | 0.0 | eingesperrter Gewinn beim BE-Zug |
| | `pending_expiry_bars` | 12 | Limit nie getriggert → EXPIRED |
| | `trail_after_tp2` | True (SL → TP1) | vs. struktur-Trail (Backlog) |
| | `worst_case_fill` | True | SL vor TP in einer Bar |
| `alerts.py` | `default_cooldown` | 15 min | je Typ überschreibbar |
| | `always_deliver` | EXIT/INVALIDATED/SL/BUY/SELL/RISK/BROKER | Cooldown-Bypass-Menge |
| `m1_feed.py` | `lookback_before_break` / `max_bars` | 30 min / 720 | Confirmation-Fenster |
| `engine.py` | `fill_timeframe` | M5 | Serie für die Fill-Simulation |

### 2.11 Kostenmodell (`strategy/costs.py` — Audit 13)
**Alle Sätze Default `0.0`** (nichts erfunden). Echte Werte einsetzen **bevor** ein Backtest-
Erwartungswert als „netto" gilt: `taker_fee_bps` / `maker_fee_bps` (Exchange-Gebührenplan,
`refdata.FeeSchedule`), `half_spread_bps` + `slippage_bps` + `slippage_atr_mult` (gemessene
Slippage je Instrument/Regime), `market_impact_bps` (größen-/tiefenabhängig),
`funding_bps_per_day` (echte Perp-Funding-Historie). Kein Tuning — Messwerte.

### 2.12 Risk-Limits (`risk/limits.py` — Audit 13, Phase 4)
`hard_max_risk_pct` 2.0 · Basis-Bänder A+/A/B 1.00/0.65/0.40 % · `max_daily_loss_pct` 3.0 ·
`max_weekly_loss_pct` 6.0 · `max_drawdown_pct` 10.0 · `max_trades_today` 6 · `loss_streak_halt` 4 ·
`max_open_positions` 3 · `max_total_open_risk_pct` 3.0 · `max_correlated_open_risk_pct` 1.5 ·
`max_cluster_open_risk_pct` 2.0 · `correlation_threshold` 0.70 · `max_leverage` 20 ·
`min_liq_distance_atr` 3.0 · `margin_buffer_pct` 20.
Validierung: Monte-Carlo-Ruin-Wahrscheinlichkeit < 5 %, Max-DD im MC-95-%-Band, stabile
Expectancy je Stufe (`anti-overfitting.md` §9 / `sizing.md` §1).

---

## 3. Explizit als Backlog (nicht jetzt)

- **Datengetriebene Score-Gewichte** statt Gleichgewicht (nach erstem OOS).
- **Log-Odds / Bayesian Faktor-Aggregation** in Confluence & Confidence.
- **Asset-spezifische** Parameter-Sätze (BTC ≠ ETH ≠ FX ≠ Indizes).
- **Timeframe-spezifische** Gewichte / Schwellen.
- **Korrelierte-Faktoren-Cap** (aktuell nur `correlated_factor_groups` als Report).
- **Struktur-basierter Trailing-Stop** im Exit-Management.
- **Regime-abhängige** Teilexit-Fraktionen und RR-Ziele.
