# Invalidierungs-Modell

**Zweck:** „Die Handelsidee ist falsch" ist **etwas anderes** als „der Stop-Loss wurde getroffen"
und **etwas anderes** als „das Ziel wurde erreicht". Dieses Dokument definiert alle vier
Ausstiegs-/Abbruch-Klassen objektiv und legt ihre **Auswertungsreihenfolge** fest.

Alle Schwellen `PROPOSED DEFAULT`. Konfig unter `invalidation.*` bzw. setup-lokal.

---

## 1. Vier Klassen

| Klasse | Wann | Wirkung |
|--------|------|---------|
| **A — Kandidaten-Invalidierung** (pre-entry) | Setup ist `ARMED`, Entry-Order **nicht** gefüllt | Kandidat verwerfen, Order stornieren, zurück zu `SCANNING` |
| **B — Strukturelle Invalidierung** (post-entry) | Position offen, die **Prämisse** der Idee ist gebrochen | **sofortiger Market-Exit**, auch wenn SL nicht getroffen |
| **C — Stop-Loss** | Preis erreicht den harten SL | Exit zum SL (bzw. schlechter bei Slippage) |
| **D — Zeit-/Dead-Trade-Exit** | Position bewegt sich nicht, `max_holding` erreicht | Market-Exit |

Zusätzlich (kein „Invalidierung", aber im selben Auswertungslauf): **Take-Profit** (TP1/2/3) und
**Trailing-Stop**.

---

## 2. Klasse A — Kandidaten-Invalidierung (pre-entry)

Gilt, solange die State Machine in `ARMED` ist und kein Fill vorliegt.

| Auslöser (Enum `CandidateInvalidation`) | Objektive Bedingung |
|-----------------------------------------|---------------------|
| `RE_SWEEP` | `confirmed close` auf `sweep.timeframe` erneut jenseits des Sweep-Extrems |
| `COUNTER_DISPLACEMENT` | Displacement (`primitives.md` §7) **gegen** `D` auf `displacement.timeframe` |
| `COUNTER_CHOCH` | CHoCH gegen `D` auf `structure.timeframe` |
| `BIAS_LOST` | HTF-Bias (`SMC-SWEEP-REV-01` §2) wird `NONE` oder dreht |
| `REGIME_LOST` | Regime-Gate (`regime.md`) nicht mehr erfüllt |
| `ZONE_CONSUMED` | Entry-Zone erreicht `MITIGATED` (`fill_fraction ≥ mitigation.consumed_threshold`) ohne Fill |
| `ZONE_STALE` | Entry-Zone erreicht `max_age_bars` |
| `CANDIDATE_EXPIRY` | `expiry.armed_bars` Bars ohne Fill **oder** Session-Grenze bei `expire_at_session_end` |
| `NEW_NOTRADE_CONDITION` | eine globale No-Trade-Bedingung (`no-trade.md`) wird während `ARMED` wahr |

**Wirkung:** Entry-Order stornieren (mit Idempotenz-Key), Kandidat-Record ins Decision Ledger
(`decision = CANDIDATE_INVALIDATED`, `reason`), State → `SCANNING`.

---

## 3. Klasse B — Strukturelle Invalidierung (post-entry)

Gilt, sobald eine Position offen ist. **Prämissen-Bruch ⇒ raus, unabhängig vom P/L.**

| Auslöser (Enum `StructuralInvalidation`) | Objektive Bedingung | Setup-Bezug |
|------------------------------------------|---------------------|-------------|
| `RECLAIM_THESIS_BROKEN` | `confirmed close` auf `structure.timeframe` jenseits des Sweep-Extrems | Der Sweep war doch ein Breakout → ganze Idee hinfällig |
| `COUNTER_CHOCH_POST` | CHoCH **gegen** `D` auf `structure.timeframe` | kurzfristige Struktur dreht |
| `HTF_BIAS_FLIP` | `merged_htf.directional` dreht in Gegenrichtung (`confirmed`, mit Hysterese) | übergeordnete Richtung weg |
| `ENTRY_ZONE_FAILED` | Preis schließt jenseits der **distalen** Kante der Entry-Zone (die Zone „hält" nicht) und `invalidation.zone_fail_confirm = true` | die POI hat nicht funktioniert |
| `PREMISE_IFVG` | die getradete FVG wird zur IFVG gegen uns (`primitives.md` §9) | Imbalance invertiert |

**Wirkung:** Market-Exit der **gesamten** Rest-Position, `exit_reason = STRUCT_INVALIDATION`,
Trade-Record. **Kein** Warten auf den SL.

| Parameter | PROPOSED DEFAULT | Bedeutung |
|-----------|------------------|-----------|
| `invalidation.zone_fail_confirm` | `true` | `ENTRY_ZONE_FAILED` erst bei `confirmed close`, nicht bei Wick |
| `invalidation.htf_flip_hysteresis_bars` | `2` | Bestätigungs-Bars für `HTF_BIAS_FLIP` |
| `invalidation.structure_tf` | `M5` | Timeframe für `RECLAIM_THESIS_BROKEN` / `COUNTER_CHOCH_POST` |

*Warum validieren:* Klasse B soll Verluste **kleiner** machen als der volle SL (früherer Ausstieg
bei gebrochener These). Der Backtest muss zeigen, dass das im Mittel stimmt — sonst ist Klasse B
nur „vorzeitiges Aussteigen aus Gewinnern". MFE/MAE-Analyse pro `StructuralInvalidation`-Grund.

---

## 4. Klasse C — Stop-Loss

- Der SL ist **hart** und wird als Broker-Order geführt (Demo/Live) bzw. bar-genau simuliert
  (Backtest/Paper), nicht nur „im Kopf".
- Definition der SL-Distanz: setup-spezifisch (`SMC-SWEEP-REV-01` §10).
- **Verschiebung nur in eine Richtung** (Richtung „weniger Risiko"): nach TP1 → Break-even; per
  Trailing (§6). **Nie** weiter weg vom Einstieg. Ein „Nachziehen in den Verlust" ist verboten
  (Verbot aus `README.md` / `risk.example.yaml`).
- `exit_reason = SL` (bzw. `SL_BREAKEVEN` / `SL_TRAILED`, wenn der Stop bereits ≥ Entry lag).

---

## 5. Klasse D — Zeit-/Dead-Trade-Exit

| Auslöser | Bedingung | PROPOSED DEFAULT |
|----------|-----------|------------------|
| `TIME_EXPIRY` | `max_holding_bars` erreicht **und** TP1 nicht erreicht | `96` M5-Bars (`SMC-SWEEP-REV-01` §15) |
| `DEAD_TRADE` | nach `invalidation.dead_trade_bars` liegt \|P/L\| < `invalidation.dead_trade_r` × R **und** MFE < `invalidation.dead_trade_mfe_r` × R | `48` Bars / `0.3` R / `0.5` R |

**Wirkung:** Market-Exit Rest-Position, `exit_reason = TIME_EXPIRY` bzw. `DEAD_TRADE`.

*Warum validieren:* Dead-Trade-Regeln reduzieren Opportunitätskosten, können aber Trades
abschneiden, die „gerade erst" laufen wollten. Kalibrieren gegen die Verteilung „Zeit bis TP1".

---

## 6. Trailing-Stop (Gewinn-Sicherung, keine Invalidierung)

- Aktiv ab `trail.activate_after` (`SMC-SWEEP-REV-01`: nach TP2).
- SL folgt dem letzten bestätigten **HL** (long) / **LH** (short) auf `trail.timeframe`
  `−/+ trail.buffer_atr × ATR(trail.timeframe)`.
- Nur monoton in Richtung Gewinn.
- `exit_reason = SL_TRAILED`, wenn er greift.

---

## 7. Auswertungsreihenfolge pro Bar (verbindlich)

Bei **jedem** `confirmed`-Bar-Close des kleinsten relevanten Timeframes (M1 für Excursion, M5 für
Entscheidungen), **in dieser Reihenfolge**:

```
IF Position offen:
  1. Wurde der harte SL im Bar-Verlauf berührt?      → Klasse C, EXIT, STOP
  2. Strukturelle Invalidierung (Klasse B) erfüllt?  → EXIT (Market), STOP
  3. News-erzwungene Aktion fällig? (news-rules.md)  → reduce/flatten gemäß Regel
  4. TP-Level im Bar-Verlauf berührt?                → Teil-Exit(s), SL→BE nach TP1
  5. Trailing-Stop nachziehen (falls aktiv)
  6. Zeit-/Dead-Trade-Exit (Klasse D) erfüllt?       → EXIT (Market)
ELSE IF Kandidat ARMED:
  7. Kandidaten-Invalidierung (Klasse A) erfüllt?    → Order stornieren, verwerfen
  8. Entry-Order im Bar-Verlauf gefüllt?             → State → TRIGGERED
```

**Konfliktregel:** Werden im selben Bar sowohl SL (1) als auch ein TP (4) „berührt" und die
Bar-Daten erlauben keine eindeutige Reihenfolge, gilt die **konservative Annahme**: zuerst der
**ungünstigere** Ausgang (SL). Das ist eine bewusste Backtest-Konvention gegen zu optimistische
Ergebnisse (`backtest-labeling.md`).

---

## 8. Was ins Trade-Record kommt

Zusätzlich zu den Feldern aus `backtest-labeling.md`:
- `invalidation_class`: `A | B | C | D | TP | TRAIL`
- `invalidation_reason`: der konkrete Enum-Wert
- `bars_held`, `mfe_r`, `mae_r`
- bei Klasse B: `hypothetical_sl_outcome_r` (was wäre passiert, wenn wir bis zum SL gewartet
  hätten) — für die Validierung, ob Klasse B netto hilft.

---

## 9. Zu bestätigen / zu validieren

- **`ENTRY_ZONE_FAILED` als Klasse-B-Auslöser**: aggressiv (früher Ausstieg). Alternative: nur
  `RECLAIM_THESIS_BROKEN` + `COUNTER_CHOCH_POST`. Startannahme = inklusive, mit
  `zone_fail_confirm = true`.
- **Dead-Trade-Parameter** (`48` / `0.3R` / `0.5R`): reine Startwerte.
- **Konservative SL-vor-TP-Konvention**: bestätigen (empfohlen für Realismus).
- **Bar-genaue vs. M1-genaue SL-Prüfung**: MVP nutzt M1 für die Intrabar-Reihenfolge, wo
  vorhanden; sonst konservative Annahme.
