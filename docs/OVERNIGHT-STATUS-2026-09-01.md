# WHAT CHANGED OVERNIGHT — 2026-08-31 → 09-01

Autonom, ~7 Commits (`61bfaf0` … `HEAD`). Keine Echtgeldorders, keine Trading-Rechte aktiviert.
Alle Details: `docs/GOLD-BREAKOUT-DIAGNOSIS-2026-08.md`.

---

## DONE

| # | Sache | Ergebnis |
|---|-------|----------|
| 1 | **Gold-Block-Diagnose** (`scripts/diag_gold_breakout.py`) | Ursache gefunden — siehe GOLD unten |
| 2 | **8 Setup-Varianten OOS/WF/MC getestet** (`setup_research.py` S6–S16) | genau **eine** überlebt (S9) |
| 3 | **S9 in die Strategie integriert** | `require_htf_bos_confluence` (default an), byte-getestet |
| 4 | **Portfolio Hub** live (`scripts/portfolio_hub.py`) | read-only Kraken+Bybit+Binance → Equity ~370 USD |
| 5 | **Market Scanner one-shot** (`scripts/market_scan.py`) | Multi-Asset → Opportunity-Ranking, ⚠️ SHADOW/LIVE |
| 6 | **Statisches Dashboard** (`web/dashboard.html` + `build_dashboard.py`) | 10 Tabs, framework-frei, 60 s Refresh |
| 7 | **Performance-Tab verdrahtet** (`scripts/performance_report.py`) | gesamt + nach Asset/Setup/Richtung/Score/Freigabe |
| 8 | **Re-Entry-Watches** ins Dashboard | `xau_shadow` emittiert Watches bei These-intaktem Exit |
| 9 | **Aktien-Engine** (`investment/stock_analysis.py` + `scripts/stock_analysis.py`) | Einzelwerte only, ETF-Reject, 6 Faktoren |
| 10 | **Cross-Asset keylos** (`ingest_yahoo.py` + `build_cross_asset_from_repo`) | DXY-YF/US10Y-YF/VIX-YF → Confluence |

## IMPROVED

- **`SETUP-BREAKOUT-RETEST-01` → S9-Logik.** Zusätzlich zum D1-Struktur-Trend wird ein
  **jüngster D1-BOS in dieselbe Richtung** verlangt. Panel-OOS (12 Instrumente, Split 2025-01,
  scaled mgmt):

  | | S4 (vorher) | **S9 (jetzt)** |
  |---|---|---|
  | Trades gesamt | 131 | 112 |
  | IS-Expectancy | +0.121 R | **+0.211 R** |
  | OOS-Expectancy | +0.374 R | **+0.414 R** |
  | OOS Profit Factor | 2.03 | **2.21** |
  | OOS Win-Rate | 64.6 % | **66.7 %** |
  | Walk-Forward + | 8/10 | 8/9 |
  | Monte-Carlo `prob_positive` | 0.61 | **0.79** |
  | Symbol-Stabilität (12 Instr.) | 0.75 | **0.83** |

  Weniger, bessere Trades. **Jede weitere Filter-Idee (Session, Thrust, Coil, Regime-ER, DXY)
  hat S9 verschlechtert** → integriert wurde nur S9. Das ist die Grenze zwischen Verbesserung
  und Overfitting.

## TESTS

- **1063 grün** (vorher 1055). `+2` Governance/Konfluenz-Tests, `+1` cross_asset, `+5` stock_analysis,
  `+4` breakout (bestehende an S9-Fixtures angepasst — kein Test „geschwächt").
- `mypy --strict` sauber (204 Dateien). `ruff check` + `ruff format` sauber.
- Live-Daemon-Kurzlauf (40 s, XAUUSDT): NO_TRADE, `orders_sent=0`, Audit-Hash-Kette intakt, 1 Snapshot.
- **Bekannter Alt-Flake** (nicht von heute): `test_look_ahead_immunity` flackerte einmal im
  Voll-Lauf, besteht isoliert 5/5. Vorbestehende Nichtdeterminie in `engine/backtest` Manifest-Hash;
  0 `src/`-Änderungen an dem Pfad heute. Separat zu untersuchen.

## GOLD

**Warum die 6 Ur-Trades verloren (−4.5 R / 17 % WR):**

1. **Falsche Richtung.** 5/6 Gegen-Trend-Shorts in einem Jahr, in dem Gold +15 % machte.
   `fwd20`/`fwd40` in 4/6 Fällen klar negativ — die Trades laufen *nach* dem Einstieg gegen die
   Position. Der 2-Swing-D1-Struktur-Trend meldet in jeder Pullback-Sequenz `trend_down`.
2. **Zu schwacher Ausbruch** (Displacement-Median ~0.6·ATR) und **zu weite „Konsolidierung"**
   (2–4·ATR Choppy-Range als „Coil").
3. **Timing:** 4/6 Einstiege 20–00 UTC (illiquide, Fakeout-Fenster).
4. **Management ist NICHT das Problem** — weiterer/struktureller Stop macht das Ergebnis
   *schlechter* (−4.5 R). Der Preis geht real weit gegen die Position.

**Was S9 daran ändert:** entfernt die Gegen-Trend-Shorts, für die kein bestätigender D1-BOS
vorliegt. Panel-weit ein klarer Gewinn (Tabelle oben).

**Was S9 NICHT löst — ungeschönt:**
- **Echtes Spot-XAUUSD 2023** (Dukascopy, einziges reales Spot-Jahr, das ich habe — und Golds
  choppigstes) bleibt **negativ**: S9 = 4 Signale, −4.0 R, 0 % WR (S4 war 6 / −4.5 R / 17 %).
  4–6 Trades sind **kein statistisches Urteil**. Breakout-*Continuation* hat in einem
  Range-Regime strukturell keine Edge.
- Die Panel-OOS-Edge ruht **stark auf Yahoo-indikativen Daten** (GC=F = Gold-*Futures*, nicht
  Spot) + FX-Proxy. Auf XAUUSD-YF (2024–26, Trend): S9 = +9.0 R / 73 % WR — aber das ist Futures.
- **Ich habe kein reales Spot-Gold aus einer Trendphase.** Der Dukascopy-Voll-Ingest wird von
  der Umgebung abgebrochen (Prozess-Zeitlimit). Reales Spot-XAUUSD = nur 2023 + 2024-01 + 2026-05…08.
- **M5-Parquet für XAUUSD ist korrupt** — H4/M15/D1 lesbar, M5 nicht. Kein Blocker für die
  H4-Research/Shadow.

### Welche Gold-Setups funktionieren / funktionieren nicht

| Setup | Auf Panel + Futures-Proxy | Auf echtem Spot-XAUUSD 2023 |
|-------|---------------------------|-----------------------------|
| S9 Breakout+Retest+HTF-Konfluenz | ✅ OOS +0.41 R, PF 2.2, MC 0.79 | ❌ 4 Trades, −4.0 R |
| S0 Sweep-Reversal (SMC-Basis) | ❌ OOS ~0, MC pp 0.0 | ❌ −36 R / n123 |
| S2 Trend-Pullback | ❌ OOS −0.12 R | ❌ −9.4 R / n12 |
| S3/S5 HTF-Break + LTF-Confirm | 🟡 OOS +0.02…+0.09 R (schwach) | ❌ negativ, n klein |
| S6/S7/S10 (enge Filter) | ❌ 0–3 Trades im ganzen Panel | — |

**Longs funktionieren, Shorts nicht** (Shadow-Baseline, 19 Trades: buy exp **+0.62 R**,
sell exp **−0.50 R**). Das ist über alle Gold-Varianten konsistent.

### Zahlen

- **Getestete Trades gesamt** (Research-Panel, alle Setups × 4 RR × 12 Instrumente): ~7 000.
- **S9 im Panel:** 112 Trades, davon 54 OOS.
- **S9 Real-Spot-Gold:** 4 Trades (2023). **S9 Futures-Proxy (XAUUSD-YF):** 15–16 Trades.
- **Expectancy S9:** Panel OOS +0.414 R · Real-Gold −1.0 R · Futures-Proxy +0.60…+0.74 R.
- **Max Drawdown:** Shadow-Baseline 4.0 R (19 Trades) · Panel-MC p95 ~10 R.

### Ist die Strategie für Live-Paper geeignet?

**S9: JA für Forward-Paper / Shadow — NEIN für Echtgeld.** Status bleibt **`IN_VALIDATION` /
SHADOW**. Begründung ungeschönt: die *relative* Verbesserung S9 > S4 ist robust; die *absolute*
Live-Tauglichkeit ist es nicht, solange (a) die Edge primär auf indikativen Futures-/FX-Daten
ruht, (b) echtes Spot-Gold negativ ist (wenn auch n zu klein), (c) < 100 Forward-Trades
vorliegen. `scripts/validate_s4.py` entscheidet automatisch VALIDATED-Kandidat / RETIRE, sobald
die vollständige Dukascopy-Historie da ist.

## LIVE SHADOW

- **XAUUSDT jetzt** (`xau_now.py`, 2026-08-31 21:30): **NO TRADE** — `regime_conflicting`,
  beide Setup-Typen SCANNING. Cross-Asset: DXY range, 10Y 4.72 %, VIX 14, risk_off = False.
- 24/7-Betrieb: `run_live_daemon.py` läuft (40 s verifiziert), muss aber dauerhaft auf einem
  Rechner ohne 2-min-Prozesslimit laufen. One-shot-Ersatz: `xau_now.py` / `market_scan.py`.
- Shadow-Journale: `data/repository_real/live/xau_shadow_XAUUSD_2023.jsonl` (4 Trades, −4.0 R),
  `…_XAUUSD-YF.jsonl` (15 Trades, +9.0 R). Re-Entry-Watches: 2 (XAUUSD-YF LONG + SHORT).

## TOP OPPORTUNITIES

- **Krypto/Gold (SMC + Breakout):** alle **NO TRADE** (Score 26.8/100) — Regime-Gate. Kein
  erzwungenes Signal.
- **Einzelaktien** (technisch, 2026-08-Daten, Benchmark S&P 500):
  1. MSFT 79.8 STRONG_BUY · 2. AAPL 53.3 HOLD · 3. NVDA 50.1 HOLD · 4. META 40.4 AVOID ·
  5. GOOGL 33.6 AVOID · 6. AMD 31.8 AVOID. (Keine Fundamentaldaten → rein technisch.)

## PORTFOLIO

- **Equity gesamt ~370 USD** (Kraken Crypto-Dust + Bybit Cash). Cash 74 %. Health **YELLOW** (67.8/100).
- Flags: viel unallokiertes Cash · Aktien-Anteil 0 % unter Zielband 35–65 %.
- Keine offene Perp-Position. Read-only bestätigt, `orders_sent=0`.

## BLOCKED (extern, unverändert)

- **Vollständige Dukascopy-Spot-Gold-Historie** — Umgebung killt den Voll-Ingest. Braucht den
  Nutzer-Rechner (`scripts/ingest_dukascopy_full.sh`, Monats-Loop).
- `FRED_API_KEY` (Makro-PIT), Polygon/Finnhub (Aktien-Fundamentals), News-/Kalender-Provider.
- `TELEGRAM_BOT_TOKEN` / `CHAT_ID` (Alerts).
- cTrader/OANDA-Demo-Token (FX-Live).
- XAUUSD-M5-Parquet korrupt (nur M5-Feinsimulation betroffen).

## NEXT (autonom, sobald sinnvoll)

1. **Breakout-Setup per Trending-vs-Ranging-Regime-Gate** absichern — die Diagnose zeigt, dass
   es in Ranges systematisch verliert. Erste Versuche (Efficiency-Ratio) waren zu streng;
   weicheres Maß oder mehr Daten nötig.
2. **Live-Chart** (Candles + Swings/FVG/OB/BOS/S/R) im Dashboard — `chart/annotations.py` erweitern.
3. **News-Kalender-CSV** mit historischen FOMC/CPI/NFP-Terminen 2023–26 seeden (reproduzierbare
   Fakten) → News-Gate im Backtest aktiv.
4. Forward-Paper-Trades sammeln (Ziel ≥ 100), dann `edge_health_check.py` + `validate_s4.py`.

---

### Welche Trading-Strategie ist aktuell die robusteste?

**`SETUP-BREAKOUT-RETEST-01` in der S9-Ausprägung** (Breakout+Retest, D1-Trend UND jüngster
D1-BOS in Richtung). Einziger Setup-Typ, der Backtest → OOS → Walk-Forward → Monte-Carlo →
Symbol-Stabilität übersteht (MC `prob_positive` 0.79). Die SMC-Basis `SMC-SWEEP-REV-01` hat
weiter **keine** nachgewiesene OOS-Edge und bleibt `UNVALIDATED`.

**Aber:** robust *relativ* im diversifizierten Panel ≠ live-tauglich auf echtem Gold-Spot.
Beide Setups bleiben **SHADOW** — kein Live-Signal, nur Forward-Tracking. Kein Echtgeld.
