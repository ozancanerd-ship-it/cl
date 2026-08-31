# Entwicklungs-Workflow

## Grundregel

> bauen → testen → Fehler beheben → testen → erst dann nächste Komponente

Kein großer ungetesteter Wurf. Eine Komponente gilt erst als "fertig", wenn ihre Tests grün
sind und sie in ARCHITECTURE.md kurz beschrieben ist.

## Schritte pro Komponente

1. **Vertrag festlegen:** Ein-/Ausgaben als Typen in `core/types.py` (oder Erweiterung davon).
2. **Tests zuerst skizzieren:** mindestens Happy Path + 2 Randfälle + "unsichere Daten → kein
   Ergebnis".
3. **Implementieren:** so klein wie möglich, keine Abhängigkeit auf spätere Phasen.
4. **`make check`:** ruff + mypy + pytest müssen grün sein.
5. **Doku:** Abschnitt in ARCHITECTURE.md aktualisieren, TODO.md-Haken setzen.
6. **Commit:** eine Komponente = ein (oder wenige) fokussierte Commits.

## Definition of Done

- [ ] Öffentliche Funktionen/Klassen haben Docstrings mit Ein-/Ausgabe.
- [ ] Unit-Tests decken Happy Path + Randfälle ab.
- [ ] Deterministisch (Zeit über `core/clock.py`, keine echten Netzwerk-Calls).
- [ ] Keine Broker-/Secret-/Live-Abhängigkeit.
- [ ] `make check` grün.
- [ ] ARCHITECTURE.md + TODO.md aktualisiert.

## Test-Konventionen

- `tests/unit/test_<modul>.py`, ein Testmodul je Quellmodul.
- Fixtures für Candle-Serien in `tests/conftest.py` (synthetisch, deterministisch).
- Golden-Tests für Analyse-Engines: dokumentiertes Chartmuster → erwartete Objekte.
- Keine Zufallszahlen ohne festen Seed.

## Branch-/Commit-Konvention (nach `git init`)

- Branch pro Komponente: `feat/market-structure-engine`.
- Commit-Präfixe: `feat:`, `fix:`, `test:`, `docs:`, `chore:`, `refactor:`.
- Kein Commit direkt auf `main` für Feature-Arbeit.

## Sicherheits-Leitplanken (immer)

- Keine echten Orders, keine Echtgeld-Broker-Anbindung.
- Keine API-Keys/Secrets im Repo; `.env` ist ignoriert.
- Bei unvollständigen/unsicheren Daten: **kein** Setup, **keine** Order.
- Risk Engine kann jede Order ablehnen – dieser Pfad wird immer getestet.
