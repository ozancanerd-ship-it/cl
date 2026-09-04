#!/usr/bin/env python3
"""Repository auf veraltete Datenreihen pruefen (Befund F12).

    python3 scripts/check_data_freshness.py
    python3 scripts/check_data_freshness.py --timeframe D1 --strict

``--strict`` beendet mit Exit-Code 1, sobald eine Reihe veraltet ist — fuer CI und
fuer den Aufruf vor einem Research-Lauf.
"""

from __future__ import annotations

import argparse
import sys

from trading_agent.data.freshness import format_report, scan_repository


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--profile",
        choices=("research", "live"),
        default="research",
        help="live: strengere Schwellen fuer den taeglichen Lauf (Krypto max. 3 Tage alt)",
    )
    ap.add_argument("--repo", default="data/repository_real")
    ap.add_argument("--timeframe", default="H4")
    ap.add_argument("--strict", action="store_true", help="Exit 1, wenn etwas veraltet ist")
    args = ap.parse_args()

    ages = scan_repository(args.repo, profile=args.profile, timeframe=args.timeframe)
    if not ages:
        print(f"keine Reihen unter {args.repo} (timeframe={args.timeframe})")
        return 1
    print(format_report(ages))
    stale = [a for a in ages if a.stale]
    if stale:
        print()
        print("Nachladen:")
        crypto = [a.instrument for a in stale if a.instrument.endswith("USDT")]
        if crypto:
            print(f"  bash scripts/ingest_panel.sh    # bzw. gezielt: {' '.join(crypto[:6])}")
        if any(a.instrument.endswith("-YF") for a in stale):
            print("  python scripts/ingest_yahoo.py --symbols ...")
    return 1 if (args.strict and stale) else 0


if __name__ == "__main__":
    sys.exit(main())
