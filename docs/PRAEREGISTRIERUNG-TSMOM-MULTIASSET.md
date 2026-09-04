# Vorab-Registrierung — TSMOM über mehrere Assetklassen

**Erstellt am 2026-09-04, BEVOR der Test gelaufen ist.** Dieses Dokument existiert, damit
das Kriterium nicht nachträglich an das Ergebnis angepasst werden kann. Wer es später
ändert, sieht es im Git-Verlauf.

## Warum diese Hypothese

Prüfrunde 1 (`docs/TSMOM-VALIDATION-2026-09-04.md`) hat TSMOM **nur auf Krypto** getestet
und den vorab gestellten Test nicht bestanden (OOS-Sharpe −0,21, p = 0,758).

Die Studie, auf der die Strategie beruht — Moskowitz, Ooi & Pedersen, *Journal of Financial
Economics* 2012 — holt ihren Sharpe über 1 ausdrücklich aus der **Streuung über 58
Instrumente in vier Assetklassen**: Aktienindizes, Währungen, Rohstoffe, Anleihen. Der
Einzelinstrument-Sharpe liegt dort deutlich niedriger; der Gewinn entsteht durch
Diversifikation über weitgehend unkorrelierte Trends.

Das Krypto-Panel hat genau diese Eigenschaft **nicht**: die Positionsanalyse des Portfolios
misst Korrelationen bis ρ = 0,80. Dreizehn Krypto-Positionen sind ungefähr eine Wette.

**Hypothese:** Der Grund für das Scheitern in Runde 1 ist nicht die Regel, sondern das
Universum. Auf einem über Assetklassen gestreuten Universum sollte dieselbe eingefrorene
Regel eine positive risikoadjustierte Rendite liefern.

Diese Hypothese kann falsch sein. Genau deshalb wird sie getestet.

## Was NICHT verändert wird

Die Regel bleibt exakt wie eingefroren (`config/setup_validation.json` → `preregistered`):
Fenster 28/56/90/120/180 Tage, Vol-Fenster 60, Zielvolatilität 40 %, maximales Gewicht 1,0,
Mindest-Zustimmung 20 %, long-only. **Kein Parameter wird angefasst.**

Der Split bleibt **2025-01-01** — derselbe wie in Runde 1. Ein neues Split-Datum wäre
Split-Shopping (Befund F11).

## Universum (abschließend, vor dem Lauf festgelegt)

| Klasse | Instrumente |
|---|---|
| Krypto | BTCUSDT, ETHUSDT, BNBUSDT |
| Aktien | NVDA-YFD, AAPL-YFD, MSFT-YFD, AMD-YFD, GOOGL-YFD, META-YFD |
| Währungen | EURUSD-YFD, GBPUSD-YFD, USDJPY-YFD |
| Rohstoffe | XAUUSD-YFD |

13 Instrumente, vier Klassen, gemeinsame Historie ab 2018-12-31.

Ausgeschlossen: SPX, VIX, DXY, US10Y (Indizes und Renditen, nicht direkt handelbar) sowie
SOL, XRP, DOGE, LINK, SEI (Historie erst ab 2023 — würden das Universum über die Zeit
verändern).

## Portfolio-Konstruktion

Je Instrument liefert die Regel ein volatilitätsskaliertes Zielgewicht. Das Portfolio ist
der **gleichgewichtete Durchschnitt** dieser Gewichte über alle 13 Instrumente. Kein
Optimierer, keine geschätzte Kovarianzmatrix — beides wäre eine zusätzliche Anpassung an
die Daten.

Kosten: 0,20 % je Positionswechsel und Instrument, einheitlich. Für Krypto realistisch,
für Aktien und FX konservativ.

## Kriterien — vorab festgelegt

**Primär.** OOS-Sharpe des Multi-Asset-Portfolios > 0, einseitig getestet, gegen die
Bonferroni-Schwelle aus `config/hypothesis_registry.json` (aktuell 807 Konfigurationen,
plus die 2 dieses Laufs → Schwelle p < 0,05/809 ≈ 0,0000618).

**Sekundär.** OOS-Sharpe des Multi-Asset-Portfolios > OOS-Sharpe eines gleichgewichteten
Buy-&-Hold-Portfolios desselben Universums.

**Deskriptiv, kein Kriterium.** Mittlere paarweise Korrelation der Instrumentenrenditen im
Multi-Asset-Universum gegen das reine Krypto-Universum. Dient der Erklärung, nicht der
Entscheidung.

## Was als Scheitern gilt

* Primärkriterium nicht erfüllt → **Hypothese verworfen.** Kein Nachjustieren des
  Universums, keine Instrumente entfernen, kein anderer Split, keine Gewichtungsvariante.
* Sekundärkriterium nicht erfüllt, Primärkriterium erfüllt → Regel bleibt
  `in_validation`, wird aber nicht als Verbesserung gegenüber Halten geführt.

## Zahl der Konfigurationen

Dieser Lauf fügt dem Register **2** hinzu: `multiasset_portfolio` und
`multiasset_buyhold_benchmark`. Sie werden vor dem Lauf eingetragen.

## Vermerk

Sollte der Test bestehen, ist das **kein Freigabesignal**. Es bliebe ein
In-Sample-plus-OOS-Befund auf historischen Daten. Der Status
`SETUP-TSMOM-ENSEMBLE-01: in_validation / SHADOW` ändert sich erst nach ≥ 100
Forward-Trades aus dem Live-Paper-Betrieb.
