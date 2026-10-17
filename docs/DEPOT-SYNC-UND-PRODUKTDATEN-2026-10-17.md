# Depot-Abgleich, Produktdaten, gemeinsames Risiko (17.10.2026)

Stand der Prüfung und was sich geändert hat. Keine Positionsdaten in dieser Datei (das Repository ist öffentlich).

## 1. Wie der Depot-Abgleich funktioniert (aus dem Code gelesen)

Das Depot liegt nur im Browser (localStorage). Der Wächter auf GitHub (`scripts/depot_wache.py`) kennt es auf zwei Wegen:

| Weg | Voraussetzung | Wirkung |
|---|---|---|
| **A. Geheimnis `DEPOT_CODE`** | In der App *Sync → Depot kopieren*, den Text als GitHub-Geheimnis `DEPOT_CODE` einsetzen | Der Wächter kennt den Stand vom Zeitpunkt des Kopierens. Jede spätere Änderung braucht ein neues Einsetzen. |
| **B. Auto-Abgleich** | Fine-grained GitHub-Token (nur Repo `cl`, *Contents: Read and write*) **und** ein Schlüssel, beides in der App eingetragen; derselbe Schlüssel als Geheimnis `DEPOT_SCHLUESSEL` | Die App lädt das Depot nach jeder Änderung versiegelt als `data/repository_real/live/depot_sync.siegel` hoch. Es gilt, sobald es sich mit dem Schlüssel öffnen lässt. |

**Befund 17.10.:** Im Repository liegt **keine** `depot_sync.siegel`. Die App hat also nie hochgeladen; Weg B ist nicht eingerichtet. Der Wächter arbeitet mit dem Text aus `DEPOT_CODE` (Fingerabdruck seit mindestens 13.10. unverändert). Ob die Trade-Republic-Positionen darin stehen, ist von außen nicht prüfbar. Geheimnisse sind von hier nicht lesbar und werden nicht gesetzt.

**Alter Stand, neuer Stand:** Wird `DEPOT_CODE` neu gesetzt, ändert sich der Schlüssel des Wächter-Standes; der Wächter beginnt dann neu (`alter Wächter-Stand passt nicht … es wird neu begonnen`). Einzelne bereits gemeldete Hinweise können dabei einmal erneut erscheinen.

## 2. Keine Verwechslung, keine Doppelzählung

* **Identität = ISIN (neu).** Ist eine gültige ISIN eingetragen (Prüfziffer wird geprüft, App und Wächter gleich), gilt sie beim Zusammenführen vor allem anderen. Zwei Scheine auf denselben Basiswert mit verschiedener ISIN bleiben zwei Posten; derselbe Schein unter zwei Namen wird einer.
* **Ohne ISIN** gilt weiter der alte Weg (Basiswert + Konto bzw. Kürzel + Konto). Zwei Scheine auf denselben Basiswert ohne ISIN können dabei fälschlich als einer gelten. Deshalb die ISIN eintragen.
* **Wächter (neu):** `depot_bereinigen` entfernt Doppelte (gleiches Kürzel+Konto oder gleiche ISIN); der spätere Eintrag gewinnt, im Protokoll steht nur ein Zähler.
* **Verkauft?** Ein Abgleich-Code mit `k` (Konten) markiert Fehlendes als verkauft — das bleibt eine Anzeige mit eigenem Knopf, nie ein stilles Löschen.

## 3. Produktdaten der Hebelscheine

Neue Box „Produktdaten der Hebelscheine“ (Reiter Portfolio): je Schein Basiswert, ISIN, Bezugsverhältnis, Stückzahl, Einstiegskurs je Schein, aktueller Scheinkurs, Knock-out. Fehlendes ist rot benannt, Platzhalter sind als Platzhalter gekennzeichnet.

* Ein Basiswert wird **nicht** aus Logo oder Namen festgelegt. Neues Feld `basis_offen` verhindert das Raten (App und Wächter).
* Eine Platzhalter-Stückzahl (`platzhalter: true`, „1 = ganze Position“) leitet **kein** Bezugsverhältnis aus dem eingetragenen Wert ab.
* Neues Eingabefeld ISIN im Formular „Position eintragen“.

## 4. S&P 500

Der Index steht nicht im Scan, aber in der Makro-Messung (`makro.werte`, Yahoo `^GSPC`, **Tagesschluss**). Ein Schein mit Basiswert `SPX` rechnet jetzt damit (Wächter und App). Es ist **kein Livekurs**: die App weist „Vortagesschluss“ aus und zeigt keine Chartanalyse für den Index. Ohne Makro-Messung gibt es keinen Kurs (nichts erfunden).

## 5. Gemeinsames Risiko (neu in der App)

* Anteil des Depots, der belegbar am US-Aktienmarkt hängt (Aktien + Scheine mit bekanntem Basiswert).
* Gleichzeitiger Rückgang aller Basiswerte um 5/10/15/20/30 %: Restwert je Schein = Wert × (K·(1+s) − KO)/(K − KO), Verlust in € und % des Depots, welche Scheine ausgeknockt wären. Angenommen sind Beta 1 und Korrelation 1 (pessimistisch, nicht gemessen); ohne Finanzierungskosten, Spread und Kurslücken.
* Scheine mit unbekanntem Basiswert sind **nicht** eingerechnet, sondern gesondert ausgewiesen (im ungünstigen Fall ist der Einsatz weg).
* Abstand zum Knock-out und Hebel je Schein. Der bestehende Stresstest (gemessene Betas, 90 Tage) bleibt daneben unverändert.

## 6. Aktien-Universum: tatsächliche Grenzen

* Feste Liste von 104 Einzelwerten in `scripts/build_scan_data.py::AKTIEN` (keine ETFs); kein Index-Abruf.
* Beim letzten Lauf waren 94 im Scan, 10 ausgefallen (NOW, COIN, MSTR, LLY, JPM, ADBE, AMAT, LRCX, MRK, VRTX). **Warum** sie ausfielen, war bisher nirgends gespeichert; ab jetzt schreibt der Scan `angefragt` und `ausfall_namen` (mit Grund) in `scan.json` und die App zeigt sie unter System → Aktien-Universum.
* Yahoo liefert nur zu Börsenzeiten Aktienkerzen, keine H4-Zeitebene.
* Eine Erweiterung (z. B. S&P-500-Mitglieder) braucht (a) eine verlässliche Mitgliederquelle, die der Lauf erreichen darf, (b) einen gestuften Lauf: grober D1-Filter über viele Werte, Detailanalyse nur für die besten. Der heutige Lauf braucht für 104 Werte ca. 8 Minuten; 500 Werte ohne Stufung würden das Zeitbudget sprengen. **Nicht umgesetzt.**

## 7. Gewinnschutz an den echten Positionen

**Nicht prüfbar**, solange Stückzahl, Kaufkurs und Bezugsverhältnis fehlen: Gewinn am Scheinpreis, Höchstgewinn und Rückgang davon sind ohne sie nicht berechenbar. Die App und der Wächter zeigen dann „UNBEWERTET“ mit dem fehlenden Feld. Die Studie zum Baustein (13.10.) hat den Nutzen nicht belegt; er bleibt Paper/Shadow-Hinweis.
