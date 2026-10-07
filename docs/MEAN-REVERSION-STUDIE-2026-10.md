# Mean Reversion bei Coins — Studie vom 07.10.2026

**Frage:** Bringt „Überdehnung zurück zur Mitte" (Bollinger + RSI) auf 1-Stunden-Kerzen einen Vorteil?

**Daten:** 24 Coins, 1 h, 01.2025–10.2026 (Replay-Archiv). IS bis 12.2025, OOS ab 01.2026.
**Regeln (vorab, im Skript-Kopf `scripts/mean_reversion_studie.py`):** Schluss außerhalb Bollinger(20,2)
und RSI(14) unter 30 bzw. über 70 · Einstieg nächste Eröffnung · Stop 1,5 ATR · Ziel Bollinger-Mitte ·
24 h Zeitausstieg · Kosten 0,25 % je Runde · Variante R nur bei Seitwärtsregime (Effizienz 120 h < 0,15).

| Variante | IS n | IS Ø R | OOS n | OOS Ø R | OOS Bootstrap 5 % |
|---|---:|---:|---:|---:|---:|
| alle | 4234 | −0,10 | 3447 | −0,23 | −0,33 |
| long | 2168 | −0,11 | 1629 | −0,18 | −0,33 |
| short | 2066 | −0,10 | 1818 | −0,29 | −0,43 |
| Regime-Filter, alle | 2956 | −0,09 | 2186 | −0,21 | −0,31 |
| Regime-Filter, long | 1523 | −0,12 | 1061 | −0,13 | −0,28 |

## Entscheidung
**Kein Edge**, in keiner Variante, weder IS noch OOS, auch nicht mit Seitwärts-Filter. Bei
Coins überwiegt nach den Kosten die Fortsetzung der Bewegung („Messer fangen"). Mean Reversion
bleibt **EXPERIMENTAL und ohne Alarm**. Passt zur Beobachtung der eigenen Bilanz: die
Setup-Arten mit Ausbruch/Rückeroberung liegen bei ≈ 0 R, Gegenbewegung ist schlechter.
Nicht getestet: Aktien-Mean-Reversion nach Gap (andere Daten), Pairs (kein Datensatz).
