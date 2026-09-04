#!/bin/bash
# Taegliche TSMOM-Forward-Aufzeichnung als launchd-Job einrichten (macOS).
#
# WARUM: Forward-Daten sind die einzigen unkontaminierten Daten, die noch entstehen
# (docs/TSMOM-MULTIASSET-ERGEBNIS-2026-09-04.md). Sie entstehen aber nur, wenn jeden Tag
# aufgezeichnet wird — und daran denkt niemand freiwillig 100 Tage lang.
#
# Der Job ruft ops/daily_run.sh: erst aufzeichnen, dann den Tagesplan per Telegram schicken.
# Er schreibt auf und benachrichtigt. Er handelt nicht und legt keine Order.
#
# ACHTUNG — NICHT ZUSAETZLICH ZU GITHUB ACTIONS LAUFEN LASSEN.
# .github/workflows/daily.yml macht dasselbe in der Cloud. Beides gleichzeitig heisst:
# zwei Telegram-Nachrichten am Tag und zwei Schreiber auf derselben Journaldatei, die sich
# beim naechsten Pull gegenseitig ueberschreiben. Das ist eine Entweder-oder-Entscheidung.
#
#   GitHub Actions  -> laeuft auch, wenn der Mac aus ist. Der Normalfall.
#   Dieser launchd-Job -> nur, wenn kein GitHub gewuenscht ist (dann Actions abschalten).
#
#   bash ops/install_forward_collector.sh          # einrichten
#   bash ops/install_forward_collector.sh --status # Stand ansehen
#   bash ops/install_forward_collector.sh --remove # wieder entfernen

set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
LABEL="com.ozan.trading-agent.tsmom-forward"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOGDIR="$REPO/data/repository_real/live/forward_logs"

case "${1:-install}" in
  --status)
    echo "Job:     $LABEL"
    launchctl list | grep -q "$LABEL" && echo "Status:  geladen" || echo "Status:  NICHT geladen"
    echo "Plist:   $PLIST"
    J="$REPO/data/repository_real/live/tsmom_forward.jsonl"
    if [ -f "$J" ]; then
      echo "Journal: $(wc -l < "$J" | tr -d ' ') Tage aufgezeichnet"
      echo "         zuletzt $(tail -1 "$J" | python3 -c 'import json,sys; print(json.load(sys.stdin)["date"])' 2>/dev/null || echo '?')"
    else
      echo "Journal: noch leer"
    fi
    [ -d "$LOGDIR" ] && echo "Logs:    $LOGDIR" && ls -t "$LOGDIR" 2>/dev/null | head -3
    exit 0
    ;;
  --remove)
    launchctl unload "$PLIST" 2>/dev/null || true
    rm -f "$PLIST"
    echo "entfernt. Das Journal bleibt erhalten."
    exit 0
    ;;
esac

PY="$REPO/.venv/bin/python"
[ -x "$PY" ] || { echo "FEHLER: $PY fehlt. Erst 'make install' ausfuehren."; exit 1; }

mkdir -p "$HOME/Library/LaunchAgents" "$LOGDIR"

# 23:10 Ortszeit: nach US-Boersenschluss (22:00 MEZ / 22:00 MESZ), vor Mitternacht.
cat > "$PLIST" <<PLISTEOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>$REPO/ops/daily_run.sh</string>
  </array>
  <key>WorkingDirectory</key><string>$REPO</string>
  <key>EnvironmentVariables</key>
  <dict><key>PYTHONPATH</key><string>$REPO/src</string></dict>
  <key>StartCalendarInterval</key>
  <dict><key>Hour</key><integer>23</integer><key>Minute</key><integer>10</integer></dict>
  <!-- Holt den Lauf nach, wenn der Mac um 23:10 aus war. Genau dafuer ist das da. -->
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>$LOGDIR/forward.log</string>
  <key>StandardErrorPath</key><string>$LOGDIR/forward.err</string>
</dict>
</plist>
PLISTEOF

launchctl unload "$PLIST" 2>/dev/null || true
launchctl load "$PLIST"

echo "Eingerichtet: $LABEL"
echo "  laeuft taeglich 23:10 Ortszeit, holt verpasste Laeufe beim naechsten Anmelden nach"
echo "  Journal: data/repository_real/live/tsmom_forward.jsonl"
echo "  Logs:    $LOGDIR"
echo
echo "Stand ansehen:  bash ops/install_forward_collector.sh --status"
echo "Wieder weg:     bash ops/install_forward_collector.sh --remove"
