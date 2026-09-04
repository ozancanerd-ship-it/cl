#!/bin/bash
# Breites Symbol-Panel ingestieren (Binance Vision Bulk, M5 → M15/H4/D1).
#
# WARUM: Der Methoden-Audit (docs/INDEPENDENT-METHOD-AUDIT-2026-09-03.md) zeigt, dass S9
# ~124 unabhaengige OOS-Trades braucht und aktuell 34 hat. Mit 7 Symbolen dauert das Jahre.
# Statistische Power kommt hier ueber BREITE, nicht ueber Wartezeit.
#
# Zwei Gruppen:
#   HOLDINGS  — was tatsaechlich im Portfolio liegt (Position-Intelligence braucht die Kurse)
#   PANEL     — liquide Majors fuer die Strategie-Validierung
#
#   bash scripts/ingest_panel.sh              # alles
#   GROUP=holdings bash scripts/ingest_panel.sh
#
# Dauer grob 2 min je Symbol beim ersten Lauf, danach gecacht und schnell.
# Abbruch/Neustart ist unkritisch.

set -u
cd "$(dirname "$0")/.."

HOLDINGS="SEIUSDT INJUSDT OPUSDT FETUSDT RENDERUSDT JUPUSDT ARBUSDT TAOUSDT WIFUSDT PEPEUSDT"
PANEL="LINKUSDT ADAUSDT AVAXUSDT LTCUSDT DOTUSDT ATOMUSDT NEARUSDT APTUSDT SUIUSDT TRXUSDT UNIUSDT AAVEUSDT FILUSDT TIAUSDT"

case "${GROUP:-all}" in
  holdings) SYMBOLS="$HOLDINGS" ;;
  panel)    SYMBOLS="$PANEL" ;;
  *)        SYMBOLS="$HOLDINGS $PANEL" ;;
esac

START="${START:-2023-01-01}"
END="${END:-2026-08-01}"   # exklusiv; Binance Vision veroeffentlicht Monatsdateien mit Verzoegerung

PY=".venv/bin/python"
[ -x "$PY" ] || PY="python3"

ok=0; fail=0
for sym in $SYMBOLS; do
  echo "=== $sym  $(date +%H:%M:%S) ==="
  if "$PY" scripts/ingest_binance_vision.py \
       --symbols "$sym" --start "$START" --end "$END" \
       --repo data/repository_real --cache data/cache/binance_vision \
       > "/tmp/ingest_${sym}.log" 2>&1; then
    ok=$((ok+1)); echo "    OK"
  else
    # Exit != 0 heisst hier meist nur: letzte Monatsdatei noch nicht veroeffentlicht.
    # Die Daten bis dahin sind trotzdem geschrieben. Luecken stehen im Log.
    fail=$((fail+1))
    echo "    unvollstaendig (Details: /tmp/ingest_${sym}.log)"
    grep -m3 -E '"missing"|missing_hours|bars_m5' "/tmp/ingest_${sym}.log" 2>/dev/null | head -3
  fi
done

echo
echo "=== fertig: $ok vollstaendig, $fail unvollstaendig ==="
echo "Instrumente im Repo:"
ls data/repository_real/ohlcv | sed 's/instrument=//' | sort | tr '\n' ' '
echo
