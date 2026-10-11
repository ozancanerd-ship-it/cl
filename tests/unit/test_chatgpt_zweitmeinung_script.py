"""ChatGPT-Meinung darf zwischen den vollen Laeufen nicht von der Seite verschwinden.

WARUM ES DIESEN TEST GIBT

Ozan, 09.10. 12:22/12:24: "integrier chatgpt mehr in der app ... deine und seine will
ich sehen", dann "nirgendwo auf meinem handy steht was mit chat". Ursache: der OpenAI-
Aufruf lief nur beim VOLLEN Lauf (Kosten, braucht den kompletten Scan), aber
``web/chatgpt_meinung.json`` ist in keinem Workflow-Lauf gecacht — jeder 10-Minuten-
"krypto"-Lauf baute die Seite aus einem frischen Checkout OHNE diese Datei und loeschte
sie damit von der Live-Seite, bis Stunden spaeter der naechste volle Lauf kam. In der
Zwischenzeit sah die App aus, als gaebe es gar keine ChatGPT-Anbindung.

Jetzt laeuft ``scripts/chatgpt_zweitmeinung.py`` bei JEDEM Lauf, der auch die Seite baut.
Beim vollen Lauf fragt es OpenAI frisch; sonst uebernimmt ``--vorlauf-url`` die zuletzt
veroeffentlichte Antwort unveraendert — kein neuer OpenAI-Aufruf, keine Kosten, aber die
Meinung bleibt durchgehend sichtbar statt alle paar Minuten zu verschwinden.

Festgehalten wird:

1. Mit ``--vorlauf-url`` und einer erreichbaren vorherigen Antwort wird GENAU diese
   Antwort (Text, je_position, je_chance, erzeugt) unveraendert uebernommen — nur
   ``geprueft`` wird aktualisiert, damit die App sieht, dass der Lauf durchgelaufen ist.
2. Ohne erreichbare vorherige Antwort (z. B. vor dem allerersten vollen Lauf) bleibt es
   ehrlich leer (``aktiv: false``) statt etwas zu erfinden.
3. ``--vorlauf-url`` ruft NIE die OpenAI-API auf — das ist der ganze Sinn des Carry-
   Forward-Laufs (kein Geld fuer einen 10-Minuten-Takt ausgeben).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import chatgpt_zweitmeinung as skript

from trading_agent.ops.zweitmeinung import Zweitmeinung


def test_voller_lauf_schreibt_die_urteils_abzeichen_mit(monkeypatch, tmp_path):
    """Ozan, 11.10. 17:18: "ihr solltet zusammenarbeiten" — das Abzeichen je Schluessel
    muss auch durch DIESES Skript (nicht nur durch zweitmeinung.py selbst) tatsaechlich
    in die veroeffentlichte Datei kommen, sonst sieht die App nie etwas davon."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(skript, "verfuegbar", lambda: True)
    monkeypatch.setattr(skript, "depot_lesen", lambda roh: [])
    monkeypatch.setattr(skript, "sync_lesen", lambda pfad, geheim: "")

    scan = tmp_path / "scan.json"
    scan.write_text(
        json.dumps(
            {
                "gesamt": [
                    {
                        "instrument": "NVDA",
                        "name": "Nvidia",
                        "klasse": "aktien",
                        "score": 88,
                        "note": "A",
                        "richtung": "long",
                        "begruendung": "Score 88, starker Trend",
                        "handelbar": True,
                        "alarm": {"ja": True},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    antwort = Zweitmeinung(
        text="Gesamt.",
        modell="gpt-4o-mini",
        erzeugt="2026-10-11T17:00:00+00:00",
        je_chance={"NVDA": "Score plausibel."},
        urteil_chance={"NVDA": "stimmt_zu"},
    )
    monkeypatch.setattr(skript, "hole_zweitmeinung", lambda *a, **k: antwort)

    out = tmp_path / "chatgpt_meinung.json"
    monkeypatch.setattr(
        sys, "argv", ["prog", "--scan", str(scan), "--out", str(out)]
    )
    rc = skript.main()
    assert rc == 0

    geschrieben = json.loads(out.read_text(encoding="utf-8"))
    assert geschrieben["je_chance"] == {"NVDA": "Score plausibel."}
    assert geschrieben["urteil_chance"] == {"NVDA": "stimmt_zu"}


def test_vorlauf_uebernimmt_die_letzte_veroeffentlichte_antwort_unveraendert(monkeypatch, tmp_path):
    alte_antwort = {
        "aktiv": True,
        "geprueft": "2026-10-09T09:00:00+00:00",
        "erzeugt": "2026-10-09T09:00:05+00:00",
        "modell": "gpt-4o-mini",
        "text": "Alte Gesamteinschaetzung.",
        "je_position": {"METUSD": "Alte Meinung zu MET."},
        "je_chance": {},
        "bezieht_sich_auf": {"positionen": 1, "chancen": 0},
    }
    monkeypatch.setattr(skript, "vorlauf_lesen", lambda url: dict(alte_antwort))

    out = tmp_path / "chatgpt_meinung.json"
    monkeypatch.setattr(
        sys, "argv", ["prog", "--out", str(out), "--vorlauf-url", "https://example.invalid"]
    )
    rc = skript.main()
    assert rc == 0

    geschrieben = json.loads(out.read_text(encoding="utf-8"))
    assert geschrieben["text"] == "Alte Gesamteinschaetzung."
    assert geschrieben["je_position"] == {"METUSD": "Alte Meinung zu MET."}
    assert geschrieben["erzeugt"] == "2026-10-09T09:00:05+00:00"  # unveraendert
    assert geschrieben["geprueft"] != "2026-10-09T09:00:00+00:00"  # aktualisiert


def test_vorlauf_mit_altem_format_ohne_urteil_felder_stuerzt_nicht_ab(monkeypatch, tmp_path):
    """Eine vor dem 11.10. veroeffentlichte Antwort hat keine urteil_position/
    urteil_chance-Felder — das darf den Carry-Forward-Lauf nicht zum Absturz bringen,
    es wird einfach unveraendert durchgereicht (die App liefert ``.get(...) || {}``
    dagegen und zeigt dann schlicht kein Abzeichen)."""
    alte_antwort_ohne_urteil = {
        "aktiv": True,
        "geprueft": "2026-10-09T09:00:00+00:00",
        "erzeugt": "2026-10-09T09:00:05+00:00",
        "modell": "gpt-4o-mini",
        "text": "Alte Gesamteinschaetzung, noch ohne Abzeichen.",
        "je_position": {"METUSD": "Alte Meinung zu MET."},
        "je_chance": {},
        "bezieht_sich_auf": {"positionen": 1, "chancen": 0},
    }
    monkeypatch.setattr(skript, "vorlauf_lesen", lambda url: dict(alte_antwort_ohne_urteil))

    out = tmp_path / "chatgpt_meinung.json"
    monkeypatch.setattr(
        sys, "argv", ["prog", "--out", str(out), "--vorlauf-url", "https://example.invalid"]
    )
    rc = skript.main()
    assert rc == 0

    geschrieben = json.loads(out.read_text(encoding="utf-8"))
    assert geschrieben["je_position"] == {"METUSD": "Alte Meinung zu MET."}
    assert "urteil_position" not in geschrieben


def test_vorlauf_ohne_vorherige_antwort_bleibt_ehrlich_leer(monkeypatch, tmp_path):
    monkeypatch.setattr(skript, "vorlauf_lesen", lambda url: None)

    out = tmp_path / "chatgpt_meinung.json"
    monkeypatch.setattr(
        sys, "argv", ["prog", "--out", str(out), "--vorlauf-url", "https://example.invalid"]
    )
    rc = skript.main()
    assert rc == 0

    geschrieben = json.loads(out.read_text(encoding="utf-8"))
    assert geschrieben["aktiv"] is False


def test_vorlauf_ruft_nie_openai_auf(monkeypatch, tmp_path):
    aufgerufen = []
    monkeypatch.setattr(skript, "vorlauf_lesen", lambda url: {"aktiv": False})
    monkeypatch.setattr(skript, "hole_zweitmeinung", lambda *a, **k: aufgerufen.append(1))
    monkeypatch.setattr(skript, "verfuegbar", lambda: (_ for _ in ()).throw(AssertionError("sollte nicht gerufen werden")))

    out = tmp_path / "chatgpt_meinung.json"
    monkeypatch.setattr(
        sys, "argv", ["prog", "--out", str(out), "--vorlauf-url", "https://example.invalid"]
    )
    skript.main()
    assert not aufgerufen
