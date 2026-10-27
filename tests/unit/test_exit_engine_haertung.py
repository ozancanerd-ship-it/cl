"""Exit-Engine: Mengen, Luecken, Kursalter, Gebuehren (Haertung 27.10.2026).

Jeder Test haelt einen Fehler fest, der im Code von Hand nachweisbar war:

* Teilverkauf: „Rest / offene Ziele" machte aus dem zweiten Drittel ein HALBES, wenn die
  Stueckzahl nach dem ersten Teilverkauf nicht von Hand verkleinert wurde.
* Stop gerissen: Bei einer Kurslücke stand nur „der Stop lag bei X" — der echte Abstand fehlte.
* Kursalter: Ein Alarm auf einem Scan-Kurs von vor einer Stunde sah aus wie ein Livekurs.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from trading_agent.portfolio_intel.depot_stops import ScanIndex, kursnote, lage_fuer, pruefe_depot


def _zeile(inst: str, kurs: float, **kw: object) -> dict:
    z = {
        "instrument": inst,
        "kurs": kurs,
        "klasse": "krypto",
        "waehrung": "USD",
        "name": inst,
        "invalidierung": None,
        "richtung": None,
        "ziel": None,
        "tp2": None,
        "tp3": None,
        "halte": None,
        "warnungen": [],
    }
    z.update(kw)
    return z


def _scan(*zeilen: dict, erzeugt: str | None = None) -> dict:
    d: dict = {"eurusd": 1.10, "gesamt": list(zeilen)}
    if erzeugt:
        d["erzeugt"] = erzeugt
    return d


def _pos(menge: float, **kw: object) -> dict:
    p = {
        "sym": "SOLUSD",
        "menge": menge,
        "einstieg": 100.0,
        "plan": {"stop": 90.0, "tp1": 110.0, "tp2": 120.0, "tp3": 130.0, "gekauft": "2026-09-20"},
    }
    p.update(kw)
    return p


def _ziel_text(ev: list) -> str:
    return " ".join(e.text for e in ev if e.art == "ziel")


def test_zweites_drittel_ist_ein_drittel_auch_wenn_die_menge_unveraendert_bleibt() -> None:
    # TP1 erreicht und bestaetigt; der Nutzer verkleinert die Menge NICHT.
    stand, ev, _ = pruefe_depot([_pos(9.0)], _scan(_zeile("SOLUSD", 111.0)), {}, {"SOLUSD": 110.8})
    assert "ca. 3 Stück von 9" in _ziel_text(ev)
    # TP2: Menge im Depot weiter 9 -> es sind trotzdem 3 Stueck (ein Drittel), nicht 4,5.
    stand, ev, _ = pruefe_depot(
        [_pos(9.0)], _scan(_zeile("SOLUSD", 121.0)), stand, {"SOLUSD": 120.5}
    )
    t = _ziel_text(ev)
    assert "ca. 3 Stück von 9" in t, t
    assert "Ausgangsmenge 9" in t


def test_zweites_drittel_bei_verkleinerter_menge_bleibt_ein_drittel() -> None:
    stand, ev, _ = pruefe_depot([_pos(9.0)], _scan(_zeile("SOLUSD", 111.0)), {}, {"SOLUSD": 110.8})
    # Der Nutzer hat 3 Stueck verkauft und die Menge auf 6 gesetzt.
    stand, ev, _ = pruefe_depot(
        [_pos(6.0)], _scan(_zeile("SOLUSD", 121.0)), stand, {"SOLUSD": 120.5}
    )
    assert "ca. 3 Stück von 6" in _ziel_text(ev), _ziel_text(ev)


def test_letztes_drittel_ist_hoechstens_die_restmenge() -> None:
    stand, _, _ = pruefe_depot([_pos(9.0)], _scan(_zeile("SOLUSD", 111.0)), {}, {"SOLUSD": 110.8})
    stand, _, _ = pruefe_depot(
        [_pos(6.0)], _scan(_zeile("SOLUSD", 121.0)), stand, {"SOLUSD": 120.5}
    )
    # Menge ist auf 2 geschrumpft (mehr verkauft als geplant): nie mehr verkaufen als da ist.
    stand, ev, _ = pruefe_depot(
        [_pos(2.0)], _scan(_zeile("SOLUSD", 131.0)), stand, {"SOLUSD": 130.5}
    )
    assert "ca. 2 Stück von 2" in _ziel_text(ev), _ziel_text(ev)


def test_nachkauf_nach_dem_ersten_ziel_vergisst_die_alte_ausgangsmenge() -> None:
    stand, _, _ = pruefe_depot([_pos(9.0)], _scan(_zeile("SOLUSD", 111.0)), {}, {"SOLUSD": 110.8})
    assert stand["positionen"]["SOLUSD|"]["menge0"] == 9.0
    stand, _, _ = pruefe_depot(
        [_pos(15.0)], _scan(_zeile("SOLUSD", 112.0)), stand, {"SOLUSD": 111.5}
    )
    assert "menge0" not in stand["positionen"]["SOLUSD|"]


def test_stop_luecke_nennt_den_echten_abstand() -> None:
    pos = [{"sym": "SOLUSD", "menge": 1, "plan": {"stop": 100.0}}]
    _, ev, _ = pruefe_depot(pos, _scan(_zeile("SOLUSD", 92.0)), {})
    t = " ".join(e.text for e in ev if e.art == "stop")
    assert "8.0 % jenseits des Stops" in t, t


def test_stop_ohne_luecke_hat_keinen_luecken_satz() -> None:
    pos = [{"sym": "SOLUSD", "menge": 1, "plan": {"stop": 100.0}}]
    _, ev, _ = pruefe_depot(pos, _scan(_zeile("SOLUSD", 99.5)), {})
    t = " ".join(e.text for e in ev if e.art == "stop")
    assert "Lücke" not in t


def test_short_luecke_wird_gespiegelt() -> None:
    pos = [{"sym": "SOLUSD", "menge": 1, "plan": {"stop": 100.0, "richtung": "short"}}]
    _, ev, _ = pruefe_depot(pos, _scan(_zeile("SOLUSD", 108.0)), {})
    t = " ".join(e.text for e in ev if e.art == "stop")
    assert "8.0 % jenseits des Stops" in t, t


def test_kursnote_nur_bei_altem_scan_kurs() -> None:
    jetzt = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)
    alt = (jetzt - timedelta(minutes=90)).isoformat()
    frisch = (jetzt - timedelta(minutes=5)).isoformat()
    pos = {"sym": "SOLUSD", "menge": 1}
    lg = lage_fuer(pos, ScanIndex(_scan(_zeile("SOLUSD", 100.0), erzeugt=alt), jetzt))
    assert "90 Min. alt" in kursnote(lg)
    lg = lage_fuer(pos, ScanIndex(_scan(_zeile("SOLUSD", 100.0), erzeugt=frisch), jetzt))
    assert kursnote(lg) == ""
    # Live nachgeholter Kurs: nie eine Altersnote, egal wie alt die Scan-Datei ist.
    lg = lage_fuer(
        pos, ScanIndex(_scan(_zeile("SOLUSD", 100.0, kurs_live=True), erzeugt=alt), jetzt)
    )
    assert kursnote(lg) == ""


def test_ohne_zeitstempel_keine_altersnote_und_kein_absturz() -> None:
    lg = lage_fuer({"sym": "SOLUSD", "menge": 1}, ScanIndex(_scan(_zeile("SOLUSD", 100.0))))
    assert kursnote(lg) == ""


def test_uebersicht_zaehlt_live_und_scan_kurse() -> None:
    pos = [{"sym": "SOLUSD", "menge": 1}, {"sym": "ETHUSD", "menge": 1}]
    scan = _scan(_zeile("SOLUSD", 100.0, kurs_live=True), _zeile("ETHUSD", 50.0))
    _, _, ue = pruefe_depot(pos, scan, {})
    assert ue["kurse_live"] == 1 and ue["kurse_scan"] == 1


def test_trade_republic_gebuehr_nur_bei_kleinen_aktien_erloesen() -> None:
    pos = lambda konto: {  # noqa: E731
        "sym": "NVDA",
        "menge": 0.6,
        "einstieg": 100.0,
        "konto": konto,
        "plan": {"stop": 90.0, "tp1": 110.0, "tp2": 120.0, "tp3": 130.0, "gekauft": "x"},
    }
    zeile = _zeile("NVDA", 111.0, klasse="aktien", waehrung="EUR")
    _, ev, _ = pruefe_depot([pos("Trade Republic")], _scan(zeile), {}, {"NVDA": 110.9})
    t = _ziel_text(ev)
    assert "1 € — bei ca. 22 € Erlös" in t, t
    # Anderes Konto: keine Gebuehren-Behauptung.
    _, ev, _ = pruefe_depot([pos("Kraken")], _scan(zeile), {}, {"NVDA": 110.9})
    assert "Trade Republic" not in _ziel_text(ev)
