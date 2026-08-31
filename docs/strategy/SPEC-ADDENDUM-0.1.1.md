# Spec Addendum — `strategy_version 0.1.1`

**Status:** `PROPOSED / TO-VALIDATE`. Diese Datei ergänzt die eingefrorene Spezifikation um
Definitionen, die im Phase-3-Spec-Audit als **fehlend** identifiziert wurden (Punkte C6, C7).
Alle Zahlen sind `PROPOSED DEFAULT` und empirisch zu validieren (`anti-overfitting.md`).

Konfig: `§1` unter `decision.*`, `§2` unter `primitives.confirmation.*` bzw.
`setups.SMC-SWEEP-REV-01.entry.confirmation.*`.

---

## 1. Decision-Output — `BUY` / `SELL` / `WAIT` / `NO_TRADE` (C6)

Die Strategy Engine (`strategy.evaluate(MarketContext) -> Decision`) gibt genau **einen** von
vier Werten aus. `WAIT` ist neu in `0.1.1`.

### 1.1 Enum

```
DecisionType = BUY | SELL | WAIT | NO_TRADE
```

### 1.2 Entscheidungsbaum (verbindlich, Reihenfolge = Prüfreihenfolge)

Aufsetzend auf `contradictions.md` §6 `resolve()`:

```
resolve(context) -> Decision:
    # Schritt 1: globale No-Trade-Checkliste (no-trade.md)
    reasons = check_no_trade(context)
    if reasons: return NO_TRADE(reasons)

    # Schritt 2: Regime-Gate (regime.md §9)
    r = regime_gate(context)
    if r != OK: return NO_TRADE(r)

    # Schritt 3: Setup-State-Machine fortschreiben (SMC-SWEEP-REV-01 §24)
    state = advance_setup_fsm(context)

    # Schritt 4: harte Vetos V1–V10 (contradictions.md §4, Setup §23)
    vetoes = collect_vetoes(context)          # V9 = pass-through, wenn portfolio_context fehlt
    if vetoes: return NO_TRADE(vetoes)

    # Schritt 5: Kette abgebrochen / Kandidat verworfen?
    if state == SCANNING and state.abort_reason is not None:
        return NO_TRADE(state.abort_reason)   # z.B. SWEEP_BECAME_BREAKOUT, NO_RECLAIM, Expiry

    # Schritt 6: Kette lebt, aber noch nicht ARMED?
    if state in {BIAS_SET, LIQUIDITY_IDENTIFIED, SWEPT, RECLAIMED, DISPLACED, STRUCTURE_SHIFTED}:
        return WAIT(state, chain_progress)    # kein Veto, kein harter Grund -> beobachten

    # Schritt 7: State == ARMED  ->  restliche Ketten-Gates + Confidence + Score
    g = setup_chain_gates(context)            # §8 (Entry-Zone/Location), §10 (SL), §16 (RR), §18, §20
    if g != OK: return NO_TRADE(g)
    c = contradiction_matrix(context)
    if c.hard: return NO_TRADE(c.reasons)

    score = weighted_score(context) - sum(c.negative_factors)   # 0..100
    tier  = tier_from(score, setup_confidence)                  # A+ | A | B | NO_TRADE
    if tier == NO_TRADE: return NO_TRADE(SCORE_BELOW_B)

    # Schritt 8: Portfolio/Sizing kann Tier weiter senken (C9: nur wenn portfolio_context vorhanden)
    tier = apply_portfolio_constraints(tier, context)
    if tier == NO_TRADE: return NO_TRADE(PORTFOLIO_CORRELATION)

    return BUY(...) if D == LONG else SELL(...)
```

### 1.3 Merkregeln

| Output | Bedeutung |
|--------|-----------|
| **`BUY` / `SELL`** | Vollständige Kette, `state == ARMED`, alle Gates + Vetos bestanden, `tier ∈ {A+, A, B}`. Enthält Entry, SL, TP1/2/3, RR, Score, Confidence, `reason_codes`. |
| **`WAIT`** | Kette **lebt** (`state ∈ {BIAS_SET … STRUCTURE_SHIFTED}`), **kein** hartes Veto, **kein** harter No-Trade-Grund. Das Setup ist beobachtungswürdig; die Engine liefert `state` + `chain_progress` (+ optional voraussichtliche Richtung `D`). Kein Entry/SL/TP. |
| **`NO_TRADE`** | Hartes Veto **oder** harter No-Trade-Grund (`no-trade.md`) **oder** Regime-Gate verletzt **oder** Kette abgebrochen (`SWEEP_BECAME_BREAKOUT`, `NO_RECLAIM`, `NO_DISPLACEMENT`, `NO_STRUCTURE_SHIFT`, Expiry, Kandidaten-Invalidierung) **oder** `state == ARMED`, aber ein spätes Gate/Contradiction verletzt **oder** `tier` unter B. Enthält **alle** zutreffenden `reason_codes` + `chain_progress`. |

### 1.4 Verhältnis zur State Machine

- `WAIT` ist **kein** eigener FSM-Zustand. Es ist die Ausgabe von `evaluate()`, solange die FSM
  in einem der Zwischenzustände steht und nichts hart dagegen spricht.
- Sobald die FSM `ARMED` erreicht, kann `evaluate()` nie mehr `WAIT` liefern — nur `BUY`/`SELL`
  (Gates bestanden) oder `NO_TRADE` (spätes Gate verletzt / Kandidat invalidiert).
- Ein `NO_TRADE` aus einem Zwischenzustand (Schritt 5) setzt die FSM auf `SCANNING` zurück
  (`SMC-SWEEP-REV-01` §24).

### 1.5 Parameter

| Parameter | PROPOSED DEFAULT | Bedeutung |
|-----------|------------------|-----------|
| `decision.wait_min_state` | `BIAS_SET` | ab welchem FSM-Zustand `WAIT` (statt stiller Nichtausgabe) zurückgegeben wird |
| `decision.emit_wait` | `true` | `false` ⇒ Zwischenzustände liefern `NO_TRADE(chain_incomplete)` (altes Verhalten) |

---

## 2. Confirmation-Entry-Muster (C7)

Gilt **ausschließlich** für `setups.SMC-SWEEP-REV-01.entry.mode = confirmation_market`
(`SMC-SWEEP-REV-01` §9). In den Default-Modi (`limit_at_proximal_edge`, `limit_at_mid`) **nicht**
verwendet. Timeframe: `entry.confirmation_tf` (**PROPOSED DEFAULT `M1`**). Nur `confirmed`-Bars.

Der Trigger ist erfüllt, wenn der Preis **in** der Entry-Zone ist (`low ≤ zone_high` und
`high ≥ zone_low` der Confirmation-Bar) **und mindestens eines** der folgenden Muster in
Richtung `D` auftritt.

> `body = |close − open|`, `range = high − low`, `upper_wick = high − max(open, close)`,
> `lower_wick = min(open, close) − low`, `ATR = ATR(primitives.atr.period, confirmation_tf)`.
> Eine Bar mit `range == 0` erfüllt **kein** Muster.

### 2.1 Engulfing (`ENGULFING`)

Bullisch (D = LONG):
1. Vorbar `p` ist bearisch: `close[p] < open[p]`.
2. Aktuelle Bar `b` ist bullisch: `close[b] > open[b]`.
3. **Body-Umschluss:** `open[b] ≤ close[p]` **und** `close[b] ≥ open[p]`
   (Toleranz `confirmation.engulf.tol_atr × ATR` an beiden Kanten erlaubt).
4. **Mindestgröße:** `body[b] ≥ confirmation.engulf.min_body_atr × ATR`
   **und** `body[b] ≥ confirmation.engulf.min_body_ratio × body[p]`.
5. **Schlusslage:** `close[b] ≥ open[p]` (schließt über dem gesamten Vorbar-Body).

Bearisch (D = SHORT): spiegelbildlich.

| Parameter | PROPOSED DEFAULT |
|-----------|------------------|
| `confirmation.engulf.tol_atr` | `0.05` |
| `confirmation.engulf.min_body_atr` | `0.6` |
| `confirmation.engulf.min_body_ratio` | `1.0` |

### 2.2 Pin Bar / Rejection (`PIN`)

Bullisch (D = LONG):
1. `lower_wick ≥ confirmation.pin.min_wick_ratio × body`
2. `lower_wick ≥ confirmation.pin.min_wick_range_frac × range`
3. `upper_wick ≤ confirmation.pin.max_opp_wick_frac × range`
4. **Docht sticht in die Zone / darunter:** `low ≤ zone_low + confirmation.pin.pierce_tol_atr × ATR`
5. **Körper über der Zonenmitte:** `min(open, close) ≥ zone_mid`
   (der Reject hat die Zone gehalten).
6. **Mindestgröße:** `range ≥ confirmation.pin.min_range_atr × ATR`.

Bearisch (D = SHORT): spiegelbildlich (`upper_wick`, Körper unter Zonenmitte).

| Parameter | PROPOSED DEFAULT |
|-----------|------------------|
| `confirmation.pin.min_wick_ratio` | `2.0` |
| `confirmation.pin.min_wick_range_frac` | `0.6` |
| `confirmation.pin.max_opp_wick_frac` | `0.2` |
| `confirmation.pin.pierce_tol_atr` | `0.15` |
| `confirmation.pin.min_range_atr` | `0.5` |

### 2.3 Minor-CHoCH auf `confirmation_tf` (`MINOR_CHOCH`)

Ein **CHoCH gemäß `primitives.md` §3**, berechnet auf `entry.confirmation_tf` (M1), mit
**reduziertem Swing-Fraktal** (`confirmation.choch.swing_left/right`, DEFAULT `1/1`) und
Richtung = `D`:
1. Der lokale M1-Zustand vor dem Zonen-Kontakt war **gegen `D`** gerichtet
   (`≥ confirmation.choch.min_swings` gegen-`D`-Swing-Paare, DEFAULT `1`).
2. Eine `confirmed` M1-Bar schließt jenseits des letzten gegen-`D`-Swings um
   `≥ confirmation.choch.buffer_atr × ATR(M1)` (DEFAULT `0.0`).
3. Der gebrochene Swing lag innerhalb der Entry-Zone `± confirmation.choch.zone_pad_atr × ATR`
   (DEFAULT `0.5`) — der CHoCH gehört zur Zonen-Reaktion, nicht zu einer weit entfernten Struktur.

| Parameter | PROPOSED DEFAULT |
|-----------|------------------|
| `confirmation.choch.swing_left` / `.swing_right` | `1` / `1` |
| `confirmation.choch.min_swings` | `1` |
| `confirmation.choch.buffer_atr` | `0.0` |
| `confirmation.choch.zone_pad_atr` | `0.5` |

### 2.4 Ausgabeobjekt

```
EntryConfirmation {
  pattern: ENGULFING | PIN | MINOR_CHOCH
  timeframe: TF                 # confirmation_tf
  bar_timestamp: UTC
  direction: BULLISH | BEARISH  # == D
  strength: float               # 0..1, muster-spezifisch (Body/Wick/Break-Distanz normiert)
  zone_ref: <FVG | OrderBlock>  # die Entry-Zone, in der bestätigt wurde
}
```

Der Entry-Market-Order wird zum `open` der **nächsten** `confirmation_tf`-Bar nach
`bar_timestamp` simuliert (`backtest-labeling.md` §5, Market-Fill + Slippage).
`information_cutoff` = `bar_timestamp`.

### 2.5 Was diese Muster **nicht** sind

- **Kein** eigenständiger Score-Faktor. Die Confirmation ist ein **Gate** für den
  `confirmation_market`-Modus (Muster vorhanden ⇒ Entry; nicht vorhanden ⇒ weiter `WAIT` bis
  Zone `MITIGATED`/`STALE` oder Expiry).
- **Kein** Ersatz für den CHoCH/BOS aus `SMC-SWEEP-REV-01` §7 (der bleibt Pflicht-Kettenglied auf
  dem Struktur-TF M5).
- **Keine** freien „Rejection/Momentum/Wick"-Detektoren — nur die drei oben.

---

## 3. Tests, die dieses Addendum verankert

- `WAIT` vs. `NO_TRADE`: Für jeden FSM-Zwischenzustand ohne Veto ⇒ `WAIT`; mit künstlichem Veto
  ⇒ `NO_TRADE`, Score nicht berechnet.
- `WAIT` nie nach `ARMED`.
- Kette abgebrochen (`SWEEP_BECAME_BREAKOUT`) im Zwischenzustand ⇒ `NO_TRADE`, nicht `WAIT`.
- `decision.emit_wait = false` ⇒ Zwischenzustände liefern `NO_TRADE`.
- Engulfing/Pin/Minor-CHoCH: je ein handkonstruierter M1-Chart, der das Muster exakt erfüllt
  bzw. knapp verfehlt (Grenzfall an jedem Parameter).
- Long/Short-Symmetrie für alle drei Muster.
- `confirmation_market`-Entry: ohne Muster bleibt State `ARMED` + Output `WAIT` bis Expiry.
