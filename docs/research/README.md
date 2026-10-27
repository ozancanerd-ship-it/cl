# Research-Wissensbasis (Stand 2026-10-27)

Dateien: `quellen.json` (Quellenregister, nur per Abruf bestätigt), `erkenntnisse.json` (eigene Paraphrasen, je mit Quelle, Stelle, Codebezug, Status, Testplan). Test: `tests/unit/test_research_wissensbasis.py`.

Regeln: keine Quelle ohne bestätigten Abruf; keine Übernahme einer Strategie nur wegen einer Quelle; Status `kein_transfer_belegt` heißt: Befund gilt für andere Märkte/Horizonte.

## Validierungsergebnisse (tatsächlich gelaufen)
- 695 Paper-Trades unabhängig nachgerechnet (`scripts/verifiziere_bilanz.py`): −122,9 R brutto, PF 0,64 bestätigt.
- Walk-Forward der Filterkandidaten (`scripts/strategie_walkforward.py`): kein Filter bestand Out-of-Sample.
- Keine Strategie ist statistisch validiert. Alles bleibt Paper/Shadow.

## Priorisierter Umsetzungsplan (noch nicht umgesetzt)
1. Versuchszähler N für alle Filter/Regeln (E01) + Deflated-Sharpe-Skript mit Unit-Test (E02).
2. Zuverlässigkeitstabelle Score ↔ tatsächliche Trefferquote aus dem Archiv, nur n ≥ 30 je Klasse (E10/E11).
3. Aktien nach Gap/intraday getrennt auswerten (E06).
4. Kostenannahmen gegen offizielle Gebührendokus belegen (Quelle noch zu verifizieren).
5. Zweitmeinung auf Responses API mit strict-Schema + Validator + Speicherung der Vorhersagen (E12).

## Befund 27.10. (Zuverlässigkeit, `scripts/zuverlaessigkeit.py`)
- Der Score sagt den Ausgang nicht voraus: Spearman Score↔R −0,10 (ohne Sofort-Stops, n=630); Score ≥70: PF 0,48 (n=79, signifikant negativ). Aktien tragen den Verlust (PF 0,35–0,47), Krypto ≈ Nulllinie.
- Alarm-Tor verschärft: „bewährt“ nur noch, wenn nach Kosten positiv und in beiden Zeithälften nicht negativ (n ≥ 12). Aktuell überlebt nur Krypto „Rückeroberung nach Liquiditätsgriff“ (long). Kosten bleiben Annahme (nicht gemessen).

## Korrektur 27.10. (späterer Nachtrag)
- **Verzerrung in der Alarm-Bilanz gefunden und behoben:** Trades, die per „Analyse gedreht“ (invalidiert) vor Ziel 1 endeten, wurden gar nicht gezählt (Krypto: 158 Trades, −37 R). Jetzt zählt der echte Ausstiegskurs.
- **Kosten belegt:** Kraken Pro Spot Stufe 1 (0 USD 30-Tage-Volumen): Maker 0,40 %, Taker 0,80 % je Seite (kraken.com/features/fee-schedule, abgerufen 27.10.2026, US-Seite; EU kann abweichen). Roundtrip Krypto daher 0,80 % (nur Maker) bis 1,60 % (Taker), nicht 0,35 %. Trade Republic: Fremdkostenpauschale 1 € je Order, Orderprovision frei (PLV Stand 09/2020; aktuelle Fassung nicht geprüft).
- **Ergebnis:** Mit Ausstiegs-Korrektur, Kosten 0,80 % und Zeithälften-Test ist **keine** Gruppe mehr „bewährt“. Das Alarm-Tor (`NUR_BEWAEHRT = True`) meldet deshalb aktuell keine Kauf-Alarme. Das ist beabsichtigt: kein belegter Edge → keine Alarme als Handelsaufforderung.
