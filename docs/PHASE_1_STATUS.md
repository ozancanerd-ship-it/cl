# Phase 1 — Data Foundation — Status

**Stand:** 2026-08-28 · **Ergebnis:** CODE-COMPLETE, alle Prüfungen grün.
**Toolchain:** `uv 0.12.7` + CPython 3.12.14 (uv-managed, keine Xcode-CLT nötig).

---

## 1. Testergebnisse

```
$ ./scripts/check.sh
=== 1/3 ruff (lint + format-check) ===   All checks passed!   (132 Dateien formatiert)
=== 2/3 mypy (strict) ===                Success: no issues found in 93 source files
=== 3/3 pytest ===                       193 passed in 0.33s
```

**Coverage der Phase-1-Module** (`pytest --cov=trading_agent`):

| Modul | Stmts | Miss | Cov |
|-------|------:|-----:|----:|
| core/enums.py | 101 | 0 | 100 % |
| core/time.py | 96 | 7 | 93 % |
| core/clock.py | 31 | 0 | 100 % |
| core/models.py | 244 | 7 | 97 % |
| core/version.py | 4 | 0 | 100 % |
| config/loader.py | 97 | 0 | 100 % |
| utils/logging.py | 47 | 0 | 100 % |
| refdata/models.py | 128 | 7 | 95 % |
| refdata/instruments.py | 41 | 4 | 90 % |
| refdata/symbols.py | 35 | 4 | 89 % |
| refdata/calendar.py | 75 | 8 | 89 % |
| refdata/seed.py | 30 | 0 | 100 % |
| data/interfaces.py | 44 | 0 | 100 % |
| data/quality.py | 113 | 5 | 96 % |
| data/resample.py | 40 | 1 | 98 % |
| data/repository.py | 241 | 3 | 99 % |
| data/health.py | 88 | 2 | 98 % |
| data/providers/mock_provider.py | 109 | 5 | 95 % |
| data/providers/csv_provider.py | 120 | 11 | 91 % |
| **gesamt (inkl. Later-Phase-Stubs)** | **1684** | **64** | **96 %** |

Die „Miss"-Zeilen sind überwiegend defensive Fehlerpfade (unbekannte Zeitzone, korrupte
Parquet-Zeit, Provider-Live-Streams) und `TYPE_CHECKING`-Zweige.

## 2. Fehler / Warnungen

- **Keine** Test-Fehler.
- **Keine** Test-Warnings (`pytest -W error` läuft ebenfalls grün).
- **Keine** ruff- oder mypy-Meldungen.
- Umgebungs-Hinweis (kein Projektfehler): `make` ist Teil der Xcode Command Line Tools und hier
  nicht installiert → `make check` funktioniert nicht; Ersatz ist **`./scripts/check.sh`**.
  `git` fehlt aus demselben Grund → das Repo ist noch nicht unter Versionskontrolle (Phase-0-Rest).
- `uv python install` gab eine Warnung `Failed to patch the install name of the dynamic library`
  aus — betrifft nur das Kompilieren nativer C-Extensions; alle Abhängigkeiten (pydantic, pyarrow,
  pyyaml, pytest, ruff, mypy) kamen als fertige Wheels, keine Auswirkung.

## 3. Erstellte / geänderte Dateien

### Quellcode (implementiert, `src/trading_agent/`)
| Datei | Zweck |
|-------|-------|
| `core/enums.py` | Alle Enums der Datenschicht (Timeframe mit `.seconds`, AssetClass, Exchange, TradingPriority, DataKind, DataQualityCode/Severity, ProviderHealth, …) |
| `core/time.py` | UTC-Normalisierung, Epoch-Konvertierung, Timeframe-Alignment (inkl. W1→Montag), `resolve_local_time` (DST: Lücke ≠ Mehrdeutigkeit), `iter_bar_opens` |
| `core/clock.py` | `Clock`-Protocol + `SystemClock` / `SimClock` / `FixedClock` |
| `core/models.py` | 12 Marktdaten-/Status-Modelle, alle frozen · `extra=forbid` · `schema_version` · `available_time` (Point-in-Time-Marker) |
| `core/version.py` | `SCHEMA_VERSION`, `REPOSITORY_LAYOUT_VERSION` |
| `config/loader.py` | `load_yaml` (schema_version-Pflicht), `config_hash`, `DataFoundationConfig` (Pydantic; `mode=live` abgelehnt) |
| `utils/logging.py` | `configure_logging` (JSON), `redact` (Secret-Maskierung), `JsonFormatter` |
| `refdata/models.py` | Instrument, SymbolMapping, SessionSpec, TradingCalendarSpec, HalfDay, CorporateAction, FeeSchedule, MarginTier |
| `refdata/instruments.py` | `InstrumentMaster` (Registry, `scan_universe` Tier1+2, Point-in-Time-Handelbarkeit) |
| `refdata/symbols.py` | `SymbolMapper` (kanonisch ↔ provider, Aliase) |
| `refdata/calendar.py` | `TradingCalendar` (`is_open`/`next_open`), `resolve_session`, `active_sessions` |
| `refdata/seed.py` | Eingebaute Seed-Daten (7 Instrumente, 5 Kalender, 3 Sessions, Mappings); `MVP_SYMBOLS = (BTCUSDT, ETHUSDT)` |
| `data/interfaces.py` | Provider-ABCs (Historical/Live OHLCV, Quote, Trade, Orderbook, Funding, OpenInterest, News, Macro) + `ProviderStatus` |
| `data/quality.py` | `check_ohlcv_series`, `check_session_resolution`, `sort_ohlcv`, `deduplicate_ohlcv`, `QualityPolicy` |
| `data/resample.py` | `resample_ohlcv` (vollständigkeitsgeprüft, look-ahead-frei via `horizon`) |
| `data/repository.py` | `MarketDataRepository` — Parquet (OHLCV/Funding) + SQLite (Meta/News/Macro), `as_of`-Reads, `dataset_fingerprint`, `ingestion_log` |
| `data/health.py` | `HealthTracker` / `HealthRegistry` / `HealthPolicy` |
| `data/providers/mock_provider.py` | `MockMarketDataProvider` (deterministisch, alle Datenarten) |
| `data/providers/csv_provider.py` | `CsvMarketDataProvider` (OHLCV/News/Macro, UTC-Pflicht, Point-in-Time) |

### Tests (`tests/`)
`conftest.py` (Fixtures: `fixed_clock`, `sim_clock`, `make_bar`, `make_series`, `csv_data_dir`),
`unit/test_{time,clock,enums,models,refdata,quality,resample,health,repository,providers,config_loader,logging}.py`,
`integration/test_data_pipeline.py`.
Testdaten: `tests/data/csv/ohlcv/{BTCUSDT_M5,ETHUSDT_M5,BADTZ_M5}.csv`, `tests/data/csv/{news,macro}.csv`.

### Projekt / Tooling
`pyproject.toml` (hatchling, deps: pydantic/pyarrow/pyyaml/tzdata; dev: pytest/ruff/mypy),
`uv.lock` (25 Pakete gepinnt), `Makefile` (uv-basiert), `scripts/check.sh`,
`.env.example` (bereinigt), `.gitignore` (data/repository, data/state, uv.lock behalten),
`requirements*.txt` (jetzt Verweis auf pyproject/uv.lock).

## 4. Exit-Gate — Einzelprüfung

| Kriterium | Status | Beleg |
|-----------|--------|-------|
| alle Tests erfolgreich | ✅ | 193 passed, 0 failed, 0 warnings |
| Data Models funktionieren | ✅ | `test_models.py` (Validierung, frozen, `extra=forbid`, `available_time`), 44 Tests |
| Repository funktioniert | ✅ | `test_repository.py` — Roundtrip Parquet, Zeitfenster, Merge/Dedup, Coverage, Persistenz über Instanzen |
| Mock/CSV-Pipeline funktioniert | ✅ | `test_providers.py` + Integrationstest; kein Netzwerk, keine Accounts |
| Data Quality funktioniert | ✅ | `test_quality.py` — fehlende/doppelte/unsortierte Bars, ungültige OHLC/Volumen, stale, Zukunft, Symbol-/TF-Mismatch, kalenderbewusste Lücken, DST |
| Point-in-Time-Regeln getestet | ✅ | Repo-`as_of`, News-/Makro-Revisionen, `TIMESTAMP_IN_FUTURE`, Resample-`horizon`, `test_point_in_time_read_never_returns_future_bars`, `test_resample_is_look_ahead_free_vs_future_data` |
| BTC/ETH-Datenpfad funktioniert | ✅ | `test_mvp_btc_eth_pipeline_end_to_end` (Provider→Quality→Resample M5→H1→H4→D1→Repository→Read) |
| Multi-Asset-Architektur vorbereitet | ✅ | Seed enthält Crypto/Altcoin/Gold/Forex/Equity/ETF; Kalender-Typen 24/7 + weekend_gap + reguläre Börse; `AssetClass`-Enum vollständig |
| Health-Monitoring funktioniert | ✅ | `test_health.py` — HEALTHY/DEGRADED/UNAVAILABLE, Hysterese, Staleness, Registry `worst()` |
| keine Trading-Logik vorzeitig | ✅ | `strategy/`, `scanner/`, `viz/`, `risk/`, `execution/` bleiben Docstring-Stubs (0 Stmts). Keine Setup-/Signal-/Order-/AI-/Allocation-Logik. |
| keine privaten Accounts verbunden | ✅ | Nur Mock + lokale CSV. Provider-ABCs definieren keine Auth. |
| keine echten API-Keys verwendet | ✅ | `.env.example` nur Platzhalter; kein Code liest Keys; kein Netzwerkzugriff im gesamten Testlauf |

**Architektur-Regel (eine Strategy Engine für Backtest/Paper/Live):** in Phase 1 nichts zu
implementieren; die Datenschicht ist engine-agnostisch (keine Backtest-Sonderpfade). Der
`Clock`-Mechanismus + `as_of` sind die Grundlage dafür, dass Phase 2/8/9 dieselbe Engine
gegen unterschiedliche Zeit-/Datenquellen fahren können.

## 5. Bekannte Design-Entscheidungen in Phase 1

1. **Point-in-Time-Marker `available_time`** auf jedem Record: Marktdaten leiten ihn ab
   (OHLCV → `close_time`, Ticks → `ts`), News/Makro tragen ihn als **explizites Feld**
   (echte Veröffentlichungszeit; Revisionen über spätere `available_time`).
2. **Naive Timestamps werden abgelehnt**, nie als UTC angenommen — im Modell **und** im
   CSV-Provider (dort als `CsvProviderError`, d. h. ein sichtbarer Timezone-Fehler).
3. **DST**: `resolve_local_time` unterscheidet *nicht existierende* (Frühjahrslücke) von
   *mehrdeutigen* (Herbst-Rückstellung) Ortszeiten und wirft in beiden Fällen — die Session-/
   Quality-Schicht macht daraus einen `DST_AMBIGUOUS`-Befund statt zu raten.
4. **Repository-Layout**: OHLCV/Funding als Parquet (`instrument=…/timeframe=…/data.parquet`,
   atomarer Replace über `.tmp`), Metadaten + News + Makro in **einer** SQLite-Datei
   (`meta.sqlite`, WAL). News/Makro in SQLite, weil sie klein sind und flexible
   Point-in-Time-/Revisions-Abfragen brauchen.
5. **`dataset_fingerprint`** (SHA-256 über sortierte OHLC-Werte, optional `as_of`-gefiltert)
   als Grundlage für das `RunManifest` in Phase 2.
6. **Health-Hysterese**: laufende Fehlerserie ⇒ mind. DEGRADED; ≥ 3 Fehler in Folge oder
   Fehlerquote ≥ 60 % (ab 5 Samples) ⇒ UNAVAILABLE; Staleness ⇒ DEGRADED.

## 6. Offene Punkte für Phase 2 (Research / Backtesting)

| # | Punkt | Anmerkung |
|---|-------|-----------|
| P2-1 | **`git init` + erster Commit** (Phase-0-Rest) | braucht `git` (Xcode CLT oder Homebrew). Danach `.pre-commit-config.yaml` mit gitleaks/ruff/mypy. |
| P2-2 | `research/dataset.py` — Point-in-Time-Feature-Bau | baut auf `repository.read_ohlcv(as_of=…)` + `resample_ohlcv(horizon=…)` auf; nur expanding/rolling Statistiken. |
| P2-3 | `engine/backtest.py` — Event-Loop mit `SimClock` | `SimClock` taktet Bar-für-Bar; Fill-/Kostenmodell aus `docs/strategy/backtest-labeling.md` §5/§6; SL-vor-TP-Konvention. |
| P2-4 | `research/registry.py` — `RunManifest` | Felder: `code_sha` (sobald git da ist), `config_hash` (vorhanden), `dataset_version`+`dataset_fingerprint` (vorhanden), `seed`, Zeitraum, `strategy_version`. |
| P2-5 | `research/validation.py` / `robustness.py` | 50/25/25-Split chronologisch, Walk-Forward 6/2/2, Monte-Carlo — Parameter in `config/anti_overfitting.example.yaml`. |
| P2-6 | **`DataQualityStatus.blocks_trading` → Pipeline-Gate** | die Backtest-Pipeline muss bei `blocks_trading` für Instrument/TF ein `NO_TRADE` erzeugen (Strategy Engine kommt erst Phase 3, aber der Hook gehört in die Pipeline-Schleife). |
| P2-7 | Realer BTC/ETH-Datensatz | aktuell nur Mock + Mini-CSV. Für einen aussagekräftigen Backtest braucht Phase 2 einen echten historischen M1/M5-Datensatz (öffentlich, kein Account) → ins Repository laden, `dataset_version` vergeben. |
| P2-8 | Bar-Semantik-Doku | in `backtest-labeling.md` §1 festgehalten; Phase-2-Loop muss `information_cutoff == signal_bar.close_time` erzwingen (Test „Zeitreise-Immunität"). |
| P2-9 | `data/providers` Live-Pfade | `stream_ohlcv` etc. sind definiert, aber nur rudimentär (Mock-Replay). Echtes Streaming erst Phase 8/9. |
| P2-10 | M30/H1 als Resample-Zwischenstufen | aktuell wird H4/D1 aus H1 gebildet; falls Setups M30 brauchen (Spec nutzt M15/M5), M15→M30-Pfad in Phase 3 ergänzen. |

## 7. Wie man es lokal nachprüft

```bash
cd ~/AI-Trading-Agent
export PATH="$HOME/.local/bin:$PATH"      # uv
uv venv --python 3.12
uv pip install -e ".[dev]"
./scripts/check.sh                         # ruff + mypy + 193 Tests
.venv/bin/pytest --cov=trading_agent --cov-report=term-missing
```
