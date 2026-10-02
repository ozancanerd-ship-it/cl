# Die Bilanz hat Trades vergessen — Befund und Reparatur, 02.10.2026

## Befund

Beim Morgen-Check stand unter „Deine Alarme" plötzlich **3 Trades, +1,5 R** statt am
Abend vorher 4 Trades, +0,5 R. Der Zcash-Stop vom 01.10. (Alarm aufs Handy) war aus der
Bilanz verschwunden — nicht weil er korrigiert wurde, sondern weil Zcash um 05:01 UTC
wieder auf die Wachliste kam.

Ursache, zwei Stellen in `scanner/watchlist.py`:

1. Die Wachliste ist nach **Instrument** geschlüsselt. Eine neue Wache für einen Wert
   überschrieb den abgeschlossenen Trade desselben Werts.
2. `aufraeumen()` behielt nur die letzten **60** abgeschlossenen Wachen — auch nie
   ausgelöste Setups zählten dazu und drängten echte Trades hinaus.

Die Bilanz, das Alarm-Tor („bewährt"/„gesperrt") und die Trefferquoten auf den
Signalkarten rechnen alle aus dieser Liste.

## Ausmaß (rekonstruiert aus 2010 Git-Ständen von `watchlist.json`)

| | Trades |
|---|---:|
| eingegangene, abgeschlossene Trades seit 05.09. | **355** |
| davon noch in der Bilanz | 38 |
| verschwunden | 317 |
| davon Alarme aufs Handy | 4 (Zcash 27.09. Stop, Zcash 29.09. Stop, AT&T, AAVE) |

Verschwunden ist bevorzugt, was wiederkam — also Werte, die nach einem Trade erneut
ein Setup zeigten. Das hat die Bilanz verzerrt, in welche Richtung auch immer.

## Die Bilanz mit allen Trades (nach Plan)

| | Trades | R |
|---|---:|---:|
| **Deine Alarme (aufs Handy)** | 8 | **+1,9** |
| Alle Signale | 285 gezählt | −96,4 |
| davon KW 36 (bis 06.09.) | 42 | −9,2 |
| davon **KW 37 (07.–13.09.)** | 150 | **−86,7** |
| KW 38 | 31 | +10,4 |
| KW 39 | 44 | +0,3 |
| KW 40 (bis 02.10.) | 18 | −11,2 |
| **seit KW 38** | 93 | **−0,5** |

Je Klasse: Coins 246 Trades −72,5 R, Aktien 26 −14,8 R, Gold 13 −9,0 R.
Der große Verlust stammt aus der Woche 07.–13.09. (damals bis zu 40 Wachen gleichzeitig,
22 davon dieselbe Krypto-Wette; am 09.09. elf Stops an einem Tag).

Für das Alarm-Tor mit allen Trades:

| Setup-Art bei Coins | Trades | R | PF | Urteil |
|---|---:|---:|---:|---|
| Ausbruch aus der Basis | 18 | +5,7 | 1,81 | bewährt |
| Rückeroberung nach Liquiditätsgriff | 24 | +9,5 | 1,86 | bewährt |
| Rücksetzer im Trend | 8 | +1,7 | 1,42 | offen |

Mit mehr Daten bleiben beide freien Coin-Setups bewährt — jetzt auf 18 und 24 Trades
statt auf 6 und 13.

## Reparatur

* **Archiv:** Was überschrieben oder aufgeräumt wird und eingestiegen war, kommt nach
  `data/repository_real/live/wachen_archiv.jsonl` (jeder Trade einmal). Der
  rekonstruierte Altbestand liegt fest in
  `data/repository_real/archiv/wachen_historie_2026-10-02.json`.
* **Bilanz, Alarm-Tor, Trefferquoten** lesen Liste + Archiv (`watchlist.mit_archiv`),
  ohne Doppel (Instrument + Aufnahmezeit).
* **Workflow** sichert die Archivdatei mit.
* **Alarm-Tor:** Mit allen Trades steht „Coins Long" bei 168 Trades, −49 R — fast nur
  unbenannte Setups aus KW 36/37, die nie klingeln. Die grobe Sperre „Klasse + Richtung"
  gilt deshalb nicht für eine Setup-Art, die sich **in ihrer Klasse bewährt** hat.
  Ohne diese Regel wären mit dem Archiv alle Coin-Long-Alarme stumm gewesen, auch die
  bewährten.
* **App:** Bilanz-Karte zeigt zusätzlich je Woche; der Satz nennt, woher der Verlust
  kommt und was seitdem passiert ist.
