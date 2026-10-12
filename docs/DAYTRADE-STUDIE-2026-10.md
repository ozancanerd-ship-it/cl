# Tageshandel Krypto + Gold — zweite Studie vom 11.10.2026

**Anlass:** Ozan, 11.10. 22:33: „ich möchte auch Alarme … für die Daytrades normal und auch
für Krypto und für Gold". Die erste Gold-Studie (Asien-Range/Sweep, 15 Min) und die
Mean-Reversion-Studie (Coins, 1 Std.) fanden keinen Edge. Hier werden vier andere,
in Literatur und Praxis dokumentierte Ideen geprüft.

**Daten:** data.binance.vision, Spot 1 Std., 01.2022–09.2026, 24 liquide Coins + PAXG (Gold).
IS 2022–2024, OOS 01.2025–09.2026. Kosten je Runde: Coins 0,25 %, PAXG 0,15 %.
Regeln vorab im Kopf von `scripts/daytrade_studie.py`; Rohzahlen in
`docs/daytrade_studie_ergebnis.json`.

## Ergebnis (Ø R je Trade nach Kosten)

| Setup | IS n | IS Ø R | OOS n | OOS Ø R | OOS Boot 5 % | Urteil |
|---|---:|---:|---:|---:|---:|---|
| M1 Ausreißer-Tag läuft weiter (Caporale/Plastun), Coins long | 1016 | −0,01 | 620 | +0,05 | −0,02 | kein Edge |
| M2 48-Std.-Ausbruch + Trend, Coins long | 3977 | +0,07 | 2455 | +0,08 | −0,03 | Kandidat |
| M2 Gold long | 184 | −0,43 | 121 | +0,45 | +0,03 | nur Gold-Hausse |
| M3 Squeeze-Ausbruch, Coins long | 5772 | −0,23 | 3602 | −0,18 | −0,22 | kein Edge |
| M4 Intraday-Momentum (Shen/Urquhart/Wang), Coins long | 5893 | −0,02 | 3676 | −0,13 | −0,15 | kein Edge |
| alle Shorts, alle Gold-Varianten außer M2 long | | < 0 | | < 0 | | kein Edge |

M2 Coins ist robust gegen andere Parameter (Lookback 36/72, Trail 12/48, ohne
Bitcoin-Filter: IS und OOS jeweils > 0) — aber **nur bei niedrigen Kosten**: bei 0,5 % je
Runde negativ, mit Kraken-Marktorders (0,8 % je Runde) klar negativ.

## Die Welle (Nachtrag, Schwelle nach Sicht auf IS)

Die IS-Trades von M2 nach „wie viele der 24 Coins brachen in den 24 Std. davor ebenfalls
aus" aufgeteilt: 1–11 Coins negativ, ab 12 deutlich positiv. Eine Schwelle (12) wurde
festgelegt und erst dann im OOS geprüft:

| M2 + Welle ≥ 12 | IS n | IS Ø R | OOS n | OOS Ø R | OOS Boot 5 % | Coins im Plus |
|---|---:|---:|---:|---:|---:|---:|
| | 1886 | +0,21 | 1283 | +0,14 | +0,01 | 16/24 |

Jedes Jahr 2022–2026 ≥ 0. Ohne Welle (einzelne Ausbrüche): IS und OOS negativ.

**Was das in Geld heißt — ehrlich:** R-Durchschnitte werden von wenigen Trades mit sehr
engem Stop und großen Wellen getragen (Trefferquote rund 25 %, Median-Trade −1 R). Mit
realistischer Größe (0,25 % Depot-Risiko je Coin, höchstens 10 % Depot je Coin, höchstens
100 % gesamt): IS +53 % Depot in 3 Jahren, OOS **+9,7 % in 21 Monaten** (2025 −3,3 %,
2026 +13 %). Bei engeren Gesamtgrenzen (50 %) ist OOS etwa null. Der Vorteil ist schmal
und hängt an den seltenen großen Wellen — wer nur einzelne Einstiege nimmt, nimmt sich
genau diese weg.

## Entscheidung

- **Krypto-Ausbruchswelle:** Alarm JA (Ozans ausdrücklicher Wunsch), als **Kandidat**
  gekennzeichnet, mit Größenregel und Kostenhinweis (Bybit-Spot oder Limit-Order). Jeder
  Trade wird als Paper-Trade vorwärts mitgeschrieben (`scripts/daytrade_alarme.py`,
  `data/repository_real/live/daytrade_stand.json`). Nach 100 Forward-Trades neu bewerten.
- **Gold-Tageshandel (PAXG):** Alarm mit 🧪 **experimentell** — 2022–24 klar negativ, nur in
  der Gold-Hausse 2025/26 positiv. Gold-Swing bleibt S9 (Reiter „Day-Trading").
- Einzelne Ausbrüche ohne Welle, Shorts, M1/M3/M4: **kein Alarm**.

Live und Studie benutzen dasselbe Modul `trading_agent.strategy.setups.daytrade_ausbruch`.

## Lohnt-sich-Tor (Nachtrag 12.10.2026)

Anlass — Ozan, 12.10.: „Benachrichtigung für Entry … aber nur wenn es sich lohnt".
Skript: `scripts/daytrade_tor_pruefung.py` (gleiche Daten, gleiches Modul, Ø R nach Kosten).

Drei Merkmale der Welle-Trades wurden aufgeteilt, jeweils IS (2022–24) / OOS (2025–09.2026):

| Merkmal | Gruppe | IS | OOS |
|---|---|---|---|
| Kosten in R | kein gleichbleibendes Muster (OOS bei großem Stop negativ, IS positiv) | — | — |
| Wellenbreite | 12–15 / 16–19 / 20–24 | +0,24 / +0,22 / +0,14 | +0,05 / +0,11 / +0,28 |
| **Überhang über 48-Std.-Hoch** | **< 0,25 ATR (knapp)** | **+0,02 R** (n 433) | **−0,30 R** (n 302) |
| | ≥ 0,25 ATR (klar) | +0,27 R (n 1.453) | +0,28 R (n 981) |

Mit Tor „≥ 0,25 ATR": OOS-Bootstrap-5 %-Grenze **+0,11 R** (ohne Tor +0,01 R), 18 von 24 Coins
im OOS im Plus (ohne 16), 2023–2026 jedes Jahr besser, 2022 gleich (~0). Schwellen
0,10 / 0,15 / 0,35 / 0,50 zeigen dasselbe Bild — keine Messerkante.

**Ehrlich:** IS und OOS wurden gleichzeitig angesehen. IS allein hätte dieselbe Schwelle
nahegelegt, aber es bleibt ein Nachtrag. Gegenprobe: bei Ausbrüchen OHNE Welle hilft das Tor
nicht (+0,01/−0,08 vs. −0,09/+0,04) — es ist eine Eigenschaft der Welle, keine allgemeine Regel.
Welle-Trades laufen gleichzeitig in vielen Coins; der Bootstrap unterstellt Unabhängigkeit und
überschätzt die Sicherheit. Status bleibt **KANDIDAT**.

**Umsetzung:** Einstiegs-Push nur bei Welle **und** klarem Ausbruch
(`daytrade_ausbruch.MIN_UEBERHANG_ATR = 0.25`). Knappe Ausbrüche laufen als stille
Paper-Gegenprobe mit (eigene Forward-Bilanz) — so zeigt die Zukunft, ob das Tor trägt.
Gold-Tagesausbruch: kein Push mehr (lohnt sich nicht belegt), nur Paper und Chart; Gold-Alarme
kommen aus S9.
