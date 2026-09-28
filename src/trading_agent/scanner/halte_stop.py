"""Der Halte-Stop — eine Stopmarke fuer JEDEN Wert mit Tageskerzen.

WARUM ES DAS GIBT

Ozan, 27.09.: „Die Stops auf meinem Portfolio 24/7 analysieren und immer neu setzen."

Bis dahin bekam eine Depotposition nur dann einen Stop, wenn der Scanner fuer genau
diesen Wert gerade ein Long-Setup mit Invalidierung hatte. Bei einer Position, in der man
laengst drin ist, ist das die Ausnahme: der Einstieg ist vorbei, ein frisches Setup gibt es
selten. Die meisten Depotwerte standen deshalb ganz ohne Stop da — und ohne Stop gibt es
auch keinen Verkaufsalarm.

WAS ER IST

Der Chandelier-Stop nach LeBeau: hoechstes Hoch der letzten 22 Tageskerzen minus
k × ATR(14). Er haengt nur an Kerzen, nicht an einem Setup, und wird nur nachgezogen.

WARUM k = 5 — UND WAS ER KOSTET

Vorab registriert und gemessen in ``docs/HALTE-STOP-STUDIE-2026-09.md`` (24 Coins seit
11/2023, 103 Aktien seit 2024, zeitlich geteilt). Das Ergebnis, ohne Schoenfaerberei:

* Er schneidet die schlimmen Faelle ab. Bei Coins lag das schlechteste Zwanzigstel ohne
  Stop bei rund −54 % in 90 Tagen, mit k = 5 bei −28 % bzw. −23 %.
* Er kostet Rendite, wenn es laeuft. In der Aufwaertsphase der Coins (+20 % ohne Stop)
  blieben mit Stop +3 % uebrig; bei Aktien +5/+10 % gegen 0/+2 %. Wer ausgestoppt wird,
  verpasst die Erholung, wenn er nicht wieder einsteigt.
* Keine Stufe erfuellte die vorab gesetzte Huerde („hoechstens 1 Prozentpunkt Rendite
  schlechter als ohne Stop"). Nach der vorab festgelegten Regel wird er trotzdem gesetzt
  — Ozan will ihn —, mit dem weitesten k und mit diesem Hinweis in der App.

Er ist damit eine **Versicherung gegen den Absturz, kein Renditebringer**. Engere Stops
aus einem echten Setup (die Invalidierung) gehen weiter vor; der Halte-Stop ist der Boden,
unter den keine Position ohne Schutz faellt.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

#: Aus der Studie (vorab registriert). Nicht ohne neue Studie aendern.
K_ATR = 5.0
N_ATR = 14
N_HOCH = 22


def _zahl(x: Any) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def atr_wilder(
    hoch: Sequence[float], tief: Sequence[float], schluss: Sequence[float], n: int = N_ATR
) -> float | None:
    """ATR nach Wilder ueber die ganze Reihe; ``None`` bei zu wenig Kerzen."""
    if len(schluss) <= n or not (len(hoch) == len(tief) == len(schluss)):
        return None
    tr: list[float] = [hoch[0] - tief[0]]
    for i in range(1, len(schluss)):
        tr.append(
            max(
                hoch[i] - tief[i],
                abs(hoch[i] - schluss[i - 1]),
                abs(tief[i] - schluss[i - 1]),
            )
        )
    atr = sum(tr[1 : n + 1]) / n
    for i in range(n + 1, len(schluss)):
        atr = (atr * (n - 1) + tr[i]) / n
    return atr if math.isfinite(atr) and atr > 0 else None


def halte_werte(bars: Sequence[Any], *, k: float = K_ATR) -> dict[str, Any] | None:
    """Aus abgeschlossenen Tageskerzen die Halte-Stops fuer long und short.

    ``bars``: Objekte mit ``open_time``, ``high``, ``low``, ``close`` (aufsteigend). Gibt
    ``None`` zurueck, wenn es fuer eine ehrliche ATR nicht reicht — dann lieber keine Marke
    als eine aus drei Kerzen.
    """
    h: list[float] = []
    lo: list[float] = []
    c: list[float] = []
    letzter = None
    for b in bars or ():
        hv, lv, cv = (
            _zahl(getattr(b, "high", None)),
            _zahl(getattr(b, "low", None)),
            _zahl(getattr(b, "close", None)),
        )
        if hv is None or lv is None or cv is None or cv <= 0:
            continue
        h.append(hv)
        lo.append(lv)
        c.append(cv)
        letzter = getattr(b, "open_time", None)
    if len(c) < max(N_ATR + 2, N_HOCH):
        return None
    atr = atr_wilder(h, lo, c)
    if atr is None:
        return None
    hoch22 = max(h[-N_HOCH:])
    tief22 = min(lo[-N_HOCH:])
    stop_long = hoch22 - k * atr
    stop_short = tief22 + k * atr
    return {
        "k": k,
        "atr": round(atr, 10),
        "atr_pct": round(atr / c[-1] * 100, 3),
        "hoch22": hoch22,
        "tief22": tief22,
        # Unter null gibt es keinen Stop — bei einem Wert, der mehr als 1/k seiner
        # Hoechstmarke als Tagesschwankung hat, ist der Chandelier schlicht nicht definiert.
        "stop_long": stop_long if stop_long > 0 else None,
        "stop_short": stop_short,
        "stand": letzter.date().isoformat()
        if letzter is not None and hasattr(letzter, "date")
        else None,
    }


__all__ = ["K_ATR", "N_ATR", "N_HOCH", "atr_wilder", "halte_werte"]
