# Betrieb — was wann läuft

Stand 2026-09-04. Diese Datei beschreibt, wie das System **im Alltag** arbeitet.
Forschung und Ergebnisse stehen woanders (siehe unten).

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
├─ 3. scripts/build_site.py --out _site
│     Baut die Web-App aus derselben Rechnung (daily_report.py --json).
│
├─ 4. Journal committen und pushen
│     Die Forward-Datenspur darf nicht auf einem einzigen Rechner liegen.
│
└─ 5. GitHub Pages veröffentlichen
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

## Von Hand nachsehen

```bash
python3 scripts/daily_report.py            # Plan anzeigen, nichts senden
python3 scripts/daily_report.py --send     # anzeigen und senden, nur bei Änderung
python3 scripts/tsmom_forward.py --report  # Signale zeigen, nichts schreiben
python3 scripts/check_data_freshness.py --profile live
python3 scripts/portfolio_hub.py           # echte Konten
python3 scripts/build_site.py --out _site  # App lokal bauen
```

## Alternative ohne GitHub

`ops/install_forward_collector.sh` richtet denselben Ablauf als launchd-Job auf dem
Mac ein. **Nicht zusätzlich zu GitHub Actions laufen lassen** — zwei Telegram-
Nachrichten am Tag und zwei Schreiber auf derselben Journaldatei.

## Stolperfallen, die schon einmal zugeschlagen haben

- **Veraltete Reihen sehen aus wie ein Kurssprung.** Am 2026-09-04 endeten die
  Krypto-Reihen 34 Tage vor dem Lauf. Aus 34 Tagen Bewegung wurde eine Tageskerze
  von +29 %, die gemessene Volatilität sprang von 39 % auf 79,6 %, das Zielgewicht
  halbierte sich. Gegenmittel: `_merged_history()` und das Frischeprofil `live`
  (Krypto max. 3 Tage). Immer `--profile live` prüfen, bevor man Zahlen glaubt.
- **Ein fehlendes Secret darf den Lauf nicht killen.** `daily_report.py --send`
  gibt bewusst 0 zurück, wenn Telegram nicht konfiguriert ist. Sonst wäre die Seite
  nie gebaut worden.
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
