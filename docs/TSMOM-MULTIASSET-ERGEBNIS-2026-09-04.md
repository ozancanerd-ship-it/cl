# TSMOM Multi-Asset — Ergebnis (2026-09-04)

Test nach `docs/PRAEREGISTRIERUNG-TSMOM-MULTIASSET.md`. Universum, Regel, Split und
Kriterien standen fest, bevor der Code lief. Reproduzierbar:
`python3 scripts/tsmom_multiasset.py`.

---

## Verdikt: Hypothese verworfen

**Das primäre Kriterium ist nicht erfüllt.** OOS-Sharpe +1,08 bei p = 0,0814 — die
Schwelle lag bei p < 0,0000618. Nicht einmal nominal auf 5 % signifikant.

Nach der Registrierung heißt das: kein Nachjustieren des Universums, kein anderer Split,
keine Gewichtungsvariante. `SETUP-TSMOM-ENSEMBLE-01` bleibt `in_validation` / SHADOW.

## Was der Test trotzdem gezeigt hat

**Die Diversifikations-Hypothese ist deskriptiv bestätigt.** Mittlere paarweise
Korrelation der Tagesrenditen:

| Universum | mittlere Korrelation |
|---|---:|
| nur Krypto | **+0,704** |
| Multi-Asset (13 Instrumente, 4 Klassen) | **+0,220** |

**Und die Wirkung ist erheblich:**

| | Sharpe | CAGR | Volatilität | Max. Drawdown |
|---|---:|---:|---:|---:|
| Gesamt TSMOM | 1,38 | 14,1 % | 10,0 % | 17,1 % |
| Gesamt Buy & Hold | 1,32 | 47,7 % | 34,0 % | 50,4 % |
| **OOS TSMOM** | **1,08** | 9,4 % | **8,7 %** | **8,4 %** |
| OOS Buy & Hold | 0,77 | 19,5 % | 28,2 % | 31,2 % |

Zum Vergleich Runde 1 auf reinem Krypto: OOS-Sharpe **−0,21**. Derselbe unveränderte Code,
dasselbe Split-Datum, nur ein gestreutes Universum — und der OOS-Sharpe springt von −0,21
auf +1,08, bei einem Drawdown von 8,4 % statt 31,2 %.

**Sekundärkriterium erfüllt:** 1,08 gegen 0,77 für Buy & Hold.

## Warum es trotzdem nicht reicht — und das ist der eigentliche Befund

Der Test scheitert nicht am Ergebnis, sondern an der **Stichprobenlänge**. Bei einem
OOS-Sharpe von 1,08 auf 606 Tagen ist t = 1,39. Das ist zu wenig, egal wie gut die Zahl
aussieht.

Wie viele Jahre Out-of-Sample ein gegebener Sharpe für Signifikanz braucht:

| Sharpe | nominal 5 % | nominal 1 % | Bonferroni (809 Versuche) |
|---:|---:|---:|---:|
| 0,50 | 10,8 J | 21,6 J | 58,9 J |
| 0,80 | 4,2 J | 8,5 J | 23,0 J |
| 1,00 | 2,7 J | 5,4 J | 14,7 J |
| **1,08** | **2,3 J** | 4,6 J | **12,6 J** |
| 1,38 | 1,4 J | 2,8 J | 7,7 J |
| 2,00 | 0,7 J | 1,4 J | 3,7 J |

Wir haben 1,66 Jahre OOS. Für nominale Signifikanz fehlen **0,7 Jahre**. Für die
korrigierte Schwelle fehlen **11 Jahre**.

**Das ist keine Eigenschaft dieser Strategie. Es ist eine Eigenschaft der Statistik.**
Eine Strategie mit Sharpe um 1 lässt sich in vertretbarer Zeit nicht beweisen — und mit
809 bereits verbrauchten Versuchen erst recht nicht.

## Was daraus folgt

Der bisherige Plan war: *validieren, dann Geld*. Dieser Test zeigt, dass die Validierung
im strengen Sinn **nie eintreten wird**. Wer auf statistische Signifikanz bei Sharpe 1
wartet, wartet über ein Jahrzehnt. Das ist kein akzeptabler Plan — aber "dann eben ohne
Beweis" ist es genauso wenig.

Der Ausweg ist eine andere Art von Sicherheit. Nicht *Beweis vorher*, sondern
**begrenztes Risiko plus Abbruch bei Verschlechterung**:

1. **Starker externer Prior.** Die Hypothese stammt nicht aus diesen Daten. Moskowitz/Ooi/
   Pedersen (JFE 2012) über 58 Instrumente und 25 Jahre; Han/Kang/Ryu für Krypto. Das
   ersetzt keinen Beweis, aber es unterscheidet diese Regel fundamental von SMC, wo es
   keine einzige unabhängige Bestätigung gibt.
2. **Vorab-Registrierung als Dauerzustand.** Parameter eingefroren, jede Änderung im
   Register, Gate im Daemon.
3. **Risiko so klein, dass ein Irrtum folgenlos bleibt.** Nicht "wie viel können wir
   verdienen", sondern "wie viel dürfen wir verlieren, ohne dass es wehtut".
4. **Laufende Degradations-Überwachung statt einmaliger Freigabe.** `edge_health_check.py`
   existiert bereits. Kriterium und Abbruchschwelle gehören vorab festgelegt.
5. **Forward-Daten sind die einzigen sauberen Daten, die noch kommen.** Alles Historische
   ist inzwischen mehrfach angesehen.

Das ist keine Aufweichung der Regeln. Es ist die ehrliche Konsequenz daraus, dass die
ursprüngliche Regel — "erst beweisen" — auf ein Kriterium hinauslief, das mathematisch
unerreichbar ist.

## Was NICHT getan wurde

* Das Universum wurde nach dem Ergebnis nicht verändert.
* Der Split wurde nicht verschoben.
* Die Korrektur-Familie wurde nicht neu definiert. Die Frage, ob 809 SMC-Versuche eine
  vorab registrierte, literaturgestützte Hypothese belasten sollten, ist methodisch
  berechtigt — sie **nach** einem günstigen Ergebnis zu stellen wäre es nicht. Sie gehört
  vor den nächsten Test, schriftlich, mit einer Begründung, die vom Ausgang unabhängig ist.

## Ein eigener Fehler, protokolliert

Der erste Lauf zeigte OOS-Sharpe 1,33. Beim Nachprüfen fiel auf, dass die Handelskosten
zweimal durch die Zahl der Instrumente geteilt wurden — sie waren um Faktor 13 zu niedrig.
Nach der Korrektur: 1,08. Alle Zahlen oben sind die korrigierten.

Genau dieser Fehlertyp — zu niedrig angesetzte Kosten — hat die SMC-Familie jahrelang gut
aussehen lassen (Befund F4).
