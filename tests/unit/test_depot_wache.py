"""Der Depot-Waechter — setzt Stops, zieht sie nach, meldet das Wichtige, verraet nichts.

Ozan: „Ich kriege nur die Alarme, wenn ich auf der App drauf bin." Und am 27.09.: „Die
Stops auf meinem Portfolio 24/7 analysieren und immer neu setzen und mir Bescheid sagen."

Was hier festgehalten wird:
  1. Jede Position mit Kurs bekommt einen Stop — auch ohne Einstiegs-Setup (Halte-Stop).
  2. Ein Stop wandert nur in Richtung Sicherheit und nie auf oder ueber den Kurs.
  3. Stop, Ziel und Knock-out melden sich genau einmal; Nachziehen erst in spuerbaren
     Schritten.
  4. Euro-Positionen werden in Euro gerechnet, auch wenn der Scan in Dollar notiert.
  5. Turbos laufen auf dem Basiswert und landen nie bei einem gleichnamigen Coin.
  6. Der Stand liegt versiegelt im oeffentlichen Repo — ohne Schluessel ist er Rauschen.
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
from scripts.depot_wache import (
    depot_lesen,
    letzter_schluss,
    neue_meldungen,
    stand_laden,
    stand_sichern,
)

from trading_agent.portfolio_intel.depot_stops import (
    MELDE_SCHRITT_PCT,
    ScanIndex,
    lage_fuer,
    pruefe_depot,
    stand_kennung,
)
from trading_agent.security.siegel import oeffnen, schluessel_aus, versiegeln

WURZEL = Path(__file__).resolve().parents[2]


def _code(positionen: list[dict]) -> str:
    roh = json.dumps({"v": 1, "t": 0, "p": positionen})
    return base64.b64encode(roh.encode("utf-8")).decode("ascii")


def _zeile(
    inst: str,
    kurs: float,
    *,
    klasse: str = "krypto",
    waehrung: str = "USD",
    halte: float | None = None,
    inv: float | None = None,
    richtung: str | None = None,
    ziel: float | None = None,
    name: str | None = None,
    warnungen: list[str] | None = None,
) -> dict:
    return {
        "instrument": inst,
        "kurs": kurs,
        "klasse": klasse,
        "waehrung": waehrung,
        "name": name or inst,
        "invalidierung": inv,
        "richtung": richtung,
        "ziel": ziel,
        "tp2": None,
        "tp3": None,
        "halte": ({"k": 5.0, "stop_long": halte, "stop_short": None} if halte else None),
        "warnungen": warnungen or [],
    }


def _scan(*zeilen: dict, eurusd: float = 1.10) -> dict:
    return {"eurusd": eurusd, "gesamt": list(zeilen)}


# --------------------------------------------------------------------------- Depot lesen


def test_depot_aus_dem_sync_text_lesen() -> None:
    p = [{"sym": "SOLUSD", "menge": 1.5, "einstieg": 80.7}]
    assert depot_lesen(_code(p))[0]["sym"] == "SOLUSD"


def test_depot_auch_aus_einem_ganzen_link() -> None:
    """Wer sich den Text selbst schickt, kopiert oft die ganze Zeile."""
    p = [{"sym": "NVDA", "menge": 1.0}]
    assert depot_lesen(f"https://example.invalid/#depot={_code(p)}")[0]["sym"] == "NVDA"


def test_unsinn_ergibt_ein_leeres_depot_statt_eines_absturzes() -> None:
    for text in ("", "   ", "kein base64 !!!", "eyJub2NoIjoia2VpbiBkZXBvdCJ9"):
        assert depot_lesen(text) == []


# --------------------------------------------------------------------------- Stops


def test_position_ohne_setup_bekommt_den_halte_stop() -> None:
    """Der Kern der Aenderung: frueher blieb so eine Position ohne jeden Schutz."""
    scan = _scan(_zeile("SOLUSD", 120.0, halte=100.0))
    stand, ev, ueb = pruefe_depot([{"sym": "SOLUSDT", "menge": 1, "konto": "Bybit"}], scan, {})
    st = stand["positionen"]["SOLUSDT|bybit"]
    assert st["stop"] == 100.0
    assert [e.art for e in ev] == ["stop_neu"]
    assert ueb["mit_stop"] == 1


def test_bei_einer_signal_position_gewinnt_die_engere_marke() -> None:
    """Ueber ein Signal gekauft: die Invalidierung des Setups (gleiche Richtung) zaehlt mit."""
    scan = _scan(_zeile("SOLUSD", 120.0, halte=100.0, inv=110.0, richtung="long"))
    pos = [{"sym": "SOLUSD", "menge": 1, "plan": {"gekauft": "2026-10-01T08:00:00Z"}}]
    stand, _, _ = pruefe_depot(pos, scan, {})
    assert stand["positionen"]["SOLUSD|"]["stop"] == 110.0


def test_gehaltene_position_bekommt_nur_den_halte_stop() -> None:
    """Seit 01.10.: eine Setup-Marke ist fuer einen neuen Einstieg gebaut und zu eng fuer
    eine Position, die man laengst haelt — gemessen ist nur der Halte-Stop."""
    scan = _scan(_zeile("SOLUSD", 120.0, halte=100.0, inv=110.0, richtung="long"))
    stand, _, _ = pruefe_depot([{"sym": "SOLUSD", "menge": 1}], scan, {})
    assert stand["positionen"]["SOLUSD|"]["stop"] == 100.0


def test_short_invalidierung_wird_nie_zum_long_stop() -> None:
    """Der Sei-Fall: der Kurs hat die Short-These ueberrollt, ihre Invalidierung liegt knapp
    UNTER dem Kurs — und wurde zum Long-Stop. Ein Tick spaeter hiess es „Stop gerissen"."""
    scan = _scan(_zeile("SEIUSD", 0.0725, halte=0.0599, inv=0.07243, richtung="short"))
    pos = [{"sym": "SEIUSD", "menge": 467, "plan": {"gekauft": "2026-09-20T08:00:00Z"}}]
    stand, _, _ = pruefe_depot(pos, scan, {})
    assert stand["positionen"]["SEIUSD|"]["stop"] == 0.0599


def test_alter_setup_stop_wird_korrigiert_samt_falschem_stop_alarm() -> None:
    """Einmalige Korrektur: ein Stand mit einem Setup-Stop (auch schon „ausgestoppt") auf
    einer gehaltenen Position faellt weg, der Halte-Stop wird neu gesetzt und gemeldet."""
    alt = {
        "positionen": {
            "SEIUSD|": {
                "stop": 0.07243,
                "ebene": "position",
                "quelle": "die laufende Analyse (Invalidierung des Setups)",
                "ausgestoppt": True,
                "erreicht": [],
                "menge": 467,
            }
        }
    }
    pos = [
        {"sym": "SEIUSD", "menge": 467, "plan": {"stop": 0.07243, "art": "setup", "quelle": "app"}}
    ]
    scan = _scan(_zeile("SEIUSD", 0.0724, halte=0.0599))
    stand, ev, _ = pruefe_depot(pos, scan, alt)
    st = stand["positionen"]["SEIUSD|"]
    assert st["stop"] == 0.0599 and not st.get("ausgestoppt")
    assert [e.art for e in ev] == ["stop_neu", "entwarnung"]
    assert "Korrektur" in ev[0].text
    # Beim naechsten Lauf passiert nichts mehr.
    stand2, ev2, _ = pruefe_depot(pos, scan, stand)
    assert ev2 == [] and stand2["positionen"]["SEIUSD|"]["stop"] == 0.0599


def test_selbst_gesetzter_stop_bleibt_auch_bei_der_korrektur() -> None:
    pos = [{"sym": "SOLUSD", "menge": 1, "plan": {"stop": 115.0, "quelle": "selbst"}}]
    stand, _, _ = pruefe_depot(pos, _scan(_zeile("SOLUSD", 120.0, halte=100.0)), {})
    assert stand["positionen"]["SOLUSD|"]["stop"] == 115.0


def test_stop_wandert_nur_nach_oben() -> None:
    pos = [{"sym": "SOLUSD", "menge": 1}]
    stand, _, _ = pruefe_depot(pos, _scan(_zeile("SOLUSD", 120.0, halte=100.0)), {})
    # Der Kurs faellt, der Halte-Stop der Analyse sinkt mit — der gesetzte Stop nicht.
    stand, ev, _ = pruefe_depot(pos, _scan(_zeile("SOLUSD", 105.0, halte=90.0)), stand)
    assert stand["positionen"]["SOLUSD|"]["stop"] == 100.0
    assert ev == []
    # Der Kurs steigt, die Marke steigt — jetzt wird nachgezogen.
    stand, _, _ = pruefe_depot(pos, _scan(_zeile("SOLUSD", 140.0, halte=115.0)), stand)
    assert stand["positionen"]["SOLUSD|"]["stop"] == 115.0


def test_nie_auf_oder_ueber_den_kurs() -> None:
    scan = _scan(_zeile("SOLUSD", 100.0, halte=100.0, inv=101.0, richtung="long"))
    stand, ev, _ = pruefe_depot([{"sym": "SOLUSD", "menge": 1}], scan, {})
    assert stand["positionen"]["SOLUSD|"]["stop"] is None
    assert ev == []


def test_stop_gerissen_meldet_sich_einmal_und_setzt_nichts_neu() -> None:
    pos = [{"sym": "SOLUSD", "menge": 1}]
    stand, _, _ = pruefe_depot(pos, _scan(_zeile("SOLUSD", 120.0, halte=100.0)), {})
    stand, ev, _ = pruefe_depot(pos, _scan(_zeile("SOLUSD", 99.0, halte=80.0)), stand)
    assert [e.art for e in ev] == ["stop"]
    neu, merker = neue_meldungen(ev, stand["gemeldet"], datetime.now(UTC))
    assert len(neu) == 1
    # Naechster Lauf: kein zweiter Alarm, und KEIN neuer Stop unter dem Kurs.
    stand["gemeldet"] = merker
    stand, ev, _ = pruefe_depot(pos, _scan(_zeile("SOLUSD", 95.0, halte=80.0)), stand)
    assert [e.art for e in ev] == []
    assert stand["positionen"]["SOLUSD|"]["ausgestoppt"] is True


def test_nach_dem_stop_neu_gekauft_beginnt_von_vorn() -> None:
    pos = [{"sym": "SOLUSD", "menge": 1}]
    stand, _, _ = pruefe_depot(pos, _scan(_zeile("SOLUSD", 120.0, halte=100.0)), {})
    stand, _, _ = pruefe_depot(pos, _scan(_zeile("SOLUSD", 99.0, halte=80.0)), stand)
    pos = [{"sym": "SOLUSD", "menge": 2}]
    stand, _, _ = pruefe_depot(pos, _scan(_zeile("SOLUSD", 99.0, halte=80.0)), stand)
    st = stand["positionen"]["SOLUSD|"]
    assert not st.get("ausgestoppt")
    assert st["stop"] == 80.0


def test_nachziehen_meldet_sich_erst_in_spuerbaren_schritten() -> None:
    """Der Chandelier ruckt fast taeglich — jede Kleinigkeit zu melden waere Laerm."""
    pos = [{"sym": "SOLUSD", "menge": 1}]
    stand, _, _ = pruefe_depot(pos, _scan(_zeile("SOLUSD", 100.0, halte=80.0)), {})
    klein = 80.0 + (MELDE_SCHRITT_PCT - 0.5)  # bei Kurs 100 knapp unter der Schwelle
    stand, ev, _ = pruefe_depot(pos, _scan(_zeile("SOLUSD", 100.0, halte=klein)), stand)
    assert stand["positionen"]["SOLUSD|"]["stop"] == klein
    assert ev == []
    gross = 80.0 + MELDE_SCHRITT_PCT + 1.0
    stand, ev, _ = pruefe_depot(pos, _scan(_zeile("SOLUSD", 100.0, halte=gross)), stand)
    assert [e.art for e in ev] == ["stop_nach"]


def test_ziel_1_zieht_den_stop_auf_den_einstieg() -> None:
    pos = [
        {
            "sym": "SOLUSD",
            "menge": 1,
            "einstieg": 100.0,
            "plan": {"stop": 90.0, "tp1": 110.0, "tp2": 120.0, "gekauft": "2026-09-20"},
        }
    ]
    stand, ev, _ = pruefe_depot(pos, _scan(_zeile("SOLUSD", 111.0)), {}, {"SOLUSD": 110.8})
    assert "ziel" in [e.art for e in ev]
    assert stand["positionen"]["SOLUSD|"]["stop"] == 100.0
    # Dasselbe Ziel meldet sich nicht noch einmal.
    stand, ev, _ = pruefe_depot(pos, _scan(_zeile("SOLUSD", 112.0)), stand, {"SOLUSD": 111.5})
    assert [e.art for e in ev if e.art == "ziel"] == []


def test_ueberdehnt_und_deutlich_im_plus_warnt_per_mail() -> None:
    pos = [{"sym": "SOLUSD", "menge": 1, "einstieg": 100.0}]
    scan = _scan(
        _zeile(
            "SOLUSD", 120.0, halte=100.0,
            warnungen=["H4-RSI 83 — die Bewegung ist bereits ueberdehnt"],
        )
    )
    stand, ev, _ = pruefe_depot(pos, scan, {})
    arten = [e.art for e in ev]
    assert "ueberdehnt" in arten
    e = next(x for x in ev if x.art == "ueberdehnt")
    assert "20,0 %" in e.text
    assert "überdehnt" in e.text
    # Dieselbe Spanne (20-25 %) meldet sich nicht noch einmal (Dedup wie bei jedem Alarm).
    jetzt = datetime.now(UTC)
    neu, gemeldet = neue_meldungen(ev, {}, jetzt)
    assert any(x.art == "ueberdehnt" for x in neu)
    stand, ev2, _ = pruefe_depot(pos, scan, stand)
    neu2, _ = neue_meldungen(ev2, gemeldet, jetzt)
    assert not any(x.art == "ueberdehnt" for x in neu2)


def test_ueberdehnt_ohne_deutlichen_gewinn_bleibt_still() -> None:
    pos = [{"sym": "SOLUSD", "menge": 1, "einstieg": 100.0}]
    scan = _scan(
        _zeile(
            "SOLUSD", 105.0, halte=95.0,
            warnungen=["H4-RSI 83 — die Bewegung ist bereits ueberdehnt"],
        )
    )
    _, ev, _ = pruefe_depot(pos, scan, {})
    assert "ueberdehnt" not in [e.art for e in ev]


def test_im_plus_ohne_ueberdehnt_warnung_bleibt_still() -> None:
    pos = [{"sym": "SOLUSD", "menge": 1, "einstieg": 100.0}]
    scan = _scan(_zeile("SOLUSD", 120.0, halte=100.0))
    _, ev, _ = pruefe_depot(pos, scan, {})
    assert "ueberdehnt" not in [e.art for e in ev]


def test_gewinn_faellt_vom_hoch_zurueck_und_meldet_sich_dringend() -> None:
    """Ozan, 08.10. 17:40: „wir haben noch nie richtig was mitgenommen" — MET lief weit
    ins Plus und faellt seitdem zurueck, ohne dass je verkauft wurde. Die Position merkt
    sich ueber zwei Laeufe den Spitzengewinn (hier +60 % bei 160) und meldet sich
    dringend, sobald davon deutlich etwas wieder weg ist (hier nur noch +20 % bei 120:
    40 Punkte vom Hoch weg, mehr als 35 % von 60)."""
    pos = [{"sym": "METUSD", "menge": 10, "einstieg": 100.0}]
    stand, ev1, _ = pruefe_depot(pos, _scan(_zeile("METUSD", 160.0, halte=90.0)), {})
    assert "gewinn_rueckgang" not in [e.art for e in ev1]  # noch am Hoch, nichts zu melden
    assert stand["positionen"]["METUSD|"]["hoch_gv_pct"] == pytest.approx(60.0)

    stand2, ev2, _ = pruefe_depot(pos, _scan(_zeile("METUSD", 120.0, halte=90.0)), stand)
    e = next(x for x in ev2 if x.art == "gewinn_rueckgang")
    assert e.dringend is True
    assert "60,0 %" in e.text and "20,0 %" in e.text
    assert stand2["positionen"]["METUSD|"]["hoch_gv_pct"] == pytest.approx(60.0)

    # Dedup: derselbe Rueckgangs-Bereich (30-39,9) meldet sich nicht noch einmal.
    jetzt = datetime.now(UTC)
    neu, gemeldet = neue_meldungen(ev2, {}, jetzt)
    assert any(x.art == "gewinn_rueckgang" for x in neu)
    _, ev3, _ = pruefe_depot(pos, _scan(_zeile("METUSD", 119.0, halte=90.0)), stand2)
    neu2, _ = neue_meldungen(ev3, gemeldet, jetzt)
    assert not any(x.art == "gewinn_rueckgang" for x in neu2)


def test_kleiner_gewinn_ohne_grossen_rueckgang_bleibt_still() -> None:
    pos = [{"sym": "METUSD", "menge": 10, "einstieg": 100.0}]
    stand, _, _ = pruefe_depot(pos, _scan(_zeile("METUSD", 112.0, halte=90.0)), {})
    _, ev2, _ = pruefe_depot(pos, _scan(_zeile("METUSD", 108.0, halte=90.0)), stand)
    assert "gewinn_rueckgang" not in [e.art for e in ev2]


def test_der_stop_aus_der_app_wird_nie_unterboten() -> None:
    pos = [{"sym": "SOLUSD", "menge": 1, "plan": {"stop": 105.0}}]
    stand, _, _ = pruefe_depot(pos, _scan(_zeile("SOLUSD", 120.0, halte=100.0)), {})
    assert stand["positionen"]["SOLUSD|"]["stop"] == 105.0


# --------------------------------------------------------------------------- Waehrung


def test_euro_position_wird_in_euro_gerechnet() -> None:
    """NVDA bei Trade Republic in Euro, der Scan in Dollar. Ohne Umrechnung laege ein
    Stop von 150 € gegen einen Kurs von 220 $ — und fiele nie, oder immer."""
    scan = _scan(_zeile("NVDA", 220.0, klasse="aktien", halte=198.0), eurusd=1.10)
    stand, _, _ = pruefe_depot([{"sym": "NVDA", "menge": 1, "konto": "Trade Republic"}], scan, {})
    assert abs(stand["positionen"]["NVDA|trade republic"]["stop"] - 180.0) < 1e-9
    lg = lage_fuer({"sym": "NVDA", "konto": "Trade Republic"}, ScanIndex(scan))
    assert abs(lg.kurs - 200.0) < 1e-9 and lg.waehrung == "EUR"


# --------------------------------------------------------------------------- Turbos


def _turbo(ko: float, **extra) -> dict:
    return {
        "sym": "TURBO-TSM-LONG",
        "basis": "TSM",
        "basis_richtung": "long",
        "hebel_richtung": "long",
        "ko": ko,
        "ratio": 1.0,
        "menge": 1,
        "konto": "Trade Republic",
        **extra,
    }


def test_turbo_stop_laeuft_auf_dem_basiswert() -> None:
    scan = _scan(_zeile("TSM", 300.0, klasse="aktien", halte=270.0, name="TSMC"))
    stand, ev, _ = pruefe_depot([_turbo(200.0)], scan, {})
    st = stand["positionen"]["TURBO-TSM-LONG|trade republic"]
    assert st["ebene"] == "basis" and st["stop"] == 270.0
    assert "TSMC" in ev[0].text


def test_stop_unter_dem_knock_out_ist_keiner() -> None:
    scan = _scan(_zeile("TSM", 300.0, klasse="aktien", halte=250.0))
    stand, _, _ = pruefe_depot([_turbo(260.0)], scan, {})
    assert stand["positionen"]["TURBO-TSM-LONG|trade republic"]["stop"] is None


def test_knock_out_puffer_warnt_einmal_und_wieder_wenn_er_zurueckkommt() -> None:
    pos = [_turbo(280.0)]  # 300 gegen 280: 6,7 % Puffer → eng
    stand, ev, _ = pruefe_depot(pos, _scan(_zeile("TSM", 300.0, klasse="aktien")), {})
    assert [e.art for e in ev] == ["puffer_eng"]
    neu, merker = neue_meldungen(ev, {}, datetime.now(UTC))
    assert len(neu) == 1
    # Gleiche Lage: still.
    _, ev, _ = pruefe_depot(pos, _scan(_zeile("TSM", 301.0, klasse="aktien")), stand)
    neu, merker = neue_meldungen(ev, merker, datetime.now(UTC))
    assert neu == []
    # Entspannt sich, dann wieder eng: das ist eine neue Lage.
    _, ev, _ = pruefe_depot(pos, _scan(_zeile("TSM", 400.0, klasse="aktien")), stand)
    _, merker = neue_meldungen(ev, merker, datetime.now(UTC))
    _, ev, _ = pruefe_depot(pos, _scan(_zeile("TSM", 300.0, klasse="aktien")), stand)
    neu, _ = neue_meldungen(ev, merker, datetime.now(UTC))
    assert [e.art for e in neu] == ["puffer_eng"]


def test_ausgeknockt_wird_gemeldet() -> None:
    _, ev, _ = pruefe_depot([_turbo(310.0)], _scan(_zeile("TSM", 300.0, klasse="aktien")), {})
    assert [e.art for e in ev] == ["ko"]


def test_turbo_landet_nie_bei_einem_gleichnamigen_coin() -> None:
    """Kraken fuehrt einen Coin namens TURBO. Ein Turbo-Zertifikat darf nie dessen Kurs
    bekommen — sonst waere jede Stopmarke Unsinn."""
    scan = _scan(
        _zeile("TURBOUSD", 0.004, halte=0.003), _zeile("TSM", 300.0, klasse="aktien", halte=270.0)
    )
    lg = lage_fuer(_turbo(200.0), ScanIndex(scan))
    assert lg.row is not None and lg.row["instrument"] == "TSM"
    lg = lage_fuer({"sym": "TURBO-SP500-LONG", "hebel_richtung": "long"}, ScanIndex(scan))
    assert lg.kurs is None


# --------------------------------------------------------------------------- Siegel


def test_siegel_hin_und_zurueck() -> None:
    k = schluessel_aus("geheim", "zweck")
    text = versiegeln(b'{"a": 1}', k)
    assert oeffnen(text, k) == b'{"a": 1}'
    assert b'{"a": 1}' not in base64.b64decode(text)


def test_falscher_schluessel_oder_manipulation_wird_abgelehnt() -> None:
    k = schluessel_aus("geheim", "zweck")
    text = versiegeln(b"hallo welt", k)
    assert oeffnen(text, schluessel_aus("anders", "zweck")) is None
    roh = bytearray(base64.b64decode(text))
    roh[-1] ^= 1
    assert oeffnen(base64.b64encode(bytes(roh)).decode(), k) is None
    assert oeffnen("kein base64 !!!", k) is None


def test_stand_ueberlebt_den_lauf_und_verraet_nichts(tmp_path) -> None:
    k = schluessel_aus("DEPOTCODE", "depot-waechter-stand-v1")
    p = tmp_path / "stand.siegel"
    stand_sichern(p, {"positionen": {"SOLUSD|bybit": {"stop": 128.4}}}, k)
    inhalt = p.read_text()
    assert "SOL" not in inhalt and "128" not in inhalt
    assert stand_laden(p, k)["positionen"]["SOLUSD|bybit"]["stop"] == 128.4
    assert stand_laden(p, schluessel_aus("ANDERS", "depot-waechter-stand-v1")) == {}
    assert stand_laden(tmp_path / "gibtesnicht", k) == {}


def test_kennung_haengt_nur_an_symbol_konto_und_menge() -> None:
    a = [{"sym": "solusd", "konto": "Bybit", "menge": 1.5, "einstieg": 80}]
    b = [{"sym": "SOLUSD", "konto": "bybit", "menge": 1.5, "plan": {"stop": 90}}]
    assert stand_kennung(a) == stand_kennung(b)
    assert stand_kennung(a) != stand_kennung([{**a[0], "menge": 2}])


# --------------------------------------------------------------------------- Ganzer Lauf


def test_ganzer_lauf_schreibt_nur_zaehler_oeffentlich(tmp_path) -> None:
    scan = _scan(_zeile("SOLUSD", 120.0, halte=100.0))
    (tmp_path / "scan.json").write_text(json.dumps(scan))
    env = {
        **os.environ,
        "PYTHONPATH": str(WURZEL / "src"),
        "DEPOT_CODE": _code([{"sym": "SOLUSDT", "menge": 1.79, "konto": "Bybit"}]),
    }
    for k in (
        "VAPID_PRIVATE_KEY",
        "PUSH_ABOS",
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_CHAT_ID",
        "GITHUB_TOKEN",
    ):
        env.pop(k, None)
    aus = subprocess.run(
        [
            sys.executable,
            str(WURZEL / "scripts" / "depot_wache.py"),
            "--scan",
            str(tmp_path / "scan.json"),
            "--stand",
            str(tmp_path / "s.siegel"),
            "--oeffentlich",
            str(tmp_path / "w.json"),
            "--send",
        ],
        capture_output=True,
        text=True,
        env=env,
        cwd=tmp_path,
        check=True,
    )
    assert "SOL" not in aus.stdout  # das Protokoll ist oeffentlich einsehbar
    w = json.loads((tmp_path / "w.json").read_text())
    assert w["aktiv"] is True and w["mit_stop"] == 1
    assert "SOL" not in json.dumps(w)
    assert "SOL" not in (tmp_path / "s.siegel").read_text()


def test_ohne_depot_code_meldet_die_seite_inaktiv(tmp_path) -> None:
    env = {**os.environ, "PYTHONPATH": str(WURZEL / "src")}
    env.pop("DEPOT_CODE", None)
    subprocess.run(
        [
            sys.executable,
            str(WURZEL / "scripts" / "depot_wache.py"),
            "--oeffentlich",
            str(tmp_path / "w.json"),
        ],
        capture_output=True,
        text=True,
        env=env,
        cwd=tmp_path,
        check=True,
    )
    assert json.loads((tmp_path / "w.json").read_text())["aktiv"] is False


def test_ziele_eines_fremden_setups_gelten_nicht_fuer_eine_gehaltene_position() -> None:
    """Ozan, 28.09.: „Ich soll schon Teil verkaufen, obwohl du selber meintest, das wäre
    nicht so sinnvoll." Ziele, die die App automatisch aus einem gerade laufenden Setup
    uebernommen hat, gehoeren nicht zu seiner Position — kein Teilverkauf daraus."""
    pos = [
        {
            "sym": "SOLUSD",
            "menge": 1,
            "einstieg": 100.0,
            "plan": {"stop": 90.0, "tp1": 110.0, "quelle": "app"},
        }
    ]
    _, ev, _ = pruefe_depot(pos, _scan(_zeile("SOLUSD", 111.0)), {})
    assert [e.art for e in ev] == []
    # Selbst gesetzt oder ueber die Signalkarte gekauft: dann schon.
    pos[0]["plan"]["quelle"] = "selbst"
    _, ev, _ = pruefe_depot(pos, _scan(_zeile("SOLUSD", 111.0)), {}, {"SOLUSD": 110.5})
    assert next(e.art for e in ev) == "ziel"


def test_der_waechter_legt_keine_ziele_an() -> None:
    scan = _scan(_zeile("SOLUSD", 120.0, halte=100.0, inv=110.0, richtung="long", ziel=125.0))
    stand, _, _ = pruefe_depot([{"sym": "SOLUSD", "menge": 1}], scan, {})
    assert not stand["positionen"]["SOLUSD|"].get("ziele")


def test_falscher_stop_alarm_bekommt_eine_entwarnung() -> None:
    """Ging auf der zu engen Setup-Marke schon „Stop gerissen" raus, kommt jetzt eine
    Entwarnung — genauso laut wie der Alarm selbst, damit niemand deswegen verkauft."""
    alt = {
        "positionen": {
            "SEIUSD|": {
                "stop": 0.07243,
                "ebene": "position",
                "quelle": "die laufende Analyse (Invalidierung des Setups)",
                "ausgestoppt": True,
                "erreicht": [],
                "menge": 467,
            }
        }
    }
    pos = [{"sym": "SEIUSD", "menge": 467}]
    _, ev, _ = pruefe_depot(pos, _scan(_zeile("SEIUSD", 0.0724, halte=0.0599)), alt)
    arten = [e.art for e in ev]
    assert "entwarnung" in arten
    e = next(x for x in ev if x.art == "entwarnung")
    assert e.dringend and "Nicht deswegen verkaufen" in e.text


def test_oeffentlicher_betreff_nennt_die_handlung_nicht_den_wert() -> None:
    from scripts.depot_wache import oeffentlicher_titel, texte

    from trading_agent.portfolio_intel.depot_stops import Ereignis

    neu = [
        Ereignis("stop", "SEIUSD|", "Sei", "…", True),
        Ereignis("ziel", "SOLUSD|", "Solana", "…", True),
        Ereignis("stop", "OPUSD|", "Optimism", "…", True),
    ]
    titel = oeffentlicher_titel(neu)
    assert titel == "DEPOT · VERKAUFEN (2) · TEIL VERKAUFEN"
    _, _, klingel = texte(neu)
    for name in ("Sei", "Solana", "Optimism", "SEIUSD"):
        assert name not in titel and name not in klingel
    assert "VERKAUFEN" in klingel


def test_teilverkauf_rahmen_aus_eigenem_stop() -> None:
    """Ozan, 13.10.: nur Einstieg und eigener Stop eintragen — dann gilt der Rahmen aus
    dem Abstand: 1 R = ein Drittel verkaufen, Stop auf Einstand. Fremde Setups bleiben aus."""
    pos = [{"sym": "SOLUSD", "menge": 3, "einstieg": 100.0, "plan": {"stop": 90.0, "quelle": "selbst"}}]
    _, ev, _ = pruefe_depot(pos, _scan(_zeile("SOLUSD", 110.5)), {}, {"SOLUSD": 110.2})
    assert ev[0].art == "ziel"
    assert "je 1 R" in ev[0].text
    pos[0]["plan"]["quelle"] = "app"
    _, ev, _ = pruefe_depot(pos, _scan(_zeile("SOLUSD", 110.5)), {}, {"SOLUSD": 110.2})
    assert [e.art for e in ev] == []


def _pos_rahmen() -> list[dict]:
    return [{"sym": "SOLUSD", "menge": 3, "einstieg": 100.0, "plan": {"stop": 90.0, "quelle": "selbst"}}]


def test_ziel_beruehrt_aber_schluss_darunter_ist_nur_vorlaeufig() -> None:
    """Berühren allein bestätigt nichts: erst ein abgeschlossener Stundenschluss über dem Ziel."""
    stand, ev, _ = pruefe_depot(_pos_rahmen(), _scan(_zeile("SOLUSD", 110.5)), {}, {"SOLUSD": 108.0})
    assert [e.art for e in ev] == ["ziel_vorlaeufig"]
    assert ev[0].text.startswith("VORLÄUFIG")
    assert ev[0].marke == "TP1-vorlaeufig"
    # Zweiter Lauf, gleiche Lage: keine Wiederholung (Merker bleibt gleich, Stand merkt es).
    _, ev2, _ = pruefe_depot(_pos_rahmen(), _scan(_zeile("SOLUSD", 110.6)), stand, {"SOLUSD": 108.0})
    assert [e.art for e in ev2] == []


def test_schluss_bestaetigt_danach_der_verkaufshinweis() -> None:
    stand, _, _ = pruefe_depot(_pos_rahmen(), _scan(_zeile("SOLUSD", 110.5)), {}, {"SOLUSD": 108.0})
    _, ev, _ = pruefe_depot(_pos_rahmen(), _scan(_zeile("SOLUSD", 111.0)), stand, {"SOLUSD": 110.2})
    arten = [e.art for e in ev]
    assert arten[0] == "ziel" and set(arten) <= {"ziel", "stop_nach"}
    assert "Stundenschluss bestätigt" in ev[0].text
    assert "Neuer Stop" in ev[0].text


def test_fehlen_stundendaten_bleibt_es_vorlaeufig_nie_bestaetigt() -> None:
    """Fehlende oder veraltete Stundendaten dürfen keinen bestätigten Alarm erzeugen."""
    stand, ev, _ = pruefe_depot(_pos_rahmen(), _scan(_zeile("SOLUSD", 110.5)), {}, None)
    assert [e.marke for e in ev] == ["TP1-vorlaeufig"]
    assert "nicht bestätigt" in ev[0].text and "keine frischen" in ev[0].text
    # Auch beim nächsten Lauf ohne Daten: keine Wiederholung, keine Bestätigung.
    _, ev2, _ = pruefe_depot(_pos_rahmen(), _scan(_zeile("SOLUSD", 110.9)), stand, None)
    assert ev2 == []


def test_vorlaeufiger_hinweis_wird_zurueckgenommen_wenn_der_kurs_zurueckfaellt() -> None:
    stand, _, _ = pruefe_depot(_pos_rahmen(), _scan(_zeile("SOLUSD", 110.5)), {}, {"SOLUSD": 108.0})
    _, ev, _ = pruefe_depot(_pos_rahmen(), _scan(_zeile("SOLUSD", 104.0)), stand, {"SOLUSD": 108.0})
    assert [e.marke for e in ev] == ["TP1-zurueck"]
    assert "zurückgenommen" in ev[0].text


def test_stop_alarm_bleibt_direkt_auch_mit_stundenlogik() -> None:
    _, ev, _ = pruefe_depot(_pos_rahmen(), _scan(_zeile("SOLUSD", 89.0)), {}, {"SOLUSD": 95.0})
    assert [e.art for e in ev] == ["stop"]


def _kerzen(t0: float, n: int = 4, c: float = 100.0) -> list[list[float]]:
    return [[t0 + 3600 * i, c, c, c, c + i, c, 1, 1] for i in range(n)]


def test_stundenschluss_nur_aus_frischen_vollstaendigen_kerzen() -> None:
    """Fehlende, lückenhafte oder veraltete Stundenkerzen dürfen nichts bestätigen."""
    k = _kerzen(1_000_000)  # Zeiten t0, +1h, +2h, +3h; die letzte läuft noch
    jetzt = 1_000_000 + 3600 * 3 + 600  # 10 Min. in der letzten Kerze
    assert letzter_schluss(k, jetzt) == 102.0  # vorletzte (t0+2h) ist fertig
    assert letzter_schluss(k[:2], jetzt) is None  # zu wenige Kerzen
    luecke = [k[0], k[1], k[3]]
    assert letzter_schluss(luecke, jetzt) is None  # Lücke
    assert letzter_schluss(k, jetzt + 3 * 3600) is None  # veraltet
    assert letzter_schluss(k, 1_000_000 + 3600 * 3 - 60) is None  # fertige Kerze noch nicht zu
    assert letzter_schluss([["x"] * 5] * 3, jetzt) is None  # Müll


def test_trend_von_und_rsi() -> None:
    from trading_agent.portfolio_intel.depot_stops import rsi14, trend_von

    steigend = [100 + i * 0.5 for i in range(260)]
    fallend = [200 - i * 0.5 for i in range(260)]
    assert trend_von(steigend) == "auf"
    assert trend_von(fallend) == "ab"
    assert trend_von(steigend[:100]) is None  # zu wenig Daten: nichts behaupten
    assert rsi14(steigend) == 100.0
    assert rsi14(fallend) < 5
    assert rsi14(steigend[:10]) is None


def test_lage_zeile_zeigt_konflikt_zum_trend_statt_ihn_zu_verschweigen() -> None:
    from trading_agent.portfolio_intel.depot_stops import lage_zeile

    auf = {"d1": "auf", "h4": "auf", "rsi_h1": 71.0}
    t = lage_zeile(True, 110.0, 120.0, 100.0, auf, "$")
    assert "Konflikt" in t and "Teilverkauf kostet Upside" in t
    assert "RSI (1 Std.) 71" in t and "nächsten Ziel" in t and "Stop" in t
    dreht = lage_zeile(True, 110.0, None, 100.0, {"d1": "ab", "h4": "auf"}, "$")
    assert "stützt den Teilverkauf" in dreht
    keine = lage_zeile(True, 110.0, None, 100.0, None, "$")
    assert "keine Daten" in keine
    assert "keine Pflicht" in keine


def test_bestaetigter_teilverkauf_enthaelt_die_lage() -> None:
    kontext = {"SOLUSD": {"d1": "auf", "h4": "auf", "rsi_h1": 66.0}}
    _, ev, _ = pruefe_depot(
        _pos_rahmen(), _scan(_zeile("SOLUSD", 110.5)), {}, {"SOLUSD": 110.2}, kontext
    )
    ziel = next(e for e in ev if e.art == "ziel")
    assert "Lage:" in ziel.text and "Konflikt" in ziel.text
    assert "Stundenschluss bestätigt" in ziel.text


def test_geschlossene_schluesse_alle_intervalle() -> None:
    from scripts.depot_wache import geschlossene_schluesse

    t0 = 2_000_000.0
    k4 = [[t0 + 14400 * i, 1, 1, 1, 50 + i, 1, 1, 1] for i in range(5)]
    jetzt = t0 + 14400 * 4 + 900
    assert geschlossene_schluesse(k4, 14400, jetzt) == [50.0, 51.0, 52.0, 53.0]
    assert geschlossene_schluesse(k4, 14400, jetzt + 14400 * 3) is None  # veraltet
    assert geschlossene_schluesse(k4, 3600, jetzt) is None  # falscher Takt


def test_schluesse_aus_ohlcv_nur_wenn_frisch() -> None:
    from datetime import timedelta
    from types import SimpleNamespace

    from scripts.depot_wache import schluesse_aus_ohlcv

    jetzt = datetime(2026, 10, 13, 15, 10, tzinfo=UTC)
    bars = [
        SimpleNamespace(open_time=jetzt - timedelta(hours=h + 1), close_time=jetzt - timedelta(hours=h), close=100 + h)
        for h in (3, 2, 1)
    ]
    ok = schluesse_aus_ohlcv(bars, jetzt, 3 * 3600)
    assert ok is not None and len(ok) == 3
    assert schluesse_aus_ohlcv(bars, jetzt + timedelta(hours=5), 3 * 3600) is None  # veraltet
    assert schluesse_aus_ohlcv(bars[:2], jetzt, 3 * 3600) is None  # zu wenig
    zukunft = [*bars[:2], SimpleNamespace(open_time=jetzt, close_time=jetzt + timedelta(hours=1), close=1.0)]
    assert schluesse_aus_ohlcv(zukunft, jetzt, 3 * 3600) is None  # Kerze noch nicht zu


def _nk_zeile(kurs: float, **plan_extra: float) -> dict:
    """Scan-Zeile mit gültigem Long-Setup und bestandenem Alarm-Tor (Einstieg 100, Stop 95)."""
    z = _zeile("SOLUSD", kurs, richtung="long")
    z["handelbar"] = True
    z["alarm"] = {"ja": True, "grund": ""}
    z["plan"] = {"einstieg": 100.0, "stop": 95.0, "tp1": 110.0, "tp2": 120.0, **plan_extra}
    return z


def _pos_nk(gross: bool = True) -> list[dict]:
    """SOL-Position (Stop 85) plus — wenn ``gross`` — ein grosses ETH-Depot, damit das
    Risiko des Nachkaufs im Rahmen bleibt (6 SOL allein waeren das ganze Depot)."""
    p = [{"sym": "SOLUSD", "menge": 6, "einstieg": 90.0, "plan": {"stop": 85.0, "quelle": "selbst"}}]
    if gross:
        p.append({"sym": "ETHUSD", "menge": 100, "einstieg": 900.0, "plan": {"stop": 990.0, "quelle": "selbst"}})
    return p


def _nk_scan(z: dict) -> dict:
    return _scan(z, _zeile("ETHUSD", 1000.0))


def test_nachkauf_zweistufig_vorlaeufig_dann_bestaetigt() -> None:
    gut = {"SOLUSD": {"d1": "auf", "h4": "auf", "rsi_h1": 60.0}}
    stand, ev, _ = pruefe_depot(_pos_nk(), _nk_scan(_nk_zeile(101.0)), {}, {"SOLUSD": 99.0}, gut)
    assert [e.art for e in ev if e.art.startswith("nachkauf")] == ["nachkauf_vorlaeufig"]
    assert "noch nicht über der Marke" in next(e for e in ev if e.art.startswith("nachkauf")).text
    # Nach bestätigtem Stundenschluss über der Marke: bestätigter Hinweis mit Lage und Obergrenze.
    _, ev2, _ = pruefe_depot(_pos_nk(), _nk_scan(_nk_zeile(102.0)), stand, {"SOLUSD": 101.5}, gut)
    nk = [e for e in ev2 if e.art == "nachkauf"]
    assert len(nk) == 1
    assert "bestätigt" in nk[0].text and "Obergrenze: ca. 2 Stück" in nk[0].text
    assert "Trend stützt den Nachkauf" in nk[0].text and "Invalidierung" in nk[0].text


def test_nachkauf_trendkonflikt_wird_gezeigt_und_bestaetigt_nichts() -> None:
    dagegen = {"SOLUSD": {"d1": "ab", "h4": "auf", "rsi_h1": 55.0}}
    _, ev, _ = pruefe_depot(_pos_nk(), _nk_scan(_nk_zeile(102.0)), {}, {"SOLUSD": 101.5}, dagegen)
    nk = [e for e in ev if e.art.startswith("nachkauf")]
    assert [e.art for e in nk] == ["nachkauf_vorlaeufig"]
    assert "Trend (D1/H4) steht dagegen" in nk[0].text and "Konflikt" in nk[0].text


def test_nachkauf_nie_ohne_frische_daten_bestaetigt() -> None:
    _, ev, _ = pruefe_depot(_pos_nk(), _nk_scan(_nk_zeile(102.0)), {}, None, None)
    nk = [e for e in ev if e.art.startswith("nachkauf")]
    assert [e.art for e in nk] == ["nachkauf_vorlaeufig"]
    assert "keine frischen" in nk[0].text and "keine Daten" in nk[0].text


def test_nachkauf_nur_bei_gueltiger_these_und_crv() -> None:
    gut = {"SOLUSD": {"d1": "auf", "h4": "auf", "rsi_h1": 60.0}}
    # Tor nicht bestanden → kein Nachkauf
    z = _nk_zeile(102.0)
    z["alarm"] = {"ja": False, "grund": "Note zu schwach"}
    _, ev, _ = pruefe_depot(_pos_nk(), _nk_scan(z), {}, {"SOLUSD": 101.5}, gut)
    assert [e for e in ev if e.art.startswith("nachkauf")] == []
    # Chance-Risiko vom jetzigen Kurs unter 2 (Ziel zu nah) → kein Hinterherlaufen
    z2 = _nk_zeile(108.0, tp2=111.0)
    _, ev, _ = pruefe_depot(_pos_nk(), _nk_scan(z2), {}, {"SOLUSD": 107.5}, gut)
    assert [e for e in ev if e.art.startswith("nachkauf")] == []
    # Invalidierung gerissen → kein Nachkauf
    z3 = _nk_zeile(94.0)
    _, ev, _ = pruefe_depot(_pos_nk(), _nk_scan(z3), {}, {"SOLUSD": 94.0}, gut)
    assert [e for e in ev if e.art.startswith("nachkauf")] == []
    # Gegenrichtung (Short-Setup bei Long-Position) → kein Nachkauf
    z4 = _nk_zeile(102.0)
    z4["richtung"] = "short"
    _, ev, _ = pruefe_depot(_pos_nk(), _nk_scan(z4), {}, {"SOLUSD": 101.5}, gut)
    assert [e for e in ev if e.art.startswith("nachkauf")] == []


def test_nachkauf_vorlaeufig_wird_zurueckgenommen() -> None:
    gut = {"SOLUSD": {"d1": "auf", "h4": "auf", "rsi_h1": 60.0}}
    stand, _, _ = pruefe_depot(_pos_nk(), _nk_scan(_nk_zeile(101.0)), {}, {"SOLUSD": 99.0}, gut)
    _, ev, _ = pruefe_depot(_pos_nk(), _nk_scan(_nk_zeile(98.0)), stand, {"SOLUSD": 99.0}, gut)
    assert [e.art for e in ev if e.art.startswith("nachkauf")] == ["nachkauf_zurueck"]


def test_stop_bleibt_direkt_ohne_jede_bestaetigung() -> None:
    _, ev, _ = pruefe_depot(_pos_nk(), _nk_scan(_nk_zeile(84.0)), {}, None, None)
    assert ev[0].art == "stop"


# --------------------------------------------------------------------------- Risiko / H4 / Neustart


def test_nachkauf_ohne_risikospielraum_wird_nie_bestaetigt() -> None:
    """Ein CRV >= 2 reicht nicht: ist die Position (fast) das ganze Depot, bleibt es vorläufig."""
    gut = {"SOLUSD": {"d1": "auf", "h4": "auf", "rsi_h1": 60.0}}
    _, ev, _ = pruefe_depot(_pos_nk(gross=False), _scan(_nk_zeile(102.0)), {}, {"SOLUSD": 101.5}, gut)
    nk = [e for e in ev if e.art.startswith("nachkauf")]
    assert [e.art for e in nk] == ["nachkauf_vorlaeufig"]
    assert "Risiko der Position" in nk[0].text and "nicht bestätigt" in nk[0].text


def test_nachkauf_summenrisiko_des_depots_wird_geprueft() -> None:
    gut = {"SOLUSD": {"d1": "auf", "h4": "auf", "rsi_h1": 60.0}}
    pos = _pos_nk()
    pos[1]["plan"]["stop"] = 700.0  # ETH-Stop weit weg: offenes Risiko ~30 % des Depots
    _, ev, _ = pruefe_depot(pos, _nk_scan(_nk_zeile(102.0)), {}, {"SOLUSD": 101.5}, gut)
    nk = [e for e in ev if e.art.startswith("nachkauf")]
    assert [e.art for e in nk] == ["nachkauf_vorlaeufig"]
    assert "offene Stop-Risiko" in nk[0].text or "Risiko der Position" in nk[0].text


def test_nachkauf_unvollstaendiges_depot_bestaetigt_nichts() -> None:
    gut = {"SOLUSD": {"d1": "auf", "h4": "auf", "rsi_h1": 60.0}}
    pos = [*_pos_nk(), {"sym": "UNBEKANNT", "menge": 1.0}]
    _, ev, _ = pruefe_depot(pos, _nk_scan(_nk_zeile(102.0)), {}, {"SOLUSD": 101.5}, gut)
    nk = [e for e in ev if e.art.startswith("nachkauf")]
    assert [e.art for e in nk] == ["nachkauf_vorlaeufig"]
    assert "unvollständig" in nk[0].text


def test_fehlendes_h4_wird_als_einschraenkung_gezeigt() -> None:
    aktie = {"SOLUSD": {"d1": "auf", "h4": None, "rsi_h1": 60.0}}
    _, ev, _ = pruefe_depot(_pos_nk(), _nk_scan(_nk_zeile(102.0)), {}, {"SOLUSD": 101.5}, aktie)
    nk = next(e for e in ev if e.art.startswith("nachkauf"))
    assert "Einschränkung: H4-Daten fehlen" in nk.text


def test_zustand_bleibt_nach_neustart_und_erneutem_scan_konsistent() -> None:
    """Stand wird als JSON gespeichert und neu geladen (Neustart): gleicher Scan = keine Wiederholung,
    kein Sprung von vorläufig auf bestätigt ohne frischen Schluss, Rücknahme nur einmal."""
    import json

    gut = {"SOLUSD": {"d1": "auf", "h4": "auf", "rsi_h1": 60.0}}

    def lauf(stand: dict, kurs: float, schluss: float | None) -> tuple[dict, list]:
        stand = json.loads(json.dumps(stand))  # Neustart: nur das Gespeicherte lebt weiter
        s = {"SOLUSD": schluss} if schluss is not None else None
        st, ev, _ = pruefe_depot(_pos_nk(), _nk_scan(_nk_zeile(kurs)), stand, s, gut)
        return st, [e.art for e in ev if e.art.startswith("nachkauf")]

    st, a = lauf({}, 101.0, 99.0)
    assert a == ["nachkauf_vorlaeufig"]
    st, a = lauf(st, 101.0, 99.0)  # Neustart + gleicher Scan
    assert a == []
    st, a = lauf(st, 101.0, None)  # Stundendaten fehlen → weiter nicht bestätigt
    assert a == []
    st, a = lauf(st, 102.0, 101.5)  # frischer Schluss über der Marke → bestätigt
    assert a == ["nachkauf"]
    st, a = lauf(st, 102.0, 101.5)  # Neustart + erneuter Scan → keine zweite Bestätigung
    assert a == []
    st, a = lauf(st, 98.0, 101.5)  # Kurs fällt zurück: bestätigter Stand wird nicht als "zurück" gemeldet
    assert "nachkauf" not in a


def test_teilverkauf_vorlaeufig_bestaetigt_zurueck_ueber_neustarts() -> None:
    import json

    def lauf(stand: dict, kurs: float, schluss: float | None) -> tuple[dict, list[str]]:
        stand = json.loads(json.dumps(stand))  # Neustart
        s = {"SOLUSD": schluss} if schluss is not None else None
        st, ev, _ = pruefe_depot(_pos_rahmen(), _scan(_zeile("SOLUSD", kurs)), stand, s)
        return st, [e.marke for e in ev if e.art.startswith("ziel")]

    st, a = lauf({}, 110.5, 108.0)
    assert a == ["TP1-vorlaeufig"]
    st, a = lauf(st, 110.5, 108.0)  # Neustart, gleiche Lage
    assert a == []
    st, a = lauf(st, 110.6, None)  # Stundendaten weg: keine Bestätigung
    assert a == []
    st, a = lauf(st, 104.0, 108.0)  # Rücknahme
    assert a == ["TP1-zurueck"]
    st, a = lauf(st, 104.0, 108.0)  # Neustart: Rücknahme nicht doppelt
    assert a == []
    st, a = lauf(st, 111.0, 110.4)  # frischer Schluss über Ziel → bestätigt
    assert a == ["TP1"]
    st, a = lauf(st, 111.0, 110.4)  # Neustart + erneuter Scan: kein zweiter Verkaufshinweis
    assert a == []
