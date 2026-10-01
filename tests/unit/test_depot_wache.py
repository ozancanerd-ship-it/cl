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

from scripts.depot_wache import depot_lesen, neue_meldungen, stand_laden, stand_sichern

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
    stand, ev, _ = pruefe_depot(pos, _scan(_zeile("SOLUSD", 111.0)), {})
    assert [e.art for e in ev] == ["ziel"]
    assert stand["positionen"]["SOLUSD|"]["stop"] == 100.0
    # Dasselbe Ziel meldet sich nicht noch einmal.
    stand, ev, _ = pruefe_depot(pos, _scan(_zeile("SOLUSD", 112.0)), stand)
    assert [e.art for e in ev if e.art == "ziel"] == []


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
    _, ev, _ = pruefe_depot(pos, _scan(_zeile("SOLUSD", 111.0)), {})
    assert [e.art for e in ev] == ["ziel"]


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
