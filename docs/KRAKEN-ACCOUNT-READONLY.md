# Kraken Account — READ-ONLY Anbindung (2026-08-30)

Sichere, **rein lesende** Anbindung eines echten Kraken-Kontos. Keine Order-, keine
Withdraw-Rechte, keine Echtgeld-Ausführung, keine Secrets im Repo.

## Was gebaut wurde

| Datei | Inhalt |
|---|---|
| `src/trading_agent/security/secrets.py` | `Secret` (redigiert sich in `repr`/`str`, Klartext nur via `.reveal()`), `get_secret` (ENV → macOS-Keychain), `missing_secrets`, `redact` |
| `src/trading_agent/data/providers/kraken_account.py` | `sign_request` (HMAC-SHA512, **gegen Krakens offiziellen Testvektor verifiziert**), `KrakenPrivateClient` (signierter POST, Nonce-Guard, Retry mit neuer Nonce), `KrakenAccountAdapter` (READ-ONLY) |
| `scripts/kraken_account_test.py` | READ-ONLY Verbindungstest |
| `tests/unit/test_kraken_account.py` | 16 Tests (Signatur-Vektor, Secret-Redaktion, respx-REST, Auth-/API-Fehler, Nonce-Retry, Sicherheits-Assertion, kein Secret-Leak) |

### Sicherheitsgarantien im Code

- `KrakenAccountAdapter` ist **kein** `BrokerAdapter`, hat **kein** `submit`/`cancel` (per Test geprüft).
- `KrakenPrivateClient.call` erlaubt nur eine feste Whitelist von `Query`-Endpunkten
  (`_ALLOWED_METHODS`) plus `AddOrder` **ausschließlich** für die `validate=true`-Assertion.
- `assert_read_only()` ruft `AddOrder` mit `validate=true` (Krakens Dry-Run — platziert nie
  eine Order) und **erwartet** `EGeneral:Permission denied`. Wird der Call akzeptiert →
  `KrakenAccountError` („Key HAT Order-Rechte → widerrufen").
- Ohne `KRAKEN_API_KEY`/`KRAKEN_API_SECRET`: `status() == UNAVAILABLE`, jeder Call wirft
  `KrakenAuthError`. Nichts wird simuliert.
- Secrets werden nie geloggt: `Secret` redigiert sich, Fehlertexte laufen durch `redact()`.

## Anleitung für dich

### 1. Kraken-Permissions

Kraken → **Settings → API → Add key**, nur folgende Häkchen:

**AN:**
- Funds → **Query funds**
- Orders & Trades → **Query open orders & trades**
- Orders & Trades → **Query closed orders & trades**
- Orders & Trades → **Query ledger entries**

**AUS (wichtig):** Create & modify orders · Cancel/close orders · Deposit funds ·
Withdraw funds · WebSockets interface · Manage/view subaccounts

Kein OTP/2FA auf dem API-Key selbst. Optional: IP-Allowlist auf dem Key.

### 2. ENV-Variablen

```
KRAKEN_API_KEY=<API Key aus Kraken>
KRAKEN_API_SECRET=<Private Key aus Kraken, Base64>
```

Ablegen in **einer** von zwei Varianten:

- **`.env`** im Projektroot (bereits in `.gitignore`), danach `chmod 600 .env`
- **macOS-Keychain:**
  ```
  security add-generic-password -s trading-agent -a KRAKEN_API_KEY -w
  security add-generic-password -s trading-agent -a KRAKEN_API_SECRET -w
  ```

Nie in `.env.example`, nie in `config/*.yaml`, nie in den Chat.

### 3. Testbefehl

```
python scripts/kraken_account_test.py
```

Optionen: `--json` (JSON-Ausgabe), `--show-balances` (Beträge im Klartext statt `***`).

Erwartete Ausgabe bei korrektem read-only Key:
```
Status: CONNECTED
  Server-Zeit: … (Clock-Skew …s)
  Konto (EUR): equity=*** balance=*** free_margin=*** …
  Offene Orders: 0   Offene Positionen: 0
  Ledger-Lesezugriff: True
  READ-ONLY-Assertion: cannot_trade=True — EGeneral:Permission denied — Key kann nicht handeln
  Provider-Health: healthy   orders_sent=0
```

`Status: BLOCKED` → ENV fehlt (exakte Namen werden ausgegeben).
`cannot_trade=False` oder `KrakenAccountError` → der Key hat zu viele Rechte, widerrufen und neu anlegen.

## Danach

Bybit-Account-Adapter (analog, `BYBIT_API_KEY`/`BYBIT_API_SECRET`, read-only), dann
Pepperstone/cTrader.
