# Backtest-Labeling & Bias-Vermeidung

**Zweck:** Exakte Definition dessen, was pro Trade aufgezeichnet wird, und der Regeln, die
Look-ahead-Bias, Data Leakage, Survivorship-Bias, Future-Information-Leakage und Overfitting
verhindern. Diese Datei ist die Spezifikation für die spätere Backtesting-Engine (kein Code jetzt).

Alle Zahlen `PROPOSED DEFAULT`. Konfig unter `backtest.*`.

---

## 1. Zeit- & Bar-Semantik (verbindlich)

- Alle Zeitstempel **UTC**.
- Eine Bar mit Label `t` deckt `[t, t+Δ)` ab und **schließt** zu `t+Δ`.
- Eine Entscheidung „am Close von Bar `t`" wird dem Zeitpunkt `t+Δ` zugeordnet.
- **`information_cutoff`** eines Setups = Close-Zeit der Bar, auf der das **letzte** Kettenglied
  bestätigt wurde. **Keine** Information mit Zeitstempel `> information_cutoff` darf die
  Entry-Entscheidung beeinflussen.
- **Entry-Fill** wird ausschließlich auf Bars mit `t ≥ information_cutoff` simuliert.
- **Multi-Timeframe-Assembly (`0.1.1` C11):** Basis-Serie ist **M5** (aus dem Repository).
  `M15 / H4 / D1` werden **aus M5 abgeleitet** (`data/resample.py`) — eine höhere Bar gilt erst
  als `confirmed`, wenn ihre letzte M5-Teilbar geschlossen ist. `M1` ist optional (Excursion /
  Intrabar-Reihenfolge); fehlt M1, gilt die konservative Intrabar-Annahme (§3, §5: SL vor TP).
  `MarketContext` enthält alle vom aktiven Setup benötigten Timeframes.

---

## 2. Trade-Record — Pflichtfelder

```
TradeRecord {
  # --- Identität & Version ---
  trade_id: uuid
  setup_id: "SMC-SWEEP-REV-01"
  strategy_version: semver           # z.B. "0.1.0"
  code_sha: str                      # Git-Commit
  config_hash: str                   # Hash aller wirksamen Parameter
  dataset_version: str               # Version + Hash des Datensatzes
  instrument: str
  direction: LONG | SHORT
  timeframe_set: { htf, sweep_tf, structure_tf, entry_tf }

  # --- Zeitachse ---
  signal_bar_timestamp: UTC          # Close der Bar, an der alle Ketten-Gates erfüllt waren
  information_cutoff: UTC            # == signal_bar_timestamp (keine spätere Info erlaubt)
  armed_timestamp: UTC              # Entry-Order platziert
  entry_order_timestamp: UTC
  entry_fill_timestamp: UTC | null   # null, wenn Kandidat vor Fill invalidiert
  exit_timestamp: UTC | null

  # --- Preise & Größe ---
  entry_price: float
  entry_size: float
  initial_sl: float
  initial_tp1: float
  initial_tp2: float
  initial_tp3_ref: str               # "trailing" / HTF-Pool-Referenz
  initial_rr_to_tp2: float
  blended_rr: float

  # --- Ergebnis ---
  exit_price: float | null
  exit_reason: TP1 | TP2 | TP3 | SL | SL_BREAKEVEN | SL_TRAILED
             | STRUCT_INVALIDATION | TIME_EXPIRY | DEAD_TRADE
             | NEWS_FLATTEN | MANUAL | KILL_SWITCH
  invalidation_class: A | B | C | D | TP | TRAIL | null
  invalidation_reason: str | null
  partials: [ {timestamp, price, size_pct, reason} ]

  gross_r: float                     # ohne Kosten
  realized_r: float                  # netto (nach Fees, Funding, Slippage)
  win_loss: WIN | LOSS | SCRATCH     # §4
  pnl_ccy: float

  # --- Excursion ---
  mfe_price: float                   # Maximum Favorable Excursion
  mfe_r: float
  mfe_timestamp: UTC
  mae_price: float                   # Maximum Adverse Excursion
  mae_r: float
  mae_timestamp: UTC
  excursion_timeframe: str           # backtest.excursion_tf (PROPOSED DEFAULT M1)

  # --- Zeit im Markt ---
  holding_time_bars: int             # in entry_tf-Bars
  holding_time_wallclock_s: int
  max_holding_time_bars: int         # das konfigurierte Limit (SMC-SWEEP-REV-01 §15)

  # --- Kosten (in R und ccy) ---
  costs: { fees_r, funding_r, slippage_entry_r, slippage_exit_r,
           fees_ccy, funding_ccy, slippage_ccy }

  # --- Kontext-Snapshot zum information_cutoff ---
  regime_at_signal: RegimeState
  confidence_at_signal: ConfidenceRecord
  score_at_signal: SetupScore
  sizing_at_signal: SizingRecord
  vetoes_evaluated: [str]            # sollte leer sein (sonst kein Trade)
  no_trade_checks_passed: [str]
  news_context: { nearest_high_event_id, minutes_to_event }
  portfolio_context: { open_positions, cluster_id, cluster_open_risk_pct }

  # --- nur bei Klasse-B-Exit: Kontrafaktik ---
  hypothetical_sl_outcome_r: float | null   # Ergebnis, wenn bis zum SL gehalten
}
```

**Auch aufgezeichnet:** `CANDIDATE`-Records für Kandidaten, die nie zum Trade wurden
(`decision = NO_TRADE` oder `CANDIDATE_INVALIDATED`), mit denselben Kontextfeldern und dem
`reason`. Diese sind für die „Warum wurde nicht getradet"-Analyse essenziell.

---

## 3. MFE / MAE — Messregeln

- Gemessen **ab `entry_fill_timestamp` bis `exit_timestamp`**, auf `backtest.excursion_tf`
  (**PROPOSED DEFAULT `M1`**; wenn M1 nicht verfügbar: `entry_tf`, mit Vermerk).
- `mfe_r = (bester erreichter Preis in Richtung Trade − entry_price) / (entry_price − initial_sl)`
  (Vorzeichen so, dass günstig = positiv).
- `mae_r` analog für die ungünstigste Exkursion.
- **Intrabar-Konvention:** innerhalb einer Bar wird angenommen, dass zuerst das **ungünstigere**
  Extrem erreicht wurde (konservativ), außer feinere Daten widerlegen das.

---

## 4. Win / Loss / Scratch

| Label | Bedingung (auf `realized_r`, **netto**) | PROPOSED DEFAULT |
|-------|----------------------------------------|------------------|
| `SCRATCH` | `|realized_r| ≤ backtest.scratch_r` | `0.1` |
| `WIN` | `realized_r > backtest.scratch_r` | |
| `LOSS` | `realized_r < −backtest.scratch_r` | |

Zusätzlich getrackt: `win_rate_incl_scratch`, `win_rate_excl_scratch`.

---

## 5. Fill-Modell (konservativ)

| Order-Typ | Fill-Regel |
|-----------|-----------|
| **Limit (Entry)** | Fill nur, wenn eine Bar mit `t ≥ information_cutoff` den Limitpreis **durchhandelt**: long ⇒ `low ≤ limit`; short ⇒ `high ≥ limit`. Fill-Preis = Limitpreis. Bei Gap über den Limitpreis hinweg: Fill zum **Open** (schlechter für uns, wenn Open ungünstiger; sonst Limitpreis). |
| **Market** | Fill zum `open` der nächsten Bar nach Signal **+ Slippage** (§6). |
| **Stop (SL/TP-Stop)** | Trigger, wenn Bar den Stop berührt; Fill zum Stop-Preis **+ Slippage**; bei Gap durch den Stop: Fill zum Open. |
| **Teilfüllung** | `backtest.partial_fills` (**PROPOSED DEFAULT `false`** im MVP; wenn `true`: Fill-Anteil ∝ min(1, `bar_volume · backtest.max_participation` / order_size)). |
| **SL & TP in derselben Bar berührt** | konservativ: **SL zuerst** (`invalidation.md` §7 Konfliktregel). |

---

## 6. Kosten (immer abgezogen)

`realized_r` ist **immer** netto. Komponenten:

| Kosten | Modell | PROPOSED DEFAULT |
|--------|--------|------------------|
| **Fees** | `taker_fee` auf Entry (Market/Stop) bzw. `maker_fee` auf Limit-Entry; `taker_fee` auf alle Exits | `taker 0.055 %`, `maker 0.02 %` (Bybit-nah, refdata-abhängig) |
| **Funding** (Perps) | Summe der Funding-Zahlungen über die Haltedauer, aus historischer Funding-Rate zum jeweiligen Settlement | reale Historie (`ARCHITECTURE_GAP_AUDIT.md` G-39) |
| **Slippage** | `slippage = backtest.slippage_atr × ATR(entry_tf)` je Seite; zusätzlich `backtest.slippage_spread_mult × spread` | `slippage_atr 0.05`, `slippage_spread_mult 0.5` |
| **Borrow/Leihe** (Aktien-Short, später) | tägliche Leihgebühr aus refdata | — |

**Backtest ohne Kosten** wird **zusätzlich** gerechnet und der Report zeigt beide — die Differenz
ist eine Pflicht-Kennzahl (`anti-overfitting.md`).

---

## 7. Bias-Vermeidung — harte Regeln & Tests

### 7.1 Look-ahead-Bias
- Alle Primitive/Regime/Confidence nur auf `confirmed`-Bars.
- Entscheidung nutzt nur Bars mit `close_time ≤ information_cutoff`.
- **Test „Zeitreise-Immunität":** Datensatz nach `information_cutoff` durch Rauschen/Zufall
  ersetzen ⇒ die Entry-Entscheidung (Richtung, Preis, SL, Größe, Score, Confidence) muss
  **bit-identisch** bleiben.

### 7.2 Data Leakage
- Feature-Normalisierung nur mit **expanding/rolling** Statistiken (nie Full-Sample).
- Kein Feature, das aus dem späteren Ergebnis abgeleitet ist.
- Regime-/Confidence-/Score-Werte werden **kausal** zum `information_cutoff` eingefroren und im
  Record gespeichert (nicht nachträglich neu berechnet).
- **Test:** Skalierer/Statistiken, die zu Zeitpunkt `t` verwendet werden, dürfen keine Daten
  `> t` gesehen haben (Assertion im Feature-Builder).

### 7.3 Survivorship-Bias
- Instrument-Universum ist **Point-in-Time**: nur handeln, was zu `t` gelistet **und** liquide war.
- Delistete/gehaltete Instrumente bleiben im Datensatz (mit Listing-/Delisting-Datum).
- Für den MVP (BTC/ETH im gewählten Fenster) trivial erfüllt — die Regel steht trotzdem, weil das
  System Multi-Asset wird (Altcoins, Aktien).
- **Test:** Universums-Query mit Datum `t` gibt nie ein Instrument zurück, dessen `listed_at > t`
  oder `delisted_at < t`.

### 7.4 Future-Information-Leakage (News/Makro/Corporate Actions)
- `NewsEvent.actual` ist vor `scheduled_time` **nicht** lesbar.
- Nur der Kalenderstand mit `available_at ≤ information_cutoff` wird genutzt (keine nachträglich
  ergänzten Events).
- Makro-Zeitreihen mit `available_at` (Erstveröffentlichung), **nie** revidierte Werte.
- Corporate Actions (später) nur „as-of" angewandt; Funding-Raten nur ab Settlement-Zeit.
- **Test:** für einen Backtest-Lauf über Zeitraum `X` liefert der News-/Makro-Provider dieselben
  Werte wie ein hypothetischer Live-Lauf zu jedem `t ∈ X`.

### 7.5 Overfitting
- **Pre-Registration:** Regelwerk-Docs + Parameterwerte werden **vor** jeder Optimierung als
  Git-Tag eingefroren (`strategy_version`).
- **Parameter-Budget:** ≤ `antioverfit.max_free_params` (**`8`**) dürfen im MVP frei variiert
  werden; der Rest bleibt auf `PROPOSED DEFAULT`.
- **Daten-Splits:** chronologisch Train / Validation / **unberührter** Test (Details
  `anti-overfitting.md`).
- **Multiple-Testing-Protokoll:** jeder Backtest-Lauf wird in der Experiment-Registry gezählt;
  der Bericht nennt die Anzahl getesteter Konfigurationen.
- **Mindest-Stichprobe:** keine Aussage je Bucket (Regime/Session/Setup) mit < `antioverfit.min_samples`
  (**`30`**) Trades.
- **Sensitivität:** Performance muss ein **Plateau** über Parameter-Nachbarschaften sein
  (`anti-overfitting.md`), kein Peak.

---

## 8. Report-Kennzahlen (Pflicht je Lauf)

Overall **und** je Bucket (`regime_at_signal`, Session, Instrument, `tier`, `exit_reason`):
Win Rate (inkl./exkl. Scratch), Profit Factor, Expectancy (R), Average R, Median R, Max Drawdown
(R und %), MFE/MAE-Verteilung, längste Verlustserie, Anzahl Trades, **Kostenanteil**
(Netto- vs. Brutto-Expectancy), Trade-Frequenz, durchschnittliche Haltedauer, Anteil
`STRUCT_INVALIDATION`/`TIME_EXPIRY`/`DEAD_TRADE`, `hypothetical_sl_outcome_r`-Vergleich für
Klasse-B-Exits.

---

## 9. Reproduzierbarkeit

Ein Backtest-Lauf ist definiert durch das **Run-Manifest**:
```
RunManifest {
  code_sha, config_hash, dataset_version, dataset_hash,
  date_range, instrument_universe, seed,
  strategy_version, cost_model_version, fill_model_version
}
→ deterministischer output_hash (Report + TradeRecords)
```
Gleicher Manifest ⇒ **bit-identischer** Output (Test).

---

## 10. Zu bestätigen / zu validieren

- **`excursion_tf = M1`**: braucht M1-Daten für BTC/ETH im Testfenster — verfügbar? sonst `M5`.
- **Fee-Defaults (taker 0.055 % / maker 0.02 %)**: an reale Bybit-Gebühren + Instrument anpassen.
- **`slippage_atr = 0.05` + `slippage_spread_mult = 0.5`**: bewusst konservativ; validieren gegen
  reale Paper-Fills, sobald vorhanden.
- **`partial_fills = false` im MVP**: bestätigen (Vereinfachung; realistischer wäre `true`).
- **SL-vor-TP-Konvention**: bestätigen (empfohlen).
- **`scratch_r = 0.1`**: Startwert.
