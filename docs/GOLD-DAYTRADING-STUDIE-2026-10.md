# Gold-Tageshandel — Studie vom 07.10.2026

**Frage:** Hat der Gold-Tageshandel (Sessions, Asien-Range, Vortages-Sweep) einen messbaren Vorteil?

**Daten:** PAXG/USDT 5 Minuten (Binance-Archiv data.binance.vision), 01.2023–09.2026, zu 15 Minuten
zusammengefasst — 131 418 Kerzen. PAXG ist ein goldgedeckter Token, die beste frei verfügbare
Goldspur; Kurs, nicht Order-Ausführung eines CFD-Brokers.

**Regeln (vorab festgelegt, in `scripts/gold_daytrading_studie.py` im Kopf):**
G1 Asien-Range-Ausbruch in London (Stop Range-Mitte, Ziel 2 R) · G2 Sweep über/unter
Vortageshoch/-tief mit Rückschluss (Stop Kerzenextrem, Ziel 2 R) · Zeitausstieg 20:00 UTC ·
Kosten 0,08 % je Runde · pessimistisch bei Stop und Ziel in derselben Kerze · IS bis 06.2025,
OOS ab 07.2025 · kein Parameter nachträglich angepasst.

## Ergebnis (R je Trade nach Kosten)

| Setup | IS n | IS Ø R | OOS n | OOS Ø R | OOS Bootstrap 5 % |
|---|---:|---:|---:|---:|---:|
| G1 alle | 379 | −0,39 | 199 | −0,06 | −0,18 |
| G1 long | 217 | −0,33 | 111 | −0,10 | −0,26 |
| G1 short | 162 | −0,48 | 88 | −0,01 | −0,26 |
| G2 alle | 362 | −0,73 | 159 | −0,57 | −0,77 |
| G2 long | 160 | −0,76 | 87 | −0,54 | −0,82 |
| G2 short | 202 | −0,71 | 72 | −0,60 | −0,83 |

## Entscheidung

**Kein Edge.** Keines der Setups ist in IS und OOS im Plus. G2 verliert deutlich, G1 liegt
im OOS nahe null, im IS klar im Minus — das ist kein Beleg. Gold-Tageshandel bleibt damit
**EXPERIMENTAL, ohne Alarm, ohne Handy-Meldung.** Gold wird weiter über Swing-Signale
(PAXGUSD im Scan) geführt, nicht als Tageshandel.

Warum es schwer ist: Gold-Tagesstops liegen bei 0,2–0,5 % — Gebühr und Spread fressen
einen großen Teil von 1 R. Wer das handeln will, braucht einen Broker mit Spread < 0,02 %
(CFD/Futures) — das ist nicht Ozans heutiger Zugang.

**Nicht getestet:** Nachrichten-Zeitpunkte (CPI/FOMC) als Filter, Orderbuch-Daten. Beides ohne
Datenquelle. Neue Hypothesen brauchen neue Vorab-Festlegung.
