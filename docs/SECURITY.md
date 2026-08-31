# Security Policy

**Grundsatz:** Read-only, so lange es geht. Keine Echtgeldorders, keine Trading-/Withdraw-/
Transfer-Rechte während Entwicklung und Validierung. Least privilege überall.

## 1. Secrets

| Regel | Umsetzung |
|---|---|
| **Niemals hardcoden** | Kein API-Key/Secret/Token im Code. `grep -rniE "api_key\s*=\s*[\"']" src/` = leer. |
| **Nur ENV / OS-Keychain** | `security/secrets.py::get_secret` — ENV zuerst, dann macOS-Keychain (`security find-generic-password -s trading-agent -a <VAR>`). Cloud: Secret-Manager der Plattform → als ENV injiziert. |
| **Nie ins Repo** | `.gitignore`: `.env`, `.env.*` (außer `.env.example`), `*.pem`, `*.key`, `secrets/`. `.env.example` enthält **nur Platzhalter-Namen**. |
| **Nie ins Log** | `Secret.__repr__`/`__str__` = `***redacted***`. Klartext nur via `.reveal()` an der Signier-/Auth-Stelle. Fehlertexte laufen durch `security.secrets.redact()`. `utils/logging.py` hat zusätzlich eine Key-Pattern-Redaction. Getestet (`test_kraken_account`, `test_bybit_account`, `test_binance_account`: „secret never leaks into error"). |
| **Nie in den Chat / Scratchpad** | Bei der Einrichtung trägt der Nutzer Werte lokal in `.env` ein; sie werden nie an den Assistenten übermittelt. |
| **Dateirechte** | `.env` → `chmod 600`. |
| **Rotation** | API-Keys alle paar Monate rotieren; bei Verdacht sofort im Portal widerrufen + neu anlegen. |

## 2. API-Key-Berechtigungen (Ist-Stand)

| Broker | Modus | Rechte | Verifiziert durch |
|---|---|---|---|
| **Kraken** | READ-ONLY | Query funds / open+closed orders & trades / ledger. KEINE Order-, KEINE Withdraw-Rechte. | `assert_read_only()` → `AddOrder(validate=true)` → `Permission denied` |
| **Bybit EU** | READ-ONLY | `readOnly=1` (bindend), `can_withdraw=False`. | `assert_read_only()` → `GET /v5/user/query-api` |
| **Binance** | READ-ONLY | `enableReading` only; withdraw/transfer/spot-trade/futures-trade = False. | `assert_read_only()` → `GET /sapi/v1/account/apiRestrictions` |
| **Pepperstone / cTrader** | PAUSIERT | OAuth-Scope `accounts` (nur lesen) vorbereitet; nicht aktiv. | — |
| **Trade Republic** | NICHT VERBUNDEN | Später, nur manueller Import (`source=manual`). | — |

**IP-Allowlist:** empfohlen für alle Keys; aktuell offen (`ip_restricted=False`) — im jeweiligen
Portal die feste IP eintragen.

## 3. Architektur-Trust-Boundaries

```
[ externe API/Broker ]  ── nur read-only Adapter ──►  [ Normalized Market Data ]
                                                              │  (keine Secrets, keine Broker-Logik)
                                                              ▼
[ MarketContext → Analyse → Regime → Score → Strategy → Risk → Portfolio → Paper ]
                                                              │  (kein Netz, kein Broker, kein Order-Code)
                                                              ▼
                                                     [ EventBus → UI / Alerts ]
```

- Die **Strategy Engine importiert nie** einen Provider-/Broker-/Account-Adapter
  (`grep -r "import.*providers" src/trading_agent/strategy/` = leer).
- **Kein Order-Nachrichtentyp** in irgendeinem Account-Adapter (`submit`/`cancel` existieren
  nicht; Binance/Kraken/Bybit-Adapter haben Pfad-Whitelists).
- `orders_sent == 0` wird in **jedem** Live-/Paper-/Backtest-Pfad hart asserted.
- MT5 (`data/providers/mt5.py`) ist `platform_only="windows"` und wird von der zentralen Engine
  nie importiert — optionaler Feeder, nie ein Muss.

## 4. Datenintegrität (kein Look-ahead / kein Fake)

- `MarketContext(information_cutoff=…)` wirft bei jeder Bar/News aus der Zukunft.
- Höhere Timeframes: `resample_ohlcv(..., require_complete=True)` — nur abgeschlossene Ziel-Bars.
- `engine/parity.py`: vorgeladener Replay ≡ streaming Kontext → Look-ahead-Beweis je Lauf.
- Fehlt ein Feed: das Feld bleibt leer / `UNKNOWN` / `DEGRADED` — **nie** synthetische Werte.
- Alle Zeitstempel UTC; `data/quality.py` prüft Vollständigkeit / Frische / Duplikate /
  Timestamp-in-Zukunft / Kalender-Lücken.

## 5. Git / CI

- `git` initialisiert (2026-08-31), Branch `main`. `.gitignore` deckt Secrets + lokale Daten.
- **Offen:** `.pre-commit-config.yaml` mit `gitleaks` + `ruff` + `mypy`; `pip-audit` in CI.
- Commit-Konvention: Co-Authored-By-Trailer, keine Secrets in Messages.

## 6. Was NICHT passieren darf (harte Regeln)

1. Kein Echtgeld-Order-Code während Entwicklung/Validierung.
2. Keine Erweiterung von API-Rechten ohne expliziten Nutzer-Auftrag.
3. Kein Secret in Commit, Log, Traceback, Chat, Scratchpad, Test-Fixture.
4. Kein Provider/Broker bestimmt die zentrale Strategy Engine.
5. Kein Fallback auf erfundene/synthetische Marktdaten.

## 7. Incident-Response (Key-Leak)

1. Key im Broker-Portal **sofort löschen**.
2. Prüfen, wo er hinkam (Commit-History `git log -p -S<fragment>`, Logs).
3. Neuen Key mit minimalen Rechten anlegen, `.env` aktualisieren, `chmod 600`.
4. Vorfall in `safety/audit_log.py` (Hash-Chain, Stufe G) protokollieren.
