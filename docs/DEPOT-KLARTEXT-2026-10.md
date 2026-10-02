# Depot-Seite: Widersprüche und unklare Sätze — Check 02.10.2026

Geprüft mit neun Test-Positionen (Coins, Aktien, Gold, ein direkter Short) gegen den echten
Scan von 07:52 UTC. Ozans echte Positionen liegen nur in seinem Browser und waren nicht Teil
des Checks.

## Gefunden und behoben

| Was auf der Karte stand | Problem | Jetzt |
|---|---|---|
| Bitcoin: „Schlusslicht … Nicht nachkaufen." und darunter „Einstiegsseite: Score 67 (Note WATCH) — hier wäre heute sogar ein Zukauf vertretbar." (ebenso Dash, Zcash) | zwei Urteile auf einer Karte; WATCH ist gar kein handelbares Setup | Ein Zukauf-Satz nur bei handelbarem Setup in Positionsrichtung **und** bestandenem Alarm-Tor (`alarm.ja`, dieselben Prüfungen wie fürs Handy). Sonst: „gerade kein handelbares Setup … für einen Zukauf fehlt der Anlass" bzw. „kein Alarm: <Grund>". Gilt auch für „Nachlegen" in „Was jetzt zu tun ist". |
| „Kapital könnte besser stehen: … Arista hat ein handelbares Setup (Note A+)" | Arista-Setup-Art liegt bei Aktien im Minus, das Alarm-Tor schweigt — die App riet trotzdem zum Umschichten | nur noch Setups, die das Alarm-Tor bestehen |
| Mondelez: Halte-Stop 50,97 € bei Kurs 50,99 € — „Halten — Stop steht … 0,0 % entfernt. Laufen lassen"; oben „Nichts zu tun … die Stops stehen" | klang nach Ruhe, der Ausstieg war eine Kursbewegung entfernt | unter 2 %: „Halten — aber der Stop ist nah … fällt der Kurs darunter, wird verkauft"; oben „Nichts zu tun — aber 2 Stops sind nah" mit Namen; in der Tabelle rot. Die Regel selbst ist unverändert. |
| „Kein Verkaufsbefehl — aber der Moment, in dem ein Stop im Depot stehen sollte" | der Stop stand längst | nennt den stehenden Stop: „dein Stop bei X entscheidet, nicht die Gegenanalyse" |
| Gold (PAX), 27 % im Plus: „fehlt die Historie — eigene Rendite negativ" | unklar, wessen Rendite | „zu wenige Werte im Scan für einen Klassenvergleich. Für sich allein: −3 % im letzten Monat, +2 % im Quartal — der Wert läuft gerade gegen dich." |
| Ethereum **short** ab 4.800 $ bei 2.724 $: „G/V −43,3 %", „Du liegst 43 % im Minus", „Nachzügler … nicht nachkaufen" | ein direkter Short (Signal „Gekauft" auf ein Short-Setup) wurde wie ein Kauf gerechnet | G/V mit richtigem Vorzeichen (+43 %), Wert = Einsatz + Ergebnis, relative Stärke gespiegelt („Für deinen Short läuft es: schwächer als 75 % der Coins"), „schliessen" statt „verkaufen", im Gleichlauf als Gegenposition |
| „8 Position(en)" | — | „8 Positionen", „1 Position" |
| „0,014 Stück (≈ 1.065 €) bringen sie auf 25 %" | Verb fehlte | „… verkaufen bringt sie auf 25 %" |
| Bilanz: Chip „KW38 +10,3 R", Satz „+10,4 R" | Rundung (10,3500… gegen 10,35) | Satz rundet erst auf zwei Stellen wie `performance.json` |
| Dash-Alarm (09:51): Karte sagt „Von 102 Signalen der Note A− erreichten 13 % das erste Ziel … die Vergangenheit spricht gegen den Trade" | dieselbe Karte klingelte, weil „Rückeroberung" bei Coins im Plus liegt; die A−-Zahl stammt fast ganz aus 91 Signalen ohne Setup-Namen. Dazu fragte der Scan nach dem Kürzel („RUECKEROBERUNG"), die Trades tragen den Namen — die Setup-Stufen fanden nie etwas | Die Trefferquote nimmt zuerst die Setup-Art **in ihrer Klasse**, gezählt wie im Alarm-Tor (nur entschiedene Trades): „Von 23 Signalen „Rückeroberung nach Liquiditätsgriff" bei Coins erreichten 12 das erste Ziel (52 %) … im Schnitt +0,39 R" |

## Nicht geändert

Keine neue Handelsregel. Stop-Regeln, Halte-Stop (k = 5), Schonfrist, Klumpen-Hinweis und
Alarm-Tor sind unverändert; geändert ist nur, dass Depot-Karte, Nachlegen und Umschichten
**dasselbe Tor** benutzen wie die Alarme — eine Kaufempfehlung, die aufs Handy nicht
durchginge, gibt es auch auf der Depot-Seite nicht mehr.

Tests: `tests/unit/test_depot_rat.py` (Zukauf nur mit Tor, nie „nicht nachkaufen" und
„vertretbar" zugleich, Stop im Gegen-Satz, Short gespiegelt), `tests/unit/test_depot_stop_nah.py`.
