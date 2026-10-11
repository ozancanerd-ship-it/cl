"""``scripts/gold_daytrading_status.py`` — die ehrliche Gold-Tageshandel-Seite.

WARUM ES DIESE TESTS GIBT

Ozan, 11.10. 17:18/17:41: "mach ... extra seite mit gold day trade auf kraken oder
crypto". Zwei vorherige Studien fanden KEINEN Edge fuer Intraday-Gold und
Krypto-Mean-Reversion (siehe die Docs, die unten zitiert werden). Das Skript zeigt
deshalb S9 (``SETUP-BREAKOUT-RETEST-01``, Status ``IN_VALIDATION`` — nicht bewiesen).
20:49: "will auch dafuer alarme bekommen" — seitdem fuehrt es je Setup einen
Paper-Trade vorwaerts und meldet Setup/TP1/TP2/Stop/Zeit, IMMER als experimentell
markiert (Tests dazu ganz unten).

Festgehalten wird:

1. Zu wenig Historie → ehrlich "unbekannt", kein erfundener Zustand, aber die
   Ehrlichkeits-Felder (``ist_experimentell``, ``hinweis``) sind trotzdem da.
2. Eine reale (flache, trendlose) H4/D1-Reihe laeuft durch die ECHTE
   ``detect_breakout_retest``-Berechnung ohne Absturz und liefert ehrlich einen
   Nicht-ARMED-Zustand.
3. Ein ARMED-Ergebnis (per Monkeypatch des Detektors — der Detektor selbst ist in
   ``test_breakout_retest.py`` ausfuehrlich getestet, hier geht es nur um die
   ehrliche JSON-Uebersetzung) bringt Entry/SL/TP/RR/Confidence UND die Warnung
   "nicht bewiesen, nur Paper" mit — niemals eine Kaufempfehlung.
4. ``_bauen`` schreibt die beiden Studien IMMER mit, auch wenn Yahoo ausfaellt oder
   zu wenig Daten liefert — die Seite bleibt informativ, auch ohne aktuellen Kurs.
5. ``main`` schreibt eine gueltige JSON-Datei an den angegebenen Pfad.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import gold_daytrading_status as skript

from trading_agent.core.enums import Direction, Timeframe
from trading_agent.core.models import OHLCV
from trading_agent.core.time import bar_close_time
from trading_agent.strategy.setups.breakout_retest import BreakoutRetestReport, BreakoutState

_T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _h4_bar(t: datetime, o: float, h: float, low: float, c: float) -> OHLCV:
    return OHLCV(
        instrument="XAUUSD-YF",
        timeframe=Timeframe.H4,
        open_time=t,
        close_time=bar_close_time(t, Timeframe.H4),
        open=o,
        high=h,
        low=low,
        close=c,
        volume=1.0,
        source="test",
    )


def _d1_bar(t: datetime, o: float, h: float, low: float, c: float) -> OHLCV:
    return OHLCV(
        instrument="XAUUSD-YF",
        timeframe=Timeframe.D1,
        open_time=t,
        close_time=bar_close_time(t, Timeframe.D1),
        open=o,
        high=h,
        low=low,
        close=c,
        volume=1.0,
        source="test",
    )


def test_zu_wenig_historie_bleibt_ehrlich() -> None:
    h4 = [_h4_bar(_T0 + timedelta(hours=4 * k), 100, 101, 99, 100) for k in range(5)]
    d1 = [_d1_bar(_T0 + timedelta(days=k), 100, 101, 99, 100) for k in range(5)]
    out = skript.gold_status(h4, d1, datetime.now(UTC))
    assert out["zustand"] == "unbekannt"
    assert out["ist_experimentell"] is True
    assert "IN_VALIDATION" in out["hinweis"]
    assert "EXPERIMENTELL" in out["hinweis"]


def test_reale_flache_reihe_crasht_nicht_und_ist_nicht_armed() -> None:
    """Flache, trendlose Kerzen — die ECHTE Berechnung (Swings/Struktur/Detektor)
    läuft durch, ohne ein Setup zu erfinden."""
    h4 = []
    t = _T0
    for k in range(80):
        base = 100.0 + (0.3 if k % 2 else -0.3)
        h4.append(_h4_bar(t, base, base + 0.4, base - 0.4, base + (0.1 if k % 3 else -0.1)))
        t += timedelta(hours=4)
    d1 = [_d1_bar(_T0 - timedelta(days=50 - k), 100, 100.5, 99.5, 100) for k in range(50)]

    out = skript.gold_status(h4, d1, t)
    assert out["zustand"] != "armed"
    assert out["ist_experimentell"] is True
    assert out["text"]


def test_armed_wird_mit_warnung_und_voller_geometrie_uebersetzt(monkeypatch) -> None:
    rep = BreakoutRetestReport(
        instrument="XAUUSD",
        information_cutoff=datetime.now(UTC),
        state=BreakoutState.ARMED,
        direction=Direction.LONG,
        entry=102.6,
        sl=100.5,
        tp1=105.75,
        tp2=108.9,
        tp3_ref="Runner: Trailing H4-Swing, aktiv nach TP2",
        rr_to_tp2=2.9,
        confidence=0.81,
        chain_progress="Konsolidierung 2.1·ATR → Ausbruch long → Retest hält",
    )
    monkeypatch.setattr(skript, "detect_breakout_retest", lambda mtf: rep)

    h4 = [_h4_bar(_T0 + timedelta(hours=4 * k), 100, 101, 99, 100) for k in range(60)]
    # D1 liegt VOR den H4-Bars: nur geschlossene Tageskerzen zaehlen (point-in-time).
    d1 = [_d1_bar(_T0 - timedelta(days=45 - k), 100, 101, 99, 100) for k in range(45)]
    out = skript.gold_status(h4, d1, datetime.now(UTC))

    assert out["zustand"] == "armed"
    assert out["richtung"] == "long"
    assert out["entry"] == 102.6 and out["sl"] == 100.5
    assert out["tp1"] == 105.75 and out["tp2"] == 108.9
    assert out["rr_bis_tp2"] == 2.9
    assert out["confidence"] == 0.81
    assert "Paper" in out["warnung"] and "nicht bewiesen" in out["warnung"]
    assert out["ist_experimentell"] is True


def test_bauen_schreibt_studien_auch_bei_quellenausfall() -> None:
    async def _kaputt(tage: int) -> list:
        raise RuntimeError("Yahoo nicht erreichbar")

    import asyncio

    orig = skript._h1_bars
    skript._h1_bars = _kaputt  # type: ignore[assignment]
    try:
        ergebnis, stand, alarme = asyncio.run(skript._bauen(400, {"letzte_bar": "x"}))
    finally:
        skript._h1_bars = orig  # type: ignore[assignment]

    assert ergebnis["aktiv"] is False
    assert "fehler" in ergebnis
    assert stand is None and alarme == []  # Ausfall: Stand bleibt unangetastet
    assert len(ergebnis["studien"]) == len(skript.STUDIEN)
    titel = {s["titel"] for s in ergebnis["studien"]}
    assert any("Asien-Range" in t for t in titel)
    assert any("Mean-Reversion" in t for t in titel)


def test_bauen_bei_zu_wenig_h1_bars_bleibt_ehrlich() -> None:
    import asyncio

    def _h1_bar(t: datetime) -> OHLCV:
        return OHLCV(
            instrument="XAUUSD-YF",
            timeframe=Timeframe.H1,
            open_time=t,
            close_time=bar_close_time(t, Timeframe.H1),
            open=100.0,
            high=101.0,
            low=99.0,
            close=100.0,
            volume=1.0,
            source="test",
        )

    async def _wenig(tage: int) -> list:
        return [_h1_bar(_T0 + timedelta(hours=k)) for k in range(10)]

    orig = skript._h1_bars
    skript._h1_bars = _wenig  # type: ignore[assignment]
    try:
        ergebnis, stand, _ = asyncio.run(skript._bauen(400))
    finally:
        skript._h1_bars = orig  # type: ignore[assignment]

    assert ergebnis["aktiv"] is False
    assert stand is None
    assert "H1-Bars" in ergebnis["fehler"]
    assert len(ergebnis["studien"]) == len(skript.STUDIEN)


def test_main_schreibt_seite_und_stand(monkeypatch, tmp_path) -> None:
    gesehen = {}

    async def _kanal(tage: int, stand: dict | None) -> tuple:
        gesehen["stand"] = stand
        seite = {
            "aktiv": True,
            "geprueft": "2026-10-11T00:00:00+00:00",
            "studien": list(skript.STUDIEN),
        }
        return (
            seite,
            {"letzte_bar": "2026-10-11T00:00:00+00:00", "offen": None, "abgeschlossen": []},
            [],
        )

    monkeypatch.setattr(skript, "_bauen", _kanal)
    out = tmp_path / "day_trading.json"
    stand = tmp_path / "stand.json"
    stand.write_text(json.dumps({"letzte_bar": "2026-10-10T00:00:00+00:00"}), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["prog", "--out", str(out), "--stand", str(stand)])

    assert skript.main() == 0
    assert gesehen["stand"] == {"letzte_bar": "2026-10-10T00:00:00+00:00"}  # alter Stand gelesen
    assert json.loads(out.read_text(encoding="utf-8"))["aktiv"] is True
    assert json.loads(stand.read_text(encoding="utf-8"))["letzte_bar"].startswith("2026-10-11")


def test_main_laesst_stand_bei_ausfall_stehen(monkeypatch, tmp_path) -> None:
    async def _kaputt(tage: int, stand: dict | None) -> tuple:
        return {"aktiv": False, "fehler": "x", "studien": []}, None, []

    monkeypatch.setattr(skript, "_bauen", _kaputt)
    stand = tmp_path / "stand.json"
    alt = {"letzte_bar": "2026-10-10T00:00:00+00:00", "offen": {"signal_ts": "a"}}
    stand.write_text(json.dumps(alt), encoding="utf-8")
    monkeypatch.setattr(
        sys, "argv", ["prog", "--out", str(tmp_path / "o.json"), "--stand", str(stand)]
    )
    assert skript.main() == 0
    assert json.loads(stand.read_text(encoding="utf-8")) == alt


_REPO = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("studie", skript.STUDIEN)
def test_jede_studie_hat_doc_und_ergebnis(studie: dict) -> None:
    assert (_REPO / studie["doc"]).exists()
    assert "edge" in studie["ergebnis"].lower()


# ------------------------------------------------------------------ Paper-Trade-Verfolgung
# Ozan, 11.10. 20:49: "will auch dafuer alarme bekommen". Jeder Alarm muss genau einmal
# kommen, und die Forward-Bilanz muss ehrlich rechnen — sonst ist sie wertlos.

_SIG = _T0 + timedelta(hours=4 * 10)


def _trade(richtung: str = "long") -> dict:
    if richtung == "long":
        return {
            "signal_ts": _SIG.isoformat(),
            "richtung": "long",
            "entry": 100.0,
            "sl": 98.0,
            "tp1": 103.0,
            "tp2": 106.0,
            "gemeldet": [],
        }
    return {
        "signal_ts": _SIG.isoformat(),
        "richtung": "short",
        "entry": 100.0,
        "sl": 102.0,
        "tp1": 97.0,
        "tp2": 94.0,
        "gemeldet": [],
    }


def _folge(*hlc: tuple[float, float, float]) -> list[OHLCV]:
    """Bars ab dem Signal-Bar; der erste Eintrag IST der Signal-Bar (wird uebersprungen)."""
    bars = []
    for k, (h, lo, c) in enumerate(((100.5, 99.5, 100.0), *hlc)):
        t = _SIG - timedelta(hours=4) + timedelta(hours=4 * k)
        bars.append(_h4_bar(t, c, h, lo, c))
    return bars


def test_long_tp1_dann_tp2() -> None:
    res = skript.verfolge(_trade(), _folge((103.5, 99.5, 103.0), (106.5, 102.0, 106.0)))
    assert res["status"] == "geschlossen"
    assert [e["art"] for e in res["ereignisse"]] == ["TP1", "TP2"]
    assert res["r"] == pytest.approx(0.5 * 1.5 + 0.5 * 3.0)


def test_long_stop_ohne_tp1_ist_minus_ein_r() -> None:
    res = skript.verfolge(_trade(), _folge((100.5, 97.5, 98.0)))
    assert [e["art"] for e in res["ereignisse"]] == ["STOP"]
    assert res["r"] == pytest.approx(-1.0)


def test_nach_tp1_steht_der_stop_auf_einstand() -> None:
    res = skript.verfolge(_trade(), _folge((103.5, 100.5, 103.0), (101.0, 99.8, 100.0)))
    assert [e["art"] for e in res["ereignisse"]] == ["TP1", "EINSTAND"]
    assert res["r"] == pytest.approx(0.75)


def test_stop_und_ziel_im_selben_bar_zaehlt_pessimistisch_der_stop() -> None:
    res = skript.verfolge(_trade(), _folge((106.5, 97.0, 104.0)))
    assert [e["art"] for e in res["ereignisse"]] == ["STOP"]
    assert res["r"] == pytest.approx(-1.0)


def test_short_spiegelt_long() -> None:
    res = skript.verfolge(_trade("short"), _folge((100.5, 96.5, 97.0), (98.0, 93.5, 94.0)))
    assert [e["art"] for e in res["ereignisse"]] == ["TP1", "TP2"]
    assert res["r"] == pytest.approx(2.25)


def test_zeitausstieg_zum_schlusskurs() -> None:
    res = skript.verfolge(_trade(), _folge(*[(101.0, 99.0, 101.0)] * 5), max_bars=5)
    assert [e["art"] for e in res["ereignisse"]] == ["ZEIT"]
    assert res["r"] == pytest.approx(0.5)


def test_offener_trade_meldet_laufendes_ergebnis() -> None:
    res = skript.verfolge(_trade(), _folge((101.5, 99.5, 101.0)))
    assert res["status"] == "offen" and res["r"] is None
    assert res["r_offen"] == pytest.approx(0.5)


def test_fortschreiben_meldet_jedes_ereignis_genau_einmal(monkeypatch) -> None:
    signal = _trade()
    signal["confidence"] = 0.8
    monkeypatch.setattr(skript, "neue_signale", lambda h4, d1, seit: [dict(signal, gemeldet=[])])
    bars = _folge((103.5, 99.5, 103.0))

    stand, alarme = skript.fortschreiben(None, bars, [])
    assert [a["dedup_key"].split(":")[-1] for a in alarme] == ["NEU", "TP1"]
    assert all("🧪" in a["titel"] for a in alarme)
    assert all("EXPERIMENTELL" in a["text"] for a in alarme)

    # derselbe Stand, dieselben Kerzen -> kein zweiter Alarm, kein zweiter Trade
    stand2, alarme2 = skript.fortschreiben(stand, bars, [])
    assert alarme2 == []
    assert stand2["offen"]["signal_ts"] == signal["signal_ts"]

    # Ziel erreicht -> genau ein TP2-Alarm mit Ergebnis, Trade in die Bilanz
    stand3, alarme3 = skript.fortschreiben(
        stand2, _folge((103.5, 99.5, 103.0), (106.5, 103, 106)), []
    )
    assert [a["dedup_key"].split(":")[-1] for a in alarme3] == ["TP2"]
    assert "+2.25 R" in alarme3[0]["text"]
    assert stand3["offen"] is None
    bilanz = skript.forward_bilanz(stand3)
    assert bilanz["anzahl"] == 1 and bilanz["summe_r"] == pytest.approx(2.25)

    # dasselbe Signal taucht im Fenster nochmal auf -> wird NICHT neu eroeffnet
    _, alarme4 = skript.fortschreiben(stand3, _folge((103.5, 99.5, 103.0), (106.5, 103, 106)), [])
    assert alarme4 == []


def test_erster_lauf_ohne_stand_prueft_nur_den_letzten_bar(monkeypatch) -> None:
    gepruefte = []

    def _fake(h4, d1, k, atr):
        gepruefte.append(k)
        return None, None

    monkeypatch.setattr(skript, "_report_bei", _fake)
    bars = _folge(*[(101.0, 99.0, 100.0)] * 10)
    skript.neue_signale(bars, [], None)
    assert gepruefte == [len(bars) - 1]

    gepruefte.clear()
    skript.neue_signale(bars, [], bars[2].close_time)
    assert gepruefte == list(range(len(bars) - skript.FENSTER_BARS, len(bars)))


def test_d1_kerze_des_laufenden_tages_fliesst_nicht_ein(monkeypatch) -> None:
    """Point-in-time: eine Tageskerze, die nach dem H4-Bar schliesst, darf der Detektor
    nicht sehen — sonst kennt der Tagestrend die Zukunft."""
    gesehen = {}

    def _detektor(mtf):
        gesehen["d1_ende"] = mtf.d1.bars[-1].close_time
        gesehen["cutoff"] = mtf.information_cutoff
        return BreakoutRetestReport(
            instrument="XAUUSD",
            information_cutoff=mtf.information_cutoff,
            state=BreakoutState.SCANNING,
        )

    monkeypatch.setattr(skript, "detect_breakout_retest", _detektor)
    h4 = [_h4_bar(_T0 + timedelta(hours=4 * k), 100, 101, 99, 100) for k in range(60)]
    d1 = [_d1_bar(_T0 - timedelta(days=45 - k), 100, 101, 99, 100) for k in range(60)]
    skript.gold_status(h4, d1, datetime.now(UTC))
    assert gesehen["d1_ende"] <= gesehen["cutoff"]


# ------------------------------------------------------------------ Kraken-Umrechnung
# Ozan kauft Gold auf Kraken (PAXG/USD), die Marken kommen vom Gold-Future. Am 11.10.
# lagen beide rund 30 $ auseinander — ohne Umrechnung waere jede Marke falsch.


def test_umrechnung_nur_mit_frischem_future_kurs() -> None:
    assert skript.umrechnungsfaktor(4220.0, 4189.6, 600) == pytest.approx(4189.6 / 4220.0, rel=1e-6)
    assert skript.umrechnungsfaktor(4220.0, 4189.6, 3 * 3600) is None  # Wochenende/Pause
    assert skript.umrechnungsfaktor(4220.0, None, 600) is None
    assert skript.umrechnungsfaktor(4220.0, 3000.0, 600) is None  # unplausibel


def test_alarm_nennt_kraken_marken_wenn_faktor_da(monkeypatch) -> None:
    monkeypatch.setattr(skript, "neue_signale", lambda h4, d1, seit: [_trade()])
    _, alarme = skript.fortschreiben(None, _folge((101.0, 99.5, 100.5)), [], kraken_faktor=0.99)
    neu = alarme[0]
    assert "PAXG/USD" in neu["text"]
    assert "Einstieg 99,00" in neu["text"]  # 100 * 0,99
    assert "Stop 97,02" in neu["text"]


def test_alarm_ohne_faktor_sagt_ehrlich_dass_es_future_kurse_sind(monkeypatch) -> None:
    monkeypatch.setattr(skript, "neue_signale", lambda h4, d1, seit: [_trade()])
    _, alarme = skript.fortschreiben(None, _folge((101.0, 99.5, 100.5)), [])
    assert "Gold-Future" in alarme[0]["text"]
    assert "0,5–1 % tiefer" in alarme[0]["text"]
