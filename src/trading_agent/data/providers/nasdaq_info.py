"""Aktien-Hintergrund von Nasdaq: Quartalszahlen-Termine, Sektor, Analystenkonsens.

WARUM

Masterplan §10 (Aktienanalyse) nennt Earnings, Branche, Sektor und Analystenrevisionen;
§8 (No-Trade) „wichtige News unmittelbar bevorstehen". Bis zum 27.09. wusste der Scan von
alledem nichts. Ein Swing-Trade in einer Aktie, der ueber einen Quartalszahlen-Termin
laeuft, ist ein anderer Trade: an diesem Tag springt der Kurs oft ueber jeden Stop
hinweg, und bei einem Turbo kann eine einzige Nacht den Knock-out bringen.

QUELLE

``api.nasdaq.com`` — dieselben Endpunkte, die die Nasdaq-Seite selbst benutzt. Frei, ohne
Schluessel. Ohne Browser-Kopfzeilen antwortet der Server nicht; mit ihnen schon. Die
Termine stammen von Zacks und sind teils geschaetzt, bis das Unternehmen sie bestaetigt —
das steht so in der App.

Dieses Modul parst nur. Abgerufen wird in ``scripts/fetch_aktien_info.py``.
"""

from __future__ import annotations

import math
import re
from datetime import date, datetime
from typing import Any

BASIS = "https://api.nasdaq.com/api"
KOPF = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Origin": "https://www.nasdaq.com",
    "Referer": "https://www.nasdaq.com/",
}

SEKTOR_DE = {
    "Technology": "Technologie",
    "Health Care": "Gesundheit",
    "Finance": "Finanzen",
    "Consumer Discretionary": "Zyklischer Konsum",
    "Consumer Staples": "Basiskonsum",
    "Energy": "Energie",
    "Industrials": "Industrie",
    "Utilities": "Versorger",
    "Real Estate": "Immobilien",
    "Telecommunications": "Telekommunikation",
    "Basic Materials": "Grundstoffe",
    "Miscellaneous": "Sonstiges",
}

ZEIT_DE = {
    "time-pre-market": "vor Börsenbeginn",
    "time-after-hours": "nach Börsenschluss",
}


def _geld(text: Any) -> float | None:
    """``"$1,234.50"`` → 1234.5; ``"N/A"`` → None."""
    if text is None:
        return None
    if isinstance(text, int | float) and not isinstance(text, bool):
        v = float(text)
        return v if math.isfinite(v) else None
    s = re.sub(r"[^0-9.\-]", "", str(text))
    if not s or s in {"-", "."}:
        return None
    try:
        v = float(s)
    except ValueError:
        return None
    return v if math.isfinite(v) else None


def kalender_zeilen(antwort: dict[str, Any] | None, tag: date) -> list[dict[str, Any]]:
    """Ein Tag aus ``/calendar/earnings?date=`` → je Unternehmen Termin, Zeit, Prognose."""
    rows = (((antwort or {}).get("data") or {}).get("rows")) or []
    aus = []
    for r in rows:
        if not isinstance(r, dict) or not r.get("symbol"):
            continue
        aus.append(
            {
                "symbol": str(r["symbol"]).upper().strip(),
                "datum": tag.isoformat(),
                "zeit": ZEIT_DE.get(str(r.get("time") or "")),
                "eps_prognose": _geld(r.get("epsForecast")),
                "analysten": int(_geld(r.get("noOfEsts")) or 0) or None,
                "eps_vorjahr": _geld(r.get("lastYearEPS")),
            }
        )
    return aus


def zusammenfassung(antwort: dict[str, Any] | None) -> dict[str, Any]:
    """``/quote/{SYM}/summary`` → Sektor, Branche, Kursziel, 52-Wochen-Spanne, Dividende."""
    d = (((antwort or {}).get("data") or {}).get("summaryData")) or {}

    def wert(k: str) -> Any:
        return (d.get(k) or {}).get("value")

    sektor = str(wert("Sector") or "").strip()
    hoch52 = tief52 = None
    spanne = str(wert("FiftTwoWeekHighLow") or "")
    if "/" in spanne:
        a, b = spanne.split("/", 1)
        hoch52, tief52 = _geld(a), _geld(b)
    aus = {
        "sektor_en": sektor or None,
        "sektor": SEKTOR_DE.get(sektor, sektor) or None,
        "branche": str(wert("Industry") or "").strip() or None,
        "kursziel_1j": _geld(wert("OneYrTarget")),
        "hoch52": hoch52,
        "tief52": tief52,
        "marktwert": _geld(wert("MarketCap")),
        "dividende_pct": _geld(wert("Yield")),
    }
    return {k: v for k, v in aus.items() if v is not None}


def kursziele(antwort: dict[str, Any] | None) -> dict[str, Any]:
    """``/analyst/{SYM}/targetprice`` → Konsens-Kursziel, Spanne, Kaufen/Halten/Verkaufen.

    Dazu die Veraenderung des Kursziels gegenueber vor drei Monaten — die einzige
    frei verfuegbare Annaeherung an „Analystenrevisionen" (Masterplan §10).
    """
    d = (antwort or {}).get("data") or {}
    k = d.get("consensusOverview") or {}
    aus: dict[str, Any] = {
        "kursziel": _geld(k.get("priceTarget")),
        "kursziel_tief": _geld(k.get("lowPriceTarget")),
        "kursziel_hoch": _geld(k.get("highPriceTarget")),
    }
    kaufen, halten, verkaufen = (_geld(k.get(x)) for x in ("buy", "hold", "sell"))
    if any(v is not None for v in (kaufen, halten, verkaufen)):
        aus["analysten"] = {
            "kaufen": int(kaufen or 0),
            "halten": int(halten or 0),
            "verkaufen": int(verkaufen or 0),
        }
    verlauf = [h for h in (d.get("historicalConsensus") or []) if isinstance(h, dict)]
    roh = [(_geld(h.get("x")), _geld(h.get("y"))) for h in verlauf]
    ziele: list[tuple[float, float]] = [(t, y) for t, y in roh if t is not None and y is not None]
    if len(ziele) >= 4:
        ziele.sort()
        jetzt_y, vorher_y = ziele[-1][1], ziele[-4][1]
        if vorher_y:
            aus["kursziel_3m_pct"] = round((jetzt_y / vorher_y - 1) * 100, 1)
    return {k2: v for k2, v in aus.items() if v is not None}


def tage_bis(datum_iso: str | None, heute: date) -> int | None:
    if not datum_iso:
        return None
    try:
        d = datetime.fromisoformat(str(datum_iso)).date()
    except ValueError:
        return None
    return (d - heute).days


__all__ = [
    "BASIS",
    "KOPF",
    "SEKTOR_DE",
    "kalender_zeilen",
    "kursziele",
    "tage_bis",
    "zusammenfassung",
]
