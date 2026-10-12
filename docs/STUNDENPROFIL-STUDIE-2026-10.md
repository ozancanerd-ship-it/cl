# Stundenprofil & Spannen-Prognose — Studie 12.10.2026

Anlass — Ozan, 12.10. 10:50: „jetzt musst du die analysen verbessern bzw perfektionieren, nutze
dafür alle ressourcen internet bücher etc". Die Analyse sagte bisher, **wo** der Kurs steht. Es
fehlte, **wie weit** er realistisch laufen kann — Grundlage für Stop-Abstand, Ziele und
„lohnt sich das heute noch?".

Skript: `scripts/stundenprofil_studie.py` · Ergebnis: `docs/stundenprofil.json` · App: Reiter
Day-Trading, Karte „Wie weit kann es laufen?" und das Band „erwartet 4 Std. (68 %)" im Chart.

## Literatur (vor dem Rechnen festgelegt)

- **Andersen & Bollerslev (1997)**, *Intraday periodicity and volatility persistence in financial
  markets* (J. Empirical Finance): Volatilität hat ein starkes Tagesmuster; für Prognosen wird es
  multiplikativ herausgerechnet (Rendite ÷ typische Schwankung dieser Tageszeit), die Persistenz
  auf dem Rest geschätzt und das Muster wieder aufgelegt.
- **RiskMetrics (J.P. Morgan 1996)**: EWMA-Varianz als robuste Prognose. Renditen sind
  fett-schwänzig → Bänder aus empirischen Quantilen statt Normalverteilung.
- **Hansen, Kim & Kimbrough (2021)**, *Periodicity in Cryptocurrency Volatility and Liquidity*:
  Krypto am bewegtesten um 16 UTC, am ruhigsten um 5 UTC, Wochenende ruhiger.
- **„Gold price dynamics around the clock" (CBS)**: Gold steigt eher in östlichen Stunden,
  fällt eher im Rest des Tages; nach Kosten kaum handelbar.

## Methode

1-Std.-Kerzen Binance Vision 01.2022–09.2026, PAXG (Gold) und 24 Coins (gepoolt). IS 2022–24,
OOS 01.2025–09.2026. Profil = mittlere |Rendite| je Wochenstunde (168 Fächer, UTC), geglättet,
Mittel 1. EWMA-λ nach IS-QLIKE aus {0,97 … 0,998} (Gold 0.97, Coins
0.995). Bandfaktoren = IS-Quantile 68 %/95 % von |Rendite ÷ Prognose|.
Kriterium je Horizont: OOS-Treffer 68 %-Band in 63–73 %, 95 %-Band in 92–98 %.

## Ergebnis

**Coins** (Faktor in σ · OOS-Treffer 68/95 % · zum Vergleich Normalverteilung ±1,96 σ · QLIKE OOS
mit vs. ohne Profil, kleiner = besser):

| Horizont | Bänder | OOS-Treffer | Normal 95 % | QLIKE | hält |
|---|---|---|---|---|---|
| 1 Std. | ±0.76 σ / ±1.95 σ | 66.4 % / 94.8 % | 94.9 % | -8.679 vs -8.627 | ✅ |
| 4 Std. | ±0.76 σ / ±1.98 σ | 66.1 % / 94.7 % | 94.6 % | -7.258 vs -7.217 | ✅ |
| 24 Std. | ±0.82 σ / ±2.06 σ | 66.0 % / 94.7 % | 93.8 % | -5.353 vs -5.343 | ✅ |

**Gold (PAXG):**

| Horizont | Bänder | OOS-Treffer | Normal 95 % | QLIKE | hält |
|---|---|---|---|---|---|
| 1 Std. | ±0.86 σ / ±2.09 σ | 69.1 % / 94.8 % | 93.9 % | -11.449 vs -11.239 | ✅ |
| 4 Std. | ±0.76 σ / ±1.84 σ | 65.9 % / 92.5 % | 93.7 % | -10.057 vs -9.854 | ✅ |
| 24 Std. | ±0.76 σ / ±1.86 σ | 61.3 % / 90.0 % | 91.2 % | -7.958 vs -7.811 | ❌ |

- Das Stundenprofil verbessert die Prognose überall (QLIKE IS und OOS) — das Tagesmuster ist
  real, wie in der Literatur.
- **Gold 24 Std. hält nicht:** 2025/26 lief Gold in einer Hausse weiter, als die Schwankung der
  Vorstunden erwarten ließ. Die App zeigt das Band trotzdem, aber markiert es „hält nicht" mit
  der echten Trefferquote; im Chart wird es nicht gezeichnet.

**Richtung je Sitzung** (Ø Rendite je Tag, Coins: Schnitt über alle Coins je Tag):

| Gold | IS | OOS |
|---|---|---|
| asien | +1.1 bp (t 0.97) | +3.0 bp (t 1.06) |
| london | +0.5 bp (t 0.4) | +3.7 bp (t 1.49) |
| newyork | +2.4 bp (t 1.01) | -2.8 bp (t -0.92) |
| spaet | -0.7 bp (t -0.46) | +3.3 bp (t 2.05) |

| Coins | IS | OOS |
|---|---|---|
| asien | +2.7 bp (t 0.42) | -7.3 bp (t -1.02) |
| london | -1.2 bp (t -0.23) | +1.9 bp (t 0.34) |
| newyork | -9.6 bp (t -1.27) | -5.7 bp (t -0.56) |
| spaet | +5.4 bp (t 1.28) | -2.5 bp (t -0.5) |

Kein Richtungsmuster ist in IS und OOS gleichzeitig signifikant. Das „Gold steigt in Asien"
der Literatur bestätigt sich auf PAXG 2022–26 nicht belastbar. (Ein erster Lauf zählte
Coin-Tage einzeln und kam bei Coins auf t-Werte um 4–5 — das war ein Fehler: 24 gleichlaufende
Coins sind keine 24 unabhängigen Beobachtungen. Korrigiert.)

## Was die App daraus macht

- Bänder 68 %/95 % für 1, 4 und 24 Std., auf den Live-Kurs zentriert, mit OOS-Trefferquote.
- Typische Hoch–Tief-Spanne (90 %-Quantil) je Horizont.
- „Bewegung in der kommenden Stunde" (Profilfaktor) und bewegteste/ruhigste Stunde der
  nächsten 24.
- **Stop- und Ziel-Check:** eigener Stop, Paper-Stop, S9-Stop/-Ziele gegen die Bänder („liegt
  in der normalen Schwankung der nächsten Stunde — wird leicht vom Rauschen ausgelöst").
- Prognose der **Schwankung, nicht der Richtung** — steht so in der App.
