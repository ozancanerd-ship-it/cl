# Bybit Account — READ-ONLY Anbindung (2026-08-30)

Sichere, **rein lesende** Anbindung eines echten **Bybit-EU-Kontos** (v5-API). Keine Trade-,
keine Withdraw-Rechte, keine Echtgeld-Ausführung, keine Secrets im Repo. Gleiches Prinzip wie
Kraken (`docs/KRAKEN-ACCOUNT-READONLY.md`).

## Host

**Bybit EU / EEA → `https://api.bybit.eu`** (offiziell laut
<https://bybit-exchange.github.io/docs/v5/guide>). Der Adapter ist fest darauf konfiguriert —
**kein** `api.bybit.com`, **kein** Demo-/Testnet-Host. Ein auf `bybit.eu` erstellter Key wird
von `api.bybit.com` mit `retCode 10003 "API key is invalid"` abgelehnt.

## Bybit-„Read-Only"-Key — warum `SpotTrade` in den Rechten steht

Bybit listet in `permissions` weiterhin die Datendomänen (`Spot`, `Derivatives`, …), für die
der Key Lesezugriff hat — inkl. Einträgen wie `SpotTrade`. **Bindend** ist das Feld
`readOnly`: bei `readOnly == 1` kann der Key **nicht** handeln und **nicht** auszahlen, egal
was in `permissions` steht (Bybit erzwingt das serverseitig). `assert_read_only()` wertet
daher primär `readOnly` aus und prüft zusätzlich, dass kein `Withdraw` im Wallet-Scope steht.

## Was gebaut wurde

| Datei | Inhalt |
|---|---|
| `src/trading_agent/data/providers/bybit_account.py` | `sign_v5` (HMAC-SHA256, gepinnter Vektor), `BybitPrivateClient` (signierter GET, Timestamp-Retry, Pfad-Whitelist), `BybitAccountAdapter` (READ-ONLY) |
| `scripts/bybit_account_test.py` | READ-ONLY Verbindungstest |
| `tests/unit/test_bybit_account.py` | 15 Tests |

### Sicherheitsgarantien im Code

- `BybitAccountAdapter` ist **kein** `BrokerAdapter`, hat **kein** `submit`/`cancel` (per Test).
- `BybitPrivateClient.get` erlaubt nur eine feste Whitelist rein lesender v5-Pfade
  (`_ALLOWED_PATHS`) — u. a. `/v5/user/query-api`, `/v5/account/wallet-balance`,
  `/v5/position/list`, `/v5/order/realtime`, `/v5/account/transaction-log`.
- **Read-only-Nachweis:** `assert_read_only()` ruft `GET /v5/user/query-api` — Bybit liefert die
  Rechte des Keys direkt (`readOnly`-Flag + `permissions`). Bestätigt nur, wenn **keine**
  Trade-Gruppe (`ContractTrade`/`Spot`/`Options`/`Derivatives`/`CopyTrading`/`BlockTrade`/`Earn`)
  belegt ist **und** `Wallet` kein `Withdraw` enthält. Sonst → `BybitAccountError`.
- Ohne `BYBIT_API_KEY`/`BYBIT_API_SECRET`: `status() == UNAVAILABLE`, Calls werfen
  `BybitAuthError`. Nichts simuliert.
- Secrets nie im Log: `Secret` redigiert sich, Fehlertexte durch `redact()` (per Test geprüft).

## Ergebnis (2026-08-30, Bybit-EU-Konto)

Test **CONNECTED** mit dem bestehenden `bybit.eu`-Read-Only-Key:

| Prüfung | Ergebnis |
|---|---|
| Verbindung + Signatur (`api.bybit.eu`) | ✔ |
| `readOnly`-Flag | **1 (bindend)** — Key kann nicht handeln/auszahlen |
| `can_withdraw` | **False** |
| Offene Orders / Positionen | 0 / 0 |
| Wallet-Balance / Transaktions-Log lesbar | ✔ (Beträge maskiert) |
| `orders_sent` | 0 |
| IP-Allowlist des Keys | `['*']` — **offen**, Einschränkung empfohlen |
| Key-Ablauf | **2026-11-30** — Bybit-EU erzwingt ~3-Monats-Limit, vorher erneuern |

`permissions` enthält `Spot: ['SpotTrade']` + `Derivatives: ['DerivativesTrade']` — das sind
**Lese-Domänen**; `readOnly=1` verhindert jeden Schreibzugriff.

## Anleitung für dich (falls du später einen neuen Key brauchst)

### 1. Bybit-EU-Key erstellen

**bybit.eu** → **API** → **Create New Key** → **System-generated API Keys**.

- **API Key Permissions:** **Read-Only**
- Module: **Unified Trading** (Positions/Orders — Lese-Sicht), **Wallet** (Guthaben-Ansicht)
- **NICHT:** Withdraw, Transfer, Sub-account-Verwaltung
- **IP-Beschränkung:** deine feste IP eintragen (statt `*`)
- Key + Secret werden **einmalig** angezeigt.

### 2. ENV-Variablen

```
BYBIT_API_KEY=<API Key aus Bybit>
BYBIT_API_SECRET=<API Secret aus Bybit>
```

Ablegen wie bei Kraken: in `.env` (chmod 600, in `.gitignore`) **oder** macOS-Keychain:
```
security add-generic-password -s trading-agent -a BYBIT_API_KEY -w
security add-generic-password -s trading-agent -a BYBIT_API_SECRET -w
```
Nie in `.env.example`, nie in `config/*.yaml`, nie in den Chat.

### 3. Testbefehl

```
python scripts/bybit_account_test.py
```

Optionen: `--json`, `--show-balances`.

Erwartete Ausgabe bei korrektem read-only Key:
```
Status: CONNECTED
  Server-Zeit: …  (Clock-Skew …s)
  API-Key: read_only_flag=True  trade_permissions=[…Lese-Domänen…]  can_withdraw=False
           IP-Allowlist=…  expires=…
  Konto (USD): equity=***  wallet_balance=***
  Offene Orders: 0   Offene Positionen: 0
  Transaktions-Log lesbar: True
  READ-ONLY-Assertion: no_trade_no_withdraw=True — readOnly=1; permissions={…}
  Provider-Health: healthy   orders_sent=0
```

- `Status: BLOCKED` → ENV fehlt.
- `retCode 10003 "API key is invalid"` → Key stammt nicht von `api.bybit.eu` (falsche Region)
  oder Tippfehler beim Kopieren.
- `BybitAccountError … Withdraw-Rechte` → Key im Bybit-Portal bearbeiten, Withdraw entfernen.
- `BybitAccountError … NICHT read-only` → Key ist `readOnly=0` **und** hat Trade-Rechte →
  im Bybit-Portal auf „Read-Only" umstellen.
- `retCode 10010` → IP nicht in der Allowlist des Keys.

## Danach

Pepperstone/cTrader-Account (OAuth2 statt HMAC — anderes Muster).
