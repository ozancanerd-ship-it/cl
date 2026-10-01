"""Die Bilanz-Karte: was zuerst steht, nach welcher Regel, und woher ein Verlust kommt.

WARUM ES DIESEN TEST GIBT

02.10., Ozans Screenshot der Bilanz: rote Karte, erster Satz „Über 39 abgeschlossene
Signale hat das System 2,8 R verloren". Was er tatsächlich bekommen hatte — die Alarme
aufs Handy — lag im Plus; die Coins lagen im Plus; der ganze Verlust kam aus
Aktien-Käufen, die nie geklingelt haben. Dazu:

* oben „alles oder nichts" (−2,8 R), darunter die Chips nach Plan (zusammen −4,3 R) —
  zwei Regeln auf einer Karte, die Zahlen ließen sich nicht zusammenzählen;
* „Welche Regel besser ist, klärt die Signal-Studie (X0 gegen X1)" — Fachjargon;
* „1 Trades liefen … sie stehen … zählen";
* „zu 74 % aus krypto".

Festgehalten wird:

1. Zuerst stehen die Alarme aufs Handy, dann alle Signale — beide nach Plan.
2. Die Herkunft je Klasse steht im Satz, mit Zahlen; Summe der Klassen = Gesamt.
3. Einzahl und Mehrzahl stimmen, Klassen heißen wie in der App.
4. Kein „X0 gegen X1".
"""

from __future__ import annotations

from typing import Any

from trading_agent.scanner.performance import bericht


def _w(klasse: str, zustand: str, erreicht: list[str] | None = None, **kw: Any) -> dict:
    basis = {
        "instrument": f"T{klasse}",
        "klasse": klasse,
        "note": "A",
        "zustand": zustand,
        "erreicht": erreicht or [],
        "bestes_r": 0.5,
        "schlechtestes_r": -1.0,
        "zuletzt": "2026-09-01T00:00:00+00:00",
        "einstieg": 100.0,
        "stop": 95.0,
        "richtung": "long",
        "einstiegskurs": 100.0,
    }
    basis.update(kw)
    return basis


def _fall() -> dict[str, Any]:
    wachen = (
        [_w("krypto", "ziel_erreicht", ["TP1", "TP2", "TP3"], gemeldet="x")] * 2
        + [_w("krypto", "stop")] * 3
        + [_w("aktien", "stop")] * 4
    )
    return {"wachen": {f"W{i}": w for i, w in enumerate(wachen)}}


def test_alarme_stehen_zuerst_und_alle_signale_nach_plan() -> None:
    b = bericht(_fall())
    assert b.saetze[0].startswith("Deine Alarme (aufs Handy): 2 Trades")
    assert "Alle 9 beobachteten Signale" in b.saetze[1]
    assert "nach Plan" in b.saetze[1]


def test_herkunft_je_klasse_steht_im_satz_und_zaehlt_sich_zusammen() -> None:
    b = bericht(_fall())
    text = " ".join(b.saetze)
    assert "Der Verlust kommt aus Aktien (-4,0 R aus 4)" in text
    assert "Coins (" in text and "liegen im Plus" in text
    plan = b.je_klasse_plan
    assert abs(sum(k.summe_r for k in plan.values()) - b.je_regel["drittel"].summe_r) < 1e-9


def test_kein_fachjargon_und_klassen_wie_in_der_app() -> None:
    text = " ".join(bericht(_fall()).saetze)
    assert "X0" not in text and "X1" not in text
    assert "krypto" not in text


def test_einzahl_bei_einem_trade_ohne_ergebnis() -> None:
    wachen = [
        _w("krypto", "stop"),
        _w("aktien", "abgelaufen", ["TP1"], instrument="COP"),
        _w("krypto", "abgelaufen", einstiegskurs=None),
    ]
    b = bericht({"wachen": {f"W{i}": w for i, w in enumerate(wachen)}})
    text = " ".join(b.saetze)
    assert "1 Trade lief noch" in text and "1 Trades" not in text
    assert "1 Setup hat seinen Einstieg nie erreicht" in text
