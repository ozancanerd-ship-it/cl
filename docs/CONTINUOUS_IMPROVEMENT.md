# Continuous Improvement — permanenter Review-Prozess

**Zweck:** Der Plan (`FINAL_IMPLEMENTATION_PLAN.md`, `TODO.md`) ist verbindlich, aber **nicht
unveränderlich**. Oberstes Ziel: die bestmögliche, objektiv-testbare **Entry-/Exit-Analyse**.
Bessere Methoden, Datenquellen oder Analyseebenen werden aufgenommen — aber nur nach dem hier
definierten Gate. Kein Indikator-Stapeln.

---

## 1. Aufnahme-Gate (jede Erweiterung MUSS alle Punkte erfüllen)

| # | Kriterium | Beleg |
|---|-----------|-------|
| G1 | **Objektiv definierbar** — kein „stark/sauber/signifikant" ohne benannten Parameter | Spec-Text + `PROPOSED DEFAULT` |
| G2 | **Testbar** — Golden-Test (Chartmuster → erwartetes Objekt) + Grenzfälle | `tests/unit/` |
| G3 | **Kausal / zeitlich korrekt** — nur `confirmed`-Bars, keine Zukunftskerze | Look-ahead-Test |
| G4 | **Point-in-Time** — `available_at` je Datensatz; im Backtest nur `<= information_cutoff` | PIT-Test (Live-Lauf == Backtest-Lauf zu jedem `t`) |
| G5 | **Datenqualität geprüft** — Vollständigkeit, Frische, Duplikate, Quellen-Übereinstimmung | `data/quality` + `data_confidence` |
| G6 | **Overfitting geprüft** — zählt gegen `antioverfit.max_free_params` (8); Sensitivitäts-Plateau; mechanistische Begründung | `anti-overfitting.md` §4, §7 |
| G7 | **Validierbar** über Backtest / Replay / Paper-Live — verbessert `OOS Expectancy R` um ≥ `complexity.min_improvement_r` (0.05) | Validierungslauf, Experiment-Registry |

**Default = Weglassen.** Eine Erweiterung, die G1–G7 nicht klar besteht, wird nicht aufgenommen.

## 2. Continuous Improvement Audit (vor jedem größeren Strategy-Abschnitt)

Sieben Fragen, kurz beantworten:

1. Fehlt ein wichtiger Analysebaustein?
2. Fehlt eine relevante Datenquelle?
3. Gibt es eine bessere Methode für einen bestehenden Baustein?
4. Professionelle Markt-/Microstructure-Daten mit echtem Mehrwert?
5. Informationen, die für Entry/Exit, Regime oder Risk wichtig sind?
6. Neue wissenschaftliche/institutionelle Erkenntnisse, die objektiv testbar sind?
7. Risiken durch Look-ahead / Survivorship / Data Leakage / Overfitting / Datenqualität?

Für jedes sinnvolle Ergebnis: **Nutzen · Datenquelle · Integrationspunkt · Test-/Validierungsplan
· Priorität (CRITICAL / HIGH / MEDIUM / OPTIONAL)** — dann implementieren, wenn sinnvoll.

## 3. Rolle zusätzlicher Daten (verbindlich)

Zusätzliche Daten **erzeugen nie automatisch neue Trades**. Sie dürfen nur:
**bestätigen · abschwächen · vetoen · Timing verbessern · Regime verändern · Risk reduzieren ·
Exit auslösen.** Die zentrale Frage bleibt: *„Ist jetzt ein guter Entry?"* und *„Wann ist der
Trade nicht mehr gültig?"*

**Asset-/Timeframe-aware:** nicht jede Assetklasse braucht jede Quelle. COT = langsamer Kontext
(Gold/FX), nie M5-Trigger. Funding/OI = Crypto-relevant. Order Flow = kurzfristiges Timing.
Gewichtung je `asset_class` × `timeframe`.

## 4. Externe Research-/Analysten-Quellen

**Prioritätsordnung:** (1) Primärquellen (2) offizielle Daten (3) Börsen-/Derivate-Daten
(4) Zentralbanken/Behörden (5) etablierte institutionelle Research (6) wissenschaftliche Forschung
(7) hochwertige professionelle Marktanalysen. **Einzelne Trader/Influencer sind NIE ein Signal.**

Wird eine externe Meinung integriert, wird gespeichert: **Quelle · Veröffentlichungszeitpunkt ·
`information_cutoff` · Richtung/These · Confidence · mögliche Interessenkonflikte.**
Widersprüchliche Quellen werden **nicht gemittelt** — das System erkennt den Konflikt und führt
ihn als **Unsicherheit** (`confidence`↓ / ggf. `NO_TRADE`).

## 5. Pipeline-Zielbild (entwickelt sich dahin, kein Sofort-Umbau)

```
RAW DATA → DATA QUALITY → MARKET CONTEXT → PRICE ACTION/STRUCTURE → LIQUIDITY/SMC
  → MICROSTRUCTURE → DERIVATIVES → MACRO/NEWS → CROSS-ASSET → POSITIONING
  → MARKET REGIME → MULTI-TIMEFRAME → SETUP FSM → CONFIRMATION → CONFLUENCE → VETO
  → SCORE → CONFIDENCE → RISK → DYNAMIC SIGNAL → ALERT / POSITION MANAGEMENT
```

Neue Ebenen werden **zwischen** bestehende gehängt, ohne die geteilte Engine
(`strategy.evaluate(MarketContext) -> Decision`) oder die Backtest=Live-Parität zu brechen.

---

## 6. Audit-Backlog — Stand 2026-08-28 (nach Phase-3 Primitives + Market Regime)

**Kernbefund:** Der Strategy-Kern (Price Action / Structure / Liquidity / SMC / Premium-Discount /
Market Regime) ist vollständig und getestet (387 Tests). **Kein CRITICAL-Baustein fehlt** für die
eingefrorene Spezifikation `SMC-SWEEP-REV-01` (`strategy_version 0.1.1`). Die meisten
Zusatzebenen sind bereits von der Spec / `ARCHITECTURE_GAP_AUDIT.md` als spätere Phasen
vorgesehen. Nächster Schritt bleibt **Sessions** (entsperrt 4 definierte Komponenten).

### CRITICAL — nichts offen im aktuellen Strategy-Kern

### HIGH

| Thema | Nutzen | Datenquelle | Integrationspunkt | Test/Validierung | Phase |
|---|---|---|---|---|---|
| **Tiefe Historie ≥ 180 Tage M5 BTC/ETH** (C12) | Regime braucht ~100 D1-Bars Warmup (`vol.lookback`); ohne das ist die Engine end-to-end nicht testbar (alles `REGIME_UNCLEAR`) | Bybit v5 Kline (Pagination), bereits Provider vorhanden | `scripts/fetch_history.py` | Backfill + `check_ohlcv_series`; Regime liefert non-`UNCLEAR` auf realen Daten | vor End-to-End-Backtest |
| **News-Veto** (`analysis/news.py`) + **PIT-Fixture** (C10) | harter Veto V4 in der eingefrorenen Spec (`SMC-SWEEP-REV-01` §17); fail-safe blockiert sonst jeden Entry | statische PIT-Fixture FOMC/CPI/NFP/PCE mit `available_at`; später Finnhub-Kalender | zwischen MARKET CONTEXT und SETUP FSM (Veto-Vorprüfung) | Leakage-Test: `actual` erst ab `scheduled_time`; Kalenderstand nur `<= information_cutoff` | Phase 3 (nach FSM) |
| **Sessions** (`analysis/sessions.py`) | entsperrt: `session_high/low` Liquidität (§4.1), Session-Entry-Gate (§18), `session_range` PD (§13), `structure.py` §2.3 Range-Bruch | `refdata/seed.py` SessionSpecs + `refdata/calendar.resolve_session` (existiert) | LIQUIDITY-Ebene + SETUP-FSM-Gate | DST-Auflösung, Overlap, Filter-Gründe, Look-ahead (nur abgeschlossene Sessions) | **jetzt** |
| **`structure.py` §2.3 Range-Bruch** | aktuell kein Bruch in `RANGE`/`UNCLEAR`; die Range-Variante von `SMC-SWEEP-REV-01` §1 braucht ihn | `regime.py` liefert `range_low/range_high` | `structure_breaks` erweitern (`origin=RANGE`) | Golden: Range → Close jenseits Grenze → BOS `origin=RANGE` | Phase 3 (nach Sessions/Regime-Verdrahtung) |

### MEDIUM

| Thema | Nutzen (kausal) | Datenquelle (Priorität) | Rolle | Phase |
|---|---|---|---|---|
| **Derivatives — Funding Rate** | crowded-Positioning-Proxy; `news-rules.md` klassifiziert `\|funding\| ≥ 0.05%` bereits als MEDIUM-Impact | Bybit v5 `/v5/market/funding/history` (Provider vorhanden), PIT ab Settlement | News/Regime-Kontext (nicht Trigger) | Phase 3/5 |
| **Derivatives — Open Interest** | OI↑ + Preis flach = Squeeze-Risiko; OI↓ in Trend = Distribution → Regime-Vorsicht | Bybit v5 `/v5/market/open-interest` (Provider vorhanden) | **neue Regel** → volle G1–G7-Prüfung; Regime-Modulator, Veto-Kandidat | Phase 5+ (nach Paper-Live-Baseline) |
| **Cross-Asset — DXY / US-Yields** | `sizing.md` §4 Korrelationsmodell (XAUUSD↔DXY −0.7); Risk-off-Kontext | FRED (DGS2/DGS10) PIT, ICE DXY / Polygon | RISK-Ebene (Korrelations-Cluster), Regime-Kontext | Phase 4/7 |
| **Macro — ALFRED-Vintages** | CPI/GDP werden Monate später revidiert; nur Erst-Veröffentlichung nutzen | **ALFRED** (nicht FRED — Vintages), `available_at` = Release-Datum | MACRO-Kontext; kein direkter Trigger in `0.1.1` | Phase 7+ |
| **Survivorship (Multi-Asset)** | delistete Altcoins / Index-Rekonstitution | `refdata` `listed_at`/`delisted_at` (Felder vorhanden), `InstrumentMaster.scan_universe(at=...)` | Universe-Query je `t` | wenn > BTC/ETH |
| **Robuste Regression für `slope_norm`** | Theil-Sen statt OLS ⇒ unempfindlicher gegen einzelne Spike-Bar | keine externe Quelle | `analysis/regime.py` `slope_norm` (ersetzen, nur nach Sensitivitäts-Vergleich) | Kalibrierungsrunde |

### OPTIONAL — parken, nur bei nachgewiesenem Bedarf

| Thema | Warum geparkt |
|---|---|
| **Order Book / L2 / CVD / Delta / Footprint / Absorption** | Für M5–M15-SMC-Swing-Trading marginaler Zusatznutzen bei hohem Daten-/Infra-Aufwand; **kein sauberer Backtest** (kaum historische L2-Tiefe verfügbar, nur Realtime). Das Preis-Primitiv `sweep_clarity` (Docht/Reclaim) fängt „Absorption/Rejection" bereits ab. **Revisit** nur, wenn Paper-Live zeigt, dass die Strategie systematisch an Fake-Sweeps ausgestoppt wird, die L2 markiert hätte. |
| **Options / IV — DVOL, Skew, Term Structure** | Nur Deribit (Crypto) mit dünner Historie; die ATR-Perzentil-Vol-Achse funktioniert. Kandidat als *zusätzliche* Dimension der Volatility-Achse in einer späteren Kalibrierungsrunde. |
| **Positioning — CFTC COT** | Wöchentlich, 3-Tage-Lag (Di-Daten, Fr-Release), `available_at` = Freitag. Sinnvoll als **langsamer** Kontext für Gold/FX, nie M5-Trigger. |
| **ETF Flows** | Relevant für BTC/Gold als Wochen-Kontext; Datenlizenz + PIT-Timestamps nötig. |
| **Sentiment / News-Sentiment** | Höchstes Missbrauchsrisiko (Influencer ≠ Wahrheit). Nur über lizenzierten Provider mit PIT-Timestamps **und** der Konflikt-/Quellen-Metadaten-Maschinerie aus §4. Niedrigste Priorität. |
| **Liquidations-Feed** | Realtime-Kaskaden-Signal; historische Abdeckung lückenhaft. Kandidat für Live-only-Alerts, nicht für den Backtest. |

### Risiken (Frage 7) — laufende Kontrolle

| Risiko | Status | Kontrolle |
|---|---|---|
| Look-ahead | **kontrolliert** — jeder Detektor hat einen Look-ahead-Test; `MarketContext` erzwingt `close_time <= information_cutoff` | bei jedem neuen Baustein Pflicht-Test |
| Data Leakage | **kontrolliert** für Preis; **offen** für News/Macro/Funding bis deren PIT-Fixtures + Tests stehen | HIGH-Backlog oben |
| Survivorship | **trivial erfüllt** (BTC/ETH); Regel steht für Multi-Asset | MEDIUM-Backlog |
| Overfitting | **kontrolliert** — alle Werte `PROPOSED DEFAULT` / `TO-VALIDATE`, 8-Parameter-Budget, noch keine Optimierung | Aufnahme-Gate G6/G7 |
| Datenqualität | **kontrolliert** — `data/quality` + `data_confidence` + fail-safe | bei jeder neuen Quelle: Quality-Check + `source_term` |

---

## 6a. Detaillierte Kandidaten-Bewertung — Audit 2 (2026-08-28, vor MTF/FSM)

Spalten: **Quelle · Frequenz · Historie · Latenz · Asset · TF · Info-Wert · Risiko · Integration
· Test**. Rolle: **K** = nur Kontext · **M** = darf modulieren (Score/Confidence/Size) · **V** =
darf vetoen · **T** = darf Timing/Exit auslösen. **Nie** darf eine Zusatzquelle einen Trade
*erzeugen*.

| Kandidat | P | Kurzbewertung |
|---|---|---|
| **Order Book / Market Depth (L2)** | **REJECT** | Quelle Bybit/Kraken WS · Frequenz ms · **Historie faktisch keine** (Tardis/Kaiko teuer, GB/Tag) · Latenz realtime-only · Asset Crypto · TF sub-M1 (unsere Sweep-TF = M15) · Info-Wert für M5+ marginal (`sweep_clarity` deckt die preis-offenbarte Absorption ab) · Risiko: **kein Backtest** (G7 ✗), **bricht Backtest=Live-Parität**, Spoofing-Noise · Integration: Live-only-Confirmation frühestens Phase 11 · Test: nur Live-Shadow. |
| **Bid/Ask Imbalance** | **REJECT** | Wie L2 (gleiche Quelle, keine Historie, realtime-only). |
| **CVD / Delta** | **MEDIUM** | Quelle: **Trade-Tape** (`publicTrade` WS taker-Seite — Provider + Modell existieren aus Phase 2; Bybit-Tagesdumps `public.bybit.com`) · Frequenz realtime, auf Bars aggregierbar · **Historie ja** · Latenz realtime/batch · Asset **Crypto hoch**, Gold/MT5 kein Tape, Stocks nur bezahlt (TAQ) · TF M1–M15 · Info-Wert **echt**: CVD-Divergenz am Sweep (Preis neues Tief, Delta nicht) = stärkerer Reclaim (Cont-Kukanov-Stoikov 2014; Orderflow-Literatur) · Risiko: Look-ahead (nur *confirmed*-Bar-Delta), Overfitting (Divergenz-Schwelle), Wash-Trading auf Kleinbörsen (nicht Bybit BTC/ETH) · Integration: MICROSTRUCTURE → neue objektive Primitive `cvd_series` + `cvd_divergence_at_sweep` → moduliert `sweep_clarity`/`sweep_unambiguity` (**M**, kein Gate) · Test: Golden (konstruiertes Tape → bekanntes CVD), Look-ahead-Test, Backtest ±CVD-Term → OOS-Δ ≥ 0.05R (G7). **Nach FSM-Baseline** (damit das Δ sauber messbar ist). |
| **Volume Profile (HVN/LVN/POC)** | **OPTIONAL** | Quelle Trades/OHLCV-Volumen · Historie ja · Asset Crypto/Stocks ok, Gold-Volumen broker-spezifisch unzuverlässig · TF Session/D1 · Info-Wert: zusätzliche S/R-/TP-Levels — **überlappt stark** mit vorhandenen Liquidity-Levels (Swing/Equal/Session/PDH-PDL) · Risiko: parameter-schwer (Fenster, Value-Area-%, Bin-Größe) → Overfitting · Integration: LIQUIDITY, als TP-Ziel-Quelle · geparkt. |
| **Open Interest** | **MEDIUM** | Quelle **Bybit `/v5/market/open-interest` (Provider existiert)**, Coinglass-Aggregat · Frequenz ~5 min · Historie Bybit ~6 Monate (5m/15m/1h), Coinglass tiefer (bezahlt) · Latenz ~5 min (ok für M15+) · Asset **Crypto hoch**, Gold nur CME-Futures-OI (täglich) · TF H1–D1 Kontext · Info-Wert **echt, crypto-spezifisch**: OI-Sturz am Sweep-Bar = Zwangsliquidations-Flush = Reversal-Treibstoff (deckt sich mit der Stop-Hunt-These) · Risiko: Bybit-OI ≠ Gesamt-Markt-OI (aber für Bybit-Ausführung richtig), ~5-min-Lag ⇒ `available_at = ts + Lag`, Datenlücken · Integration: DERIVATIVES → Regime-Modulator (OI-Divergenz-Flag) + Confidence-Term am Sweep (**M**) · Test: Golden, PIT-Test (Lag respektiert), Backtest ±OI-Term. **Nach FSM-Baseline** (neue Regel → volle G1–G7). |
| **Funding Rate** | **HIGH** | Quelle **Bybit `/v5/market/funding/history` (Provider existiert, Teil-Historie geladen)** · Frequenz Settlement alle 8 h · **Historie voll** · Latenz: bekannt ab Settlement · Asset **nur Crypto-Perps** · TF: (a) direkter **Kosten**-Input (`sizing.md` §2 Schritt 11, `FUNDING_COST_EXCESSIVE` **V**), (b) H4–D1 Crowding-Kontext (`news-rules.md`: `\|funding\| ≥ 0.05 %/8h` = MEDIUM-Impact **M**) · Info-Wert: **bereits in der eingefrorenen Spec** — es geht ums Verdrahten, nicht Hinzufügen · Risiko: predicted vs. realized (Backtest = realized, nur ab Settlement) · Integration: RISK/Cost-Model + NEWS-Klassifikation · Test: PIT-Test (Funding nur ≤ Settlement), Cost-Model-Test, News-Klassifikations-Test. **Slot: mit `analysis/news.py` + Phase 4.** |
| **Liquidations** | **REJECT** | Quelle Bybit `allLiquidation` WS / Coinglass · Frequenz Events · **Historie schlecht** (WS live-only, Coinglass-Granular bezahlt) · Latenz realtime · Asset Crypto · TF M1–M5 · Info-Wert: Kaskade in Liquidität = Flush — aber der Preis (Sweep-Docht + Volumen-Spike) zeigt das bereits · Risiko: **kein Backtest**, Parität · Integration: Live-only-Alert-Anreicherung (Phase 11), nie Backtest-Gate. |
| **Basis (Perp vs. Spot)** | **OPTIONAL** | Quelle Perp-Mark − Spot-Index (beides Bybit) · Historie ja · Asset Crypto · TF H4–D1 · Info-Wert: Basis-Blowout = euphorischer Hebel — **weitgehend redundant mit Funding** (Funding ≈ basis-getrieben) · geparkt. |
| **Options IV / Skew / Term Structure** | **OPTIONAL** | Quelle **Deribit** (einzige liquide Crypto-Options-Venue; DVOL-Index) / CBOE (GVZ Gold, VIX SPX) · Frequenz realtime · Historie DVOL ab ~2021, IV-Surface dünn · Latenz realtime · Asset BTC/ETH (Alts keine), Gold/SPX via GVZ/VIX · TF D1-Regime · Info-Wert: IV/RV + Skew geben eine *vorausschauende* Vol-Dimension, die der ATR-Perzentil-Achse fehlt — **zweiter Ordnung** für M15-Sweep · Risiko: dünne Historie, BTC/ETH-only, neue Datenabhängigkeit (Deribit), Overfitting · Integration: REGIME-Volatility-Achse-Verfeinerung (Kalibrierungsrunde). Konkreter Input = **DVOL-Index**. |
| **CFTC COT** | **MEDIUM (Gold/FX-Phase) · OPTIONAL (Crypto)** | Quelle **CFTC (offiziell, gratis)** · Frequenz **wöchentlich** (Di-Snapshot, **Fr 15:30 ET Release**) · Historie Jahrzehnte · Latenz ~3 Tage, **`available_at` = Freitag** · Asset: Gold/Silber/FX/Indizes ja; Crypto = nur CME-BTC/ETH-Futures (kleiner Teil des Gesamt-Positioning, marginal für Bybit) · TF **wöchentlich/D1-Kontext, NIE M5/M15-Trigger** · Info-Wert: für Gold/FX legitimer langsamer Contrarian-Kontext (Positioning-Extreme) · Risiko: **klassische Leakage-Falle** — Fr-Release-Datum, nicht Di-Referenzdatum · Integration: POSITIONING → langsamer Kontext, moduliert nur Confidence/Size für Gold/FX (**M**) · Test: PIT-Test (Daten erst ab Freitag). **Auf die Multi-Asset/Gold-Phase verschoben.** |
| **ETF Flows** | **OPTIONAL (BTC) · MEDIUM (Gold-Phase)** | Quelle: BTC — Farside Investors (gescraped) / Issuer-Disclosures; Gold — World Gold Council (GLD/IAU-Holdings); Aktien — ICI/Lipper (bezahlt) · Frequenz **täglich (T+1)** · Historie: BTC-Spot-ETF **erst ab Jan 2024** (zu kurz für Validierung, G7 min-samples ✗), Gold-Holdings Jahrzehnte · Latenz T+1 · Asset BTC/ETH/Gold, Alts keine · TF D1–wöchentlich · Info-Wert: BTC-Netflows = strukturelle Nachfrage (2024+), aber **~18 Monate Historie → nicht validierbar** · Integration: MACRO/POSITIONING-Kontext D1 · Revisit 2026–2027. Gold-ETF: MEDIUM in der Gold-Phase. |
| **DXY** | **HIGH (Gold-Phase) · MEDIUM (Crypto-Kontext)** | Quelle: ICE DXY (bezahlt) / FX-Proxy-Basket (aus FX-Bars berechenbar) / FRED `DTWEXBGS` (breit, täglich) · Frequenz realtime (FX) / täglich (FRED) · Historie Jahrzehnte · Latenz realtime / ~1 Tag · Asset **Gold hoch** (XAUUSD↔DXY ρ ≈ −0.7, **schon in `sizing.md` §4**), Crypto mittel (Risk-off-Proxy, verrauscht), FX hoch · TF H4–D1-Kontext, **kein M5-Trigger** · Info-Wert: für Gold erstklassiger Intermarket-Regime-Input; für Crypto schwacher Sentiment-Proxy (Funding/OI direkter) · Risiko: Korrelation regime-abhängig/instabil (BTC-DXY-ρ kippt), Overfitting bei harter Regel · Integration: CROSS-ASSET → REGIME-Modulator (Risk-on/off) + RISK-Korrelationsmodell (**K/M**) · Test: rollende Korrelation, regime-konditioniert. |
| **US Treasury Yields** | **HIGH (Gold) · MEDIUM (Crypto)** | Quelle **FRED `DGS2`/`DGS10`/`DFII10` (offiziell, gratis)**, intraday via Polygon · Frequenz FRED EOD (~1 Tag) · Historie Jahrzehnte · Latenz ~1 Tag (FRED) · Asset **Gold hoch** (Realzins ≈ Gold-Haupttreiber), Crypto mittel (Liquiditäts-Regime), FX hoch · TF D1-Regime · Info-Wert: für Gold erste Ordnung; für Crypto langsamer Liquiditäts-Kontext · Risiko: FRED-Lag, regime-abhängig · Integration: MACRO/CROSS-ASSET → REGIME (**K**) · Test: regime-konditionierter Backtest, PIT (`available_at`). Paart mit DXY. |
| **VIX (+ DVOL-Analog)** | **MEDIUM** | Quelle CBOE VIX (EOD gratis / realtime bezahlt), Crypto = Deribit DVOL, Gold = GVZ · Frequenz realtime / EOD · Historie Jahrzehnte · Latenz realtime / 15-min-verzögert (gratis, ok für D1) · Asset Aktien hoch, Crypto mittel (VIX-Spike = globales Risk-off = BTC korreliert), Gold gemischt · TF D1–H4-Regime · Info-Wert: VIX > 30 / scharfer Spike = „Risiko runter" — speist den **`risk_off`-Flag, der schon in `news-rules.md` §6 spezifiziert ist** · Risiko: realtime bezahlt, Korrelation regime-abhängig · Integration: CROSS-ASSET → `risk_off`-Flag (**V/M**) + Size-Modulator · Test: VIX-konditionierter Backtest, PIT. Der Mechanismus (`risk_off`) existiert schon; VIX/DVOL ist ein sauberer Auto-Trigger dafür. |
| **Economic Calendar** (FOMC/CPI/PCE/NFP/GDP) | **HIGH** | Quelle: **Finnhub `/calendar/economic`** (Free-Tier) / Trading Economics (bezahlt) / **kuratierte statische PIT-Fixture** (MVP) · Frequenz: Termine Tage–Wochen vorab, `actual` zur `scheduled_time` · Historie: Fixture aus offiziellen Release-Kalendern (bls.gov, federalreserve.gov, bea.gov) · Latenz: 0 (Termin bekannt), `actual` bei Release · Asset **ALLE** (`news-rules.md`-Routing: USD-Makro → Crypto/Gold/FX/Aktien) · TF: Event-Zeit → **harter Veto V4** (`SMC-SWEEP-REV-01` §17) + Blackout-Fenster + Pre-Positioning-Ban · Info-Wert: **kritisch für Korrektheit** — ohne ihn ist jeder Backtest-Bar `NO_TRADE` (fail-safe) oder blind in FOMC · Risiko: **Leakage** (nur `available_at ≤ information_cutoff`; `actual` erst ab `scheduled_time`; nie der revidierte Kalender) — `NewsEvent`-Modell erzwingt den `actual`-vor-`scheduled`-Guard bereits · Integration: NEWS → Veto V4 + Blackout · Test: Leakage-Test (Live-Lauf == Backtest-Lauf zu jedem `t`), Blackout-Unit-Tests je Impact, fail-safe-Test. **MVP = kleine kuratierte Fixture (FOMC/CPI/PCE/NFP 2023–2025 mit `available_at`), keine Live-API.** |
| **Breaking News (ungeplant)** | **MEDIUM (objektiver `auto_risk_off`) · OPTIONAL (Feed)** | Quelle: Finnhub-News / CryptoPanic / Benzinga (bezahlt) / Bloomberg-Reuters (sehr bezahlt) · Frequenz realtime · Historie: **schlecht für sauberes PIT** (Publikationszeit ≠ „Markt wusste es") · Latenz Sek–Min · Asset: Crypto (Hacks/Regulierung/ETF — `news-rules.md` listet sie), alle (Geopolitik) · TF: Event-getrieben → **Risk-off-Flag, kein Trigger** · Info-Wert: der **objektive `auto_risk_off`** (extreme unerklärte Vola, `news.shock_move_atr`) ist **HIGH und testbar**; ein echter News-Feed ist OPTIONAL/teuer/kaum backtestbar · Risiko: massive Leakage bei falschem Timestamping; Sentiment-Scoring subjektiv (G1 ✗ ohne rigorose Quelle); Influencer-Noise (Nutzer-Warnung) · Integration: NEWS → `risk_off`-Flag · Test: `auto_risk_off` testbar (Vol-Shock → Flag → Block); kuratierte Szenario-Liste (LUNA/FTX/ETF). **Split: `auto_risk_off` mit dem News-Modul bauen; Feed verschieben.** |
| **Institutionelle Research-Daten** | **OPTIONAL (On-Chain) · REJECT (Analysten-Text)** | Quelle: Bank-Research (nicht API-verfügbar, Lizenz verbietet Weitergabe) → **REJECT** (Nutzer-Regel: Meinung ≠ Signal); On-Chain: **Glassnode / CryptoQuant / CoinMetrics** (bezahlt, gute Historie) · Frequenz täglich–wöchentlich · Historie gut (bezahlte Tiers) · Latenz: On-Chain-Metriken mit Berechnungs-Lag, teils rückwirkend angepasst · Asset Crypto · TF D1–wöchentlich · Info-Wert: Exchange-Netflow / Stablecoin-Supply / LTH-Verhalten = echter Angebots-/Nachfrage-Kontext (D1) · Risiko: bezahlt, Look-ahead (Metrik-`available_at`), **Overfitting** (Dutzende Metriken → Cherry-Picking) · Integration: MACRO/On-Chain → REGIME-Modulator BTC/ETH D1 (**K**), **harte Obergrenze 2–3 Metriken mit mechanistischer Begründung** · Test: PIT-Test, langsamer Regime-Kontext-Backtest. Revisit als D1-Regime-Verfeinerung nach dem Kern. |

### Verbesserungen an bereits geplanten Komponenten (Frage 8)

| Komponente | Verbesserung | P |
|---|---|---|
| `analysis/regime.py` `slope_norm` | OLS → **Theil-Sen** (robuste Regression) ⇒ unempfindlich gegen einzelne Spike-Bar. Nur nach Sensitivitäts-Vergleich (Plateau). | MEDIUM (Kalibrierungsrunde) |
| `analysis/regime.py` Volatility-Achse | optionale IV/RV-Dimension (DVOL) als *zweiter* Vol-Indikator | OPTIONAL |
| `core/types.py` `MarketContext` | **jetzt schon** optionale (leere) Slots für `derivatives` / `cross_asset` / `news` vorsehen, damit CVD/OI/Funding/VIX später **ohne** Änderung der `evaluate()`-Signatur andocken (Vorwärtskompatibilität) | HIGH — **beim MTF-Schritt mitnehmen** |
| `analysis/news.py` (geplant) | Design so, dass ein `EconomicCalendar` **injizierbar** ist → FSM ohne Fixture testbar; Fixture ist ein getrennter Deliverable | HIGH — Design-Hinweis |
| `execution/simulation.py` Cost-Model | reale Funding-Historie einspeisen (statt Konstante) | HIGH — Phase 4 |

### Entscheidung: was VOR MTF/FSM implementieren?

**Nichts Neues.** Der Audit bestätigt die Reihenfolge. Alle HIGH-Punkte sind „bereits Spezifiziertes
verdrahten" und haben ihren natürlichen Slot **nach** MTF:
- **Economic Calendar / News-Fixture** (C10) → mit `analysis/news.py` (Schritt 4 der Arbeitsreihenfolge, nach Regime/MTF)
- **Deep History** (C12) → vor dem ersten End-to-End-Backtest (nach dem FSM)
- **Funding-Verdrahtung** → `analysis/news.py` + Phase 4

**Einzige Sofortmaßnahme:** beim MTF-Schritt die **`MarketContext`-Vorwärtskompatibilität** (leere
`derivatives`/`cross_asset`/`news`-Slots) mitnehmen. Die MEDIUM-Kandidaten (CVD, OI, VIX-`risk_off`,
DXY/Yields) kommen in die **Post-FSM-Verfeinerungsrunde**, damit die Komplexitäts-Ratsche (G6/G7)
das Δ sauber messen kann.

---

## 6b. Audit 5 — vor Location-Gate / RR-Gate (2026-08-29)

**Frage:** Fehlt ein objektiver Baustein für Location oder Risk/Reward? Gibt es bessere *dynamische*
SL-/TP-Methoden als die Spec-Defaults?

**Ergebnis: kein neuer Indikator.** Alle Eingaben sind bereits vorhanden
(`swept_leg_range` in `pd.py`, `LiquiditySweep.penetration_extreme`, Entry-Zone aus dem FSM,
`TimeframeContext.liquidity` je TF, `TimeframeContext.structure.last_swing_*`, ATR je TF,
`MarketContext.spread`). Zwei kleine Robustheits-Ergänzungen (aus vorhandenen Daten ableitbar,
spec-konform):

| Ergänzung | Begründung | Priorität | Datenanforderung | Testmethode |
|-----------|-----------|-----------|------------------|-------------|
| **Swept-Leg-Range zeitstempel-basiert** statt index-basiert | `pd.swept_leg_range` nutzt `displacement.start_index/end_index`. In `build_mtf_context` stimmen die Indizes; ein hand-/fremd-gebauter `Displacement` kann out-of-range sein ⇒ konservativer `None`-Pfad. Zeitstempel (`start_bar`/`end_bar`) sind robuster. | LOW | keine | Golden + „degenerierte Range ⇒ WAIT/BLOCK" |
| **TP2-Kandidaten aus H4/M15-Swing-Levels** zusätzlich zur „signifikanten opposing Liquidität" | §13 nennt explizit „**oder** ein H4/M15-Struktur-Level in Richtung D". `structure.last_swing_high/low` liefert das objektiv. | LOW | keine | „TP2 = Swing-Level wenn keine signifikante Liquidität" |

**Bewusst NICHT übernommen:** ein *dritter, engerer* SL-Kandidat (z. B. „hinter dem letzten M5-HL").
`SMC-SWEEP-REV-01` §10 schreibt die **ungünstigere** der beiden Kandidaten vor (Sweep-Extrem ∨
distale Zonenkante). Ein tighterer struktureller SL würde diese konservative Vorgabe verletzen und
das 1R künstlich verkleinern ⇒ **abgelehnt** (widerspricht der eingefrorenen Spec).

**Dynamik-Check:** Die SL-/TP-Werte sind bereits marktstruktur-getrieben (Sweep-Extrem, Zonenkante,
nächste/​signifikante opposing Liquidität, Swing-Level) — die R-Multiplikatoren wirken nur als
**Deckel/Boden**, nicht als Primärquelle. Fixe Werte kommen nur zum Tragen, wenn die Struktur
**keinen** Anker liefert (keine opposing Liquidität) — dann R-Cap + `note`, damit Confidence das
später abwerten kann.

**Leakage/Overfitting:** rein geometrische Funktion über `MtfContext` (≤ `information_cutoff`), keine
neue Datenquelle, kein Fitting. Alle Multiplikatoren bleiben `PROPOSED DEFAULT` → Sensitivitäts­lauf
offen (Backlog, s. u.).

**Backlog aus Confirmation (weiterhin offen):** nativer M1-Datenloader · `choch_min_leg_atr`-Kalibrierung
auf M1 · Sensitivitätsprüfung aller `PROPOSED DEFAULT` (Addendum §2 + Setup §8/§10/§16).

---

## 6c. Audit 6 — vor der Confluence-Engine (2026-08-29)

**Frage:** Fehlt etwas für eine professionelle Confluence? Gibt es eine bessere Methode als
„Punkte addieren"? Welche Faktoren sind Kontext statt Score?

**Ergebnis: kein neuer Indikator.** Alle Faktoren stammen aus bereits validierten Bausteinen
(MTF-Bias/Struktur, Sweep/Reclaim, Displacement, Struktur-Bruch, FVG/OB, Premium/Discount, Regime,
Location-/RR-Gate, Confirmation, `MarketContext`-Slots für News/Derivatives/Cross-Asset).

**Bessere Methode als Addition — umgesetzt:** *relevanz-gewichteter Gruppen-Durchschnitt über
unabhängige Informationsdimensionen* statt Summe über Einzelfaktoren.
- Korrelierte Faktoren teilen sich eine **Gruppe** (z. B. `MOMENTUM_STRUCTURE` = Displacement +
  Struktur-Bruch + Phase). Innerhalb der Gruppe wird **gemittelt**, nicht summiert ⇒ ein
  redundanter gleichgerichteter Faktor verschiebt das Gruppen-Ergebnis **nicht**.
- BOS/CHoCH werden **nie getrennt** als Faktoren geführt — es gibt **einen** `structure_shift`
  (aus `candidate.structure_break.kind`).
- `net_confluence` = gewichtetes Mittel der **verfügbaren** gescorten Gruppen (nicht der Faktor-Zahl).

**Als *echte* Verbesserung erkannt, aber verschoben:** eine **Log-Odds-/Bayes-Kombination** (jeder
Faktor = Likelihood-Ratio, Kombination im Log-Odds-Raum unter bedingter Unabhängigkeit je Gruppe).
- *Begründung:* prinzipientreuer als ein Mittelwert; erlaubt echte „Evidenzstärke".
- *Priorität:* MEDIUM — **nach dem ersten OOS-Backtest**. Vorher scheitert es an G1/G6: die
  per-Faktor-LR sind ohne gelabelte Trade-Ergebnisse reine Ratewerte.
- *Datenanforderung:* gelabelte Trade-Outcomes je Faktor-Ausprägung.
- *Testmethode:* Log-Odds- vs. Mittelwert-Confluence auf OOS-Expectancy-Monotonie vergleichen
  (schneiden Setups mit höherem `net_confluence` messbar besser ab?).
- Die aktuelle Architektur ist bereits vorbereitet: jeder `ConfluenceFactor` trägt seinen
  Roh-Beitrag ⇒ die Aggregations-Funktion ist austauschbar, ohne die Faktor-Extraktion zu ändern.

**Kontext statt Score** (`scored=False`, fließt in **Confidence**/Veto, **nicht** in `net_confluence`):
`mtf_disagreement`, `volatility_regime`, `phase_compression`, `session_context`, `data_confidence`.
Grund: das sind **Filter/Klarheits**-Größen, keine gerichtete Entry-Evidenz.

**Fehlende Daten:** `news`/`derivatives`/`cross_asset` heute leer ⇒ Faktor `data_quality=UNAVAILABLE`,
Beitrag 0, Gruppe **aus dem Nenner ausgeschlossen** (keine künstliche +/−-Bewertung). Wird eine
Quelle später verfügbar, ändert sich nur die Extraktions-Funktion, nicht die Aggregation.

**Leakage/Overfitting:** alle Eingaben aus `MtfContext` (≤ `information_cutoff`); News nur aus
`market_context.news.events` (bereits PIT-gefiltert). Gruppen-Gewichte **gleich (1.0)** im MVP
(C2-Geist) — differenzierte Gewichte sind Kalibrierungsziel.

---

## 6d. Audit 7 — vor der Veto-Engine (2026-08-29)

**Fragen:** Fehlt ein Veto? Sind manche Vetos eigentlich Score-Faktoren? Redundanz? Gemeinsame
Ursachen? FP/FN-Risiken? Was muss OOS validiert werden?

**Fehlendes Veto:** keins. Die V1–V10-Matrix (`contradictions.md` §4, Setup §23) ist eingefroren
und deckt alle **harten Barrieren** ab. Alles andere (Kill-Switch, Session-Filter, Cooldown-after-Stop,
`DUPLICATE_EXPOSURE`, `ENTRY_INTO_OPPOSING_HTF_ZONE`/C9) ist ein `NoTradeReason` an einem **früheren**
Pipeline-Schritt (No-Trade-Checkliste bzw. Contradiction-Matrix C1–C12) — **nicht** Sache der Veto-Engine.

**„Vetos, die eigentlich Score-Faktoren sind":** bewusst **nicht** als Veto geführt:
- Volatilität `HIGH` (nur `EXTREME`/`LOW` → V3) — `HIGH` ist tradebar (fließt in
  `displacement_strength`-Normierung / `regime_alignment`).
- `messy_sweep` (1–2 Pools) — **Penalty** (`contradictions.md` §5), erst ab 3 Pools Veto.
- `proximity_opposing_htf_zone` < 50 % Überlappung — **Penalty**; ≥ 50 % ist C9 (Matrix, nicht Veto).

**Redundanz / gemeinsame Ursache — als Verbesserung integriert:** `VetoRecord.correlated_with`
verknüpft Vetos mit derselben Wurzel, damit die App das gruppieren kann:
- **V6 ↔ V7** (stale/lückenhafte Daten senken `data_confidence` **und** erhöhen das Datenalter),
- **V8 ↔ V10** (ein RR-Gate-BLOCK kann `SL_TOO_WIDE`/`SL_TOO_TIGHT` **und** `RR_BELOW_MIN` tragen).
CONFLICTING-Regime wird eindeutig **V1** zugeordnet (nicht zusätzlich V3); UNCLEAR/EXTREME/LOW/
COMPRESSION → **V3**.

**FP/FN:**
- V5 nutzt `close` jenseits des Sweep-Extrems (nicht Docht) ⇒ geringes FP-Risiko; Intrabar-Re-Sweep
  auf M15-Close nicht sichtbar (FN) — die FSM-Invalidierung + Klasse-B (post-entry) fangen das ab.
- V7-Spread aus `market_context.spread`; fehlt er (Paper/Demo) ⇒ **NOT_AVAILABLE, nicht blockierend**.
- V4 blockiert bei fehlendem News-Feed (`require_news_feed=True`, spec-konform C10/`news-rules.md`) —
  das ist ein **True Positive** (ohne Feed kein Entry), kein Bug.
- V9 ohne `portfolio_context` ⇒ **pass-through** (C9); reale korrelierte Exposure erst mit
  Phase-4-`PortfolioState` vollständig (dokumentierter FN).

**OOS zu validieren:** V3 (Vol-Perzentil-Grenzen), V6 (`min_data_confidence = 0.50`),
V7 (`max_spread_atr = 0.10`, `max_spread_pct = 0.05 %`, `max_data_age_periods`), V9
(`correlation_threshold`, `cluster_cap_pct`, `portfolio_heat_cap_pct`). **Strukturell korrekt / geringer
Validierungsbedarf:** V1 (D1/H4 gegensätzlich), V2 (pd-Mathematik), V8/V10 (RR-Mathematik) — reine
deterministische Folgen eingefrorener Regeln.

---

## 6e. Audit 8 — vor der Confidence-Engine (2026-08-29)

**Fragen:** Sind die Komponenten unabhängig genug? Double-Counting? 40/60 nur ein Default? Was
muss OOS getestet werden? Fehlt ein Faktor? Sollte einer nur Kontext sein? Bessere Mathematik?

**Unabhängigkeit / Double-Counting:**
- Die 3 Ebenen sind **strukturell getrennt**: `data_confidence` (Datenlage), `analysis_confidence`
  (Erkennungs-Sicherheit), `setup_confidence` (Kombination). Sie messen verschiedene Dinge als
  Score (Qualität) und Confluence (Richtungs-Stütze).
- Innerhalb `analysis_confidence`: 6 Terme, je ein **eigener Erkennungs-Aspekt** (Zeit /
  Bruch-Sauberkeit / Sweep-Eindeutigkeit / Regime-Klarheit / TF-Kohärenz / Zonen-Frische). Milde
  Korrelation `regime_clarity ↔ htf_mtf_agreement` (klares Regime auf D1+H4 stimmt eher überein) —
  bewusst als **zwei Terme** belassen (Spec §3), Kalibrierungs-Hinweis.
- Dass Confidence dieselben *Roh*-Eingaben wie Confluence/Score nutzt (`break_distance_atr`,
  Sweep-Docht, `mtf_disagreement`, `fill_fraction`) ist **kein** Double-Count — die Fragen sind
  orthogonal (*„erkannt?"* vs. *„stützt die Richtung?"*), so ausdrücklich in `confidence.md` vorgesehen.

**40/60 & Schwellen:** initialer Default (`confidence.md` §9). **OOS/Sensitivity zu validieren:**
`wd/wa` (40/60), `soft_floor` 0.60, `data_hard_floor` 0.50, `min_setup_confidence` 0.60,
`floor_penalty` 0.5, die 6 `analysis_weights`, `single_source_value` 0.80, `source_disagree_atr` 0.30,
`structure_min/max_dist_atr`, `regime_settle_bars`, `regime_margin_pct`.

**Fehlender Faktor?** Keiner. **`confirmation_market`-Bestätigung** wurde erwogen (ein unabhängiges
M1-Muster korroboriert die M5-Struktur) — **abgelehnt**: Scope-Creep + milder Double-Count mit
`structure_clarity`, und die Confirmation ist bereits ein GATE. Sie wird nur als informatives
Evidence-Feld (`confirmation_present`) geführt, ohne den Wert zu beeinflussen.

**Kontext statt Confidence?** Nein — alle 6 Terme sind echte Erkennungs-Sicherheit.

**Bessere Mathematik — als Verbesserung integriert:** Ein weighted-mean allein setzt §5 („unbestätigter
Swing existiert für Entscheidungen nicht") **nicht hart** durch. Deshalb: expliziter
**`unconfirmed_swing`-Flag** (objektiv: `bars_since_confirmation < swing.right` für einen beteiligten
Swing) → `report.blocking = True`, unabhängig vom Mittelwert. Kein neuer Veto, sondern die
spec-geforderte harte Sonderfall-Behandlung. Log-Odds-/Bayes bleibt Backlog (§6c).

---

## 6f. Audit 9 — vor der Scoring-Engine (2026-08-29)

**Fragen:** Double-Counting? Korrelation? Redundante Faktoren? Faktoren, die nur Kontext / besser
Veto sind? Was sollte später asset-/TF-aware gewichtet werden?

**Rollen-Exklusivität (R-06):** eingehalten — die Scoring-Engine implementiert **nur** die 12
`WEIGHTED`-Faktoren (`scoring-rubric.md` §3). `entry_location`/`rr`/`sl` sind je einmal `GATE`
(`entry_location_ok` = V2, `rr_ok` = V8, `sl_definable` = V10) **und** einmal `WEIGHTED`
(`entry_location_depth`, `risk_reward`) — **verschiedene benannte Faktoren**, kein Faktor doppelt.

**Korrelierte WEIGHTED-Faktoren (bekannte MVP-Limitation):** die 12 Spec-Faktoren enthalten
korrelierte Gruppen:
- `liquidity_quality` + `sweep_clarity` + `reclaim_quality` → alle ``LIQUIDITY_EVENT``
- `displacement_strength` + `structure_shift_quality` → beide ``MOMENTUM_STRUCTURE``
- `htf_bias_strength` ↔ `regime_alignment` (beide HTF-Regime)
- `entry_location_depth` ↔ `risk_reward` (tieferer Entry ⇒ besseres RR)

Im **gestaffelten** Gewichtungsschema (`scoring-rubric.md` §4: 20/14/13/12/10/…) ist das
teilkompensiert (die späteren Faktoren wiegen weniger). Im **MVP mit Gewicht 10 pro Faktor** wiegt
die Liquiditäts-Dimension 30/122 statt fair ~14/122. **Kein Bug** — so von C2 / `DECISIONS-0.1.0.md`
#4 definiert, und `KEINE Gewichtsoptimierung in Phase 3`. **Mitigation umgesetzt:** die
Scoring-Engine liefert `correlated_factor_groups` (welche WEIGHTED-Faktoren zur selben
Confluence-Gruppe gehören) → die Kalibrierungsrunde kann einen Gruppen-Cap anwenden. **Priorität:
HIGH — erste Kalibrierungsrunde nach OOS-Datensatz.**

**Faktoren, die eigentlich Kontext sind (Doku-Spannung):** `session_context` und
`data_confidence_bonus` sind in `confluence.py` als **`scored=False`** (Kontext) markiert, in
`scoring-rubric.md` §3 aber **`WEIGHTED`**. Der Score folgt der `scoring-rubric` (beide sind
WEIGHTED-Faktoren) — die Confluence-`scored`-Flags betreffen `net_confluence`, eine andere Größe.
**Kalibrierungs-Frage:** sollten Session/`data_confidence` echte Score-Faktoren sein oder nur
Tier-Gate/Kontext? `data_confidence` wirkt bereits als Tier-Gate (§21) **und** als
`data_confidence_bonus` (§3.12) — bewusst (analog `entry_location`), aber im MVP-Gleichgewicht
überproportional. **Priorität: MEDIUM.**

**Fehlende Faktoren (dokumentiert, NICHT aufgenommen):**
| Kandidat | Begründung | Priorität | Datenanforderung |
|----------|-----------|-----------|------------------|
| `news_proximity_score` | weiches Timing-Signal „Abstand zum nächsten HIGH-Impact-Event" (aus `NewsContext.minutes_to_next_high_impact`) — heute nur der harte V4-Blackout | MEDIUM | News-Modul (C10) |
| `target_structure_quality` | Exit-Qualität: `EntryGeometry.tp2_from_structure` (TP2 an realer Struktur vs. R-Cap) | LOW | vorhanden (`EntryGeometry`) |

**Bessere Mathematik:** datengetriebene Gewichte / Bayesian / Log-Odds / asset-/TF-spezifische
Gewichte — alle **erst nach ausreichendem OOS-Datensatz** (Backlog). Der MVP bleibt bewusst der
gleichgewichtete lineare Score.

---

## 6g. Audit 10 — vor Contradictions + No-Trade (2026-08-29)

**Fragen:** Fehlt ein Hard-Block? Redundante Contradictions? Sind manche eigentlich Confluence/Veto?
Korrelationen? Leakage/Overfitting?

**Fehlender Hard-Block:** keiner. `contradictions.md` C1–C12 und `no-trade.md` §2 (8 Gruppen) sind
eingefroren und vollständig. `DATA_PRICE_ANOMALY` (OHLC-Konsistenz) ist bereits durch den
Pydantic-Validator von `OHLCV` **plus** `data/quality.py` abgedeckt.

**Redundante Contradictions (dokumentiert, bewusst so):** C3–C8 und C10 sind **Echos** vorhandener
Vetos:

| Matrix-Zeile | = Veto |
|--------------|--------|
| C3 (News) | V4 |
| C4 (`data_confidence < 0.50`) | V6 |
| C5 (RR < min_to_tp2) | V8 |
| C6 (korrelierte Exposure) | V9 |
| C7 (D1/H4 gegensätzlich) | V1 |
| C8 (coiled COMPRESSION) | V3 |
| C10 (Ziel-Raum) | V8 |

⇒ Die Contradiction-Engine **re-entscheidet diese nicht**. Sie protokolliert sie als
`VETO_ECHO` (severity INFO, `covered_by_veto=Vx`), das harte NO_TRADE kommt vom Veto-Schritt
(`contradictions.md` §6 Schritt 4 **vor** Schritt 5). In `evaluate()` wird der Contradiction-Schritt
bei aktivem Veto nie erreicht.

**Matrix-eigener Mehrwert:** **C1** (opposing Liquidität darüber gebrochen+gehalten), **C2**
(beide Seiten gesweept = kein Edge), **C9 ≥ 50 %** (Entry in gegen-D HTF-Zone), **C12**
(zwei gegenläufige Setups). Dafür 4 `NoTradeReason` **angehängt** (append-only, in
`contradictions.md` §4 bereits benannt): `OPPOSING_LIQUIDITY_BREAKOUT`, `MESSY_LIQUIDITY`,
`ENTRY_INTO_OPPOSING_HTF_ZONE`, `COUNTER_SETUP_CONFLICT`.

**Negativfaktoren (§5) = Penalties, keine Vetos, keine stille Score-Änderung:** `messy_sweep`
(−8), `proximity_opposing_htf_zone` (−10), `stale_structure` (−5), `weak_displacement` (−6),
`mtf_partial_disagreement` (−7), `wide_sl` (−5), `late_session` (−4). Die Engine **meldet** die
Punktwerte (fürs Ledger + Kalibrierung), `evaluate()` wendet sie im **MVP nicht** an
(`ScoreParams.penalties = {}`, C2). **Unkalibriert** (`contradictions.md` §8) — OOS-Kandidat.

**Korrelationen (dokumentiert):**
- `messy_sweep` (2 Pools **derselben** Seite, Penalty) ↔ C2 (beide Seiten, hart) ↔
  Confidence `sweep_unambiguity` (Erkennungssicherheit) — **drei verschiedene Fragen**.
- `proximity_opposing_htf_zone` (< 50 %, Penalty) ↔ C9 (≥ 50 %, hart) — **derselbe** Overlap,
  ein Schwellenwert ⇒ die Engine nutzt **den einen** Confluence-Faktor `opposing_htf_zone_proximity`
  und verzweigt nach Betrag (kein Doppelzählen).
- `mtf_partial_disagreement` (Band (0.33, 0.66)) ↔ V1 (voll gegensätzlich) ↔ Confidence
  `htf_mtf_agreement` (stetig) — disjunkte Bänder.

**Leakage/Overfitting:** alle Eingaben aus `MtfContext` / den reinen Report-Objekten
(≤ `information_cutoff`). Die Penalty-Punktwerte und die C1/C2-Frische-Fenster sind Params —
unkalibriert, als OOS-Kandidaten markiert.

**No-Trade-Module in Phase 3:** nur die **objektiv jetzt prüfbaren** Gruppen sind scharf geschaltet
(SYSTEM via optionalem `SystemState`, DATA via `mtf.quality`, REGIME via `htf_regime_gate`, TIME via
`analysis/sessions.session_filter`, NEWS via `market_context.news`, STRATEGY-STATE via
`PortfolioContext` + optionaler `InstrumentHistory`, EXECUTION via `spread`/Datenalter). Gruppen, die
Konto-/Broker-/Margin-State brauchen (Loss-Limits, Drawdown, Margin, Funding, API-Health, Clock-Drift),
werden als `not_checked` protokolliert und blockieren **nicht** — vollständige Umsetzung mit
Phase 4 (`risk/`) bzw. Phase 9+ (Broker). **Kein Fake, kein stiller Pass.**

---

## 6h. Audit 11 — Dynamic-Signal / Exit / Alert / Re-Evaluation / Paper-Live (2026-08-29)

**Kontext:** „Overnight Run" Schritte 4–10 (`strategy/signal.py`, `strategy/position.py`,
`strategy/alerts.py`, `strategy/engine.py`, `strategy/m1_feed.py`, `strategy/paper_live.py`).

**Frage 1–2 (Informationswert / fehlt etwas):** Der lebende Lifecycle war Pflichtteil des
Auftrags, kein Kandidat aus der Liste. Kein zusätzlicher Marktdaten-Faktor wurde eingeführt —
die Module orchestrieren die vorhandenen Reports (`EvaluationResult`), sie analysieren nicht neu.

**Frage 3 (Double-Counting / Korrelation):** Das Signal-Diffing liest ausschließlich die
`Decision`-Felder (score/entry/sl/tp/state/reason_codes). Der `SignalState` ab `TRIGGERED`
kommt **allein** aus der `PaperPosition` (`signal_state_for`), nie doppelt aus Decision + Position.
Alerts leiten sich 1:1 aus `SignalChangeKind` ab (eine Änderung → höchstens ein Alert je Typ).

**Frage 4 (nur Kontext statt Entscheidung?):** Die M1-Confirmation bleibt **Kontext** (füttert
Confluence/Confidence), sie blockt im MVP-Modus `limit_at_proximal_edge` nicht — unverändert
gegenüber Audit 8. `strategy/m1_feed.py` liefert das Fenster PIT-korrekt; ohne echte M1-Historie
kommt **leer** zurück (kein Fake). Der 1-Tick-Nachlauf (Kandidat entsteht erst im `evaluate()`
dieses Ticks) ist dokumentiert und für Backtest/Paper unkritisch.

**Frage 5 (bessere Methode?):** Exit-Modell = fixe R-Multiples + BE-Nachzug + Trail auf TP1.
Struktur-basierter Trailing-Stop (Swing-Low-Ketten) ist die dokumentierte Weiterentwicklung —
**Backlog**, braucht denselben M5-Struktur-Feed wie der Entry und OOS-Vergleich gegen das
fixe Modell. Nicht blind ersetzen.

**Frage 6 (asset-/timeframe-spezifisch?):** `PositionParams` (Teilexit-Fraktionen, Puffer,
`pending_expiry_bars`), `SignalParams` (`score_change_eps`), `AlertParams` (Cooldowns) —
**alle unkalibriert**, asset-/regime-spezifische Werte sind OOS-Kandidaten (→ `CALIBRATION_BACKLOG.md`).

**Frage 7 (Risiken):** (a) worst-case-Fill (SL vor TP in einer Bar) ist konservativ, kann echte
Gewinner unterschlagen — bewusst, bis Intrabar-/M1-Fill-Daten da sind. (b) Ein mid-Trade
globaler No-Trade (Kill-Switch) verwaist aktuell die offene Paper-Position im Tick (kein Fill-
Update) — akzeptiert, weil dann das ganze System steht; sauberes „Positionen trotzdem weiter
bewerten" ist Backlog. (c) `SignalTracker.sweep` altert nach `stale_ticks` **Ticks**, nicht
nach Zeit — bei ungleichmäßiger Tick-Rate zu justieren.

**Frage 8 (Verbesserungen an Geplantem):** `EvaluationResult` als vollständiger Explainability-
Container bleibt die Grundlage; `EngineTick` / `PaperLiveStep` reichen ihn unverändert durch —
jede Signal-Revision und jeder Alert referenziert die Decision, die sie erzeugt hat.

**Leakage/Overfitting:** Alle fünf Module sind rein diffend/orchestrierend, alle Zeitbezüge
laufen über `information_cutoff` bzw. `close_time`. `m1_feed` filtert zusätzlich hart auf
`close_time <= as_of`. Keine gefitteten Konstanten außer den (markierten) Params.

**Frage: die große Datenquellen-Liste (Schritt 11)** — erneut durchgegangen (MTF, Liquidity,
Structure, Price Action, Regime, Volume, Volume Profile, Funding, OI, CVD, Basis, DXY, Yields,
VIX, DVOL, Options IV/Skew/Term, COT, ETF-Flows, On-Chain, Breaking News, Economic Calendar,
FOMC/CPI/PCE/NFP/ECB, institutionelle Research, Microstructure, Order Book). **Ergebnis
unverändert gegenüber Audit 2/6:** nichts davon wird jetzt aufgenommen — es gibt keine
PIT-fähige, backtest-parätätsfähige Historie im Repo. Der `MarketContext` hält die typisierten
Slots `derivatives` / `cross_asset` / `news` bereits vor (Schritt 9); die Confluence bewertet
sie als `UNAVAILABLE`, **nicht** neutral-positiv. Aufnahme-Reihenfolge bei echter Historie:
News/Economic-Calendar (HIGH) → Funding/OI (HIGH) → CVD/Basis (MEDIUM) → DXY/Yields/VIX (MEDIUM)
→ Rest OPTIONAL. Details: `CALIBRATION_BACKLOG.md` + `HISTORICAL_DATA_PLAN.md`.

---

## 6i. Audit 12 — End-to-End-Backtest + ReplayClock-Harness (2026-08-29)

**Kontext:** `engine/replay.py` (`ReplayClock`, `MarketContextAssembler`, `validate_dataset`,
`ReplayHarness`), `engine/backtest.py` (Strategy-Backtest über `PaperLiveRunner`),
`engine/backtest_metrics.py` (erweiterte Kennzahlen + Signal-Analyse). Der MA-Crossover-Pfad
wurde nach `engine/reference_backtest.py` abgetrennt (validiert nur die Execution-Schicht).

**Leakage / Look-ahead:** Der `MarketContextAssembler` slict die (einmal vorgeladenen) Serien
strikt auf `close_time <= cutoff` (`bisect`); der `MarketContext`-Konstruktor wirft bei jeder
Bar/News nach dem `information_cutoff` — doppelter Boden. Test `test_look_ahead_immunity`:
zukünftige Bars grob verfälschen ⇒ `output_hash` unverändert. Höhere TF: Bar erst ab ihrer
`close_time` sichtbar. News/Funding: `available_time`/`ts <= cutoff`.

**Data Snooping:** Der Backtest **optimiert nichts** — er misst nur. `RunManifest` fixiert
Dataset-Fingerprint + Params + `strategy_version` + Commit; `output_hash` über die
entscheidungsrelevanten Trade-Felder. Zwei Läufe mit gleichem Manifest ⇒ bit-gleiche Ergebnisse
(`test_deterministic_output_hash`). Die `research.validation`-Split-/Walk-Forward-/Purge-Embargo-
Infrastruktur steht bereit, wird aber **nicht** zur Parameterwahl benutzt (Phase Kalibrierung).

**Survivorship / Selection Bias:** `DatasetRequirements.instruments` wird **vorab** übergeben,
nicht aus dem Ergebnis abgeleitet. `validate_dataset` meldet Lücken/fehlende Instrumente
**eindeutig** (`DatasetReport.missing`) und `Backtest.run` bricht dann mit
`DatasetIncompleteError` ab — **keine** synthetischen Bars, kein stilles Weglassen.

**Zeitliche Ausrichtung:** ein Replay-Schritt = eine abgeschlossene M5-Bar (`close_time` als
`cutoff`). Warmup-Bars (Default 300) liegen **vor** `start`, gehören zum Kontext, sind aber
nie Teil des Replay-Grids.

**Signalrevisionen:** unverändert über den `SignalTracker` (Audit 11) — jede Neubewertung im
Replay erzeugt genau die Revision, die auch live entstünde. Keine Backtest-Sonderlogik.

**Positions-Simulation / Intrabar / SL-TP-selbe-Bar:** die konservative Worst-Case-Regel (SL vor
TP in derselben Bar) bleibt (Audit 11), bis echte Intrabar-/M1-Daten da sind — dokumentiert und
getestet (`test_position.py::test_worst_case_fill_sl_before_tp`, im Backtest `by_exit_reason`).

**Gefundener + behobener echter Bug:** `ContinuousEvaluator._advance_position` verschlüsselte
`_seen_fill_bar` inkonsistent (Kandidaten-`setup_id` vs. `PaperPosition.position_id` aus der
`Decision`) **und** simulierte eine frisch eröffnete Position beim ersten Tick gegen die
**gesamte Warmup-Historie** im `MarketContext` (statt nur gegen Bars nach der Eröffnung). Bei
den 1–3-Bar-Kontexten der Schritt-5-Tests fiel das nicht auf; im Backtest über echte Serien
sofort. Fix: `position_id`/`signal_id` an die Kandidaten-`setup_id` binden + `_seen_fill_bar`
beim Öffnen auf `now` setzen. Test-Lücke geschlossen (`test_strategy_backtest.py`).

**Verbesserungen:** (1) `MarketContextAssembler` lädt einmal vor und slict per `bisect` —
skaliert auf ≥ 180 T M5. (2) `evaluate_fn`-DI durchgereicht bis `PaperLiveRunner`/`Backtest`
(deterministisches Skript-Replay für Tests, ohne die echte Pipeline zu berühren). (3) `PaperPosition`
trägt jetzt `entry_ts` (Fill-Zeit) + `tp_level_reached`. (4) `ContinuousEvaluator.force_close`
für END_OF_DATA.

**Offen (Backlog):** echter Kosten-/Slippage-Aufschlag im Paper-Sim (aktuell 0 — `gross_r ==
realized_r`); struktur-basierter Trailing-Stop; Intrabar-/M1-Fill-Auflösung; Backtest-vs-Paper-
Parity-Report (`engine/parity.py` verdrahten).

---

## 6j. Audit 13 — echte BTC/ETH-Historie · erster realer Backtest · Kosten · Risk · Adapter (2026-08-29)

**Kontext:** Binance-Vision-Ingest (`data/providers/binance_vision.py`), erster End-to-End-
Backtest über die **echte** Pipeline auf realen M5-Daten, `strategy/costs.py`, `risk/*`,
`safety/kill_switch.py`, `portfolio/engine.py`, Live-Adapter-Verträge (`data/providers/*`).

### Backtest-Ergebnis (2025 H1 BTC/ETH, alle PROPOSED DEFAULTS): **0 Trades**

104 256 M5-Ticks, **jeder** NO_TRADE. Funnel (nach Behebung der u. g. Bugs, per Sampling
n=1086/Symbol, alle 4 h):

| Stufe | BTCUSDT | ETHUSDT | Bemerkung |
|---|---|---|---|
| News-Fail-safe | 100 % | 100 % | ohne PIT-News-Feed blockt V4 jeden Entry — **by design**, Research-Flag nötig |
| Wochenende | 29 % | 29 % | **Bug**: `avoid_weekend=True` galt auch für Krypto (24/7) |
| Regime-Gate `regime_ok` | **0 %** | **0 %** | D1/H4-`directional` = `unclear` ~87 %, `vol=extreme` ~20 % |
| … davon `both_htf_directional` | 0.6 % | 0.8 % | selbst bei voll relaxtem Gate ≤ 0.3 % passierbar |
| Setups / ARMED | 0 / 0 | 0 / 0 | FSM startet erst nach dem Regime-Gate |

**Gegenprobe 2024-Q4 (BTC-Bullrun):** `regime_ok` **4.4 %**, `setups_found` 10, `wait` 12,
Chain-Fails `no_reclaim`/`no_displacement`/`no_structure_shift` — d. h. die Pipeline ist **period-
abhängig**, nicht kaputt. 2025 H1 war schlicht choppy/volatil (Range Jan-Feb, Tariff-Crash
März-April) → eine konservative Swing-Strategie sitzt das aus.

**Leakage / Look-ahead / Data-Snooping / Survivorship:** keine gefunden.
`data_confidence = 1.0` (saubere Daten), `parity.match_rate = 1.0` (vorgeladen ≡ streaming),
`output_hash` deterministisch, Fingerprints im `RunManifest`. Es wurden **keine Parameter
geändert um Trades zu erzwingen** (das wäre Overfitting/Data-Snooping).

**Score/Confidence-Informationswert:** **nicht messbar** mit diesem Datenfenster + Config
(0 Trades). Voraussetzung: das Regime-Gate muss (OOS-kalibriert) überhaupt Trades zulassen.
→ **CALIBRATION_BACKLOG §2.9** (Regime-Gate = neuer Top-Kandidat).

### Gefundene & behobene Bugs

1. **Krypto-Wochenend-Block** (real): `NoTradeParams.avoid_weekend` kannte keine Asset-Klasse.
   Fix: `market_is_24_7`-Flag, in `EvaluateParams.__post_init__` aus `asset_class==CRYPTO` gesetzt.
2. **`_analyze_tf` IndexError** bei zu kurzem Warmup (keine vollständige D1-Kerze): `blist[-1]`
   ungeschützt. Fix: leere Bar-Liste → degradierter `TimeframeContext` (data_confidence 0 ⇒
   sauberes NO_TRADE statt Crash).
3. **Assembler-Perf/Korrektheit**: native höhere TF wuchsen unbegrenzt → jetzt fenster-
   begrenzt (`higher_warmup_bars`), `DatasetRequirements.higher_min_bars` + Tiefen-Check.

### Config-Ergänzungen (kein Param-Tuning)

- `run_backtest.py --news-gate off` — Research-Modus, **laut** protokolliert („NICHT live-
  repräsentativ"), erfindet **keine** News (News = `not_checked`). `require_news_feed` war
  bereits ein Param; hier nur durchgereicht.

### Kostenmodell (`strategy/costs.py`) — Frage: bringt es echten Wert?

Ja — ohne Kostenmodell ist jeder Backtest-Erwartungswert **brutto** und damit optimistisch.
Design: alle Sätze **Default 0.0** (nichts erfunden), bps→R-Umrechnung, Maker/Taker/Spread/
Slippage/Impact/Funding modular. `realized_r` wird netto, `gross_realized_r` bleibt brutto →
`Metrics.cost_drag_r` zeigt die Kostenwirkung. **Kein Tuning** — echte Werte kommen aus
`refdata.FeeSchedule` + gemessener Slippage.

### Risk Engine — Frage: kann Score/Confidence sie überstimmen?

**Nein, strukturell nicht.** `RiskEngine.review(Decision)` → nur `APPROVED` / `REJECTED` /
`PASS_THROUGH`. Eine `WAIT`/`NO_TRADE`-Decision ist **immer** `PASS_THROUGH` (kein Upgrade-Pfad
im Code). Score/Confidence wählen nur das Basis-Risikoband (A+/A/B → 1.00/0.65/0.40 %, hart
gedeckelt `hard_max_risk_pct=2.0`). Alle Limits sind reine Zahlenschranken (Kill-Switch,
Daily/Weekly/DD, Trades/Tag, Loss-Streak, max_open, Opposite/Duplicate, Portfolio-Heat,
korrelierte/Cluster-Exposure). Getestet: „perfekter" Score + DD über Limit → `REJECTED`.

### Live-Adapter — Frage: bleibt die Strategy Engine providerfrei?

**Ja** (verifiziert: `strategy/evaluate.py` importiert nichts broker-spezifisches). Adapter über
`data/providers/adapter_base.LiveDataAdapter` + `data/interfaces`-ABCs. `CredentialSpec`
deklariert nur ENV-Var-**Namen**, hält keine Werte; ohne Keys → `status()` `UNAVAILABLE`, inert.
TradingView bleibt **Interface-Platzhalter** (keine Browser-Automation, Phase 14).

**Offen (Backlog):** Regime-Gate-Kalibrierung (Top); MTF-Analyse-Caching (Backtest ~31 ms/Tick);
echte Fee/Slippage-Werte; Portfolio-Ledger an die Paper-Live-Schleife verdrahten; RiskEngine in
`PaperLiveRunner` einhängen; Adapter-`fetch_*`-Live-Implementierungen (Phase 9+).

---

## 6k. Audit 14 — Regime-OOS-Kalibrierung · Parity · MTF-Caching · Risk-Pfad · Architektur (2026-08-29)

**Kontext:** OOS-Kalibrierung des Regime-Gates, Parity vollständig in Backtest + `PaperLiveRunner`,
MTF-Analyse-Caching, Risk-Limits im Paper-Live-Pfad Ende-zu-Ende, Live-Adapter-Audit,
Architektur-Prep (Multi-Asset / Multi-Agent / 24/7).

### Regime-Gate OOS-Kalibrierung — Ergebnis: **Baseline bleibt** (`docs/REGIME-CALIBRATION-2026-08.md`)

18 912 Samples (BTC+ETH, 2024-06 → 2025-06, Split 2024-12). Das Gate **ist informativ**
(`gate_ok`-Probes: Expectancy **+0.382 R** / PF 1.71 vs. Bias-only +0.008 R / PF 1.01), aber die
Abdeckung ist 1.0 % IS / **0.0 % OOS** und stark period-abhängig. **Jede** Lockerung (V1–V5, inkl.
„M15-Vol nicht hart blockend") zieht die Expectancy zurück auf Bias-only-Niveau und ist **OOS
netto verlierend** (−0.03 bis −0.11 R). Walk-Forward: nur 1/4 Test-Folds positiv, Fold 2 für alle
Varianten negativ. → **Kein Default geändert.** `context_vol_is_hard_block` bleibt `True`.
`RegimeGateParams` ist jetzt über `MtfParams.regime_gate` konfigurierbar (Architektur, keine
Verhaltensänderung). Der erste Lauf hatte einen Bug (Probe-Richtung ~98 % `None`) — mit
korrigierter Richtung neu gefahren.

### 7-Fragen-Audit

| Frage | Befund |
|---|---|
| **1 Fehlt ein Marktdaten-Faktor?** | Nein — die Analyse zeigt: der Engpass ist **Abdeckung**, nicht ein fehlender Indikator. Echte Hebel = mehr Instrumente (SOL, weitere Coins, XAUUSD) + niedrige Trade-Frequenz akzeptieren. Kein neuer Faktor aufgenommen. |
| **2 Redundanz?** | `BacktestResult.parity_summary()` nutzt jetzt `engine.parity.render_parity` (ein Renderer statt zwei). Keine weitere Redundanz. |
| **3 Leakage / Look-ahead?** | Neu gegen Caching geprüft: MTF-Cache-Key kodiert Bar-Fenster-Identität + Freshness-Bucket (`cutoff // tf.seconds`) + `d1_key` + Session-Fenster-Anzahl; **kein Caching bei stale letzter Bar** (Alter > 1 Periode). Test `test_mtf_cache_does_not_change_decisions` (bit-identische Decisions mit/ohne Cache). Parity-`match_rate` bleibt 1.0. |
| **4 Data-Snooping?** | Bewusst vermieden: keine Variante adoptiert, obwohl V1–V5 IS besser aussehen — **weil OOS negativ**. Genau der Fall, für den das OOS-Gate da ist. |
| **5 Overfitting-Risiko?** | Gering — es wurde **nichts** an die Daten angepasst. Die einzige Code-Änderung (Konfigurierbarkeit) ändert das Default-Verhalten nicht. |
| **6 Sind die Thresholds sinnvoll?** | Regime-Gate: ja, mit der Einschränkung „niedrige Abdeckung ist hier schützend, nicht kostspielig". Risk-Limits: Ende-zu-Ende im Paper-Live-Pfad verifiziert (s. u.). |
| **7 Ist der Score informativ?** | Weiterhin **nicht direkt messbar** (Baseline erzeugt in den realen Fenstern ~0 Trades). Der **Forward-Probe-Proxy** zeigt aber: die HTF-Bias-Richtung trägt Information (trend_up +0.13 R vs. unclear −0.01 R). Voll messbar erst mit mehr Instrumenten. |
| **8 Fehlt eine Datenquelle?** | Nein neu. Bestätigt: PIT-News-Feed (blockt sonst 100 %, by design) + mehr Instrumente sind die offenen Punkte — beide schon im Backlog. |

### Parity — vollständig integriert

- **Backtest:** `BacktestConfig.parity_check` → `run_parity` (vorgeladen ≡ streaming), `ParityReport`
  am Ergebnis, `render_parity()` als menschenlesbarer Diff.
- **PaperLiveRunner:** `decision_trace` (jede `feed()` → `(cutoff, instrument, Decision)`) +
  `parity_against(reference)` → `compare_decisions`. Erlaubt den Diff zweier Läufe (Replay ↔
  neu-eingespielte Folge). Test: zwei Runner, gleiche MC-Folge → `match_rate == 1.0`.

### MTF-Caching — Performance ohne Korrektheitsverlust

`build_mtf_context(analysis_cache=…)` memoisiert M15/H4/D1-Analysen; `ContinuousEvaluator` hält
den Cache über Ticks. Messung (200 Ticks, echte Pipeline): **27.2 → 16.9 ms/Tick (1.6×)**. M5 nie
gecacht; 4096-Eintrag-Deckel. Korrektheit per Parity-Test abgesichert.

### Risk-Limits im Paper-Live-Pfad — Ende-zu-Ende verifiziert

Neue Tests (`test_paper_live_risk.py`, jetzt 8): Kill-Switch, Daily-Loss, **Max-Drawdown**
(peak-to-trough, nach Tages-Reset), **Loss-Streak**, **Max-Open-Positions**, und explizit
**Score/Confidence hebt ein hartes Limit NICHT auf** (Score 100 + A+-Tier + DD über Limit →
`REJECTED`, `opened is None`). Bekannte Grenze: Leverage/Margin-Checks sind inert, solange der
Paper-Ledger kein `available_margin` liefert (kein Fake) — dokumentiert.

### Live-Adapter-Audit

13-Punkte-Checkliste in `LIVE-DATA-ADAPTERS.md §9`. Ergebnis: Interfaces / Datenmodelle /
Symbol-Mapping / PIT / Stale / Quality / Fehlerbehandlung / Rate-Limits / ENV-only / keine
Secrets = **✅ vorhanden & getestet**. WS-Reconnect + WS-Lifecycle = **◑ Gerüst** (Phase 9+).
Keine Fake-Live-Daten (Stubs werfen `NotImplementedError`).

### Architektur-Prep (Design-only)

`docs/ARCHITECTURE-MULTI-ASSET-AGENT-CLOUD.md`: Multi-Asset (`Instrument`-Metadaten +
`AssetProfile`-Prep, kein Strategie-Fork), Multi-Agent (`agents/` — Informations-Agenten →
zentrale Engine entscheidet, kein Agent handelt, harte Vetos bleiben hart, Risk sieht keine
Agenten-Confidence), 24/7 (`runtime/` — Bus + Supervisor + read-only API, kein Order-Endpunkt).

### Aufgenommen / Backlog

- **CRITICAL:** keine.
- **HIGH (integriert):** Parity in PaperLiveRunner · MTF-Caching · Risk-Limit-E2E-Tests ·
  `RegimeGateParams` konfigurierbar.
- **MEDIUM/OPTIONAL (Backlog):** MTF-Cache-Eviction statt Full-Clear bei Langläufen ·
  `EvaluateParams.for_instrument()`-Factory · `AssetProfile` je Assetklasse · WS
  Heartbeat/Resubscribe-Test · Struktur-Klassifikator (`derive_structure_state`) isoliert gegen
  vollen Marktzyklus kalibrieren (separat vom Gate).

---

## 6l. Audit 15 — News/Macro-Analyse · Multi-Asset-Prep · Corporate Actions · Margin (2026-08-29)

**Kontext:** Nutzer-Arbeitsblock „professionelle Multi-Asset-Plattform". Ziel: Entry-/Exit-
Qualität, **kein** Trade-Count-Optimieren, keine Fake-Daten, keine blind getunten Parameter.

### Was implementiert wurde (mit Tests, ruff+mypy strict grün — 874 Tests)

| Modul | vorher | jetzt |
|---|---|---|
| `analysis/news.py` | 4-Zeilen-Stub | `assess_news`/`build_news_context` — PIT-Filter, asset-spezifische Relevanz (`news_relevance`), Blackout / Pre-Positioning / risk_off → `NewsContext`. Deterministisch, look-ahead-frei. Verdrahtet in `MarketContextAssembler` (`AssemblerConfig.asset_class`). |
| `analysis/macro.py` | 4-Zeilen-Stub | `assess_macro` → `MacroContext` (rate_cycle / inflation_trend / growth_trend / risk_sentiment) aus FRED-`MacroEvent`-Vintages, PIT über `available_time`, `UNKNOWN` statt Fake. Evidence-Strings. Neue Enums `MacroTrend`/`MacroRateCycle`/`MacroRiskSentiment`. |
| `refdata/corporate_actions.py` | 4-Zeilen-Stub | `adjust_ohlcv` (PIT Split/Div-Backadjust via `available_time`), `CorporateActionBook`, `resolve_symbol_at` (SYMBOL_CHANGE-Ketten), `is_delisted`. |
| `risk/margin.py` | 4-Zeilen-Stub | isolated-linear `liquidation_price` / `estimate_liquidation` / `max_leverage_for_liq_distance`. In `position_sizing.py` verdrahtet: neue `SizingInputs.side` + `maintenance_margin_rate` (**Default 0.0 ⇒ identisch** zur bisherigen `entry/leverage`-Heuristik; `mmr>0` = korrekter/konservativer). |

### Frage 8 (Verbesserung an Geplantem) — was NICHT gemacht wurde

- **News erzwingt nie einen Trade.** `analysis/news` kann nur blockieren (Blackout/risk_off);
  es erzeugt keine Richtung. risk_off nur aus **explizit** geopolitischen/Notfall-Event-Typen —
  ein großer Makro-Surprise allein setzt es **nicht** (nur Evidence). Konservativ.
- **Kein Runtime-Verhalten für Crypto geändert.** `asset_class` default CRYPTO überall;
  `news_feed_available` default `False` ⇒ Assembler liefert weiter den Fail-safe-`NewsContext`.
- **`position_sizing` Liquidations-Check:** nur Formel-Refactor bei `mmr=0` (beweisbar identisch),
  kein Default-Tuning.
- **Multi-Agent:** `agents/roles.py` bleibt Vertrag-only (Nutzer-Vorgabe „noch keine
  konkurrierenden Trading-Gehirne"). `NewsMacroAgent`/`MarketAgent` sind jetzt auf den neuen
  Modulen implementierbar — Backlog.

### Architektur-Prep (Design + kleine additive Verdrahtung)

- `docs/MULTI-ASSET-READINESS.md` — Stocks/ETF · Gold · FX: Provider-Bewertung (Empfehlung
  **Polygon.io** für Aktien, **Dukascopy-Bulk** für Gold+FX), `asset_class`-Verdrahtung,
  Session-/Kalender-/Corporate-Action-/Survivorship-Handling, offene Nutzer-Entscheidungen.
- `BacktestConfig.asset_class` → Assembler + `EvaluateParams`; `EvaluateParams.__post_init__`
  behandelt jetzt **ALTCOIN wie CRYPTO** für das 24/7-Flag (Korrektur).
- `scripts/run_backtest.py`: `--asset-class`, `--cost-profile {zero,estimate}` (nutzt
  `cost_profiles.estimate_profile`; Report weist **brutto/netto/cost_drag** getrennt aus).

### Multi-Symbol-Backtest — Ergebnis: **0 Trades über alle 6 Symbole** (`docs/MULTI-SYMBOL-BACKTEST-2026-08.md`)

6 Krypto-Symbole (BTC/ETH/SOL/BNB/XRP/DOGE) × voller Strategiepfad, 2023-08 → 2025-06
(201 312 M5-Ticks/Symbol), Research-Modus (News-Gate aus), deterministisch (`output_hash` für
alle 6 identisch = leere Trade-Liste).

- **0 Trades.** 1 205 944 NO_TRADE / 1 928 WAIT. Gründe: `regime_unclear` 66 %,
  `regime_vol_extreme` 33 % (skaliert mit Asset-Vol: SOL 45 %, DOGE 48 %, BTC 24 %).
- Signale entstehen auf WATCH-Level (BTC 1782), aber **keiner** kommt bis ARMED+Fill —
  der HTF-Regime-Gate blockt davor.
- **„Mehr Instrumente" (Krypto) ist widerlegt als Hebel:** 4 zusätzliche Coins → 0 zusätzliche
  Trades; die hoch-volatilen Alts werden **häufiger** geblockt. Kein Bug (dataset_ok,
  deterministisch, PIT). Punkt 11/12: nichts zu analysieren (keine Entries); Punkt 12
  Nutzer-gated (keine Daten → nicht optimieren).
- **Keine Parameteränderung** (Nutzer-Vorgabe + Regime-Kalibrierungs-Baseline).

### Nebenbefund + Fix: `validate_dataset` Kontinuitäts-Check

2023-03-24 hat 272/288 M5-Bars für **alle** Symbole (~80 min echter Binance-Ausfall). Native
D1-Ingest + Resampler lassen den unvollständigen Tag weg ⇒ 1-Tages-D1-Loch ⇒ `data_gap_recent`
NO_TRADE über ~70 Tage (via D1-200-Warmup-Fenster; wirkungslos — regime-geblockt). **Fix:**
`DatasetRequirements.check_continuity` (Default `True`) — scannt M5 + native höhere TFs auf
interne Lücken, meldet als `notes` (kein harter Abbruch). +2 Tests.

### Aufgenommen / Backlog

- **CRITICAL:** keine.
- **MEDIUM:** `NewsMacroAgent`/`MarketAgent` auf `analysis/news`+`analysis/macro`+`build_mtf_context`
  implementieren · CA-Book im Assembler für EQUITY/ETF verdrahten · `data/providers/dukascopy.py`
  (Gold+FX Bulk) · Bybit-Funding/OI-Ingest für `DerivativesContext`.
- **Offene Nutzer-Entscheidungen:** Aktien-Provider (Polygon vs. Databento) · Dividenden im
  Backtest glätten vs. als Event · Gold/FX-Historie via Dukascopy vs. auf MT5-Live warten.

---

## 6t. Audit 23 — XAUUSDT-Ingest + erster Gold-Backtest (2026-08-31)

**Kontext:** Nutzer-Auftrag „XAUUSDT-Historie ingestieren (M5-Basis, M15/H4/D1 ableiten,
Quality/Replay-Validierung, Manifest, kein Look-ahead), bestehende Engine, dann erster echter
Gold-Backtest". Details: `docs/GOLD-BACKTEST-XAUUSDT-2026-08.md`.

### Ergebnis

- **`scripts/ingest_binance_futures.py`** — paginierter `fapi/v1/klines`-Ingest (1500/Request,
  kein Key) über `BinancePublicDataProvider`. M5 = Basis; M15/H4/D1 aus M5 (`require_complete`,
  PIT) + nativ. Forming-Bar verworfen. Quality-Check gegen echte Uhr. Manifest mit
  `recommended_backtest_window`.
- **XAUUSDT existiert erst seit 2025-12-11** (`TRADIFI_PERPETUAL`) — ~9 Monate, **keine 2 Jahre**.
  Ingest: **75 630 M5-Bars** (2025-12-11 → 2026-08-30), M15 25 209 / H4 1 574 / D1 261,
  **0 interne Lücken, 100 % vollständig, 0 Quality-Issues**. In `data/repository_real/`,
  Fingerprint gesetzt.
- **`scripts/run_backtest.py` +`--require-native-higher {on,off}`** (begründet: junger Perp hat
  keine 200 D1-Bars *vor* Listing; `off` nutzt native TFs wo vorhanden + leitet Rest PIT-sauber
  aus M5 ab. `read_native_higher` bleibt an. **Keine Analyse-Parameter geändert.**).
- **Backtest** XAUUSDT/gold, 2026-02-01 → 2026-08-30 (60 480 M5-Bars), news-gate off (Research),
  cost estimate. **`run_id=0ce5b18899ebf6e4`, dataset_ok=True.**
  - **0 Trades.** 60 355 NO_TRADE + 125 WAIT + 0 BUY/SELL.
  - **605 Signale erzeugt** (mehr FSM-Aktivität als bei Crypto!), 573 invalidiert, 28 expired.
    Vetos: V3 273×, V5 506×.
  - No-Trade: `regime_unclear` 82,8 % (wie Crypto), `data_incomplete` 31,9 % (Frühfenster-Artefakt
    des jungen Instruments), `regime_vol_extreme` 15,4 %, `data_confidence_floor` 4,3 %.
  - Winrate/PF/Expectancy/MaxDD/Ø R: **n/a — keine Trades.** Score/Confidence-Informationswert
    weiter nicht messbar.
- **Befund:** „Gold = anderes Vol-Regime" als Hebel ist im 9-Monats-Fenster **widerlegt** —
  Engpass bleibt der Regime-/Struktur-Klassifikator (`regime_unclear` bei Gold-D1/H4). Deckt sich
  mit Audit 14 (H4 `unclear` 93 %) + Audit 15 (Multi-Symbol 0 Trades).
- **Nächste Hebel:** Dukascopy XAUUSD Spot 2y · H4-Struktur-Klassifikator isoliert kalibrieren ·
  späterer Backtest-Start (2026-05) für sauberes Regime-Bild ohne DATA-Gate-Anteil.

**969 Tests grün (unverändert — Ingest-Script + Flag nicht unit-getestet). ruff + ruff-format +
mypy --strict grün.**

---

## 6s. Audit 22 — Binance READ-ONLY (Marktdaten + Account) + XAUUSDT (2026-08-30)

**Kontext:** Nutzer-Auftrag „Binance verbinden, READ-ONLY, prüfen ob XAUUSDT verfügbar ist
(Live/Historie/M1..D1/Mark/Funding/OI) und durch MarketContext + Analyse-Engine läuft".
Details: `docs/BINANCE-INTEGRATION-2026-08.md`.

### Ergebnis

- Vorhandener `binance_vision.py` = **Bulk-Datei-Import** (Historie), **kein** REST-/WS-API-Adapter.
  → beides neu, `binance_vision` unverändert.
- **`data/providers/binance.py`** — `BinancePublicDataProvider` (kein Key), `market="spot"` /
  `"futures_usdm"`. OHLCV / bookTicker-Quote / 24h-Ticker / Mark Price / Funding-Historie /
  Open-Interest-Historie / `list_symbols`. Futures-only-Guards auf Spot.
- **`data/providers/binance_account.py`** — `BinanceAccountAdapter` READ-ONLY (HMAC-SHA256,
  Signierer gegen Binance-Testvektor), `assert_read_only()` via `/sapi/v1/account/apiRestrictions`
  (bestätigt nur wenn weder Withdraw noch Transfer aktiv; meldet Trading-Flags). **Kein
  submit/cancel**, Pfad-Whitelist. Ohne ENV → UNAVAILABLE.
- **`BinanceWSSource`** (`@aggTrade` → BarAggregator). `build_rest_provider` +
  `_maybe_refresh_derivatives` + `_new_ws` kennen Binance.
- **refdata:** `XAUUSDT`-Instrument (Binance/GOLD/Perp, Kalender `xau_spot`) + Symbol-Mappings.
- **XAUUSDT geklärt:** Spot NEIN (`PAXGUSDT` stattdessen), **USD-M-Futures JA** (`TRADIFI_PERPETUAL`,
  aktiv). Live Bid/Ask, M1..D1-Klines, Mark Price, Funding (Rate 0.0 — TradiFi-Klasse), OI —
  alle real verifiziert.
- **Live-Durchstich:** Binance → XAUUSDT → LivePipeline (warmup M5/M15/H4/D1 voll) →
  MarketContext → `evaluate()` = NO_TRADE (regime_vol_extreme + weekend), Derivatives-Kontext
  (funding 0.0, OI 90k) befüllt, `orders_sent=0`.
- `scripts/binance_market_test.py` (public) + `scripts/binance_account_test.py` (READ-ONLY).
  `.env`/`.env.example` um `BINANCE_API_KEY`/`BINANCE_API_SECRET` ergänzt.

**+17 Tests (952→969). ruff + ruff-format + mypy --strict grün (177 Dateien).**
Kraken/Bybit unverändert, cTrader weiter pausiert. **Wartet auf den Binance-Key** für den
Account-Test (public Marktdaten laufen bereits).

---

## 6r. Audit 21 — cTrader / Pepperstone READ-ONLY-Anbindung (2026-08-30)

**Kontext:** Nutzer-Auftrag „Pepperstone-Account als READ-ONLY/PAPER, cTrader Open API prüfen,
Schritt-für-Schritt-Verknüpfung". Ziel FX+XAUUSD → LivePipeline → Paper. Details:
`docs/CTRADER-PEPPERSTONE-READONLY.md`.

### Ergebnis

- **`data/providers/ctrader.py`** von Vertrag auf echte Implementierung:
  - OAuth2: `authorize_url` (Scope **`accounts`** = nur lesen), `exchange_code`,
    `refresh_access_token` — Token-Endpoint `https://openapi.ctrader.com/apps/token`.
  - `CTraderClient` — JSON-über-WebSocket (`wss://{demo|live}.ctraderapi.com:5036`),
    Envelope `{clientMsgId,payloadType,payload}`, `clientMsgId`-Korrelation, Heartbeat-Loop,
    App-Auth (2100) → Account-Auth (2102) → `symbols()` (2114) / `get_trendbars()` (2137) /
    `spot_snapshot()` (2127 + 2131). Preise ×100 000, Trendbar `low`+Deltas.
    **Kein Order-Nachrichtentyp im Modul** (per Test).
  - `CTraderAdapter` (READ-ONLY) — `fetch_ohlcv` / `fetch_quote`. **Kein `submit`/`cancel`**,
    kein `BrokerAdapter`. Ohne ENV → `UNAVAILABLE`.
- **`scripts/ctrader_link.py`** — OAuth-Flow lokal, schreibt Tokens nach `.env` (Tokens nie
  am Bildschirm). **`scripts/ctrader_account_test.py`** — Connectivity-Test XAUUSD/EURUSD/
  GBPUSD/USDJPY (Symbol-Auflösung, Trendbars, Spot-Snapshot, `orders_sent=0`).
- Endpunkte aus dieser Umgebung **erreichbar** (WS-Handshake demo+live OK, Token-Endpoint HTTP 200).
- `.env`/`.env.example` um `CTRADER_*` ergänzt. Alte Vertrags-Tests aus
  `test_gold_fx_providers.py` → nach `test_ctrader.py` (11 Tests).

**Netto +8 Tests (944→952). ruff + ruff-format + mypy --strict grün (175 Dateien).**
Kraken + Bybit unverändert. **Wartet auf die OAuth-Verknüpfung durch den Nutzer**, dann
Connectivity-Test, dann Pipeline-Verdrahtung.

---

## 6q. Audit 20 — Bybit Account READ-ONLY-Anbindung (2026-08-30)

**Kontext:** Nutzer-Auftrag „weiter mit Bybit, gleiches Sicherheitsprinzip wie Kraken,
READ-ONLY ONLY". Details: `docs/BYBIT-ACCOUNT-READONLY.md`.

### Ergebnis

- **`data/providers/bybit_account.py`** neu:
  - `sign_v5` — Bybit-v5 HMAC-SHA256 (`hex(HMAC(secret, ts+key+recv+payload))`), gegen einen
    gepinnten Vektor + unabhängige Neuberechnung getestet.
  - `BybitPrivateClient` — signierter GET, Pfad-Whitelist (`_ALLOWED_PATHS`, nur lesend),
    Retry mit frischem Timestamp bei `retCode 10002`, Fehler-Klassifikation
    (`BybitAuthError` vs `BybitAPIError`).
  - `BybitAccountAdapter` — **kein `BrokerAdapter`**, **kein `submit`/`cancel`** (per Test).
    `get_api_key_info` / `get_wallet_balance` / `get_open_orders` / `get_positions` /
    `get_transaction_log` / `server_time`.
    `assert_read_only()` liest `GET /v5/user/query-api` und bestätigt nur, wenn weder eine
    Trade-Permission-Gruppe belegt ist noch `Wallet` ein `Withdraw` enthält (Bybits eigene
    Rechte-Introspektion — kein Dry-Run-Order nötig).
    Ohne ENV → `status()==UNAVAILABLE`.
- **`scripts/bybit_account_test.py`** — READ-ONLY Test (ENV → Serverzeit → query-api →
  Wallet maskiert → OpenOrders/Positions=0 → Transaction-Log → Read-only-Assertion →
  `orders_sent=0`).
- `.env.example` + `.env` um `BYBIT_API_KEY`/`BYBIT_API_SECRET` (Platzhalter) ergänzt.

**+16 Tests (928→944). ruff + ruff-format + mypy --strict grün (175 Dateien).** Kraken unverändert.

### Nachtrag — Bybit EU

Nutzer-Konto ist **Bybit EU**. Offizieller Host laut Bybit-Docs:
**`https://api.bybit.eu`** (ein `bybit.eu`-Key wird von `api.bybit.com` mit `retCode 10003`
abgelehnt). Adapter fest auf `api.bybit.eu` gestellt (`_BASE_URL`), Demo-/Testnet-Optionen im
Script entfernt.
`assert_read_only()` angepasst: Bybit listet bei einem „Read-Only"-Key weiterhin
`Spot:["SpotTrade"]` / `Derivatives:["DerivativesTrade"]` als **Lese-Domänen**, setzt aber
`readOnly=1` (bindend). Bestätigung nun: `readOnly==1` **und** kein `Withdraw` → confirmed;
`readOnly==0` **mit** Trade-Gruppe → Fehler.
**Live-Test CONNECTED:** `readOnly=1`, `can_withdraw=False`, 0 Orders/0 Positionen, Wallet +
Transaktions-Log lesbar, `orders_sent=0`. Hinweise an den Nutzer: Key-IP-Allowlist `['*']`
(offen), Key-Ablauf 2026-11-30 (Bybit-EU-3-Monats-Limit).

---

## 6p. Audit 19 — Kraken Account READ-ONLY-Anbindung + Secrets (2026-08-30)

**Kontext:** Nutzer-Auftrag „sichere Account-Anbindung, beginne mit Kraken, strikt READ-ONLY,
keine Order-/Withdraw-Rechte, keine Secrets im Repo". Details: `docs/KRAKEN-ACCOUNT-READONLY.md`.

### Ergebnis

- **`security/secrets.py`** von Stub auf real: `Secret` (redigiert `repr`/`str`, Klartext nur
  `.reveal()`), `get_secret` (ENV → macOS-Keychain via `security find-generic-password`),
  `missing_secrets`, `redact`.
- **`data/providers/kraken_account.py`** neu:
  - `sign_request` — Kraken HMAC-SHA512, **gegen Krakens offiziellen Testvektor verifiziert**.
  - `KrakenPrivateClient` — signierter POST `/0/private/*`, streng monotone Nonce (µs + Guard),
    Retry mit **neuer** Nonce bei `EAPI:Invalid nonce`, Fehler-Klassifikation
    (`KrakenAuthError` vs `KrakenAPIError`), Methoden-Whitelist (`_ALLOWED_METHODS` +
    `AddOrder` nur für die Assertion).
  - `KrakenAccountAdapter` — **kein `BrokerAdapter`**, **kein `submit`/`cancel`** (per Test).
    `get_balances`/`get_trade_balance`/`get_open_orders`/`get_open_positions`/`get_ledgers`,
    `server_time` (+Skew), `assert_read_only()` (ruft `AddOrder validate=true` — platziert nie
    etwas — erwartet `EGeneral:Permission denied`; Erfolg ⇒ `KrakenAccountError`).
    Ohne ENV → `status()==UNAVAILABLE`, Calls werfen `KrakenAuthError`.
- **`scripts/kraken_account_test.py`** — READ-ONLY Verbindungstest (ENV-Check → Serverzeit →
  Balance/TradeBalance maskiert → OpenOrders/Positions=0 → Ledger → Read-only-Assertion →
  `orders_sent=0`).
- `.env.example` um `KRAKEN_API_KEY`/`KRAKEN_API_SECRET` (nur Platzhalter-Namen + Permission-
  Liste) ergänzt.
- Secrets lecken nirgends: `Secret` redigiert sich, Fehlertexte durch `redact()` (per Test
  geprüft, auch wenn Kraken das Secret zurück-echot).

**+16 Tests (912→928). ruff + ruff-format + mypy --strict grün (174 Dateien).**
Noch keine Credentials angefordert. Noch keine Orderausführung.

---

## 6o. Audit 18 — Gold/FX READ-ONLY/Paper + Pepperstone/MT5-Bewertung (2026-08-30)

**Kontext:** Nutzer-Auftrag „MT5 / Pepperstone + XAUUSD / FX READ-ONLY PAPER". Bewerten:
Pepperstone API / cTrader API / Pepperstone Web / MT5 — beste 24/7-Cloud-Variante ohne
Windows-Abhängigkeit. XAUUSD + FX read-only/paper vorbereiten. Details:
`docs/GOLD-FX-DATA-SOURCES.md` (Bewertung) + `docs/GOLD-FX-INTEGRATION-2026-08.md`.

### Ergebnis

- **Bewertung:** „Pepperstone API" gibt es nicht eigenständig — Pepperstone = MT4/MT5/cTrader.
  **Empfehlung: cTrader Open API** (Protobuf-TCP:5035 / JSON-WS:5036, cloud-tauglich, kein
  Terminal, Pepperstone-Demo genügt, Marktdaten brauchen kein Trading-Recht). OANDA v20 Practice
  als gleichwertiges Backup. „Pepperstone Web" = keine API → verworfen. **MT5 = reiner
  Windows-Fallback**, gekapselt (`mt5.py` unverändert, `platform_only="windows"`); die zentrale
  Engine importiert MT5 nie (verifiziert per grep).
- **Neue Adapter:** `data/providers/ctrader.py` (Vertrag, ENV `CTRADER_*`, ohne Creds
  UNAVAILABLE + `CTraderUnavailable`, kein Fake), `dukascopy.py` (**echter** `.bi5`-Decoder:
  LZMA + 20-Byte `>IIIff`, Tick→Mid-M5 + `BarSpread`; **live verifiziert** aus dieser Umgebung),
  `yahoo_finance.py` (keyless OHLCV, `source="yahoo_indicative"`, kein Bid/Ask, klar als
  nicht-handelsqualitätstauglich markiert).
- **refdata additiv:** `Instrument.pip_size/swap_long_points/swap_short_points/swap_basis`;
  `TradingCalendarSpec.daily_break_start/end` (in `is_open` honoriert); Seed: GBPUSD + USDJPY
  neu, `xau_spot` CME-Pause 21:00–22:00 UTC, `XAUUSD→bybit XAUTUSDT / kraken XAUT/USD` +
  oanda/ctrader/dukascopy/yahoo-Mappings.
- **Pipeline:** `LivePipelineConfig.session_specs` (additiv) → `runner.feed(session_specs=)`;
  `run_live_paper.py` setzt für Nicht-Crypto automatisch `seed_sessions()`. **Kein neuer
  Entscheidungspfad** — XAUUSD/FX nutzen `SMC-SWEEP-REV-01 v0.1.1` identisch zu Crypto.
- **Historie:** `scripts/ingest_dukascopy.py` (analog `ingest_binance_vision.py`) — Manifest mit
  Quelle/PIT-Konvention/OHLCV-Definition/Point-Faktoren/Spread/Fingerprint. Probelauf (2 T,
  XAUUSD + EURUSD): 552/576 M5-Bars, mittl. Spread 0.39 USD / 0.24 pip, Quality clean. Die
  Continuity-Prüfung meldete korrekt die 12-Bar-CME-Pause.

### Live-Test (real, read-only, Samstag ~00:25 UTC)

- **XAUUSD via Bybit `XAUTUSDT` (Tokengold): LIVE** — Bid/Ask 4455.3/4455.4, Spread 0.1,
  Data-Age 0.2 s, WS verbindet. Warmup M5=401/M15=451/H4=301/D1=221.
- Paper: prime + 1 WS-M5-Bar → **2× NO_TRADE** (Reason: `REGIME_UNCLEAR` + `WEEKEND` +
  `SPREAD_TOO_WIDE`). `orders_sent=0`, `ws_restarts=0`, `quality_blocks=0`. NO_TRADE ist das
  korrekte Ergebnis — kein Setup künstlich erzeugt. Der Wochenend-Filter (aus `session_specs`)
  greift, der Regime-Gate greift wie bei Crypto.
- **FX (EUR/GBP/JPY) live: BLOCKED** auf cTrader/OANDA-Credentials. Yahoo nur indikativ
  (Wochenende → Freitag-Close, **nichts interpoliert**). cTrader `status()==NOT_AVAILABLE`.

**+12 Tests (900→912). ruff + ruff-format + mypy --strict grün.**

---

## 6n. Audit 17 — M-01: 24/7-Supervisor + rollierendes Resample + Bybit-Derivatives (2026-08-30)

**Kontext:** Nutzer-Auftrag M-01 — `runtime/supervisor.py` von der bounded ScannerShell auf die
echte `LivePipeline` umstellen, **dauerhaft 24/7**, mit Recovery/Reconnect/Health/Fehler-Isolation
und Cloud-Vorbereitung. Details: `docs/M01-24-7-SUPERVISOR.md`.

### Ergebnis

- **`LiveSupervisor`** (neu, neben dem Phase-2B-`Supervisor`): besitzt eine `LivePipeline`,
  fährt sie bis SIGTERM/SIGINT/`request_stop()`. Recovery aus atomarem Snapshot (offene
  Paper-Positionen wieder eingehängt, `_last_open`/`_fed_opens`/Zähler geseedet, Gap per REST
  gebackfillt), zweistufiger WS-Auto-Reconnect (`_WSBase` intern + Supervisor-Neustart der
  Task), stale-Detection + REST-Backfill, Watchdog → `SystemHealth`
  (HEALTHY/DEGRADED/UNAVAILABLE, **kein** Kill-Switch im Paper-Modus), Fehler-Isolation je
  Instrument/Task, sauberer Shutdown mit finalem Snapshot. Alles **Wall-Clock** (Sleep-fest).
- **`state/store.py` + `state/recovery.py`** von Stub auf voll: `SnapshotStore` (atomar via
  `os.replace`, `schema_version`, kaputt/alt ⇒ **verworfen**, kein Halb-Laden), `PaperPosition`
  verlustfreier JSON-Round-Trip, Gap-Rechnung + REST-History-Clamp (~700 M5-Bars).
- **Follow-up 1 — rollierendes Resample:** M15 aus dem M5-Puffer (`m5_store_bars=1600`,
  kein REST); H4/D1 REST bei 4h/1d-Kadenz statt „alles stündlich". REST-Last höhere TFs ~12×
  runter, kein Datenverlust.
- **Follow-up 2 — Bybit Funding/OI:** `_maybe_refresh_derivatives` (Bybit-only, `--derivatives`)
  füllt `DerivativesContext` **nur bei validen PIT-Daten**; echte Endpunkte verifiziert
  (funding 6/2d, OI 24/1d). Leere Antwort ⇒ Kontext bleibt leer (kein Fake).
- `PipelineHealth`/`InstrumentHealth` (typisiert). `scripts/run_live_daemon.py`.
- **`orders_sent` = 0** in jedem Pfad asserted. Kein Broker, kein Order-Code.

### Beim Verifizieren gefunden & behoben (Recovery-Pfad)

1. **M15-Regression:** das rollierende Resample überschrieb die per REST geholte M15-Historie
   mit einem flachen ~134-Bar-Resample (M5-Puffer erst nach ~5.5 Tagen tief genug). **Fix:**
   Warmup füllt M15 per REST; danach wird das Resample-Segment mit der bestehenden Historie
   **verschmolzen** (Kontinuitäts-Check an der Grenze). M15 bleibt ~450 Bars.
2. **Doppelter cutoff nach Backfill:** der Prime-Durchlauf nach Recovery lag auf demselben
   cutoff wie die letzte gebackfillte Bar → zweites `DecisionMade`-Event. **Fix:** `_last_fed_cutoff`
   je Instrument — genau **ein** `feed()` je cutoff.
3. **Backfill sah eine leere Lücke:** `warmup()` schob `_last_open` vor `_backfill_gap()`. **Fix:**
   `warmup(preserve_last_open=True)` im Recovery-Pfad — Puffer nur bis zum Recovery-Stand füllen,
   die Lücke bleibt für den Bar-für-Bar-Backfill.
4. **`backfill()`-Grenze zu konservativ:** `close_time <= now − 1 Intervall` schloss die *gerade*
   abgeschlossene Bar aus (bei 1-Bar-Lücken ⇒ 0 gefeedet). **Fix:** `close_time <= now` (die
   noch formende REST-Zeile hat `close_time > now`). `_backfill_gap` loggt jetzt gap + tatsächlich
   gebackfillte Bars.

### Fragen 7/8

- **Keine Strategie-/Risk-Änderung.** M-01 ist Orchestrierung. `PaperLiveRunner` unverändert.
- **Signal-Historie über Restart:** bewusst **nicht** persistiert (nur offene Positionen +
  `_fed_opens` + `_last_fed_cutoff`). Nach Neustart leitet der `SignalTracker` neu ab —
  konsistent, nur kürzere Revisions-Historie. Dokumentiert.
- **Datenverlust bei sehr großer Lücke** (> REST-Verlauf ~2–3 Tage): Teil-Backfill + Log,
  unvermeidbar ohne tiefere öffentliche Historie. Ehrlich gemeldet, nicht gefaked.

### 18-min-Live-Test (echt, `run_live_daemon.py`, beide Exchanges)

Bybit + Kraken, BTC+ETH: Uptime 1085/1090 s, 8 `DecisionMade` je Exchange (alle
`NO_TRADE/SCANNING` — Regime-Gate, wie Backtest), **0** feed_errors / dup_bars / qblocks /
ws_restarts, `any_stale=False`, 25 Snapshots, Provider-Health `healthy`, **`orders_sent`=0**
(4 Prüfstellen). Restart-/Recovery-Test: Snapshot geladen, `_recovered=True`, Gap erkannt +
gebackfillt, kein cutoff doppelt, offene Positionen (0) korrekt übernommen.

**+15 Tests (test_state 8, test_live_supervisor 6, +1 Gap-Backfill). 900 grün, ruff +
ruff-format + mypy --strict grün.**

---

## 6m. Audit 16 — Live-Data-Integration Kraken + Bybit (read-only public) (2026-08-30)

**Kontext:** Nutzer-Auftrag „echte LIVE-DATA-INTEGRATION, nur Kraken + Bybit, READ-ONLY /
public market data, keine Keys, keine Order". Details: `docs/LIVE-DATA-INTEGRATION-2026-08.md`.

### Ergebnis

- **Connectivity-Test (echte Calls):** Kraken **CONNECTED**, Bybit **CONNECTED**. REST
  (Zeit/OHLCV M1+M5/Bid-Ask) + WebSocket (Trade-Stream) bei beiden. Clock-Skew < 1 s.
  Data-Quality auf den REST-Bars sauber, keine Zukunftsdaten. Provider-Health `HEALTHY`.
- **Pipeline `runtime/live_pipeline.py`:** REST-Warmup → `prime()` → WS confirmed M5 →
  `MarketContext(cutoff=close_time)` → `PaperLiveRunner.feed()` → Decision → Signal → Alert →
  Paper. EventBus mit neuen Events (`DecisionMade`/`SignalRevised`/`AlertRaised`/
  `PaperPositionChanged`). `orders_sent` in jedem Lauf asserted 0.
- **Live-Test:** Warmup (M5=401 …), `prime` = Decision `NO_TRADE/SCANNING`, dann bei **jedem**
  M5-Grenzübergang eine neue confirmed WS-Bar ⇒ neuer MarketContext ⇒ voller Durchlauf. Decision
  bleibt `NO_TRADE/SCANNING` — **identisch zum Backtest** (Regime-Gate). Die Pipeline läuft
  korrekt auf Live-Daten; sie hat nur (erwartungsgemäß) nichts zu signalisieren.

### Fragen 7/8 — was gefunden & behoben wurde

- **Symbol-Mapping-Bug (Kraken WS):** `KrakenWSSource` sendete das kanonische `BTCUSDT` an die
  Kraken-v2-API (die „BASE/QUOTE" erwartet) ⇒ 0 Trades geparst. Zusätzlich: Kraken-Liquidität
  liegt in den **USD**-Paaren (`BTC/USDT` handelte 0×). **Fix:** `kraken_ws_name()` mappt
  `BTCUSDT`→`BTC/USD`; `_parse` mappt zurück (akzeptiert auch das kanonische Format). Bybit war
  korrekt (`BTCUSDT` = WS-Name).
- **Kein Fake:** `fetch_quote` neu (Kraken `/0/public/Ticker`, Bybit `/v5/market/tickers`) —
  echtes Bid/Ask. Kein Orderbook-L2, kein privater WS (nicht Teil von read-only-public, nicht
  benötigt) — als „nicht angebunden" dokumentiert, nicht simuliert.
- **Forming-Bar-Guard + Dedup** (`_fed_opens`) im Live-Pfad — eine Bar mit `close_time` > 1
  Intervall in der Zukunft oder eine bereits verarbeitete `open_time` wird verworfen.
- **Kein Order-Pfad.** `PaperLiveRunner` hat keinen Broker; `LivePipeline.orders_sent` bleibt 0.

### Backlog

- 24/7-Daemon: `runtime/supervisor.py` von der Phase-2B-`ScannerShell` auf `LivePipeline`
  umstellen (M-01).
- Höhere TFs im Live-Pfad rollierend resampeln statt per REST nachladen.
- Bybit Funding/OI in `DerivativesContext`.

**+11 Tests (test_live_pipeline 5, Quote-Tests 4, Kraken-WS-Mapping 2). 886 grün, ruff +
ruff-format + mypy --strict grün.**

---

## 7. Änderungshistorie dieses Dokuments

- **2026-08-31 (Audit 23)** — XAUUSDT-Ingest (`scripts/ingest_binance_futures.py`, 75 630 M5,
  100 % vollständig) + **erster Gold-Backtest**: 0 Trades, 605 Signale/573 invalidiert,
  `regime_unclear` 82,8 %. „Gold = anderer Regime-Hebel" widerlegt (9-Mon.-Fenster). §6t.
- **2026-08-30 (Audit 22)** — Binance READ-ONLY. `data/providers/binance.py` (public REST,
  spot + USD-M-Futures) + `binance_account.py` (READ-ONLY HMAC, keine Orders) + `BinanceWSSource`.
  XAUUSDT via USD-M-Futures verifiziert (Live/M1..D1/Mark/Funding/OI) + Pipeline-Durchstich.
  `scripts/binance_market_test.py` + `binance_account_test.py`. §6s. **969 grün.**
- **2026-08-30 (Audit 21)** — cTrader/Pepperstone READ-ONLY. `data/providers/ctrader.py`
  (OAuth2 Scope `accounts`, `CTraderClient` JSON-WS, `CTraderAdapter` ohne submit/cancel).
  `scripts/ctrader_link.py` + `ctrader_account_test.py`. §6r. **952 grün.**
- **2026-08-30 (Audit 20)** — Bybit Account READ-ONLY-Anbindung (**Bybit EU**, Host
  `api.bybit.eu`). `data/providers/bybit_account.py` (`sign_v5` HMAC-SHA256,
  `BybitAccountAdapter` ohne submit/cancel, `assert_read_only` via `/v5/user/query-api` mit
  `readOnly`-Flag-Auswertung). Live CONNECTED. `scripts/bybit_account_test.py`. §6q. **944 grün.**
- **2026-08-30 (Audit 19)** — Kraken Account READ-ONLY-Anbindung. `security/secrets.py` real
  (`Secret`/`get_secret`/`redact`). `data/providers/kraken_account.py` (HMAC-SHA512 gegen
  Kraken-Testvektor, `KrakenAccountAdapter` ohne submit/cancel, `assert_read_only`).
  `scripts/kraken_account_test.py`. §6p. **928 grün.**
- **2026-08-30 (Audit 18)** — Gold/FX READ-ONLY/Paper + Pepperstone/MT5-Bewertung. Empfehlung
  cTrader Open API (kein Windows). Neue Adapter `ctrader`/`dukascopy`/`yahoo_finance`. XAUUSD
  live via Bybit `XAUTUSDT`, Paper → NO_TRADE (Regime + Weekend + Spread). `session_specs` in
  die Pipeline. `scripts/ingest_dukascopy.py`. §6o. **912 grün.**
- **2026-08-30 (Audit 17)** — M-01: `LiveSupervisor` fährt die `LivePipeline` **24/7**
  (Recovery via atomarem Snapshot, zweistufiger WS-Reconnect, stale→REST-Backfill, Watchdog,
  Fehler-Isolation, SIGTERM graceful, Wall-Clock). `state/store.py`+`state/recovery.py` von
  Stub auf voll. M15 rollierend aus M5 resampelt (REST-Last −12×). Bybit Funding/OI im
  `DerivativesContext` (nur valide Daten). §6n. 899 Tests grün. `orders_sent`=0.
- **2026-08-30 (Audit 16)** — Live-Data-Integration Kraken + Bybit (read-only public). §6m.
  Ergebnis: beide **CONNECTED** (REST + WS), `runtime/live_pipeline.py` fährt echte Live-Daten
  durch die volle Pipeline bis Paper-Position, `orders_sent`=0. Kraken-WS-Symbol-Mapping-Bug
  gefunden & behoben (`BTCUSDT`→`BTC/USD`). `fetch_quote` (Bid/Ask) neu. Decision bleibt
  `NO_TRADE/SCANNING` (Regime-Gate, wie im Backtest). 886 Tests grün.
- **2026-08-29 (Audit 15)** — News/Macro-Analyse-Schicht (`analysis/news.py` + `analysis/macro.py`

- **2026-08-29 (Audit 15)** — News/Macro-Analyse-Schicht (`analysis/news.py` + `analysis/macro.py`
  von Stub auf voll, PIT, asset-spezifisch, kein Fake) · `refdata/corporate_actions.py` +
  `risk/margin.py` implementiert · `asset_class` durch den Backtest-Pfad · `--cost-profile` ·
  `docs/MULTI-ASSET-READINESS.md` · **Multi-Symbol-Backtest: 0 Trades über 6 Krypto-Assets**
  (`docs/MULTI-SYMBOL-BACKTEST-2026-08.md`) — „mehr Krypto-Instrumente" als Hebel widerlegt,
  Regime-Gate blockt ~100 %, keine Parameteränderung · `validate_dataset` Kontinuitäts-Check
  (fand echte 2023-03-24-Lücke). §6l. **876 Tests grün, ruff+mypy strict grün.**
  Kein Crypto-Runtime-Verhalten geändert.
- **2026-08-29 (Audit 14)** — Regime-OOS-Kalibrierung · Parity · MTF-Caching · Risk-Pfad ·
  Architektur-Prep. §6k. Ergebnis: **Regime-Baseline bleibt** (kein Default geändert — jede
  Lockerung ist OOS negativ); Parity vollständig in Backtest + `PaperLiveRunner`; MTF-Cache 1.6×
  ohne Korrektheitsverlust; Risk-Limits Ende-zu-Ende im Paper-Live-Pfad verifiziert inkl.
  „Score überstimmt kein Limit"; Live-Adapter-Audit (13 Punkte); Architektur-Doc für
  Multi-Asset / Multi-Agent / 24/7. Kein CRITICAL, kein neuer Marktdaten-Faktor.
- **2026-08-29 (Audit 13)** — echte BTC/ETH-Historie · erster realer Backtest · Kosten · Risk ·
  Live-Adapter. §6j. Ergebnis: **0 Trades auf 2025 H1** (Regime-Gate + News-Fail-safe blocken
  100 %; period-abhängig, in 2024-Q4 passiert das Gate 4.4 %). Kein Leakage/Snooping/Survivorship.
  2 reale Bugs behoben (Krypto-Wochenende, `_analyze_tf`-Crash). Score/Confidence-Informationswert
  **noch nicht messbar** (braucht Regime-Kalibrierung → Backlog). Kostenmodell (Default 0),
  Risk Engine (strukturell nicht durch Score überstimmbar), Live-Adapter-Verträge stehen.
- **2026-08-29 (Audit 12)** — End-to-End-Backtest + ReplayClock-Harness. §6i. Ergebnis:
  PIT/Look-ahead doppelt abgesichert (Assembler-Slice + MarketContext-Validator, Test grün);
  Backtest optimiert nichts (RunManifest/output_hash, deterministisch); Survivorship durch
  Vorab-Instrumentliste + `DatasetIncompleteError` (kein Fake); **echter Engine-Bug gefunden &
  behoben** (`_seen_fill_bar`-Verschlüsselung + Warmup-Replay); Kosten-Modell im Paper-Sim,
  struktur-Trail, Intrabar-Fill, Parity-Report = Backlog.

- **2026-08-29 (Audit 11)** — Dynamic-Signal / Exit / Alert / Re-Evaluation / Paper-Live
  (Overnight-Run Schritte 4–10). §6h. Ergebnis: kein neuer Marktdaten-Faktor (reine
  Orchestrierung); `SignalState` ab TRIGGERED **nur** aus der Position (kein Double-Count);
  M1-Confirmation bleibt Kontext, `m1_feed` liefert ohne echte Historie leer (kein Fake);
  struktur-basierter Trail-Stop = Backlog; alle neuen Params unkalibriert →
  `CALIBRATION_BACKLOG.md`; große Datenquellen-Liste erneut geprüft → unverändert nichts
  aufgenommen (keine PIT-Historie), Slots stehen bereit.
- **2026-08-28 (Audit 1)** — Erstfassung nach Phase-3-Primitives + Market Regime. Ergebnis:
  Sessions als nächster Schritt; HIGH-Backlog = tiefe Historie, News+PIT, Sessions, Range-Bruch;
  kein CRITICAL offen.
- **2026-08-29 (Audit 10)** — vor Contradictions + No-Trade. §6g. Ergebnis: kein fehlender
  Hard-Block; C3–C8/C10 = Veto-Echos (INFO, nicht re-entschieden); Matrix-Mehrwert = C1/C2/C9≥50%/C12
  (+ 4 `NoTradeReason` angehängt); §5-Penalties werden gemeldet, im MVP **nicht** auf den Score
  angewandt; No-Trade-Gruppen ohne Konto-/Broker-State = `not_checked` (blockieren nicht).
- **2026-08-29 (Audit 9)** — vor der Scoring-Engine. §6f. Ergebnis: R-06 eingehalten (12 reine
  WEIGHTED-Faktoren); korrelierte Faktor-Gruppen als bekannte MVP-Limitation + `correlated_factor_groups`
  als Mitigation (HIGH-Kalibrierung); `session_context`/`data_confidence_bonus` als Kontext-vs-Score
  offen (MEDIUM); 2 fehlende Faktoren dokumentiert, nicht aufgenommen (news_proximity MEDIUM,
  target_structure LOW).
- **2026-08-29 (Audit 8)** — vor der Confidence-Engine. §6e. Ergebnis: **kein fehlender Faktor**;
  `confirmation_market`-Bestätigung als Confidence-Faktor **abgelehnt** (nur Evidence-Feld);
  Verbesserung: harter `unconfirmed_swing`-Flag (Spec §5) statt reinem weighted-mean; OOS zu
  validieren: 40/60, alle Floors, die 6 analysis_weights, source-Terme.
- **2026-08-29 (Audit 7)** — vor der Veto-Engine. §6d. Ergebnis: **kein fehlendes Veto**; HIGH-Vol /
  messy_sweep / <50 % Zonen-Überlappung bleiben **Penalties** (keine Vetos); Verbesserung:
  `VetoRecord.correlated_with` (V6↔V7, V8↔V10 gemeinsame Ursache); OOS zu validieren: V3/V6/V7/V9.
- **2026-08-29 (Audit 6)** — vor der Confluence-Engine. §6c. Ergebnis: **kein neuer Indikator**;
  Methode = relevanz-gewichteter Gruppen-Durchschnitt (Information statt Faktor-Zahl); BOS/CHoCH nie
  getrennt; Log-Odds-Kombination als MEDIUM-Upgrade **nach dem ersten OOS-Backtest** dokumentiert;
  `mtf_disagreement`/`volatility`/`phase`/`session`/`data_confidence` = Kontext (nicht Score).
- **2026-08-29 (Audit 5)** — vor Location-/RR-Gate. §6b. Ergebnis: **kein neuer Indikator**; zwei
  Robustheits-Ergänzungen (zeitstempel-basierte Swept-Leg-Range, H4/M15-Swing-Levels als TP2-Kandidat);
  ein engerer struktureller SL-Kandidat **abgelehnt** (verletzt §10 „ungünstigere").
- **2026-08-28 (Audit 2)** — vor MTF/FSM. §6a: detaillierte Kandidaten-Bewertung (18 Kandidaten).
  Ergebnis: **nichts Neues vor MTF**; L2/Bid-Ask/Liquidations = REJECT (kein Backtest, Parität);
  CVD/OI/VIX/DXY/Yields = MEDIUM Post-FSM; Economic Calendar/News + Funding = HIGH, aber Slot nach
  MTF; einzige Sofortmaßnahme = `MarketContext`-Vorwärtskompatibilität beim MTF-Schritt.
