"""Die Wachliste — was aus einem Signal wird, nachdem es einmal da war."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from trading_agent.scanner.watchlist import HALTBARKEIT, Wachliste, Zustand

T0 = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)


def _zeile(
    instrument: str = "BTCUSDT",
    *,
    richtung: str = "long",
    einstieg: float = 100.0,
    stop: float = 90.0,
    tp1: float | None = 110.0,
    tp2: float | None = 120.0,
    tp3: float | None = 130.0,
    handelbar: bool = True,
    note: str = "A",
    art: str = "Ruecklauf in FVG H1",
) -> dict[str, Any]:
    return {
        "instrument": instrument,
        "klasse": "krypto",
        "richtung": richtung,
        "note": note,
        "handelbar": handelbar,
        "einstieg": einstieg,
        "einstieg_art": art,
        "invalidierung": stop,
        "ziel": tp1,
        "tp2": tp2,
        "tp3": tp3,
        "score": 65.0,
        "rr": 2.0,
        "erwartete_bewegung_pct": 20.0,
    }


def _kurs(hoch: float, tief: float, letzter: float | None = None) -> dict[str, dict[str, float]]:
    return {
        "BTCUSDT": {"hoch": hoch, "tief": tief, "letzter": letzter if letzter is not None else hoch}
    }


# ------------------------------------------------------------------ aufnehmen


def test_nur_handelbare_setups_kommen_auf_die_liste() -> None:
    w = Wachliste()
    ev = w.aufnehmen([_zeile(), _zeile("ETHUSDT", handelbar=False)], jetzt=T0)
    assert set(w.wachen) == {"BTCUSDT"}
    assert [e.art for e in ev] == ["NEUES_SETUP"]


def test_setup_ohne_stop_wird_nicht_beobachtet() -> None:
    """Ohne Invalidierung gibt es kein Risiko, das man messen koennte."""
    z = _zeile()
    z["invalidierung"] = None
    assert Wachliste().aufnehmen([z], jetzt=T0) == []


def test_meldung_sagt_ob_man_jetzt_kaufen_soll_oder_warten() -> None:
    w = Wachliste()
    (sofort,) = w.aufnehmen([_zeile("AAA", art="sofort")], jetzt=T0)
    assert "Einstieg liegt beim aktuellen Kurs" in sofort.text

    w2 = Wachliste()
    (warten,) = w2.aufnehmen([_zeile("BBB")], jetzt=T0)
    assert "Noch nicht einsteigen" in warten.text
    assert "100" in warten.text


def test_laufende_wache_wird_nicht_ueberschrieben() -> None:
    """Sonst wandert der Stop mit jedem Scan und das Ergebnis ist gegen nichts gemessen."""
    w = Wachliste()
    w.aufnehmen([_zeile(stop=90.0)], jetzt=T0)
    w.aufnehmen([_zeile(stop=95.0)], jetzt=T0 + timedelta(hours=2))
    assert w.wachen["BTCUSDT"].stop == 90.0


# ------------------------------------------------------------------ Einstieg


def test_einstieg_wird_gemeldet_wenn_der_kurs_ihn_beruehrt() -> None:
    w = Wachliste()
    w.aufnehmen([_zeile()], jetzt=T0)
    ev = w.pruefen(_kurs(hoch=105.0, tief=99.5), jetzt=T0 + timedelta(minutes=15))
    assert [e.art for e in ev] == ["EINSTIEG"]
    assert ev[0].dringend
    assert w.wachen["BTCUSDT"].zustand == Zustand.AKTIV.value


def test_einstieg_wird_nur_einmal_gemeldet() -> None:
    w = Wachliste()
    w.aufnehmen([_zeile()], jetzt=T0)
    w.pruefen(_kurs(105.0, 99.5), jetzt=T0 + timedelta(minutes=15))
    ev = w.pruefen(_kurs(105.0, 99.5), jetzt=T0 + timedelta(minutes=30))
    assert not any(e.art == "EINSTIEG" for e in ev)


def test_kurs_der_den_einstieg_nicht_erreicht_meldet_nichts() -> None:
    w = Wachliste()
    w.aufnehmen([_zeile()], jetzt=T0)
    assert w.pruefen(_kurs(108.0, 101.0), jetzt=T0 + timedelta(minutes=15)) == []


def test_das_tief_zaehlt_nicht_der_schlusskurs() -> None:
    """Ein Treffer um 14:23, der um 14:30 wieder weg ist, darf nicht durchrutschen."""
    w = Wachliste()
    w.aufnehmen([_zeile()], jetzt=T0)
    ev = w.pruefen(_kurs(hoch=106.0, tief=98.0, letzter=105.0), jetzt=T0 + timedelta(minutes=15))
    assert [e.art for e in ev] == ["EINSTIEG"]


# ------------------------------------------------------------------ Ziele und Stop


def test_ziele_werden_der_reihe_nach_gemeldet() -> None:
    w = Wachliste()
    w.aufnehmen([_zeile()], jetzt=T0)
    w.pruefen(_kurs(100.0, 99.0), jetzt=T0 + timedelta(minutes=15))
    ev = w.pruefen(_kurs(112.0, 105.0), jetzt=T0 + timedelta(minutes=30))
    assert [e.art for e in ev] == ["TP"]
    assert "TP1" in ev[0].titel
    assert w.wachen["BTCUSDT"].erreicht == ["TP1"]

    ev2 = w.pruefen(_kurs(131.0, 120.0), jetzt=T0 + timedelta(minutes=45))
    assert [e.titel.split()[0] for e in ev2] == ["TP2", "TP3"]
    assert w.wachen["BTCUSDT"].zustand == Zustand.ZIEL_ERREICHT.value


def test_tp1_rat_ist_konkret() -> None:
    w = Wachliste()
    w.aufnehmen([_zeile()], jetzt=T0)
    w.pruefen(_kurs(100.0, 99.0), jetzt=T0 + timedelta(minutes=15))
    ev = w.pruefen(_kurs(112.0, 105.0), jetzt=T0 + timedelta(minutes=30))
    assert "Stop auf den Einstieg" in ev[0].text
    assert "+1.00R" in ev[0].text


def test_stop_beendet_die_wache() -> None:
    w = Wachliste()
    w.aufnehmen([_zeile()], jetzt=T0)
    w.pruefen(_kurs(100.0, 99.0), jetzt=T0 + timedelta(minutes=15))
    ev = w.pruefen(_kurs(101.0, 89.0), jetzt=T0 + timedelta(minutes=30))
    assert [e.art for e in ev] == ["STOP"]
    assert w.wachen["BTCUSDT"].zustand == Zustand.STOP.value
    # Danach passiert nichts mehr, auch wenn der Kurs zurueckkommt.
    assert w.pruefen(_kurs(140.0, 130.0), jetzt=T0 + timedelta(hours=2)) == []


def test_ziel_und_stop_im_selben_fenster_zaehlt_als_stop() -> None:
    """Die Reihenfolge ist aus Hoch und Tief nicht ablesbar — dann die pessimistische
    Annahme. Eine Statistik, die sich im Zweifel den Gewinn gutschreibt, ist geschoent."""
    w = Wachliste()
    w.aufnehmen([_zeile()], jetzt=T0)
    w.pruefen(_kurs(100.0, 99.0), jetzt=T0 + timedelta(minutes=15))
    ev = w.pruefen(_kurs(hoch=125.0, tief=88.0), jetzt=T0 + timedelta(minutes=30))
    assert [e.art for e in ev] == ["STOP"]
    assert w.wachen["BTCUSDT"].erreicht == []


def test_short_laeuft_spiegelbildlich() -> None:
    w = Wachliste()
    w.aufnehmen(
        [_zeile(richtung="short", einstieg=100.0, stop=110.0, tp1=90.0, tp2=80.0, tp3=None)],
        jetzt=T0,
    )
    ev = w.pruefen(_kurs(hoch=100.5, tief=97.0), jetzt=T0 + timedelta(minutes=15))
    assert [e.art for e in ev] == ["EINSTIEG"]
    ev2 = w.pruefen(_kurs(hoch=99.0, tief=79.0), jetzt=T0 + timedelta(minutes=30))
    assert [e.titel.split()[0] for e in ev2] == ["TP1", "TP2"]
    assert w.wachen["BTCUSDT"].zustand == Zustand.ZIEL_ERREICHT.value


# ------------------------------------------------------------------ Ablauf und Invalidierung


def test_setup_laeuft_ab_wenn_der_einstieg_nie_kommt() -> None:
    w = Wachliste()
    w.aufnehmen([_zeile()], jetzt=T0)
    ev = w.pruefen(_kurs(108.0, 101.0), jetzt=T0 + HALTBARKEIT + timedelta(hours=1))
    assert [e.art for e in ev] == ["ABGELAUFEN"]
    assert w.wachen["BTCUSDT"].zustand == Zustand.ABGELAUFEN.value


def test_richtungswechsel_im_scan_macht_die_wache_ungueltig() -> None:
    w = Wachliste()
    w.aufnehmen([_zeile()], jetzt=T0)
    ev = w.gegen_scan([_zeile(richtung="short")], jetzt=T0 + timedelta(hours=3))
    assert [e.art for e in ev] == ["INVALIDIERT"]
    assert w.wachen["BTCUSDT"].zustand == Zustand.INVALIDIERT.value


def test_fehlende_kurse_behaupten_nichts() -> None:
    w = Wachliste()
    w.aufnehmen([_zeile()], jetzt=T0)
    assert w.pruefen({}, jetzt=T0 + timedelta(minutes=15)) == []
    assert w.wachen["BTCUSDT"].zustand == Zustand.WARTET.value


# ------------------------------------------------------------------ Persistenz


def test_zustand_ueberlebt_speichern_und_laden() -> None:
    w = Wachliste()
    w.aufnehmen([_zeile()], jetzt=T0)
    w.pruefen(_kurs(112.0, 99.0), jetzt=T0 + timedelta(minutes=15))
    wieder = Wachliste.from_dict(w.as_dict())
    assert wieder.wachen["BTCUSDT"].zustand == w.wachen["BTCUSDT"].zustand
    assert wieder.wachen["BTCUSDT"].erreicht == w.wachen["BTCUSDT"].erreicht
    # Und meldet danach nichts erneut.
    assert not any(
        e.art in ("EINSTIEG", "TP")
        for e in wieder.pruefen(_kurs(112.0, 99.0), jetzt=T0 + timedelta(minutes=30))
    )


def test_aufraeumen_begrenzt_die_abgeschlossenen() -> None:
    w = Wachliste()
    for i in range(20):
        w.aufnehmen([_zeile(f"C{i}")], jetzt=T0)
    for wache in w.wachen.values():
        wache.zustand = Zustand.STOP.value
        wache.zuletzt = T0.isoformat()
    assert w.aufraeumen(behalten=5) == 15
    assert len(w.wachen) == 5
