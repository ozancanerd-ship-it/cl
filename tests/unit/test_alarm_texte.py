"""Die Texte, die aufs Telefon gehen: Zahlenformat, Short-Wortwahl, duenner Markt.

WARUM ES DIESEN TEST GIBT

01.10., Issue #187: „ZIEL 1 Mondelez (MDLZ) — Teil verkaufen. Mondelez hat Ziel 1 bei
57.78 erreicht (+1.57R). Ein Drittel verkaufen …" Drei Fehler in drei Zeilen:

1. Der Trade war ein SHORT. Wer einen Short direkt haelt, verkauft nicht, er kauft
   zurueck; wer ihn ueber einen Short-Schein haelt, verkauft den Schein. „Ein Drittel
   verkaufen" ist fuer einen Short eine Anweisung, die man falsch herum ausfuehren kann.
2. „57.78" und „+1.57R" mit Punkt — in einer App, die ueberall „57,78" schreibt.
3. In der Analyse stand bei JEDEM Coin „… Mio USDT Tagesumsatz — gross genug, um wieder
   herauszukommen", auch bei 0,1 Mio („0 Mio USDT Tagesumsatz — gross genug"), zwei
   Zeilen unter der Warnung „duenn fuer schnelle Ausstiege".

Dazu der Hinweis zum duennen Markt im Kaufalarm: KEINE Sperre — in der eigenen Bilanz
liefen Coins unter 1 Mio Tagesumsatz nicht schlechter (docs/LIQUIDITAET-STUDIE-2026-10.md)
—, aber eine Limit-Order statt Marktpreis.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from trading_agent.scanner.watchlist import DUENN_UMSATZ, Wache, Wachliste, einstieg_text

T0 = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


def _zeile(klasse: str = "krypto", richtung: str = "long", umsatz: float | None = None) -> dict:
    lang = richtung == "long"
    z: dict[str, Any] = {
        "instrument": "MDLZ" if klasse == "aktien" else "DCRUSD",
        "klasse": klasse,
        "richtung": richtung,
        "note": "A",
        "handelbar": True,
        "einstieg": 59.55,
        "einstieg_art": "sofort",
        "invalidierung": 58.42 if lang else 60.68,
        "ziel": 61.32 if lang else 57.78,
        "tp2": 63.0 if lang else 55.83,
        "tp3": 64.5 if lang else 55.26,
        "score": 70.0,
        "name": "Mondelez" if klasse == "aktien" else "Decred",
        "setup": {"name": "Ruecksetzer im Trend"},
    }
    if umsatz is not None:
        z["zusatz"] = {"umsatz_24h": umsatz}
    return z


def _bis_ziel1(z: dict) -> list:
    w = Wachliste()
    w.aufnehmen([z], jetzt=T0)
    name = z["instrument"]
    lang = z["richtung"] == "long"
    w.pruefen(
        {name: {"hoch": 59.6, "tief": 59.5, "letzter": 59.55}}, jetzt=T0 + timedelta(minutes=5)
    )
    if lang:
        k = {name: {"hoch": 61.4, "tief": 59.9, "letzter": 61.35}}
    else:
        k = {name: {"hoch": 59.2, "tief": 57.7, "letzter": 57.75}}
    return w.pruefen(k, jetzt=T0 + timedelta(minutes=10))


def test_ziel_1_bei_einem_aktien_short_sagt_schliessen_und_wie_es_mit_dem_schein_geht():
    (e,) = _bis_ziel1(_zeile("aktien", "short"))
    assert e.art == "TP"
    assert e.titel.endswith("Teil schliessen")
    assert "Ein Drittel schliessen" in e.text
    assert "Short-Schein: ein Drittel des Scheins verkaufen" in e.text
    assert "verkaufen und" not in e.text.split("(")[0]


def test_ziel_1_bei_einem_krypto_short_nennt_den_terminkontrakt():
    (e,) = _bis_ziel1(_zeile("krypto", "short"))
    assert "Terminkontrakt: ein Drittel zurueckkaufen" in e.text


def test_ziel_1_bei_einem_long_bleibt_verkaufen():
    (e,) = _bis_ziel1(_zeile("krypto", "long"))
    assert e.titel.endswith("Teil verkaufen")
    assert "Ein Drittel verkaufen und den Stop auf den Einstieg" in e.text
    assert "Schein" not in e.text and "Terminkontrakt" not in e.text


def test_zahlen_im_alarm_mit_deutschem_komma():
    (e,) = _bis_ziel1(_zeile("aktien", "short"))
    assert "57,78" in e.text and "57.78" not in e.text
    assert "59,55" in e.text
    # (59,55 - 57,78) / 1,13 = +1,57 R
    assert "(+1,57 R)" in e.text


def test_aufnahme_merkt_sich_den_umsatz():
    w = Wachliste()
    w.aufnehmen([_zeile(umsatz=190_000.0)], jetzt=T0)
    assert w.wachen["DCRUSD"].umsatz_24h == 190_000.0
    # und er ueberlebt Speichern/Laden
    w2 = Wachliste.from_dict(w.as_dict())
    assert w2.wachen["DCRUSD"].umsatz_24h == 190_000.0


def _wache(klasse: str, umsatz: float | None) -> Wache:
    return Wache(
        instrument="DCRUSD" if klasse == "krypto" else "MDLZ",
        klasse=klasse,
        richtung="long",
        note="A−",
        einstieg=18.169,
        einstieg_art="limit",
        stop=17.708,
        tp1=18.65,
        tp2=19.1,
        tp3=19.6,
        score=70.0,
        rr=3.1,
        erwartet_pct=5.0,
        umsatz_24h=umsatz,
    )


def test_duenner_markt_steht_im_kaufalarm():
    t = einstieg_text(_wache("krypto", 190_000.0))
    assert "Duenner Markt: nur 0,2 Mio USD Umsatz am Tag" in t
    assert "Limit-Order" in t


def test_kein_hinweis_bei_genug_umsatz_oder_ohne_angabe_oder_bei_aktien():
    assert "Duenner Markt" not in einstieg_text(_wache("krypto", DUENN_UMSATZ * 5))
    assert "Duenner Markt" not in einstieg_text(_wache("krypto", None))
    assert "Duenner Markt" not in einstieg_text(_wache("aktien", 1_000.0))


def test_analyse_lobt_den_umsatz_nur_wenn_er_gross_ist():
    from trading_agent.scanner.analysis_view import kommentar

    class Chance:
        richtung = None
        kurs = 18.0
        rr = None

    klein = kommentar(Chance(), [], [], zusatz={"umsatz_24h": 112_708})
    assert not any("gross genug" in s for s in klein["warum_jetzt"])
    gross = kommentar(Chance(), [], [], zusatz={"umsatz_24h": 25_000_000})
    assert any("25 Mio USDT Tagesumsatz" in s for s in gross["warum_jetzt"])


def test_wartende_wache_bekommt_den_umsatz_nachgetragen_ohne_den_plan_zu_aendern():
    """Wachen von vor dem 01.10. haben keinen Umsatz gespeichert (z. B. DCRUSD)."""
    w = Wachliste()
    w.aufnehmen([_zeile()], jetzt=T0)
    alt = w.wachen["DCRUSD"]
    assert alt.umsatz_24h is None
    stop, ziel = alt.stop, alt.tp1
    w.aufnehmen([_zeile(umsatz=190_000.0)], jetzt=T0 + timedelta(minutes=10))
    neu = w.wachen["DCRUSD"]
    assert neu.umsatz_24h == 190_000.0
    assert (neu.stop, neu.tp1, neu.aufgenommen) == (stop, ziel, alt.aufgenommen)
