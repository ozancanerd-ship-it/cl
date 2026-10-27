"""Rückblick auf Verkaufshinweise — wohin ging der Kurs NACH dem Hinweis?

Warum es das gibt: Ozan will keinen Verkauf, nach dem es zwei Wochen später steigt (01.10.),
und die Gewinnschutz-Studie (13.10.) hat keinen Vorteil belegt. Statt zu meinen, wird
gemessen: jeder Hinweis, der zum Verkaufen/Absichern rät, wird mit dem Kurs festgehalten
und nach 1, 3, 7 und 14 Tagen mit dem dann aktuellen Scan-Kurs verglichen.

Regeln, damit die Messung nicht schummelt:

* Nur der Kurs, der zum Hinweis galt, und spätere Scan-Kurse desselben Instruments. Kein
  Nachtragen aus der Zukunft: ist ein Horizont um mehr als ``FENSTER_H`` Stunden
  verstrichen, ohne gemessen worden zu sein, bleibt er LEER (``"x"``), er wird nie später
  gefüllt.
* Gemessen wird nur mit einem frischen Scan (``MAX_SCAN_ALTER_MIN``).
* Bewertet wird in Sicht der Position: ``gegen_pct`` > 0 heißt, der Kurs lief nach dem
  Hinweis in Richtung der Position weiter — der Hinweis hätte Gewinn gekostet.
* Unter ``GENUG`` Fällen je Gruppe ist jede Zahl Rauschen und trägt die Marke ``belastbar=False``.

Das Journal liegt versiegelt im Wächter-Stand (nicht öffentlich). Öffentlich werden nur
Zähler und Mediane ohne Namen und Mengen ausgegeben.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from statistics import median
from typing import Any

#: Hinweisarten, die zum Verkaufen oder Absichern raten — und ihre Gruppe im Rückblick.
GRUPPEN = {
    "ziel": "teilverkauf",
    "gewinnschutz": "teilverkauf",
    "gewinnschutz_teil": "teilverkauf",
    "gewinn_rueckgang": "teilverkauf",
    "gewinnschutz_exit": "ausstieg",
    "stop": "stop",
    "puffer_kritisch": "knockout",
    "puffer_eng": "knockout",
}
HORIZONTE_TAGE = (1, 3, 7, 14)
#: So lange nach Ablauf eines Horizonts darf noch gemessen werden (Läufe sind periodisch).
FENSTER_H = 12.0
MAX_SCAN_ALTER_MIN = 60.0
GENUG = 30
MAX_EINTRAEGE = 400


def _iso(t: datetime) -> str:
    return t.astimezone(UTC).isoformat()


def _lies(t: Any) -> datetime | None:
    try:
        d = datetime.fromisoformat(str(t))
    except (TypeError, ValueError):
        return None
    return d if d.tzinfo else d.replace(tzinfo=UTC)


def fortschreiben(
    journal: list[dict[str, Any]],
    ereignisse: list[Any],
    idx: Any,
    jetzt: datetime,
) -> list[dict[str, Any]]:
    """Neue Hinweise aufnehmen und fällige Horizonte messen. Gibt das neue Journal zurück."""
    aus = [dict(e) for e in journal if isinstance(e, dict)]
    bekannt = {e.get("id") for e in aus}
    for ev in ereignisse:
        art = str(getattr(ev, "art", ""))
        if art not in GRUPPEN:
            continue
        kurs = getattr(ev, "kurs", None)
        inst = getattr(ev, "instrument", "")
        lang = getattr(ev, "lang", None)
        if not inst or not kurs or kurs <= 0 or lang is None:
            continue  # ohne Instrument/Kurs/Richtung ist nichts messbar
        eid = ev.merker
        if eid in bekannt:
            continue
        bekannt.add(eid)
        aus.append(
            {
                "id": eid,
                "art": art,
                "ts": _iso(jetzt),
                "inst": inst,
                "kurs": float(kurs),
                "lang": bool(lang),
                "h": {},
            }
        )
    scan_ok = getattr(idx, "alter_min", None) is not None and idx.alter_min <= MAX_SCAN_ALTER_MIN
    for e in aus:
        t0 = _lies(e.get("ts"))
        if t0 is None:
            continue
        h = e.setdefault("h", {})
        for tage in HORIZONTE_TAGE:
            k = str(tage)
            if k in h:
                continue
            faellig = t0 + timedelta(days=tage)
            if jetzt < faellig:
                continue
            if jetzt > faellig + timedelta(hours=FENSTER_H):
                h[k] = "x"  # verpasst — nie aus der Zukunft nachtragen
                continue
            if not scan_ok:
                continue
            row = idx.zeile(str(e.get("inst")), nur_genau=True)
            kurs = row.get("kurs") if isinstance(row, dict) else None
            if isinstance(kurs, int | float) and kurs > 0:
                h[k] = float(kurs)
    aus.sort(key=lambda e: str(e.get("ts")))
    return aus[-MAX_EINTRAEGE:]


def gegen_pct(e: dict[str, Any], tage: int) -> float | None:
    """Kursänderung seit dem Hinweis in Sicht der Position (>0 = lief für sie weiter)."""
    k = (e.get("h") or {}).get(str(tage))
    if not isinstance(k, int | float) or not e.get("kurs"):
        return None
    pct = (float(k) / float(e["kurs"]) - 1.0) * 100.0
    return pct if e.get("lang", True) else -pct


def zusammenfassung(journal: list[dict[str, Any]]) -> dict[str, Any]:
    """Öffentlich taugliche Zähler: je Gruppe und Horizont Anzahl, Anteil und Median."""
    aus: dict[str, Any] = {"genug": GENUG, "n": 0, "gruppen": {}}
    for e in journal:
        g = GRUPPEN.get(str(e.get("art")))
        if g is None:
            continue
        aus["n"] += 1
        eintrag = aus["gruppen"].setdefault(g, {"n": 0, "h": {}})
        eintrag["n"] += 1
    for g, eintrag in aus["gruppen"].items():
        for tage in HORIZONTE_TAGE:
            werte = [
                w
                for e in journal
                if GRUPPEN.get(str(e.get("art"))) == g
                for w in [gegen_pct(e, tage)]
                if w is not None
            ]
            if not werte:
                continue
            eintrag["h"][str(tage)] = {
                "n": len(werte),
                "weiter_gelaufen": round(sum(1 for w in werte if w > 0) / len(werte), 3),
                "median_pct": round(median(werte), 2),
                "belastbar": len(werte) >= GENUG,
            }
    return aus


__all__ = ["GRUPPEN", "HORIZONTE_TAGE", "fortschreiben", "gegen_pct", "zusammenfassung"]
