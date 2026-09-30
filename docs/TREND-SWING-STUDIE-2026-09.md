# Trend-Swing-Studie 2026-09 — Signale, wenn der Markt einfach steigt

**Vorab festgelegt am 30.09.2026, bevor der erste Lauf gerechnet wurde.** Was hier steht,
wird nach dem Lauf nicht mehr geändert. Das Ergebnis kommt unten in einen eigenen
Abschnitt „Ergebnis" — mit Datum.

## Anlass

Ozan, 30.09.: „die Cryptos waren komplett alle am Steigen und du hast mir nicht ein
Signal gegeben." Die Prüfung am Live-Scan bestätigt das: Bitcoin, Ethereum, Solana, XRP
und Dogecoin lagen über 63 Tage 30–61 % im Plus, über 21 Tage 4–15 % — und alle fünf
standen auf `NO_TRADE` mit „keine belastbare Invalidierung".

Die Ursache ist keine Panne, sondern Bauart: `bewerte_chart` bestimmt die Richtung nur aus
dem Regime je Zeitebene, und das Regime verlangt eine saubere Swing-Struktur (höhere Hochs
UND höhere Tiefs) plus Mindeststeigung. Ein gleichmäßiges Steigen ohne sauberen Ruecklauf
erfüllt das auf keiner Ebene → keine Richtung → keine Invalidierung → kein Trade.

In den 38 000 Scan-Zeilen der Signal-Studie (24 Coins, 19 Monate) hat der Scanner in
**54 %** aller Zeitpunkte gar keine Richtung — und in mehr als der Hälfte der Zeitpunkte,
in denen ein Coin über einen und drei Monate im Plus liegt, ebenfalls nicht. Das ist die
Lücke. Ob man sie schließen SOLLTE, ist eine andere Frage: das beantwortet diese Studie.

## Die Regel (fest)

„Trend-Swing, Long": einfache Zeitreihen-Momentum-Regel (Moskowitz/Ooi/Pedersen 2012;
im Projekt seit 04.09. als Kernfamilie festgelegt, `docs/STRATEGIE-ENTSCHEID-2026-09-04.md`).

- **Prüfzeitpunkt:** täglich 00:00 UTC, nur mit D1-Kerzen, die dann geschlossen waren.
- **Bedingungen, alle nötig:**
  1. Rendite über 63 Tageskerzen > 0
  2. Rendite über 21 Tageskerzen > 0
  3. Schlusskurs über dem EMA 50 (D1), EMA 20 über EMA 50
- **Einstieg:** zum Schlusskurs der letzten Tageskerze.
- **Anfangs-Stop:** Einstieg − 2,5 × ATR 14 (D1). 1 R = diese Strecke.
- **Nachziehen:** nach jeder Tageskerze Stop = max(bisheriger Stop, höchster Schluss seit
  Einstieg − 3 × aktueller ATR 14). Der Stop sinkt nie.
- **Ausstieg:** berührt ein Tagestief den Stop → raus zum Stop (öffnet die Kerze schon
  darunter: zum Eröffnungskurs). Spätestens nach 30 Tageskerzen zum Schluss.
- **Je Coin höchstens eine Position.** Neuer Einstieg frühestens am Tag nach dem Ausstieg.
- **Kosten:** 0,5 % vom Einstieg (Hin- und Rückweg), umgerechnet in R.
- **Werte:** die 24 Coins der Signal-Studie, Kerzen von data.binance.vision.

## Zeiträume (fest)

- **Vorperiode (VP):** Einstiege 01.01.2024 – 28.02.2025
- **In-Sample (IS):** Einstiege 01.03.2025 – 31.12.2025 (wie Signal-Studie)
- **Out-of-Sample (OOS):** Einstiege 01.01.2026 – 20.09.2026 (wie Signal-Studie)

Trades, die am Datenende noch offen sind, zählen im Hauptergebnis nicht; sie werden zum
letzten Schluss bewertet und getrennt ausgewiesen.

## Entscheidungsregel (fest)

Drei mögliche Ausgänge:

**A — Alarm freigeben** (Handy-Alarm „Trend-Swing", ausdrücklich halbe Positionsgröße
gegenüber einem A-Setup), wenn alle Punkte erfüllt sind:

1. Erwartungswert je Trade nach Kosten > 0 in **VP, IS und OOS**.
2. Mindestens 30 abgeschlossene Trades in OOS.
3. Cluster-Bootstrap auf OOS (Ziehen ganzer Einstiegsmonate mit Zurücklegen, 5 000
   Ziehungen, Seed 7): das 5-%-Quantil des Erwartungswerts liegt **über null**.

Der Monats-Bootstrap statt Einzeltrade-Bootstrap, weil Krypto-Trends gleichzeitig laufen
(mittlere Korrelation +0,70): 20 Einstiege in derselben Woche sind kein 20-facher Beleg.

**B — Nur anzeigen** (in der Markt-Liste als „Trend läuft" mit Stop-Vorschlag, KEIN
Handy-Alarm), wenn 1 und 2 erfüllt sind, 3 aber nicht.

**C — Verwerfen**, wenn 1 oder 2 nicht erfüllt ist. Dann bleibt es beim Scanner, und das
ist das Ergebnis — auch wenn es nicht das ist, was man hören will.

## Nur zur Einordnung (entscheidet nichts)

- Variante „nur Lücke": nur Einstiege, bei denen der Struktur-Scanner zum selben Zeitpunkt
  KEIN handelbares Long hatte.
- Buy & Hold derselben Coins im selben Zeitraum.
- Anteil der Coins mit positiver Summe in OOS.
- Größte Zahl gleichzeitig offener Positionen (Klumpenrisiko).
- Offene Trades zum letzten Schluss bewertet.

---

## Ergebnis (30.09.2026, erster und einziger Lauf)

`python3 scripts/trend_swing_studie.py --npz … --zeilen …` — Regel unverändert wie oben.

| Zeitraum | Trades | Erwartung je Trade | Summe | Treffer | Profitfaktor | Buy & Hold (Ø Coin) |
|---|---:|---:|---:|---:|---:|---:|
| VP 2024 – Feb. 2025 | 221 | **+0,65 R** | +144,5 R | 30 % | 2,34 | +32 % |
| IS März – Dez. 2025 | 128 | **−0,28 R** | −35,2 R | 25 % | 0,49 | −32 % |
| OOS Jan. – Sep. 2026 | 74 | **−0,02 R** | −1,4 R | 27 % | 0,97 | −6 % |

Monats-Bootstrap OOS, 90-%-Spanne: −0,60 bis +0,59 R.

**Urteil nach der Regel: C — verwerfen.** Punkt 1 (Erwartung > 0 in allen drei
Zeiträumen) ist nicht erfüllt: IS und OOS sind negativ. Punkt 3 ebenfalls nicht.

### Was das heißt

„Kaufen, wenn es steigt" hat in einem durchgehenden Bullenmarkt (2024) viel verdient und
im Jahr danach 35 R verloren — in Seitwärts- und Abwärtsphasen wird eine Trendregel
ständig ausgestoppt (IS: 98 von 128 Trades am Stop). Die Regel hätte Ozan im laufenden
Anstieg Signale gegeben, ja. Sie hätte ihm aber über die ganze Strecke 2025–2026 kein Geld
gebracht.

Die Lücke im Scanner ist damit real, aber sie ist **kein verpasster Gewinn**, den man mit
einer einfachen Trendregel einsammeln kann.

### Nur zur Einordnung

- Die 20 Trades, die am Datenende (25.09.) noch offen waren, stehen zum letzten Schluss bei
  +44 R (+2,2 R je Trade) — das ist genau der Anstieg, den Ozan gesehen hat. Sie zählen
  nach der Regel nicht, weil nicht abgeschlossen. Mit ihnen läge OOS bei rund +0,45 R je
  Trade. Das ist die Versuchung, die das Vorab-Dokument verhindern soll: ein einziger
  Schub, der noch nicht ausgestanden ist, würde das Urteil drehen.
- „Nur Lücke" (Scanner hatte kein Long): IS −0,26 R, OOS −0,04 R — gleiches Bild.
- 12 von 24 Coins in OOS positiv. Bis zu 24 Positionen gleichzeitig — ein reines
  Klumpenrisiko, weil alle Coins zusammen laufen.

### Was stattdessen passiert

Die Regel wird ab jetzt **vorwärts** mitgeschrieben (nur aufgezeichnet, nie gemeldet).
Vorwärtsdaten sind die einzigen, die noch niemand gesehen hat. Eine erneute Prüfung ist
erlaubt, sobald mindestens 60 neue abgeschlossene Trades vorliegen — mit genau dieser
Regel und genau dieser Entscheidungsregel.
