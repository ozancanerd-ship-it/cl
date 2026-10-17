# Hebelscheine im Wächter und in der App (17.10.2026)

## Gefundene Fehler
1. **Wächter (`depot_stops.pflegen`):** Bei Scheinen (Ebene „basis“) wurde der Gewinn als
   `Basiskurs / Einstiegspreis des Scheins` gerechnet — gemischte Einheiten (z. B. 250 $ gegen 4,80 €
   ⇒ „+5.100 %“). Betroffen: Hinweis „überdehnt und deutlich im Plus“, `gewinn_rueckgang`, Höchstgewinn.
2. **App (`hebelLage`):** Fehlte das Bezugsverhältnis, galt still 1. Wer die Trade-Republic-Schreibweise
   „0,1“ eintrug, bekam einen Scheinpreis um den Faktor 100 daneben (die App teilte durch 0,1).
3. **Gewinnschutz** lief nur für Positionen auf Positions-Ebene, nicht für Scheine.

## Änderungen
* `depot_stops.schein_produkt`: Scheinpreis = (Basiskurs − Knock-out) / Bezugsverhältnis in Positionswährung;
  Bezugsverhältnis < 1 gilt als Faktor (TR), ≥ 1 als Teiler; fehlt es, wird es aus `wert_manuell`/Stückzahl
  abgeleitet, sonst bleibt der Scheinpreis **unbekannt**. Dazu der Basis-Einstand (Basiskurs, bei dem der
  Schein auf dem Einstieg stünde).
* Gewinn eines Scheins = Scheinpreis gegen Einstiegspreis. Alter Höchstgewinn eines Scheins wird einmalig
  verworfen (`gv_schein_v1`), damit kein Fehlalarm aus den alten Zahlen entsteht.
* Gewinnschutz für Scheine: Struktur aus den Basiswert-Kerzen, Gewinn aus dem Schein, zusätzliches Signal
  „Knock-out-Puffer < 15 %“, Trailing-Vorschlag nie jenseits des Knock-outs. Ohne Scheinpreis: `UNBEWERTET`
  mit dem fehlenden Feld im Text.
* App: `scheinTeiler` (gleiche Regel), `ratio_fehlt` statt still 1, Hinweis in der Hebel-Box, Eingabefeld
  und Erklärtext korrigiert (Bezugsverhältnis ist für Wert und Gewinn nötig).

## Tests
`tests/unit/test_gewinnschutz.py`: Scheinpreis/Basis-Einstand, kein Fehlalarm aus altem Höchstgewinn,
kein stilles Verhältnis 1 (+ Ableitung aus eingetragenem Wert), Gewinnschutz am Basiswert mit Knock-out-Signal,
„0,1“ = „10“ in Python und in der App (Node).
