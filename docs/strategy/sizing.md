# Risk & Position Sizing

> **`strategy_version 0.1.1`** — Risikostufen A+/A/B = **1.00 / 0.65 / 0.40 %** (Audit-Punkt C1,
> `DECISIONS-0.1.1.md`). Ersetzt die 0.50/0.35/0.25 % aus `0.1.0`. Weiterhin `PROPOSED DEFAULT`.

**Grundsatz:** Die Positionsgröße wird **niemals** vom gewünschten Gewinn bestimmt. Sie ergibt
sich aus **Account Equity · erlaubtem Risiko · Stop-Loss-Distanz · Volatilität · Hebel · Margin ·
Portfolio-Exposure · korrelierter Exposure · Liquidität**.

**Hebel darf die Position (Notional/Margin-Effizienz) vergrößern, aber niemals den maximal
erlaubten Verlust überschreiben.** Der Stop-Loss definiert `1R`; `1R` ist die Obergrenze des
Verlusts, punkt.

**Der Hebel ist kein Strategieparameter.** Es gibt **keine starren pauschalen Hebel-Caps**. Der
Hebel wird **dynamisch pro Trade** berechnet: der Algorithmus bestimmt **zuerst** das zulässige
Risiko (in EUR/USD), **dann** die notwendige Positionsgröße, **dann** den Hebel, der diese
Positionsgröße bei gegebener Margin ermöglicht — begrenzt nur durch reale Constraints
(Liquidationsabstand, Maintenance Margin, Broker-/Exchange-Limits, Liquidität, freie Margin).

Alle Prozentwerte sind `PROPOSED DEFAULT` und **bewusst noch nicht final**. Konfig unter
`sizing.*` und `risk.*`.

---

## 1. Risikostufen — kontrolliert aggressiv (Nutzer-Entscheidung 2026-08-28)

Das System soll **kontrolliert aggressiver** handeln können und **sinnvolle gehebelte
Positionen** ermöglichen — **kein künstlich extrem konservatives Mini-Trade-System**. Der Hebel
darf aber **niemals das erlaubte Verlustbudget überschreiben**.

**Zwei getrennte Konfig-Ebenen:**

| Ebene | Zweck | PROPOSED DEFAULT |
|-------|-------|------------------|
| **`risk.risk_tiers.<tier>`** — Basis-Risikobudget je Setup-Stufe | Grundgröße | siehe unten |
| **`risk.hard_max_risk_pct`** — absolute Obergrenze pro Trade, egal was die Faktoren sagen | harte Schranke | `2.0 %` |

| Stufe | Herkunft | Basis-`risk_pct` (**PROPOSED DEFAULT**, validierungspflichtig) |
|-------|----------|--------------------------------------------------------------|
| **A+** | Score ≥ 85 **und** `setup_confidence ≥ 0.80` | `1.00 %` der Equity |
| **A**  | Score ≥ 75 **und** `setup_confidence ≥ 0.70` | `0.65 %` |
| **B**  | Score ≥ 65 **und** `setup_confidence ≥ 0.60` | `0.40 %` |
| **NO TRADE** | sonst, oder ein Veto, oder Portfolio-Constraint | `0 %` |

> Diese Bänder sind **PROPOSED DEFAULTS**. **Finale Risk-Bänder werden durch Backtest, OOS,
> Walk-Forward und Monte-Carlo validiert** (`anti-overfitting.md` §9): Kriterium ist eine
> akzeptable Ruin-Wahrscheinlichkeit (< 5 % im MC), Max-DD innerhalb des MC-95-%-Bands und
> stabile Expectancy je Stufe. Bis dahin gelten die Werte als Hypothese.

### 1a. Dynamischer Größen-Multiplikator

`effective_risk_pct = base_risk_pct[tier] × Π(faktoren)`, dann hart gedeckelt:
`effective_risk_pct = min(effective_risk_pct, risk.hard_max_risk_pct)`.

Jeder Faktor ∈ `[floor, ceiling]`; `Π(faktoren)` ∈ `[sizing.multiplier.min, sizing.multiplier.max]`.

| Faktor | Input | Wirkung (PROPOSED DEFAULT) |
|--------|-------|---------------------------|
| `setup_quality` | Score / Confidence über der Stufen-Schwelle | 0.7 … 1.15 |
| `volatility` | Regime-Vol-Perzentil | `HIGH` → 0.7 · `NORMAL` → 1.0 · `LOW` (falls erlaubt) → 0.85 |
| `drawdown` | aktueller Equity-Drawdown vom Hoch | 0 % → 1.0 · nähert sich `drawdown.max_total_pct` → linear → 0.3 |
| `portfolio_exposure` | Portfolio-Heat vs. Limit | > 70 % des Limits → 0.6 |
| `correlation` | Cluster-Auslastung | > 70 % des Cluster-Caps → 0.5 |
| `liquidity` | Spread / Order-Book-Tiefe vs. Ordergröße | dünn → 0.5 |
| `equity` | absolute Kontogröße | sehr klein (< `sizing.small_account_eur`) → 1.0 (kein Straf-Faktor, aber Mindest-Notional-Check greift) |

| Parameter | PROPOSED DEFAULT |
|-----------|------------------|
| `risk.hard_max_risk_pct` | `2.0` % |
| `sizing.multiplier.min` | `0.25` |
| `sizing.multiplier.max` | `1.20` |  (> 1.0 nur nach positiver MC/WF-Validierung freischalten; Start `1.0`) |
| `sizing.small_account_eur` | `200` |

**Verbote (im Code erzwungen, mit Tests):** kein Martingale · keine Verlustprogression · kein
All-In · kein Revenge Trading (nach Verlustserie `LOSS_STREAK_REVIEW`, **nicht** größere Position)
· **Liquidationsnähe ist kein Setup** (`sizing.liq.min_buffer_atr` wird immer erzwungen).

---

## 2. Sizing-Algorithmus (Reihenfolge verbindlich)

**Kernprinzip:** *zuerst Risiko → dann Größe → dann Hebel*. Der Hebel ist die **Folge**, nie der
Ausgangspunkt.

```
Eingaben:
  equity, free_capital        # Account Equity + tatsächlich verfügbares Kapital (Paper/Backtest: simuliert)
  tier                        # A+ | A | B  (sonst: kein Aufruf)
  entry_price, sl_price       # aus dem Setup
  atr_entry_tf, atr_daily     # Volatilität
  spread, est_slippage        # aktueller Spread + erwartete Slippage
  instrument_refdata          # tick_size, lot_size, min_notional, contract_multiplier,
                              #   margin_tiers, maintenance_margin, fee_schedule, max_leverage_broker,
                              #   max_position_broker
  funding_rate                # falls Perp
  portfolio_state             # offene Positionen, Exposure, Faktor-Exposure, offenes Risiko, freie Margin
  correlation_model           # ρ-Matrix (statisch + gemessen)
  market_liquidity            # ADV / Order-Book-Tiefe (soweit verfügbar)
  limits                      # risk.yaml

# ── Schritt 1: erlaubtes Risiko in Kontowährung ──────────────────────────────
risk_pct        = sizing.risk_pct[tier]
risk_budget_ccy = equity * risk_pct

# ── Schritt 2: Konto-Ebene begrenzen ────────────────────────────────────────
daily_remaining    = max(0, limits.daily.max_loss_pct  * equity - realized_and_open_loss_today)
weekly_remaining   = max(0, limits.weekly.max_loss_pct * equity - realized_and_open_loss_week)
drawdown_headroom  = max(0, limits.drawdown.max_total_pct * equity_high - current_drawdown_ccy)
heat_remaining     = max(0, limits.portfolio.max_open_risk_pct * equity - sum_open_risk_ccy)
available_ccy      = min(risk_budget_ccy, daily_remaining, weekly_remaining,
                         drawdown_headroom, heat_remaining)
if available_ccy < sizing.min_fraction * risk_budget_ccy:
    return NO_TRADE(RISK_BUDGET_EXHAUSTED)

# ── Schritt 3: korrelierte Exposure ────────────────────────────────────────
cluster           = correlation_model.cluster_of(instrument, direction)   # §4
cluster_cap_ccy   = limits.portfolio.max_correlated_exposure_pct * equity  # als RISIKO interpretiert
cluster_remaining = max(0, cluster_cap_ccy - sum_open_risk_ccy_in(cluster))
available_ccy     = min(available_ccy, cluster_remaining)
if available_ccy < sizing.min_fraction * risk_budget_ccy:
    return NO_TRADE(PORTFOLIO_CORRELATION)          # Veto V9

# ── Schritt 4: effektive SL-Distanz inkl. Ausführungskosten ────────────────
# Der reale Verlust bei SL-Fill ist SL-Distanz + Slippage + anteilige Gebühren.
sl_distance_price   = abs(entry_price - sl_price)
exec_cushion_price  = est_slippage + spread * sizing.exec.spread_in_risk_mult
eff_sl_distance     = sl_distance_price + exec_cushion_price
risk_per_unit       = eff_sl_distance * instrument_refdata.contract_multiplier
                       + fee_per_unit(entry_price, instrument_refdata)      # Entry+Exit-Fees je Einheit

# ── Schritt 5: Roh-Positionsgröße AUS DEM RISIKO ──────────────────────────
raw_size = available_ccy / risk_per_unit          # <-- Größe folgt aus Risiko, nicht aus Gewinn

# ── Schritt 6: Volatilitäts-Cap ───────────────────────────────────────────
position_daily_vol_ccy = raw_size * instrument_refdata.contract_multiplier * atr_daily
if position_daily_vol_ccy > sizing.vol.max_position_vol_pct * equity:
    raw_size *= (sizing.vol.max_position_vol_pct * equity) / position_daily_vol_ccy

# ── Schritt 7: Liquiditäts-Cap ────────────────────────────────────────────
raw_size = min(raw_size,
               sizing.liquidity.max_adv_pct  * recent_avg_volume_per_bar(instrument),
               sizing.liquidity.max_depth_pct * visible_depth_at_entry(instrument))   # depth falls verfügbar

# ── Schritt 8: notwendigen Hebel DYNAMISCH bestimmen ──────────────────────
notional         = raw_size * entry_price * instrument_refdata.contract_multiplier
# Hebel, den DIESE Position bräuchte, damit die Margin ins verfügbare Kapital passt:
margin_budget    = min(free_capital, portfolio_state.free_margin) * sizing.margin.max_utilization
needed_leverage  = notional / max(margin_budget, tick)          # rein rechnerisch
leverage_used    = clamp(needed_leverage, 1, instrument_refdata.max_leverage_broker)

# Reicht der bei den Broker-Grenzen mögliche Hebel nicht für die risikobasierte Größe?
if needed_leverage > instrument_refdata.max_leverage_broker:
    # Position auf das verkleinern, was mit max. Broker-Hebel + Margin-Budget geht.
    # Das REDUZIERT die Größe (und damit das genutzte Risiko), erhöht es NIE.
    raw_size      = (margin_budget * instrument_refdata.max_leverage_broker) /
                    (entry_price * instrument_refdata.contract_multiplier)
    notional      = raw_size * entry_price * instrument_refdata.contract_multiplier
    leverage_used = instrument_refdata.max_leverage_broker

required_margin  = notional / leverage_used
if required_margin > margin_budget:
    return NO_TRADE(INSUFFICIENT_MARGIN)

# ── Schritt 9: Liquidationsabstand prüfen ─────────────────────────────────
liq_price = estimate_liquidation_price(entry_price, direction, leverage_used,
                                       instrument_refdata.maintenance_margin,
                                       instrument_refdata.margin_tiers)
min_gap   = eff_sl_distance + sizing.liq.min_buffer_atr * atr_entry_tf
if distance(entry_price, liq_price) < min_gap:
    # iterativ Hebel senken (Größe sinkt), bis Liq-Abstand passt oder Hebel = 1
    reduce_leverage_until_liq_ok()          # senkt raw_size, nie das Risiko
    if still_violated:
        return NO_TRADE(LIQUIDATION_TOO_CLOSE)

# ── Schritt 10: Broker-Positionslimit ────────────────────────────────────
if raw_size > instrument_refdata.max_position_broker:
    raw_size = instrument_refdata.max_position_broker

# ── Schritt 11: Funding-Vorabschätzung (Perp) ────────────────────────────
est_funding_ccy = expected_funding_cost(notional, funding_rate, expected_holding_time)
if est_funding_ccy > available_ccy * sizing.funding.max_share_of_risk:
    return NO_TRADE(FUNDING_COST_EXCESSIVE)

# ── Schritt 12: Rundung + Re-Check ──────────────────────────────────────
size = round_down_to_lot(raw_size, instrument_refdata.lot_size)
realized_risk_ccy = size * risk_per_unit
if size * entry_price * contract_multiplier < instrument_refdata.min_notional:
    return NO_TRADE(SIZE_BELOW_MIN)
if size < sizing.min_fraction * (available_ccy / risk_per_unit):
    return NO_TRADE(SIZE_BELOW_MIN)          # risikobasierte Zielgröße nicht erreichbar
if realized_risk_ccy > risk_budget_ccy * sizing.round.tolerance:
    size = round_down_one_more_lot(size)

return Position(size, realized_risk_ccy, leverage_used, required_margin,
                liq_price, cluster, est_funding_ccy)
```

### Parameter

| Parameter | PROPOSED DEFAULT | Bedeutung |
|-----------|------------------|-----------|
| `sizing.risk_pct.A_plus` / `.A` / `.B` | `1.00 %` / `0.65 %` / `0.40 %` | Risiko je Stufe (**Nutzer-Festlegung 2026-08-28 / `0.1.1` C1, nicht final**) |
| `sizing.min_fraction` | `0.5` | Wird die risikobasierte Zielgröße nicht zu ≥ diesem Anteil erreichbar ⇒ `NO_TRADE` (kein „Mini-Trade", keine Kompromiss-Größe) |
| `sizing.vol.max_position_vol_pct` | `1.5 %` | max. Beitrag einer Position zur Tages-Portfolio-Vola |
| `sizing.exec.spread_in_risk_mult` | `0.5` | Anteil des Spreads, der in die effektive SL-Distanz eingerechnet wird |
| `sizing.margin.max_utilization` | `0.5` | max. Anteil des verfügbaren Margin-Budgets je Position |
| `sizing.liq.min_buffer_atr` | `2.0` | Mindestabstand Liquidation ↔ (SL + Exec-Puffer) in ATR(entry_tf) |
| `sizing.liquidity.max_adv_pct` | `1 %` | max. Anteil am durchschnittlichen Bar-Volumen |
| `sizing.liquidity.max_depth_pct` | `10 %` | max. Anteil an sichtbarer Order-Book-Tiefe |
| `sizing.funding.max_share_of_risk` | `0.25` | erwartete Funding-Kosten dürfen höchstens diesen Anteil des Trade-Risikos betragen |
| `sizing.round.tolerance` | `1.05` | erlaubte Risiko-Überschreitung durch Rundung |

> **Es gibt bewusst KEINEN Parameter `sizing.leverage.max`.** Die einzige Hebel-Obergrenze ist
> `instrument_refdata.max_leverage_broker` (reale Exchange-Grenze). Der genutzte Hebel ergibt
> sich rechnerisch aus Notional / Margin-Budget und wird durch Liquidationsabstand,
> Maintenance Margin und die risikobasierte Größe nach unten korrigiert.
>
> **Beispiel (Nutzer-Vorgabe):** Equity 50 EUR, sinnvolle Positionsgröße lt. Setup ≈ 200–300 EUR.
> Das System **darf** den dafür nötigen Hebel (hier ~4–6×) berechnen und nutzen — **sofern**
> SL-Risiko ≤ Stufen-Budget, Liquidationsabstand ≥ `eff_sl_distance + min_buffer`, Margin
> vorhanden, Gebühren/Funding im Rahmen, Portfolio-/Korrelations-Limits eingehalten und der
> Broker-Hebel ausreicht. Ist **eine** dieser Bedingungen verletzt und lässt sich die Position
> nicht regelkonform verkleinern ⇒ **`NO_TRADE`**.

---

## 3. Invarianten (als Tests verankert)

1. **`realized_risk_ccy ≤ risk_budget_ccy × round.tolerance`** — immer. Kein Pfad erhöht das
   Risiko über das Stufen-Budget.
2. **Hebel ändert `1R` nicht.** Test: gleiche Eingaben, `max_leverage_broker` = 5 vs. 25
   (beide ausreichend) ⇒ **identisches** `realized_risk_ccy`, nur `leverage_used` /
   `required_margin` unterscheiden sich.
3. **Reihenfolge Risiko → Größe → Hebel.** Test: der berechnete `leverage_used` ist eine reine
   Funktion von (risikobasierter Größe, Margin-Budget, Broker-Grenze) — er ist **nie** Eingabe
   der Größenberechnung.
4. **Positionsgröße ist unabhängig von TP-Distanzen.** Test: TP1/2/3 variieren ⇒ Größe konstant.
   Es gibt keinen Code-Pfad, der aus einem gewünschten Gewinn rückwärts rechnet.
5. **Kein Martingale / kein Averaging-down / keine Verlustprogression.** Es gibt **keinen**
   Code-Pfad, der die Größe nach einem Verlust erhöht oder eine Verlustposition vergrößert. Nach
   Verlustserie: `NO_TRADE` (`LOSS_STREAK_REVIEW`), **nicht** Größenänderung.
6. **`NO_TRADE` statt Kompromiss-Größe:** kann die risikobasierte Zielgröße nicht zu ≥
   `min_fraction` erreicht werden (Margin, Liquidation, Broker-Limit, Liquidität) ⇒ **kein
   Trade**, nicht „so groß wie es geht".
7. **Leverage umgeht kein Veto.** Test: bei aktivem Risk-Engine-Veto (V1–V10) wird die
   Sizing-Funktion gar nicht erst aufgerufen; es gibt keinen Pfad, der ein Veto durch
   Hebel/Größe „löst".

---

## 4. Korrelations- & Faktor-Exposure (MVP-Umfang)

### 4.1 Korrelationsquelle
`ρ(i, j)` = `max( ρ_static(i,j) , ρ_measured(i,j) )` (konservativ das höhere).
- `ρ_static`: gepflegte Tabelle bekannter Beziehungen — **PROPOSED DEFAULT**:
  `BTCUSDT↔ETHUSDT = 0.80`, `crypto↔crypto (Alt) = 0.7`, `XAUUSD↔DXY = −0.7`,
  `equity↔SPX = 0.6`, `EURUSD↔DXY = −0.9`.
- `ρ_measured`: rollierende Korrelation der Log-Returns über `sizing.corr.window`
  (**PROPOSED DEFAULT `30`** Tage) auf `sizing.corr.return_tf` (**PROPOSED DEFAULT `H1`**).

### 4.2 Cluster
Ein **Cluster** ist eine Menge offener + geplanter Positionen mit paarweise `|ρ| ≥
sizing.corr.threshold` (**PROPOSED DEFAULT `0.70`**) **und gleicher effektiver Richtung**
(Netting: Long BTC + Short ETH bei ρ>0 ⇒ teilweise gegenläufig ⇒ reduzierte Cluster-Last).
- Für den MVP: BTCUSDT-Long und ETHUSDT-Long sind **ein** Cluster.
- Cluster-Risiko-Cap: `risk.portfolio.max_correlated_exposure_pct` der Equity, **als Summe des
  offenen 1R-Risikos** interpretiert (nicht Notional).

### 4.3 Faktor-Exposure (Vorbereitung, MVP minimal)
Jedes Instrument trägt Gewichte zu Faktoren: `USD`, `RATES`, `EQUITY_BETA`, `CRYPTO_BETA`,
`GOLD`. MVP: nur `CRYPTO_BETA` (BTC/ETH ≈ 1.0) und `USD` (invers, ≈ −0.3) aktiv genutzt.
Voll ausgebaut in der späteren `Exposure-/Faktor-Risikomodell`-Komponente
(`ARCHITECTURE_GAP_AUDIT.md` G-21).

---

## 5. Tagesverlust / Wochenverlust / Drawdown — Aktionen

| Ereignis | Aktion (PROPOSED DEFAULT) | automatisiert? |
|----------|---------------------------|----------------|
| `daily.max_loss_pct` erreicht | **keine neuen Entries** bis Tageswechsel (UTC); offene Trades laufen mit ihren Regeln weiter | ja |
| `weekly.max_loss_pct` erreicht | keine neuen Entries bis Wochenwechsel | ja |
| `drawdown.max_total_pct` erreicht | **globaler Kill-Switch** (stop + Alert) + **manuelle Freigabe** nötig, um weiterzumachen | Stop ja, Freigabe **nein** |
| `LOSS_STREAK_REVIEW` (`n` Verluste in Folge) | Pause + Alert + **manuelle Entscheidung** | Pause ja, Weiter **nein** |

**Kein** automatisches Flatten bei Tages-/Wochenlimit (MVP) — nur Entry-Stopp. Automatisches
Flatten nur über Kill-Switch / strukturelle Invalidierung / News-Regeln.

---

## 6. Capital Allocation Engine (VORBEREITUNG — nicht implementieren)

Das System unterscheidet langfristig **drei Handels-Horizonte**:

| Horizont | Typische Haltedauer | Beispiel-Setups | Kapital-Topf |
|----------|---------------------|-----------------|--------------|
| **Long-Term Investment** | Wochen–Monate | Akkumulation, DCA, Makro-Trend | `allocation.buckets.long_term` |
| **Swing Trading** | Tage–Wochen | HTF-SMC, Trend-Continuation | `allocation.buckets.swing` |
| **Short-Term Trading** | Stunden–Tage | `SMC-SWEEP-REV-01`, Intraday-Reversals | `allocation.buckets.short_term` |

### Vorgesehene Schnittstelle (Stub, keine Logik jetzt)
```
CapitalAllocationEngine:
  inputs:
    monthly_budget_eur          # variabel, typ. 200–400 EUR
    current_equity_by_bucket
    portfolio_state             # Positionen, Exposure, Korrelation
    market_regime               # global + je Asset
    opportunity_signals         # Anzahl/Qualität aktueller Setups je Bucket
    risk_limits
  outputs:
    allocation_plan:
      per_bucket_target_weight
      per_bucket_deposit_eur     # wie das Monatsbudget verteilt wird
      per_instrument_max_risk
      recommendation: ALLOCATE | HOLD | DO_NOT_ADD    # explizit "nicht aufstocken"
      rationale: str             # ins Decision Ledger
```

**Anforderungen an die spätere Engine (dokumentiert, damit die Architektur passt):**
- Sie darf **`DO_NOT_ADD`** empfehlen (z. B. Regime `UNCLEAR`/`EXTREME`, Korrelations-Cluster voll,
  Drawdown aktiv, keine A/A+-Gelegenheiten) — Nichtstun ist ein gültiges Ergebnis.
- Sie erhöht **nie** das Risiko pro Trade; sie verteilt nur **Kapital** auf Töpfe.
- Das variable Monatsbudget (200–400 EUR) fließt gemäß `allocation_plan.per_bucket_deposit_eur`,
  nicht automatisch „alles in den heißesten Topf".
- Sie berücksichtigt Portfolio-Gewichtung, Korrelation, Risiko, Marktregime und Gelegenheiten.
- Bis zur Implementierung: das Monatsbudget wird **manuell** zugeteilt; die Engine ist ein
  benannter Platzhalter in `ARCHITECTURE.md` (neues Paket `allocation/`).

---

## 7. Ins Decision Ledger / Trade-Record

```
SizingRecord {
  tier, risk_pct, risk_budget_ccy
  available_ccy, limiting_constraint        # z.B. "cluster_remaining"
  sl_distance_price, sl_distance_atr
  raw_size, vol_capped, leverage_capped, margin_capped, liquidity_capped
  final_size, realized_risk_ccy, realized_risk_pct
  leverage_used, required_margin, liquidation_price, liq_buffer_atr
  cluster_id, cluster_open_risk_ccy_before, cluster_open_risk_ccy_after
}
```

---

## 8. Status der Festlegungen (Nutzer-Bestätigung 2026-08-28)

| Punkt | Status |
|-------|--------|
| `risk_pct` A+/A/B = `1.00 / 0.65 / 0.40 %` (`0.1.1` C1) | **bestätigt als PROPOSED DEFAULT** — muss durch Backtest, OOS, Walk-Forward, Drawdown-Analyse validiert werden (Kriterium: Ruin-Wahrscheinlichkeit < 5 %, Max-DD im MC-95-%-Band) |
| **Keine starren Hebel-Caps als Strategieparameter** | **bestätigt** — Hebel wird dynamisch pro Trade berechnet (§2), einzige Obergrenze = reale Broker-Grenze |
| Reihenfolge Risiko → Größe → Hebel | **bestätigt** (Invariante §3) |
| Position nie aus gewünschtem Gewinn rückwärts | **bestätigt** (Invariante §3.4) |
| Kein Martingale / keine Verlustprogression / keine Auto-Risikoerhöhung nach Verlusten | **bestätigt** (Invariante §3.5) |
| Leverage umgeht kein Risk-Engine-Veto | **bestätigt** (Invariante §3.7) |
| `max_correlated_exposure_pct` als **Risiko** (Summe 1R) interpretiert | **bestätigt** |
| Kein Auto-Flatten bei Tages-/Wochenlimit (nur Entry-Stopp); separater Emergency Kill Switch bleibt | **bestätigt** (Entscheidung 10) |
| Drei-Horizont-Modell + `allocation/`-Paket als Platzhalter | **bestätigt** (Entscheidung 14) |

**Noch zu validieren (empirisch):**
- `ρ_static(BTC,ETH) = 0.80` — Startwert; `ρ_measured` überschreibt, sobald genug Daten.
- `sizing.vol.max_position_vol_pct`, `sizing.margin.max_utilization`, `sizing.liq.min_buffer_atr`,
  `sizing.funding.max_share_of_risk` — alle Startwerte.
- Ob `min_fraction = 0.5` (kein Trade unter halber Zielgröße) zu streng/zu lasch ist.
