#!/usr/bin/env python3
"""Trend-Swing vorwaerts mitschreiben — nur aufzeichnen, nie melden.

Die Regel aus ``docs/TREND-SWING-STUDIE-2026-09.md`` wurde am 30.09. verworfen (Ergebnis C):
in 2025 und 2026 zusammen kein Gewinn. Das Dokument legt fest, dass sie ab jetzt vorwaerts
aufgezeichnet wird. Vorwaertsdaten sind die einzigen, die noch niemand gesehen hat — eine
erneute Pruefung ist erlaubt ab 60 neuen abgeschlossenen Trades, mit genau dieser Regel.

Das Journal ist ein Protokoll, keine Neuberechnung. Je Coin steht darin die offene Position
(Einstieg, Stop, Hoch, Tage) und die zuletzt verarbeitete Tageskerze. Jeder Lauf verarbeitet
nur die Kerzen, die seitdem geschlossen haben — mit genau derselben Funktion ``kerze`` wie
die Studie. Ein einmal eingetragener Trade wird nie umgeschrieben. Eine Neuberechnung ueber
ein gleitendes 400-Tage-Fenster waere pfadabhaengig: je nach Fensterstart entstuende eine
andere Kette von Trades, und die Geschichte veraenderte sich still.

    python3 scripts/trend_swing_forward.py            # holt Tageskerzen, schreibt Journal
    python3 scripts/trend_swing_forward.py --bericht  # nur Stand zeigen
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from trend_swing_studie import COINS, ERSTE_KERZE, Position, indikatoren, kerze

JOURNAL = Path("data/repository_real/live/trend_swing_forward.json")
#: Ab hier zaehlt es als vorwaerts. Alles davor ist Studie.
START = datetime(2026, 10, 1, tzinfo=UTC)
ERNEUT_PRUEFEN_AB = 60

BINANCE_HOSTS = (
    "https://data-api.binance.vision",  # oeffentlicher Daten-Spiegel, meist ohne Geosperre
    "https://api.binance.com",
    "https://api-gcp.binance.com",
    "https://api1.binance.com",
)


def _hole(coin: str, tage: int = 400) -> dict[str, list[Any]] | None:
    import httpx

    for host in BINANCE_HOSTS:
        try:
            r = httpx.get(
                f"{host}/api/v3/klines",
                params={"symbol": f"{coin}USDT", "interval": "1d", "limit": tage},
                timeout=30,
            ).json()
        except Exception:
            continue
        if isinstance(r, list) and len(r) > 70:
            fertig = r[:-1]  # letzte Kerze laeuft noch
            return {
                "t": [int(k[0]) for k in fertig],
                "o": [float(k[1]) for k in fertig],
                "h": [float(k[2]) for k in fertig],
                "l": [float(k[3]) for k in fertig],
                "c": [float(k[4]) for k in fertig],
                "schluss": [
                    datetime.fromtimestamp(int(k[0]) / 1000, tz=UTC) + timedelta(days=1)
                    for k in fertig
                ],
            }
    return None


def fortschreiben(
    journal: dict[str, Any], daten: dict[str, dict[str, list[Any]]], jetzt: datetime
) -> dict[str, Any]:
    """Je Coin die neu geschlossenen Tageskerzen durch ``kerze`` schicken."""
    positionen: dict[str, Any] = dict(journal.get("positionen") or {})
    zuletzt: dict[str, str] = dict(journal.get("zuletzt") or {})
    trades: list[dict[str, Any]] = list(journal.get("trades") or [])
    for coin, d in daten.items():
        ind = indikatoren(d)
        pos = Position(**positionen[coin]) if positionen.get(coin) else None
        grenze = (
            datetime.fromisoformat(zuletzt[coin])
            if coin in zuletzt
            else START - timedelta(seconds=1)
        )
        for i in range(ERSTE_KERZE, len(d["c"])):
            if d["schluss"][i] <= grenze:
                continue
            pos, fertig = kerze(d, ind, i, pos, coin)
            if fertig is not None:
                eintrag = asdict(fertig)
                eintrag["erfasst"] = jetzt.isoformat()
                trades.append(eintrag)
            zuletzt[coin] = d["schluss"][i].isoformat()
        positionen[coin] = asdict(pos) if pos is not None else None
    zu = [t for t in trades if t.get("grund") in ("stop", "zeit")]
    offen = {c: p for c, p in positionen.items() if p}
    return {
        "regel": "docs/TREND-SWING-STUDIE-2026-09.md",
        "hinweis": "nur Aufzeichnung — die Regel ist verworfen und erzeugt keine Alarme",
        "start": START.isoformat(),
        "stand": jetzt.isoformat(),
        "abgeschlossen": len(zu),
        "offen": len(offen),
        "summe_r": round(sum(t["r"] for t in zu), 2),
        "erwartung_r": round(sum(t["r"] for t in zu) / len(zu), 3) if zu else None,
        "erneut_pruefen": len(zu) >= ERNEUT_PRUEFEN_AB,
        "positionen": positionen,
        "zuletzt": zuletzt,
        "trades": trades,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bericht", action="store_true")
    args = ap.parse_args()
    journal = json.loads(JOURNAL.read_text(encoding="utf-8")) if JOURNAL.exists() else {}
    if args.bericht:
        print(
            json.dumps(
                {k: v for k, v in journal.items() if k not in ("trades", "positionen", "zuletzt")},
                indent=1,
            )
        )
        return 0
    daten: dict[str, dict[str, list[Any]]] = {}
    fehlt = []
    for coin in COINS:
        d = _hole(coin)
        if d is None:
            fehlt.append(coin)
            continue
        daten[coin] = d
    if len(fehlt) > len(COINS) // 2:
        print(
            f"::warning::Trend-Swing vorwaerts: {len(fehlt)} Coins ohne Daten — nichts geschrieben"
        )
        return 0
    # Ein fehlender Coin wird einfach beim naechsten Lauf nachgeholt: "zuletzt" bleibt stehen.
    neu = fortschreiben(journal, daten, datetime.now(UTC))
    if fehlt:
        neu["ohne_daten"] = fehlt
    JOURNAL.parent.mkdir(parents=True, exist_ok=True)
    JOURNAL.write_text(json.dumps(neu, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(
        f"Trend-Swing vorwaerts: {neu['abgeschlossen']} abgeschlossen, {neu['offen']} offen, "
        f"Summe {neu['summe_r']} R"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
