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
            "Kein Edge in keiner Variante. OOS −0,13 bis −0,29 R, auch mit Seitwärts-Regime-Filter."
        ),
        "doc": "docs/MEAN-REVERSION-STUDIE-2026-10.md",
    },
    {
        "titel": "Tageshandel 2. Studie: Ausbruch, Ausreißer-Tag, Squeeze, Intraday-Momentum",
        "zeitraum": "24 Coins + PAXG, 1 Std., 01.2022–09.2026, OOS ab 01.2025",
        "ergebnis": (
            "Krypto-Ausbruchswelle (≥ 12 Coins): Kandidat mit schmalem Edge, OOS +0,14 R — "
            "dafür kommen jetzt Alarme. Gold-Ausbruch nur in der Hausse 2025/26 positiv "
            "(experimentell). Alle anderen Varianten ohne Edge."
        ),
        "doc": "docs/DAYTRADE-STUDIE-2026-10.md",
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
    "bewiesen. Alarme kommen nur als EXPERIMENTELL markiert, als Paper-Trade — keine "
    "Kaufempfehlung, kein Echtgeld."
)
_WARNUNG_ARMED = "Experimentelles Setup — Edge nicht bewiesen. Nur Paper-Trade, kein Echtgeld."
_WARNUNG_ALARM = (
    "EXPERIMENTELL: S9 ist noch nicht bewiesen (IN_VALIDATION). Nur Paper, kein Echtgeld."
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


_NEED_H4 = 14 + 12 + 14 + 5  # consolidation + retest_window + atr_period + Puffer
_NEED_D1 = 40


def _report_bei(h4: list[OHLCV], d1: list[OHLCV], k: int, atr: list) -> tuple[object, object]:
    """S9-Auswertung am H4-Bar ``k`` — streng point-in-time.

    Nur H4-Bars bis einschliesslich ``k`` und nur D1-Bars, die zu diesem Zeitpunkt schon
    GESCHLOSSEN waren. Der laufende Tag (unvollstaendige D1-Kerze aus dem Resampling)
    darf nicht einfliessen, sonst wuerde der Tagestrend Kursbewegungen "kennen", die beim
    Signal noch gar nicht passiert waren. Gibt ``(report, trend)`` zurueck oder
    ``(None, None)``, wenn die Historie nicht reicht.
    """
    cutoff = h4[k].close_time
    d1_pit = [b for b in d1 if b.close_time <= cutoff]
    if k + 1 < _NEED_H4 or len(d1_pit) < _NEED_D1:
        return None, None
    trend, brueche = _d1_struktur(d1_pit)
    mtf = _NS(
        instrument="XAUUSD",
        information_cutoff=cutoff,
        h4=_NS(bars=tuple(h4[: k + 1]), atr=(atr[k] or 0.0) if atr else 0.0),
        d1=_NS(
            bars=tuple(d1_pit),
            structure=_NS(directional=trend),
            structure_breaks=brueche,
            regime=_NS(directional_score=0.72),
        ),
    )
    return detect_breakout_retest(mtf), trend  # type: ignore[arg-type]


def gold_status(h4: list[OHLCV], d1: list[OHLCV], jetzt: datetime) -> dict[str, object]:
    """Reine Funktion: H4/D1-Bars -> ehrlicher JETZT-Zustand von S9 als dict.

    Keine Netzwerk-, keine Dateizugriffe — testbar mit synthetischen Bars oder,
    fuer den ARMED-Pfad, durch Monkeypatching von ``detect_breakout_retest`` selbst
    (der Detektor hat seine eigenen Tests in ``tests/unit/test_breakout_retest.py``).
    """
    basis: dict[str, object] = {
        "instrument": "XAUUSD",
        "ist_experimentell": True,
        "hinweis": _HINWEIS_S9,
    }
    rep, trend = (None, None)
    if h4:
        rep, trend = _report_bei(h4, d1, len(h4) - 1, atr_series(h4, 14))
    if rep is None:
        return {
            **basis,
            "zustand": "unbekannt",
            "text": (
                f"Nicht genug Gold-Historie für eine Einschätzung (H4={len(h4)}, D1={len(d1)})."
            ),
        }
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
                "warnung": _WARNUNG_ARMED,
            }
        )
    return eintrag


# --------------------------------------------------------------------------------------
# Experimentelle Alarme + Forward-Journal (Ozan, 11.10. 20:49: "will auch dafuer alarme
# bekommen"). Die Alarme sind ausdruecklich als EXPERIMENTELL markiert — S9 bleibt
# IN_VALIDATION. Gleichzeitig wird jeder Trade vorwaerts mitgeschrieben: genau diese
# Forward-Trades (Ziel >= 100) entscheiden spaeter, ob S9 je "bewiesen" heisst.
# --------------------------------------------------------------------------------------

STAND = "data/repository_real/live/gold_s9_stand.json"
PROTOKOLL = "data/repository_real/live/alerts.jsonl"
#: Wie viele zurueckliegende H4-Bars bei jedem Lauf nach einem frischen ARMED abgesucht
#: werden — deckt ausgefallene Laeufe von bis zu einem Tag ab.
FENSTER_BARS = 6
#: Zeit-Ausstieg wie in ``scripts/xau_shadow.py`` (60 H4-Bars = 10 Handelstage).
MAX_HALTEN_BARS = 60
FORWARD_ZIEL = 100


def neue_signale(h4: list[OHLCV], d1: list[OHLCV], seit: datetime | None) -> list[dict]:
    """ARMED-Signale an den letzten ``FENSTER_BARS`` H4-Bars, die nach ``seit`` schlossen.

    Ohne ``seit`` (allererster Lauf) zaehlt nur der letzte Bar — sonst kaeme beim ersten
    Lauf ein Alarm fuer ein Setup, das schon Stunden alt ist.
    """
    if not h4:
        return []
    atr = atr_series(h4, 14)
    erster = len(h4) - 1 if seit is None else max(0, len(h4) - FENSTER_BARS)
    raus: list[dict] = []
    for k in range(erster, len(h4)):
        if seit is not None and h4[k].close_time <= seit:
            continue
        rep, _ = _report_bei(h4, d1, k, atr)
        if rep is None or not rep.is_armed:
            continue
        raus.append(
            {
                "signal_ts": h4[k].close_time.isoformat(),
                "richtung": rep.direction.value,
                "entry": float(rep.entry),
                "sl": float(rep.sl),
                "tp1": float(rep.tp1),
                "tp2": float(rep.tp2),
                "confidence": rep.confidence,
                "gemeldet": [],
            }
        )
    return raus


def _r(trade: dict, preis: float) -> float:
    d = 1.0 if trade["richtung"] == "long" else -1.0
    einheit = abs(trade["entry"] - trade["sl"])
    return d * (preis - trade["entry"]) / einheit if einheit > 0 else 0.0


def verfolge(trade: dict, h4: list[OHLCV], max_bars: int = MAX_HALTEN_BARS) -> dict:
    """Spielt den Trade Bar fuer Bar ab dem Signal durch. Deterministisch, ohne Zustand
    zwischen den Laeufen: jeder Lauf rechnet aus denselben Kerzen dasselbe Ergebnis.

    Regeln (wie das S9-Setup, vereinfacht): Einstieg zum Schluss des Retest-Bars. 50 % an
    TP1, danach Stop auf Einstand; Rest an TP2. Stop und Ziel im selben Bar -> pessimistisch
    der Stop. Nach ``max_bars`` H4-Bars Ausstieg zum Schluss. Ohne Gebuehren (Brutto-R).
    """
    signal = datetime.fromisoformat(trade["signal_ts"])
    d = 1 if trade["richtung"] == "long" else -1
    sl, rest, r, tp1_da = trade["sl"], 1.0, 0.0, False
    ereignisse: list[dict] = []
    bars = 0
    letzter_kurs = trade["entry"]
    for b in h4:
        if b.close_time <= signal:
            continue
        bars += 1
        letzter_kurs = b.close
        ts = b.close_time.isoformat()
        hit_sl = (b.low <= sl) if d > 0 else (b.high >= sl)
        hit_tp1 = (b.high >= trade["tp1"]) if d > 0 else (b.low <= trade["tp1"])
        hit_tp2 = (b.high >= trade["tp2"]) if d > 0 else (b.low <= trade["tp2"])
        if hit_sl:
            r += rest * _r(trade, sl)
            ereignisse.append({"art": "EINSTAND" if tp1_da else "STOP", "ts": ts, "preis": sl})
            return {
                "status": "geschlossen",
                "ereignisse": ereignisse,
                "r": round(r, 3),
                "bars": bars,
                "kurs": letzter_kurs,
            }
        if hit_tp1 and not tp1_da:
            r += 0.5 * _r(trade, trade["tp1"])
            rest, tp1_da, sl = 0.5, True, trade["entry"]
            ereignisse.append({"art": "TP1", "ts": ts, "preis": trade["tp1"]})
        if hit_tp2 and tp1_da:
            r += rest * _r(trade, trade["tp2"])
            ereignisse.append({"art": "TP2", "ts": ts, "preis": trade["tp2"]})
            return {
                "status": "geschlossen",
                "ereignisse": ereignisse,
                "r": round(r, 3),
                "bars": bars,
                "kurs": letzter_kurs,
            }
        if bars >= max_bars:
            r += rest * _r(trade, b.close)
            ereignisse.append({"art": "ZEIT", "ts": ts, "preis": b.close})
            return {
                "status": "geschlossen",
                "ereignisse": ereignisse,
                "r": round(r, 3),
                "bars": bars,
                "kurs": letzter_kurs,
            }
    offen_r = r + rest * _r(trade, letzter_kurs)
    return {
        "status": "offen",
        "ereignisse": ereignisse,
        "r": None,
        "r_offen": round(offen_r, 3),
        "bars": bars,
        "kurs": letzter_kurs,
    }


_ART_TEXT = {
    "TP1": "TP1 erreicht — Hälfte raus, Stop auf Einstand",
    "TP2": "TP2 erreicht — Trade beendet",
    "STOP": "Stop getroffen — Trade beendet",
    "EINSTAND": "Rest auf Einstand ausgestoppt — Trade beendet",
    "ZEIT": "Zeitausstieg nach 10 Tagen — Trade beendet",
}


def _fmt(x: float) -> str:
    return f"{x:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def umrechnungsfaktor(
    future_kurs: float | None, paxg_kurs: float | None, alter_s: float | None
) -> float | None:
    """Gold-Future -> Kraken PAXG/USD. Der Future notiert wegen Zins/Lagerkosten meist
    0,5-1 % ueber Spot; PAXG ist Spot-Gold. Nur wenn der Future-Kurs frisch ist (Boerse
    offen, hoechstens 2 Std. alt) — am Wochenende steht der Future, PAXG laeuft weiter,
    ein Faktor daraus waere falsch. Unplausible Abstaende (> 5 %) werden verworfen."""
    if not future_kurs or not paxg_kurs or alter_s is None or alter_s > 2 * 3600:
        return None
    f = paxg_kurs / future_kurs
    return round(f, 6) if 0.95 < f < 1.05 else None


def _kraken_zeile(t: dict) -> str:
    f = t.get("kraken_faktor")
    if not f:
        return "Gold-Future-Kurse; auf Kraken (PAXG/USD) liegt Gold meist 0,5–1 % tiefer."
    return (
        f"Auf Kraken (Pax Gold, PAXG/USD) ≈ Einstieg {_fmt(t['entry'] * f)} · Stop "
        f"{_fmt(t['sl'] * f)} · TP1 {_fmt(t['tp1'] * f)} · TP2 {_fmt(t['tp2'] * f)}."
    )


def _alarm_neu(t: dict) -> dict:
    richtung = "LONG (steigend)" if t["richtung"] == "long" else "SHORT (fallend)"
    return {
        "titel": f"🧪 Gold S9 {richtung} — experimentell",
        "text": (
            f"Breakout-Retest auf Gold (4-Std.-Chart). Gold-Future: Einstieg "
            f"{_fmt(t['entry'])} · Stop {_fmt(t['sl'])} · TP1 {_fmt(t['tp1'])} · TP2 "
            f"{_fmt(t['tp2'])} $.\n{_kraken_zeile(t)}\n{_WARNUNG_ALARM}"
        ),
        "dedup_key": f"gold-s9:{t['signal_ts']}:NEU",
    }


def _alarm_ereignis(t: dict, e: dict, r: float | None) -> dict:
    zusatz = f" Ergebnis {r:+.2f} R." if r is not None else ""
    f = t.get("kraken_faktor")
    kraken = f" (PAXG ≈ {_fmt(e['preis'] * f)})" if f else ""
    return {
        "titel": f"🧪 Gold S9: {_ART_TEXT.get(e['art'], e['art'])}",
        "text": f"Bei {_fmt(e['preis'])} ${kraken}.{zusatz} Paper-Trade vom "
        f"{t['signal_ts'][:16].replace('T', ' ')} UTC.\n" + _WARNUNG_ALARM,
        "dedup_key": f"gold-s9:{t['signal_ts']}:{e['art']}",
    }


def fortschreiben(
    stand: dict | None, h4: list[OHLCV], d1: list[OHLCV], kraken_faktor: float | None = None
) -> tuple[dict, list[dict]]:
    """Stand + neue Kerzen -> neuer Stand + Liste der zu sendenden Alarme.

    Hoechstens EIN offener Trade zur Zeit (wie im Shadow-Lauf). Jedes Ereignis wird je
    Trade nur einmal gemeldet (``gemeldet``), auch wenn der Lauf zehnmal am Tag kommt.
    """
    stand = dict(stand or {})
    abgeschlossen: list[dict] = list(stand.get("abgeschlossen") or [])
    offen: dict | None = stand.get("offen")
    seit_roh = stand.get("letzte_bar")
    seit = datetime.fromisoformat(seit_roh) if seit_roh else None
    alarme: list[dict] = []

    def _abarbeiten(t: dict) -> dict | None:
        res = verfolge(t, h4)
        for e in res["ereignisse"]:
            if e["art"] in t["gemeldet"]:
                continue
            ende = res["status"] == "geschlossen" and e is res["ereignisse"][-1]
            alarme.append(_alarm_ereignis(t, e, res["r"] if ende else None))
            t["gemeldet"].append(e["art"])
        if res["status"] == "geschlossen":
            abgeschlossen.append({**t, "r": res["r"], "ausstieg": res["ereignisse"][-1]})
            return None
        t["r_offen"] = res["r_offen"]
        t["kurs"] = res["kurs"]
        t["bars"] = res["bars"]
        return t

    if offen is not None:
        offen = _abarbeiten(offen)
    if offen is None:
        gesperrt_bis = (
            datetime.fromisoformat(abgeschlossen[-1]["ausstieg"]["ts"]) if abgeschlossen else None
        )
        for s in neue_signale(h4, d1, seit):
            ts = datetime.fromisoformat(s["signal_ts"])
            if gesperrt_bis is not None and ts <= gesperrt_bis:
                continue
            if any(a["signal_ts"] == s["signal_ts"] for a in abgeschlossen):
                continue
            if kraken_faktor:
                s["kraken_faktor"] = kraken_faktor
            alarme.append(_alarm_neu(s))
            s["gemeldet"].append("NEU")
            offen = _abarbeiten(s)
            if offen is not None:
                break
            gesperrt_bis = datetime.fromisoformat(abgeschlossen[-1]["ausstieg"]["ts"])
    neu = {
        "letzte_bar": h4[-1].close_time.isoformat() if h4 else seit_roh,
        "offen": offen,
        "abgeschlossen": abgeschlossen,
    }
    return neu, alarme


def forward_bilanz(stand: dict) -> dict[str, object]:
    fertig = [t for t in (stand.get("abgeschlossen") or []) if t.get("r") is not None]
    n = len(fertig)
    summe = round(sum(t["r"] for t in fertig), 3)
    treffer = sum(1 for t in fertig if t["r"] > 0.02)
    return {
        "anzahl": n,
        "ziel": FORWARD_ZIEL,
        "summe_r": summe,
        "schnitt_r": round(summe / n, 3) if n else None,
        "trefferquote": round(treffer / n, 3) if n else None,
        "letzte": fertig[-5:][::-1],
    }


async def _h1_bars(tage: int) -> list[OHLCV]:
    from trading_agent.data.providers.yahoo_finance import YahooFinanceProvider

    jetzt = datetime.now(UTC)
    provider = YahooFinanceProvider(symbol_map={_SYMBOL: "GC=F"})
    try:
        return await provider.fetch_ohlcv(
            _SYMBOL, Timeframe.H1, jetzt - timedelta(days=tage), jetzt
        )
    finally:
        await provider.aclose()


async def _kraken_paxg() -> float | None:
    """Mittelkurs Pax Gold (PAXG/USD) bei Kraken — oder None, wenn nicht erreichbar."""
    try:
        from trading_agent.data.providers.kraken import KrakenDataProvider

        prov = KrakenDataProvider()
        try:
            q = await prov.fetch_quote("PAXGUSD")
        finally:
            await prov.aclose()
        return round((q.bid + q.ask) / 2, 2)
    except Exception:
        return None


async def _bauen(
    tage: int, stand: dict | None = None
) -> tuple[dict[str, object], dict | None, list[dict]]:
    """Gibt (Seiten-JSON, neuer Stand oder None bei Ausfall, Alarme) zurueck."""
    jetzt = datetime.now(UTC)
    basis = {
        "aktiv": False,
        "geprueft": jetzt.isoformat(),
        "alarm_experimentell": True,
        "studien": list(STUDIEN),
        "forward": forward_bilanz(stand or {}),
        "offen": (stand or {}).get("offen"),
    }
    try:
        h1 = await _h1_bars(tage)
    except Exception as exc:  # Quelle offline — ehrlich leer statt erfunden.
        return {**basis, "fehler": f"Yahoo-Abruf fehlgeschlagen: {exc}"}, None, []
    if len(h1) < 500:
        return {**basis, "fehler": f"nur {len(h1)} H1-Bars von Yahoo — zu wenig"}, None, []

    h1 = [b.model_copy(update={"instrument": _SYMBOL}) for b in h1]
    h4 = list(resample_ohlcv(h1, Timeframe.H1, _H4, require_complete=True, source_name="yahoo_h1"))
    d1 = list(resample_ohlcv(h1, Timeframe.H1, _D1, require_complete=False, source_name="yahoo_h1"))
    gold = gold_status(h4, d1, jetzt)
    paxg = await _kraken_paxg()
    faktor = umrechnungsfaktor(
        h1[-1].close, paxg, (jetzt - h1[-1].close_time).total_seconds() if h1 else None
    )
    neuer_stand, alarme = fortschreiben(stand, h4, d1, faktor)
    seite = {
        **basis,
        "aktiv": True,
        "gold": gold,
        "kurs": h4[-1].close if h4 else None,
        "paxg_kurs": paxg,
        "kraken_faktor": faktor,
        "forward": forward_bilanz(neuer_stand),
        "offen": neuer_stand.get("offen"),
    }
    return seite, neuer_stand, alarme


def _senden(alarme: list[dict]) -> None:
    from trading_agent.ops.notify import (
        FileSink,
        GitHubIssueSink,
        Notification,
        Notifier,
        Severity,
        TelegramSink,
        WebPushSink,
    )

    tg = TelegramSink(min_severity=Severity.INFO)
    push = WebPushSink(min_severity=Severity.INFO)
    gh = GitHubIssueSink(erwaehnen="ozancanerd-ship-it")
    sinks: list = [FileSink(PROTOKOLL)]
    for s in (push, tg):
        if s.available():
            sinks.insert(0, s)
    if gh.available():
        sinks.append(gh)
    n = Notifier(sinks, max_per_window=10, dedup_window_s=0.0)
    jetzt = datetime.now(UTC)
    raus = sum(
        1
        for a in alarme
        if n.notify(
            Notification(
                severity=Severity.CRITICAL,
                title=a["titel"],
                body=a["text"],
                dedup_key=a["dedup_key"],
                ts=jetzt,
            )
        )
    )
    print(f"{raus} von {len(alarme)} Gold-S9-Alarm(en) verschickt ({n.active_sinks})")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="web/day_trading.json")
    ap.add_argument("--stand", default=STAND)
    ap.add_argument("--send", action="store_true", help="experimentelle Alarme verschicken")
    ap.add_argument(
        "--tage", type=int, default=400, help="H1-Fenster in Tagen (Yahoo-Limit ~730 Tage)"
    )
    args = ap.parse_args()

    stand_pfad = Path(args.stand)
    stand = None
    if stand_pfad.exists():
        try:
            stand = json.loads(stand_pfad.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            print(f"::warning::{stand_pfad} unlesbar — starte ohne Stand")

    seite, neuer_stand, alarme = asyncio.run(_bauen(args.tage, stand))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(seite, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    if neuer_stand is not None:
        stand_pfad.parent.mkdir(parents=True, exist_ok=True)
        stand_pfad.write_text(
            json.dumps(neuer_stand, indent=2, ensure_ascii=False, default=str), encoding="utf-8"
        )
    for a in alarme:
        print(f"ALARM {a['titel']}\n  {a['text']}")
    if args.send and alarme:
        _senden(alarme)
    print(json.dumps({k: v for k, v in seite.items() if k != "studien"}, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
