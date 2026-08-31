# Stufe G — 24/7 Operations (2026-08-31)

Masterplan §51–§62 (Watchdog/Recovery, Audit-Log, Alert-System, System-Health, Daily/Weekly-Report).

## Ausgangslage (bereits vorhanden)

| Baustein | Datei | Status |
|---|---|---|
| 24/7-Supervisor: Recovery aus atomarem Snapshot, WS-Reconnect (2-stufig), Stale→REST-Backfill, Watchdog, Wall-Clock-Deadlines | `runtime/supervisor.py::LiveSupervisor` | ✅ (M-01) |
| `SystemHealth` (Provider-/Broker-Health, Heartbeat-Staleness, Data-Blocks, Kill-Switch-State) | `ops/health.py` | ✅ |
| `KillSwitch` (Paper: kein echter Kill, nur State) | `safety/kill_switch.py` | ✅ |
| Metrics-Registry (Histogramme, Counter) | `ops/metrics.py` | ✅ |
| Snapshot-Store + Recovery (atomar, schema-versioniert) | `state/store.py`, `state/recovery.py` | ✅ |

## Neu gebaut in Stufe G

| Datei | Masterplan | Inhalt |
|---|---|---|
| `safety/audit_log.py` — `AuditLog` (war 4-Zeilen-Stub) | §51 | **Hash-verkettetes, append-only JSONL-Audit-Log.** Jede Zeile trägt `prev_hash` (SHA-256 der Vorzeile) + eigenen `entry_hash` über den kanonischen Body. `verify()` erkennt jede nachträgliche Änderung + `seq`-Sprünge und meldet `broken_at`. `fsync` nach jedem Append. Wiedereröffnen setzt die Kette an der Spitze fort. |
| `ops/notify.py` — `Notifier` + Sinks (war 4-Zeilen-Stub) | §56/§57 | Severity (DEBUG/INFO/WARNING/CRITICAL). **Dedup** (gleicher `dedup_key` in `dedup_window_s` → verworfen). **Rate-Limit** (`max_per_window` je `rate_window_s`; Überschuss gezählt → eine Sammelmeldung „N unterdrückt" bei der nächsten durchgelassenen Nachricht). CRITICAL umgeht das Rate-Limit. Sinks: `ConsoleSink`, `FileSink` (JSONL), `TelegramSink` (**UNAVAILABLE** ohne `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` — kein Fake-Versand; `transport`-Injection für Tests). |
| `ops/reports.py` — `build_report()` | §60–§62 | `ReportInputs` (Top-Opportunities, ausgegebene Signale, Portfolio-Intelligence-`as_dict()`, Market-Breadth, System-Health, Paper-Performance, Notizen) → `Report` mit `as_text()` / `as_dict()`. Reine Aufbereitung. NO-TRADE-Zeitraum wird als solcher benannt („lieber kein Trade als ein schlechter"). |
| `tests/unit/test_stage_g_ops.py` | | 11 Tests |

## Verifiziert

```
uv run pytest -q            → 1022 passed
uv run mypy --strict src/   → Success: no issues found in 193 source files
uv run ruff check / format  → clean
```

Audit-Log: 3 Einträge → `verify().ok`; eine manipulierte Zeile → `ok=False, broken_at=2`;
Wiedereröffnen setzt fort. Notifier: Dedup verwirft die Wiederholung, nach `dedup_window_s` wieder
zugelassen; 5 Nachrichten bei `max_per_window=3` → 3 emittiert + 2 rate-limited + Sammelmeldung;
CRITICAL kommt trotz Limit durch. TelegramSink: ohne Token `available()=False` + `sent=0`; mit
Token (monkeypatch) → 1 Versand über den injizierten Transport, `chat_id` korrekt.

## Status

**DONE**
- Hash-Chain-Audit-Log, Notifier mit Anti-Spam, Report-Generator — implementiert, getestet, mypy-strict-clean.
- Watchdog/Recovery/Health: bereits durch `LiveSupervisor` (M-01) abgedeckt.

**PARTIAL**
- **Verdrahtung in `run_live_daemon.py`**: `AuditLog` + `Notifier` + periodischer `build_report`-Aufruf noch nicht an die LivePipeline-Events gehängt (`DecisionMade`→Audit+Notify, täglicher Report-Tick). Nächster kleiner Schritt, kein Blocker.
- `ops/watchdog.py` / `ops/monitoring.py` bleiben 4-Zeilen-Stubs — ihre Funktion liegt im `LiveSupervisor`. Konsolidierung (eigene `Watchdog`-Klasse, die der Supervisor nutzt) ist Kosmetik, nicht dringend.
- `safety/error_handling.py` (Retry/Backoff-Klassen) noch Stub — die Adapter haben je eigene Retry-Logik; eine gemeinsame Basis ist Aufräumarbeit.

**BLOCKED**
- **Telegram-Live-Versand**: braucht `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` in `.env` (Nutzer legt Bot via @BotFather an). Bis dahin laufen Alerts über `ConsoleSink`/`FileSink`. Der Sink ist fertig.

**NEXT**
- Stufe H — UI: `api/` (FastAPI REST + WS über den EventBus), `chart/annotations.py` (Lightweight-Charts-Payloads), 10-Tab-Frontend (Overview, Market Scanner, Top Opportunities, Chart/Analysis, Signals, My Portfolios, Paper Trading, Performance, News/Macro, System Health). Bewusst als Letztes.
- Danach Stufe I — Production: Deployment, Kill-Switch-Drills, Parität Backtest ≡ Paper ≡ Demo. Echtgeld weiterhin ausgeschlossen.
- Kurzfristig: `AuditLog`/`Notifier`/Report an den Live-Daemon hängen.
