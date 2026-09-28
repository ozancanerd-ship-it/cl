"""Anzeige im Depot: Umlaute und Teilverkaufs-Regeln, die nur einmal gelten.

WARUM ES DIESEN TEST GIBT

Ozan, 28.09.: „Ich soll bei mehreren Sachen schon Teil verkaufen, obwohl du selber
meintest, das wäre nicht so sinnvoll … und die Analysen gefallen mir nicht."

Zwei Dinge sind dabei aufgefallen, die dieser Test festhält:

1. Nach einem Teilverkauf („Puffer wird eng — Hälfte raus") kam derselbe Rat auf die
   Restmenge sofort wieder, bis nichts mehr übrig war. Jetzt hakt der „Verkauft"-Knopf
   die Regel in ``pos.erledigt`` ab. Diese Marken dürfen aber den Ausstiegsplan nicht
   beenden: ``naechsterSchritt`` zählte vorher JEDEN Eintrag in ``erledigt`` als erreichtes
   Ziel und meldete dann „Alle Ziele erreicht — es gibt keinen Plan mehr".
2. Analysetexte kamen mit ae/oe/ue auf den Schirm („laeuft ueber", „Boersenzeit").
   ``mitUmlauten`` repariert das beim Anzeigen — über eine Liste von Wortstämmen, damit
   „aktuell", „neue Quelle" oder „Euro" nicht beschädigt werden.

Geprüft wird der echte Code aus ``site/template.html``, ausgeführt in Node.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

VORLAGE = Path(__file__).resolve().parents[2] / "site" / "template.html"


def _node() -> str:
    node = shutil.which("node")
    if not node:  # pragma: no cover - nur auf Rechnern ohne Node
        pytest.skip("node nicht vorhanden")
    return node


def _funktion(roh: str, name: str) -> str:
    start = roh.index(f"function {name}(")
    tiefe, i = 0, roh.index("{", start)
    anfang = i
    while True:
        if roh[i] == "{":
            tiefe += 1
        elif roh[i] == "}":
            tiefe -= 1
            if tiefe == 0:
                break
        i += 1
    return roh[start:anfang] + roh[anfang : i + 1]


def _umlaut_block() -> str:
    roh = VORLAGE.read_text(encoding="utf-8")
    return roh[roh.index("const UMLAUT_PAARE") : roh.index("const UMLAUT_NICHT")]


def _laufen(skript: str, eingabe: object) -> object:
    aus = subprocess.run(
        [_node(), "-e", skript, json.dumps(eingabe)],
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    return json.loads(aus.stdout)


def test_umlaute_werden_repariert() -> None:
    faelle = {
        "Der Kurs laeuft ueber der Rueckeroberung": "Der Kurs läuft über der Rückeroberung",
        "ausserhalb der Boersenzeit normal": "außerhalb der Börsenzeit normal",
        "Faellt er darunter, ist die Idee hinfaellig": "Fällt er darunter, ist die Idee hinfällig",
        "fuer dich, dafuer nicht, Fuer alle": "für dich, dafür nicht, Für alle",
        "Liquiditaetsgriff im Abwaertstrend": "Liquiditätsgriff im Abwärtstrend",
        "Ueber den Basiswert — schliesst hoechstens": "Über den Basiswert — schließt höchstens",
    }
    skript = (
        _umlaut_block()
        + "\nconst e = JSON.parse(process.argv[1]);"
        + "process.stdout.write(JSON.stringify(e.map(mitUmlauten)));"
    )
    aus = _laufen(skript, list(faelle))
    assert aus == list(faelle.values())


def test_richtige_woerter_bleiben_unberuehrt() -> None:
    """Die Stammliste darf nichts kaputt machen, was schon richtig geschrieben ist."""
    heil = [
        "aktuell, manuell, eventuell",
        "neue Quelle, Steuer, teuer, Euro, Feuer, Dauer",
        "zuerst, Issue, Queue, Daemon, true, value",
        "BTCUSD, SUEDZUCKER, QUEENS",
        "Schluss, Ausstieg, Masse, Klasse, Risiko",
        "läuft über die Brücke — schon mit Umlaut",
    ]
    skript = (
        _umlaut_block()
        + "\nconst e = JSON.parse(process.argv[1]);"
        + "process.stdout.write(JSON.stringify(e.map(mitUmlauten)));"
    )
    assert _laufen(skript, heil) == heil


def test_umlaute_sind_idempotent() -> None:
    skript = (
        _umlaut_block()
        + "\nconst e = JSON.parse(process.argv[1]);"
        + "const a = mitUmlauten(e); process.stdout.write(JSON.stringify([a, mitUmlauten(a)]));"
    )
    a, b = _laufen(skript, "laeuft ueber, faellt zurueck, ausserhalb")
    assert a == b


def _zahlen_block() -> str:
    roh = VORLAGE.read_text(encoding="utf-8")
    return roh[roh.index("const DEZIMAL_RE") : roh.index("function anzeigeText")]


def test_dezimalpunkte_werden_zu_kommas() -> None:
    faelle = {
        "Einstieg bei 529.4 — Stop bei 540": "Einstieg bei 529,4 — Stop bei 540",
        "CRV 1:3.98, erwartete Bewegung 8.0 %": "CRV 1:3,98, erwartete Bewegung 8,0 %",
        "1 R = 10.61 (2.0 % vom Einstieg)": "1 R = 10,61 (2,0 % vom Einstieg)",
        "Vol 1.5× · Kurs 0.00012": "Vol 1,5× · Kurs 0,00012",
        # bleibt: deutsche Tausender, Daten, Versionen, schon deutsche Zahlen
        "Depotwert 10.020 € am 28.09. (v1.2)": "Depotwert 10.020 € am 28.09. (v1.2)",
        "Stop 76.151,92 $ · 28.09.2026 · 1.234.567": "Stop 76.151,92 $ · 28.09.2026 · 1.234.567",
    }
    skript = (
        _zahlen_block()
        + "\nconst e = JSON.parse(process.argv[1]);"
        + "process.stdout.write(JSON.stringify(e.map(deZahlen)));"
    )
    assert _laufen(skript, list(faelle)) == list(faelle.values())


def test_abgehakte_teilverkaufsregel_beendet_den_plan_nicht() -> None:
    """``erledigt`` enthält seit 28.09. auch ko_eng/gegen — das sind keine Ziele."""
    roh = VORLAGE.read_text(encoding="utf-8")
    teile = [
        "const digits = v => 2;",
        "const num = (v, d) => Number(v).toFixed(2);",
        _funktion(roh, "waehrungsZeichen"),
        _funktion(roh, "zieleGelten"),
        _funktion(roh, "naechsterSchritt"),
    ]
    skript = (
        "\n".join(teile)
        + "\nconst e = JSON.parse(process.argv[1]);"
        + "process.stdout.write(JSON.stringify(e.map(p => naechsterSchritt(p, 100, 'USD').stufe)));"
    )
    plan = {"stop": 90, "art": "halte", "quelle": "halte"}
    faelle = [
        {"sym": "X", "plan": plan},
        {"sym": "X", "plan": plan, "erledigt": ["ko_eng"]},
        {"sym": "X", "plan": plan, "erledigt": ["gegen"]},
        # Ein wirklich erreichtes Ziel beendet einen Plan ohne weitere Ziele weiterhin.
        {"sym": "X", "plan": plan, "erledigt": ["TP1"]},
    ]
    assert _laufen(skript, faelle) == ["halten", "halten", "halten", "fertig"]


def test_halten_text_nennt_die_waehrung() -> None:
    roh = VORLAGE.read_text(encoding="utf-8")
    teile = [
        "const digits = v => 2;",
        "const num = (v, d) => Number(v).toFixed(2);",
        _funktion(roh, "waehrungsZeichen"),
        _funktion(roh, "zieleGelten"),
        _funktion(roh, "naechsterSchritt"),
    ]
    skript = (
        "\n".join(teile)
        + "\nconst p = JSON.parse(process.argv[1]);"
        + "process.stdout.write(JSON.stringify(naechsterSchritt(p, 100, 'EUR').text));"
    )
    text = _laufen(skript, {"sym": "X", "plan": {"stop": 90, "quelle": "halte"}})
    assert "90.00 €" in text
    # Der Stop wird von der App nachgezogen — der Text darf das nicht Ozan aufladen.
    assert "zieht die App den Stop nach" in text
