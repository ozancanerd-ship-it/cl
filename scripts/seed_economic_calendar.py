#!/usr/bin/env python
"""Seed ``config/economic_calendar.csv`` mit **reproduzierbaren, historisch bekannten**
High-Impact-Terminen — für den Backtest-News-Gate ohne kostenpflichtigen Feed.

**Nur Termine, deren Datum ein Fakt ist:**

* **FOMC-Zinsentscheid** — die Fed veröffentlicht den Sitzungskalender ~1 Jahr im Voraus
  (hier fest hinterlegt, 2023–2026, Entscheid 18:00 UTC ≈ 14:00 ET).
* **US-NFP** — Bureau of Labor Statistics: **erster Freitag** im Monat, 08:30 ET (12:30/13:30
  UTC je nach Sommerzeit). Harte Regel, kein Rätselraten.

``actual`` bleibt **leer** — es wird nichts erfunden, nur der Termin (= Pre-Positioning-Ban /
Blackout). ``available_time`` = ``scheduled - 300 Tage`` (der Kalender war lange vorher bekannt;
konservativ Richtung „früher sichtbar", nie „später").

CPI / PCE / ECB brauchen einen echten Kalender-Feed — bewusst NICHT approximiert.

    uv run python scripts/seed_economic_calendar.py
"""

from __future__ import annotations

import argparse
import csv
from datetime import UTC, datetime, timedelta
from pathlib import Path

# FOMC-Zinsentscheide (2. Sitzungstag), 18:00 UTC. Quelle: federalreserve.gov Kalender.
_FOMC: list[str] = [
    "2023-02-01",
    "2023-03-22",
    "2023-05-03",
    "2023-06-14",
    "2023-07-26",
    "2023-09-20",
    "2023-11-01",
    "2023-12-13",
    "2024-01-31",
    "2024-03-20",
    "2024-05-01",
    "2024-06-12",
    "2024-07-31",
    "2024-09-18",
    "2024-11-07",
    "2024-12-18",
    "2025-01-29",
    "2025-03-19",
    "2025-05-07",
    "2025-06-18",
    "2025-07-30",
    "2025-09-17",
    "2025-10-29",
    "2025-12-10",
    "2026-01-28",
    "2026-03-18",
    "2026-04-29",
    "2026-06-17",
    "2026-07-29",
    "2026-09-16",
    "2026-10-28",
    "2026-12-09",
]

_LEAD = timedelta(days=300)
# von FOMC / USD-Makro betroffen: Gold, USD-Paare, Krypto-Majors (Risk-Asset-Kopplung)
_AFFECTED = "XAUUSD|XAUUSDT|EURUSD|GBPUSD|USDJPY|BTCUSDT|ETHUSDT"


def _first_friday(year: int, month: int) -> datetime:
    d = datetime(year, month, 1, tzinfo=UTC)
    # weekday(): Mon=0 … Fri=4
    return d + timedelta(days=(4 - d.weekday()) % 7)


def _nfp_dates(start_year: int, end_year: int) -> list[datetime]:
    out: list[datetime] = []
    for y in range(start_year, end_year + 1):
        for m in range(1, 13):
            ff = _first_friday(y, m)
            # 08:30 ET → 12:30 UTC (EST) bzw. 13:30 UTC (EDT). Konservativ 12:30 UTC.
            out.append(ff.replace(hour=12, minute=30))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="config/economic_calendar.csv")
    ap.add_argument("--start-year", type=int, default=2023)
    ap.add_argument("--end-year", type=int, default=2026)
    ap.add_argument(
        "--ingest-repo",
        default=None,
        help="zusätzlich in den News-Store dieses Repos schreiben (z. B. data/repository_real)",
    )
    args = ap.parse_args()

    rows: list[dict[str, str]] = []
    for iso in _FOMC:
        y = int(iso[:4])
        if not (args.start_year <= y <= args.end_year):
            continue
        sched = datetime.fromisoformat(iso).replace(hour=18, minute=0, tzinfo=UTC)
        rows.append(
            {
                "event_id": f"FOMC_RATE:{iso}",
                "event_type": "FOMC_RATE",
                "impact": "high",
                "scheduled_time": sched.isoformat(),
                "available_time": (sched - _LEAD).isoformat(),
                "actual": "",
                "forecast": "",
                "previous": "",
                "affected_symbols": _AFFECTED,
            }
        )
    for dt in _nfp_dates(args.start_year, args.end_year):
        rows.append(
            {
                "event_id": f"US_NFP:{dt.date().isoformat()}",
                "event_type": "US_NFP",
                "impact": "high",
                "scheduled_time": dt.isoformat(),
                "available_time": (dt - _LEAD).isoformat(),
                "actual": "",
                "forecast": "",
                "previous": "",
                "affected_symbols": _AFFECTED,
            }
        )
    rows.sort(key=lambda r: r["scheduled_time"])

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    cols = [
        "event_id",
        "event_type",
        "impact",
        "scheduled_time",
        "available_time",
        "actual",
        "forecast",
        "previous",
        "affected_symbols",
    ]
    with out.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    print(
        f"→ {out}  ·  {len(rows)} Termine  "
        f"({sum(1 for r in rows if r['event_type'] == 'FOMC_RATE')} FOMC, "
        f"{sum(1 for r in rows if r['event_type'] == 'US_NFP')} NFP)  "
        f"{rows[0]['scheduled_time'][:10]} … {rows[-1]['scheduled_time'][:10]}"
    )

    if args.ingest_repo:
        from trading_agent.data.providers.news_calendar import CsvEconomicCalendar
        from trading_agent.data.repository import MarketDataRepository

        cal = CsvEconomicCalendar(str(out))
        evs = cal.get_calendar(
            datetime(args.start_year, 1, 1, tzinfo=UTC),
            datetime(args.end_year + 1, 1, 1, tzinfo=UTC),
        )
        n = MarketDataRepository(args.ingest_repo).write_news(evs)
        print(f"→ {n} News-Events in {args.ingest_repo} geschrieben")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
