# Strategie-Entscheid — 2026-09-04

Antwort auf die Frage: **Swing oder Day-Trading, und welche Strategiefamilie?**
Grundlage: Arithmetik auf den eigenen Daten + Replikation der bisherigen Forschung
auf korrigierten Daten + Literaturlage.

---

## Entscheid in drei Sätzen

1. **Zeithorizont: Swing/Position auf D1 primär, H4 sekundär. Day-Trading und Scalping
   sind für dieses System arithmetisch ausgeschlossen** — nicht als Meinung, sondern weil
   die notwendige Trefferquote über 100 % läge.
2. **Strategiefamilie: Time-Series-Momentum mit Volatilitäts-Skalierung wird der neue Kern.**
   Die SMC-/Breakout-Familie ist auf den korrigierten Daten signifikant negativ.
3. **Die gesamte Infrastruktur bleibt.** Ausgetauscht wird der Setup-Detektor —
   geschätzt unter 5 % der Codebasis.

---

## F12 (NEU, kritisch) — Die Kern-Krypto-Daten waren 430 Tage veraltet

Vor der Korrektur endeten **BTCUSDT, ETHUSDT, SOLUSDT, BNBUSDT, XRPUSDT und DOGEUSDT
am 2025-06-30** — obwohl `setup_research.py` mit `--end 2026-08-29` lief.

Konsequenz: Das OOS-Fenster (Split 2025-01-01) war für genau diese Symbole nur
**sechs Monate lang**, nicht zwanzig. Die berichtete OOS-Edge ruhte damit noch stärker
auf den indikativen Yahoo-Reihen als angenommen.

Behoben: alle sechs Symbole plus LINKUSDT und SEIUSDT auf Stand 2026-07-31 nachgeladen.
**5.477 → 7.853 H4-Bars je Symbol (+43 %)**, davon 13 Monate echtes Neuland.

## Die Replikation — was die vollständigen Daten mit der Edge machen

Identischer Aufbau wie v11 (gleiches Panel, gleicher Split, gleiche Kosten 0.03 R),
**einziger Unterschied: vollständige Daten.** Kein Parameter angefasst.

| Setup | OOS n alt → neu | Expectancy alt → neu | Δ |
|---|---|---|---|
| S9_htf_confluence | 34 → 45 | +0.573 → **+0.370** | −0.203 |
| S11_htf_conf_session | 24 → 32 | +0.626 → +0.501 | −0.125 |
| S16_dxy_headwind | 31 → 42 | +0.583 → +0.303 | −0.280 |
| S8_session_filter | 28 → 39 | +0.470 → +0.253 | −0.217 |
| S4_breakout_trendfilter | 41 → 54 | +0.409 → +0.156 | −0.253 |
| S1_breakout_retest | 216 → 324 | +0.195 → +0.029 | −0.166 |
| **S0_sweep_reversal** | 670 → 703 | +0.046 → **−0.122** | −0.168 |
| COMBINED | 685 → 720 | +0.067 → **−0.117** | −0.184 |

**14 von 15 Setups wurden schlechter. Durchschnitt −0.175 R.**

Das ist die Lehrbuch-Signatur: Die scheinbare Edge war ein Artefakt der
unvollständigen Stichprobe.

### Signifikanz nach der Korrektur

Bei Kosten 0.03 R (unrealistisch niedrig): **0 von 15 Setups nominal signifikant.**
Bester Wert S11 mit p = 0.0512.

Bei realistischen Kosten 0.20 R:

| Setup | OOS n | Expectancy | t | p |
|---|---:|---:|---:|---:|
| S11_htf_conf_session | 32 | +0.331 | 1.08 | 0.140 |
| S9_htf_confluence | 45 | +0.200 | 0.78 | 0.217 |
| S1_breakout_retest | 324 | −0.141 | −1.61 | 0.946 |
| **S0_sweep_reversal** | **703** | **−0.292** | **−4.80** | 1.000 |
| **COMBINED** | **720** | **−0.287** | **−4.78** | 1.000 |

**Verdikt: Die SMC-/Sweep-Reversal-Familie ist nicht „unbewiesen" — sie ist auf der
größten verfügbaren Stichprobe (703 Trades) signifikant negativ.** Sie wird eingestellt.

## Warum Day-Trading arithmetisch ausscheidet

Break-even-Trefferquote allein für Gebühren + Slippage, bei RR 1:2, Stop = 0.8 × ATR,
Binance Taker 0.2 % Round-Trip + Slippage. Gerechnet auf den echten ATR-Werten im Repo:

| Symbol | M5 | M15 | H4 | D1 |
|---|---:|---:|---:|---:|
| XAUUSDT | **145.6 %** | **93.5 %** | 46.9 % | 38.7 % |
| BTCUSDT | **100.8 %** | 68.3 % | 40.7 % | 36.0 % |
| ETHUSDT | 85.6 % | 60.6 % | 39.1 % | 35.4 % |
| SOLUSDT | 62.9 % | 49.4 % | 37.0 % | 34.7 % |
| SEIUSDT | 58.9 % | 47.1 % | 36.4 % | 34.5 % |

Ohne Kosten läge die Schwelle bei 33.3 %. Auf M5 bräuchte Gold **über 100 %** — das ist
kein schwieriges Ziel, sondern ein unmögliches. Der Grund ist strukturell: der Stop
schrumpft mit dem Timeframe, die Gebühr nicht.

**Gold ist auf jedem Timeframe das teuerste Instrument des Panels** — ausgerechnet das
Asset, um das dieses Projekt herum gebaut wurde. Bei D1 ist es tragbar (38.7 %).

Literatur bestätigt dasselbe von der anderen Seite: Barber/Lee/Liu/Odean/Zhang (Taiwan,
1992–2006, ~450.000 Day-Trader/Jahr): 97 % verlieren an einem beliebigen Tag nach Gebühren,
unter 1 % sind dauerhaft profitabel. Chague/De-Losso/Giovannetti (Brasilien, 2013–2015):
von denen, die 300+ Tage durchhielten, verloren 97 %.

## Warum Time-Series-Momentum der neue Kern wird

### Evidenzlage

| | SMC / Order Blocks / FVG | Time-Series-Momentum |
|---|---|---|
| Peer-reviewed Beleg | **keiner** | Moskowitz/Ooi/Pedersen, JFE 2012 |
| Unabhängiger Test | 54 mechanische Setups auf 2,55 Mio. EURUSD-M1-Bars: **0 profitabel nach Kosten** | 58 Futures, 4 Assetklassen, 25 Jahre, Sharpe > 1 |
| Kryptomarkt | — | Han/Kang/Ryu: bestes Fenster 28 Tage, Sharpe 1.51 |
| Kostenempfindlichkeit | brutal (0.2–0.4 R je Trade) | gering (niedriger Umschlag) |
| Ergebnis auf **unseren** Daten | −0.29 R, t = −4.80 | siehe unten |

### Eigener Test — 2017 bis 2026, zwei volle Krypto-Zyklen

`scripts/tsmom_research.py`. Long-only, Signal = Vorzeichen der Rendite über das
Rückblickfenster, Position auf 40 % Zielvolatilität skaliert, 0.2 % Kosten je Wechsel.
BTCUSDT, ETHUSDT, BNBUSDT, XRPUSDT. Enthält die Bärenmärkte 2018 und 2022.

| Fenster | Sharpe | CAGR | Vol | MaxDD | im Markt |
|---|---:|---:|---:|---:|---:|
| 28 Tage | 0.93 | 27.7 % | 31.7 % | 43.3 % | 51.7 % |
| 56 Tage | 0.95 | 29.9 % | 34.1 % | 47.2 % | 53.0 % |
| 90 Tage | 0.69 | 19.5 % | 32.8 % | 49.6 % | 52.8 % |
| 120 Tage | 0.80 | 23.2 % | 33.5 % | 42.9 % | 52.2 % |
| 180 Tage | 0.63 | 16.8 % | 34.2 % | 55.9 % | 55.6 % |
| **Ensemble (alle 5)** | **0.86** | **23.5 %** | **29.7 %** | **37.2 %** | 82.2 % |
| Buy & Hold | 0.76 | 33.6 % | 82.2 % | 81.4 % | 100 % |

Je Symbol (Ensemble): BTC Sharpe 1.07 vs. 0.75 · ETH 0.89 vs. 0.64 · BNB 0.93 vs. 0.99 ·
XRP 0.57 vs. 0.66. Maximaler Drawdown durchgehend etwa halbiert (32–45 % vs. 76–90 %).

**Ehrliche Lesart:**

* TSMOM liefert **bessere risikoadjustierte Rendite** (Sharpe 0.86 vs. 0.76) bei
  **weniger als der halben Volatilität und dem halben Drawdown.**
* Es gibt **absolute Rendite ab** (6.3× statt 17.9× über 9 Jahre). Wer den ganzen
  Bullenmarkt durchhält, verdient mehr — muss dafür aber 81 % Drawdown aushalten.
* **Stabil über alle fünf Fenster** (Sharpe 0.63–0.95, keines negativ). Genau das
  Gegenteil der SMC-Familie, wo jeder Filter das Ergebnis wild verschob.
* Der Umschlag von 8–22× p. a. bedeutet 1.6–4.4 % Kostenbelastung im Jahr. Die
  SMC-Kette lag bei 0.2–0.4 R **je Trade**.

**Was das NICHT ist:** ein Beleg. Vier Symbole, eine Assetklasse, neun überwiegend
steigende Jahre, sechs getestete Konfigurationen (die ins Hypothesen-Register gehören).
Es ist ein vielversprechender Erstbefund, der dieselbe Prüfkette durchlaufen muss wie
alles andere: OOS, Walk-Forward, Monte-Carlo, Robustheit, Multiple-Testing-Korrektur.

Der Unterschied zur bisherigen Lage: Wir starten diesmal mit einer Hypothese, die
außerhalb dieses Projekts über 25 Jahre und 58 Instrumente Bestand hatte — statt mit
einer, für die es keinen einzigen unabhängigen Beleg gibt.

## Was bleibt, was geht

**Bleibt (unverändert wertvoll):** Datenschicht und Repository, Qualitäts- und
Lückenprüfung, Regime-Erkennung, Risk-Engine mit Positionsgrößen-Berechnung,
Portfolio-Intelligence, Governance/ValidationRegistry, Signal-FSM und Lifecycle,
Paper-Trading und Journal, Alert-Engine, 24/7-Daemon, die gesamte Testbasis.

**Geht:** `SETUP-SMC-SWEEP-REV-01` und `SETUP-BREAKOUT-RETEST-01` als Live-Kandidaten.
Beide bleiben im Code und in der Registry als **widerlegt** dokumentiert — ein
negatives Ergebnis ist Teil des Forschungsstands, kein Müll.

**Kommt:** `SETUP-TSMOM-ENSEMBLE-01` — Ensemble-Momentum-Signal auf D1, Positionsgröße
über Volatilitäts-Zielsteuerung, monatliche bis wöchentliche Anpassung statt
Trade-für-Trade-Setups.

## Konsequenz für das Portfolio-Denken

TSMOM ist keine Setup-Strategie, sondern eine **Allokationsregel**: Sie beantwortet
nicht „wo ist der Einstieg", sondern „wie viel von was, und wann gar nicht".

Das passt zu dem, was tatsächlich im Depot liegt: 13 Positionen ohne Stops, ohne
Invalidierung, ohne Ausstiegsregel. Genau die Lücke, die eine Allokationsregel füllt —
und die eine Setup-Strategie nie gefüllt hätte.
