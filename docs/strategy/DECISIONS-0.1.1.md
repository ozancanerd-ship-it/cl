# Strategy Freeze — `strategy_version 0.1.1`

**Eingefroren am:** 2026-08-28 (Nachfolger von `DECISIONS-0.1.0.md`)
**Gilt für:** alle Dokumente in `docs/strategy/` inkl. `setups/SMC-SWEEP-REV-01.md` und
`SPEC-ADDENDUM-0.1.1.md`.
**Bedeutung:** `0.1.1` löst die im Phase-3-Spec-Audit gefundenen Widersprüche/Lücken auf
(Punkte C1–C12). Alle Zahlenwerte bleiben `PROPOSED DEFAULT` / `TO-VALIDATE` — kein Wert ist
durch Backtest/OOS bestätigt. Live-Trading bleibt ausgeschlossen.

> `0.1.1` ist weiterhin eine **Hypothese, kein validiertes System**. Änderungen an Regeln oder
> Defaults ⇒ neue Version + Changelog-Eintrag + erneute OOS-Prüfung.

---

## Phase-3-Spec-Audit — bestätigte Auflösungen (Nutzer, 2026-08-28)

| # | Thema | Verbindliche Festlegung |
|---|-------|-------------------------|
| **C1** | Risikostufen | **A+ = 1.00 % · A = 0.65 % · B = 0.40 %** der Equity (Basis-`risk_pct`). `risk.hard_max_risk_pct = 2.0 %`. Maßgeblich für die kontrolliert-aggressive Risk-Philosophie. Ersetzt die 0.50/0.35/0.25 % aus `DECISIONS-0.1.0.md` #12. Weiterhin `PROPOSED DEFAULT`, zu validieren via Backtest/OOS/Walk-Forward/Monte-Carlo (Ruin-Wahrscheinlichkeit < 5 %, Max-DD im MC-95-%-Band). |
| **C2** | Scoring-Gewichte | Phase 3 / MVP: **gleichgewichtet, jeder WEIGHTED-Faktor = 10, alle Penalties = 0** (`scoring.example.yaml`, `DECISIONS-0.1.0.md` #4). Die gestaffelten Gewichte in `scoring-rubric.md` §4 bleiben als **späteres Kalibrierungsziel** dokumentiert. **Keine Gewichts-Optimierung in Phase 3.** |
| **C3** | Session-Fenster | Maßgeblich sind die **börsenlokalen, DST-korrekt aufgelösten** Zeiten aus `refdata/seed.py` / `config.example.yaml` (`refdata.calendar.resolve_session`). Für 24/7-Crypto muss die Session-Logik trotzdem lückenlos funktionieren (kein Handels-Stopp außerhalb von Sessions — nur der **Entry-Gate** aus `SMC-SWEEP-REV-01` §18 greift). `glossary.md` an die aufgelösten Werte angepasst. |
| **C4** | Premium / Discount | Der **numerische Gate** `pd_position(zone_mid) ≤ entry.max_pd_position` (Long; Default `0.50`) bzw. `≥ 1 − entry.max_pd_position` (Short) ist maßgeblich. „DISCOUNT/PREMIUM" im Fließtext präzisiert zu „auf oder jenseits des Equilibriums in Trade-Richtung". |
| **C5** | Setup State Machine | Maßgeblich ist die **eingefrorene FSM `SMC-SWEEP-REV-01` §24**: `SCANNING → BIAS_SET → LIQUIDITY_IDENTIFIED → SWEPT → RECLAIMED → DISPLACED → STRUCTURE_SHIFTED → ARMED → TRIGGERED/MANAGED → CLOSED → REVIEW`. Die generischen Namen (`WATCH/DEVELOPING/ARMED/CONFIRMED/INVALIDATED/EXPIRED`) sind **nur Anzeige-Aliase** (Scanner-Lifecycle, Phase 5) — Mapping in `SMC-SWEEP-REV-01` §24. Die interne State Machine bleibt die eingefrorene Spec. |
| **C6** | `WAIT`-Signal | **Neuer Decision-Output.** `WAIT` = Setup-Kette lebt (State ∈ {`BIAS_SET` … `ARMED`}), **kein** hartes Veto, **kein** harter No-Trade-Grund, Setup weiter beobachtungswürdig. `NO_TRADE` = hartes Veto **oder** harter No-Trade-Grund **oder** abgebrochene Kette (`SCANNING` mit Grund) **oder** Expiry **oder** vollständige Kette ohne ausreichende Tier-Qualität (`SCORE_BELOW_B`). Details: `SPEC-ADDENDUM-0.1.1.md` §1. In Tests fest verankert. |
| **C7** | Price Action | Phase 3 implementiert **nur objektiv definierte** Price-Action-Komponenten, die die Spec tatsächlich braucht: **Displacement** (`primitives.md` §7, vollständig) + **Engulfing / Pin / Minor-CHoCH-M1** für `entry.mode = confirmation_market` mit objektiven Schwellen — `SPEC-ADDENDUM-0.1.1.md` §2, markiert als `PROPOSED / TO-VALIDATE`. **Kein** eigenständiger Score-Faktor für Rejection / Momentum / allgemeines Wick-Behaviour (keine objektive Spec-Definition vorhanden). |
| **C8** | Support / Resistance | Für `contradictions.md` C10 und `SMC-SWEEP-REV-01` §16.3 (Ziel-Raum) dienen die **opposing `LiquidityLevel`s** (`primitives.md` §4) als S/R-Proxy. **Kein separates S/R-Modell in Phase 3.** |
| **C9** | Portfolio Context | `evaluate()` erhält einen **optionalen** `portfolio_context`. Fehlt er ⇒ **Veto V9 = pass-through** (dokumentiert, mit Test). V10-SL-Geometrie (Floor/Cap aus `SMC-SWEEP-REV-01` §10) wird **vollständig** implementiert. Der vollständige `PortfolioState` kommt in Phase 4. |
| **C10** | News-Fixture | Point-in-Time-News-Fixture für das Backtest-Fenster: **FOMC, CPI, NFP, PCE** mit `available_at`. Fail-safe (`NEWS_FEED_UNAVAILABLE` bei fehlendem/veraltetem Feed) **bleibt bestehen** — **kein** Backtest-Override, der fehlende News ignoriert. |
| **C11** | Multi-Timeframe | **Basis-TF = M5.** `D1 / H4 / M15` werden aus M5 per `data/resample.py` abgeleitet. `M1` nur optional für Excursion / Intrabar-Analyse; fehlt M1 ⇒ konservative Intrabar-Annahme (`backtest-labeling.md` §3/§5, SL vor TP). `MarketContext` enthält alle benötigten Timeframes. |
| **C12** | Historische Daten | Mindestens **180 Tage M5** für BTC und ETH nachladen; `scripts/fetch_history.py` um robuste **Bybit-Kline-Pagination** erweitern. 180 Tage = Minimum für einen aussagekräftigen Phase-3-Backtest. Mehr laden erlaubt, wenn Regime-/Warmup-Berechnungen es erfordern; **keine** künstliche Begrenzung auf exakt 180 Tage. |

---

## Changelog

### `0.1.1` — 2026-08-28
**Auslöser:** Phase-3-Strategy-Spec-Audit (12 Konflikte/Lücken, alle vom Nutzer aufgelöst).

**Geänderte Dokumente:**
- `DECISIONS-0.1.0.md` — Kopf-Hinweis „für die unten genannten Punkte durch `0.1.1` ersetzt"; #12 (Risiko) verweist auf C1.
- `sizing.md` — §1 bereits 1.00/0.65/0.40 (unverändert); §2-Parameter­tabelle `sizing.risk_pct.*` und §8-Status­tabelle von 0.50/0.35/0.25 auf **1.00/0.65/0.40** korrigiert; Kopf auf `0.1.1`.
- `scoring-rubric.md` — §4: expliziter Hinweis „MVP = gleichgewichtet (`scoring.example.yaml`); §4-Tabelle = Kalibrierungsziel"; Kopf auf `0.1.1`.
- `contradictions.md` — §3 Premium/Discount-Wortlaut präzisiert (C4); §6 `resolve()` gibt jetzt auch **`WAIT`** zurück (C6); C10 nennt „opposing LiquidityLevels" als S/R-Proxy (C8); V9-Zeile: `portfolio_context` optional, pass-through wenn fehlend (C9); Kopf auf `0.1.1`.
- `setups/SMC-SWEEP-REV-01.md` — §8 Location-Gate-Wortlaut (C4); §16.3 S/R-Proxy (C8); §21 Pipeline endet mit `BUY/SELL/WAIT/NO_TRADE` (C6); §24 Alias-Mapping-Tabelle (C5); Kopf auf `0.1.1`.
- `glossary.md` — Sessions-Abschnitt: Verweis auf die börsenlokalen Specs + 24/7-Crypto-Hinweis (C3).
- `backtest-labeling.md` — §1: MTF-Assembly aus M5-Basis (C11); Kopf-Hinweis auf `0.1.1`.
- `regime.md` — §10-Statustabelle: Hinweis, dass MTF-Serien im MVP aus M5 abgeleitet werden (C11).

**Neue Dokumente:**
- `DECISIONS-0.1.1.md` (dieses Dokument).
- `SPEC-ADDENDUM-0.1.1.md` — §1 `WAIT`/`NO_TRADE`-Abgrenzung + Decision-Output-Enum; §2 objektive Confirmation-Entry-Muster (Engulfing / Pin / Minor-CHoCH-M1).

**Geänderte Configs:**
- `config/config.example.yaml`, `config/strategy.example.yaml` — `strategy_version: "0.1.1"`.
- `config/risk.example.yaml` — bereits 1.00/0.65/0.40 (unverändert); Kommentar-Verweis auf C1.
- `config/scoring.example.yaml` — unverändert (bereits gleichgewichtet).

**Nicht geändert (bewusst):** alle Primitive-Definitionen, Regime-Achsen, `no-trade.md`-Enum,
`confidence.md`-Formeln, die 8 TO-VALIDATE-Parameter, die kausale Kette `SMC-SWEEP-REV-01` §0–§20.

### `0.1.0` — 2026-08-28
- Erste eingefrorene Spezifikation (siehe `DECISIONS-0.1.0.md`).
