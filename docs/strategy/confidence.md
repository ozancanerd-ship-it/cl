# Confidence & Unsicherheits-Modell

**Zweck:** Unsicherheit wird **nicht** ignoriert und **nicht** stillschweigend „durchgereicht".
Sie wird gemessen, propagiert und führt bei Unterschreitung von Schwellen zu `NO_TRADE`.
**Default unter Unsicherheit = aussetzen.**

`confidence` ist **getrennt** vom Setup-Score. Der Score misst „wie gut ist die Konstellation";
`confidence` misst „wie sicher sind wir, dass wir sie **korrekt erkannt** haben". Ein Setup
braucht **beides**.

Alle Zahlen `PROPOSED DEFAULT`. Konfig unter `confidence.*`.

---

## 1. Zwei Komponenten

```
setup_confidence = combine( data_confidence , analysis_confidence )
```

| Komponente | Frage | Bereich |
|------------|-------|---------|
| `data_confidence` | Sind die zugrunde liegenden **Daten** vollständig, frisch, konsistent? | [0, 1] |
| `analysis_confidence` | Sind die **erkannten Strukturen** eindeutig und bestätigt? | [0, 1] |

---

## 2. `data_confidence`

`data_confidence = min( completeness , freshness , consistency , source_term )`
(bewusst **`min`**, nicht Mittelwert — die schwächste Dimension bestimmt das Vertrauen.)

| Term | Definition | PROPOSED DEFAULT |
|------|------------|------------------|
| `completeness` | `vorhandene_bars / erwartete_bars` im benötigten Lookback über **alle** benötigten Timeframes | Floor `0.98` |
| `freshness` | `clip(1 − (age(last_bar) − Δ) / (freshness_grace · Δ), 0, 1)` je TF, dann `min` | `freshness_grace = 1.0` |
| `consistency` | `1` wenn keine Duplikate, keine nicht-monotonen Timestamps, OHLC-Konsistenz überall; sonst `0` | — |
| `source_term` | 1 Quelle ⇒ `confidence.single_source_value`; ≥ 2 übereinstimmende Quellen ⇒ `1.0`; Quellen weichen > `confidence.source_disagree_atr` ab ⇒ `0` | `single_source_value = 0.8`, `source_disagree_atr = 0.3` |

**Harter Floor (Veto V6):** `data_confidence < veto.min_data_confidence` (**`0.50`**) ⇒
`NO_TRADE` (`DATA_CONFIDENCE_FLOOR`), unabhängig von allem anderen.

---

## 3. `analysis_confidence`

Gewichtetes Mittel objektiver Klarheits-Terme (jeweils [0,1]):

| Term | Definition | Gewicht (PROPOSED DEFAULT) |
|------|------------|---------------------------:|
| `swing_confirmation` | `clip(min_bars_since_confirmation / swing.right, 0, 1)` über die beteiligten Swings — ein Swing, der erst seit `< R` Bars „steht", ist nicht sicher bestätigt | 0.20 |
| `structure_clarity` | `1` wenn CHoCH/BOS mit `close`-Abstand > `0.5·ATR` **und** < `max_break_distance`; linear reduziert bei knappen/überdehnten Brüchen; `0.3` bei Equal-High/Low-Mehrdeutigkeit an der Bruchstelle | 0.20 |
| `sweep_unambiguity` | `1` bei genau 1 sauber gesweeptem Pool, Penetration mittig im Band; `0.5` bei 2 Pools / Randlage | 0.20 |
| `regime_clarity` | normierter Abstand aller Regime-Metriken zu ihren Schwellen; nahe an einer Grenze (± `confidence.regime_margin_pct`) ⇒ niedrig | 0.15 |
| `htf_mtf_agreement` | `1 − mtf_disagreement` (`contradictions.md` §2) | 0.15 |
| `fvg_integrity` | `1` wenn Entry-Zone `fill_fraction = 0` und nicht `STALE`; linear fallend mit `fill_fraction` | 0.10 |

`analysis_confidence = Σ w_i · term_i` (Gewichte summieren zu 1).

| Parameter | PROPOSED DEFAULT |
|-----------|------------------|
| `confidence.regime_margin_pct` | `10` (Perzentilpunkte Abstand zur Vol-Schwelle) |

---

## 4. Kombination

```
setup_confidence = ( wd · data_confidence + wa · analysis_confidence )
                   · floor_penalty
```
mit
- `wd = 0.40`, `wa = 0.60` (**PROPOSED DEFAULT**)
- `floor_penalty = 0.5` **wenn** `data_confidence < confidence.soft_floor` **oder**
  `analysis_confidence < confidence.soft_floor`, sonst `1.0`
- `confidence.soft_floor = 0.60` (**PROPOSED DEFAULT**) — eine schwache Einzelkomponente halbiert
  die Gesamt-Confidence (verhindert, dass eine sehr hohe Komponente eine sehr niedrige „übertönt").

**Harte Floors (Veto):**
- `data_confidence < 0.50` ⇒ Veto V6.
- `setup_confidence < setups.SMC-SWEEP-REV-01.min_confidence` (**`0.60`**) ⇒ `NO_TRADE`
  (`CONFIDENCE_BELOW_MIN`).

---

## 5. Unsicherheits-Sonderfälle (explizit)

| Fall | Behandlung |
|------|------------|
| **Unbestätigter Swing** | Ein Swing mit `bars_since_confirmation < swing.right` existiert für Entscheidungen **nicht**. Kein Trade „auf Verdacht", dass er hält. |
| **Struktur mehrdeutig** (Equal Highs/Lows genau an der Bruchstelle) | `structure_clarity ≤ 0.3` ⇒ zieht `analysis_confidence` unter den soft_floor ⇒ meist `NO_TRADE`. |
| **Regime an der Grenze** | `regime_clarity` niedrig; zusätzlich Hysterese (`regime.md` §6) — frisch gewechseltes Regime ⇒ Cooldown ⇒ ohnehin `NO_TRADE`. |
| **Konfligierende Timeframes** | `htf_mtf_agreement` niedrig; bei D1/H4-Konflikt greift ohnehin Veto V1. |
| **Teilweise mitigierte Entry-Zone** | `fvg_integrity` linear reduziert; ab `fill_fraction ≥ consumed_threshold` ist die Zone `MITIGATED` ⇒ `NO_ENTRY_ZONE`. |
| **Datenlücke im Lookback** | `completeness < 0.98` ⇒ `data_confidence` niedrig; bei Lücke in den letzten `gap_lookback_bars` ⇒ direkt `DATA_GAP_RECENT` (No-Trade). |
| **Fehlende News-Daten** | eigener No-Trade-Pfad (`NEWS_FEED_UNAVAILABLE`), nicht über Confidence. |
| **LLM-/AI-Ausgabe** (später) | LLM-`confidence` ist eine **separate** Größe, fließt **nicht** in `setup_confidence` ein und darf den Score nur innerhalb enger Grenzen modulieren (siehe `ARCHITECTURE_GAP_AUDIT.md` G-16). Bei Schema-Verstoß / Timeout: AI-Beitrag = neutral, System läuft regelbasiert weiter. |

---

## 6. Confidence → Risikostufe

`setup_confidence` ist Teil der Stufen-Bedingung (`scoring-rubric.md` §1):

| Stufe | benötigt `setup_confidence ≥` |
|-------|-------------------------------|
| A+ | `0.80` |
| A | `0.70` |
| B | `0.60` |

Ein Setup mit Score 90, aber `setup_confidence = 0.62` ⇒ **Stufe B**, nicht A+.

---

## 7. Ins Decision Ledger

```
ConfidenceRecord {
  data_confidence: float
  data_terms: { completeness, freshness, consistency, source_term }
  analysis_confidence: float
  analysis_terms: { swing_confirmation, structure_clarity, sweep_unambiguity,
                    regime_clarity, htf_mtf_agreement, fvg_integrity }
  setup_confidence: float
  floor_penalty_applied: bool
  limiting_factor: str          # Name des kleinsten Terms
}
```

Das `limiting_factor`-Feld macht auswertbar, **warum** Confidence niedrig war (Daten? Struktur?
Regime?) — Input für spätere Verbesserungen.

---

## 8. Tests, die diese Datei verankert

- `data_confidence` = `min` der Terme (nicht Mittelwert): Test mit einer schlechten Dimension.
- Harter Floor 0.50 ⇒ `NO_TRADE` trotz perfektem Score.
- `soft_floor` ⇒ `floor_penalty = 0.5`.
- Unbestätigter Swing ⇒ nicht sichtbar für Entscheidungen.
- `limiting_factor` korrekt gesetzt.

---

## 9. Zu bestätigen / zu validieren

- **`wd = 0.40 / wa = 0.60`**: Startgewichtung Daten vs. Analyse.
- **Harter Floor `0.50`, Setup-Min `0.60`, Stufen-Schwellen 0.60/0.70/0.80**: alle Platzhalter.
  Validierung: schneiden Trades mit höherer Confidence OOS messbar besser ab (weniger MAE,
  höhere Trefferquote)?
- **`min`-Verknüpfung bei `data_confidence`**: bewusst streng — bestätigen.
- **`floor_penalty = 0.5`**: Startwert; ggf. weicher (0.7) oder härter.
