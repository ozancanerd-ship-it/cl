"""``scripts/gold_daytrading_status.py`` — die ehrliche Gold-Tageshandel-Seite.

WARUM ES DIESE TESTS GIBT

Ozan, 11.10. 17:18/17:41: "mach ... extra seite mit gold day trade auf kraken oder
crypto". Zwei vorherige Studien fanden KEINEN Edge fuer Intraday-Gold und
Krypto-Mean-Reversion (siehe die Docs, die unten zitiert werden) — eine neue,
alarmfaehige Strategie zu erfinden waere genau der erfundene Edge, den dieses
Projekt nicht will. Das Skript zeigt deshalb nur den JETZT-Zustand von S9
(``SETUP-BREAKOUT-RETEST-01``, Status ``IN_VALIDATION`` — nicht bewiesen) und die
ehrliche Zusammenfassung der beiden negativen Studien.

Festgehalten wird:

1. Zu wenig Historie → ehrlich "unbekannt", kein erfundener Zustand, aber die
   Ehrlichkeits-Felder (``ist_experimentell``, ``kein_alarm``, ``hinweis``) sind
   trotzdem da — die App darf nie auf ein fehlendes Feld treffen.
2. Eine reale (flache, trendlose) H4/D1-Reihe laeuft durch die ECHTE
   ``detect_breakout_retest``-Berechnung ohne Absturz und liefert ehrlich einen
   Nicht-ARMED-Zustand.
3. Ein ARMED-Ergebnis (per Monkeypatch des Detektors — der Detektor selbst ist in
   ``test_breakout_retest.py`` ausfuehrlich getestet, hier geht es nur um die
   ehrliche JSON-Uebersetzung) bringt Entry/SL/TP/RR/Confidence UND eine explizite
   "nur Beobachtung"-Warnung mit — niemals eine Kauf-/Verkaufsaufforderung.
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
    assert out["kein_alarm"] is True
    assert "IN_VALIDATION" in out["hinweis"]


def test_reale_flache_reihe_crasht_nicht_und_ist_nicht_armed() -> None:
    """Flache, trendlose Kerzen — die ECHTE Berechnung (Swings/Struktur/Detektor)
    läuft durch, ohne ein Setup zu erfinden."""
    h4 = []
    t = _T0
    for k in range(80):
        base = 100.0 + (0.3 if k % 2 else -0.3)
        h4.append(_h4_bar(t, base, base + 0.4, base - 0.4, base + (0.1 if k % 3 else -0.1)))
        t += timedelta(hours=4)
    d1 = [_d1_bar(_T0 + timedelta(days=k), 100, 100.5, 99.5, 100) for k in range(50)]

    out = skript.gold_status(h4, d1, t)
    assert out["zustand"] != "armed"
    assert out["ist_experimentell"] is True
    assert out["kein_alarm"] is True
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
    d1 = [_d1_bar(_T0 + timedelta(days=k), 100, 101, 99, 100) for k in range(45)]
    out = skript.gold_status(h4, d1, datetime.now(UTC))

    assert out["zustand"] == "armed"
    assert out["richtung"] == "long"
    assert out["entry"] == 102.6 and out["sl"] == 100.5
    assert out["tp1"] == 105.75 and out["tp2"] == 108.9
    assert out["rr_bis_tp2"] == 2.9
    assert out["confidence"] == 0.81
    assert "Nur Beobachtung" in out["warnung"]
    assert out["ist_experimentell"] is True
    assert out["kein_alarm"] is True


def test_bauen_schreibt_studien_auch_bei_quellenausfall() -> None:
    async def _kaputt(tage: int) -> list:
        raise RuntimeError("Yahoo nicht erreichbar")

    import asyncio

    orig = skript._h1_bars
    skript._h1_bars = _kaputt  # type: ignore[assignment]
    try:
        ergebnis = asyncio.run(skript._bauen(400))
    finally:
        skript._h1_bars = orig  # type: ignore[assignment]

    assert ergebnis["aktiv"] is False
    assert "fehler" in ergebnis
    assert len(ergebnis["studien"]) == 2
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
        ergebnis = asyncio.run(skript._bauen(400))
    finally:
        skript._h1_bars = orig  # type: ignore[assignment]

    assert ergebnis["aktiv"] is False
    assert "H1-Bars" in ergebnis["fehler"]
    assert len(ergebnis["studien"]) == 2


def test_main_schreibt_gueltige_json_datei(monkeypatch, tmp_path) -> None:
    async def _kanal(tage: int) -> dict:
        return {"aktiv": False, "geprueft": "2026-10-11T00:00:00+00:00", "studien": list(skript.STUDIEN)}

    monkeypatch.setattr(skript, "_bauen", _kanal)
    out = tmp_path / "day_trading.json"
    monkeypatch.setattr(sys, "argv", ["prog", "--out", str(out)])

    rc = skript.main()
    assert rc == 0
    geschrieben = json.loads(out.read_text(encoding="utf-8"))
    assert geschrieben["aktiv"] is False
    assert len(geschrieben["studien"]) == 2


_REPO = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("studie", skript.STUDIEN)
def test_jede_studie_hat_doc_und_ergebnis(studie: dict) -> None:
    assert (_REPO / studie["doc"]).exists()
    assert "edge" in studie["ergebnis"].lower()
