"""Notenbank-Sitzungen lesen: was wurde entschieden, und was heisst das fuer die Klassen?

Reine Textarbeit, kein Netz (das macht ``scripts/fetch_notenbanken.py``). Die Einordnung
ist REGELBASIERT und ein Hinweis, kein Signal: ob eine straffe Fed Krypto, Gold oder
Aktien tatsaechlich in den Folgetagen bewegt, ist hier nicht belegt (Studie steht aus)."""

from __future__ import annotations

import html
import re
from typing import Any

#: Wirkung einer straffen / lockeren Geldpolitik je Klasse — die uebliche Lehrbuch-Richtung,
#: ausdruecklich keine gemessene Gesetzmaessigkeit.
WIRKUNG = {
    "straff": {
        "krypto": "Gegenwind: hoehere Zinsen ziehen Liquiditaet aus Risikoanlagen",
        "gold": "Gegenwind: hoehere reale Zinsen machen zinslose Anlagen unattraktiver",
        "aktien": "Gegenwind vor allem fuer Wachstums- und Tech-Werte (hoeher diskontierte Gewinne)",
    },
    "locker": {
        "krypto": "Rueckenwind: mehr Liquiditaet, Risiko wird eher belohnt",
        "gold": "Rueckenwind: sinkende reale Zinsen",
        "aktien": "Rueckenwind, besonders fuer Wachstumswerte",
    },
    "neutral": {
        "krypto": "keine neue Richtung aus der Geldpolitik",
        "gold": "keine neue Richtung aus der Geldpolitik",
        "aktien": "keine neue Richtung aus der Geldpolitik",
    },
}

TAKTIK = {
    "straff": [
        "Hebel-Positionen: Knock-out-Puffer grosszuegig halten — Zinsueberraschungen bewegen schnell.",
        "Nachlegen nur mit Alarm-Tor und Stop, nicht auf Verdacht.",
        "Vor den naechsten Zins-/Inflationsterminen keine neuen Hebel-Positionen eroeffnen.",
    ],
    "locker": [
        "Risikoanlagen duerfen etwas mehr Gewicht bekommen — aber nur ueber Signale mit Alarm-Tor.",
        "Stops nicht lockern: Rueckenwind ersetzt keinen Stop.",
    ],
    "neutral": [
        "Keine Anpassung aus der Geldpolitik noetig — die Einzelsignale entscheiden.",
    ],
}


def text_aus_html(roh: str) -> str:
    t = re.sub(r"<script.*?</script>|<style.*?</style>", " ", roh or "", flags=re.S | re.I)
    t = html.unescape(re.sub(r"<[^>]+>", " ", t))
    return re.sub(r"\s+", " ", t).strip()


def fed_statement(seite: str) -> str:
    """Der Beschlusstext einer FOMC-Mitteilung (ohne Navigation und Fusszeile)."""
    t = text_aus_html(seite)
    a = t.find("approved the following statement")
    if a < 0:
        a = t.find("The Committee decided")
    if a < 0:
        return ""
    e = t.find("For media inquiries", a)
    return t[a: e if e > 0 else a + 3500].strip()


def fed_entscheid(text: str) -> dict[str, Any]:
    """Zinsentscheid aus dem Beschlusstext. ``aktion`` ist unbekannt, wenn nichts passt."""
    t = text or ""
    aktion = "unbekannt"
    m = re.search(
        r"decided to (raise|lower|maintain|keep|reduce|increase)\b[^.]*?target range", t, re.I
    )
    if m:
        w = m.group(1).lower()
        aktion = "anheben" if w in ("raise", "increase") else "senken" if w in ("lower", "reduce") else "halten"
    schritt = re.search(r"by (\d+/\d+|\d+(?:\.\d+)?)\s*percentage point", t, re.I)
    spanne = re.search(r"target range[^.]*? to ([\d\-/ ]+(?: to [\d\-/ ]+)?)\s*percent", t, re.I)
    stimmen = re.search(r"(\d+)\s*[–\-]\s*(\d+) vote", t)
    return {
        "aktion": aktion,
        "schritt": schritt.group(1) if schritt else None,
        "spanne": spanne.group(1).strip() if spanne else None,
        "stimmen": f"{stimmen.group(1)}:{stimmen.group(2)}" if stimmen else None,
        "inflation_hoch": bool(re.search(r"inflation (remains|is|has remained)? ?elevated", t, re.I)),
        "arbeitsmarkt": "stabil" if re.search(r"unemployment rate has (changed little|remained low)", t, re.I) else None,
        "unsicherheit": bool(re.search(r"uncertainty[^.]*elevated", t, re.I)),
    }


def ton_von(aktion: str) -> str:
    return {"anheben": "straff", "senken": "locker", "halten": "neutral"}.get(aktion, "neutral")


def _bruch(s: str | None) -> str:
    if not s:
        return ""
    return {"1/4": "0,25", "1/2": "0,5", "3/4": "0,75"}.get(s, s.replace(".", ","))


def fed_satz(e: dict[str, Any]) -> str:
    a = e.get("aktion")
    sp = f" auf {e['spanne'].replace(' to ', ' bis ').replace('-', ' ')} %" if e.get("spanne") else ""
    st = f" ({e['stimmen']} Stimmen)" if e.get("stimmen") else ""
    if a == "anheben":
        s = _bruch(e.get("schritt"))
        return f"Die Fed hat den Leitzins{(' um ' + s + ' Prozentpunkte') if s else ''} angehoben{sp}{st}."
    if a == "senken":
        s = _bruch(e.get("schritt"))
        return f"Die Fed hat den Leitzins{(' um ' + s + ' Prozentpunkte') if s else ''} gesenkt{sp}{st}."
    if a == "halten":
        return f"Die Fed hat den Leitzins unveraendert gelassen{sp}{st}."
    return "Zur Zinsentscheidung der Fed liess sich im Text nichts Eindeutiges lesen — Mitteilung selbst oeffnen."


SCHLAGWORT = re.compile(
    r"monetary policy decision|monetary policy summary|interest rate|bank rate|fomc|"
    r"mpc|minutes|projections|press conference|monetary policy statement|account of",
    re.I,
)


RAUSCHEN = re.compile(r"FXJSC|Sub-?Committee|Money Markets Code|discount rate|Code Committee", re.I)


def relevante(items: list[dict[str, Any]], n: int = 6) -> list[dict[str, Any]]:
    """Aus einem Notenbank-Feed die Eintraege, die mit Geldpolitik zu tun haben."""
    return [
        i
        for i in items
        if SCHLAGWORT.search(i.get("titel", "")) and not RAUSCHEN.search(i.get("titel", ""))
    ][:n]


def einordnung(ton: str, klassen_anteile: dict[str, float] | None = None) -> dict[str, Any]:
    """Wirkung je Klasse und Taktik-Hinweise. Mit Depotanteilen (0..1) kommt der Satz zum Depot dazu."""
    w = WIRKUNG.get(ton, WIRKUNG["neutral"])
    out: dict[str, Any] = {"ton": ton, "wirkung": w, "taktik": TAKTIK.get(ton, TAKTIK["neutral"])}
    if klassen_anteile:
        out["depot"] = {k: round(v, 3) for k, v in klassen_anteile.items()}
    return out
