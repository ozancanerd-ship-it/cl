# Betrieb — was wann läuft

App: **https://ozancanerd-ship-it.github.io/cl/**
Repo: **https://github.com/ozancanerd-ship-it/cl** (public — alles auf der App-Seite
ist öffentlich lesbar; echte Depotdaten gehören deshalb nicht dorthin)

Stand 2026-09-05. Diese Datei beschreibt, wie das System **im Alltag** arbeitet.
Forschung und Ergebnisse stehen woanders (siehe unten).

## Was „24/7" hier wörtlich heißt

Es gibt **keinen dauerlaufenden Prozess**. Es gibt einen Zeitplan:

| Wann | Was |
|---|---|
| stündlich, :25 UTC | voller Marktscan (Krypto, Aktien, Gold) → Alarm **nur bei Änderung** → Seite neu bauen |
| täglich, 23:10 UTC | zusätzlich Forward-Journalzeile + Tagesplan per Telegram |

Der echte Dauerlauf-Daemon (`scripts/run_live_daemon.py`) existiert und ist fertig
verdrahtet: WebSocket-Stream → MarketContext → MTF → Strategie → Entscheidung →
Signal → Alert → Risiko → Paper-Position, dazu Heartbeat (10 s), Watchdog (20 s),
Snapshot (60 s), Wiederanlauf aus dem Snapshot mit REST-Backfill der Lücke, SIGTERM-
Behandlung und Neustart der Pipeline, wenn ihr Task stirbt. **Er braucht aber eine
Maschine, die durchläuft.** Solange es die nicht gibt, ist der Stundentakt die
ehrliche Antwort — und nicht die Behauptung, es liefe etwas rund um die Uhr.

Was der Stundentakt gegenüber dem Daemon **nicht** kann: auf eine Bewegung innerhalb
der Stunde reagieren, und Positionen live nachführen (Stop nachziehen, Teilgewinn).
Wer das braucht, braucht den Daemon auf einem Server.

Daemon von Hand starten (read-only, sendet nie eine Order):

```bash
PYTHONPATH=src python3 scripts/run_live_daemon.py \
  --exchange binance_spot --symbols BTCUSDT ETHUSDT --notify --max-seconds 600
```

## Die tägliche Kette

```
23:10 UTC, GitHub Actions (.github/workflows/daily.yml)
│
├─ 1. scripts/tsmom_forward.py --source api
│     Holt Tagesschlusskurse direkt bei Binance und Yahoo, wertet die eingefrorene
│     Regel aus, hängt eine Zeile an data/repository_real/live/tsmom_forward.jsonl.
│     Entscheidet nichts, handelt nichts. Nur aufschreiben.
│
├─ 2. scripts/daily_report.py --send
│     Übersetzt die Zeile in einen Euro-Plan unter config/risk.yaml und schickt ihn
│     per Telegram — aber nur, wenn sich gegenüber gestern etwas geändert hat.
│
├─ 3. scripts/build_scan_data.py --out web/scan.json      (auch stündlich)
│     Der Gesamtmarkt in einem Durchgang: 28 Coins, 30 Einzelaktien, Gold über
│     PAXG/XAUT. Je Instrument ein Chart-Score aus sechs Faktoren, Ziele und Stop.
│
├─ 4. scripts/scan_alert.py --send                       (auch stündlich)
│     Vergleicht mit dem letzten Stand und schickt NUR die Änderung: neues A+/A-
│     Setup, weggebrochenes Setup, neue Nummer 1. Dieselbe Meldung frühestens nach
│     zwölf Stunden wieder. Kein Alarm ist das normale Ergebnis.
│
├─ 5. scripts/build_site.py --out _site
│     Baut die Web-App aus derselben Rechnung (daily_report.py --json).
│
├─ 6. Journal und Alarm-Stand committen und pushen
│     Die Forward-Datenspur darf nicht auf einem einzigen Rechner liegen.
│
└─ 7. GitHub Pages veröffentlichen
      Feste Adresse, PWA-fähig, auf dem iPhone-Homescreen wie eine App.
```

**Der Mac muss dafür nicht laufen.** Das ist der ganze Punkt.

## Warum die Seite sich nicht selbst nachlädt

Sie ist statisch und trägt ihr Baudatum im Fuß. Steht dort ein altes Datum, ist der
Job nicht gelaufen — und genau das soll sichtbar sein. Eine Seite, die ihre Daten
live nachlädt, sähe bei einem Ausfall weiterhin richtig aus und würde alte Zahlen
zeigen, ohne dass es jemand merkt. Bei etwas, nach dem gehandelt wird, ist das die
gefährlichere Bauweise.

## Die drei Filter im Tagesplan

Die Regel liefert je Instrument ein volatilitätsskaliertes Zielgewicht. Bis daraus
ein Plan wird, greifen drei Filter — in dieser Reihenfolge:

1. **Konto** — was nirgends gekauft werden kann, bekommt kein Budget. Es wird
   trotzdem ausgewiesen (`ohne_konto`), damit sichtbar bleibt, was die Regel wollte.
2. **Anlageklasse** — die Auswahl geht reihum durch Krypto, Aktien, Gold, FX. Nicht
   stur nach Gewicht: sechs Aktien würden sonst alles andere verdrängen, und die
   Mischung ist der ganze Wert der Regel (Krypto allein: OOS-Sharpe −0,21; gemischt:
   +1,08).
3. **Gebühren** — die Zahl der Positionen folgt dem Geld, nicht einer festen Acht.
   Bei Trade Republic kostet jede Order 1 €; eine 20-€-Position verliert damit 10 %
   rein und raus. Solche Positionen werden gar nicht erst vorgeschlagen.

## Wo was liegt

| Was | Wo |
|---|---|
| Forward-Journal (die saubere Datenspur) | `data/repository_real/live/tsmom_forward.jsonl` |
| Verworfene Journalzeilen mit Begründung | `…/tsmom_forward_corrections.jsonl` |
| Risikogrenzen (das Sicherheitsnetz) | `config/risk.yaml` — versioniert |
| Kostenmodell je Symbol | `config/costs.yaml` |
| Vorlage der Web-App | `site/template.html` |
| Erzeugte Web-App | `_site/` — nicht versioniert |
| Secrets | `.env`, Rechte 0600, gitignored; in der CI als GitHub Secrets |
| Gesamtmarkt-Scan | `web/scan.json` — bei jedem Lauf neu |
| Alarm-Stand (wogegen verglichen wird) | `data/repository_real/live/scan_alert_state.json` — **muss mitcommittet werden** |
| Alarm-Verlauf (was rausging) | `data/repository_real/live/alerts.jsonl` |

## Von Hand nachsehen

```bash
python3 scripts/daily_report.py            # Plan anzeigen, nichts senden
python3 scripts/daily_report.py --send     # anzeigen und senden, nur bei Änderung
python3 scripts/tsmom_forward.py --report  # Signale zeigen, nichts schreiben
python3 scripts/check_data_freshness.py --profile live
python3 scripts/portfolio_hub.py           # echte Konten
python3 scripts/build_site.py --out _site  # App lokal bauen
python3 scripts/build_scan_data.py --out web/scan.json     # Gesamtmarkt scannen
python3 scripts/scan_alert.py --dry-run    # zeigen, was gemeldet würde — Stand bleibt
python3 scripts/trader_analysis.py BTCUSDT # Struktur, Liquidität, Zonen im Detail
```

## Alternative ohne GitHub

`ops/install_forward_collector.sh` richtet denselben Ablauf als launchd-Job auf dem
Mac ein. **Nicht zusätzlich zu GitHub Actions laufen lassen** — zwei Telegram-
Nachrichten am Tag und zwei Schreiber auf derselben Journaldatei.

## Wenn Daten fehlen

Die Journalzeile trägt `complete` und `missing`. Fehlt auch nur ein Instrument des
präregistrierten Universums, ist die Zeile **keine gültige Beobachtung**: die Gewichte
verteilen sich auf weniger Titel, und ein Vergleich mit gestern misst den Ausfall statt
den Markt. Dann passiert Folgendes:

- `tsmom_forward.py` endet mit Code 2 und nennt die fehlenden Instrumente
- `daily_report.py` baut **keinen** Plan, sondern schickt eine Ausfallmeldung
- die Web-App zeigt die Warnung ganz oben, vor allem anderen
- der Workflow läuft trotzdem zu Ende (damit Warnung und Seite rausgehen) und wird
  erst danach gezielt rot

Der Tag zählt nicht als Forward-Tag mit.

## Stolperfallen, die schon einmal zugeschlagen haben

- **Veraltete Reihen sehen aus wie ein Kurssprung.** Am 2026-09-04 endeten die
  Krypto-Reihen 34 Tage vor dem Lauf. Aus 34 Tagen Bewegung wurde eine Tageskerze
  von +29 %, die gemessene Volatilität sprang von 39 % auf 79,6 %, das Zielgewicht
  halbierte sich. Gegenmittel: `_merged_history()` und das Frischeprofil `live`
  (Krypto max. 3 Tage). Immer `--profile live` prüfen, bevor man Zahlen glaubt.
- **Ein fehlendes Secret darf den Lauf nicht killen.** `daily_report.py --send`
  gibt bewusst 0 zurück, wenn Telegram nicht konfiguriert ist. Sonst wäre die Seite
  nie gebaut worden.
- **Binance sperrt Rechenzentren.** `api.binance.com` antwortet GitHub-Runnern mit
  "Service unavailable from a restricted location". Beim ersten Cloud-Lauf fielen BTC,
  ETH und BNB lautlos aus, der Lauf war grün, und der Tagesplan bestand nur noch aus
  Aktien und Gold. Gegenmittel: die Host-Kette in `BINANCE_HOSTS` (der öffentliche
  Spiegel `data-api.binance.vision` funktioniert) plus die Vollständigkeitsprüfung oben.
  **Nie eine andere Börse als Ersatz einsetzen** — unterschiedliche Kurse an
  unterschiedlichen Tagen liest die Regel als Kursbewegung.
- **Ein fehlender Alarm-Stand meldet alles neu.** `scan_alert_state.json` ist die
  einzige Erinnerung des Wachpostens. Fehlt sie im Repo, fängt jeder Lauf bei null an
  und schickt sämtliche Setups erneut — das wäre der Spam, den die Abkühlzeit gerade
  verhindern soll. Deshalb steht sie im `git add -f` des Workflows.
- **Eine stumme Anlageklasse ist kein weggebrochenes Setup.** Fällt Krypto aus, wären
  alle Krypto-Setups plötzlich „entfallen". Der Wachposten trägt den alten Stand
  unverändert weiter und meldet nichts — geprüft in
  `tests/unit/test_scan_alerting.py::test_stumme_anlageklasse_erzeugt_keinen_wegbruch`.
- **Kein Take-Profit.** Die Regel hat keinen. Positionen enden, wenn das Signal
  dreht. 59 von 398 historischen Positionen liefen über drei Monate und trugen
  praktisch den gesamten Gewinn — wer früh verkauft, verkauft genau diese weg.

## Weiterlesen

- `docs/TSMOM-MULTIASSET-ERGEBNIS-2026-09-04.md` — warum diese Regel, und was
  gegen sie spricht
- `docs/STRATEGIE-ENTSCHEID-2026-09-04.md` — warum ohne statistischen Beweis
  gehandelt wird
- `docs/INDEPENDENT-METHOD-AUDIT-2026-09-03.md` — die zwölf Befunde, die zur
  Widerlegung der SMC-Familie führten
- `docs/TRADE-REPUBLIC-ANBINDUNG.md` — warum es keine API gibt
