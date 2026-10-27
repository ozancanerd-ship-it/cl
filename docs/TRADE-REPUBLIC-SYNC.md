# Trade-Republic-Abgleich (pytr, nur lesend)

Inoffiziell: pytr nutzt die private App-Schnittstelle (nicht von Trade Republic autorisiert, kann nach App-Updates brechen, Restrisiko Kontoeinschraenkung). Login und Handy-Bestaetigung macht nur du.

1. `uv tool install pytr` (oder `pipx install pytr`)
2. `pytr login --v2 --store_credentials` (Push in der TR-App bestaetigen). Sitzung/Cookies liegen unter `~/.pytr/`.
3. `python3 scripts/tr_sync.py --pull --out ~/tr_depot.txt`
4. App -> Sync -> Depot-Text einfuegen -> Abgleichen. Trade Republic gilt als vollstaendig beschrieben: Fehlendes wird als verkauft angezeigt, nie still geloescht; Stop/Ziele bleiben.

Nicht enthalten: Basiswert und Knock-out der Scheine, Gebuehren. Ablaufende Sitzung = Abbruch mit Hinweis (kein stiller Altstand). Stand der Pruefung: Format aus dem pytr-Quelltext gelesen, mit echten Daten noch nicht getestet.
