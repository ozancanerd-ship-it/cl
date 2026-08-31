# Strategy Logic Audit

**Datum:** 2026-08-28
**Umfang:** ausschließlich die **Trading-Logik** (Entry, Invalidierung, Exit, No-Trade, Regime,
Verknüpfung Liquidity/Structure/SMC, Widersprüche, Bewertung, Unsicherheit, Risiko/Sizing,
Portfolio/Korrelation, News, Assetklassen, Overfitting, fehlende Bausteine).
**Geprüfte Quellen:** `ARCHITECTURE.md` §4 (strategy/, analysis/), `docs/glossary.md`,
`config/config.example.yaml`, `config/scoring.example.yaml`, `config/risk.example.yaml`,
`docs/ARCHITECTURE_GAP_AUDIT.md`.
**Kein Trading-Code geschrieben.**

---

## Kernbefund

> Es existiert eine **Taxonomie** (BOS, CHoCH, FVG, OB, Sweep, Premium/Discount, …) und ein
> **Scoring-Gerüst** (13 Faktoren, Gewichte, Schwellen), aber **keine einzige operationalisierte
> Handelsregel**. Die Strategie ist heute eine Liste von Konzepten plus **ein** Prosa-Beispiel
> (`"HTF bullish + Sweep der Sell-Side + CHoCH + FVG im Discount"` in ARCHITECTURE.md §4).

Konkret fehlen durchgehend:
- **operative Definitionen** der Primitive (was ist ein Swing? was genau ist ein BOS? wann ist ein
  FVG „gültig"? wann ist ein OB „mitigated"?),
- **Regelketten** (in welcher Reihenfolge, auf welchem Timeframe, mit welchem Trigger),
- **Abbruch- und Ausnahmeregeln** (Invalidierung ≠ Stop-Loss; Widerspruchsauflösung; No-Trade).

Das ist für Phase 1 **blockierend**, weil die in „Phase 1a" geplanten Engines (`market_structure`,
`liquidity`, `smc`, `regime`, `setup_detection`, `confluence`, `scoring`) genau diese Definitionen
als Spezifikation brauchen. Ohne sie werden die Engines gegen ein Bauchgefühl implementiert – und
jede spätere Anpassung „bis der Chart passt" ist verdeckte Overfitting (siehe Frage 14).

---

## Die 15 Fragen

### 1. Sind unsere Entry-Regeln ausreichend konkret?

**Nein.** Es gibt genau ein Beispiel, keine Regel.

Nicht definiert:
- **Swing-Definition:** Fraktal mit `n` Kerzen links/rechts? ZigZag mit Schwelle in % oder ATR?
  Ab wann ist ein Swing *bestätigt* (braucht `k` Folgekerzen)?
- **„HTF bullish":** Reihe aus HH/HL über wie viele bestätigte Swings? Auf welchem TF exakt,
  relativ zum Entry-TF? Was, wenn D1 und H4 uneinig sind?
- **Sweep:** Wick über/unter Level um wie viel (Ticks / % / ATR)? Muss die Kerze *zurück* ins
  Level schließen? Innerhalb wie vieler Kerzen? Volumen-/Displacement-Bedingung?
- **CHoCH/BOS:** *Close* jenseits des letzten Gegen-Swings oder *Wick*? Auf welchem TF?
  Welcher Swing zählt als „relevant"?
- **FVG-Gültigkeit:** Mindestgröße (absolut / % / ATR)? Höchstalter? Muss unberührt sein?
  Muss Teil des Displacement-Legs sein, das die Struktur gebrochen hat?
- **Order Block:** Welche Kerze genau (letzte Gegenkerze / letzter Down-Close vor Up-Move)?
  Nur gültig, wenn er einen BOS verursacht hat? Zone = Body oder Body+Wick?
- **Premium/Discount:** 50 %-Equilibrium **welcher** Range – Dealing Range / letztes Impuls-Leg /
  Session-Range / HTF-Swing-Range?
- **Entry-Ausführung:** Limit an FVG-Kante / FVG-50 % / OB-Open? Market bei Bestätigung?
  Stop-Entry beim Bruch? Bestätigungskerze nötig (Engulfing / Rejection / LTF-CHoCH)?
- **Timing:** bei Close der Signalkerze / bei Tap der Zone / erst nach LTF-Bestätigung?
- **Kandidaten-Lebensdauer:** Verfällt ein Setup nach `N` Kerzen / bei Session-Ende, wenn kein
  Trigger?
- **Mehrere gültige Setups** auf demselben Instrument gleichzeitig – welches gewinnt?

**Nötig:** je Setup-Typ eine Entry-Checkliste + eine Zustandsmaschine
`beobachtet → scharf (armed) → getriggert → gefüllt`.

### 2. Sind Invalidierungsregeln definiert?

**Nein.** „Exit bei Invalidierung" wird in `trade_management` genannt, aber nirgends definiert.
Das Decision Ledger hat kein Invalidierungs-Feld.

Fehlt komplett die Trennung:
- **Pre-Entry-Invalidierung:** Setup ist *armed*, aber die Prämisse bricht weg → Kandidat löschen.
  Auslöser z. B.: gegenläufiger CHoCH, Sweep des Schutzpunkts, HTF-Bias-Flip, News-Fenster öffnet,
  Zeitablauf, Regimewechsel.
- **Post-Entry-Strukturinvalidierung ≠ Stop-Loss:** Preis schließt jenseits des OB/FVG oder bricht
  den CHoCH-Punkt, der das Setup begründet hat → **jetzt raus**, auch wenn der harte SL noch nicht
  getroffen ist.
- **Zeit-Invalidierung:** Trade „lebt" nicht innerhalb `N` Kerzen / bis Session-Ende → flat.
- **Prämissen-Invalidierung:** die getradete FVG wird vollständig invertiert (IFVG gegen uns).

**Nötig:** ein „Structural-Invalidation-Model" mit expliziten Auslösern, getrennt vom SL.

### 3. Sind Exit-Regeln vollständig?

**Nein – nur Überschriften.** Genannt: TP1/2/3, Teilgewinn, Break-even, Trailing, struktur-basierter
Stop, Exit bei Invalidierung. Nicht definiert:
- **TP-Platzierung:** an gegenüberliegender Liquidität (BSL/SSL)? an nächster HTF-Struktur?
  festen R-Vielfachen? Welche Logik für welchen Setup-Typ?
- **Teilgrößen:** 33/33/33? 50/25/25? nicht festgelegt.
- **Break-even-Trigger:** nach TP1? nach +1R? nach Verlassen der FVG?
- **Trailing-Methode:** Swing-zu-Swing (welcher TF)? ATR-Chandelier? Kerze-für-Kerze?
- **Struktur-Stop:** hinter welchem Swing genau, Puffergröße?
- **Präzedenz:** wenn Trailing-Stop, Struktur-Stop und Zeit-Exit gleichzeitig feuern – Reihenfolge?
- **„Toter Trade":** flat nach `X` Kerzen ohne Fortschritt nahe Entry?
- **Exit bei News:** vor High-Impact-Event schließen, oder nur keine neuen Entries?
- **Session-Ende:** Intraday-Setups zwangsflat? (assetklassenabhängig – Crypto 24/7!)
- **Runner-Behandlung**, wenn ein Gegen-Setup erscheint.

**Nötig:** ein Exit-Entscheidungsbaum mit klarer Präzedenz, je Setup-Typ.

### 4. Ist klar definiert, wann NICHT gehandelt wird?

**Teilweise.** Vorhanden: `min_score`, `min_confluence_factors`, `min_risk_reward`,
News-Blackout 15/15 min, `block_on_incomplete_data`, verbotene Verhaltensweisen.

Fehlt als **konsolidierte Stand-Aside-Checkliste**:
- Regime = Range/choppy und Strategie ist trendfolgend → kein Trade (Regime-Engine nicht verbunden).
- Volatilität zu niedrig (kein Displacement möglich) oder zu hoch (Stops unbrauchbar, Spread-Blowout).
- Spread / Liquidität zu dünn (Tageszeit, illiquider Altcoin).
- Kein sauberer HTF-Bias (D1 gegen H4) → aussetzen.
- Preis im „Niemandsland" (Mitte der Range, Equilibrium, keine nahe Liquidität als Ziel).
- Erste `N` Minuten nach Session-Open.
- Ziel-Liquidität zu nah → erreichbares R vor Gegen-Liquidität < Schwelle.
- Gerade auf demselben Instrument ausgestoppt / fehlgeschlagener Sweep → Cooldown.
- Wochenende / dünne Feiertage / Rollover.
- `max_trades` / `max_open_positions` / korrelierte Exposure erreicht.
- R:R erreicht das Minimum **nur** wegen künstlich engem SL.
- `data_confidence` unter Schwelle.

**Nötig:** eine einzige, testbare `no_trade_reasons`-Liste (Enum), die *vor* der Score-Berechnung greift.

### 5. Sind die Regeln für Trend, Range und hohe Volatilität getrennt?

**Nein.** Regime ist heute nur ein Scoring-Faktor (`market_regime`, Gewicht 7) und eine
Backtest-Auswertungsdimension. Es fehlt:
- ein **Regime-Klassifikator** mit konkreten Definitionen (Trend / Range / Vol-hoch / Vol-niedrig /
  Expansion), Inputs und Schwellen, mit **Hysterese** gegen Flattern.
- ein **Setup-Katalog je Regime** (Trend-Continuation vs. Range-Fade vs. Breakout/Expansion vs.
  Sweep-Reversal).
- **Parameter-Sets je Regime:** Score-Schwelle, RR-Ziel, Positionsgröße, TP-Logik, erlaubte Entry-Art
  unterscheiden sich pro Regime.
- **Übergangsbehandlung:** Regime gerade gewechselt → keine neuen Entries / offene Trades straffen /
  flat?
- **Hoch-Vol-Regel:** Stops weiter, Größe kleiner, größeres Displacement gefordert, keine Limit-Entries.

### 6. Sind Liquidity, Market Structure und SMC logisch miteinander verknüpft?

**Nein, nur implizit im einen Beispiel.** Das kausale Modell ist nirgends aufgeschrieben.

Fehlt die **SMC-Narrative-Kette** als formales Modell:

```
HTF-Bias  →  Preis expandiert zu einem Liquiditätspool  →  Sweep dieses Pools
          →  LTF-Strukturbruch (CHoCH/BOS)  →  Rückkehr in die Ursprungs-Imbalance
             (FVG/OB) in Premium/Discount  →  Entry  →  Ziel = gegenüberliegende Liquidität
```

Fehlende **Abhängigkeitsregeln**:
- Ein OB ist nur gültig, wenn er einen BOS verursacht hat.
- Eine FVG ist nur handelbar, wenn sie Teil des Displacement-Legs ist, das die Struktur brach.
- Ein Sweep zählt nur, wenn er *bedeutsame* Liquidität nimmt (Equal Highs/Lows, PDH/PDL,
  Session-H/L) – nicht irgendein Wick.
- **TF-Zuordnung:** Bias aus D1/H4, Liquiditätskarte aus H4/H1, Strukturbruch aus M15,
  Entry-Verfeinerung aus M5/M1. Heute nicht festgelegt.
- **POI-Auswahl:** bei mehreren OBs/FVGs – welcher wird getradet? (Ranking-Logik fehlt → Gefahr
  von Hindsight-Auswahl, siehe Frage 14.)
- **Konflikt:** Struktur bullish, aber Buy-Side gesweept (bearish) – Auflösungsregel fehlt.

### 7. Gibt es Regeln gegen widersprüchliche Signale?

**Kaum.** `required_factors` und `min_confluence_factors` sind rein **additive** Gates; es gibt keine
**Widerspruchs-Behandlung**.

Nicht abgedeckte Widersprüche:
- HTF bullish vs. MTF-Struktur bearish.
- Long-Setup, aber Preis im Premium (für Longs sollte es Discount sein) → Location-Sanity fehlt.
- SSL-Sweep (bullish) direkt gefolgt von BSL-Sweep (beide Seiten genommen → kein Edge).
- Zwei Engines uneinig (S/R sagt Widerstand hier, SMC sagt bullisher OB hier).
- News-Bias gegen technischen Bias.
- Long-Setup, während das Portfolio bereits netto-long im korrelierten Korb ist.

**Nötig:**
- **Veto-Matrix** (Paare, die sich gegenseitig ausschließen),
- **Directional-Agreement-Check** über die TFs,
- **Location-Sanity-Check** (Long nur im Discount),
- **Tie-Breaker**,
- Regel: **ungelöster Widerspruch = kein Trade** (nicht „mitteln").

### 8. Wie wird ein Setup objektiv bewertet?

**Rahmen vorhanden, Substanz fehlt.** Vorhanden: 0–100 gewichtete Faktoren, Schwellen je Assetklasse,
Pflichtfaktoren.

Probleme:
- **Faktorwert in [0,1] ist undefiniert.** Wie wird `htf_bias = 0.7` berechnet? Es fehlt je Faktor
  eine **objektive Rubrik** (welcher messbare Input → welcher Wert).
- **Keine Trennung** binärer Faktoren (Sweep: ja/nein) von graduellen (RR: kontinuierlich).
- **Gewichte sind geraten**, ohne Herleitung und ohne Kalibrierungsplan (und auf denselben Daten
  tunen = Overfit → Frage 14).
- **Confluence-Gate vs. Score ist redundant** (bereits in `ARCHITECTURE_GAP_AUDIT.md` R-06): ein
  Faktor ist entweder hartes Gate **oder** gewichteter Beitrag, nicht beides.
- **Kein Setup-Typ-Konzept:** Range-Fade und Trend-Continuation teilen sich eine Scoring-Tabelle.
- **Score nicht an Expectancy gekoppelt:** ein 75er muss empirisch besser abschneiden als ein 65er –
  dafür gibt es keine Validierungsschleife.
- **Nur positive Beiträge** – Widersprüche müssten **Punkte abziehen**.

### 9. Wie wird Unsicherheit behandelt?

**Minimal.** `block_on_incomplete_data`, `is_complete`/`has_gaps` – rein boolesch.

Fehlt:
- **Daten-Konfidenz als graduelle Größe** (Teilhistorie, `X` min veraltet, Einzelquelle).
- **Analytische Unsicherheit:** Swing noch unbestätigt (braucht Zukunftskerzen), FVG teil-mitigiert,
  Struktur mehrdeutig (Equal Highs unklar), Regime nahe an einer Grenze.
- **Propagation:** unsichere Inputs müssen Score senken / geforderte Confluence erhöhen, nicht still
  durchlaufen.
- **„Unbestätigte Struktur"-Regel:** nicht von einem Swing traden, den die aktuelle Kerze noch
  invalidieren kann (Look-ahead-nah).
- **Konfligierende TFs = Unsicherheit** (Frage 7).
- **Default unter Unsicherheit = aussetzen** (muss explizit sein).
- **Kein `confidence`-Feld** im Decision Ledger.
- LLM-/AI-Unsicherheit (Contract im vorherigen Audit gefordert, G-16).

### 10. Sind Risiko und Positionsgröße vollständig definiert?

**Am besten abgedeckt, aber nicht vollständig.** Vorhanden: `risk_pct 0.5`, SL-Pflicht, RR ≥ 2,
`max_position_pct 20`, Tages-/Wochen-/Drawdown-Limits, Verbote (Martingale / Nachkaufen / Erhöhung
nach Verlusten).

Fehlt / undefiniert:
- **Vol-adjustierte Größe (ATR):** fixes % ignoriert, dass 0,5 % Risiko mit winzigem SL = riesiges
  Notional. (auch `ARCHITECTURE_GAP_AUDIT.md` G-23)
- **SL-Distanz-Regel:** wie bestimmt die *Strategie* den SL (struktur-basiert: hinter Sweep/OB +
  Puffer)? Puffergröße undefiniert.
- **Mindest-SL-Distanz** (gegen überhebelte Mini-Stop-Trades).
- **Partial-Fill → tatsächliche Größe** für die Risikorechnung.
- **Scaling in/out:** ist Zuladen zu Gewinnern erlaubt? („kein Nachkaufen von Verlusten" verbietet
  nur Verlierer – Gewinner unklar.)
- **Risiko je Setup-Typ / je Regime** (Range-Trades kleiner?).
- **Korrelierte Risiko-Aggregation** in die Einzeltrade-Entscheidung (4. korrelierte Position →
  effektives Portfoliorisiko > Summe).
- **Leverage / Margin / Liquidationsdistanz** (Crypto-Perps) – nicht im Strategie-Sizing (G-22).
- **Tagesverlust erreicht → nur keine neuen Entries, oder auch flatten?** undefiniert.
- **„Risiko" = Distanz zum harten SL oder zur Strukturinvalidierung?** (unterschiedlich).

### 11. Sind Portfolio- und Korrelationsrisiken berücksichtigt?

**Teilweise (Config).** Vorhanden: `max_correlated_exposure_pct 30`, `correlation_threshold 0.7`,
`max_total_exposure_pct 60`, `max_open_positions 4`.

Fehlt:
- **Korrelationsquelle/-fenster undefiniert** (rollierend über wie viele Tage, welche Returns,
  welcher TF).
- **Bekannte/statische Beziehungen** (BTC↔Alts, XAU↔DXY invers, Index-Beta) als Baseline-Matrix –
  vs. gemessen.
- **Ökonomische/Faktor-Exposure** (USD, Zinsen, Risk-on), nicht nur Preis-Korrelation (G-21).
- **Netting:** Long BTC + Short ETH = reduziert; Long BTC + Long ETH = additiv – ist die Rechnung
  richtungsbewusst?
- **Cluster-Limit** (max `N` Positionen je Cluster: „Crypto-Beta", „USD-Short", „Long-Duration").
- **Neu-Trade-Impact-Test:** Portfolio **mit** dem Kandidaten simulieren, *bevor* freigegeben wird.
- **Gleichrichtungs-Stacking** auf einem Instrument über mehrere Setups.
- **Korrelations-Regimewechsel** (Korrelationen → 1 im Crash) als Stress-Annahme.
- **Portfolio-Heat** (Summe offener Risiken) vs. harte Obergrenze – ist `max_total_exposure`
  Notional oder Risiko?

### 12. Sind News-/Event-Regeln vollständig?

**Nein.** Vorhanden: Blackout 15 min vor/nach, News als Scoring-Faktor.

Fehlt:
- **Impact-Klassifikation** (high/medium/low) und welche Stufen den Blackout auslösen.
- **Event → Instrument-Routing** (NFP/CPI/FOMC → USD-Paare, XAU, Indizes; Token-Unlock → dieser Alt;
  Earnings → diese Aktie). Glossar listet Events, aber ohne Zuordnung.
- **Blackout-Umfang:** nur neue Entries sperren? auch offene Positionen schließen/reduzieren? auf BE
  ziehen? Stops weiten?
- **Dauer:** symmetrische 15 min sind naiv – FOMC braucht länger; manche Events brauchen ein
  Vorpositionierungs-Verbot Stunden vorher.
- **Post-News-Re-Entry-Regel** (warten auf erste Struktur / `X` min / Volatilitäts-Normalisierung).
- **Geplant vs. ungeplant** (Geopolitik) – keine Headline-Risk-Behandlung, keine Ingestion-Quelle.
- **Datenrevisionen** (`ARCHITECTURE_GAP_AUDIT.md` B-02).
- **Session-Open als Quasi-Event** (erste `N` min).
- **Earnings:** Halten über Earnings verboten? Gap-Risiko.
- **Crypto:** kein Äquivalent (Funding-Settlement-Zeiten, große Unlocks, Exchange-Wartung).
- **Zentralbankreden vs. Hartdaten** – unterschiedliche Behandlung?
- **News-Kalender fehlt/veraltet → fail-safe (kein Trade)** – undefiniert.

### 13. Sind unterschiedliche Assetklassen ausreichend berücksichtigt?

**Nein – die Logik ist generisch.** `instruments` trägt `asset_class`, aber die Strategie ist
one-size.

- **Crypto:** 24/7 (kein Session-Close-Flat), Funding als Kosten **und** Signal (Extrem-Funding =
  überfüllt = faden?), OI-/Liquidations-Kaskaden als Liquiditätsereignisse, Wochenend-Illiquidität,
  Alt-Beta zu BTC (kein Alt-Long, wenn BTC bearish), Listing/Delisting, exchange-spezifische
  Liquidität, höhere Vola → andere Stop-Multiplikatoren.
- **Gold (XAUUSD):** getrieben von DXY / Yields / Realzinsen / Fed – technisches Setup **muss** mit
  Makro übereinstimmen (kein XAU-Long, wenn DXY ausbricht); London/NY-Session-Liquidität; Spread
  weitet sich in Asia; Rollover.
- **Forex:** session-getrieben, Zentralbank-Divergenz, Carry/Rollover, sehr enge Asia-Ranges, DXY
  als gemeinsamer Faktor, Korrelations-Cluster (EUR/GBP, Rohstoffwährungen).
- **Aktien:** nur RTH + Gap-Risiko (Overnight-Halten = Gap-Exposure), Earnings, Halts, Pre-/
  After-Hours-Illiquidität, Sektor-/Relative-Stärke, Index-Kontext (SPX/NDX) – kein Long einer
  schwachen Aktie in schwachem Sektor bei schwachem Markt; Leihgebühr für Shorts;
  Hard-to-borrow = kein Short.
- **Welche Setups gelten für welche Klasse** (SMC-Intraday funktioniert auf liquidem FX/Crypto/
  Indizes; weniger auf Einzelaktien mit Gaps).
- **Instrument-spezifische Parameter** (Tick-Size, typischer Spread, Session, Mindest-Stop-Distanz).

### 14. Gibt es mögliche Overfitting-Fallen?

**Ja, viele – und das Design lädt aktiv dazu ein.**

Konkrete Fallen in diesem Projekt:
- **Tuning der 13 Scoring-Gewichte** auf Historie, dann Performance auf denselben Daten berichten.
  Freiheitsgrade: 13 Gewichte + 4 Assetklassen-Schwellen + `min_confluence_factors` + `min_score` +
  `min_risk_reward` ≈ **20 Knöpfe**.
- **SMC-Parameter-Wildwuchs:** Swing-Lookback, FVG-Mindestgröße, Sweep-Penetrationstiefe,
  OB-Definition, Mitigation-%, Displacement-Schwelle, Premium/Discount-Referenz, Puffergrößen –
  jeder ist ein Knopf; jeder „bis der Chart passt" getunte Wert ist verdeckte diskretionäre
  Überanpassung im Code.
- **Session-Zeiten** an das Backtest-Fenster angepasst.
- **POI-Auswahl in Hindsight** (die FVG/OB nehmen, die funktioniert hat) – die fehlende
  Ranking-Regel (Frage 6) macht das zur Einladung.
- **Regime-Grenzen** so getunt, dass In-Sample-Buckets „sauber" aussehen.
- **Zu viele Setup-Typen** mit je wenigen Samples → jeder sieht auf 15 Trades großartig aus.
- **Optimierung auf einem Krypto-Bullenmarkt** (2020–21, 2023–24) → Survivorship + Regime-Bias.
- **Blackout-Fenster / Cooldowns** so gelegt, dass sie konkrete historische Verluste vermeiden.
- **Confluence-Faktorliste wächst**, bis die Win-Rate ein Ziel trifft.
- **Backtest nach jeder Regeländerung neu laufen** und behalten, was half (Multiple Testing ohne
  Korrektur).

**Gegenmaßnahmen (in die Strategie-Doku und den Research-Plan schreiben):**
- Regelwerk + Parameter **vor** dem Test schriftlich fixieren (Pre-Registration, Verweis in der
  Experiment-Registry G-14).
- Parameter als **Bereiche**, nicht Punkte; Sensitivitätsanalyse – Performance muss ein **Plateau**
  sein, kein Peak.
- Walk-Forward + unberührtes Hold-out (G-12).
- **Obergrenze für freie Parameter** (Vorschlag: ≤ 8 für den MVP).
- Primitive-Definitionen aus **Theorie/Marktmechanik** ableiten, nicht aus dem Fit.
- **Mindest-Samplegröße je Bucket** (Vorschlag: ≥ 30 Trades, sonst nicht bewerten).
- **Anzahl getesteter Konfigurationen protokollieren** und bei der Signifikanz berücksichtigen.
- Wenige Setup-Typen mit vielen Samples statt vieler mit wenigen.

### 15. Welche strategischen Komponenten fehlen noch?

| Fehlende Komponente | Zweck |
|---|---|
| **Strategie-Spezifikation `docs/strategy/`** | Das eigentliche Regelbuch: je Setup-Typ Entry/Invalidierung/Exit/No-Trade, TF-Zuordnung, Regime-Anwendbarkeit. |
| **Setup-Katalog / Taxonomie mit IDs** | z. B. `SMC-TREND-CONT-01`, `SMC-SWEEP-REV-01`, `RANGE-FADE-01` – Voraussetzung für setup-spezifische Statistik. |
| **Regime-Klassifikator-Spec** | konkrete Definitionen, Inputs, Hysterese. |
| **Liquidity-Draw- / Target-Model** | Wohin will der Preis? – die Ziel-Liquiditäts-Auswahl. |
| **Narrative- / Bias-Model** | die Top-down-Story, die HTF→LTF verbindet („was macht der Preis und warum"). |
| **POI-Auswahl & -Ranking** | welcher OB/FVG, objektiv bewertet. |
| **Structural-Invalidation-Model** | getrennt vom SL. |
| **Widerspruchs-/Veto-Matrix** | inkl. MTF-Konfliktauflösung. |
| **Faktor-Scoring-Rubriken** | objektives 0..1-Mapping je Faktor. |
| **Konfidenz-/Unsicherheits-Model** | graduelle Datenqualität + analytische Unsicherheit. |
| **Trade-/Setup-Zustandsmaschine** | Idee → beobachtet → armed → getriggert → gemanagt → geschlossen → reviewt. |
| **Session-/Tageszeit-Playbook je Assetklasse** | wann welche Setups aktiv sind. |
| **Korrelations- & Faktor-Exposure-Baseline** | bekannte Beziehungen (BTC↔Alt, XAU↔DXY, Index-Beta). |
| **News-Routing-Tabelle** | Event → betroffene Instrumente → Aktion. |
| **Cooldown- / Tilt-Control-Regeln** | nach Verlust/Stop, nach Tagesmax, nach fehlgeschlagenem Sweep. |
| **Entry-Ausführungs-Model** | Limit vs. Stop vs. Bestätigung, exakte Position. |
| **Partial-/Scaling-Regeln** | explizit. |
| **Exit-Entscheidungsbaum mit Präzedenz** | siehe Frage 3. |
| **Backtest-Labeling-Regeln** | was zählt als Win, wo werden MFE/MAE gemessen, Entry/Exit-Zeitstempel-Semantik. |
| **Expectancy-Tracking → Gewichts-Kalibrierungsschleife** | nur OOS, governt. |
| **„Minimal Viable Strategy" für den MVP** | **ein** Setup-Typ, **ein** Regime, **ein** Asset, vollständig spezifiziert – das, was zuerst gebaut wird. |

---

## Querschnitt-Befunde

1. **Redundanz Confluence ↔ Scoring** (auch R-06 im Architektur-Audit): vor Phase 1 entscheiden –
   entweder harte Gates + einfaches RR-Modell für den MVP, oder das gewichtete 0–100-Modell, aber
   nicht beides parallel.
2. **Regime ist der fehlende Dreh- und Angelpunkt:** ohne Regime-Klassifikator hängen die Fragen
   5, 8, 10 und 14 in der Luft.
3. **Die Strategie braucht ein explizites Zielobjekt:** `SetupCandidate` in `core/types.py` muss
   *alle* Felder tragen, die Entry, Invalidierung, Exit, Score, Konfidenz und Setup-ID abbilden –
   sonst können die Engines nichts Vollständiges liefern.
4. **„Kein Einzelindikator-Trade" ist heute nur eine Zahl** (`min_confluence_factors: 3`), keine
   Logik – 3 schwache, gleichgerichtete Faktoren sind kein Edge.
5. **Alles hängt an Definitionen der Primitive.** Solange „Swing", „BOS", „FVG-gültig", „OB",
   „Sweep", „Displacement", „Premium/Discount-Range" nicht numerisch definiert sind, ist jede
   Engine-Implementierung Ratearbeit.

---

## Ergebnis-Listen

### MUSS vor Phase 1 ergänzt werden

> Grund: Phase 1a baut genau die Engines, die diese Spezifikationen als Input brauchen. Ohne sie
> wird gegen Bauchgefühl implementiert und später „bis der Chart passt" nachjustiert (= Overfit).

| # | Ergänzung | Warum blockierend |
|---|-----------|-------------------|
| M-01 | **Operative Definitionen der Primitive** (numerisch): Swing/Fraktal (Lookback), BOS/CHoCH (Close vs. Wick, welcher Swing), FVG (Mindestgröße in ATR, Alter, unberührt, Teil des Struktur-Legs), Order Block (welche Kerze, nur mit BOS, Zone), Liquiditäts-Level (welche zählen: Equal H/L, PDH/PDL, PWH/PWL, Session-H/L), Sweep (Penetration + Reclaim + Frist), Displacement (Schwelle), Premium/Discount (Referenz-Range) | Engines `market_structure`, `liquidity`, `smc` sind ohne diese nicht baubar/testbar |
| M-02 | **Regime-Klassifikator-Spezifikation** (Trend / Range / Vol-hoch / Vol-niedrig / Expansion): Inputs, Schwellen, Hysterese | `analysis/regime.py` ist im Architektur-Audit MVP-Pflicht; steuert Strategie *und* Risiko |
| M-03 | **Minimal Viable Strategy: ein Setup-Typ voll spezifiziert** (Entry-Checkliste, Trigger, Kandidaten-Lebensdauer, Pre-/Post-Entry-Invalidierung, Exit-Baum mit Teilgrößen/BE/Trailing, SL-Distanz-Regel + Puffer, No-Trade-Bedingungen) – Vorschlag: `SMC-SWEEP-REV-01` auf Crypto, HTF H4-Bias / M15-Struktur / M5-Entry | `setup_detection` + `trade_management` brauchen ein konkretes Ziel; MVP-Backtest sonst inhaltsleer |
| M-04 | **Konsolidierte No-Trade-Checkliste** als Enum (`NoTradeReason`) | Frage 4; muss *vor* dem Scoring greifen und getestet werden |
| M-05 | **Structural-Invalidation-Model** (Auslöser pre- und post-Entry), getrennt vom harten SL | Frage 2; ohne das gibt es kein „die Idee ist falsch"-Exit |
| M-06 | **Widerspruchs-/Veto-Regeln**: Directional-Agreement über TFs, Location-Sanity (Long nur Discount), Veto-Paare, MTF-Konfliktauflösung, Regel „ungelöster Widerspruch = kein Trade" | Frage 7; „kein Einzelindikator-Trade" wird sonst zur Farce |
| M-07 | **Entscheidung Confluence-Gate vs. gewichtetes Scoring** für den MVP + (falls Scoring) **Faktor-Rubriken** (objektives 0..1-Mapping) für die tatsächlich genutzten Faktoren; negative Faktoren zulassen | Frage 8 + R-06; doppelte/undefinierte Bewertung sonst nicht implementierbar |
| M-08 | **Konfidenz-/Unsicherheits-Model**: `data_confidence` (graduell) + `analysis_confidence` (unbestätigte Struktur etc.) als Felder im `MarketContext`; Regel „Konfidenz < Schwelle → kein Setup"; `confidence` ins Decision Ledger | Frage 9 + Architektur-Audit A-07; „keine Trades bei unsicheren Daten" braucht einen technischen Träger |
| M-09 | **`SetupCandidate`-Schema vollständig**: Setup-ID, Richtung, TF-Set, Entry-Zone + Entry-Art, harter SL, Struktur-Invalidierungspunkt, TP1–3 + Teilgrößen, Score/Konfidenz, Begründungsbausteine, Ablaufzeitpunkt | `core/types.py` (Phase 1) muss das tragen, sonst Nacharbeit an allen Engines |
| M-10 | **Positionsgrößen-Vervollständigung für den MVP**: SL-Distanz-Regel (struktur-basiert + Puffer), Mindest-SL-Distanz, Vol-Adjustierung (ATR-Option), Partial-Fill→Ist-Größe, korrelierte Risiko-Aggregation, Tagesverlust-Aktion (nur Stop neuer Entries vs. Flatten) | Frage 10; `position_sizing` + `risk_engine` sonst unvollständig |
| M-11 | **News-Minimalregeln für die MVP-Assetklasse**: Impact-Klassifikation, Event→Instrument-Routing-Tabelle, Blackout-Umfang + -Dauer je Impact-Stufe, fail-safe bei fehlendem Kalender | Frage 12; `analysis/news.py` (MVP) braucht die Routing-Logik |
| M-12 | **Setup-Katalog mit IDs** (auch wenn zunächst nur 1–2 Einträge) + **Backtest-Labeling-Regeln** (Win-Definition, MFE/MAE-Messpunkte, Zeitstempel-Semantik) | setup-spezifische Statistik + reproduzierbarer Backtest (Architektur-Audit B-13) |
| M-13 | **Overfitting-Guardrails schriftlich** im Research-Plan: Pre-Registration des MVP-Regelwerks, Parameter-Obergrenze (≤ 8), Definitionen-aus-Theorie-Policy, Mindest-Samplegröße je Bucket, „Anzahl Konfigurationen protokollieren" | Frage 14; muss stehen, *bevor* der erste Backtest läuft |
| M-14 | **Assetklassen-Grundprofil für die MVP-Klasse** (Vorschlag Crypto: 24/7, kein Session-Flat, Funding als Kosten, Alt-Beta-zu-BTC-Regel, Wochenend-Illiquidität, Vola-Stop-Multiplikator) | Frage 13; generische Logik führt sonst zu falschen Stops/Exits |

### SOLLTE später ergänzt werden

| # | Ergänzung | Sinnvoll ab |
|---|-----------|-------------|
| S-01 | Vollständiger Setup-Katalog (alle Regime: Continuation, Reversal, Range-Fade, Breakout/Expansion) | nach MVP, vor Paper |
| S-02 | Liquidity-Draw- / Ziel-Auswahl-Model (wohin will der Preis) | Paper |
| S-03 | POI-Ranking-Model (mehrere OB/FVG objektiv bewerten) | Paper |
| S-04 | Per-Assetklassen-Playbooks (Gold: DXY/Yields-Agreement; Forex: Session/Carry/Cluster; Aktien: Sektor/RS/Index-Kontext, Gap-/Halt-/Borrow-Regeln) | wenn die jeweilige Klasse aktiviert wird |
| S-05 | Faktor-Exposure- / ökonomisches Korrelationsmodell (USD, Zinsen, Risk-on) statt nur Preis-Korrelation | Paper (auch Architektur-Audit G-21) |
| S-06 | Cluster-Limits (max N je „Crypto-Beta" / „USD-Short" / „Long-Duration") | Paper |
| S-07 | Cooldown- / Tilt-Control-Regeln (nach Verlust/Stop/Tagesmax/Sweep-Fail) | Paper |
| S-08 | Expectancy-Tracking → governte, OOS-basierte Gewichts-Kalibrierung | Paper (nach ausreichend Trades) |
| S-09 | Post-News-Re-Entry-Regeln + Session-Open-Handling je Assetklasse | Paper |
| S-10 | Neu-Trade-Portfolio-Impact-Simulation vor Freigabe | Paper |
| S-11 | Gewichtetes 0–100-Scoring (falls MVP mit binären Gates startet) | Paper |
| S-12 | Vollständige Trade-/Setup-Zustandsmaschine mit Review-Phase | Paper |
| S-13 | Multi-Source-Daten-Konfidenz-Graduierung | Paper/Demo |
| S-14 | Korrelations-Regime-Stressmodell (Korrelationen → 1 im Crash) | Demo |
| S-15 | Zentralbankreden vs. Hartdaten – differenzierte Behandlung; Headline-/Geopolitik-Ingestion | Demo |

### OPTIONAL

| # | Ergänzung | Anmerkung |
|---|-----------|-----------|
| O-01 | ML-basiertes Setup-Ranking / Regime-Erkennung | erst nach stabiler regelbasierter Baseline |
| O-02 | LLM-Narrative-/Bias-Generierung (Contract ist MUSS, *Nutzung* optional) | streng gemäß G-16-Grenzen; darf Risk/Confluence/Scoring nie umgehen |
| O-03 | Sentiment / Order-Flow / Footprint / DOM-Analyse | zusätzliche Datenquellen nötig |
| O-04 | Inter-Market-Lead-Lag-Modelle (z. B. DXY führt XAU) | Forschungsprojekt |
| O-05 | Alt-Season- / BTC-Dominanz-Rotationslogik | nur bei aktivem Altcoin-Portfolio |
| O-06 | Options-abgeleitete Signale (Gamma, Skew) für Indizes | nur Aktienindizes |
| O-07 | Auto-Tuning der Gewichte | gefährlich; nur stark governt, OOS, mit Trial-Protokoll |
| O-08 | Meta-Strategie / Portfolio-Allokation über mehrere Strategien | erst bei > 1 validierter Strategie |
| O-09 | Adaptive Parameter je Volatilitäts-Perzentil (statt fester Schwellen) | erhöht Overfitting-Risiko – nur mit Walk-Forward |

---

## Empfehlung zum weiteren Vorgehen

1. **Die 5 Architekturentscheidungen noch nicht finalisieren**, bis M-01 bis M-03 grob stehen –
   sie beeinflussen `core/types.py` (`SetupCandidate`-Schema, M-09) und die Wahl
   Confluence-vs-Scoring (M-07).
2. **Neues Verzeichnis `docs/strategy/`** anlegen mit:
   `primitives.md` (M-01), `regime.md` (M-02), `setups/SMC-SWEEP-REV-01.md` (M-03),
   `no-trade.md` (M-04), `invalidation.md` (M-05), `contradictions.md` (M-06),
   `scoring-rubric.md` (M-07), `confidence.md` (M-08), `sizing.md` (M-10),
   `news-rules.md` (M-11), `backtest-labeling.md` (M-12), `anti-overfitting.md` (M-13).
3. **Erst danach** ARCHITECTURE.md / TODO.md / Configs anpassen und Phase 1a starten.
