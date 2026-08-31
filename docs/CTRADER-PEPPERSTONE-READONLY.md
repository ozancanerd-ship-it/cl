# cTrader / Pepperstone — READ-ONLY Anbindung (2026-08-30)

Live-Marktdaten (FX + XAUUSD) über die **cTrader Open API**. Cloud-tauglich, reines
Netzprotokoll, **keine Windows-/Terminal-Abhängigkeit**. READ-ONLY: kein Order-Nachrichtentyp
im Adapter, OAuth-Scope `accounts` (nicht `trading`).

## Was gebaut wurde

| Datei | Inhalt |
|---|---|
| `src/trading_agent/data/providers/ctrader.py` | `authorize_url` / `exchange_code` / `refresh_access_token` (OAuth2), `CTraderClient` (JSON-WS: App-Auth → Account-Auth → Symbols/Trendbars/Spots, Heartbeat), `CTraderAdapter` (READ-ONLY `fetch_ohlcv` + `fetch_quote`) |
| `scripts/ctrader_link.py` | OAuth-Verknüpfung — schreibt Tokens in `.env` |
| `scripts/ctrader_account_test.py` | READ-ONLY Connectivity-Test (XAUUSD/EURUSD/GBPUSD/USDJPY) |
| `tests/unit/test_ctrader.py` | 11 Tests (Auth-Flow, Trendbar-Dekodierung, Spot-Snapshot, kein Order-Pfad) |

### Technik

- **Endpunkt:** `wss://demo.ctraderapi.com:5036` (bzw. `live…`) — JSON-Envelope
  `{"clientMsgId","payloadType","payload"}`. Aus dieser Umgebung **erreichbar** (TLS+WS-Handshake OK).
- **Preise:** alle cTrader-Open-API-Preise sind Integer ×100 000 → `_PRICE_SCALE`. Trendbar:
  `low` absolut, `deltaOpen/High/Close` relativ. `utcTimestampInMinutes × 60` = Epoch-Sekunden.
- **payloadType-Konstanten** (verifiziert an der Doku): App-Auth 2100/2101, Account-Auth
  2102/2103, GetAccounts 2149/2150, SymbolsList 2114/2115, SubscribeSpots 2127/2128,
  SpotEvent 2131, GetTrendbars 2137/2138, ErrorRes 2142, Heartbeat 51.
- **Trendbar-Perioden:** M1=1, M5=5, M15=7, M30=8, H1=9, H4=10, D1=12.

### Sicherheitsgarantien

- `CTraderAdapter` ist **kein** `BrokerAdapter`, hat **kein** `submit`/`cancel` (per Test).
- Kein Modul-Name enthält `NEW_ORDER`/`NewOrder` (per Test).
- OAuth-Scope **`accounts`** — Spotware-Definition: „just have access to user trading account
  data" (nur lesen). Der `trading`-Scope wird nie angefordert.
- Credentials über `security.secrets` (ENV → Keychain), nie im Code, nie ins Log.
- Ohne ENV → `status() == UNAVAILABLE`, Calls werfen `CTraderUnavailable`.

## Anleitung für dich

### 1. Open-API-App registrieren

1. https://openapi.ctrader.com öffnen, mit deiner **cTrader-ID** einloggen.
2. **Add Application** (o. „Create App"). Name z. B. `trading-agent-readonly`.
3. **Redirect URI:** `http://localhost/` eintragen (exakt so — wird später gebraucht).
4. Speichern → du bekommst **Client ID** und **Client Secret**.
5. Beide in `.env` eintragen (die leeren Zeilen sind schon da):
   ```
   CTRADER_CLIENT_ID='...'
   CTRADER_CLIENT_SECRET='...'
   ```
   Danach `chmod 600 .env`.

### 2. Account verknüpfen (OAuth, READ-ONLY)

Im Terminal:
```
cd ~/AI-Trading-Agent
source .venv/bin/activate
python scripts/ctrader_link.py --redirect-uri http://localhost/
```

- Das Skript zeigt eine **Autorisierungs-URL** (Scope `accounts`).
- URL im Browser öffnen → mit cTrader-ID einloggen → dein **Pepperstone-Demo-Konto** auswählen
  → **Authorize**.
- Der Browser landet auf `http://localhost/?code=XXXX` — die Seite lädt nicht, das ist normal.
  **Kopiere die komplette URL aus der Adresszeile.**
- Zurück im Terminal: die URL einfügen, Enter.
- Das Skript tauscht den Code gegen Tokens, listet deine Konten, wählt (bei einem Konto)
  automatisch die `ctidTraderAccountId` und schreibt alles nach `.env`.
  **Tokens werden nicht angezeigt.**

### 3. Connectivity-Test

```
python scripts/ctrader_account_test.py
```

Erwartete Ausgabe:
```
Status: CONNECTED   Umgebung: demo
  Verbindung: app+account auth OK  (ctidTraderAccountId=…)
  XAUUSD: id=…  M5-bars=…  last_close=…  bid/ask=…/… spread=…
  EURUSD: id=…  M5-bars=…  …
  GBPUSD: …
  USDJPY: …
  OAuth-Scope: accounts (nur lesen)
  Trading-Rechte: NEIN   Withdraw-Rechte: NEIN
  Provider-Health: healthy   orders_sent=0
```

(Am Wochenende sind FX/XAU geschlossen → `M5-bars` aus der letzten Session, evtl.
`kein Spot-Event` — kein Fehler, nichts wird erfunden.)

## Danach

Nach grünem Connectivity-Test: XAUUSD/EURUSD/GBPUSD/USDJPY → `LivePipeline` (dieselbe zentrale
`strategy.evaluate` wie Crypto) → MarketContext → Decision → Paper. Kein Echtgeld, keine Order.
