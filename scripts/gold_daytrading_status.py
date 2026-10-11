#!/usr/bin/env python
"""Gold-Tageshandel-Seite — ehrlicher Stand statt eines Alarmkanals.

WARUM ES DIESES SKRIPT GIBT

Ozan, 11.10. 17:18/17:41 (zusammengefasst): erst "Gold Day-Trading auf Kraken neu
pruefen", nach meiner Rueckfrage dann konkret: "mach ... extra seite mit gold day
trade auf kraken oder crypto". Zwei vorherige, vorab festgelegte Studien
(`docs/GOLD-DAYTRADING-STUDIE-2026-10.md`, `docs/MEAN-REVERSION-STUDIE-2026-10.md`)
fanden **keinen Edge** fuer Gold-Intraday (Asien-Range/Sweep) und Krypto-Mean-
Reversion. Eine neue Alarm-faehige Strategie zu erfinden, nur um die Seite zu
fuellen, waere genau der erfundene Edge, den dieses Projekt nicht will.

Diese Seite ist deshalb KEIN neuer Alarmkanal. Sie zeigt zwei ehrliche Dinge:

1. Den aktuellen Stand von **SETUP-BREAKOUT-RETEST-01** (S9) — einer Gold-SWING-
   Strategie (H4/D1, nicht Intraday), die in `config/setup_validation.json` als
   ``IN_VALIDATION`` gefuehrt wird: historisch plausibel, aber **nicht ausreichend
   bewiesen** (`scripts/xau_shadow.py` ist der zugehoerige Shadow-Forward-Lauf,
   separat von diesem Skript). Hier wird nur der JETZT-Zustand berechnet — derselbe
   Detektor (`detect_breakout_retest`), derselbe Punkt-in-Zeit-Schnitt, aber ohne
   Backtest-Schleife. Ausdruecklich **kein Alarm, keine Telegram-Meldung** — nur
   Beobachtung auf der Seite.
2. Eine ehrliche Zusammenfassung der beiden bereits gelaufenen Studien, mit den
   echten Zahlen, damit auf der Seite steht, was schon geprueft wurde und warum es
   (noch) keinen Intraday-Kanal gibt — statt dass Ozan sich fragen muss, ob daran
   niemand gearbeitet hat.

Datenquelle: Yahoo Finance H1 (``YahooFinanceProvider``, keylos, indikativ — wie
``scripts/ingest_yahoo.py``), im Skript zu H4/D1 zusammengefasst. Faellt die Quelle
aus, schreibt dieses Skript ``aktiv: false`` und sonst nichts Erfundenes — wie
``chatgpt_zweitmeinung.py`` es bei fehlendem Schluessel tut.

    uv run python scripts/gold_daytrading_status.py --out web/day_trading.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from trading_agent.core.enums import RegimeDirectional, Timeframe
from trading_agent.core.models import OHLCV
from trading_agent.data.resample import resample_ohlcv
from trading_agent.strategy.primitives.atr import atr_series
from trading_agent.strategy.primitives.structure import derive_structure_state, structure_breaks
from trading_agent.strategy.primitives.swings import detect_swings
from trading_agent.strategy.setups.breakout_retest import BreakoutState, detect_breakout_retest

_H4, _D1 = Timeframe.H4, Timeframe.D1
_SYMBOL = "XAUUSD-YF"

#: Die beiden vorab festgelegten, bereits abgeschlossenen Studien — mit den echten
#: Zahlen aus den Entscheidungsdokumenten. Nicht neu bewertet, nur zitiert: wird eine
#: der beiden Dateien je ueberarbeitet, muss diese Zusammenfassung von Hand nachziehen.
STUDIEN: tuple[dict[str, object], ...] = (
    {
        "titel": "Gold Intraday: Asien-Range-Ausbruch (G1) + Vortages-Sweep (G2)",
        "zeitraum": "PAXG/USDT 15-Min, 01.2023–09.2026, OOS ab 07.2025",
        "ergebnis": (
            "Kein Edge. G1 OOS −0,06 R (IS −0,39 R), "
            "G2 OOS −0,57 R (IS −0,73 R) — beide Setups im Minus."
        ),
        "doc": "docs/GOLD-DAYTRADING-STUDIE-2026-10.md",
    },
    {
        "titel": "Krypto Mean-Reversion (Bollinger + RSI, stuendlich)",
        "zeitraum": "24 Coins, 1 Std., 01.2025–10.2026, OOS ab 01.2026",
        "ergebnis": (
            "Kein Edge in keiner Variante. OOS −0,13 bis −0,29 R, "
            "auch mit Seitwärts-Regime-Filter."
        ),
        "doc": "docs/MEAN-REVERSION-STUDIE-2026-10.md",
    },
)

#: Chain-Progress-Texte fuer die Zustaende ohne Geometrie (SCANNING/AWAIT_RETEST/
#: INVALIDATED), falls der Detektor selbst keinen liefert (z. B. SCANNING-Basisfall).
_ZUSTAND_TEXT: dict[BreakoutState, str] = {
    BreakoutState.SCANNING: "Kein Breakout-Retest-Muster erkennbar — Markt wird weiter beobachtet.",
    BreakoutState.CONSOLIDATION: "Enge Range erkannt, noch kein Ausbruch.",
    BreakoutState.AWAIT_RETEST: "Ausbruch da, wartet auf einen haltenden Retest.",
    BreakoutState.ARMED: "Geometrie steht — Entry/SL/TP berechnet.",
    BreakoutState.INVALIDATED: "Ausbruch bereits zurückgetestet — kein frischer Einstieg.",
}

#: Deckt die Felder ab, die die App unabhängig vom Zustand erwartet, damit ein
#: fehlendes Feld nicht zu einem JS-Fehler statt einer leeren Anzeige wird.
_HINWEIS_S9 = (
    "SETUP-BREAKOUT-RETEST-01 („S9“) — Status IN_VALIDATION in "
    "config/setup_validation.json: historisch plausibel, noch NICHT ausreichend "
    "bewiesen. Reine Beobachtung — kein Alarm, keine Telegram-Meldung, kein "
    "Handelssignal."
)


def _d1_struktur(d1: list[OHLCV]) -> tuple[RegimeDirectional, tuple]:
    """D1-Trend + Struktur-Brüche über die GESAMTE übergebene D1-Reihe.

    Point-in-time ist hier automatisch erfüllt: es wird nur der JETZT-Zustand
    gebraucht (kein Backtest über die Vergangenheit), also darf die ganze Reihe
    einfliessen — anders als in ``xau_shadow.py``, das bei jedem historischen Bar
    nur die bis dahin bekannten D1-Bars sehen darf.
    """
    sw = detect_swings(d1, _D1, left=2, right=2, min_leg_atr=0.5)
    sw.sort(key=lambda s: s.confirmed_at)
    trend = (
        derive_structure_state(sw, _D1, min_swings=2).directional
        if len(sw) >= 4
        else RegimeDirectional.UNCLEAR
    )
    brueche = structure_breaks(d1, sw, _D1, min_swings=2)
    brueche.sort(key=lambda b: b.break_bar_timestamp)
    return trend, tuple(brueche)


class _NS:
    """Minimaler Platzhalter für den Teil von ``MtfContext``, den der Detektor
    tatsächlich liest — derselbe Kunstgriff wie in ``scripts/xau_shadow.py``."""

    def __init__(self, **kw: object) -> None:
        self.__dict__.update(kw)


def gold_status(h4: list[OHLCV], d1: list[OHLCV], jetzt: datetime) -> dict[str, object]:
    """Reine Funktion: H4/D1-Bars → ehrlicher JETZT-Zustand von S9 als dict.

    Keine Netzwerk-, keine Dateizugriffe — testbar mit synthetischen Bars oder,
    für den ARMED-Pfad, durch Monkeypatching von ``detect_breakout_retest`` selbst
    (der Detektor hat seine eigenen, ausführlichen Tests in
    ``tests/unit/test_breakout_retest.py``; hier wird nur geprüft, dass DIESES
    Skript sein Ergebnis ehrlich und ohne Absturz in JSON übersetzt).
    """
    basis: dict[str, object] = {
        "instrument": "XAUUSD",
        "ist_experimentell": True,
        "kein_alarm": True,
        "hinweis": _HINWEIS_S9,
    }
    need_h4 = 14 + 12 + 14 + 5  # consolidation + retest_window + atr_period + Puffer
    if len(h4) < need_h4 or len(d1) < 40:
        return {
            **basis,
            "zustand": "unbekannt",
            "text": (
                f"Nicht genug Gold-Historie für eine Einschätzung "
                f"(H4={len(h4)}, D1={len(d1)})."
            ),
        }

    trend, brueche = _d1_struktur(d1)
    atr = atr_series(h4, 14)
    mtf = _NS(
        instrument="XAUUSD",
        information_cutoff=jetzt,
        h4=_NS(bars=tuple(h4), atr=(atr[-1] or 0.0) if atr else 0.0),
        d1=_NS(
            bars=tuple(d1),
            structure=_NS(directional=trend),
            structure_breaks=brueche,
            regime=_NS(directional_score=0.72),
        ),
    )
    rep = detect_breakout_retest(mtf)  # type: ignore[arg-type]

    eintrag: dict[str, object] = {
        **basis,
        "zustand": rep.state.value,
        "richtung": rep.direction.value if rep.direction is not None else None,
        "d1_trend": getattr(trend, "value", str(trend)),
        "text": rep.chain_progress or _ZUSTAND_TEXT.get(rep.state, ""),
    }
    if rep.is_armed:
        eintrag.update(
            {
                "entry": rep.entry,
                "sl": rep.sl,
                "tp1": rep.tp1,
                "tp2": rep.tp2,
                "tp3_hinweis": rep.tp3_ref,
                "rr_bis_tp2": rep.rr_to_tp2,
                "confidence": rep.confidence,
                "warnung": (
                    "Nur Beobachtung — S9 ist nicht alarmfähig. Kein Trade wird "
                    "ausgelöst oder vorgeschlagen."
                ),
            }
        )
    return eintrag


async def _h1_bars(tage: int) -> list[OHLCV]:
    from trading_agent.data.providers.yahoo_finance import YahooFinanceProvider

    jetzt = datetime.now(UTC)
    provider = YahooFinanceProvider(symbol_map={_SYMBOL: "GC=F"})
    try:
        return await provider.fetch_ohlcv(_SYMBOL, Timeframe.H1, jetzt - timedelta(days=tage), jetzt)
    finally:
        await provider.aclose()


async def _bauen(tage: int) -> dict[str, object]:
    jetzt = datetime.now(UTC)
    basis = {
        "aktiv": False,
        "geprueft": jetzt.isoformat(),
        "studien": list(STUDIEN),
    }
    try:
        h1 = await _h1_bars(tage)
    except Exception as exc:  # Quelle offline — ehrlich leer statt erfunden.
        return {**basis, "fehler": f"Yahoo-Abruf fehlgeschlagen: {exc}"}
    if len(h1) < 500:
        return {**basis, "fehler": f"nur {len(h1)} H1-Bars von Yahoo — zu wenig"}

    h1 = [b.model_copy(update={"instrument": _SYMBOL}) for b in h1]
    h4 = resample_ohlcv(h1, Timeframe.H1, _H4, require_complete=True, source_name="yahoo_h1")
    d1 = resample_ohlcv(h1, Timeframe.H1, _D1, require_complete=False, source_name="yahoo_h1")
    gold = gold_status(list(h4), list(d1), jetzt)
    return {**basis, "aktiv": True, "gold": gold}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="web/day_trading.json")
    ap.add_argument(
        "--tage", type=int, default=400, help="H1-Fenster in Tagen (Yahoo-Limit ~730 Tage)"
    )
    args = ap.parse_args()

    ergebnis = asyncio.run(_bauen(args.tage))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(ergebnis, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({k: v for k, v in ergebnis.items() if k != "studien"}, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
