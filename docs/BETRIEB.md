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
| alle 15 Min (:05 :20 :35 :50) | **Wächter**: kein Scan, nur die Kurse der beobachteten Setups. Meldet, wenn Einstieg, Ziel oder Stop tatsächlich getroffen wurde |
| stündlich, :25 UTC | voller Marktscan (Krypto, Aktien, Gold) → neue Setups auf die Wachliste → Alarm bei Änderung → Seite neu bauen |
| täglich, 23:10 UTC | zusätzlich Forward-Journalzeile + Tagesplan per Telegram |

## Die Wachliste

Jedes handelbare Setup wandert automatisch drauf. Ozans Vorgabe: „will nicht selber
alarme erstellen."

```
WARTET_AUF_EINSTIEG ──► AKTIV ──► TP1 ──► TP2 ──► TP3 ──► ZIEL_ERREICHT
        │                 │
        ▼                 ▼
   ABGELAUFEN           STOP / INVALIDIERT
```

Geprüft wird gegen **Hoch und Tief seit der letzten Prüfung**, nicht gegen den
Schlusskurs — sonst rutscht ein Treffer um 14:23 durch, weil der Kurs um 14:30 wieder
darunter steht. Jeder Übergang wird genau einmal gemeldet; der Zustand liegt in
`data/repository_real/live/watchlist.json` und wird mitcommittet.

**Zwei ehrliche Einschränkungen:**

1. Sind im selben Fenster Ziel **und** Stop berührt worden, sagt ein Hoch/Tief nicht,
   was zuerst kam. Die Wachliste nimmt dann den **Stop** an. Eine Statistik, die sich
   im Zweifel den Gewinn gutschreibt, wäre geschönt.
2. Der Takt ist 15 Minuten, nicht Echtzeit. Für Swing-Trades über Tage kein
   Unterschied; für Scalping wäre es einer.

**Was aufs Telefon geht:** alles, was einen laufenden Trade betrifft (Einstieg, Ziel,
Stop, ungültig) — immer, unabhängig von der Note. Neue Setups nur ab **A−**; B und B+
stehen auf der Wachliste und in der App, klingeln aber nicht. Beim ersten Lauf waren es
17 Setups auf einmal; ohne diese Grenze wäre das eine Lawine gewesen.

**Wer meldet was** — damit dieselbe Nachricht nicht zweimal kommt:

| Meldung | Skript |
|---|---|
| Neues Setup mit Einstieg, Stop, Zielen, CRV — **nur ab A−** | `watch_levels.py --vollstaendig` |
| Einstieg / TP1 / TP2 / TP3 / Stop erreicht | `watch_levels.py` |
| Setup abgelaufen, Richtung gedreht | `watch_levels.py` |
| Setup weggebrochen, neue Nummer 1, Chart zieht an | `scan_alert.py --ohne-setups` |

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
├─ 3. scripts/build_scan_data.py --out web                (auch stündlich)
│     Der Gesamtmarkt in einem Durchgang. Das Krypto-Universum kommt von der
│     Börse, nicht aus dem Code: ~490 USDT-Paare, gefiltert auf Liquidität und
│     Qualität, nach Umsatz gekappt. Dazu 40 Einzelaktien und Gold (PAXG/XAUT).
│     Je Wert: Chart-Score aus sechs Faktoren, Ziele, Stop, Note, Muster,
│     MTF-Tabelle, Kommentar und die Koordinaten für die Zeichnung.
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

## Das Krypto-Universum

Es gibt **keine Coin-Liste im Code**. Bei jedem Lauf:

```
alle USDT-Paare der Börse            ~490
 ├─ Stablecoins, verpackte Doppel      raus  (USDC, FDUSD, WBTC, WBETH …)
 ├─ Hebel-Token                        raus  (…UP/…DOWN/…BULL/…BEAR, 3L/3S)
 ├─ tokenisierte Aktien und ETFs       raus  (NVDAB, TSLAB, QQQB, SOXLB …)
 ├─ unter 3 Mio USDT Umsatz in 24 h    raus
 ├─ unter 3.000 Abschlüssen            raus
 ├─ Kurs unter 0,0005                  raus
 └─ nach Umsatz sortiert, gekappt      ~85–95 Paare
```

**Warum die Schwellen so liegen.** 3 Mio USDT klingt niedrig, ist es aber nicht:
eine Position von 50–200 € ist dort ein Tropfen. Höhere Schwellen (10 Mio)
halbieren das Universum, ohne dass es für dieses Kapital einen Unterschied macht —
dann fehlen genau die Altcoins, die sich schnell bewegen.

**Tokenisierte Aktien** erkennt `ist_tokenisierte_aktie()` an der Endung `B`, mit
einer kurzen Ausnahmeliste für echte Coins (BNB, ARB, SHIB, TRB, CKB, DGB, BB, YB).
Der Test schlägt im Zweifel zum Ausschluss aus: ein neues Aktien-Token fällt
automatisch raus, ein neuer Coin auf B müsste einmal eingetragen werden. Die
Ausgeschlossenen stehen mit Namen im Bericht und im System-Tab der App — ein
falsch aussortierter Coin fällt auf, statt zu verschwinden.

## Die Notenskala

Nicht mehr binär. Drei Größen entscheiden, nicht eine:

| | Score | CRV | erwartete Bewegung |
|---|---|---|---|
| A+ | ≥ 66 | ≥ 1:2,2 | ≥ 5 % |
| A | ≥ 57 | ≥ 1:1,8 | ≥ 3,5 % |
| A− | ≥ 50 | ≥ 1:1,5 | ≥ 2,5 % |
| B+ | ≥ 43 | ≥ 1:1,3 | ≥ 1,8 % |
| B | ≥ 36 | ≥ 1:1,15 | ≥ 1,2 % |

(Profil `aggressiv`, der Standard. `ausgewogen` und `konservativ` verlangen mehr.)

Dazu zwei Sonderregeln: eine sehr große erwartete Bewegung mit solidem CRV hebt
die Note auf mindestens A−; unter 1 % Bewegung ist nie mehr als WATCH möglich,
egal wie sauber der Chart aussieht.

**Ohne Invalidierung gibt es in keinem Profil eine handelbare Note.** Mehr
Risikobereitschaft heißt größere Position oder weiterer Stop — nicht kein Stop.

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
| Gesamtmarkt-Scan (Rangliste) | `web/scan.json` — bei jedem Lauf neu |
| Detailansicht je Wert | `web/asset/<SYM>.json` — Zeichnung, MTF, Muster, Kommentar |
| Alarm-Stand (wogegen verglichen wird) | `data/repository_real/live/scan_alert_state.json` — **muss mitcommittet werden** |
| Wachliste (Zustand je Setup) | `data/repository_real/live/watchlist.json` — **muss mitcommittet werden** |
| Alarm-Verlauf (was rausging) | `data/repository_real/live/alerts.jsonl` |

## Von Hand nachsehen

```bash
python3 scripts/daily_report.py            # Plan anzeigen, nichts senden
python3 scripts/daily_report.py --send     # anzeigen und senden, nur bei Änderung
python3 scripts/tsmom_forward.py --report  # Signale zeigen, nichts schreiben
python3 scripts/check_data_freshness.py --profile live
python3 scripts/portfolio_hub.py           # echte Konten
python3 scripts/build_site.py --out _site  # App lokal bauen
python3 scripts/build_scan_data.py --out web                # Gesamtmarkt scannen
python3 scripts/build_scan_data.py --out web --profil konservativ   # strengere Noten
python3 scripts/opportunity_scan.py --symbols BTCUSDT --top 5       # einzeln
python3 scripts/scan_alert.py --dry-run    # zeigen, was gemeldet würde — Stand bleibt
python3 scripts/trader_analysis.py BTCUSDT # Struktur, Liquidität, Zonen im Detail
python3 scripts/watch_levels.py --dry-run  # zeigen, was gemeldet würde
python3 scripts/watch_levels.py --vollstaendig --dry-run   # inkl. neuer Setups
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
- **Ein Stop 0,06 % entfernt ist kein Stop.** Am 2026-09-05 stand UUSDT (ein Wert
  nahe 1 USDT) mit Einstieg 0,9993, Stop 0,9999 und Ziel 0,984 auf der Wachliste —
  auf dem Papier Chance-Risiko 1:25, in Wirklichkeit ein sicherer Verlust: allein Hin-
  und Rückweg kosten rund 0,2 %. Gegenmittel: `MIN_STOP_PCT = 0,6` als zweite
  Untergrenze neben den 1,5 ATR. Die größere der beiden gewinnt.
- **Der Scan lief in den Speicher.** Der erste Durchgang über 133 Werte hat alle
  MTF-Kontexte gesammelt (jeder hält sämtliche Bars aller fünf Zeitebenen plus die
  Analyseobjekte). Der Kernel hat den Prozess abgeräumt — **ohne Fehlermeldung**, der
  Lauf war einfach weg, das Log endete mitten im Satz. Gegenmittel: die Detaildatei
  wird geschrieben, solange der Kontext noch lebt, danach wird er freigegeben. Wer
  hier etwas ergänzt, darf den Kontext nicht wieder aufheben.
- **Was live ist und was nicht, muss auf der Seite unterscheidbar sein.** Krypto-
  und Goldkurse kommen per Websocket direkt von der Börse in den Browser; die
  eingezeichnete Analyse stammt aus dem letzten Scan, bei Aktien auch der Kurs.
  Eine Seite, die beides gleich aussehen lässt, verleitet dazu, einen Stand von vor
  einer Stunde für den aktuellen Kurs zu halten.
- **Das Portfolio gehört nicht ins Repo.** Es ist öffentlich. Die Positionen liegen
  ausschließlich im Browser (localStorage) und werden dort gerechnet.
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
