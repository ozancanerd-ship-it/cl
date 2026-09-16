"""Der Depot-Waechter — meldet das Wichtige, schweigt beim Rest, verraet nichts.

Ozan: „Ich kriege nur die Alarme, wenn ich auf der App drauf bin. Fuer jeden Buy- oder
Verkauf-Alarm moechte ich auch eine Mail — aber nicht, dass ich vollgespammt werde."

Drei Zusagen stehen hier fest:
  1. Stop, Ziel und ein bedrohlicher Knock-out-Puffer melden sich.
  2. Jede Meldung kommt genau EINMAL — der Stand ueberlebt den Lauf.
  3. Eine Kursbewegung ohne getroffene Marke meldet gar nichts.
"""

from __future__ import annotations

import base64
import json

from scripts.depot_wache import (
    PUFFER_KRITISCH_PCT,
    depot_lesen,
    ereignisse_fuer,
    kurs_fuer,
    stand_laden,
    stand_sichern,
)


def _code(positionen: list[dict]) -> str:
    roh = json.dumps({"v": 1, "t": 0, "p": positionen})
    return base64.b64encode(roh.encode("utf-8")).decode("ascii")


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


def test_derselbe_coin_wird_auch_unter_der_anderen_endung_gefunden() -> None:
    """Bybit fuehrt USDT, Kraken USD. Sonst faellt genau die Position aus der
    Ueberwachung, die von der anderen Boerse eingetragen wurde."""
    tabelle = {"SOLUSDT": 98.0, "NVDA": 184.0}
    assert kurs_fuer("SOLUSD", tabelle) == 98.0
    assert kurs_fuer("SOL", tabelle) == 98.0
    assert kurs_fuer("NVDA", tabelle) == 184.0
    assert kurs_fuer("GIBTESNICHT", tabelle) is None


def test_stop_gerissen_meldet_sich() -> None:
    pos = {"sym": "SOLUSD", "plan": {"stop": 90.0, "tp1": 120.0}}
    e = ereignisse_fuer(pos, 89.5, {})
    assert [x["art"] for x in e] == ["stop"]


def test_ueber_dem_stop_meldet_sich_nichts() -> None:
    pos = {"sym": "SOLUSD", "plan": {"stop": 90.0, "tp1": 120.0}}
    assert ereignisse_fuer(pos, 95.0, {}) == []


def test_ziel_meldet_sich_einmal_je_marke() -> None:
    pos = {"sym": "SOLUSD", "plan": {"stop": 90.0, "tp1": 120.0, "tp2": 130.0}}
    assert [x["marke"] for x in ereignisse_fuer(pos, 121.0, {})] == ["TP1"]
    pos["erledigt"] = ["TP1"]
    assert [x["marke"] for x in ereignisse_fuer(pos, 121.0, {})] == []
    assert [x["marke"] for x in ereignisse_fuer(pos, 131.0, {})] == ["TP2"]


def test_gerissener_stop_verdraengt_das_ziel() -> None:
    """Ist der Stop durch, ist die Frage nach dem Ziel erledigt — nicht beides melden."""
    pos = {"sym": "X", "plan": {"stop": 100.0, "tp1": 90.0}, "hebel_richtung": "short"}
    e = ereignisse_fuer(pos, 101.0, {})
    assert [x["art"] for x in e] == ["stop"]


def test_knock_out_puffer_warnt_bevor_alles_weg_ist() -> None:
    """Der Fall, gegen den kein Stop hilft: die Schwelle greift auch nachts.

    Das ist zugleich die Gegenprobe zu einem Fehler, der beim Schreiben drinstand — die
    Kurstabelle wurde nicht durchgereicht, damit lief diese Pruefung nie an."""
    pos = {"sym": "TURBO-TSMC-LONG", "basis": "TSM", "ko": 358.03}
    knapp = 358.03 / (1 - (PUFFER_KRITISCH_PCT - 1) / 100)  # ~5 % Puffer
    assert [x["art"] for x in ereignisse_fuer(pos, None, {"TSM": knapp})] == ["puffer"]
    weit = 358.03 / (1 - 25 / 100)  # 25 % Puffer
    assert ereignisse_fuer(pos, None, {"TSM": weit}) == []


def test_ausgeknockt_meldet_keinen_puffer_mehr() -> None:
    pos = {"sym": "TURBO", "basis": "TSM", "ko": 400.0}
    assert ereignisse_fuer(pos, None, {"TSM": 390.0}) == []


def test_der_stand_ueberlebt_den_lauf(tmp_path) -> None:
    """Ohne das faengt jeder Lauf bei null an und meldet alles neu — genau der Spam."""
    p = tmp_path / "stand.json"
    stand_sichern(str(p), {"SOLUSD|stop|": "2026-09-16T20:00:00+00:00"})
    assert stand_laden(str(p)) == {"SOLUSD|stop|": "2026-09-16T20:00:00+00:00"}


def test_fehlender_stand_ist_kein_fehler(tmp_path) -> None:
    assert stand_laden(str(tmp_path / "gibtesnicht.json")) == {}


def test_position_ohne_plan_meldet_nichts() -> None:
    """Ein Depoteintrag ohne Marken ist kein Alarm, sondern eine Luecke — und die
    steht in der App, nicht im Postfach."""
    assert ereignisse_fuer({"sym": "KASUSD"}, 0.03, {}) == []
