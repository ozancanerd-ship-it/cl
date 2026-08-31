#!/bin/bash
# Vollständige Dukascopy-Spot-Historie (Gold + FX) monatsweise ingestieren.
#
# WARUM MONATSWEISE: ein einzelner grosser Ingest-Lauf (Fetch + .bi5-Dekodierung von >6000
# Stunden-Dateien) laeuft in manchen Umgebungen laenger als das Prozess-Zeitlimit und wird
# gekillt. Ein Monat (~530 Dateien) passt immer. Der Repo-Merge (_merge_ohlcv) kombiniert die
# Monate; H4/D1 werden am Ende aus der vollen M5-Reihe neu abgeleitet (require_complete=False,
# Gold-Futures-Pause).
#
# Auf einem Rechner OHNE Prozess-Zeitlimit einfach am Stueck laufen lassen:
#   bash scripts/ingest_dukascopy_full.sh
# Dauer grob: ~3-10 min je nicht gecachtem Monat (503-Retries), Gesamt ~4-8 h fuer 3 Jahre.
# Abbruch/Neustart ist unkritisch — gecachte Stunden werden uebersprungen.

set -u
cd "$(dirname "$0")/.."

SYMBOLS="${SYMBOLS:-XAUUSD EURUSD GBPUSD USDJPY}"
START="${START:-2023-01-01}"
END="${END:-2026-08-01}"   # exklusiv; letzter voller Monat davor

python3 - "$START" "$END" <<'PY' | while read -r s e; do
import sys, datetime
d = datetime.date.fromisoformat(sys.argv[1]).replace(day=1)
end = datetime.date.fromisoformat(sys.argv[2]).replace(day=1)
while d < end:
    nd = (d.replace(day=28) + datetime.timedelta(days=8)).replace(day=1)
    print(d.isoformat(), nd.isoformat())
    d = nd
PY
  for sym in $SYMBOLS; do
    echo "=== $sym $s  $(date +%H:%M:%S) ==="
    uv run python scripts/ingest_dukascopy.py --symbols "$sym" \
      --start "$s" --end "$e" \
      --request-delay 0.1 --max-retries 4 --retry-backoff 2.0 \
      --repo data/repository_real 2>&1 | grep -E '"bars_m5"|missing_hours' | head -2
  done
done

echo "=== H4/D1 aus voller M5-Reihe neu ableiten ==="
uv run python - <<'PY'
from datetime import datetime, UTC
from trading_agent.core.enums import Timeframe
from trading_agent.data.repository import MarketDataRepository
from trading_agent.data.resample import resample_ohlcv

repo = MarketDataRepository("data/repository_real")
for sym in "XAUUSD EURUSD GBPUSD USDJPY".split():
    try:
        m5 = repo.read_ohlcv(sym, Timeframe.M5, datetime(2020, 1, 1, tzinfo=UTC), datetime(2030, 1, 1, tzinfo=UTC))
    except Exception:
        continue
    if not m5:
        continue
    for tf in (Timeframe.M15, Timeframe.H4, Timeframe.D1):
        rb = resample_ohlcv(m5, Timeframe.M5, tf, require_complete=False, source_name="dukascopy_m5")
        if rb:
            repo.write_ohlcv(rb)
    print(f"{sym}: M5={len(m5)}")
PY

echo "FERTIG — danach:  uv run python scripts/setup_research.py --symbols XAUUSD EURUSD GBPUSD USDJPY BTCUSDT ETHUSDT SOLUSDT BNBUSDT XRPUSDT DOGEUSDT --split 2025-01-01 --manage scaled --cost-r 0.06"
