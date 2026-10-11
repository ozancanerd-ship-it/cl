"""Der Lauf muss seinen Stand wirklich sichern — sonst vergisst das System, was es gemeldet hat.

WARUM ES DIESEN TEST GIBT

11.10. abends, beim Pruefen der neuen Gold-S9-Alarme: in 6 von 30 Laeufen stand im Log
"Journalzeile konnte nicht gesichert werden". Ursache: ``actions/checkout`` holt ohne
``ref`` den Commit vom AUSLOESEN des Laufs. Wartet der Lauf hinter einem anderen in der
Sperre "scan", hat der vorige Lauf inzwischen neuen Stand gepusht — der wartende rechnet
auf altem Stand, sein Commit kollidiert beim Rebase und der komplette Stand des Laufs
(Wachliste, Alarm-Gedaechtnis, Gold-S9-Paper-Trade) war weg. Folge: Alarme doppelt oder
gar nicht, Paper-Trades ohne Ergebnis.

Festgehalten wird:

1. Der Scan-Job checkt ``main`` aus, nicht den Ausloese-SHA.
2. Bei einer Kollision gewinnt der Stand dieses Laufs (``-X theirs``) statt abzubrechen.
3. Der Gold-S9-Stand ist in der Liste der gesicherten Dateien.
"""

from __future__ import annotations

from pathlib import Path

import yaml

WORKFLOW = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "daily.yml"


def _schritte() -> list[dict]:
    doc = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    return doc["jobs"]["tagesplan"]["steps"]


def test_scan_job_checkt_main_aus() -> None:
    checkout = next(s for s in _schritte() if str(s.get("uses", "")).startswith("actions/checkout"))
    assert (checkout.get("with") or {}).get("ref") == "main"


def test_kollision_behaelt_den_stand_dieses_laufs() -> None:
    sichern = next(s for s in _schritte() if s.get("name") == "Journal sichern")
    assert "git pull --rebase -X theirs" in sichern["run"]
    assert "git rebase --abort" in sichern["run"]


def test_gold_s9_stand_wird_gesichert() -> None:
    sichern = next(s for s in _schritte() if s.get("name") == "Journal sichern")
    assert "data/repository_real/live/gold_s9_stand.json" in sichern["run"]


def test_daytrade_stand_wird_gesichert_und_alarme_senden() -> None:
    sichern = next(s for s in _schritte() if s.get("name") == "Journal sichern")
    assert "data/repository_real/live/daytrade_stand.json" in sichern["run"]
    dt = next(s for s in _schritte() if s.get("name") == "Tageshandel-Alarme")
    assert "--send" in dt["run"] and "VAPID_PRIVATE_KEY" in (dt.get("env") or {})


def test_gold_schritt_sendet_und_hat_die_kanaele() -> None:
    gold = next(s for s in _schritte() if s.get("name") == "Gold-Tageshandel-Stand")
    assert "--send" in gold["run"]
    env = gold.get("env") or {}
    for k in ("VAPID_PRIVATE_KEY", "PUSH_ABOS", "GITHUB_TOKEN", "TELEGRAM_BOT_TOKEN"):
        assert k in env
