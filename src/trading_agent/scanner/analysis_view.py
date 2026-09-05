"""Was die AI sieht — als Zeichnung, als Tabelle, als Satz.

Ozans Vorgabe woertlich: „Diese Elemente sollen nicht nur als Text irgendwo stehen. SIE
SOLLEN DIREKT IM CHART EINGEZEICHNET WERDEN." Und: „Ich moechte verstehen koennen: WAS
SIEHT DIE AI? WARUM IST ES WICHTIG? WAS ERWARTET SIE? WAS WUERDE DIE THESE INVALIDIEREN?"

Dieses Modul uebersetzt den fertigen MTF-Kontext in drei Ausgaben:

1. :func:`zeichnung` — Koordinaten. Swings mit HH/HL/LH/LL, Struktubrueche, Liquiditaet,
   FVGs, Order Blocks, Premium/Discount, Entry/SL/TP. Die App zeichnet daraus, sie
   rechnet nichts nach — sonst gaebe es zwei Wahrheiten.
2. :func:`mtf_tabelle` — je Zeitebene eine Zeile: Regime, Struktur, letzter Bruch,
   naechste Liquiditaet, Lage in der Spanne, und ein Satz dazu.
3. :func:`kommentar` — die Erklaerung in Worten, gebaut aus genau denselben Zahlen.

**Nichts hier erfindet etwas.** Jeder Satz kommt aus einem berechneten Wert; steht der
Wert nicht zur Verfuegung, fehlt der Satz. Ein Kommentar, der ueber die Daten
hinausgeht, waere schlimmer als keiner.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from trading_agent.core.enums import Direction, Timeframe

#: Reihenfolge, in der die Zeitebenen ueberall auftauchen: gross nach klein.
EBENEN: tuple[Timeframe, ...] = (Timeframe.D1, Timeframe.H4, Timeframe.H1, Timeframe.M15)

_REGIME_TEXT = {
    "trend_up": "Aufwaertstrend",
    "trend_down": "Abwaertstrend",
    "range": "Seitwaerts",
    "unclear": "unklar",
}
_VOLA_TEXT = {"low": "ruhig", "normal": "normal", "high": "hoch", "extreme": "extrem"}
_PHASE_TEXT = {
    "expansion": "Ausdehnung",
    "contraction": "Zusammenziehen",
    "accumulation": "Akkumulation",
    "distribution": "Distribution",
    "neutral": "neutral",
}


def _v(obj: Any, default: str = "") -> str:
    return str(getattr(obj, "value", obj) if obj is not None else default)


def _iso(ts: Any) -> str | None:
    return ts.isoformat() if isinstance(ts, datetime) else None


def _richtung_text(d: Direction | None) -> str:
    return "LONG" if d is Direction.LONG else "SHORT" if d is Direction.SHORT else "—"


# --------------------------------------------------------------------------- Zeichnung


def _swings(tfc: Any, grenze: int = 40) -> list[dict[str, Any]]:
    out = []
    for s in list(getattr(tfc, "swings", ()) or ())[-grenze:]:
        out.append(
            {
                "t": _iso(getattr(s, "timestamp", None)),
                "p": float(getattr(s, "price", 0.0)),
                "hoch": bool(getattr(s, "is_high", False)),
                "label": _v(getattr(s, "label", None)).upper() or None,
                "bein_atr": round(float(getattr(s, "leg_size_atr", 0.0) or 0.0), 2),
            }
        )
    return out


def _brueche(tfc: Any, grenze: int = 8) -> list[dict[str, Any]]:
    out = []
    for b in list(getattr(tfc, "structure_breaks", ()) or ())[-grenze:]:
        out.append(
            {
                "t": _iso(getattr(b, "break_bar_timestamp", None)),
                "p": float(getattr(b, "broken_level_price", 0.0)),
                "schluss": float(getattr(b, "break_close", 0.0)),
                "art": _v(getattr(b, "kind", None)).upper(),
                "richtung": _v(getattr(b, "direction", None)),
                "abstand_atr": round(float(getattr(b, "break_distance_atr", 0.0) or 0.0), 2),
            }
        )
    return out


def _liquiditaet(tfc: Any, grenze: int = 14) -> list[dict[str, Any]]:
    lv = list(getattr(tfc, "liquidity", ()) or ())
    lv.sort(key=lambda x: -float(getattr(x, "strength", 0.0) or 0.0))
    out = []
    for x in lv[:grenze]:
        out.append(
            {
                "p": float(getattr(x, "price", 0.0)),
                "typ": _v(getattr(x, "type", None)),
                "seite": _v(getattr(x, "side", None)),
                "staerke": round(float(getattr(x, "strength", 0.0) or 0.0), 3),
                "beruehrungen": int(getattr(x, "touch_count", 0) or 0),
                "zustand": _v(getattr(x, "state", None)),
            }
        )
    return out


def _zonen(tfc: Any, grenze: int = 12) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for quelle, art in (
        (getattr(tfc, "fvgs", ()) or (), "FVG"),
        (getattr(tfc, "order_blocks", ()) or (), "OB"),
    ):
        for z in quelle:
            zustand = _v(getattr(z, "state", None))
            if zustand not in ("unmitigated", "partial"):
                continue
            out.append(
                {
                    "art": art,
                    "von": float(getattr(z, "zone_low", 0.0)),
                    "bis": float(getattr(z, "zone_high", 0.0)),
                    "richtung": _v(getattr(z, "direction", None)),
                    "zustand": zustand,
                    "gefuellt": round(float(getattr(z, "fill_fraction", 0.0) or 0.0), 2),
                    "alter": int(getattr(z, "age_bars", 0) or 0),
                    "t": _iso(getattr(z, "created_bar", None) or getattr(z, "ob_bar", None)),
                }
            )
    out.sort(key=lambda z: z["alter"])
    return out[:grenze]


def _pd(tfc: Any) -> dict[str, Any] | None:
    pd = getattr(tfc, "premium_discount", None)
    if pd is None:
        return None
    return {
        "tief": float(getattr(pd, "range_low", 0.0)),
        "hoch": float(getattr(pd, "range_high", 0.0)),
        "mitte": float(getattr(pd, "equilibrium", 0.0)),
        "position": round(float(getattr(pd, "pd_position", 0.0) or 0.0), 3),
        "zone": _v(getattr(pd, "zone", None)),
    }


def _kerzen(tfc: Any, grenze: int) -> list[list[float | str | None]]:
    """Kerzen kompakt als Listen — [t, o, h, l, c, v]. Ein Dict je Kerze waere dreimal so gross."""
    out: list[list[float | str | None]] = []
    for b in list(getattr(tfc, "bars", ()) or ())[-grenze:]:
        out.append(
            [
                _iso(getattr(b, "open_time", None)),
                float(b.open),
                float(b.high),
                float(b.low),
                float(b.close),
                float(getattr(b, "volume", 0.0) or 0.0),
            ]
        )
    return out


def zeichnung(
    mtf: Any,
    *,
    ebenen: Sequence[Timeframe] = EBENEN,
    kerzen: int = 300,
) -> dict[str, Any]:
    """Alles, was die App auf den Chart malen soll — je Zeitebene ein Block."""
    per_tf: dict[Timeframe, Any] = dict(getattr(mtf, "per_tf", {}) or {})
    out: dict[str, Any] = {}
    for tf in ebenen:
        tfc = per_tf.get(tf)
        if tfc is None:
            continue
        out[tf.value] = {
            "kerzen": _kerzen(tfc, kerzen),
            "swings": _swings(tfc),
            "brueche": _brueche(tfc),
            "liquiditaet": _liquiditaet(tfc),
            "zonen": _zonen(tfc),
            "pd": _pd(tfc),
            "atr": round(float(getattr(tfc, "atr", 0.0) or 0.0), 8),
        }
    return out


# --------------------------------------------------------------------------- MTF-Tabelle


def _naechste_liquiditaet(tfc: Any, kurs: float) -> tuple[float | None, float | None]:
    """Naechstes offenes Level ueber und unter dem Kurs."""
    oben: list[float] = []
    unten: list[float] = []
    for x in getattr(tfc, "liquidity", ()) or ():
        if _v(getattr(x, "state", None)) == "swept":
            continue
        p = float(getattr(x, "price", 0.0))
        (oben if p > kurs else unten).append(p)
    return (min(oben) if oben else None, max(unten) if unten else None)


def mtf_tabelle(
    mtf: Any, kurs: float, *, ebenen: Sequence[Timeframe] = EBENEN
) -> list[dict[str, Any]]:
    """Je Zeitebene eine Zeile — das ist die Tabelle, die Ozan sehen will."""
    per_tf: dict[Timeframe, Any] = dict(getattr(mtf, "per_tf", {}) or {})
    zeilen: list[dict[str, Any]] = []
    for tf in ebenen:
        tfc = per_tf.get(tf)
        if tfc is None:
            continue
        reg = getattr(tfc, "regime", None)
        richtung = _v(getattr(reg, "directional", None), "unclear")
        brueche = list(getattr(tfc, "structure_breaks", ()) or ())
        letzter = brueche[-1] if brueche else None
        oben, unten = _naechste_liquiditaet(tfc, kurs)
        pd = _pd(tfc)
        satz = _tf_satz(tf, richtung, reg, letzter, pd, oben, unten, kurs)
        zeilen.append(
            {
                "tf": tf.value,
                "regime": _REGIME_TEXT.get(richtung, richtung),
                "regime_roh": richtung,
                "staerke": round(float(getattr(reg, "directional_score", 0.0) or 0.0), 2),
                "volatilitaet": _VOLA_TEXT.get(_v(getattr(reg, "volatility", None)), "—"),
                "volatilitaet_pct": round(float(getattr(reg, "volatility_pct", 0.0) or 0.0), 1),
                "phase": _PHASE_TEXT.get(_v(getattr(reg, "phase", None)), "—"),
                "letzter_bruch": (
                    {
                        "art": _v(getattr(letzter, "kind", None)).upper(),
                        "richtung": _v(getattr(letzter, "direction", None)),
                        "preis": float(getattr(letzter, "broken_level_price", 0.0)),
                        "t": _iso(getattr(letzter, "break_bar_timestamp", None)),
                    }
                    if letzter is not None
                    else None
                ),
                "liquiditaet_oben": oben,
                "liquiditaet_unten": unten,
                "pd": pd,
                "atr": round(float(getattr(tfc, "atr", 0.0) or 0.0), 8),
                "datenguete": round(float(getattr(tfc, "data_confidence", 0.0) or 0.0), 2),
                "satz": satz,
            }
        )
    return zeilen


def _tf_satz(
    tf: Timeframe,
    richtung: str,
    reg: Any,
    letzter: Any,
    pd: dict[str, Any] | None,
    oben: float | None,
    unten: float | None,
    kurs: float,
) -> str:
    teile = [f"{tf.value}: {_REGIME_TEXT.get(richtung, richtung)}"]
    stark = float(getattr(reg, "directional_score", 0.0) or 0.0)
    if richtung in ("trend_up", "trend_down") and stark:
        teile[0] += f" (Staerke {stark:.2f})"
    if letzter is not None:
        art = _v(getattr(letzter, "kind", None)).upper()
        rr = _v(getattr(letzter, "direction", None))
        pfeil = "nach oben" if rr == "bullish" else "nach unten"
        teile.append(
            f"letzter {art} {pfeil} bei {float(getattr(letzter, 'broken_level_price', 0.0)):g}"
        )
    if pd:
        zone = {
            "premium": "im teuren Drittel",
            "discount": "im guenstigen Drittel",
            "equilibrium": "in der Mitte",
        }.get(pd["zone"], pd["zone"])
        teile.append(f"Kurs {zone} der Spanne")
    ziele = []
    if oben is not None:
        ziele.append(f"{oben:g} darueber ({(oben / kurs - 1) * 100:+.1f} %)")
    if unten is not None:
        ziele.append(f"{unten:g} darunter ({(unten / kurs - 1) * 100:+.1f} %)")
    if ziele:
        teile.append("naechste Liquiditaet " + " und ".join(ziele))
    return ", ".join(teile) + "."


# --------------------------------------------------------------------------- Kommentar


def kommentar(
    chance: Any,
    zeilen: Sequence[dict[str, Any]],
    muster: Sequence[Any],
    *,
    zusatz: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Was sieht die AI, warum jetzt, was erwartet sie, was waere sie widerlegt.

    Jeder Satz haengt an einer Zahl aus der Analyse. Fehlt die Zahl, fehlt der Satz.
    """
    z = dict(zusatz or {})
    richtung = getattr(chance, "richtung", None)
    rt = _richtung_text(richtung)
    kurs = float(getattr(chance, "kurs", 0.0) or 0.0)

    was_ich_sehe = [zeile["satz"] for zeile in zeilen]
    for m in list(muster)[:3]:
        was_ich_sehe.append(f"{m.zeitebene}: {m.name} — {m.beschreibung}")

    warum_jetzt: list[str] = []
    for f in getattr(chance, "faktoren", ()) or ():
        if f.anteil >= 0.5:
            warum_jetzt.append(f"{f.name.replace('_', ' ')}: {f.detail}")
    rr = getattr(chance, "rr", None)
    if rr:
        warum_jetzt.append(f"Chance-Risiko-Verhaeltnis 1:{rr:.2f} bis TP2")
    move = getattr(chance, "erwartete_bewegung_pct", None)
    if move:
        warum_jetzt.append(f"erwartete Bewegung {move:+.1f} % bis TP2")
    if z.get("umsatz_24h"):
        warum_jetzt.append(f"24h-Umsatz {float(z['umsatz_24h']) / 1e6:.0f} Mio USDT — handelbar")

    erwartung: list[str] = []
    ziel = getattr(chance, "ziel", None)
    tp2 = getattr(chance, "tp2", None)
    tp3 = getattr(chance, "tp3", None)
    if richtung is not None and ziel:
        wohin = "steigt" if richtung is Direction.LONG else "faellt"
        erwartung.append(
            f"Erwartet wird, dass der Kurs von {kurs:g} in Richtung {ziel:g} {wohin} "
            f"({(ziel / kurs - 1) * 100:+.1f} %)."
        )
        if tp2:
            erwartung.append(
                f"Laeuft es weiter, liegt das naechste Liquiditaetsziel bei {tp2:g} "
                f"({(tp2 / kurs - 1) * 100:+.1f} %)"
                + (f", danach {tp3:g} ({(tp3 / kurs - 1) * 100:+.1f} %)." if tp3 else ".")
            )
    if not erwartung:
        erwartung.append(
            "Kein Ziel, das weiter entfernt liegt als der Stop — deshalb keine Erwartung."
        )

    inval = getattr(chance, "invalidierung", None)
    was_waere_falsch: list[str] = []
    if inval and richtung is not None:
        seite = "unter" if richtung is Direction.LONG else "ueber"
        was_waere_falsch.append(
            f"Die {rt}-These ist hinfaellig, sobald der Kurs {seite} {inval:g} schliesst "
            f"({(inval / kurs - 1) * 100:+.1f} % von hier)."
        )
    gegen = [zl for zl in zeilen if zl["regime_roh"] in ("trend_up", "trend_down")]
    if richtung is not None and gegen:
        soll = "trend_up" if richtung is Direction.LONG else "trend_down"
        dagegen = [zl["tf"] for zl in gegen if zl["regime_roh"] != soll]
        if dagegen:
            was_waere_falsch.append(
                f"Gegen die These sprechen bereits {', '.join(dagegen)} — dort laeuft der "
                "Trend andersherum."
            )
    for m in muster:
        if m.richtung is not None and richtung is not None and m.richtung is not richtung:
            was_waere_falsch.append(
                f'Das Muster „{m.name}" auf {m.zeitebene} zeigt in die Gegenrichtung.'
            )
            break
    if not was_waere_falsch:
        was_waere_falsch.append(
            "Ohne Invalidierung gibt es keine These, die falsch werden koennte."
        )

    return {
        "was_ich_sehe": was_ich_sehe,
        "warum_jetzt": warum_jetzt,
        "erwartung": erwartung,
        "was_waere_falsch": was_waere_falsch,
    }


__all__ = ["EBENEN", "kommentar", "mtf_tabelle", "zeichnung"]
