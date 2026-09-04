# Trade Republic — Anbindungsoptionen (Stand 2026-09-03)

**Ergebnis: Es gibt keine offizielle, für Privatkunden nutzbare API. Empfohlener Weg ist
der manuelle Import über einen sauberen Adapter.**

## Die drei Optionen

### 1. Manueller Import — EMPFOHLEN

Trade Republic App → Profil → Depotauszug / Transaktionsübersicht → Export (PDF/CSV).
Import über einen `TradeRepublicManualAdapter` mit `source=manual`.

- Kein ToS-Verstoß, kein Sperrrisiko, kein Wartungsaufwand bei App-Updates
- Nicht live — aber **das ist bei einem Aktiendepot kaum relevant**: die Positionen
  ändern sich selten, ein Import bei jeder eigenen Transaktion reicht
- Passt zur bestehenden Architektur (`config/brokers.example.yaml`,
  `integration: manual_import`)

Wichtig für die Umsetzung: Der Adapter implementiert dieselbe Schnittstelle wie die
Exchange-Adapter. Wenn Trade Republic später eine offizielle API anbietet, wird nur der
Adapter getauscht — der Rest des Systems merkt nichts davon.

### 2. PSD2 / Open Banking über Aggregator — ungeeignet

Trade Republic ist PSD2-konform und über den Aggregator **Powens** (ehemals Budget Insight)
erreichbar.

Zwei Gründe, warum das hier nicht hilft:

- **PSD2 deckt Zahlungskonten ab, nicht Wertpapierdepots.** Erreichbar wäre das
  Verrechnungskonto (Cash, IBAN) — die Aktienpositionen selbst nicht.
- Powens ist ein **B2B-Dienst** mit Vertrag und Lizenzanforderungen, nicht für
  Privatpersonen nutzbar.

### 3. Inoffizielle APIs — abgelehnt

Es existieren funktionierende Community-Bibliotheken (`pytr`, `TradeRepublicApi`,
`trade-republic-api`), die die private App-API ansprechen. Sie können Depot, Transaktionen
und Dokumente auslesen.

`pytr` sagt selbst: *„This is a library for the private API of the Trade Republic online
brokerage. It is not affiliated with Trade Republic Bank GmbH."*

Warum das hier nicht in Frage kommt:

- Nutzung der privaten API verstößt gegen die Nutzungsbedingungen
- Kein Support, keine Stabilitätsgarantie — bricht bei jedem App-Update
- Im schlimmsten Fall **Kontosperre bei einem Depot mit echtem Geld**
- Der Gewinn ist gering: siehe oben, Depotpositionen ändern sich selten

Das Risiko-Nutzen-Verhältnis ist eindeutig schlecht. `docs/SECURITY.md` und
`docs/FINAL_ARCHITECTURE_AUDIT.md` hatten das bereits so entschieden — diese Prüfung
bestätigt es.

## Wichtige Abgrenzung

Trade Republic ist ein **Portfolio-Zustands-Problem, kein Datenproblem.**

Für die Analyse von Einzelaktien braucht das System Kursdaten und Fundamentals — die kommen
von einer Marktdatenquelle (Polygon / Finnhub / andere), nicht vom Broker. Trade Republic
wird nur gebraucht, um zu wissen, **was gehalten wird**.

Deshalb: Aktien-Datenquelle hat Priorität, Trade Republic-Import ist nachgelagert und
manuell völlig ausreichend.

## Quellen

- Open Banking Tracker — Trade Republic: PSD2-konform, ein Aggregator (Powens), kein
  öffentliches Developer-Portal
- `pytr` (GitHub) — inoffiziell, private API, nicht mit Trade Republic Bank GmbH verbunden
