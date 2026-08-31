# Globale No-Trade-Bedingungen

**Zweck:** Eine einzige, testbare Liste aller Bedingungen, unter denen das System **keinen** neuen
Trade eröffnet — **unabhängig vom Setup und vom Score**. Diese Prüfung läuft **als erster Schritt**
der Entscheidungs-Pipeline, **vor** Regime-Gate, Ketten-Gates, Vetos und Scoring.

**Enum:** `NoTradeReason` (stabil versioniert; neue Gründe werden nur angehängt, nie umbenannt).
Jeder ausgelöste Grund wird ins **Decision Ledger** geschrieben (auch wenn mehrere gleichzeitig
zutreffen — alle werden protokolliert).

Alle Schwellen sind `PROPOSED DEFAULT`. Werte konfigurierbar unter `no_trade.*`.

---

## 1. Prüf-Pipeline (Reihenfolge)

```
[1] SYSTEM/SAFETY   → Kill-Switch, Fehlerzustände
[2] DATA            → Datenqualität, Frische, Lücken
[3] REGIME          → regime.md §9
[4] TIME/SESSION    → Session-Fenster, Wochenende, Feiertage
[5] NEWS            → news-rules.md
[6] RISK/PORTFOLIO  → Limits, Exposure, Drawdown, Verlustserien
[7] STRATEGY-STATE  → Duplikate, Cooldowns
[8] EXECUTION       → Spread, Slippage, Liquidität, Latenz
```
Erst wenn **alle 8** Gruppen passieren, geht es weiter zu Regime-Feinprüfung → Ketten-Gates →
Veto → Score.

---

## 2. Vollständige Bedingungsliste

### [1] System / Safety
| `NoTradeReason` | Auslöser | Default |
|-----------------|----------|---------|
| `KILL_SWITCH_GLOBAL` | globaler Kill-Switch aktiv | — |
| `KILL_SWITCH_BROKER` | Broker-Ebene aktiv (betrifft alle Instrumente dieses Brokers) | — |
| `KILL_SWITCH_ASSET` | Asset-Ebene aktiv (dieses Instrument) | — |
| `KILL_SWITCH_STRATEGY` | Strategie-Ebene aktiv (dieses Setup) | — |
| `KILL_SWITCH_DATA` | Daten-Kill-Switch aktiv (siehe [2]) | — |
| `SYSTEM_STARTING_UP` | Prozess < `no_trade.startup_grace_s` seit Start (State-Recovery, Reconciliation läuft) | `120` s |
| `RECONCILIATION_PENDING` | Broker-/Interner-State nicht abgeglichen (Demo/Live) | — |
| `UNHANDLED_ERROR_STATE` | offener Fehler ohne definierten Recovery-Pfad | — |

> **Fail-safe:** Beim Start ist der Zustand **`gestoppt`**, bis Recovery + Reconciliation
> abgeschlossen sind und der Kill-Switch explizit `frei` meldet.

### [2] Daten
| `NoTradeReason` | Auslöser | Default |
|-----------------|----------|---------|
| `DATA_INCOMPLETE` | erwartete Bars fehlen im benötigten Lookback (`is_complete = false`) | — |
| `DATA_STALE` | `age(last_bar) > no_trade.max_bar_age_factor × Δ(tf)` auf einem benötigten TF | `1.5` |
| `DATA_GAP_RECENT` | Lücke innerhalb der letzten `no_trade.gap_lookback_bars` Bars | `50` |
| `DATA_DUPLICATE` | doppelte Bars / doppelte Timestamps erkannt | — |
| `DATA_TIMESTAMP_INVALID` | rückläufige / nicht-monotone / zukünftige Timestamps | — |
| `DATA_PRICE_ANOMALY` | Preis-Spike > `no_trade.spike_atr` ATR ohne Volumenbestätigung; OHLC-Konsistenz verletzt (`low ≤ open,close ≤ high`) | `10` |
| `DATA_SOURCE_UNHEALTHY` | Data-Source-Health unter Schwelle / Primärquelle ausgefallen ohne validierten Fallback | — |
| `DATA_CONFIDENCE_FLOOR` | `data_confidence < no_trade.min_data_confidence` (`confidence.md`) | `0.50` |
| `CLOCK_DRIFT` | \|lokale Zeit − Referenzzeit\| > `no_trade.max_clock_drift_s` (Demo/Live) | `2` s |

### [3] Regime
Siehe `regime.md` §9. Gründe: `REGIME_UNCLEAR`, `REGIME_CONFLICTING`, `REGIME_VOL_EXTREME`,
`REGIME_VOL_TOO_LOW`, `REGIME_COMPRESSION`, `REGIME_COOLDOWN`, `REGIME_NOT_ALLOWED_FOR_SETUP`.

### [4] Zeit / Session
| `NoTradeReason` | Auslöser | Default |
|-----------------|----------|---------|
| `SESSION_NOT_ALLOWED` | aktuelle Session ∉ `setup.session.allowed` | — |
| `SESSION_OPEN_BUFFER` | < `no_trade.session_open_buffer_min` nach einem großen Session-Open | `15` |
| `WEEKEND` | Sa/So (UTC) und `setup.session.avoid_weekend = true` | `true` |
| `PRE_WEEKEND_BUFFER` | < `no_trade.pre_weekend_buffer_min` vor Wochenendbeginn | `60` |
| `MARKET_CLOSED` | Instrument-Handelskalender: Markt geschlossen / Feiertag / Half-Day-Sperrfenster (Stocks/Gold/Forex) | — |
| `ROLLOVER_WINDOW` | Futures-/CFD-Rollover-Fenster (Gold/Forex/Index) | — |

### [5] News
Siehe `news-rules.md`. Gründe: `NEWS_BLACKOUT_HIGH`, `NEWS_BLACKOUT_MEDIUM`,
`NEWS_PRE_POSITIONING_BAN`, `NEWS_FEED_UNAVAILABLE`, `NEWS_RISK_OFF_FLAG`.

### [6] Risk / Portfolio
| `NoTradeReason` | Auslöser | Default |
|-----------------|----------|---------|
| `DAILY_LOSS_LIMIT` | realisierter + offener Tagesverlust ≥ `risk.daily.max_loss_pct` | konfig. |
| `WEEKLY_LOSS_LIMIT` | ≥ `risk.weekly.max_loss_pct` | konfig. |
| `MAX_DRAWDOWN` | Equity-Drawdown vom Hoch ≥ `risk.drawdown.max_total_pct` (⇒ auch Kill-Switch) | konfig. |
| `MAX_TRADES_TODAY` | Anzahl heute geöffneter Trades ≥ `risk.daily.max_trades` | konfig. |
| `MAX_OPEN_POSITIONS` | offene Positionen ≥ `risk.portfolio.max_open_positions` | konfig. |
| `MAX_TOTAL_EXPOSURE` | Portfolio-Exposure ≥ `risk.portfolio.max_total_exposure_pct` | konfig. |
| `MAX_CORRELATED_EXPOSURE` | korrelierte Exposure (Cluster) ≥ Limit (`sizing.md`) | konfig. |
| `PORTFOLIO_HEAT` | Summe offener Risiken ≥ `risk.portfolio.max_open_risk_pct` | konfig. |
| `RISK_BUDGET_EXHAUSTED` | verbleibendes Risikobudget < `sizing.min_fraction` × beabsichtigtes Risiko | `0.5` |
| `SIZE_BELOW_MIN` | risikobasierte Zielgröße nicht zu ≥ `sizing.min_fraction` erreichbar / unter `min_notional` (`sizing.md`) | — |
| `INSUFFICIENT_MARGIN` | erforderliche Margin > verfügbares Margin-Budget, auch nach Verkleinerung auf Broker-Max-Hebel (`sizing.md` §2) | — |
| `LIQUIDATION_TOO_CLOSE` | Liquidationspreis näher als `eff_sl_distance + sizing.liq.min_buffer_atr × ATR`, auch nach iterativer Hebelsenkung | — |
| `FUNDING_COST_EXCESSIVE` | erwartete Funding-Kosten > `sizing.funding.max_share_of_risk` × Trade-Risiko | `0.25` |
| `LOSS_STREAK_REVIEW` | `n` aufeinanderfolgende Verluste ≥ `no_trade.loss_streak_review` ⇒ **manuelle Freigabe nötig** (nicht automatisch weiter) | `4` |

> **Wichtig:** `LOSS_STREAK_REVIEW` führt **nicht** zu Risikoreduktion-und-Weitermachen und
> **nicht** zu Risikoerhöhung. Es pausiert und verlangt eine menschliche Entscheidung
> (bewusst nicht automatisiert — siehe `sizing.md`).

### [7] Strategy-State
| `NoTradeReason` | Auslöser | Default |
|-----------------|----------|---------|
| `DUPLICATE_POSITION` | offene Position gleiche Richtung, gleiches Instrument | — |
| `DUPLICATE_ARMED_SETUP` | bereits `ARMED`-Kandidat gleiche Richtung, gleiches Instrument | — |
| `OPPOSITE_POSITION_OPEN` | offene Position **gegen** die neue Richtung (kein Hedging im MVP) | — |
| `COOLDOWN_AFTER_STOP` | letzter Stop-Out auf diesem Instrument < `setup.cooldown_bars` her | `12` (M15) |
| `COOLDOWN_AFTER_SWEEP_FAIL` | letzter fehlgeschlagener Sweep-Versuch (`SWEEP_BECAME_BREAKOUT`) < `no_trade.sweep_fail_cooldown_bars` | `6` (M15) |
| `SETUP_VERSION_MISMATCH` | laufende Strategy-Version ≠ freigegebene Version (Config/Deploy) | — |

### [8] Execution
| `NoTradeReason` | Auslöser | Default |
|-----------------|----------|---------|
| `SPREAD_TOO_WIDE` | `spread > exec.max_spread_atr × ATR` **oder** `> exec.max_spread_pct` | `0.10` / `0.05 %` |
| `SLIPPAGE_ESTIMATE_HIGH` | geschätzte Slippage > `exec.max_slippage_r × R` | `0.10` |
| `LIQUIDITY_THIN` | Order-Book-Tiefe / ADV-Anteil unter Schwelle (sobald Daten verfügbar) | `10×` |
| `API_DEGRADED` | Broker-/Exchange-API-Fehlerquote über Schwelle / Rate-Limit nahe (Demo/Live) | — |
| `DATA_AGE_EXECUTION` | Live-Datenalter > `exec.max_data_age_s` | `5` s |

---

## 3. Verhalten bei Treffer

1. **Keine neue Order.** Bestehende Positionen werden **nicht** automatisch geschlossen (dafür
   ist `invalidation.md` / `news-rules.md` / Kill-Switch zuständig).
2. **Decision Ledger-Eintrag** mit `decision = NO_TRADE`, `reasons = [...]` (alle zutreffenden),
   Snapshot des `MarketContext`, Regime, Confidence, Strategy-Version, Config-Hash.
3. **Kein Score** wird berechnet (spart Rechenzeit, verhindert „Score-Anstarren").
4. Bei `LOSS_STREAK_REVIEW`, `MAX_DRAWDOWN`, `RECONCILIATION_PENDING`, `UNHANDLED_ERROR_STATE`:
   zusätzlich **Alert** über den Notification-Layer.

---

## 4. Tests, die diese Datei verankert

- Für **jeden** `NoTradeReason`: ein Unit-Test, der die Bedingung künstlich herstellt und prüft,
  dass (a) keine Order entsteht, (b) der korrekte Grund im Ledger steht.
- Ein Test, dass die No-Trade-Prüfung **vor** der Score-Berechnung läuft (Score-Funktion wird bei
  Treffer nicht aufgerufen).
- Ein Test, dass mehrere gleichzeitige Gründe **alle** protokolliert werden.

---

## 5. Zu bestätigen / zu validieren

- **`LOSS_STREAK_REVIEW = 4`**: Startwert. Ob 3, 4 oder 5 — empirisch aus der
  Consecutive-Loss-Verteilung (`backtest-labeling.md`).
- **`avoid_weekend` für Crypto**: bewusst konservativ `true`. Backtest muss zeigen, ob
  Wochenend-Sweeps wirklich schlechter sind.
- **`DATA_STALE`-Faktor `1.5`**: knapp genug, um echte Verzögerungen zu fangen, ohne bei
  normalem Bar-Timing zu blockieren — validieren gegen reale Feed-Latenzverteilung.
- **`max_clock_drift_s = 2`**: erst ab Demo relevant.
