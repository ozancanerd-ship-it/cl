"""Gewinnschutz — beurteilt waehrend der GANZEN Haltedauer, ob die Aufwaertsthese einer
Position im Gewinn intakt ist, schwaecher wird oder kippt (Ozan, 13.10.).

Das ist keine zweite Gewinnverwaltung neben dem Waechter, sondern ein reiner Baustein, den
``depot_stops.pflegen`` aufruft: gleiche Position, gleicher Stand, gleiche Alarmwege. Die
bisherige Regel „Gewinn-Rueckgang" (``gewinn_rueckgang``: erst wenn ein grosser Teil des
Gewinns schon weg ist) bleibt als Sicherheitsnetz bestehen; dieser Baustein soll VORHER
erkennen, ob mehrere Faktoren auf eine Umkehr deuten.

Grundsaetze
-----------
* Vier getrennte Urteile: HOLD · PROFIT_PROTECTION · PARTIAL_TAKE_PROFIT · EXIT.
* Kein einzelnes Signal entscheidet. Eine rote Kerze, ein RSI-Wert oder ein Gewinnprozentsatz
  allein erzwingt nichts: jedes Urteil ueber HOLD braucht mindestens zwei unabhaengige
  Schwaechesignale, PARTIAL mindestens drei (davon ein Strukturbruch bzw. -Versagen).
* Es zaehlen nur ABGESCHLOSSENE Kerzen (kein Blick in die Zukunft, keine laufende Kerze).
* Fehlende Daten werden als fehlend ausgewiesen (``fehlt``), nie geschaetzt. Zu wenig Daten
  ⇒ Urteil ``UNBEWERTET``, kein Alarm.
* Shorts werden gespiegelt (Preise negiert) und dann wie Longs bewertet.
* Reine Funktionen, kein Netz, kein Zustand. Keine Orders, keine Beratung: Ergebnis ist ein
  Hinweis zum Pruefen (Paper/Shadow).

Die Schwellen unten sind Startwerte, nicht angepasst an einzelne vergangene Gewinner.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from trading_agent.analysis.indicators import rsi, rsi_divergenz

#: Eine abgeschlossene Kerze: (open, high, low, close, volume), aelteste zuerst.
Kerze = tuple[float, float, float, float, float]

HOLD = "HOLD"
PROFIT_PROTECTION = "PROFIT_PROTECTION"
PARTIAL = "PARTIAL_TAKE_PROFIT"
EXIT = "EXIT"
UNBEWERTET = "UNBEWERTET"
RANG = {UNBEWERTET: -1, HOLD: 0, PROFIT_PROTECTION: 1, PARTIAL: 2, EXIT: 3}

MIN_KERZEN = 40  # darunter ist jede Struktur-Aussage Zufall
SWING_N = 2  # Kerzen links/rechts, die einen Swing bestaetigen
MIN_GEWINN_PCT = 1.0  # unter 1 % im Plus gibt es nichts zu schuetzen
SCHWELLE_PP = 2.0
SCHWELLE_PARTIAL = 3.5
SCHWELLE_EXIT = 4.0


@dataclass(frozen=True, slots=True)
class Signal:
    name: str
    gewicht: float
    text: str
    struktur: bool = False  # Strukturbruch bzw. -Versagen (zaehlt fuer PARTIAL/EXIT)


@dataclass(slots=True)
class Bewertung:
    urteil: str = UNBEWERTET
    punkte: float = 0.0
    signale: list[Signal] = field(default_factory=list)
    staerken: list[str] = field(default_factory=list)
    fehlt: list[str] = field(default_factory=list)
    gewinn_pct: float | None = None
    hoch_pct: float | None = None
    rueckgang_pct: float | None = None  # Prozentpunkte vom Hoechstgewinn
    upside_pct: float | None = None
    teil_anteil: float | None = None
    teil_menge: float | None = None
    rest_menge: float | None = None
    trail_stop: float | None = None
    wechsel_unten: float | None = None  # Schluss darunter ⇒ Einschaetzung wird schlechter
    wechsel_oben: float | None = None  # Schluss darueber ⇒ Struktur wieder voll intakt
    zeitebene: str = ""
    bias: dict[str, str | None] = field(default_factory=dict)
    aktion: str = ""
    begruendung: str = ""
    neubewertung: str = ""

    def als_dict(self) -> dict[str, Any]:
        """Serialisierbar fuer Stand/App. Zahlen gerundet, nichts Geheimes."""

        def r(v: float | None, n: int = 4) -> float | None:
            return None if v is None else round(v, n)

        return {
            "urteil": self.urteil,
            "punkte": round(self.punkte, 2),
            "signale": [{"name": s.name, "gewicht": s.gewicht, "text": s.text} for s in self.signale],
            "staerken": list(self.staerken),
            "fehlt": list(self.fehlt),
            "gewinn_pct": r(self.gewinn_pct, 2),
            "hoch_pct": r(self.hoch_pct, 2),
            "rueckgang_pct": r(self.rueckgang_pct, 2),
            "upside_pct": r(self.upside_pct, 2),
            "teil_anteil": r(self.teil_anteil, 3),
            "teil_menge": r(self.teil_menge, 6),
            "rest_menge": r(self.rest_menge, 6),
            "trail_stop": r(self.trail_stop, 8),
            "wechsel_unten": r(self.wechsel_unten, 8),
            "wechsel_oben": r(self.wechsel_oben, 8),
            "zeitebene": self.zeitebene,
            "bias": dict(self.bias),
            "aktion": self.aktion,
            "begruendung": self.begruendung,
            "neubewertung": self.neubewertung,
        }


# --------------------------------------------------------------------------- Bausteine


def spiegeln(bars: Sequence[Kerze]) -> list[Kerze]:
    """Short ⇒ Long: Preise negieren (Hoch/Tief tauschen), Volumen bleibt."""
    return [(-o, -lo, -h, -c, v) for (o, h, lo, c, v) in bars]


def atr(bars: Sequence[Kerze], n: int = 14) -> float | None:
    if len(bars) < n + 1:
        return None
    tr = [
        max(h - lo, abs(h - bars[i][3]), abs(lo - bars[i][3]))
        for i, (_o, h, lo, _c, _v) in enumerate(bars[1:])
    ]
    w = tr[-n:]
    return sum(w) / len(w) if w else None


def swings(bars: Sequence[Kerze], n: int = SWING_N) -> list[tuple[int, str, float]]:
    """Bestaetigte Swing-Punkte ``(index, 'H'|'L', preis)``. Ein Swing steht erst fest, wenn
    ``n`` Kerzen danach geschlossen haben — der letzte Rand ist nie ein Swing."""
    out: list[tuple[int, str, float]] = []
    for i in range(n, len(bars) - n):
        h, lo = bars[i][1], bars[i][2]
        if all(h > bars[i - k][1] and h >= bars[i + k][1] for k in range(1, n + 1)):
            out.append((i, "H", h))
        if all(lo < bars[i - k][2] and lo <= bars[i + k][2] for k in range(1, n + 1)):
            out.append((i, "L", lo))
    return sorted(out)


def _ema(werte: Sequence[float], n: int) -> float | None:
    if len(werte) < n:
        return None
    a, e = 2 / (n + 1), werte[0]
    for v in werte[1:]:
        e = a * v + (1 - a) * e
    return e


def _letzte(sw: Sequence[tuple[int, str, float]], art: str, k: int = 1) -> list[tuple[int, float]]:
    return [(i, p) for i, a, p in sw if a == art][-k:]


# --------------------------------------------------------------------------- Signale


def signale_fuer(bars: Sequence[Kerze], *, tf: str = "") -> tuple[list[Signal], list[str], dict[str, float | None]]:
    """Schwaechesignale der Long-Sicht (Shorts vorher spiegeln). Rueckgabe:
    ``(signale, staerken, level)``; ``level`` = ``wechsel_unten`` (juengster Swing-Low)
    und ``wechsel_oben`` (juengstes Swing-High)."""
    sig: list[Signal] = []
    stark: list[str] = []
    level: dict[str, float | None] = {"unten": None, "oben": None}
    if len(bars) < MIN_KERZEN:
        return sig, stark, level
    kurs = bars[-1][3]
    sw = swings(bars)
    hoch2 = _letzte(sw, "H", 2)
    tief2 = _letzte(sw, "L", 2)
    a = atr(bars) or 0.0
    if tief2:
        level["unten"] = tief2[-1][1]
    if hoch2:
        level["oben"] = hoch2[-1][1]

    # 1) Swing-Low bricht (CHoCH): Schluss unter dem juengsten bestaetigten Swing-Low,
    #    nachdem vorher hoehere Tiefs da waren (= es gab einen Aufwaertstrend).
    if len(tief2) == 2 and tief2[1][1] > tief2[0][1] and kurs < tief2[1][1]:
        sig.append(
            Signal("choch", 2.0, f"{tf}: Schluss unter dem letzten höheren Tief ({tief2[1][1]:.6g}) — Aufwärtsstruktur gebrochen", True)
        )
    elif tief2 and kurs < tief2[-1][1]:
        sig.append(Signal("tief_bruch", 1.5, f"{tf}: Schluss unter dem letzten Swing-Low ({tief2[-1][1]:.6g})", True))

    # 2) Tieferes Hoch nach einem Hoch (kein neues Hoch mehr, Struktur flacht ab)
    if len(hoch2) == 2 and hoch2[1][1] < hoch2[0][1] and kurs < hoch2[1][1]:
        sig.append(Signal("tieferes_hoch", 1.0, f"{tf}: tieferes Hoch ({hoch2[1][1]:.6g} unter {hoch2[0][1]:.6g}) — kein neues Hoch mehr", True))
    elif len(hoch2) == 2 and hoch2[1][1] > hoch2[0][1] and (not tief2 or kurs > tief2[-1][1]):
        stark.append(f"{tf}: höhere Hochs und Tiefs — Aufwärtsstruktur intakt")

    # 3) Wick-Rejection: lange obere Lunte, Schluss im unteren Teil, nahe am juengsten Hoch
    hh20 = max(b[1] for b in bars[-20:])
    for b in bars[-3:]:
        o, h, lo, c, _v = b
        rng = h - lo
        if rng <= 0 or a <= 0 or rng < 0.8 * a:
            continue
        obere = h - max(o, c)
        if obere / rng >= 0.55 and (c - lo) / rng <= 0.40 and h >= hh20 - a:
            sig.append(Signal("wick_rejection", 1.0, f"{tf}: lange obere Lunte nahe dem Hoch ({obere / rng * 100:.0f} % der Kerze) — Ablehnung", True))
            break

    # 4) Gescheiterter Ausbruch: neues 20er-Hoch in den letzten 6 Kerzen, Schluss wieder darunter
    if len(bars) >= 27:
        for k in range(2, 8):
            i = len(bars) - k
            vorher = max(b[1] for b in bars[max(0, i - 20) : i])
            if bars[i][1] > vorher and kurs < vorher:
                sig.append(Signal("ausbruch_gescheitert", 1.5, f"{tf}: Ausbruch über {vorher:.6g} ist zurückgefallen — Schluss wieder darunter", True))
                break

    # 5) Momentum: baerische RSI-Divergenz oder RSI faellt deutlich vom juengsten Hoch weg
    schluss = [b[3] for b in bars]
    if rsi_divergenz(schluss) == "baerisch":
        sig.append(Signal("rsi_divergenz", 1.0, f"{tf}: Kurs macht ein neues Hoch, der RSI nicht — Kraft lässt nach"))
    else:
        reihe = [rsi(schluss[: i + 1]) for i in range(len(schluss) - 10, len(schluss))]
        rv = [x for x in reihe if x is not None]
        if len(rv) >= 8 and max(rv) - rv[-1] >= 15 and kurs >= 0.97 * hh20:
            sig.append(Signal("momentum_laesst_nach", 1.0, f"{tf}: RSI {rv[-1]:.0f}, vor kurzem {max(rv):.0f} — Momentum nimmt ab, Kurs noch nahe am Hoch"))

    # 6) Volumen: duenn auf Aufwaertskerzen nahe dem Hoch (Distribution) oder Abverkaufskerze mit viel Volumen
    vol = [b[4] for b in bars]
    if any(vol[-25:]):
        schnitt = sum(vol[-21:-1]) / 20 if len(vol) >= 21 else 0.0
        if schnitt > 0:
            auf = [b[4] for b in bars[-5:] if b[3] > b[0]]
            nahe = kurs >= hh20 - 1.5 * a
            if auf and nahe and (sum(auf) / len(auf)) < 0.7 * schnitt:
                sig.append(Signal("volumen_duenn", 1.0, f"{tf}: Aufwärtskerzen nahe dem Hoch mit dünnem Volumen (unter 70 % des Schnitts)"))
            elif any(b[3] < b[0] and b[4] >= 1.8 * schnitt and (b[0] - b[3]) >= 0.8 * a for b in bars[-3:]):
                sig.append(Signal("abverkauf_volumen", 1.0, f"{tf}: kräftige Abwärtskerze mit überdurchschnittlichem Volumen"))
    return sig, stark, level


# --------------------------------------------------------------------------- Bewertung


def bewerte(
    *,
    lang: bool,
    kurs: float,
    einstieg: float | None,
    hoch_gv_pct: float | None,
    menge: float | None,
    stop: float | None,
    ziel_next: float | None,
    kerzen: dict[str, Sequence[Kerze] | None] | None,
    trend: dict[str, str | None] | None,
    zahlen_tage: int | None = None,
    z: str = "",
) -> Bewertung:
    """Urteil fuer EINE Position im Gewinn. ``kerzen``: ``{"h1","h4","d1"}`` abgeschlossene
    Kerzen (oder fehlend). ``trend``: ``{"d1","h4"}`` mit ``auf``/``ab``/``gemischt``/``None``
    in Sicht der RICHTUNG DER MARKT-Kurve (nicht gespiegelt). ``hoch_gv_pct``: bisheriger
    Hoechstgewinn in % (Gedaechtnis des Waechters)."""
    b = Bewertung()
    kerzen = kerzen or {}
    trend = trend or {}
    if not einstieg or einstieg <= 0 or kurs <= 0:
        b.fehlt.append("Einstiegskurs fehlt — Gewinn nicht berechenbar")
        b.aktion = "Nicht bewertet: Einstiegskurs fehlt."
        return b
    gv = ((kurs / einstieg - 1.0) if lang else (einstieg / kurs - 1.0)) * 100.0  # wie der Waechter rechnet
    hoch = max(hoch_gv_pct if hoch_gv_pct is not None else gv, gv)
    b.gewinn_pct, b.hoch_pct, b.rueckgang_pct = gv, hoch, hoch - gv
    if gv < MIN_GEWINN_PCT:
        b.urteil = HOLD
        b.aktion = "Kein Gewinn zu schützen (unter 1 % im Plus) — Stop und Plan gelten wie bisher."
        return b

    # Zeitebene fuer die Struktur: H4, sonst H1. Die jeweils andere ergaenzt Momentum/Wicks.
    prim = "h4" if len(kerzen.get("h4") or []) >= MIN_KERZEN else "h1" if len(kerzen.get("h1") or []) >= MIN_KERZEN else None
    if prim is None:
        b.fehlt.append("Kerzen (H4/H1) fehlen oder reichen nicht — keine Struktur-Bewertung")
        b.aktion = "Nicht bewertet: zu wenig frische Kerzendaten. Stop und Plan gelten wie bisher."
        return b
    if prim == "h1":
        b.fehlt.append("H4-Kerzen fehlen — Struktur nur auf H1 geprüft (empfindlicher, weniger belastbar)")
    b.zeitebene = prim.upper()
    if not kerzen.get("d1") or len(kerzen["d1"] or []) < MIN_KERZEN:
        b.fehlt.append("D1-Kerzen fehlen")
    if trend.get("d1") is None:
        b.fehlt.append("D1-Trend fehlt")
    if trend.get("h4") is None and prim == "h4":
        b.fehlt.append("H4-Trend fehlt")
    b.fehlt.append("News/Makro: nicht angebunden — nicht in die Bewertung eingeflossen")

    def spiegel(bars: Sequence[Kerze] | None) -> list[Kerze]:
        bars = list(bars or [])
        return bars if lang else spiegeln(bars)

    sig, stark, level = signale_fuer(spiegel(kerzen[prim]), tf=prim.upper())
    if prim == "h4" and len(kerzen.get("h1") or []) >= MIN_KERZEN:
        s1, _st1, _lv1 = signale_fuer(spiegel(kerzen["h1"]), tf="H1")
        # H1 liefert nur Ablehnung/Momentum/Volumen als Ergaenzung, keine zweite Struktur-Zaehlung
        sig += [s for s in s1 if s.name in ("wick_rejection", "momentum_laesst_nach", "volumen_duenn", "abverkauf_volumen") and not any(x.name == s.name for x in sig)]
    if level["unten"] is not None:
        b.wechsel_unten = level["unten"] if lang else -level["unten"]
    if level["oben"] is not None:
        b.wechsel_oben = level["oben"] if lang else -level["oben"]
    b.staerken = stark

    dagegen = "ab" if lang else "auf"
    dafuer = "auf" if lang else "ab"
    b.bias = {"d1": trend.get("d1"), "h4": trend.get("h4")}
    for tf in ("d1", "h4"):
        if trend.get(tf) == dagegen:
            sig.append(Signal(f"trend_{tf}", 1.0, f"{tf.upper()}-Trend steht gegen die Position", False))
    if trend.get("d1") == dafuer and trend.get("h4") in (dafuer, None):
        b.staerken.append("D1" + ("/H4" if trend.get("h4") else "") + "-Trend stützt die Position")

    # Gewinn-Abgabe vom Hoechststand: zaehlt als EIN Signal, nie allein
    if b.hoch_pct and b.hoch_pct >= 3.0 and b.rueckgang_pct is not None:
        anteil = b.rueckgang_pct / b.hoch_pct
        if anteil >= 0.5:
            sig.append(Signal("gewinn_abgabe", 1.5, f"{anteil * 100:.0f} % des Höchstgewinns ({b.hoch_pct:.1f} %) sind wieder abgegeben"))
        elif anteil >= 0.3:
            sig.append(Signal("gewinn_abgabe", 1.0, f"{anteil * 100:.0f} % des Höchstgewinns ({b.hoch_pct:.1f} %) sind wieder abgegeben"))
    if zahlen_tage is not None and 0 <= zahlen_tage <= 3:
        sig.append(Signal("ereignis", 1.0, f"Quartalszahlen in {zahlen_tage} Tag(en) — Gap-Risiko"))

    # Upside bis zum naechsten Ziel
    if ziel_next is not None:
        up = ((ziel_next - kurs) if lang else (kurs - ziel_next)) / kurs * 100.0
        b.upside_pct = up
    else:
        b.fehlt.append("Kein nächstes Kursziel bekannt — Restpotenzial nicht berechenbar")

    punkte = sum(s.gewicht for s in sig)
    # Intakte Struktur dämpft: kein Verkauf nur wegen eines hohen Gewinns
    if not any(s.struktur for s in sig) and len(b.staerken) >= 2:
        punkte -= 1.0
    b.signale, b.punkte = sig, max(punkte, 0.0)
    n_sig, n_struk = len(sig), sum(1 for s in sig if s.struktur)

    # Verlustrisiko bis zum Tief/Stop
    schutz_ref = b.wechsel_unten if b.wechsel_unten is not None else stop
    risiko_pct = abs(kurs - schutz_ref) / kurs * 100.0 if schutz_ref else None
    chance_schlecht = (
        b.upside_pct is not None and risiko_pct is not None and risiko_pct > 0 and b.upside_pct / risiko_pct < 1.0
    )

    urteil = HOLD
    if n_sig >= 2 and b.punkte >= SCHWELLE_PP:
        urteil = PROFIT_PROTECTION
    if n_sig >= 3 and n_struk >= 1 and b.punkte >= SCHWELLE_PARTIAL:
        urteil = PARTIAL
    choch = any(s.name in ("choch", "tief_bruch") for s in sig)
    gegen = trend.get("d1") == dagegen or trend.get("h4") == dagegen
    if choch and gegen and n_sig >= 3 and b.punkte >= SCHWELLE_EXIT and (chance_schlecht or b.upside_pct is None):
        urteil = EXIT
    b.urteil = urteil

    # Trailing-Vorschlag: unter dem letzten hoeheren Tief (mit ATR-Puffer), sonst 1,5 ATR unter
    # dem Kurs — nie schlechter als Einstand, nur wenn er den bisherigen Stop verbessert.
    a = atr(spiegel(kerzen[prim])) or 0.0
    kand: list[float] = []
    if level["unten"] is not None:
        kand.append(level["unten"] - 0.25 * a)
    kurs_l = kurs if lang else -kurs
    if a > 0:
        kand.append(kurs_l - 1.5 * a)
    for t_l in sorted(kand, reverse=True):  # bester (naechster) zuerst
        t = t_l if lang else -t_l
        t = max(t, einstieg) if lang else min(t, einstieg)
        ok = (t < kurs) if lang else (t > kurs)
        schlaegt = stop is None or ((t > stop) if lang else (t < stop))
        if ok and schlaegt:
            b.trail_stop = t
            break

    f = lambda x: f"{x:,.4f}".rstrip("0").rstrip(".")  # noqa: E731
    gruende = "; ".join(s.text for s in sig) or "keine Schwächesignale"
    b.begruendung = gruende
    unten = f" Verliert der Kurs {f(b.wechsel_unten)}{z} per Schluss, wird die Lage schlechter." if b.wechsel_unten else ""
    oben = f" Ein Schluss über {f(b.wechsel_oben)}{z} stellt die Struktur wieder voll her." if b.wechsel_oben else ""
    b.neubewertung = "Neubewertung bei jedem neuen abgeschlossenen Stundenschluss." + unten + oben

    if urteil == HOLD:
        b.aktion = "HOLD: Struktur, Momentum und Restpotenzial rechtfertigen das Halten. Kein Verkauf nur wegen der Höhe des Gewinns."
    elif urteil == PROFIT_PROTECTION:
        t = f" Mögliches Nachziehen des Stops auf {f(b.trail_stop)}{z}." if b.trail_stop else ""
        b.aktion = f"PROFIT PROTECTION: Gewinn ist deutlich da, die Korrekturgefahr steigt.{t} Teilverkauf prüfen, falls weitere Schwäche dazukommt."
    elif urteil == PARTIAL:
        anteil = 0.5 if b.punkte >= 5.0 else 1 / 3
        b.teil_anteil = anteil
        if menge and menge > 0:
            b.teil_menge, b.rest_menge = menge * anteil, menge * (1 - anteil)
            mt = f" ca. {b.teil_menge:.4g} Stück ({anteil * 100:.0f} %), danach bleiben {b.rest_menge:.4g} Stück."
        else:
            mt = f" ca. {anteil * 100:.0f} % der Position (Stückzahl unbekannt)."
        t = f" Stop-Vorschlag für den Rest: {f(b.trail_stop)}{z}." if b.trail_stop else ""
        b.aktion = f"PARTIAL TAKE PROFIT: Teilverkauf prüfen —{mt}{t}"
    else:
        b.aktion = "EXIT: Die Aufwärtsthese ist gebrochen und Chance und Risiko rechtfertigen das Halten nicht mehr. Position prüfen."
    return b


__all__ = [
    "EXIT",
    "HOLD",
    "PARTIAL",
    "PROFIT_PROTECTION",
    "RANG",
    "UNBEWERTET",
    "Bewertung",
    "Kerze",
    "Signal",
    "atr",
    "bewerte",
    "signale_fuer",
    "spiegeln",
    "swings",
]
