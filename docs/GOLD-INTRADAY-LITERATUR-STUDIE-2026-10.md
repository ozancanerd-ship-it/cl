# Gold-Tageshandel nach der Literatur — Studie 12.10.2026

Anlass — Ozan, 12.10. 15:19: Hauptfokus Swing, aber Gold als Tageshandel „anders angehen, anders
analysieren … recherchieren … dass wir realistisch damit Gewinn erzielen können".

Skript `scripts/gold_intraday_literatur_studie.py` · Ergebnis `docs/gold_intraday_literatur.json`.

## Recherche: was ist im Tageshandel überhaupt belegt?

| Quelle | Regel | Belegt wo | Gold? |
|---|---|---|---|
| Zarattini, Aziz & Barbon (2024), *Beat the Market*, SFI RP 24-97 | „Rauschzone" je Uhrzeit (14-Tage-Mittel der Bewegung seit Eröffnung); Ausbruch zur halben/vollen Stunde, Ausstieg an Grenze/VWAP, Schluss am Sitzungsende | SPY 2007–24, netto 19,6 % p. a., Sharpe 1,33 | nicht geprüft |
| Zarattini & Aziz (2023), *Can Day Trading Really Be Profitable?* | 5-Min.-Opening-Range-Breakout, Ziel 10 R | QQQ/TQQQ | nicht geprüft |
| Nachrechnung ORB auf 5 Indizes (mql5, 2026) | dieselbe Regel unverändert | brutto +0,05…+0,13 R, **netto nach Spread ≈ 0** | — |
| Holmberg, Lönnbark & Lundström (2013), *Finance Research Letters* | ORB mit Schwelle | Rohöl-Futures, Rendite > 0 | — |
| Gao, Han, Li & Zhou (2018) / Jin et al. (2020, J. Futures Markets) | erste halbe Stunde sagt letzte halbe Stunde voraus | US-Aktienindex; chin. Rohstoff-Futures | nein |
| FXStreet-Auswertung NFP (35 Termine) | Gold reagiert gegenläufig zur Überraschung (r ≈ −0,57 nach 15 Min., −0,45 nach 4 Std.) | nur Reaktion, Überraschung vorher unbekannt | ja |

Handelsplätze und Kosten: Kraken-Spot PAXG Einstiegsstufe 0,40–0,80 % Taker je Seite; Gold-Perps
(Binance XAUUSDT seit 01.2026, BitMEX XAUt) rund 0,02–0,05 % je Seite.

## Test auf Gold (PAXG 5 Min., 1.237 Sitzungen 08:30–15:00 New York, 01.2022–09.2026)

Regeln unverändert aus den Papieren, Eröffnung 08:30 ET (US-Daten, COMEX). IS 2022–24, OOS 2025–26.
Hauptfall Kosten 0,12 % je Runde (Gold-Perp, Marktorder + Slippage).

**Rauschzonen-Momentum (NM)** — Ø je Tag:

| | brutto | Limit-Perp 0,04 % | **Markt-Perp 0,12 %** | Kraken 0,80 % |
|---|---|---|---|---|
| IS | −0,023 % | −0,067 % | **−0,154 %** | −0,894 % |
| OOS | −0,004 % | −0,050 % | **−0,141 %** | −0,915 % |

Jedes Jahr 2022–2026 negativ. Schon **brutto kein Vorteil** — auf Gold läuft der Ausbruch aus der
Rauschzone nicht weiter wie bei SPY.

**5-Min.-ORB** — Ø je Trade in R: brutto IS −0,34 / OOS +0,12; mit 0,12 % Kosten −1,6 / −3,3 R. Der
Stop der ersten 5-Min.-Kerze ist im Median nur 0,105 % vom Kurs (≈ 4 $) — die Kosten einer Runde
sind größer als das ganze Risiko.

## Ergebnis

**Kein Edge** — wie die zwei Gold-Studien davor (Asien-Range/Sweep, 48-Std.-Ausbruch). Fünf
dokumentierte Tageshandels-Ansätze auf Gold, keiner ist nach realistischen Kosten im Plus.

Was das für die App heißt:
- Gold-Tageshandel bleibt **Lern- und Paper-Bereich**, keine Einstiegs-Alarme daraus.
- Gold-**Swing** (S9, 4 Std./Tag, 🧪) bleibt der Weg mit Aussicht — Kosten fallen dort kaum ins
  Gewicht.
- Der Day-Trading-Reiter zeigt diese Befunde als „Gold-Playbook" mit Erklärungen, damit man daraus
  lernt, statt nach Gefühl zu handeln.
