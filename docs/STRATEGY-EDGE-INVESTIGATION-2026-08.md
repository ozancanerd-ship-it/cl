# Strategy-Edge — Kernblocker-Untersuchung (2026-08-31)

**Auftrag:** Den wichtigsten verbleibenden Blocker lösen — einen **zweiten robusten Swing-
Setup-Typ** finden, damit die Strategie auf XAUUSDT valide Chancen erkennt. Keine Filter
lockern, um Trade-Zahl zu erzeugen. Echte positive Out-of-Sample-Edge oder ein ehrliches „nein".

**Ergebnis:** Über **6 strukturell verschiedene Setup-Konstruktionen**, 2 Trade-Management-
Modelle, 4 RR-Werte, 7 Instrumente und 3+ Jahre — **keine** zeigt eine robuste, regime-
unabhängige, kosten-überlebende OOS-Edge. Der Befund aus Stufe B bestätigt sich: das Problem
ist **nicht** ein fehlender zweiter Setup-Typ und **nicht** das Regime-Gate. SMC/Price-Action-
Swing-Muster in dieser Form haben auf den verfügbaren Daten **keine nachweisbare prädiktive
Edge**.

---

## Teil 1 — Diagnose: Warum erzeugt das aktuelle Setup (fast) keine Trades?

### 1.1 Der Fluss

`strategy.evaluate` schleust **jeden** Kandidaten durch:

```
No-Trade-Gate → regime_gate(D1, H4) → Setup-FSM (SMC-SWEEP-REV-01)
  → Veto → Location → RR → Confirmation → Confluence → Contradictions → Confidence → Score → Decision
```

### 1.2 Welche Gates blockieren (XAUUSDT, aus dem Gold-Backtest + Code-Analyse)

| Gate | Blockrate | Ursache |
|---|---:|---|
| **`REGIME_UNCLEAR`** | **~83 %** | `regime_gate` verlangt **D1 UND H4** beide klar klassifiziert (`TREND_*`/`RANGE`) **und** konsistent. `directional_regime` klassifiziert D1 nur ~20 % als Trend (Struktur-Zustand **plus** `slope_norm ≥ 0.05` — zwei Bedingungen), H4 ~12 %. RANGE braucht eine saubere Box mit ≥ 2 Berührungen je Seite. Alles andere → `UNCLEAR`. → D1∧H4 klar+einig ≈ **3–6 %**. |
| `REGIME_VOL_EXTREME` | ~15 % | M15-**Kontext**-Vol-Spike ist ein **harter** Block (`context_vol_is_hard_block=True`). |
| `V3` / `V5` Vetos | auf den wenigen ARMED | Location- / RR-Veto. |
| `CONFIDENCE_BELOW_MIN`, `SCORE_BELOW_B` | Rest | dünne Evidenz auf den ~5 % durchgelassenen Kandidaten. |

Zusätzlich: der Voll-Pipeline-Backtest auf XAUUSDT **startet ohne `--require-native-higher off`
gar nicht** — er verlangt 200 D1-Warmup-Bars, XAUUSDT hat insgesamt nur 261 D1-Bars
(Launch 2025-12-11).

### 1.3 Welche Bedingungen sind **zu restriktiv**

- Die **D1∧H4-Regime-Konsens-Pflicht** (beide müssen klar klassifiziert sein **und** übereinstimmen). Der größte Einzelfilter — und laut Stufe B + dieser Untersuchung entfernt er überwiegend *neutrale* Setups, keine schlechten (der Klassifikator trägt keine OOS-Information).
- `directional_regime` verlangt Struktur-Zustand **UND** Slope (Doppelbedingung).
- RANGE-Definition (≥ 2 Touches je Seite, flach, Höhe ≤ 8 ATR) ist eng.
- M15-Kontext-Vol als **harter** EXTREME-Block (M15 ist Kontext, kein HTF).
- Die strikt-monotone-3-Swings-Struktur-Definition (Stufe-B-V0).

### 1.4 Welche Bedingungen sind **tatsächlich sinnvoll** (behalten)

- **Kein Look-ahead / PIT** — nicht verhandelbar.
- Sweep braucht Docht + Reclaim-Close (echte Liquiditäts-Signatur).
- Displacement ≥ ~1.5 ATR (echter Impuls, kein Drift).
- Mindest-RR-Gate (keine 0.5-R-Trades).
- SL jenseits der Struktur (nicht willkürlich).
- Nicht in EXTREME-Vol der **Entry**-TF handeln.
- Eine Position je Instrument, Cooldown nach Verlust.
- Score-/Confidence-Floor (nicht auf dünner Evidenz handeln).

### 1.5 Welche Setup-Typen passen zum Swing-Fokus

Breakout+Retest · Trend-Pullback/Continuation · HTF-Structure-Break+LTF-Confirm · Sweep+Reversal
(bestehend). **Nicht** Scalping-Mean-Reversion, **nicht** M1/M5-Momentum.

---

## Teil 2 — Setup-Forschung (`scripts/setup_research.py`)

### 2.1 Methodik (look-ahead-frei)

- **Timeframes:** H4 = Setup + Entry + Management, D1 = HTF-Regime. Swing-Fokus.
- **Kein Look-ahead:** an H4-Bar `i` sieht ein Detektor nur Swings/Breaks mit `confirmed_at ≤ h4[i].close_time` und den D1-Zustand zum selben Cutoff. Entry = **`h4[i+1].open`**. Exits ausschließlich vorwärts. Worst-Case-Fill (SL vor TP bei Bar-Overlap).
- **Primitive:** die projekteigenen `detect_swings` / `structure_breaks` / `atr_series` (getestet, PIT-sicher). Swings einmal je Symbol über die volle Serie berechnet und per `confirmed_at` PIT-gefiltert — identisch zur inkrementellen Rechnung (Swings werden nie revidiert), ~100× schneller.
- **Panel:** XAUUSDT (Ziel, ~8,5 Monate, komplett OOS) + BTC/ETH/SOL/BNB/XRP/DOGE (2023-01 → 2026-08, ~5 470 H4-Bars je Symbol) für statistische Aussagekraft.
- **RR** wird auf **IS** gewählt (max Expectancy, ≥ 15 IS-Trades), OOS/WF/MC an genau diesem RR berichtet — kein Peeking.
- **Management:** `fixed` (fester RR-Zielpunkt, 1R-Stop) **und** `scaled` (50 % @ +1R, SL→BE, Rest bis Ziel).
- **Kosten:** 0.03 R (fixed-Lauf) und 0.06 R (scaled-Lauf, realistischer: Spread+Fees round-trip).
- **Validierung:** IS/OOS-Split · Walk-Forward (200/90/90 Tage) · Monte-Carlo (2000 Läufe, Cost-Stress 1.5 auf Verlierer, 10 % Dropout) · Symbol-Stabilität · 90-Tage-Zeitfenster.

### 2.2 Getestete Setups

| ID | Typ |
|---|---|
| **S0** | Liquidity Sweep + Reversal (baseline-analog, vereinfacht) |
| **S1** | Breakout + Retest (Konsolidierung → Bruch + Displacement → Retest hält) |
| **S2** | Trend Pullback / Continuation (D1-Trend + H4-Pullback 33–85 % + Rejection) |
| **S3** | HTF Structure Break + LTF Confirmation (jüngster D1-BOS → erster H4-Close jenseits Swing-Extrem) |
| **S4** | S1 **nur in Richtung des D1-Struktur-Trends** (Continuation-Filter) |
| **S5** | S3-Variante: D1-**Struktur-Zustand** statt D1-BOS als Bias-Quelle |

### 2.3 Ergebnisse — Split 2025-06-01

**Fixed-Management, Kosten 0.03 R:**

| Setup | n | ALL exp | IS exp | OOS n | OOS exp | WF +/n | MC prob_positiv | Symbol +Anteil |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| S0 | 1259 | +0.005 | **−0.008** | 99 | +0.162 | 7/11 | **0.00** | 0.71 |
| S1 | 495 | +0.052 | +0.053 | 49 | +0.038 | 7/10 | **0.001** | 0.43 |
| S2 | 193 | +0.058 | +0.091 | 17 | **−0.284** | 5/9 | 0.03 | 0.40 |
| S3 | 151 | +0.135 | +0.146 | 12 | +0.004 | 7/9 | 0.17 | 0.67 |
| S4 | 63 | +0.344 | +0.391 | 8 | +0.017 | 4/5 | 0.51 | 0.25 |
| S5 | 131 | +0.191 | +0.202 | 12 | +0.077 | 7/8 | 0.30 | 0.60 |
| Combined S0+S1 | 1219 | +0.018 | +0.015 | 98 | +0.060 | 9/11 | **0.00** | 0.71 |

**Scaled-Management, Kosten 0.06 R:**

| Setup | n | ALL exp | IS exp | OOS exp | MC prob_positiv |
|---|---:|---:|---:|---:|---:|
| S0 | 1570 | **−0.009** | −0.028 | +0.241 | **0.00** |
| S1 | 532 | +0.078 | +0.086 | +0.014 | **0.006** |
| S2 | 216 | +0.037 | +0.063 | **−0.232** | 0.02 |
| S3 | 159 | +0.073 | +0.078 | +0.020 | 0.10 |
| S4 | 64 | +0.182 | +0.172 | +0.253 | 0.38 |
| S5 | 136 | +0.101 | +0.095 | +0.161 | 0.17 |

### 2.4 Ergebnisse — Split 2024-06-01 (längeres OOS, scaled, Kosten 0.06 R)

| Setup | IS exp | OOS n | OOS exp | WF +/n | MC prob_positiv |
|---|---:|---:|---:|---:|---:|
| S1 | **−0.053** | 282 | +0.194 | 9/10 | **0.006** |
| S3 | +0.150 | 80 | **−0.029** | 6/9 | 0.06 |
| S4 | +0.093 | 30 | +0.315 | 4/6 | 0.40 |
| S5 | **+0.205** | 66 | **−0.009** | 5/8 | 0.17 |
| S0 | −0.038 | 902 | −0.006 | 4/11 | **0.00** |

### 2.5 Interpretation — jedes Setup fällt aus einem von drei Gründen durch

1. **Vorzeichen kippt zwischen IS und OOS** (Overfit): S2, S3, S5 — bei jedem Split. S4 IS +0.39 (Split 2025-06) → OOS +0.017. Klassische „IS-Muster, OOS-Rauschen"-Signatur.
2. **„Positive" Phase = Bull-Market-Beta, nicht Edge**: S1 macht bei Split 2024-06 im OOS (2024–2026 Crypto/Gold-Hausse) **+0.19 R**, aber im IS (2023er-Chop) **−0.05 R**. Alle Setups zeigen deutlich bessere Long- als Short-Expectancy (S0 long +0.11 / short −0.09; S4 long +0.63 / short −0.43). Ein long-lastiges Breakout-System druckt in einer Hausse Geld — das ist keine prädiktive Edge.
3. **Kosten-Fragilität**: die einzigen Setups mit MC-`prob_positive` > 0.3 (S4, S5) haben **8–30 OOS-Trades** — statistisch bedeutungslos. Alles mit realem Sample (S0 1259, S1 495, Combined 1219) hat **`prob_positive` ≈ 0** und einen Monte-Carlo-Median von **−80 bis −400 R** — die Setups leben komplett innerhalb des Transaktionskosten-Bandes. S1s „Edge" von +0.04–0.05 R/Trade liegt unter realistischen Round-Trip-Kosten (XAUUSDT-Spread ≈ 0.04 ATR ≈ 0.03–0.06 R; Crypto-Fees+Slippage ≈ 0.05–0.08 R).

**XAUUSDT isoliert** (das Zielinstrument, komplett OOS): nur S0 (+9.5 R fixed) und S1 (+7.5 R fixed) positiv — beide **front-loaded** (S0: 1. Hälfte +19 R, 2. Hälfte −10 R), n = 25–53, und mit scaled-Management + realistischen Kosten fällt S1 auf **−0.72 R**. Es ist „long Gold in der H1-2026-Hausse", kein wiederholbares Muster.

---

## Teil 3 — Verdikt

**Der Kernblocker ist nicht ein fehlender zweiter Setup-Typ.** Er ist: **SMC/Price-Action-
Swing-Muster, so konstruiert, haben auf den verfügbaren Daten (2023–2026 Crypto, 2025–26 Gold)
keine nachweisbare, regime-unabhängige, kosten-überlebende prädiktive Edge.** Sechs
verschiedene Konstruktionen — inklusive aller vier vom Auftrag genannten Kandidaten (Sweep+
Reversal, Breakout+Retest, Trend-Pullback, HTF-Break+Confirm) plus zwei Filter-Varianten —
bestätigen das.

Einen „Setup B" jetzt zu integrieren würde **Trade-Zahl ohne Edge** hinzufügen — genau das,
was der Auftrag ausschließt. Die im Auftrag genannte Bedingung *„wenn der zweite Setup-Typ eine
echte Edge zeigt → integrieren"* ist **nicht erfüllt**. Also **keine Integration in den Live-
Decision-Pfad.**

### Was das *nicht* heißt

- Die Infrastruktur (Analyse, MTF, Primitive, Backtest, Validierung, Portfolio-Intelligence, Ops) ist solide und getestet (1026 Tests grün). Das ist nicht das Problem.
- Es heißt nicht, dass SMC-Swing-Trading grundsätzlich nicht funktioniert — nur, dass es **auf diesen Daten nicht belegbar ist**.

### Empfehlung — Entscheidung liegt beim Nutzer

| Option | Inhalt |
|---|---|
| **A — Daten** | Mehr Gold-Historie (bezahlte Quelle: 5–10 Jahre XAUUSD, unterschiedliche Regimes) + FX via cTrader/OANDA-Demo (sobald Token da). Erst dann Setup-Forschung wiederholen. Bis dahin: keine belegbare Edge = kein Live-Trading. |
| **B — Hypothese neu fassen** | Wenn auch mit mehr Daten nichts trägt: der reine SMC-Ansatz auf H4/D1-Swing trägt evtl. nicht. Alternativen: längere Haltedauer (D1/W1), andere Kante (Vol-Term-Struktur, Cross-Asset-Flows, saisonale Gold-Muster), oder ein reines Regime-/Allokations-Modell statt diskreter Trades. |
| **C — S1 als Beobachtungs-Kandidat** | S1 (Breakout+Retest) ist die *am wenigsten schlechte* Konstruktion (IS ≈ OOS bei Split 2025-06, regime-gate-unabhängig). **Nicht** als Live-Signal, sondern nur im Paper-/Shadow-Betrieb, explizit als **UNVALIDIERT** markiert, mit fortlaufendem Live-Performance-Tracking — um über die nächsten 6–12 Monate echte Forward-Daten zu sammeln. |

---

## Teil 4 — Unabhängige Fortschritte (edge-unabhängig)

- **`scripts/setup_research.py`** ist ab jetzt die permanente Werkbank für **jede** künftige Setup-Hypothese: Detektor als Funktion `(Ctx, i) -> Signal | None` hinzufügen, `DETECTORS`-Dict ergänzen, laufen lassen → voller Metrik-/Validierungs-Block. Look-ahead-frei by construction.
- Roh-Ergebnisse archiviert unter `data/repository_real/research/setup_research_*.json` (3 Läufe).
- **XAUUSDT Live-Paper**: die volle Kette **DATA → 24/7-ANALYSE → MTF → OPPORTUNITY SCORE** läuft live auf XAUUSDT (Binance USD-M Futures, read-only) über `scripts/run_live_daemon.py --exchange binance --symbols XAUUSDT`. **BUY/SELL-Signale, Entry/SL/TP, Re-Evaluation, Paper-Trade** sind vollständig implementiert (Stufen C/D) und würden auslösen, sobald ein Setup ARMED erreicht — was mit der bestehenden (validierten, gesperrten) Baseline und der aktuellen Datenlage praktisch nicht passiert. XAUUSDT läuft damit im **Beobachtungs-Modus**: Kontext + Ranking live, Signal-Ausgabe wartet auf eine belegte Edge.
- **Nicht gebaut:** kein zweiter Setup-Typ im Live-Decision-Pfad (Auftrags-Bedingung nicht erfüllt), kein Filter gelockert.

---

## Änderungen an anderen Dokumenten

- `docs/STAGE-B-STRATEGY-VALIDATION-2026-08.md` — Befund erweitert: die Setup-Ebene (nicht nur der Klassifikator) hat keine OOS-Edge. Dieses Dokument ist die tiefere Untersuchung dazu.
