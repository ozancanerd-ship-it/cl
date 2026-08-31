# Multi-Symbol-Backtest — 6 Krypto-Assets, voller Strategiepfad (2026-08-29)

**Status:** abgeschlossen · **Ergebnis: 0 Trades über alle 6 Symbole. Regime-Gate blockt ~100 %.**
**Keine Parameteränderung** (Nutzer-Vorgabe + Regime-OOS-Kalibrierung: Baseline bleibt).

Harness: `scripts/run_multi_backtest.sh` → `scripts/run_backtest.py` (echte `strategy.evaluate`-
Pipeline) · Auswertung: `scripts/analyze_multi_backtest.py` · Rohdaten:
`data/repository_real/bt_multi/{SYMBOL}.json`

---

## 1. Setup

| | |
|---|---|
| Symbole | BTCUSDT, ETHUSDT, SOLUSDT, BNBUSDT, XRPUSDT, DOGEUSDT |
| Zeitraum | 2023-08-01 → 2025-06-30 (23 Monate, voller Zyklus: Erholung → 2024-Bull → 2025-Korrektur) |
| Daten | Binance-Vision Spot M5 + native M15/H4/D1, `data/repository_real/` |
| Bars/Symbol | 201 312 M5-Ticks durch die Pipeline · je Symbol `dataset_ok = True` |
| Modus | **Research (News-Gate AUS)** — Fail-safe V4 deaktiviert, News als `not_checked`, **keine** News-Daten erfunden. NICHT live-repräsentativ (live blockt V4 ohne PIT-Feed jeden Entry). |
| Kosten | `zero` (brutto) — bei 0 Trades irrelevant |
| Parameter | **alle PROPOSED DEFAULTS, unverändert** |
| Determinismus | `output_hash = e3b0c442…` (SHA-256 der leeren Trade-Liste) für alle 6 Läufe identisch |

---

## 2. Ergebnis

### 2.1 Entscheidungs-Verteilung (aggregiert, 1 207 872 Ticks)

| Decision | Anzahl | Anteil |
|---|--:|--:|
| `NO_TRADE` | 1 205 944 | 99.84 % |
| `WAIT` | 1 928 | 0.16 % |
| `BUY` / `SELL` | **0** | **0 %** |

### 2.2 NO_TRADE-Gründe (aggregiert)

| Grund | Anzahl | Kommentar |
|---|--:|---|
| `regime_unclear` | 800 663 | HTF-Regime-Gate: D1 **und** H4 nicht gleichzeitig sauber gerichtet |
| `regime_vol_extreme` | 399 184 | Vol über der EXTREME-Grenze (M5/M15/H4/D1 — irgendeine TF ≥ 97. Perzentil) |
| `data_gap_recent` | 119 232 | **Datenartefakt**, s. §4 — kein echter Feed-Ausfall |
| `regime_conflicting` | 3 786 | D1 ↔ H4 gegenläufig |
| `regime_vol_too_low` | 4 194 | Vol unter der LOW-Grenze |

### 2.3 Je Symbol

| Symbol | Signale (WATCH) | invalidiert | `regime_unclear` | `regime_vol_extreme` | Vetos |
|---|--:|--:|--:|--:|---|
| BTCUSDT | 1 782 | 1 752 | 74 % | 24 % | V5 1147 · V3 918 |
| ETHUSDT | 297 | 244 | 71 % | 29 % | V5 212 · V3 30 · V2 3 · V8 1 |
| SOLUSDT | **0** | 0 | 55 % | **45 %** | — |
| BNBUSDT | 40 | 38 | 79 % | 21 % | V3 32 · V5 37 |
| XRPUSDT | 103 | 100 | 68 % | 32 % | V5 100 |
| DOGEUSDT | 1 | 0 | 52 % | **48 %** | — |

**Signale (WATCH-Level) entstehen** — die Setup-FSM erkennt Kandidaten (v. a. BTC) — aber **keiner
kommt bis `ARMED` + Fill durch**, weil der HTF-Regime-Gate **davor** `NO_TRADE` zurückgibt.

---

## 3. Interpretation

### 3.1 „Mehr Instrumente" (Krypto) ist NICHT der Hebel

Die Regime-OOS-Kalibrierung (`REGIME-CALIBRATION-2026-08.md`) empfahl u. a. *„(a) mehr Instrumente
→ das informative Gate bekommt mehr Gelegenheiten zu feuern"*. **Für Krypto ist das jetzt getestet
und widerlegt:**

- Die hoch-volatilen Altcoins (SOL 45 %, DOGE 48 % `vol_extreme`) werden **häufiger** geblockt,
  nicht seltener. `regime_vol_extreme` skaliert mit der Eigen-Vol des Assets.
- `regime_unclear` bleibt bei 52–79 % — die Struktur-/Trend-Klarheit auf D1 **und** H4
  gleichzeitig ist bei allen Coins selten (deckt sich mit `STRUCTURE-CALIBRATION-2026-08.md`:
  „D1↔H4 aligned nur ~5 %").
- Netto: 4 zusätzliche Coins → **0 zusätzliche Trades**.

### 3.2 Das ist kein Bug

- `dataset_ok = True`, deterministisch (`output_hash` identisch), PIT (Parity-Test grün in Audit
  14), kein Leakage/Snooping/Survivorship (Audit 12/13).
- Der Regime-Gate ist **informativ** (Audit 14: `gate_ok`-Probes Expectancy +0.38 R vs. Bias-only
  +0.008 R) — er blockt viel, aber die Population, die er durchlässt, ist deutlich besser.
- **Kein Trade heißt hier: die SMC-Sweep-Reversal-Familie findet in Krypto 2023–2025 fast nie
  einen sauberen HTF-Regime-Kontext.** Das ist ein Eigenschafts-, kein Fehlerbefund.

### 3.3 Konsequenz für Entry-/Exit-Qualität (Nutzer-Punkte 11/12)

- **Punkt 11 (Entry-/Exit-Qualität):** keine Entries → nichts zu analysieren. Der Engpass liegt
  **vor** dem Entry (Regime-Gate), nicht in der Ausführung. MFE/MAE/Give-Back/TP-Effizienz sind
  erst messbar, wenn Trades entstehen.
- **Punkt 12 (Score/Confidence OOS):** Nutzer-Vorgabe *„Wenn keine ausreichende Datenbasis →
  NICHT optimieren."* 0 Trades ⇒ **nicht angefasst.** `score_outcome_correlation` = `null`.

---

## 4. Nebenbefund — Datenlücke 2023-03-24

Beim Scan der Historie gefunden (nicht vorher bekannt, `validate_dataset` prüft nur die
Abdeckungs-Endpunkte):

- **2023-03-24: 272/288 M5-Bars** je Symbol (≈ 80 min fehlen) — identisch für alle 6 Coins ⇒
  **echter Binance-Datenausfall an diesem Tag**, kein Ingest-Fehler.
- Der Tag ist damit „unvollständig" ⇒ native D1-Ingest **und** Resampler lassen ihn weg ⇒ die
  D1-Reihe hat ein 1-Tages-Loch (2023-03-23 → 2023-03-25).
- `check_ohlcv_series` meldet das korrekt als `GAP` ⇒ `data_gap_recent` NO_TRADE, für **jeden**
  M5-cutoff, dessen D1-200-Bar-Warmup-Fenster den 24.03.2023 enthält (≈ 2023-08-01 → 2023-10-10,
  ≈ 70 Tage ≈ 19 872 Ticks/Symbol — deckt sich exakt).
- **Wirkung auf das Ergebnis: keine** — diese ~70 Tage wären ohnehin regime-geblockt.
- Sonst ist die Historie **99.99 % vollständig** (912/914 Tage mit vollen 288 Bars;
  2022-12-30 = beabsichtigter Warmup-Rand).

**Optionen (Backlog, nicht dringend):** (a) 2023-03-24 M5 aus Kraken/Bybit backfillen; (b)
Backtest-Fenster nach 2023-06 beginnen; (c) `validate_dataset` um einen Kontinuitäts-Check der
höheren TFs erweitern (empfohlen — würde solche Löcher künftig **vor** dem Lauf melden).

---

## 5. Nächste echte Hebel (keiner davon ist „Gate lockern")

1. **Andere Asset-Klasse mit anderem Vol-Regime** — XAUUSD / FX-Majors. Dukascopy-Bulk als
   Quelle (`docs/MULTI-ASSET-READINESS.md` §3/§4). Gold/FX haben eine andere Vol-Struktur und
   klarere Session-getriebene HTF-Trends — realistische Chance, dass der Gate häufiger feuert,
   **ohne** die Trennschärfe zu opfern.
2. **Struktur-Klassifikator (`derive_structure_state`) auf H4 isoliert kalibrieren** — die
   D1-Kalibrierung (`STRUCTURE-CALIBRATION-2026-08.md`) ließ H4 offen; `regime_unclear` ist der
   größte Einzelblocker.
3. **Niedrige Frequenz als korrekt akzeptieren** für die SMC-Sweep-Reversal-Familie und einen
   **zweiten Setup-Typ** (z. B. Range-Bruch, Continuation) parametrisieren — mehr Gelegenheiten
   ohne den Gate zu berühren.
4. **`data_gap_recent` bereinigen** (§4 Option c) — damit spätere Läufe kein Rauschen tragen.
