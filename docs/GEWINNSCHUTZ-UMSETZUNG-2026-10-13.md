# Gewinnschutz — was geändert wurde (13.10.2026)

Anlass: Ozan, 13.10. — Gewinnmitnahme soll rechtzeitig aus mehreren Signalen entstehen, nicht erst nach
einem großen Rückgang. Bestehende Logik wurde zuerst gelesen: `depot_stops.pflegen` (Wächter, 5-Minuten-Takt),
`gewinn_rueckgang` (reagiert erst nach ≥ max(8 Punkte, 35 %) Rückgang), `position_intel`/`exit_intel`
(R-basiert, für die Engine/Dashboard, nicht für das echte Depot). Es wurde **kein zweites System** gebaut:
der neue Baustein hängt im selben Wächter, im selben Stand und auf denselben Alarmwegen.

## Neu
* `src/trading_agent/portfolio_intel/gewinnschutz.py` — reine Bewertung: HOLD · PROFIT_PROTECTION ·
  PARTIAL_TAKE_PROFIT · EXIT. Signale: CHoCH/Swing-Low-Bruch, tieferes Hoch, Wick-Rejection, gescheiterter
  Ausbruch, RSI-Divergenz/Momentum, Volumen, D1-/H4-Trend gegen die Position, Abgabe vom Höchstgewinn,
  Quartalszahlen. Kein Signal entscheidet allein (PP ≥ 2 Signale, PARTIAL ≥ 3 inkl. Struktur, EXIT zusätzlich
  Trend dagegen + schlechtes Chance-Risiko). Intakte Struktur dämpft. Fehlendes steht in `fehlt`
  (H4/D1/Kursziel/News-Makro), zu wenig Daten ⇒ `UNBEWERTET`, kein Alarm. Teilverkauf mit echter Menge und Rest.
* `depot_stops._gewinnschutz` — zweistufig und restart-fest (`st["gs"]`): ein Urteil über HOLD gilt erst,
  wenn es nach einem weiteren abgeschlossenen Stundenschluss unverändert ist; ohne frische Stundenkerze nie
  bestätigt. Gemeldet wird nur die Verschlechterung. Neue Arten: `gewinnschutz`, `gewinnschutz_teil`,
  `gewinnschutz_exit` (Teil/Exit dringend).
* `scripts/depot_wache.py` — Kerzen (H1/H4/D1, abgeschlossen, frisch) für Kraken und Yahoo (H4 bei Aktien fehlt
  sichtbar), versiegelte Urteils-Datei `data/repository_real/live/depot_urteil.siegel` (nur bei inhaltlicher
  Änderung oder stündlich), Titel/Handlungen.
* App (`site/template.html`): Kasten „Gewinnschutz" im Depot-Reiter — Gewinn, Höchstgewinn, Rückgang,
  D1/H4-Bias, Schwächesignale, Restpotenzial, Aktion mit Menge, Neubewertungs-Bedingung und Wechsel-Level,
  „Was fehlt". Liest das versiegelte Urteil mit dem Auto-Abgleich-Schlüssel.
* `.github/workflows/daily.yml`: Urteils-Datei wird mitgesichert.
* Studie: `docs/GEWINNSCHUTZ-STUDIE-2026-10.md` (+ `-ERGEBNIS.json`), `scripts/gewinnschutz_replay.py`.

## Unverändert
Stop-Alarm (direkt), zweistufiger Teilverkauf, Nachkauf mit Risikotor, `gewinn_rueckgang` (Sicherheitsnetz),
Plan ⅓–⅓–⅓, Alarmzustände. Wiedereinstieg: kein neuer Code — der Nachkauf-/Wiedereinstiegs-Pfad verlangt
weiter ein neu berechnetes gültiges Setup (Entry/Stop/Ziele/CRV), nie nur einen Kursanstieg.

## Ergebnis der Studie (vorab festgelegte Regel)
V2 (bisher + Gewinnschutz) vs. V0 (bisher), OOS: +0,013 R/Trade, Bootstrap-5-%-Quantil −0,052 ⇒ **nicht
belegt**. Daher bleibt der Baustein Paper/Shadow-Hinweis. Siehe Studie.
