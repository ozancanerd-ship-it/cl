# Widerspruchs- & Veto-Logik

> **`strategy_version 0.1.1`:** §3 Wortlaut präzisiert (C4); §6 `resolve()` kennt jetzt `WAIT`
> (C6, Vollform in `SPEC-ADDENDUM-0.1.1.md` §1.2); C10 nutzt opposing LiquidityLevels als
> S/R-Proxy (C8); Veto V9 = pass-through ohne `portfolio_context` (C9).

**Zweck:** Regeln dafür, was passiert, wenn Signale sich widersprechen. **Grundsatz: ein starkes
positives Signal darf einen harten Negativfaktor niemals überstimmen.** Widersprüche werden
**nicht gemittelt** — ein ungelöster Widerspruch ergibt `NO_TRADE`.

Alle Schwellen `PROPOSED DEFAULT`. Konfig unter `contradictions.*`.

---

## 1. Signal-Hierarchie

Bei Konflikt gewinnt das **höher stehende** Element. Diese Rangordnung ist fix:

```
1. HARTE FILTER          News-Blackout · Spread/Slippage · Datenqualität · Kill-Switch · Regime EXTREME
   (Veto — können NICHT überstimmt werden, egal wie gut der Rest ist)
2. RISK / PORTFOLIO       Limits · korrelierte Exposure · Drawdown · Verlustserie
3. HTF-KONTEXT            D1/H4-Bias & -Regime
4. LIQUIDITÄT & SWEEP     die kausale Kette (Sweep → Reclaim → Displacement → Struktur)
5. LTF-STRUKTUR           M15/M5 CHoCH/BOS
6. LOKATION               Premium/Discount des relevanten Legs
7. ENTRY-FEINHEIT         FVG-/OB-Qualität, Bestätigungskerzen
```

**Regel:** Ein Element darf ein **niedriger** stehendes präzisieren oder verwerfen, aber ein
niedriger stehendes darf ein **höher** stehendes **nie** kompensieren.
Beispiel: eine perfekte M5-FVG (Rang 7) rettet kein Setup, dessen H4-Bias fehlt (Rang 3).

---

## 2. Multi-Timeframe-Directional-Agreement

Berechne je Timeframe `tf ∈ {D1, H4, M15, M5}` einen `dir_num(tf) ∈ {+1, 0, −1}`
(`TREND_UP = +1`, `RANGE/NEUTRAL = 0`, `TREND_DOWN = −1`; `UNCLEAR/CONFLICTING` ⇒ Sonderfall).

| Situation | Regel |
|-----------|-------|
| `D1` und `H4` gegensätzlich (`dir_num` +1 vs −1) | **Veto V1** → `NO_TRADE` (`REGIME_CONFLICTING`) |
| `D1` oder `H4` = `UNCLEAR` | `NO_TRADE` (`REGIME_UNCLEAR`) — Standard streng |
| HTF (D1+H4) einig, aber `M15` gegensätzlich | **erlaubt** — genau das ist die Prämisse von `SMC-SWEEP-REV-01` (kurzfristiger Gegen-Move in die Liquidität). `M15`-Gegenrichtung senkt aber `structure_clarity` in der Confidence. |
| HTF einig, `M15` einig, `M5` gegensätzlich | erlaubt, solange die Ketten-Gates (§7 des Setups) einen `M5`-Bruch in Richtung `D` liefern; sonst `NO_STRUCTURE_SHIFT` |
| `D1` = RANGE, `H4` = Trend | erlaubt (merged = schwacher Trend), reduzierte Größe (`regime.md` §7) |

**Disagreement-Score** (fürs Ledger/Confidence):
`mtf_disagreement = (|dir_num(D1) − dir_num(H4)| + 0.5·|dir_num(H4) − dir_num(M15)|) / 3`

---

## 3. Lokations-Sanity (harte Regel — Veto V2)

| Richtung | Zulässige Entry-Lokation (Mittelpunkt der Entry-Zone), Reference `swept_leg` |
|----------|------------------------------------------------------|
| `LONG` | `pd_position(zone_mid) ≤ setup.entry.max_pd_position` (Default `0.50`) |
| `SHORT` | `pd_position(zone_mid) ≥ 1 − setup.entry.max_pd_position` (Default `0.50`) |

**Maßgeblich ist der numerische Gate** (`0.1.1` C4). Der Default `0.50` erlaubt Entries bis
**einschließlich Equilibrium** in Trade-Richtung; ein strengerer Wert (z. B. `0.35`) verlangt
echten Discount/Premium. „auf der falschen Seite des Equilibriums" (`pd_position > 0.50` für Long
bzw. `< 0.50` für Short) ⇒ **`NO_TRADE` (`ENTRY_WRONG_SIDE_OF_EQUILIBRIUM`)**, unabhängig von
allen anderen Faktoren. Kein Score-Ausgleich.

---

## 4. Widerspruchs-Paar-Matrix

Jede Zeile: wenn **beide** Bedingungen zutreffen → definierte Auflösung.

| # | Bedingung A | Bedingung B | Auflösung |
|---|-------------|-------------|-----------|
| C1 | Setup-Richtung `LONG` | Buy-Side-Liquidität **darüber** wurde gerade gesweept **und gehalten** (Breakout, kein Reclaim) | `NO_TRADE` — B (Rang 4) schlägt Setup-Absicht |
| C2 | Sweep der Sell-Side (bullisch) | im selben Fenster **auch** Sweep der Buy-Side (`sweep.max_pools_in_window` überschritten) | `NO_TRADE` (`MESSY_LIQUIDITY`) — beide Seiten genommen = kein Edge |
| C3 | Technischer Bias `LONG` | `HIGH`-Impact-USD-Event in < Blackout | `NO_TRADE` (Veto V4) — Rang 1 schlägt Rang 3–7 |
| C4 | Score ≥ A+ | `data_confidence < 0.50` | `NO_TRADE` (Veto V6) — Rang 1 |
| C5 | Score ≥ A+ | RR < `rr.min_to_tp2` | `NO_TRADE` (Veto V8) |
| C6 | Setup-Richtung `LONG` | Portfolio bereits an `max_correlated_exposure` (z. B. BTC-Long offen, ETH-Long-Signal) | `NO_TRADE` (Veto V9) **oder** Größe auf Rest-Budget; wenn Rest < `sizing.min_fraction` ⇒ `NO_TRADE` |
| C7 | H4 `TREND_UP` | D1 `TREND_DOWN` | `NO_TRADE` (Veto V1) |
| C8 | Alle Ketten-Gates erfüllt | Regime `phase = COMPRESSION (coiled)` | `NO_TRADE` (Veto V3) — Setup braucht Displacement-Fähigkeit |
| C9 | FVG-Entry-Zone gültig | Zone liegt **innerhalb** `contradictions.opposing_zone_buffer_atr` einer starken **gegen**-`D` HTF-Zone (OB/FVG H4/D1, `strength ≥ 0.6`) | Negativfaktor (§5) **−**; wenn Überlappung > 50 % ⇒ `NO_TRADE` (`ENTRY_INTO_OPPOSING_HTF_ZONE`) |
| C10 | **opposing LiquidityLevel** (S/R-Proxy, `0.1.1` C8) am/über Entry (long) innerhalb `< rr.min_target_room_r × R` | Setup meldet Ziel darüber | `NO_TRADE` (Veto V8, Ziel-Raum) |
| C11 | LTF-CHoCH Richtung `D` vorhanden | dieser CHoCH bricht einen Swing, der weiter als `structure.max_break_distance_atr` entfernt war | `NO_STRUCTURE_SHIFT` — überdehnter Bruch zählt nicht |
| C12 | Setup `ARMED` | zweites, **gegenläufiges** Setup-Signal (anderer Setup-Typ) auf demselben Instrument | beide `NO_TRADE` bis einer der beiden alle Gates + höhere Confidence hat; bei Gleichstand: **kein** Trade |

| Parameter | PROPOSED DEFAULT |
|-----------|------------------|
| `contradictions.opposing_zone_buffer_atr` | `0.5` |
| `contradictions.opposing_zone_overlap_veto` | `0.50` |
| `sweep.max_pools_in_window` | `2` |

---

## 5. Negativfaktoren (Score-Abzug, kein Veto)

Diese senken den Score, führen aber nicht allein zu `NO_TRADE` (es sei denn, eine Veto-Schwelle
in §4 wird zusätzlich gerissen). Details in `scoring-rubric.md`.

| Negativfaktor | Messung | Abzug (PROPOSED DEFAULT) |
|---------------|---------|--------------------------|
| `messy_sweep` | `1 < pools_in_window ≤ 2`, oder Penetration am Rand des erlaubten Bands | −`8` Punkte |
| `proximity_opposing_htf_zone` | Entry-Zone ≤ `opposing_zone_buffer_atr` einer gegen-`D` HTF-Zone (aber < 50 % Überlappung) | −`10` |
| `stale_structure` | beteiligte Swings `bars_since_confirmation > liquidity.age_saturation` | −`5` |
| `weak_displacement` | `net_move_atr` nur knapp über `displacement.min_atr` (< 1.2×) | −`6` |
| `mtf_partial_disagreement` | `mtf_disagreement ∈ (0.33, 0.66)` | −`7` |
| `wide_sl` | `distance(entry, SL) > 2.0 × ATR(sweep.tf)` (aber unter Cap) | −`5` |
| `late_session` | Entry in den letzten `contradictions.late_session_min` einer Session | −`4` |

---

## 6. Auflösungs-Algorithmus (verbindlich)

> **`0.1.1`:** Die WAIT-bewusste Vollform (mit FSM-Zwischenzuständen) steht in
> `SPEC-ADDENDUM-0.1.1.md` §1.2. Der Ablauf unten bleibt gültig für ein **vollständig geformtes**
> Setup (`state == ARMED`). Ergänzungen: Schritt 4 `collect_vetoes` behandelt **V9 als
> pass-through**, wenn kein `portfolio_context` übergeben wurde (C9); Schritt 7
> `apply_portfolio_constraints` wird nur mit vorhandenem `portfolio_context` ausgeführt.

```
resolve(context) -> Decision:            # Decision ∈ {BUY, SELL, WAIT, NO_TRADE}
    # Schritt 1: harte Filter / No-Trade
    reasons = check_no_trade(context)            # no-trade.md
    if reasons: return NO_TRADE(reasons)

    # Schritt 2: Regime-Gate
    r = regime_gate(context)                      # regime.md §9
    if r != OK: return NO_TRADE(r)

    # Schritt 3: Ketten-Gates des Setups
    g = setup_chain_gates(context)                # SMC-SWEEP-REV-01 §2–§10,§16,§18
    if g != OK: return NO_TRADE(g)

    # Schritt 4: Veto-Prüfung (V1–V10)
    vetoes = collect_vetoes(context)              # §4 + Setup §23
    if vetoes: return NO_TRADE(vetoes)            # <-- Score wird NICHT berechnet

    # Schritt 5: Widerspruchs-Paar-Matrix (nicht-Veto-Ausgänge)
    c = contradiction_matrix(context)             # §4 Zeilen ohne bereits gezogenes Veto
    if c.hard: return NO_TRADE(c.reasons)
    negatives = c.negative_factors                # §5

    # Schritt 6: ERST JETZT Score
    score = weighted_score(context) - sum(negatives)
    tier  = tier_from(score, setup_confidence)    # SMC-SWEEP-REV-01 §21
    if tier == NO_TRADE: return NO_TRADE(SCORE_BELOW_B)

    # Schritt 7: Portfolio/Sizing kann Tier weiter senken
    tier, size = apply_portfolio_constraints(tier, context)   # sizing.md
    if tier == NO_TRADE: return NO_TRADE(PORTFOLIO_CORRELATION)

    return TRADE(tier, size, ...)
```

**Kerneigenschaft:** Zwischen Schritt 4 und Schritt 6 gibt es keine Möglichkeit, ein Veto durch
Punkte aufzuheben. Der Score existiert nur in einer Welt, in der bereits alle harten
Negativfaktoren `false` sind.

---

## 7. Tests, die diese Datei verankert

- Für **jedes** Veto V1–V10: ein Test „maximaler positiver Kontext + genau dieses Veto" ⇒
  Ergebnis `NO_TRADE`, Score-Funktion nicht aufgerufen.
- Für jede Matrix-Zeile C1–C12: Test der definierten Auflösung.
- Test: `mtf_disagreement` im mittleren Bereich ⇒ Negativfaktor gezogen, aber kein Veto.
- Test: zwei gegenläufige Setups gleichzeitig ⇒ kein Trade.

---

## 8. Zu bestätigen / zu validieren

- **Strenge bei HTF `UNCLEAR`** (Standard: kein Trade) — auch in `regime.md` §10.
- **Abzugswerte in §5** (−4 bis −10 Punkte): völlig unkalibriert, reine Startwerte. Sie müssen
  gegen realisierte Expectancy je Negativfaktor geprüft werden (`anti-overfitting.md`).
- **C9-Overlap-Veto bei 50 %**: Startwert.
- **C12 (gegenläufige Setups)**: konservativ „beide aus". Alternative wäre „höhere Confidence
  gewinnt" — bewusst **nicht** gewählt, um Ambiguität nicht zu belohnen.
