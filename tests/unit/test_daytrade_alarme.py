"""Tageshandel-Alarme (Krypto-Ausbruchswelle + Gold experimentell).

WARUM ES DIESE TESTS GIBT

Ozan, 11.10. 22:33: "ich moechte auch Alarme ... fuer die Daytrades normal und auch fuer
Krypto und fuer Gold". Grundlage: ``scripts/daytrade_studie.py`` / ``docs/DAYTRADE-STUDIE-
2026-10.md``. Festgehalten wird:

1. Studie und Live-Alarm rechnen dasselbe (Backtest ≡ Paper): ``daytrade_ausbruch.verfolge``
   liefert fuer dieselben Kerzen dasselbe R wie ``daytrade_studie.ergebnis``.
2. Ein Coin-Ausbruch ohne Welle (< 12 Coins) loest KEINEN Alarm aus — genau das war in der
   Studie negativ.
3. Eine Welle ist EINE gebuendelte Meldung, nicht 24 — und beim naechsten Lauf mit denselben
   Kerzen kommt nichts doppelt.
4. Der Ausstieg kommt als eigener Alarm mit R; der Trade landet in der Forward-Bilanz.
5. Gold-Ausbrueche laufen seit 12.10. nur still als Paper mit (lohnt sich nicht belegt).
6. Die laufende (unfertige) Kraken-Kerze wird nie ausgewertet.
7. "Lohnt sich"-Tor (Ozan, 12.10.: "Benachrichtigung fuer Entry ... aber nur wenn es sich
   lohnt"): ein knapper Ausbruch (< 0,25 ATR ueber dem 48-Std.-Hoch) kommt nicht als Push,
   laeuft aber als stille Gegenprobe mit — und sein Ausstieg kommt auch nicht als Push.
"""

from __future__ import annotations

import json
import math
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import daytrade_alarme as da
import daytrade_studie as studie

from trading_agent.strategy.setups import daytrade_ausbruch as m

_T0 = datetime(2026, 9, 1, tzinfo=UTC)


def _reihe(n: int = 300, *, ausbruch: bool = True, nachher: list[tuple] | None = None, basis=100.0):
    """Ruhiger Aufwaertstrend (ueber EMA 200), letzte Kerze bricht ueber das 48-Std.-Hoch."""
    k = []
    for i in range(n):
        mid = basis * (1 + 0.0004 * i) + basis * 0.004 * math.sin(i / 3)
        o, c = mid, mid * 1.0005
        k.append((_T0 + timedelta(hours=i), o, max(o, c) * 1.003, min(o, c) * 0.997, c))
    if ausbruch:
        hoch = max(x[2] for x in k[-48:])
        t = _T0 + timedelta(hours=n)
        c = hoch * 1.01
        k.append((t, k[-1][4], c * 1.001, k[-1][4] * 0.999, c))
    for h, lo, c in nachher or []:
        t = k[-1][0] + timedelta(hours=1)
        o = k[-1][4]
        k.append((t, o, max(h, o, c), min(lo, o, c), c))
    return k


# ------------------------------------------------------------------ 1. Paritaet
@pytest.mark.parametrize(
    "nachher",
    [
        [(1.05, 0.90, 0.91)],  # Stop
        [(1.02, 1.00, 1.01)] * 80,  # Zeitausstieg
        [(1.06, 1.04, 1.05)] * 30 + [(1.05, 0.95, 0.96)] + [(1.0, 0.99, 0.995)] * 3,  # Trail
    ],
)
def test_live_und_studie_rechnen_gleich(nachher) -> None:
    k = _reihe(ausbruch=True)
    signal = len(k) - 1
    ref = k[-1][4]
    k = k + [
        (
            k[-1][0] + timedelta(hours=1 + j),
            ref * h if j == 0 else ref * c,
            ref * h,
            ref * lo,
            ref * c,
        )
        for j, (h, lo, c) in enumerate(nachher)
    ]
    k = [(t, o, max(o, h, c), min(o, lo, c), c) for t, o, h, lo, c in k]
    a = m.atr(k, 24)
    stop = k[signal + 1][1] - 2 * a[signal]
    live = m.verfolge(k, signal + 1, stop)
    alt = studie.ergebnis(k, signal + 1, 1, stop, max_bars=72, trail_n=24, kosten=0.0025)
    assert live["status"] == "geschlossen" and alt is not None
    assert live["r"] == pytest.approx(alt[0])
    assert live["i_aus"] == alt[1]


def test_signal_braucht_neues_hoch_und_ema() -> None:
    k = _reihe(ausbruch=True)
    e = m.ema([x[4] for x in k], 200)
    assert m.ist_signal(k, len(k) - 1, e, markt_ok=True)
    assert not m.ist_signal(k, len(k) - 1, e, markt_ok=False)  # Bitcoin unter EMA 200
    k2 = _reihe(ausbruch=False)
    e2 = m.ema([x[4] for x in k2], 200)
    assert not m.ist_signal(k2, len(k2) - 1, e2, markt_ok=True)


# ------------------------------------------------------------------ 2.-5. Alarme
def _alle(n_ausbruch: int, gold: bool = False) -> dict:
    d = {}
    for j, sym in enumerate(da.COINS):
        d[sym] = _reihe(ausbruch=j < n_ausbruch or sym == "BTC", basis=100.0 + j)
    d["PAXG"] = _reihe(ausbruch=gold, basis=4000.0)
    # gleiche Zeitachse: Coins ohne Ausbruch bekommen eine ruhige Schlusskerze
    for k in d.values():
        if len(k) == 300:
            t = k[-1][0] + timedelta(hours=1)
            c = k[-1][4]
            k.append((t, c, c * 1.001, c * 0.999, c))
    return d


def test_ohne_welle_kein_alarm() -> None:
    _, alarme, welle = da.fortschreiben(None, _alle(5))
    assert welle < da.MIN_WELLE
    assert alarme["neu"] == []
    assert da.alarm_texte(alarme, welle) == []


def test_welle_ist_eine_gebuendelte_meldung_und_kommt_nur_einmal() -> None:
    daten = _alle(20)
    stand, alarme, welle = da.fortschreiben(None, daten)
    assert welle >= da.MIN_WELLE
    assert len(alarme["neu"]) >= da.MIN_WELLE
    texte = da.alarm_texte(alarme, welle)
    assert len(texte) == 1
    assert "lohnt sich" in texte[0]["titel"]
    assert "Warum es sich lohnt" in texte[0]["text"]
    assert "Bybit" in texte[0]["text"] and "Kandidat" in texte[0]["text"]
    assert "Größe ≈" in texte[0]["text"]

    stand2, alarme2, _ = da.fortschreiben(stand, daten)
    assert alarme2 == {"neu": [], "aus": []}
    assert set(stand2["offen"]) == set(stand["offen"])


def test_ausstieg_kommt_mit_r_und_landet_in_der_bilanz() -> None:
    daten = _alle(20)
    stand, _, _ = da.fortschreiben(None, daten)
    # naechste Kerze: Einstieg; danach Absturz unter den Stop
    for k in daten.values():
        t = k[-1][0]
        c = k[-1][4]
        k.append((t + timedelta(hours=1), c, c * 1.002, c * 0.998, c))
        k.append((t + timedelta(hours=2), c, c, c * 0.80, c * 0.81))
    stand2, alarme, welle = da.fortschreiben(stand, daten)
    assert alarme["aus"], "Stop muss als Ausstieg gemeldet werden"
    assert all(t["grund"] == "STOP" and t["r"] < -1 for t in alarme["aus"])
    texte = da.alarm_texte(alarme, welle)
    assert any("verkaufen" in t["titel"] for t in texte)
    b = da.bilanz(stand2, "coin")
    assert b["anzahl"] == len(alarme["aus"])
    assert stand2["offen"] == {} or all(
        s not in stand2["offen"] for s in (t["sym"] for t in alarme["aus"])
    )


def test_gold_laeuft_still_als_paper_ohne_push() -> None:
    stand, alarme, welle = da.fortschreiben(None, _alle(3, gold=True))
    assert da.alarm_texte(alarme, welle) == []
    assert "PAXG" in stand["offen"]
    assert stand["offen"]["PAXG"]["alarm"] is False


def _knapp(k: list) -> None:
    """Letzte Kerze schliesst nur hauchduenn ueber dem 48-Std.-Hoch."""
    t, o, h, lo, _ = k[-1]
    hoch = max(x[2] for x in k[-49:-1])
    c = hoch * 1.0001
    k[-1] = (t, o, max(h, c), lo, c)


def test_knapper_ausbruch_kommt_nicht_als_push_aber_als_gegenprobe() -> None:
    daten = _alle(20)
    _knapp(daten["ETH"])
    stand, alarme, welle = da.fortschreiben(None, daten)
    eth = stand["offen"]["ETH"]
    assert eth["lohnt"] is False and eth["alarm"] is False
    assert eth["ueberhang_atr"] < da.m.MIN_UEBERHANG_ATR
    assert all(t["sym"] != "ETH" for t in alarme["neu"])
    texte = da.alarm_texte(alarme, welle)
    assert "(ETH/USDT)" not in texte[0]["text"]
    # Absturz: der stille Trade wird abgerechnet, aber nicht gemeldet
    for k in daten.values():
        t, c = k[-1][0], k[-1][4]
        k.append((t + timedelta(hours=1), c, c * 1.002, c * 0.998, c))
        k.append((t + timedelta(hours=2), c, c, c * 0.80, c * 0.81))
    stand2, alarme2, _ = da.fortschreiben(stand, daten)
    assert all(t["sym"] != "ETH" for t in alarme2["aus"])
    assert da.bilanz(stand2, "coin", alarm=False)["anzahl"] == 1
    assert da.bilanz(stand2, "coin", alarm=True)["anzahl"] == len(alarme2["aus"])


def test_alte_trades_ohne_alarm_feld_melden_ihren_ausstieg() -> None:
    daten = _alle(20)
    stand, _, _ = da.fortschreiben(None, daten)
    for t in stand["offen"].values():
        t.pop("alarm", None)  # Stand von vor dem 12.10.
    for k in daten.values():
        t, c = k[-1][0], k[-1][4]
        k.append((t + timedelta(hours=1), c, c * 1.002, c * 0.998, c))
        k.append((t + timedelta(hours=2), c, c, c * 0.80, c * 0.81))
    _, alarme, _ = da.fortschreiben(stand, daten)
    assert {t["sym"] for t in alarme["aus"]} >= {"BTC", "ETH"}


def test_analyse_liefert_die_marken_der_regel() -> None:
    daten = _alle(20)
    daten["TAO"] = _reihe(ausbruch=True, basis=300.0)
    a = da.analysen(daten, da.btc_filter(daten))
    eth = a["ETH"]
    for f in ("kurs", "ema200", "marke48", "tief24", "atr24", "stop_bei_signal", "status"):
        assert f in eth
    assert eth["signal"] and eth["status"] == "ausbruch" and eth["lohnt"]
    assert eth["stop_bei_signal"] == pytest.approx(eth["kurs"] - 2 * eth["atr24"])
    assert a["PAXG"]["klasse"] == "gold" and a["PAXG"]["lohnt"] is False
    assert a["TAO"]["klasse"] == "extra" and a["TAO"]["lohnt"] is False


def test_extra_werte_zaehlen_nicht_zur_welle() -> None:
    assert "TAO" in da.EXTRA and "TAO" not in da.COINS


def test_groesse_ist_gedeckelt() -> None:
    assert da._groesse({"signal_kurs": 100.0, "stop": 99.0}) == pytest.approx(
        10.0
    )  # 1 % -> 25 % -> Deckel
    assert da._groesse({"signal_kurs": 100.0, "stop": 95.0}) == pytest.approx(5.0)


# ------------------------------------------------------------------ 6. Kraken
def test_laufende_kraken_kerze_wird_verworfen(monkeypatch) -> None:
    jetzt = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    reihe = [
        [int((jetzt - timedelta(hours=2)).timestamp()), "1", "2", "0.5", "1.5", "1", "1", 1],
        [int((jetzt - timedelta(hours=1)).timestamp()), "1", "2", "0.5", "1.5", "1", "1", 1],
        [int(jetzt.timestamp()), "1", "2", "0.5", "1.5", "1", "1", 1],  # laeuft noch
    ]

    class _R:
        def __init__(self, body):
            self.body = body

        def read(self):
            return self.body

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(
        da.urllib.request,
        "urlopen",
        lambda url, timeout=30: _R(
            json.dumps({"error": [], "result": {"X": reihe, "last": 1}}).encode()
        ),
    )
    k = da.kraken_kerzen("XBTUSD")
    assert len(k) == 2
    assert k[-1][0] == jetzt - timedelta(hours=1)
