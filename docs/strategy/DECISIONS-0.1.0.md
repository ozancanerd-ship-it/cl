# Strategy Freeze — `strategy_version 0.1.0`

> **Teilweise ersetzt durch `DECISIONS-0.1.1.md`** (Phase-3-Spec-Audit, 2026-08-28). Für die
> Punkte C1–C12 dort gilt `0.1.1`. Insbesondere: **Risikostufen A+/A/B = 1.00 / 0.65 / 0.40 %**
> (nicht mehr 0.50/0.35/0.25 %, siehe #12 unten + `DECISIONS-0.1.1.md` C1).

**Eingefroren am:** 2026-08-28
**Gilt für:** alle Dokumente in `docs/strategy/` inkl. `setups/SMC-SWEEP-REV-01.md`
**Bedeutung:** Ab hier gilt Pre-Registration (`anti-overfitting.md` §1). Jede inhaltliche
Änderung an Regeln oder `PROPOSED DEFAULT`-Werten ⇒ neue Version (`0.1.1` / `0.2.0`), Eintrag in
diesem Changelog, erneute OOS-Prüfung.

> **`0.1.0` ist eine Hypothese, kein validiertes System.** Alle Zahlenwerte sind `PROPOSED
> DEFAULT`. Kein Wert ist durch Backtest/OOS/Walk-Forward bestätigt. Live-Trading ist ausdrücklich
> ausgeschlossen.

---

## Bestätigte Entscheidungen (Nutzer, 2026-08-28)

| # | Thema | Festlegung |
|---|-------|------------|
| 1 | HTF-Regime unklar | `directional(D1)` **oder** `directional(H4)` = `UNCLEAR` ⇒ **`NO_TRADE`** |
| 2 | Low-Vol-Regime | für `SMC-SWEEP-REV-01` im MVP **verboten**; Lockerung nur nach empirischer Validierung |
| 3 | Regime-Konsens-Timeframes | **D1 + H4**. M15 nur Kontext (kein Veto-Recht) |
| 4 | Scoring-Start | **gleichgewichtet** (jeder WEIGHTED-Faktor gleich), alle Penalties = **0**. Optimierung erst nach OOS-Nachweis eines Edges |
| 5 | `entry.max_pd_position` | `0.50` (PROPOSED DEFAULT) |
| 6 | Premium/Discount-Referenz | **`swept_leg`** primär; `dealing_range` wird später empirisch verglichen |
| 7 | `news.flatten_high_impact` | **`true`** für Crypto im MVP (Trades vor HIGH-Impact-Events vollständig geschlossen) |
| 8 | `session.avoid_weekend` | **`true`** im MVP |
| 9 | `invalidation.zone_fail_confirm` | **`true`** (`ENTRY_ZONE_FAILED` nur bei bestätigtem Close) |
| 10 | Tages-/Wochenverlustlimit | blockiert **neue Entries**; **kein** automatisches Schließen bestehender Positionen; separater Emergency Kill Switch bleibt bestehen |
| 11 | TO-VALIDATE-Parameter | die **8** aus `anti-overfitting.md` §2.2 bleiben unverändert |
| 12 | Risikostufen | ~~A+ = 0.50 % · A = 0.35 % · B = 0.25 %~~ → **ersetzt in `0.1.1` (C1): A+ = 1.00 % · A = 0.65 % · B = 0.40 %** der Equity — ausschließlich `PROPOSED DEFAULT`, zu validieren durch Backtest / OOS / Walk-Forward / Monte-Carlo |
| 13 | Hebel / Position Sizing | **keine starren pauschalen Hebel-Caps**; Hebel wird **dynamisch pro Trade** berechnet (`sizing.md` §2). Reihenfolge: **erlaubtes Risiko → Positionsgröße → Hebel**. Invarianten: Hebel erhöht nie das erlaubte Verlustrisiko; Größe nie rückwärts aus Gewinn; kein Martingale / keine Verlustprogression / keine Auto-Risikoerhöhung; Leverage umgeht **kein** Risk-Engine-Veto. Berücksichtigte Faktoren: Equity, verfügbares Kapital, erlaubtes Risiko (EUR/USD), SL-Distanz, Asset-Volatilität, Liquidität, Spread, erwartete Slippage, Margin-Anforderung, Broker-/Exchange-Limits, Portfolio-/Correlation-Exposure, bestehende Positionen, Maintenance Margin / Liquidationsabstand, Gebühren, Funding. Beispiel: Equity 50 EUR, sinnvolle Positionsgröße ≈ 200–300 EUR ⇒ nötiger Hebel darf berechnet/genutzt werden, wenn alle Constraints es erlauben; sonst **`NO_TRADE`** |
| 14 | Capital Allocation | `allocation/`-Paket + 3-Horizont-Modell (Long-Term / Swing / Short-Term) bleiben **Architektur-Platzhalter** (noch nicht implementiert) |

---

## Was `0.1.0` festlegt (Referenz)

| Dokument | Inhalt |
|----------|--------|
| `primitives.md` | 13 Primitive, objektiv & programmierbar |
| `regime.md` | 3 Achsen (Directional / Volatility / Phase), MTF-Konsens D1+H4, NO-TRADE-Ausgänge |
| `setups/SMC-SWEEP-REV-01.md` | kausale Kette (8 Glieder), 22 Spez-Punkte, 10 Vetos, State Machine |
| `no-trade.md` | Enum `NoTradeReason`, 8 Prüfgruppen |
| `invalidation.md` | 4 Klassen (A Kandidat / B strukturell / C SL / D Zeit), Bar-Auswertungsreihenfolge |
| `contradictions.md` | Signal-Hierarchie, Veto-Matrix, `resolve()`-Algorithmus |
| `scoring-rubric.md` | 12 WEIGHTED-Faktoren mit 0..1-Rubriken; Start gleichgewichtet |
| `confidence.md` | `data_confidence` + `analysis_confidence`, Floors 0.50 / 0.60 |
| `sizing.md` | Risiko→Größe→Hebel, dynamischer Hebel, Invarianten |
| `news-rules.md` | HIGH/MEDIUM/LOW, Routing, Blackout, fail-safe |
| `backtest-labeling.md` | TradeRecord-Schema, `information_cutoff`, Bias-Tests |
| `anti-overfitting.md` | Parameter-Budget (8), Splits, Walk-Forward, Monte-Carlo, Kill-Kriterien |

---

## Changelog

### `0.1.0` — 2026-08-28
- Erste eingefrorene Spezifikation. Alle 14 Nutzer-Entscheidungen eingearbeitet.
- `sizing.md`: statische Hebel-Caps entfernt, dynamische Hebelberechnung (§2) eingeführt;
  Risikostufen auf 0.50 / 0.35 / 0.25 % gesetzt; neue No-Trade-Gründe `INSUFFICIENT_MARGIN`,
  `LIQUIDATION_TOO_CLOSE`, `FUNDING_COST_EXCESSIVE`.
- Noch **kein** Code. Noch **kein** Backtest. Nächster Schritt: `FINAL_IMPLEMENTATION_PLAN.md`.
