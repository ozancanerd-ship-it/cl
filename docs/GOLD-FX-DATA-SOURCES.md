# Gold / FX — Datenquellen-Bewertung & Empfehlung (2026-08-30)

**Ziel:** 24/7-Cloud → Live Market Data → MarketContext → MTF → Strategy → Decision → Signal →
Risk → Paper Position. **Keine Windows-Abhängigkeit für die zentrale Engine.** Read-only, keine
Order, keine Trading-/Withdraw-Rechte.

Die zentrale Engine (`strategy.evaluate`, `runtime.live_pipeline`, `runtime.supervisor`) ist
**providerfrei** — sie sieht nur `MarketContext`. Ein Broker-/Datenquellen-Adapter ist rein
peripher und über `data.providers.*` austauschbar (wie Kraken/Bybit für Crypto).

---

## 1. Die vier angefragten Varianten

| # | Variante | Cloud-tauglich (Linux, kein GUI) | Live-Marktdaten | Historie | Auth | Bewertung |
|---|---|---|---|---|---|---|
| **A** | **„Pepperstone API"** | — | — | — | — | **Existiert nicht als eigenständige Markt­daten-API.** Pepperstone stellt Marktzugang **ausschließlich** über MT4, MT5 und **cTrader** bereit. „Pepperstone API" = de facto **cTrader Open API** (Variante B) bzw. MT5 (D). |
| **B** | **cTrader Open API** (Spotware) | **✅ ja** — reines Netzprotokoll (Protobuf über TLS-TCP `:5035` **oder** JSON über WebSocket `:5036`), keine GUI, kein Terminal | ✅ Spot-Ticks (Bid/Ask), Trendbars (OHLC), Depth | ✅ historische Trendbars per Request | OAuth2 (App `CTRADER_CLIENT_ID`/`SECRET` + `CTRADER_ACCESS_TOKEN` + `CTRADER_ACCOUNT_ID`), **Demo-Konto reicht**, für Marktdaten **kein** Trading-Recht nötig | **★ beste Variante für unser Ziel.** Läuft in einem Container ohne Umbau. Pepperstone ist cTrader-Broker (Demo kostenlos). Symbol-Auflösung über `ProtoOASymbolsListReq` (numerische IDs je Konto). Heartbeat alle 10 s. |
| **C** | Pepperstone Web (TradingView-basiert) | ✅, aber… | ⚠️ nur via Browser-Session | ✕ | Session-Cookie | **Nicht tragfähig.** Keine offizielle Daten-API, Scraping verstößt gegen die ToS und ist fragil. **Verworfen.** |
| **D** | **MT5 / MetaTrader5** | **✕ nein** — das `MetaTrader5`-Python-Paket läuft **nur unter Windows** und braucht ein **laufendes MT5-Terminal** mit Broker-Login | ✅ Bid/Ask, M1/Ticks | ✅ `copy_rates_range` | `MT5_LOGIN`/`PASSWORD`/`SERVER` + Terminal | **Nur als Fallback.** Windows-Zwang ist mit „keine Windows-Abhängigkeit für die zentrale Engine" unvereinbar. Adapter bleibt gekapselt (`data/providers/mt5.py`), die Engine hängt **nicht** daran. Nutzbar von einer separaten Windows-VM als *zusätzlichem* Feeder in das gemeinsame `MarketDataRepository`. |

---

## 2. Praktische Zusatz-Optionen (für Historie / sofort testbare Live-Daten)

| Quelle | Cloud | Live | Historie | Auth | Hinweis |
|---|---|---|---|---|---|
| **Dukascopy Bulk** (`datafeed.dukascopy.com/datafeed/.../*.bi5`) | ✅ | ✕ (nur Verlauf) | ✅ **Tick ab ~2003**, echtes Bid/Ask, alle Majors + XAUUSD | **keine** | **Beste Historie-Quelle** für Gold + FX. `.bi5` = LZMA-komprimiert, 20-Byte-Records `>IIIff`. Adapter: `data/providers/dukascopy.py` — **verifiziert lauffähig aus dieser Umgebung** (EURUSD + XAUUSD 2024-06-04 real geladen, dekodiert, zu M15 aggregiert; mittlerer Spread EURUSD ~0.18 pip, XAUUSD ~0.38 USD). Vereinzelt transiente `HTTP 503` je Stunde → Adapter retryt 3×, überspringt dann + protokolliert (keine Fake-Bars). |
| **cTrader Open API — historische Trendbars** | ✅ | — | ✅ (Trendbar-Historie, Broker-abhängig ~mehrere Jahre) | OAuth2 | Deckt Historie **und** Live in einer Anbindung ab (Variante B). |
| **OANDA v20 (fxTrade Practice)** | ✅ | ✅ REST + Streaming, echtes Bid/Ask | ✅ Candles | `OANDA_API_TOKEN` (Practice kostenlos) | Sehr saubere REST/Stream-API. Host `api-fxpractice.oanda.com` **erreichbar** (HTTP 401 ohne Token). Solide Alternative/Backup zu cTrader. |
| **Bybit / Kraken — tokenisiertes Gold** (`XAUT` = Tether Gold) | ✅ | ✅ **echtes Bid/Ask + WebSocket** über die **bereits gebauten** Adapter | ✅ Kline-REST | **keine** | **Sofort nutzbar für Gold.** `XAUT/USD` ist 1:1 physisch hinterlegt und folgt XAU/USD mit kleinem Basis-Spread. Bybit `XAUTUSDT` (linear + spot), Kraken `XAUT/USD`, `PAXG/USD`. **Kein FX-Äquivalent.** |
| **Yahoo Finance** (`query1.finance.yahoo.com/v8/finance/chart/...`) | ✅ | ⚠️ **~15 min verzögert, indikativ, kein echtes Bid/Ask** | ✅ (grob) | keine | **Nur Pipeline-Validierung**, nicht handelsqualitätstauglich. `EURUSD=X`/`GBPUSD=X`/`USDJPY=X`/`GC=F`. Erreichbar (HTTP 200). |

---

## 3. Empfehlung

### Dauerhaft für die 24/7-Cloud

**cTrader Open API (Variante B) als primäre Live-Quelle für Gold *und* FX.**

Gründe:
1. **Keine Windows-/Terminal-Abhängigkeit** — reines Netzprotokoll, läuft in einem Linux-Container.
2. **Eine Anbindung** liefert Live-Bid/Ask, Trendbars (OHLC) **und** historische Trendbars.
3. **Pepperstone ist cTrader-Broker** — ein kostenloses Pepperstone-**Demo**-Konto genügt;
   für Marktdaten wird **kein** Trading-Recht benötigt.
4. Credentials rein über ENV (`CTRADER_CLIENT_ID`, `CTRADER_CLIENT_SECRET`,
   `CTRADER_ACCESS_TOKEN`, `CTRADER_ACCOUNT_ID`) — nichts im Repo.

**OANDA v20 Practice** als gleichwertige **Backup-Anbindung** (zweiter `RestMarketData`-Adapter,
Failover über `data/router.py`).

### Historie

**Dukascopy Bulk** für den tiefen Tick-Verlauf (Gold + FX), zusätzlich cTrader-Trendbar-Historie
für die letzten Jahre. Beide PIT-sauber ingestierbar (Dukascopy-Ticks tragen ms-genaue
Zeitstempel; Trendbars sind abgeschlossene Kerzen).

### Was JETZT (ohne Broker-Credentials) geht

- **Gold live, sofort, echt:** `XAUUSD` → Bybit `XAUTUSDT` / Kraken `XAUT/USD` über die
  vorhandenen Adapter + `LiveSupervisor`. Basis-Caveat dokumentiert.
- **FX live:** erst mit cTrader- oder OANDA-Token. Bis dahin: Adapter-Vertrag steht,
  `status() == NOT_AVAILABLE`, **nichts simuliert**. Indikative Yahoo-Daten nur für einen
  Pipeline-Durchstich (klar als „delayed/indicative" markiert).
- **Historie:** Dukascopy-Adapter gebaut, fixture- **und** live-getestet (real geladen +
  dekodiert). Voller Ingest XAUUSD/FX ausstehend (nächster Schritt), Dataset-Contract in
  `docs/MULTI-ASSET-READINESS.md`.

---

## 4. MT5-Fallback — Regel

`data/providers/mt5.py` bleibt als Adapter erhalten, **gekapselt**:
- `AdapterInfo.platform_only == "windows"`, `status()` → `UNAVAILABLE` außerhalb Windows.
- Die zentrale Engine importiert **nie** `mt5` (Prüfung: `grep -r "import MetaTrader5" src/trading_agent/{strategy,runtime,risk,engine}` = leer).
- Betrieb: eine **separate Windows-VM** kann ein MT5-Terminal fahren und Bars in das gemeinsame
  `MarketDataRepository` schreiben — die Cloud-Engine liest nur das Repository. MT5 ist damit
  ein *optionaler zusätzlicher Feeder*, nie ein *Muss*.
