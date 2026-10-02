"""Das Gedaechtnis der Bilanz: kein eingegangener Trade geht mehr verloren.

WARUM ES DIESEN TEST GIBT

02.10.: Die Wachliste ist nach Instrument geschluesselt. Kam ein Wert nach einem
abgeschlossenen Trade wieder auf die Liste, ueberschrieb die neue Wache den alten Trade;
``aufraeumen`` warf alles ueber 60 abgeschlossene Wachen weg. Nachgezaehlt aus der
Git-Historie: 355 eingegangene Trades seit 05.09., in der Bilanz standen 38. Zcash war
zweimal ausgestoppt — beide Male als Alarm aufs Handy — und beide Stops waren aus
„Deine Alarme" verschwunden, weil Zcash wieder auf die Liste kam.

Festgehalten wird:

1. Ueberschreiben und Aufraeumen legen den Trade ins Archiv (nur eingegangene).
2. Das Archiv haengt jeden Trade genau einmal an.
3. Bilanz und Alarm-Tor sehen Liste + Archiv, ohne Doppel.
4. Eine in ihrer Klasse bewaehrte Setup-Art klingelt trotz Sperre „Klasse + Richtung".
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from trading_agent.scanner import alarm_tor as at
from trading_agent.scanner.performance import bericht
from trading_agent.scanner.watchlist import (
    Wachliste,
    archiv_anhaengen,
    archiv_laden,
    mit_archiv,
)

T0 = datetime(2026, 9, 27, 12, 0, tzinfo=UTC)


def _zeile(instrument: str = "ZECUSD") -> dict[str, Any]:
    return {
        "instrument": instrument,
        "klasse": "krypto",
        "richtung": "long",
        "note": "A−",
        "handelbar": True,
        "einstieg": 100.0,
        "einstieg_art": "sofort",
        "invalidierung": 95.0,
        "ziel": 106.0,
        "tp2": 110.0,
        "tp3": 115.0,
        "score": 70.0,
        "rr": 3.0,
        "setup": {"name": "Rueckeroberung nach Liquiditaetsgriff"},
    }


def _ausgestoppt(liste: Wachliste, name: str, t: datetime) -> None:
    liste.pruefen({name: {"hoch": 100.2, "tief": 99.9, "letzter": 100.0}}, jetzt=t)
    liste.pruefen(
        {name: {"hoch": 100.0, "tief": 94.0, "letzter": 94.5}}, jetzt=t + timedelta(hours=1)
    )


def test_neuaufnahme_ueberschreibt_den_alten_trade_nicht_mehr() -> None:
    liste = Wachliste()
    liste.aufnehmen([_zeile()], jetzt=T0)
    _ausgestoppt(liste, "ZECUSD", T0 + timedelta(minutes=5))
    assert liste.wachen["ZECUSD"].zustand == "stop"
    liste.aufnehmen([_zeile()], jetzt=T0 + timedelta(days=2))
    assert liste.wachen["ZECUSD"].zustand == "wartet_auf_einstieg"
    assert len(liste.archiv_neu) == 1
    assert liste.archiv_neu[0]["zustand"] == "stop"
    assert liste.archiv_neu[0]["aufgenommen"] == T0.isoformat()


def test_aufraeumen_archiviert_eingegangene_und_vergisst_nie_ausgeloeste() -> None:
    liste = Wachliste()
    for i in range(3):
        liste.aufnehmen([_zeile(f"C{i}USD")], jetzt=T0)
        _ausgestoppt(liste, f"C{i}USD", T0 + timedelta(minutes=5 + i))
    # eine, die nie eingestiegen ist
    liste.aufnehmen([_zeile("NIEUSD")], jetzt=T0)
    liste.wachen["NIEUSD"].zustand = "abgelaufen"
    assert liste.aufraeumen(behalten=0) == 4
    assert sorted(d["instrument"] for d in liste.archiv_neu) == ["C0USD", "C1USD", "C2USD"]


def test_archiv_haengt_jeden_trade_nur_einmal_an(tmp_path: Path) -> None:
    lf = tmp_path / "archiv.jsonl"
    d = {"instrument": "ZECUSD", "aufgenommen": "x", "zustand": "stop", "einstiegskurs": 1.0}
    assert archiv_anhaengen([d], lf) == 1
    assert archiv_anhaengen([d], lf) == 0
    assert len(lf.read_text().splitlines()) == 1
    stamm = tmp_path / "stamm.json"
    stamm.write_text(json.dumps([d, {**d, "aufgenommen": "y"}]))
    assert len(archiv_laden(stamm, lf)) == 2


def _trade(name: str, aufgenommen: str, zustand: str, erreicht: list[str], **kw: Any) -> dict:
    return {
        "instrument": name,
        "klasse": "krypto",
        "richtung": "long",
        "note": "A−",
        "setup": "Rueckeroberung nach Liquiditaetsgriff",
        "einstieg": 100.0,
        "stop": 95.0,
        "einstiegskurs": 100.0,
        "zustand": zustand,
        "erreicht": erreicht,
        "aufgenommen": aufgenommen,
        "zuletzt": aufgenommen,
        "bestes_r": 0.5,
        "schlechtestes_r": -1.0,
        **kw,
    }


def test_bilanz_sieht_liste_und_archiv_ohne_doppel() -> None:
    jetzt = _trade("ZECUSD", "2026-10-01T05:00:00+00:00", "stop", [], gemeldet="x")
    frueher = _trade("ZECUSD", "2026-09-29T13:37:00+00:00", "stop", [], gemeldet="x")
    stand = {"wachen": {"ZECUSD": jetzt}}
    b = bericht(mit_archiv(stand, [frueher, jetzt]))
    assert b.abgeschlossen == 2  # der aktuelle zaehlt nicht doppelt
    assert b.je_meldung["aufs_handy"].anzahl == 2
    assert b.saetze[0].startswith("Deine Alarme (aufs Handy): 2 Trades")


def test_bewaehrte_setup_art_schlaegt_die_sperre_je_klasse_und_richtung() -> None:
    """Coins Long im Minus (alte, unbenannte Trades) — die benannte Art im Plus."""
    gut = [
        _trade(f"G{i}USD", f"2026-09-2{i}T00:00:00+00:00", "ziel_erreicht", ["TP1", "TP2", "TP3"])
        for i in range(4)
    ] + [_trade(f"S{i}USD", f"2026-09-2{i}T01:00:00+00:00", "stop", []) for i in range(2)]
    alt = [
        {**_trade(f"A{i}USD", f"2026-09-0{i}T00:00:00+00:00", "stop", []), "setup": ""}
        for i in range(9)
    ]
    stand = at.bilanz(gut + alt)
    assert stand["klasse_richtung:krypto|long"].urteil == "gesperrt"
    t = at.pruefe(
        note="A−",
        setup="Rueckeroberung nach Liquiditaetsgriff",
        klasse="krypto",
        richtung="long",
        crv=3.0,
        ziel1_pct=5.0,
        stand=stand,
    )
    assert t.ja, t.grund
    r = at.regeln_uebersicht(stand)
    assert "Coins Long (außer bewährte Setup-Arten)" in r["gesperrt"]


def test_fuers_telefon_rechnet_mit_dem_archiv() -> None:
    """Ohne Archiv sah das Tor eine bewaehrte Art nicht, deren Trades ueberschrieben waren."""
    archiv = [
        _trade(f"G{i}USD", f"2026-09-2{i}T00:00:00+00:00", "ziel_erreicht", ["TP1", "TP2", "TP3"])
        for i in range(6)
    ]
    liste = Wachliste()
    liste.aufnehmen([_zeile("NEUUSD")], jetzt=T0)
    ev = liste.pruefen(
        {"NEUUSD": {"hoch": 100.2, "tief": 99.9, "letzter": 100.0}}, jetzt=T0 + timedelta(minutes=5)
    )
    einstiege = [e for e in ev if e.art == "EINSTIEG"]
    assert einstiege
    ohne, _ = at.fuers_telefon(einstiege, liste.wachen, raus_vorher={}, jetzt=T0)
    assert ohne == []
    liste.wachen["NEUUSD"].gemeldet = ""
    mit, notizen = at.fuers_telefon(
        einstiege, liste.wachen, raus_vorher={}, jetzt=T0, archiv=archiv
    )
    assert [e.art for e in mit] == ["EINSTIEG"], notizen
