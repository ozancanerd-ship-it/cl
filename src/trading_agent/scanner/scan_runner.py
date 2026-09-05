"""Der Scan-Durchgang selbst — viele Instrumente, parallel, ohne Live-Pipeline.

Warum nicht die ``LivePipeline``: die ist fuer den Dauerbetrieb gebaut (Websocket,
Zustand, Snapshots) und arbeitet ein Instrument nach dem anderen ab. Fuer einen
Stichtags-Scan ueber hundert Paare ist das die falsche Form — dort zaehlt nur:
Bars holen, MTF bauen, bewerten, weiter.

Der Gewinn ist Zeit. Sequentiell braucht ein Instrument fuenf REST-Aufrufe; bei 100
Instrumenten sind das 500 Aufrufe hintereinander. Mit begrenzter Nebenlaeufigkeit
laufen sie ueberlappend, und der Ratenbegrenzer des HTTP-Clients bleibt trotzdem der
Taktgeber — wir umgehen ihn nicht, wir warten nur nicht unnoetig.

**Ein Ausfall kippt den Scan nicht.** Ein Instrument ohne Historie wird vermerkt und
uebersprungen; die Liste der Ausfaelle geht mit ins Ergebnis, damit ein duennes Ranking
nicht mit einem ruhigen Markt verwechselt wird.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from trading_agent.analysis.mtf import build_mtf_context
from trading_agent.core.enums import AssetClass, Timeframe
from trading_agent.utils.logging import get_logger

_log = get_logger("scanner.scan_runner")

#: Wie viel Historie je Zeitebene geholt wird. Grosszuegig gegenueber ``min_bars``
#: (M5 200, M15 200, H1 200, H4 120, D1 120), weil Boersen Luecken haben und ein
#: knapp bemessenes Fenster sonst genau bei den interessanten Werten reisst.
FENSTER: dict[Timeframe, timedelta] = {
    Timeframe.M5: timedelta(days=4),
    Timeframe.M15: timedelta(days=8),
    Timeframe.H1: timedelta(days=20),
    Timeframe.H4: timedelta(days=45),
    Timeframe.D1: timedelta(days=400),
}

#: Wie viele Instrumente gleichzeitig. Der Ratenbegrenzer im HTTP-Client bleibt die
#: eigentliche Bremse; hoehere Werte bringen nichts und riskieren nur einen Bann.
NEBENLAEUFIG = 8


@dataclass(slots=True)
class ScanErgebnis:
    """Was ein Durchgang geliefert hat — inklusive dessen, was gefehlt hat."""

    #: Nur die Bewertungen. Der MtfContext wird **nicht** aufgehoben: er haelt saemtliche
    #: Bars und Analyseobjekte aller Zeitebenen, und bei hundert Instrumenten sprengt das
    #: den Speicher (der erste Versuch wurde vom Kernel abgeraeumt, ohne Fehlermeldung).
    #: Wer den Kontext braucht, bekommt ihn im ``verarbeite``-Rueckruf — dort, wo er noch
    #: lebt, und genau einmal.
    chancen: list[Any] = field(default_factory=list)
    ausfaelle: dict[str, str] = field(default_factory=dict)
    #: Bewusst aussortiert (kein Fehler) — mit Begruendung, damit es nachvollziehbar ist.
    abgelehnt: dict[str, str] = field(default_factory=dict)
    dauer_s: float = 0.0

    @property
    def vollstaendig(self) -> bool:
        return not self.ausfaelle


async def _hole_reihen(
    provider: Any,
    instrument: str,
    ende: datetime,
    zeitebenen: Sequence[Timeframe],
    fenster: Mapping[Timeframe, timedelta],
) -> dict[Timeframe, list[Any]]:
    reihen: dict[Timeframe, list[Any]] = {}
    for tf in zeitebenen:
        start = ende - fenster[tf]
        bars = await provider.fetch_ohlcv(instrument, tf, start, ende)
        reihen[tf] = list(bars)
    return reihen


async def scanne(
    provider: Any,
    instrumente: Sequence[str],
    *,
    asset_class: AssetClass,
    bewerter: Any,
    zeitebenen: Sequence[Timeframe] = (
        Timeframe.M5,
        Timeframe.M15,
        Timeframe.H1,
        Timeframe.H4,
        Timeframe.D1,
    ),
    nebenlaeufig: int = NEBENLAEUFIG,
    fenster: Mapping[Timeframe, timedelta] | None = None,
    min_m5: int = 200,
    min_d1: int = 60,
    zusatz: Mapping[str, Mapping[str, Any]] | None = None,
    profil: Any = None,
    verarbeite: Any = None,
    pruefe: Any = None,
    fortschritt: Any = None,
) -> ScanErgebnis:
    """Bewertet alle ``instrumente`` und gibt die Chancen zurueck.

    ``bewerter`` ist ``chart_score.bewerte_chart``-kompatibel: ``(name, mtf, kurs)``.
    ``zusatz`` reicht je Instrument Kennzahlen durch (Umsatz, 24h-Bewegung …), die der
    Bewerter als Kontext bekommt, ohne dass der Scanner sie kennen muss.
    ``verarbeite(chance, mtf)`` wird direkt nach der Bewertung aufgerufen, solange der
    Kontext noch existiert — dort gehoert alles hin, was die Bars braucht (Zeichnung,
    Muster, Kommentar). Danach wird der Kontext freigegeben.
    ``pruefe(name, reihen)`` darf ein Instrument nach dem Laden ablehnen und gibt dann
    den Grund zurueck. Damit lassen sich Dinge aussortieren, die man erst an den Daten
    sieht — etwa ein "Coin", der in Wahrheit nur zu Boersenzeiten handelt.
    """
    t0 = datetime.now(UTC)
    ergebnis = ScanErgebnis()
    sem = asyncio.Semaphore(max(1, nebenlaeufig))
    ende = datetime.now(UTC)
    fw = dict(fenster or FENSTER)
    fertig = 0

    async def _eines(name: str) -> None:
        nonlocal fertig
        async with sem:
            try:
                reihen = await _hole_reihen(provider, name, ende, zeitebenen, fw)
                m5 = reihen.pop(Timeframe.M5, [])
                if len(m5) < min_m5:
                    ergebnis.ausfaelle[name] = f"zu wenig M5-Historie ({len(m5)})"
                    return
                d1 = reihen.get(Timeframe.D1) or []
                if len(d1) < min_d1:
                    ergebnis.ausfaelle[name] = f"zu wenig Tageshistorie ({len(d1)})"
                    return
                if pruefe is not None:
                    grund = pruefe(name, {Timeframe.M5: m5, **reihen})
                    if grund:
                        ergebnis.abgelehnt[name] = grund
                        return
                mtf = build_mtf_context(
                    m5,
                    instrument=name,
                    asset_class=asset_class,
                    now=m5[-1].close_time,
                    native_higher={tf: b for tf, b in reihen.items() if b},
                )
                extra: dict[str, Any] = {}
                if profil is not None:
                    extra["profil"] = profil
                chance = bewerter(
                    name,
                    mtf,
                    m5[-1].close,
                    zusatz=dict((zusatz or {}).get(name, {})),
                    **extra,
                )
                if verarbeite is not None:
                    verarbeite(chance, mtf)
                # Kontext bewusst fallenlassen: er ist das Speicherproblem, nicht die Chance.
                del mtf, reihen, m5
                ergebnis.chancen.append(chance)
            except Exception as exc:
                ergebnis.ausfaelle[name] = f"{type(exc).__name__}: {exc}"
            finally:
                fertig += 1
                if fortschritt is not None and fertig % 10 == 0:
                    fortschritt(fertig, len(instrumente))

    await asyncio.gather(*(_eines(n) for n in instrumente))
    ergebnis.chancen.sort(key=lambda c: -c.score)
    ergebnis.dauer_s = round((datetime.now(UTC) - t0).total_seconds(), 1)
    if ergebnis.ausfaelle:
        _log.warning(
            "Scan mit Ausfaellen",
            extra={"anzahl": len(ergebnis.ausfaelle), "von": len(instrumente)},
        )
    return ergebnis


def handelt_durchgehend(tagesbars: Sequence[Any], *, tage: int = 90, mindest: float = 0.5) -> bool:
    """Laeuft das Instrument wirklich 24/7 — oder nur zu Boersenzeiten?

    Der Grund: die Boerse fuehrt auch tokenisierte Aktien und ETFs als USDT-Paare
    (NVDAB, TSLAB, QQQB, SOXLB …). Die sehen im Universum aus wie Altcoins, sind aber
    Wochenendpausen, Nachbildungsfehler und — bei den gehebelten ETF-Token — genau das,
    was hier nicht gehandelt werden soll. Eine Ausschlussliste waere wieder eine feste
    Liste; sie muesste bei jedem neuen Produkt nachgepflegt werden.

    Die Daten sagen es selbst: ein echter Coin hat auch samstags eine Tageskerze. Wer
    an mehr als der Haelfte der Wochenendtage keine hat, ist keiner.
    """
    letzte = list(tagesbars)[-tage:]
    if len(letzte) < 20:
        return True  # zu wenig, um es zu behaupten — nicht ablehnen
    wochenende = 0
    for b in letzte:
        t = getattr(b, "open_time", None)
        if t is not None and t.weekday() >= 5:
            wochenende += 1
    # In `tage` Kalendertagen liegen rund 2/7 Wochenendtage. Beobachtet werden aber nur
    # vorhandene Bars, deshalb der Vergleich gegen den Anteil.
    anteil = wochenende / len(letzte)
    return anteil >= mindest * (2.0 / 7.0)


async def schliesse(provider: Any) -> None:
    with contextlib.suppress(Exception):
        await provider.aclose()


__all__ = ["FENSTER", "NEBENLAEUFIG", "ScanErgebnis", "scanne", "schliesse"]
