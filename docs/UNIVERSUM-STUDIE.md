# Bringt ein größeres Universum mehr? — gemessen

**Ergebnis: Mehr Coins bringen fast nichts. Mehr Aktien bringen wenig. Was fehlt,
sind Anleihen und Rohstoffe — und die sind bisher per Regel ausgeschlossen.**

Stand 2026-09-04 · 49 Instrumente · ~5 Jahre Tagesdaten
Rohdaten: `docs/UNIVERSUM-STUDIE.json` · Code: `scripts/universe_study.py`

## Warum diese Prüfung

Die Recherche (Quantica 2025) misst für dieselbe Trendfolgeregel einen erwarteten
Sharpe von 0,33 bei 10 Märkten und 0,72 bei 69. Der Gewinn entsteht im Portfolio,
nicht im einzelnen Trade. Wenn das auch für Ozans erreichbare Märkte gilt, wäre ein
größeres Universum der stärkste verfügbare Hebel.

Aber die Zahl der Instrumente ist die falsche Größe. Zwanzig Altcoins sind keine
zwanzig Wetten — sie laufen alle mit Bitcoin. Gemessen wird deshalb die **effektive
Zahl unabhängiger Wetten**:

```
N_eff = n² / Σ ρ_ij
```

Zwei perfekt korrelierte Instrumente zählen als eines.

## Wo Streuung wirklich entsteht

| Gruppe | Instrumente | → effektive Wetten | Ø Korrelation |
|---|---:|---:|---:|
| nur Krypto | 11 | **1,6** | +0,58 |
| nur Aktien (24 Sektoren) | 24 | **3,6** | +0,25 |
| Krypto + Aktien | 35 | **4,9** | +0,18 |
| **nur Anleihen/Rohstoffe** | **10** | **4,0** | +0,16 |
| alles zusammen | 49 | **7,4** | +0,12 |

**Das ist die wichtigste Zeile der ganzen Studie:** Zehn Anleihen- und Rohstoff-Titel
liefern fast so viele unabhängige Wetten (4,0) wie fünfunddreißig Aktien und Coins
zusammen (4,9).

Elf Kryptowährungen sind **1,6 Wetten**. Wer den zwölften Coin dazunimmt, kauft
praktisch dieselbe Wette noch einmal.

## Was das für die Rendite bedeutet

| Universum | n | N_eff | Sharpe | Rendite/Jahr | Vol | Max. Rückgang |
|---|---:|---:|---:|---:|---:|---:|
| heute (13) | 13 | 4,4 | 1,28 | 11,4 % | 8,7 % | 8,1 % |
| breiter, nur handelbar (35) | 35 | 4,9 | 1,23 | 8,8 % | 7,1 % | 8,2 % |
| alles ohne ETFs (39) | 39 | 6,2 | 1,24 | 8,1 % | 6,4 % | 7,2 % |
| **+ Anleihen & Rohstoffe (45)** | 45 | 6,2 | **1,38** | 8,2 % | **5,8 %** | 7,3 % |
| nur Anleihen/Rohstoffe (10) | 10 | 4,0 | 0,24 | 1,1 % | 4,8 % | 11,8 % |

Drei Dinge sind daran wichtig:

1. **Mehr Aktien und Coins allein haben den Sharpe nicht verbessert** (1,28 → 1,23).
   Sie haben Schwankung und Rendite im gleichen Verhältnis gesenkt. Das ist kein
   Gewinn, nur ein kleineres Rad.
2. **Anleihen und Rohstoffe allein sind eine schlechte Strategie** (Sharpe 0,24) —
   aber *zusammen* mit dem Rest ergeben sie die beste Kombination. Genau darum geht
   es bei Diversifikation: nicht die beste Zutat, sondern die andersartige.
3. **Die Schwankung fällt von 8,7 % auf 5,8 %.** Das ist der verlässlichste Teil des
   Befunds — er folgt aus der Korrelationsrechnung und braucht den Momentum-Effekt
   gar nicht.

## Die unbequeme Folge

Ozans Masterplan schließt ETFs aus („nur einzelne Aktien, keine ETFs"). Anleihen und
Rohstoffe sind für ihn aber praktisch **nur** als ETF erreichbar — einzelne
Staatsanleihen oder Rohstoff-Futures kann er mit seinen Konten nicht handeln.

**Diese eine Regel kostet damit den einzigen Hebel, der in dieser Studie messbar
gewirkt hat.** Die Entscheidung liegt bei ihm; die Zahl dazu steht jetzt hier.

## Was NICHT belegt ist

- **Der Sharpe-Unterschied 1,28 → 1,38 ist nicht signifikant getestet** und liegt
  gut im Rauschen. Verlässlich ist die Korrelationsrechnung, nicht die Rendite.
- **Der Zeitraum ist nur ~5 Jahre** (2021–2026) und überwiegend Aufwärtsmarkt. Ein
  Sharpe über 1 auf so kurzer Strecke ist ein Warnsignal, kein Versprechen —
  live liefern Trendfolgefonds seit 2000 etwa 0,25.
- **Das ist eine neue Konfiguration**, kein Ergebnis der präregistrierten Hypothese.
  Registriert als `HYP-UNIVERSE-01`; die Konfigurationen zählen zur
  Multiple-Testing-Last.

## Konkrete Konsequenzen

1. **Keine weiteren Altcoins.** 11 Coins sind 1,6 Wetten. Der Zuwachs ist null.
2. **Aktien über Sektoren streuen**, nicht Technologie sammeln — aber der Effekt ist
   kleiner als erhofft (24 Aktien = 3,6 Wetten).
3. **Die Lücke sind Anleihen und Rohstoffe.** Ohne sie fehlt die halbe Streuung.
4. **Nicht die Instrumentenzahl feiern.** 49 Instrumente sind 7,4 Wetten. Wer „50
   Märkte" sagt und 7 meint, betrügt sich selbst.
