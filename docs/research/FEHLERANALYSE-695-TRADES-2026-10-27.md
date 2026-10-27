# Fehleranalyse der 695 Paper-Trades (Stand 2026-10-27)

Reproduzierbar: `python3 scripts/verlustquellen.py` (baut auf `trade_analyse.laden()`, gleiche Trades wie die Bilanz). Nur Paper/Shadow. Intervalle sind 95 %-Normalnäherung auf Trade-Ebene; Trades am selben Tag sind korreliert, die Intervalle sind daher eher zu eng. Gruppen mit n < 30 tragen keine Aussage.

## Inventar (nichts davon wurde wiederholt)
Vorhanden und gelaufen: Bilanz-Nachrechnung (`verifiziere_bilanz.py`), `trade_analyse.py` (Aufschlüsselung nach Setup/Richtung/Klasse/Entry/Note/Stop-Distanz/Dauer/Score/Uhrzeit), `strategie_walkforward.py` (kein Filter besteht OOS), 15 Studien in `docs/` (Signal, Gegenthese, Funding, Halte-Stop, Trend-Swing, Liquidität, Mean-Reversion, Stundenprofil, Einstieg-Faktoren, Ausstieg, Ausstieg-Trailing, Gewinnschutz, Daytrade, Gold, Universum). Versuchsregister `config/hypothesis_registry.json`: Stand 2026-09-04, 820 Konfigurationen, 217 Einträge. Alle späteren Studien (September/Oktober) sind dort nicht erfasst, die Zahl der Versuche ist also zu niedrig angesetzt.

## Neue Fragestellung
Wo genau entsteht der Verlust, und welcher Teil ist ein Messartefakt statt Handelsverlust?

## Befund 1 (belegt): 67 Sofort-Stops sind Messartefakte
- 67 Trades haben identischen Start- und Endzeitstempel, MFE 0, MAE −1,05 bis −1,3, Ausgang Stop, immer −1,0 R: Summe −67,0 R. 65 Krypto, 2 Gold. Alle zwischen 06.09. und 11.09. (38 allein am 10.09.). Kein einziger wurde aufs Handy gemeldet.
- Mechanismus (im Code nachgewiesen, `Wachliste.pruefen`): `watch_levels.py` nimmt neue Setups zur Zeit `jetzt` auf und prüft danach Hoch/Tief im Fenster `_seit(...)..jetzt`; das Fenster beginnt vor der Aufnahme. Ein Test mit einer frisch angelegten Wache und einem Tief im Fenster unter dem Stop liefert sofort `STOP`.
- Nicht belegt: ob jeder der 67 genau so entstand (Rohkerzen vom 06.–11.09. liegen nicht vor) und warum seit dem 12.09. keiner mehr auftritt. Naheliegend, aber ungeprüft: Marktsturz-Tage; der Codepfad besteht weiter.
- Wirkung: Brutto −122,9 R → −55,9 R (n = 628). Netto (Annahme) −182,4 R → −112,5 R. PF brutto 0,64 → 0,80.

## Befund 2 (belegt): die Alarm-Sperre „Krypto Long“ stammt allein aus diesen Artefakten
`alarm_tor.bilanz` mit und ohne die 67: `klasse_richtung:krypto|long` 249 Trades, −44,3 R, gesperrt → 192 Trades, +12,7 R, offen. Alle anderen Gruppenurteile bleiben gleich (Aktien Long/Short gesperrt, Rücksetzer im Trend gesperrt, Rückeroberung bewährt). Praktische Wirkung heute klein, weil bewährte Setups die grobe Sperre überstimmen und `NUR_BEWAEHRT` gilt; die in der App angezeigte Begründung ist aber falsch.

## Befund 3 (belegt): der echte Verlust liegt bei Aktien
Ohne Artefakte: Krypto −2,5 R aus 469 (Mittel −0,005, nicht von 0 unterscheidbar), Aktien −47,6 R aus 139 (Mittel −0,343 ± 0,157), Gold −5,7 R aus 20 (n < 30).
- Aktien Short −38,5 R aus 77 (Mittel −0,50 ± 0,17); Aktien Long −9,1 R aus 62 (nicht unterscheidbar).
- „Rücksetzer im Trend“ bei Aktien −35,2 R aus 63 (Mittel −0,56 ± 0,19, Trefferquote 17,5 %); dasselbe Setup bei Krypto n = 16, keine Aussage.
- Einstieg „Rücklauf in FVG H4“ bei Aktien −25,4 R aus 55.
- Aktien-Ausgänge: 85 Stops (−70,0 R), 10 Ziele (+20,0 R), 44 invalidiert (+2,4 R).
- Diese Gruppen überlappen stark und wurden nachträglich ausgewählt (viele Schnitte angesehen). Sie sind Hypothesen aus Stichprobendaten, kein Nachweis.

## Befund 4 (belegt als Beobachtung, Ursache offen): Aktien-Stops liegen an der US-Eröffnung
60 von 85 Aktien-Stops (71 %) fallen auf 13–15 UTC, zusammen −48,7 R. 42 davon betreffen Trades, die in der Sitzung begannen, also über Nacht gehalten wurden. Der Start-Zeitpunkt (Sitzung vs. außerhalb) trennt die Ergebnisse nicht (−0,33 vs. −0,38 R). Ob echte Eröffnungs-Gaps oder die Prüflogik (erste Prüfung nach Börsenschluss) die Stops auslöst, lässt sich mit den gespeicherten Daten nicht trennen: Gap-Größe und Vortagesschluss fehlen. Die Buchung zum Füllkurs ändert wenig (Aktien-Stops −70,0 → −74,8 R, gesamt −47,6 → −47,1 R).
Literaturbezug: Lou/Polk/Skouras (E06) beschreibt Nacht- und Taganteil der Aktienrendite; Übertragbarkeit auf unsere Signale ist nicht belegt.

## Befund 5 (belegt): Kosten sind die zweite große Größe, aber eine Annahme
Auf den 628 Trades ergeben die angenommenen Kosten (Krypto 0,35 %, Aktien 0,10 %, Gold 0,05 % Roundtrip) −56,6 R, so viel wie der gesamte Brutto-Verlust. Gemessen sind sie nicht. Bei Stops unter 1 % Abstand steigt der Netto-Verlust von −0,28 auf −0,68 R je Trade (n = 29, knapp unter der Aussagegrenze).

## Befund 6 (belegt): einziges positives Fach ist wackelig
„Rückeroberung nach Liquiditätsgriff“ bei Krypto: +23,5 R aus 186, Mittel +0,126 ± 0,141, netto +0,036 je Trade. Je Kalenderwoche schwankt es von −12,3 R (KW40) bis +19,5 R (KW41). Nicht von 0 unterscheidbar.

## Nicht untersuchbar (Daten fehlen)
Marktregime, HTF/MTF-Trend, Spread, echte Gebühren, Gap-Größe, Referenzmarkt (BTC/S&P) je Trade; Kerzenrohdaten der Aufnahmefenster vom 06.–11.09.; Setup-Name bei 300 Trades („ohne“, 233 ohne Artefakte) – überwiegend frühe Phase.

## Priorisierte Empfehlungen (max. 3; noch nichts umgesetzt)
1. **Messkorrektur.** Neue Wache erst gegen Kurse ab `aufgenommen` prüfen (Fensterbeginn = max(seit, aufgenommen)); Altfälle als `messartefakt` markieren und aus Bilanz und Alarm-Tor ausnehmen (markieren, nicht löschen). Begründung: Befund 1 und 2. Daten: vorhanden. Validierung: Regressionstest (Beispiel oben), Replay mit `signal_replay.py` mit/ohne Fix auf denselben Daten, Vergleich der Gate-Urteile; Erfolg = keine Wache mit Start = Ende, Krypto-Long-Sperre fällt. Ändert die Paper-Statistik und das Gate, keine Handelsregel; braucht deine Freigabe.
2. **Kostenannahme belegen.** Offizielle Gebührenseiten (Kraken, Trade Republic) und echte Spreads/Fills heranziehen, dann Sensitivität ±50 %. Begründung: Befund 5, entscheidet, ob die Rückeroberung netto trägt. Daten: Doku-Quellen noch zu verifizieren. Validierung: Unit-Test gegen die Gebührentabelle, Bilanz mit alter vs. neuer Annahme.
3. **Aktien-Eröffnungsrisiko messen, nicht raten.** Bei Aktien-Stops Vortagesschluss, Eröffnungskurs und Gap speichern. Erst bei ≥ 30 neuen Aktien-Stops zwischen Stop-Puffer und keiner Übernacht-Position entscheiden, vorab registrierte Regel, nur Vorwärtsdaten (die Hypothese stammt aus den bisherigen Daten). Begründung: Befund 3 und 4. Aktien-Setups sind für das Handy bereits gesperrt, der Nutzen liegt in besserer Entscheidungsgrundlage.
Zurückgestellt: Deflated-Sharpe und Versuchszähler (E01/E02) erst, nachdem das Register nachgezogen ist (Studien nach dem 04.09.); sie ersetzen diese Analyse nicht.
