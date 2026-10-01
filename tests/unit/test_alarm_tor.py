"""Das Alarm-Tor — was aufs Handy darf.

Ozan, 26.09.: „Die Alarme sind irgendwie schlecht geworden … bessere Alarme, da wo es
sich wirklich lohnt." Diese Tests halten fest, was seitdem gilt, damit es nicht beim
naechsten Umbau stillschweigend wieder aufweicht.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from trading_agent.scanner import alarm_tor as at
from trading_agent.scanner.watchlist import Wachliste, Zustand

T0 = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)


def _fertig(
    setup: str,
    zustand: str,
    erreicht: list[str] | None = None,
    *,
    klasse: str = "krypto",
    richtung: str = "long",
    eingestiegen: bool = True,
) -> dict[str, Any]:
    return {
        "setup": setup,
        "zustand": zustand,
        "erreicht": erreicht or [],
        "klasse": klasse,
        "richtung": richtung,
        "einstiegskurs": 100.0 if eingestiegen else None,
    }


# ------------------------------------------------------------------ Bilanz


def test_basis_fasst_usd_und_usdt_zusammen() -> None:
    assert at.basis("LINKUSDT") == at.basis("LINKUSD") == "LINK"
    assert at.basis("BTCEUR") == "BTC"
    assert at.basis("NVDA") == "NVDA"


def test_bilanz_zaehlt_nur_entschiedene_trades() -> None:
    """Nie eingestiegen, abgelaufen oder ungueltig ohne Ziel — das sagt nichts ueber die
    Setup-Art. Die alte Zaehlung buchte es mit 0 R und verduennte damit alles."""
    wachen = [
        _fertig("X", "stop"),
        _fertig("X", "ziel_erreicht", ["TP1", "TP2", "TP3"]),
        _fertig("X", "invalidiert", ["TP1"]),  # Ziel 1 → mit Drittel-Regel entschieden
        _fertig("X", "abgelaufen"),  # kein Ergebnis
        _fertig("X", "invalidiert"),  # kein Ergebnis
        _fertig("X", "stop", eingestiegen=False),  # nie eingestiegen
        _fertig("X", "aktiv", ["TP1"]),  # laeuft noch
    ]
    st = at.bilanz(wachen)["setup:X"]
    assert st.anzahl == 3
    assert round(st.summe_r, 3) == round(-1.0 + 6.5 / 3 + 1 / 3, 3)
    assert st.ziel1 == 2


def test_verlierende_setup_art_wird_gesperrt() -> None:
    wachen = [_fertig("Rueckeroberung", "stop") for _ in range(5)]
    wachen.append(_fertig("Rueckeroberung", "invalidiert", ["TP1", "TP2"]))
    st = at.bilanz(wachen)["setup:Rueckeroberung"]
    assert st.anzahl == at.MIN_FAELLE
    assert st.urteil == "gesperrt"


def test_zu_wenig_faelle_sind_kein_urteil() -> None:
    wachen = [_fertig("Neu", "stop") for _ in range(at.MIN_FAELLE - 1)]
    assert at.bilanz(wachen)["setup:Neu"].urteil == "offen"


def test_gewinnende_setup_art_ist_bewaehrt() -> None:
    wachen = [_fertig("Ausbruch", "ziel_erreicht", ["TP1", "TP2", "TP3"]) for _ in range(3)]
    wachen += [_fertig("Ausbruch", "stop") for _ in range(3)]
    st = at.bilanz(wachen)["setup:Ausbruch"]
    assert st.urteil == "bewaehrt"


# ------------------------------------------------------------------ feste Pruefungen

#: Eine Setup-Art mit Erfolgsnachweis: 4× bis Ziel 3, 2× Stop. Seit dem Gewinner-Tor
#: (01.10.) klingelt nur noch, was so eine Bilanz hat.
_BEWAEHRT = [_fertig("Ausbruch aus der Basis", "ziel_erreicht", ["TP1", "TP2", "TP3"])] * 4 + [
    _fertig("Ausbruch aus der Basis", "stop")
] * 2


def _bewaehrt_stand() -> dict[str, at.Stand]:
    return at.bilanz(_BEWAEHRT)


def _liste_mit_bilanz() -> Wachliste:
    """Eine Wachliste, auf der „Ausbruch aus der Basis" schon bewaehrt ist."""
    from trading_agent.scanner.watchlist import Wache

    liste = Wachliste()
    for i, d in enumerate(_BEWAEHRT):
        liste.wachen[f"HIST{i}USD"] = Wache(
            instrument=f"HIST{i}USD",
            klasse="krypto",
            richtung="long",
            note="A",
            einstieg=100.0,
            einstieg_art="sofort",
            stop=95.0,
            tp1=105.0,
            tp2=110.0,
            tp3=117.5,
            score=70.0,
            rr=3.5,
            erwartet_pct=17.5,
            zustand=d["zustand"],
            erreicht=list(d["erreicht"]),
            einstiegskurs=100.0,
            setup="Ausbruch aus der Basis",
        )
    return liste


def _pruefe(**kw: Any) -> at.Tor:
    basis = {
        "note": "A−",
        "setup": "Ausbruch aus der Basis",
        "klasse": "krypto",
        "richtung": "long",
        "crv": 3.0,
        "ziel1_pct": 5.0,
        "stand": _bewaehrt_stand(),
    }
    basis.update(kw)
    return at.pruefe(**basis)


def test_gutes_setup_kommt_durch() -> None:
    t = _pruefe()
    assert t.ja, t.grund
    assert t.grund == ""


def test_ohne_benanntes_setup_kein_alarm() -> None:
    t = _pruefe(setup="")
    assert not t.ja
    assert "kein benanntes Setup" in t.grund


def test_b_plus_klingelt_nicht_mehr() -> None:
    """Gewinner-Tor: B+ klingelt auch bei bewaehrter Setup-Art nicht mehr."""
    t = _pruefe(note="B+")
    assert not t.ja
    assert "ab A−" in t.grund


def test_setup_art_ohne_erfolgsnachweis_klingelt_nicht() -> None:
    """Gewinner-Tor (01.10.): eine Art, ueber die die eigene Bilanz nichts weiss, klingelt
    nicht mehr — auch mit A+ nicht."""
    t = _pruefe(note="A+", stand={})
    assert not t.ja
    assert "noch nicht bewaehrt" in t.grund


def test_setup_art_im_minus_unter_der_mindestzahl_klingelt_nicht() -> None:
    """Der Fall vom 30.09.: „Ruecksetzer im Trend", 4 Trades, −1,3 R — vorher „offen" und
    damit frei, jetzt ohne Alarm."""
    wachen = [_fertig("Ruecksetzer im Trend", "stop", klasse="aktien", richtung="short")] * 3
    wachen.append(
        _fertig("Ruecksetzer im Trend", "invalidiert", ["TP1"], klasse="aktien", richtung="short")
    )
    t = _pruefe(
        note="A",
        setup="Ruecksetzer im Trend",
        klasse="aktien",
        richtung="short",
        stand=at.bilanz(wachen),
    )
    assert not t.ja
    assert "4 Trades" in t.grund


def test_regeln_nennen_die_freien_setup_arten() -> None:
    r = at.regeln_uebersicht(_bewaehrt_stand())
    assert r["nur_bewaehrt"] is True
    assert r["frei"] == ["Ausbruch aus der Basis"]


def test_b_klingelt_nie() -> None:
    wachen = [_fertig("Ausbruch aus der Basis", "ziel_erreicht", ["TP1", "TP2", "TP3"])] * 6
    assert not _pruefe(note="B", stand=at.bilanz(wachen)).ja


def test_gesperrte_setup_art_klingelt_auch_mit_a_nicht() -> None:
    wachen = [_fertig("Rueckeroberung nach Liquiditaetsgriff", "stop")] * 6
    t = _pruefe(note="A", setup="Rueckeroberung nach Liquiditaetsgriff", stand=at.bilanz(wachen))
    assert not t.ja
    assert "klingelt nicht" in t.grund


def test_gesperrte_richtung_in_der_klasse() -> None:
    """Krypto-Shorts: am 20.09. liefen fuenf von fuenf in den Stop."""
    wachen = [_fertig(f"S{i}", "stop", richtung="short") for i in range(6)]
    t = _pruefe(richtung="short", setup="Neu", stand=at.bilanz(wachen))
    assert not t.ja
    assert "Coins Short" in t.grund


def test_zu_kleines_crv_und_zu_wenig_raum() -> None:
    assert "unter 1:2" in _pruefe(crv=1.4).grund
    t = _pruefe(ziel1_pct=0.4)
    assert not t.ja and "Gebuehren" in t.grund
    # Aktien haben eine niedrigere Schwelle (niedrigere Kosten).
    assert _pruefe(klasse="aktien", ziel1_pct=1.2).ja


# ------------------------------------------------------------------ Deckel


def test_derselbe_coin_nicht_zweimal() -> None:
    t = at.deckel(_pruefe(), instrument="LINKUSD", verlauf=[(T0, "LINKUSDT")], jetzt=T0)
    assert not t.ja
    assert "LINK" in t.grund


def test_hoechstens_drei_am_tag() -> None:
    verlauf = [(T0 - timedelta(hours=h), f"C{h}USD") for h in (1, 5, 9)]
    assert not at.deckel(_pruefe(), instrument="XUSD", verlauf=verlauf, jetzt=T0).ja
    alt = [(T0 - timedelta(hours=30), f"C{h}USD") for h in (1, 2, 3)]
    assert at.deckel(_pruefe(), instrument="XUSD", verlauf=alt, jetzt=T0).ja


# ------------------------------------------------------------------ was aufs Telefon geht


def _zeile(instrument: str, **kw: Any) -> dict[str, Any]:
    z = {
        "instrument": instrument,
        "klasse": "krypto",
        "richtung": "long",
        "note": "A−",
        "handelbar": True,
        "einstieg": 100.0,
        "einstieg_art": "sofort",
        "invalidierung": 95.0,
        "plan": {"tp1": 105.0, "tp2": 110.0, "tp3": 117.5, "crv": 3.5},
        "score": 70.0,
        "setup": {"name": "Ausbruch aus der Basis", "these": "t", "trigger": "k"},
        "name": "Chainlink",
        "broker": "Bybit (USDT) oder Kraken (USD)",
        "eurusd": 1.1,
    }
    z.update(kw)
    return z


def _kurs(name: str, hoch: float, tief: float) -> dict[str, dict[str, float]]:
    return {name: {"hoch": hoch, "tief": tief, "letzter": (hoch + tief) / 2}}


def test_einstieg_mit_b_note_bleibt_in_der_app() -> None:
    """Der eigentliche Fehler bis zum 26.09.: jeder Einstieg klingelte, auch B."""
    liste = Wachliste()
    liste.aufnehmen([_zeile("LINKUSD", note="B")], jetzt=T0)
    ev = liste.pruefen(_kurs("LINKUSD", 101.0, 99.5), jetzt=T0 + timedelta(minutes=15))
    assert [e.art for e in ev] == ["EINSTIEG"]
    raus, notizen = at.fuers_telefon(ev, liste.wachen, raus_vorher={}, jetzt=T0)
    assert raus == []
    assert liste.wachen["LINKUSD"].tor_grund
    assert notizen
    # Und seine Folgealarme bleiben ebenfalls still: da steckt kein Geld drin.
    ev2 = liste.pruefen(_kurs("LINKUSD", 106.0, 101.0), jetzt=T0 + timedelta(minutes=30))
    assert [e.art for e in ev2] == ["TP"]
    assert at.fuers_telefon(ev2, liste.wachen, raus_vorher={}, jetzt=T0)[0] == []


def test_guter_einstieg_klingelt_mit_vollem_plan() -> None:
    liste = _liste_mit_bilanz()
    liste.aufnehmen([_zeile("LINKUSD")], jetzt=T0)
    ev = liste.pruefen(_kurs("LINKUSD", 101.0, 99.5), jetzt=T0 + timedelta(minutes=15))
    raus, _ = at.fuers_telefon(ev, liste.wachen, raus_vorher={}, jetzt=T0)
    assert [e.art for e in raus] == ["EINSTIEG"]
    t = raus[0]
    # Voller Name, Kuerzel in Klammern, Handelsort, Euro zur Orientierung.
    assert "Chainlink (LINK)" in t.titel
    assert "Bybit (USDT) oder Kraken (USD)" in t.text
    assert "€" in t.text
    assert "Warum dieser Alarm" in t.text
    assert liste.wachen["LINKUSD"].gemeldet


def test_folgealarme_nur_fuer_gemeldete_trades() -> None:
    liste = _liste_mit_bilanz()
    liste.aufnehmen([_zeile("LINKUSD")], jetzt=T0)
    ev = liste.pruefen(_kurs("LINKUSD", 101.0, 99.5), jetzt=T0 + timedelta(minutes=15))
    at.fuers_telefon(ev, liste.wachen, raus_vorher={}, jetzt=T0)
    ev2 = liste.pruefen(_kurs("LINKUSD", 106.0, 101.0), jetzt=T0 + timedelta(minutes=30))
    raus, _ = at.fuers_telefon(ev2, liste.wachen, raus_vorher={}, jetzt=T0)
    assert [e.art for e in raus] == ["TP"]
    assert raus[0].titel.startswith("ZIEL 1")


def test_nachgezogener_stop_nach_ziel_1() -> None:
    """HBAR am 23.09.: Ziel 1, danach zurueck unter den Einstieg. Laut eigenem Rat war
    der Rest bei ±0 raus — gemeldet wurde spaeter ein voller Stop mit −1 R."""
    liste = _liste_mit_bilanz()
    liste.aufnehmen([_zeile("HBARUSD", name="Hedera")], jetzt=T0)
    ev = liste.pruefen(_kurs("HBARUSD", 101.0, 99.5), jetzt=T0 + timedelta(minutes=15))
    at.fuers_telefon(ev, liste.wachen, raus_vorher={}, jetzt=T0)
    liste.pruefen(_kurs("HBARUSD", 106.0, 101.0), jetzt=T0 + timedelta(minutes=30))
    w = liste.wachen["HBARUSD"]
    assert w.schutz == w.einstiegskurs

    # Zurueck durch den Einstieg UND durch den alten Stop im selben Fenster.
    vorher = {k: v.raus for k, v in liste.wachen.items()}
    ev3 = liste.pruefen(_kurs("HBARUSD", 100.5, 94.0), jetzt=T0 + timedelta(hours=1))
    assert [e.art for e in ev3] == ["SCHUTZ", "STOP"]
    raus, _ = at.fuers_telefon(ev3, liste.wachen, raus_vorher=vorher, jetzt=T0)
    assert [e.art for e in raus] == ["SCHUTZ"]
    assert "ohne Verlust" in raus[0].text
    # Die Statistik laeuft mit dem echten Stop weiter.
    assert w.zustand == Zustand.STOP.value

    # Danach klingelt zu diesem Trade nichts mehr.
    assert at.fuers_telefon(ev3, liste.wachen, raus_vorher={"HBARUSD": True}, jetzt=T0)[0] == []


def test_analyse_dreht_waehrend_des_trades() -> None:
    """Vorher: nie dringend (Zustand wurde vor der Pruefung umgesetzt), nie aufs Telefon."""
    liste = _liste_mit_bilanz()
    liste.aufnehmen([_zeile("SOLUSD", name="Solana")], jetzt=T0)
    ev = liste.pruefen(_kurs("SOLUSD", 101.0, 99.5), jetzt=T0 + timedelta(minutes=15))
    at.fuers_telefon(ev, liste.wachen, raus_vorher={}, jetzt=T0)
    ev2 = liste.gegen_scan([_zeile("SOLUSD", richtung="short", kurs=102.0)], jetzt=T0)
    assert [e.art for e in ev2] == ["AUSSTIEG"]
    assert ev2[0].dringend
    raus, _ = at.fuers_telefon(ev2, liste.wachen, raus_vorher={}, jetzt=T0)
    assert [e.art for e in raus] == ["AUSSTIEG"]
    assert liste.wachen["SOLUSD"].ausstiegskurs == 102.0


def test_wartendes_setup_wird_nur_still_ungueltig() -> None:
    liste = Wachliste()
    liste.aufnehmen([_zeile("SOLUSD")], jetzt=T0)
    ev = liste.gegen_scan([_zeile("SOLUSD", richtung="short")], jetzt=T0)
    assert [e.art for e in ev] == ["INVALIDIERT"]
    assert not ev[0].dringend


def test_derselbe_coin_kommt_nur_einmal_auf_die_liste() -> None:
    liste = Wachliste()
    liste.aufnehmen([_zeile("LINKUSD"), _zeile("LINKUSDT")], jetzt=T0)
    assert set(liste.wachen) == {"LINKUSD"}


def test_tagesdeckel_laesst_die_besten_durch() -> None:
    liste = _liste_mit_bilanz()
    zeilen = [_zeile(f"C{i}USD", note="A−") for i in range(4)]
    zeilen.append(_zeile("TOPUSD", note="A+"))
    liste.aufnehmen(zeilen, jetzt=T0)
    kurse: dict[str, dict[str, float]] = {}
    for n in liste.wachen:
        kurse.update(_kurs(n, 101.0, 99.5))
    ev = liste.pruefen(kurse, jetzt=T0 + timedelta(minutes=15))
    assert len(ev) == 5
    raus, notizen = at.fuers_telefon(ev, liste.wachen, raus_vorher={}, jetzt=T0)
    assert len(raus) == at.MAX_JE_TAG
    assert "TOPUSD" in {e.instrument for e in raus}
    assert len(notizen) == 5 - at.MAX_JE_TAG


def test_ziele_nach_dem_ausstieg_zaehlen_nicht() -> None:
    """Bis zum 27.09.: Ziel 1, zurueck auf Einstand (laut Plan raus), spaeter Ziel 2 und 3.
    Die Wache laeuft fuer die Statistik weiter — und die Bilanz buchte +2,17 R, obwohl der
    Plan bei +0,33 R ausgestiegen war. Das hat Setup-Arten besser aussehen lassen, als sie
    fuer jemanden waren, der den Alarmen gefolgt ist."""
    from trading_agent.scanner import performance

    liste = Wachliste()
    liste.aufnehmen([_zeile("AAVEUSD", name="Aave")], jetzt=T0)
    liste.pruefen(_kurs("AAVEUSD", 101.0, 99.5), jetzt=T0 + timedelta(minutes=15))
    liste.pruefen(_kurs("AAVEUSD", 106.0, 101.0), jetzt=T0 + timedelta(minutes=30))
    liste.pruefen(_kurs("AAVEUSD", 101.0, 99.8), jetzt=T0 + timedelta(minutes=45))
    w = liste.wachen["AAVEUSD"]
    assert w.raus and w.raus_erreicht == ["TP1"] and w.raus_kurs == w.einstiegskurs
    liste.pruefen(_kurs("AAVEUSD", 118.0, 104.0), jetzt=T0 + timedelta(hours=2))
    assert w.zustand == Zustand.ZIEL_ERREICHT.value
    assert w.erreicht == ["TP1", "TP2", "TP3"]

    d = w.as_dict()
    st = at.bilanz([d])["setup:Ausbruch aus der Basis"]
    assert round(st.summe_r, 3) == round(1 / 3, 3)
    erg = performance.aus_wachliste({"wachen": {"AAVEUSD": d}})
    assert round(erg[0].r_drittel, 3) == round(1 / 3, 3)
    # Die Alles-oder-nichts-Rechnung sieht den ganzen Verlauf und bleibt bei Ziel 3.
    assert erg[0].r_ganz == 3.5


def test_raus_nach_ziel_2_rest_bei_ziel_1() -> None:
    assert round(at._r_drittel("stop", ["TP1", "TP2"], ["TP1", "TP2"]), 3) == round(4 / 3, 3)
    assert at._r_drittel("aktiv", ["TP1"], ["TP1"]) == 1 / 3


def test_laufender_trade_ist_nach_dem_schutz_stop_entschieden() -> None:
    """Laut Plan draussen ist entschieden — die Bilanz muss nicht warten, bis die Wache
    fuer die Statistik zu Ende gelaufen ist."""
    d = _fertig("Y", "aktiv", ["TP1"])
    d.update(raus=True, raus_erreicht=["TP1"])
    st = at.bilanz([d])["setup:Y"]
    assert st.anzahl == 1
    assert round(st.summe_r, 3) == round(1 / 3, 3)
