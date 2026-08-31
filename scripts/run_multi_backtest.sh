#!/usr/bin/env bash
# Läuft die 6 Crypto-Symbole EINZELN und PARALLEL durch den vollen Strategiepfad.
# Research-Modus (News-Gate aus) — Ergebnisse sind NICHT live-repräsentativ, News wird
# als 'not_checked' protokolliert, es werden KEINE News-Daten erfunden.
set -u
cd "$(dirname "$0")/.."
OUT=data/repository_real/bt_multi
mkdir -p "$OUT"
START=${START:-2023-08-01}
END=${END:-2025-06-30}
SYMS=${SYMS:-"BTCUSDT ETHUSDT SOLUSDT BNBUSDT XRPUSDT DOGEUSDT"}
NEWS=${NEWS:-off}

pids=()
for S in $SYMS; do
  ( .venv/bin/python scripts/run_backtest.py --repo data/repository_real \
      --symbols "$S" --start "$START" --end "$END" --news-gate "$NEWS" --json \
      > "$OUT/${S}.json" 2> "$OUT/${S}.err"
    echo "done $S rc=$?" ) &
  pids+=($!)
done

rc=0
for p in "${pids[@]}"; do wait "$p" || rc=1; done
echo "ALL DONE rc=$rc  ($(date -u +%FT%TZ))"
exit $rc
