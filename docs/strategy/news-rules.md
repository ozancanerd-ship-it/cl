# News- & Event-Regeln

**Zweck:** Vollständige, objektive Regeln für den Umgang mit terminierten und ungeplanten
Ereignissen. **Bei fehlenden/veralteten News-Daten gilt fail-safe: keine neuen Entries.**

Alle Zahlen `PROPOSED DEFAULT`. Konfig unter `news.*`. Datenquelle im MVP: **statische
Kalender-Fixture / CSV** (kein externer Call) — Point-in-Time (`available_at` je Eintrag).

---

## 1. Impact-Klassifikation

| Impact | Event-Typen (`news.impact_map`) |
|--------|--------------------------------|
| **HIGH** | FOMC-Zinsentscheid, FOMC-Statement, Fed-Chair-Pressekonferenz/-Rede, CPI, Core CPI, NFP (Non-Farm Payrolls), PCE / Core PCE, GDP (Advance), ECB/BoE/BoJ-Zinsentscheide, Notfall-Statements von Zentralbanken; **Crypto:** große Exchange-Halts/Incidents, Regulierungs-Entscheidungen (SEC/MiCA), Spot-ETF-Entscheidungen, Token-Unlock ≥ `news.crypto.unlock_pct` der zirkulierenden Menge in ≤ 48 h |
| **MEDIUM** | PPI, Retail Sales, ISM/PMI (Manufacturing & Services), Initial Jobless Claims, JOLTS, Consumer Confidence / Michigan Sentiment, FOMC-Minutes, FOMC-Mitglieder-Reden, GDP-Revisionen, ADP; **Crypto:** geplante Netzwerk-Upgrades/Hard Forks, Funding-Rate-Extrem (\|funding\| ≥ `news.crypto.funding_extreme`), größere Listings/Delistings an Top-Exchanges |
| **LOW** | 2.+ Datenrevisionen, regionale Fed-Indizes, kleinere Wirtschaftsdaten, Reden ohne geldpolitischen Bezug |

| Parameter | PROPOSED DEFAULT |
|-----------|------------------|
| `news.crypto.unlock_pct` | `1.0 %` |
| `news.crypto.funding_extreme` | `0.05 %` (8h-Rate) |

*Warum validieren:* Die Zuordnung „NFP = HIGH, PMI = MEDIUM" ist Marktkonvention, aber die
**tatsächliche** Volatilitätswirkung auf BTC/ETH schwankt und muss aus historischen
Post-Event-Bewegungen bestätigt werden. Einzelne Events bekommen ggf. `per_event_override`.

---

## 2. Event → Instrument-Routing (`news.routing`)

| Event-Gruppe | Betroffene Instrumente (Handelsstopp/-vorsicht) | Begründung |
|--------------|--------------------------------------------------|------------|
| **USD-Makro** (FOMC, CPI, Core CPI, NFP, PCE, GDP, US-PMI, Claims) | alle USD-Paare, `XAUUSD`, US-Indizes/ETFs, **`BTCUSDT`, `ETHUSDT`, liquide Altcoins** | Crypto reagiert auf USD-Liquidität/Realzinsen/Risk-Sentiment; empirisch klare Spikes um FOMC/CPI |
| **EUR-Makro** (ECB, EU-CPI, EU-PMI) | EUR-Paare, EU-Indizes; **Crypto: nur wenn `news.routing.eur_to_crypto = true`** (DEFAULT `false`) | schwächerer, weniger konsistenter Effekt auf Crypto |
| **Sonstige Zentralbanken** (BoE, BoJ, SNB) | jeweilige Währungspaare | |
| **Token-Unlock / Listing / Delisting** | **nur das betroffene Token** (+ enger korrelierter Basket, wenn Unlock ≥ 2 %) | idiosynkratisch |
| **Exchange-Incident / Regulierung** | alle Crypto-Instrumente dieses Marktes / global bei systemischen Fällen | Ansteckungsrisiko |
| **Earnings** (später, Aktien) | die Aktie (+ Sektor/Index bei Mega-Cap) | Gap-Risiko |
| **Geopolitik / Headline** | via manuellem/Feed-`risk_off`-Flag: **alle** Instrumente | nicht terminierbar |

---

## 3. Blackout-Fenster

| Impact | pre (min) | post (min) | Pre-Positioning-Ban (min) |
|--------|-----------|------------|---------------------------|
| **HIGH** | `news.blackout.high.pre` = `30` | `news.blackout.high.post` = `30` | `news.prepos_ban.high` = `120` |
| **HIGH — FOMC/Fed-Presser** (Override) | `30` | `60` | `180` |
| **MEDIUM** | `15` | `15` | `0` |
| **LOW** | `0` | `0` (nur Monitoring) | `0` |

**Pre-Positioning-Ban:** innerhalb dieses Fensters vor einem HIGH-Event **kein neuer Entry in
irgendeine Richtung** auf den betroffenen Instrumenten (nicht nur „keine Wette auf die News" —
jede Richtung, weil die Positionierung selbst das Risiko trägt).

---

## 4. Blackout-Umfang (was genau gesperrt/gemacht wird)

| Zustand | Aktion im Blackout / Pre-Positioning-Ban |
|---------|------------------------------------------|
| **Neuer Entry** | verboten (`NoTradeReason = NEWS_BLACKOUT_HIGH` / `_MEDIUM` / `NEWS_PRE_POSITIONING_BAN`) |
| **`ARMED`-Kandidat** | Order storniert, wenn der mögliche Fill-Zeitpunkt ins Fenster fällt (Klasse-A-Invalidierung `NEW_NOTRADE_CONDITION`) |
| **Offene Position, Impact = MEDIUM** | keine Zwangsaktion; Trailing/Invalidierung normal |
| **Offene Position, Impact = HIGH, im Gewinn ≥ `news.open_position.be_trigger_r`** (`0.5`) | SL → Break-even `news.open_position.pre_event_min` (`15`) min vor Event |
| **Offene Position, Impact = HIGH, sonst** | auf `news.open_position.reduce_pct` (`50 %`) reduzieren, `15` min vor Event |
| **Offene Position, Impact = HIGH, `news.flatten_high_impact = true`** (DEFAULT) | **vollständig flat** `news.open_position.pre_event_min` min vor Event; `exit_reason = NEWS_FLATTEN` |

> Reihenfolge in `invalidation.md` §7, Schritt 3.

---

## 5. Post-Event-Re-Entry

Nach dem Ende des post-Fensters ist ein neuer Entry erst erlaubt, wenn **alle** gelten:
1. `≥ news.reentry.min_min` (**`30`**) seit Event-Zeitpunkt
2. Volatilitäts-Perzentil `vol_pct ≤ news.reentry.max_vol_pct` (**`90`**) auf dem Entry-TF
   (nicht mehr im Post-Event-Spike)
3. eine **frische** Struktur hat sich seit dem Event gebildet (mind. 1 neuer bestätigter Swing auf
   `structure.timeframe`)
4. Spread wieder unter `exec.max_spread_*` (`no-trade.md` [8])

---

## 6. Ungeplante Ereignisse / Headline-Risiko

- Ein `risk_off`-Flag kann gesetzt werden durch: (a) manuellen Eingriff, (b) späteren
  Headline-/Sentiment-Feed, (c) `news.auto_risk_off` bei extremer, nicht durch ein geplantes
  Event erklärbarer Volatilität (`vol_pct ≥ 99` + Preis-Move ≥ `news.shock_move_atr` in
  `news.shock_window_bars`).
- Wirkung: wie **HIGH**-Blackout auf **alle** Instrumente, bis das Flag manuell/automatisch
  (`vol_pct` zurück < `85` für `news.risk_off_clear_bars`) gelöscht wird.

| Parameter | PROPOSED DEFAULT |
|-----------|------------------|
| `news.shock_move_atr` | `4.0` |
| `news.shock_window_bars` | `3` (Entry-TF) |
| `news.risk_off_clear_bars` | `12` |

---

## 7. Fehlende / veraltete News-Daten (fail-safe)

| Bedingung | Aktion |
|-----------|--------|
| News-Feed/Fixture-Alter > `news.feed.max_age_h` (**`12`**) | `news.feed.failure_action` — DEFAULT `block_new_entries` (`NoTradeReason = NEWS_FEED_UNAVAILABLE`) |
| Feed-Abruf schlägt fehl (Demo/Live) | ebenso; zusätzlich Alert |
| Einzelnes Event ohne `available_at` / mit unplausibler Zeit | Event wird als **HIGH** behandelt (konservativ), Warnung ins Log |

**Backtest:** Es wird **nur** der Kalenderstand verwendet, der zum `information_cutoff`
(`backtest-labeling.md`) verfügbar war. Nie der revidierte/nachträglich ergänzte Kalender.

---

## 8. Session-Open als Quasi-Event

Erste `news.session_open_buffer_min` (**`15`**) Minuten nach dem Open von London bzw. New York:
`NoTradeReason = SESSION_OPEN_BUFFER`. (Für 24/7-Crypto über die UTC-Session-Fenster definiert.)

---

## 9. Datenmodell

```
NewsEvent {
  id: str
  type: str                 # "CPI", "FOMC_RATE", "TOKEN_UNLOCK", ...
  impact: HIGH | MEDIUM | LOW
  scheduled_time: UTC
  available_at: UTC         # ab wann dieser Kalendereintrag bekannt war (Point-in-Time)
  affected_instruments: [str]   # aufgelöst über news.routing
  actual: float?            # erst nach Veröffentlichung; im Backtest nur ab scheduled_time nutzbar
  forecast: float?
  previous: float?
  per_event_override: { pre_min?, post_min?, prepos_ban_min? }
}
```

`surprise` (nur informativ, **nie** für Entry-Entscheidungen vor `scheduled_time`):
`surprise = (actual − forecast) / stdev_historical_surprise`.

---

## 10. Ins Decision Ledger

Bei jedem `NO_TRADE` mit News-Grund: `blocking_event_id`, `event_type`, `impact`, `minutes_to_event`,
`window` (`pre` | `post` | `prepos_ban`).
Bei jeder News-erzwungenen Positionsaktion: `event_id`, `action` (`be` | `reduce` | `flatten`),
`pnl_r_at_action`.

---

## 11. Zu bestätigen / zu validieren

- **`eur_to_crypto = false`**: Startannahme (EUR-Events beeinflussen BTC/ETH kaum) — bestätigen.
- **`flatten_high_impact = true` für Crypto**: konservativ — Trades werden vor FOMC/CPI komplett
  geschlossen. Alternative: nur auf BE/50 % reduzieren. Empfehlung für MVP: `true`.
- **Blackout-Längen (30/30, FOMC 30/60)**: Startwerte; validieren gegen historische
  Post-Event-Volatilitäts-Abklingzeit für BTC/ETH.
- **`unlock_pct = 1 %` als HIGH-Schwelle**: Startwert.
- **`prepos_ban.high = 120 min`**: bewusst großzügig; ggf. auf 60 reduzieren nach Datensicht.
