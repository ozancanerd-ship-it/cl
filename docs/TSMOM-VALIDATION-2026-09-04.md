# TSMOM — Pruefkette, erste vollstaendige Runde (2026-09-04)

Geprueft wurde die **ausgelieferte** Regel `SETUP-TSMOM-ENSEMBLE-01`
(`src/trading_agent/strategy/setups/tsmom.py`), nicht eine Nachbildung.
Reproduzierbar: `python3 scripts/tsmom_validation.py`.

Panel: BTC, ETH, SOL, BNB, XRP, DOGE, LINK auf D1 (Repository, 2023-01 bis 2026-07).
Split 2025-01-01. Kosten 0,2 % je Positionswechsel. Parameter eingefroren.

---

## Verdikt

**Die vorab gestellte Frage lautete: ist der absolute OOS-Ertrag signifikant positiv,
korrigiert fuer die Zahl der Versuche? Die Antwort ist Nein.**

| | |
|---|---|
| OOS-Sharpe (gepoolt) | **−0,21** |
| 95 %-Bootstrap-Intervall | **[−0,82, +0,36]** — schliesst die Null ein |
| OOS p-Wert | **0,758** |
| Bonferroni-Schwelle bei 807 Versuchen | p < 0,000062 |
| Deflated Sharpe Ratio | **0,000** |
| Symbole mit positivem OOS-Sharpe | **2 von 7** |

`SETUP-TSMOM-ENSEMBLE-01` bleibt `in_validation` und damit SHADOW. Keine Freigabe.

## Was trotzdem gehalten hat

Der Befund ist nicht dasselbe wie bei der SMC-Familie. Dort war das Ergebnis
*signifikant negativ* (−0,196 R bei t = −3,71 auf 958 Trades). Hier ist es
*nicht von Null unterscheidbar* — und drei Achsen sehen weiterhin gut aus:

| Achse | Ergebnis |
|---|---|
| Sharpe > Buy & Hold (Gesamtzeitraum) | **7 von 7 Symbolen** |
| Maximaler Drawdown < Buy & Hold | **7 von 7 Symbolen** (24–36 % statt 53–85 %) |
| Parameter-Robustheit (9 Varianten) | Sharpe **0,75 bis 0,88**, keine negativ, kein Abriss |
| Jahre mit positivem Sharpe | 3 von 4 |

Eine Regel, die bei jeder Parameterstoerung zwischen 0,75 und 0,88 bleibt, verhaelt sich
grundlegend anders als die SMC-Kette, wo jeder zusaetzliche Filter das Ergebnis wild
verschob (Korrelation log(n) vs. Expectancy = −0,574).

## Woran der OOS-Test scheitert — die Jahresdiagnose

Geometrisches Mittel je Symbol, Strategie gegen einfaches Halten:

| Jahr | Wechsel/Symbol | im Markt | Markt | Strategie | Delta |
|---|---:|---:|---:|---:|---:|
| 2023 | 7,7 | 85,2 % | +73,6 % | +35,6 % | **−38,0 pp** |
| 2024 | 13,7 | 90,4 % | +115,7 % | +51,4 % | **−64,3 pp** |
| 2025 | 10,9 | 84,9 % | −24,8 % | −1,3 % | **+23,6 pp** |
| 2026 | 6,5 | 52,8 % | −36,7 % | −9,2 % | **+27,5 pp** |

Das ist exakt das dokumentierte Verhalten von Trendfolge: **Sie gibt im Bullenmarkt
Aufwaerts ab und schuetzt im Baerenmarkt.** 2026 hat die Regel korrekt de-risked — Zeit im
Markt faellt auf 52,8 %, der Umschlag halbiert sich, der Verlust bleibt bei 9,2 % statt
36,7 %.

Das OOS-Fenster (2025 + 2026) faellt vollstaendig in einen fallenden Kryptomarkt. Eine
defensive Regel hat dort per Konstruktion einen leicht negativen absoluten Ertrag — und
genau den misst der Test.

## Die ehrliche Trennung

Hier ist die Versuchung gross, das Kriterium nachtraeglich zu wechseln: "gemessen an Buy &
Hold war OOS doch stark positiv". Das waere genau der Fehler, den dieses Projekt gerade
teuer gelernt hat.

Deshalb sauber getrennt:

1. **Vorab gestellte Frage — absoluter OOS-Ertrag signifikant positiv?** → **Nein.**
   p = 0,758. Das ist das Ergebnis dieser Runde, und es steht.
2. **Nachtraeglich sichtbar gewordene Frage — schlaegt die Regel das Halten?** → sieht OOS
   deutlich besser aus (+23,6 pp und +27,5 pp). Aber diese Frage war **nicht vorab
   registriert**. Ihre Antwort ist damit eine **Hypothese fuer die naechste Runde**, kein
   Resultat dieser.

## Nachtrag: Historie verdoppelt, OOS-Ergebnis unveraendert

Nach der ersten Runde wurde die Historie fuer BTC, ETH und BNB bis 2019 zurueck ingestiert
(BTC/ETH jetzt 2749 D1-Bars ueber 7,6 Jahre statt 1308 ueber 3,6). Das In-Sample-Fenster
waechst damit von 3850 auf 7456 Tage.

**Der OOS-Wert hat sich um keine Stelle veraendert:** Sharpe −0,21, Intervall
[−0,82, +0,36], p = 0,758.

Das ist kein Zufall, sondern eine Eigenschaft der Regel: **weil nichts gefittet wird, kann
zusaetzliche In-Sample-Historie das Out-of-Sample-Ergebnis nicht beeinflussen.** Bei der
SMC-Familie war das Gegenteil der Fall — dort verschob jede Datenaenderung alle Zahlen
(14 von 15 Setups wurden schlechter, als die fehlenden 13 Monate dazukamen).

### Was die acht Jahre zeigen

BTC, ETH und BNB, Strategie gegen einfaches Halten:

| Jahr | im Markt | Markt | Strategie | Delta |
|---|---:|---:|---:|---:|
| 2019 | 77,9 % | −44,7 % | −18,0 % | **+26,6 pp** |
| 2020 | 92,6 % | +386,1 % | +111,6 % | −274,5 pp |
| 2021 | 98,3 % | +176,8 % | +45,6 % | −131,2 pp |
| 2022 | 65,1 % | −61,2 % | −20,3 % | **+40,9 pp** |
| 2023 | 87,1 % | +91,0 % | +41,2 % | −49,8 pp |
| 2024 | 94,0 % | +97,6 % | +50,7 % | −46,9 pp |
| 2025 | 89,1 % | +1,9 % | +10,4 % | **+8,5 pp** |
| 2026 | 59,7 % | −32,6 % | −8,2 % | **+24,3 pp** |

**In allen drei Baerenjahren hat die Regel deutlich geschuetzt** (+26,6, +40,9 und
+24,3 Prozentpunkte). **In allen fuenf Bullenjahren ist sie zurueckgeblieben.** Das Muster
ist ueber acht Jahre und drei Assets ohne Ausnahme konsistent: die Regel schneidet beide
Enden ab.

Damit ist beschrieben, **was** sie tut: sie ist eine Risiko-Reduktions-Regel, keine
Rendite-Steigerungs-Regel. Genau deshalb misst das vorab gestellte Kriterium — absoluter
Ertrag in einem OOS-Fenster, das komplett aus einem fallenden Markt besteht — an ihr vorbei.

**Und genau deshalb darf das Kriterium jetzt nicht gewechselt werden.** Diese Tabelle ist
gesehen worden; jeder Test, den ich jetzt auf diesen Daten formuliere, ist kontaminiert.
Die relative Frage laesst sich auf diesem Datensatz nicht mehr sauber beantworten — nur
noch vorwaerts.

## Was daraus folgt

**Neue Vorab-Registrierung fuer die naechste Runde.** Die Kennzahl muss zum Zweck der Regel
passen. Eine defensive Allokationsregel misst man nicht am absoluten Ertrag in einem
Baerenmarkt, sondern gegen die Alternative. Vorzuschlagen:

* Primaer: **risikoadjustierter Ueberschuss gegenueber Buy & Hold** (Differenz der Sharpes,
  gepoolt ueber Symbole), einseitig getestet.
* Sekundaer: **Drawdown-Reduktion** gegenueber Buy & Hold.
* Vorab festzulegen **bevor** neue Daten gesehen werden — sonst ist es wieder Selektion.

**Laengere Historie.** Das Repository beginnt 2022-12-31. Der Erstbefund ueber 2017–2026
(vier Symbole, Sharpe 0,86 gegen 0,76) hatte zwei volle Zyklen; diese Pruefung hat
anderthalb. Ein OOS-Fenster, das ausschliesslich aus einem fallenden Markt besteht, ist
effektiv **eine** Regime-Beobachtung, nicht zwanzig Monate unabhaengige Evidenz.
`scripts/ingest_panel.sh` und ein Ingest ab 2017 sind der naechste Schritt.

**Forward-Paper bleibt der eigentliche Test.** Beide Fragen — absolute und relative — lassen
sich nur mit Daten beantworten, die es noch nicht gibt.

## Was NICHT getan wird

* Keine Parameteranpassung, um den OOS-Test zu bestehen. Die neun Robustheitsvarianten sind
  bereits als Versuche im Register (`config/hypothesis_registry.json`, jetzt 807
  Konfigurationen). Eine zehnte, die "endlich passt", waere ein Zufallstreffer.
* Kein Wechsel des Split-Datums. Genau das ist bei der SMC-Forschung ueber 14 Laeufe
  passiert (Befund F11) und hat die OOS-Aussage wertlos gemacht.
* Keine Freigabe. `in_validation` / SHADOW bleibt.

## Einordnung

Diese Runde hat funktioniert. Sie hat eine Strategie, die auf dem Gesamtzeitraum gut
aussieht (7 von 7 Symbolen besser als Halten, Drawdown halbiert, robust ueber neun
Parametervarianten), **daran gehindert, live zu gehen** — weil das vorab gestellte
Kriterium nicht erfuellt ist.

Genau dafuer ist die Kette da.
