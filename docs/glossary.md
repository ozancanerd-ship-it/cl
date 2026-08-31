# Glossar

Begriffe, die die Analyse-Engines verwenden. Definitionen sind die im Projekt gültige
Referenz für die Implementierung.

## Market Structure

| Begriff | Bedeutung |
|---------|-----------|
| HH / HL | Higher High / Higher Low – Folge steigender Swing-Hochs/-Tiefs (Aufwärtsstruktur) |
| LH / LL | Lower High / Lower Low – Folge fallender Swing-Hochs/-Tiefs (Abwärtsstruktur) |
| BOS | Break of Structure – Bruch des letzten relevanten Swings **in Trendrichtung** |
| CHoCH | Change of Character – erster Bruch **gegen** die bestehende Struktur (mögliche Wende) |
| Trend | gerichtete Struktur (HH/HL oder LH/LL) |
| Range | seitwärts zwischen definierter Ober-/Untergrenze |
| Consolidation | enge Range mit fallender Volatilität |
| Expansion | Ausbruch aus Consolidation, steigende Range/Volatilität |
| Displacement | schnelle, impulsive Bewegung mit großen Kerzenkörpern / Imbalance |

## Liquidity

| Begriff | Bedeutung |
|---------|-----------|
| Buy-Side Liquidity (BSL) | Stop-Cluster **über** Hochs (Buy-Stops) |
| Sell-Side Liquidity (SSL) | Stop-Cluster **unter** Tiefs (Sell-Stops) |
| Equal Highs / Lows | (nahezu) gleiche Hochs/Tiefs – klarer Liquiditätspool |
| Swing High / Low | lokales Hoch/Tief mit n Kerzen links & rechts niedriger/höher |
| PDH / PDL | Previous Day High / Low |
| PWH / PWL | Previous Week High / Low |
| Asian/London/NY High/Low | Extrempunkte der jeweiligen Session |
| Liquidity Sweep | Preis nimmt Liquidität (durchbricht Level) und kehrt zurück |
| Stop Hunt | gezielter Sweep von Stops mit sofortiger Umkehr |
| False Breakout | Ausbruch ohne Follow-through, Rückkehr in die Range |

## Smart Money Concepts / Price Action

| Begriff | Bedeutung |
|---------|-----------|
| Fair Value Gap (FVG) | 3-Kerzen-Imbalance: Lücke zwischen Kerze 1 und Kerze 3 |
| Inverse FVG | durchbrochene FVG, die nun als Gegenzone wirkt |
| Order Block | letzte Gegenkerze vor impulsivem Move; potenzielle Reaktionszone |
| Breaker Block | Order Block, dessen Struktur gebrochen wurde und der invertiert wirkt |
| Mitigation | erneuter Test einer Zone (FVG/OB), oft teilweiser Fill |
| Imbalance | Ungleichgewicht Angebot/Nachfrage, sichtbar als FVG/Displacement |
| Premium / Discount | obere / untere Hälfte einer Range (Fibonacci 0.5 als Grenze) |
| Rejection | deutliche Ablehnung eines Levels (langer Docht) |
| Engulfing | Kerze umschließt Körper der Vorkerze vollständig |
| Pin Bar | Kerze mit langem Docht und kleinem Körper |
| Failed Breakout | Ausbruch, der scheitert und in die Struktur zurückfällt |

## Sessions

**Maßgeblich** (`strategy_version 0.1.1`, C3): die Session-Fenster sind in **Börsenlokalzeit**
definiert (`refdata/seed.py` / `config.example.yaml`) und werden zur Laufzeit **DST-korrekt nach
UTC aufgelöst** (`refdata.calendar.resolve_session`). Die folgenden UTC-Werte sind daher nur
grobe Orientierung und schwanken mit der Sommerzeit.

| Session | Börsenlokal (Spec) | ≈ UTC (Sommerzeit) |
|---------|--------------------|--------------------|
| Asia | Asia/Tokyo 09:00–15:00 | ≈ 00:00–06:00 |
| London | Europe/London 08:00–16:30 | ≈ 07:00–15:30 |
| New York | America/New_York 09:30–16:00 | ≈ 13:30–20:00 |
| London/New York Overlap | Schnittmenge London ∩ New York | ≈ 13:30–15:30 |

Für **24/7-Crypto** approximieren diese Fenster Liquiditäts-Phasen. Außerhalb einer Session wird
**nicht** der Handel gestoppt — es greift allein der **Entry-Session-Gate** aus
`setups/SMC-SWEEP-REV-01.md` §18 (`session.allowed = [london, newyork, london_ny_overlap]`).

## News / Makro

CPI, NFP, FOMC, Fed, PCE, GDP, PMI, Arbeitsmarktdaten, Zinsentscheidungen,
Zentralbankreden, geopolitische Ereignisse.
Gold zusätzlich: USD, DXY, US Treasury Yields, Fed-Erwartungen.

## Crypto-spezifisch

BTC, ETH, liquide Altcoins, Volume, Spread, Volatility, Funding Rate, Open Interest,
Liquidations, BTC-Dominanz, Korrelationen, Market Cap, Liquidität.

## Aktien-spezifisch

Earnings + Earnings Calendar, Pre-Market, After-Hours, Gap-ups/-downs, Unternehmensnews,
relevante Filings, Sektor-Stärke, Relative Strength, S&P-500- und Nasdaq-Kontext.

## Kennzahlen (Backtest / Performance)

| Kennzahl | Bedeutung |
|----------|-----------|
| Win Rate | Anteil gewonnener Trades |
| Profit Factor | Bruttogewinn / Bruttoverlust |
| Expectancy | erwarteter Gewinn pro Trade (in R) |
| Average R | durchschnittliches Ergebnis in Vielfachen des Anfangsrisikos |
| Max Drawdown | größter Equity-Rückgang vom Hoch |
| MFE / MAE | Maximum Favorable / Adverse Excursion je Trade |
| Consecutive Losses | längste Verlustserie |
