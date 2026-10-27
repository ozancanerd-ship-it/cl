"""Konsistenz der Research-Ablage: keine Erkenntnis ohne belegte Quelle."""
import json
from pathlib import Path

D = Path(__file__).resolve().parents[2] / "docs" / "research"
PFLICHT_Q = {"id", "autoren", "titel", "jahr", "ort", "url", "verifikation", "typ"}
PFLICHT_E = {"id", "prio", "thema", "quelle", "stelle", "kern", "codebezug", "status", "test"}


def _laden():
    q = json.loads((D / "quellen.json").read_text(encoding="utf-8"))
    e = json.loads((D / "erkenntnisse.json").read_text(encoding="utf-8"))
    return q, e


def test_quellen_vollstaendig():
    q, _ = _laden()
    ids = [s["id"] for s in q["quellen"]]
    assert len(ids) == len(set(ids))
    for s in q["quellen"]:
        assert PFLICHT_Q <= s.keys(), s["id"]
        assert s["url"].startswith("https://")
        assert s["verifikation"] == "abruf"


def test_erkenntnisse_verweisen_auf_vorhandene_quellen():
    q, e = _laden()
    ids = {s["id"] for s in q["quellen"]}
    eids = [x["id"] for x in e["erkenntnisse"]]
    assert len(eids) == len(set(eids))
    for x in e["erkenntnisse"]:
        assert PFLICHT_E <= x.keys(), x["id"]
        assert x["quelle"] in ids, x["id"]
        assert x["kern"].strip() and len(x["kern"]) < 400  # eigene Paraphrase, kein Volltext


def test_unverifiziertes_nicht_als_quelle():
    q, _ = _laden()
    genutzt = " ".join(s["titel"] for s in q["quellen"])
    assert "Stop-Loss Rules Stop Losses" not in genutzt
