"""Chart-Bewertung aus der Marktstruktur — die Frage "wo ist JETZT die beste Chance?".

WARUM ES DAS BRAUCHT

``score_opportunity`` bewertet den KONTEXT (Regime, Datenguete, HTF-Klarheit) und setzt
darauf den Setup-Score. Solange kein Setup der eingefrorenen Regel geformt ist, faellt
der Setup-Teil weg — und dann bekommen alle Instrumente fast denselben Wert. Am
2026-09-05 lagen acht Kryptowerte bei 26.8, 26.8, 26.8, 26.8, 26.8, 26.8, 20.5, 20.5.
Ein Ranking, in dem alles gleich ist, beantwortet die Frage nicht, fuer die es da ist.

Dieses Modul bewertet stattdessen den CHARTZUSTAND selbst, so wie ein Trader ihn liest,
und zwar aus den Bausteinen, die ohnehin berechnet werden (``strategy.primitives``,
``analysis.mtf``). Es braucht kein fertiges Setup und unterscheidet trotzdem.

WAS DAS IST UND WAS NICHT

Das ist eine **Beschreibung**, keine belegte Handelsregel. Ein hoher Chart-Score sagt:
"hier sind mehrere Dinge gleichzeitig ausgerichtet". Er sagt NICHT, dass daraus Gewinn
folgt — das muesste getrennt geprueft werden, wie jede andere Hypothese auch. Der Wert
liegt darin, dass die Aufmerksamkeit dorthin geht, wo etwas passiert, statt jeden Tag
auf dieselbe Liste zu schauen.

Sechs Faktoren, zusammen 100 Punkte. Die Gewichte sind gesetzt, nicht optimiert — sie
sind bewusst NICHT an historische Ergebnisse angepasst, weil genau das Overfitting waere.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from trading_agent.core.enums import Direction, Timeframe


def _passt(objekt_richtung: Any, wette: Direction) -> bool:
    """Richtungsvergleich ueber zwei Enums hinweg.

    Die Primitives beschreiben Richtung als ``Polarity`` (bullish/bearish), die
    Handelsseite heisst ``Direction`` (long/short). Ein direkter Vergleich ist immer
    False — genau deshalb waren Struktur-, Zonen- und Momentum-Faktor beim ersten Lauf
    ausnahmslos null, obwohl H4 einen bullishen CHoCH und 15 Displacements hatte.
    """
    v = getattr(objekt_richtung, "value", objekt_richtung)
    if wette is Direction.LONG:
        return v in ("long", "bullish")
    return v in ("short", "bearish")


# Gewicht je Timeframe fuer die Richtungsbestimmung. Das Tagesbild fuehrt, die
# Ausfuehrungsebene bestaetigt nur — bei einem Swing-Trade darf M15 die Richtung
# nicht gegen D1 drehen.
TF_GEWICHT: dict[Timeframe, float] = {
    Timeframe.D1: 0.40,
    Timeframe.H4: 0.30,
    Timeframe.H1: 0.20,
    Timeframe.M15: 0.10,
}

MAX_PUNKTE: dict[str, float] = {
    "mtf_ausrichtung": 25.0,
    "struktur_frisch": 20.0,
    "bewegungsraum": 20.0,
    "zonen": 15.0,
    "momentum": 10.0,
    "lage": 10.0,
}


@dataclass(frozen=True, slots=True)
class ChartFaktor:
    name: str
    punkte: float
    max_punkte: float
    detail: str

    @property
    def anteil(self) -> float:
        return self.punkte / self.max_punkte if self.max_punkte else 0.0


@dataclass(frozen=True, slots=True)
class ChartChance:
    """Was der Chart hergibt — inklusive der Frage, wie weit es laufen koennte."""

    instrument: str
    richtung: Direction | None
    score: float
    faktoren: tuple[ChartFaktor, ...]
    kurs: float
    ziel: float | None  # naechstes Liquiditaetsziel in Richtung der Wette
    ziel_art: str | None
    invalidierung: float | None  # dahinter waere die Idee falsch
    bewegung_pct: float | None  # Weg bis zum Ziel
    bewegung_atr: float | None
    rr: float | None  # Ziel gegen Invalidierung
    headline: str
    urteil: str  # A_PLUS | A | WATCH | NO_TRADE

    def as_dict(self) -> dict[str, Any]:
        return {
            "instrument": self.instrument,
            "richtung": self.richtung.value if self.richtung else None,
            "score": round(self.score, 1),
            "kurs": self.kurs,
            "ziel": self.ziel,
            "ziel_art": self.ziel_art,
            "invalidierung": self.invalidierung,
            "bewegung_pct": round(self.bewegung_pct, 2) if self.bewegung_pct else None,
            "bewegung_atr": round(self.bewegung_atr, 2) if self.bewegung_atr else None,
            "rr": round(self.rr, 2) if self.rr else None,
            "headline": self.headline,
            "urteil": self.urteil,
            "faktoren": [
                {
                    "name": f.name,
                    "punkte": round(f.punkte, 1),
                    "max": f.max_punkte,
                    "detail": f.detail,
                }
                for f in self.faktoren
            ],
        }


def _richtung_von(tfc: Any) -> Direction | None:
    reg = getattr(tfc, "regime", None)
    d = getattr(getattr(reg, "directional", None), "value", "")
    if d == "trend_up":
        return Direction.LONG
    if d == "trend_down":
        return Direction.SHORT
    return None


def _bias(per_tf: dict[Timeframe, Any]) -> tuple[Direction | None, float, str]:
    """Gewichtete Richtung ueber die Timeframes. Rueckgabe: Richtung, Einigkeit 0..1, Text."""
    long_g = short_g = gesamt = 0.0
    teile: list[str] = []
    for tf, gew in TF_GEWICHT.items():
        tfc = per_tf.get(tf)
        if tfc is None:
            continue
        gesamt += gew
        r = _richtung_von(tfc)
        if r is Direction.LONG:
            long_g += gew
            teile.append(f"{tf.value}↑")
        elif r is Direction.SHORT:
            short_g += gew
            teile.append(f"{tf.value}↓")
        else:
            teile.append(f"{tf.value}·")
    if gesamt <= 0:
        return None, 0.0, "keine Timeframes"
    if long_g == short_g == 0:
        return None, 0.0, " ".join(teile) + " — kein Trend"
    richtung = Direction.LONG if long_g >= short_g else Direction.SHORT
    einigkeit = max(long_g, short_g) / gesamt
    return richtung, einigkeit, " ".join(teile)


def _atr(per_tf: dict[Timeframe, Any]) -> float:
    for tf in (Timeframe.H4, Timeframe.H1, Timeframe.D1, Timeframe.M15):
        a = getattr(per_tf.get(tf), "atr", None)
        if a:
            return float(a)
    return 0.0


def _naechstes_ziel(
    per_tf: dict[Timeframe, Any], kurs: float, richtung: Direction
) -> tuple[float | None, str | None, float | None]:
    """Naechste unberuehrte Liquiditaet in Wettrichtung — das ist das realistische Ziel.

    Nur D1/H4: auf M15 liegt in jeder Richtung binnen 0.2 % irgendein Swing-Punkt, das
    waere kein Ziel, sondern Rauschen. Gesucht wird der naechste Level, der noch nicht
    abgeholt wurde und eine nennenswerte Staerke hat.
    """
    beste: tuple[float, float, str] | None = None
    for tf in (Timeframe.D1, Timeframe.H4):
        tfc = per_tf.get(tf)
        for lv in getattr(tfc, "liquidity", ()) or ():
            if getattr(getattr(lv, "state", None), "value", "") == "swept":
                continue
            if lv.strength < 0.10:
                continue
            p = float(lv.price)
            passt = p > kurs if richtung is Direction.LONG else p < kurs
            if not passt:
                continue
            abstand = abs(p - kurs)
            if beste is None or abstand < beste[0]:
                beste = (abstand, p, getattr(lv.type, "value", "level"))
    if beste is None:
        return None, None, None
    return beste[1], beste[2], beste[0]


def _invalidierung(
    per_tf: dict[Timeframe, Any], kurs: float, richtung: Direction, atr: float
) -> float | None:
    """Wo die Idee widerlegt waere: hinter dem letzten Strukturpunkt gegen die Wette."""
    kand: list[float] = []
    for tf in (Timeframe.H4, Timeframe.H1, Timeframe.D1):
        st = getattr(per_tf.get(tf), "structure", None)
        if st is None:
            continue
        sw = st.last_swing_low if richtung is Direction.LONG else st.last_swing_high
        p = getattr(sw, "price", None)
        if p is None:
            continue
        p = float(p)
        if (richtung is Direction.LONG and p < kurs) or (richtung is Direction.SHORT and p > kurs):
            kand.append(p)
    mindest = 1.5 * atr
    if richtung is Direction.LONG:
        tauglich = [p for p in kand if kurs - p >= mindest]
        return max(tauglich) if tauglich else kurs - 2.0 * atr
    tauglich = [p for p in kand if p - kurs >= mindest]
    return min(tauglich) if tauglich else kurs + 2.0 * atr


def bewerte_chart(instrument: str, mtf: Any, kurs: float) -> ChartChance:
    """Sechs Faktoren, 100 Punkte. Kein Setup noetig — der Chartzustand allein zaehlt."""
    per_tf: dict[Timeframe, Any] = dict(getattr(mtf, "per_tf", {}) or {})
    richtung, einigkeit, bias_text = _bias(per_tf)
    atr = _atr(per_tf)
    f: list[ChartFaktor] = []

    # 1 — Sind sich die Zeitebenen einig?
    f.append(
        ChartFaktor(
            "mtf_ausrichtung",
            MAX_PUNKTE["mtf_ausrichtung"] * einigkeit,
            MAX_PUNKTE["mtf_ausrichtung"],
            bias_text,
        )
    )

    # 2 — Gibt es einen frischen Bruch in Wettrichtung? Hoehere TF zaehlt mehr.
    punkte, detail = 0.0, "kein Bruch in Richtung"
    if richtung is not None:
        for tf, gew in ((Timeframe.D1, 1.0), (Timeframe.H4, 0.8), (Timeframe.H1, 0.5)):
            brueche = list(getattr(per_tf.get(tf), "structure_breaks", ()) or ())
            if not brueche:
                continue
            b = brueche[-1]
            if not _passt(b.direction, richtung):
                continue
            frische = max(0.0, 1.0 - len(brueche[-1:]) * 0.0)  # letzter Bruch = frisch
            p = MAX_PUNKTE["struktur_frisch"] * gew * frische
            if p > punkte:
                punkte = p
                detail = (
                    f"{getattr(b.kind, 'value', '?').upper()} auf {tf.value} "
                    f"bei {b.broken_level_price:,.2f}".replace(",", " ")
                )
    f.append(
        ChartFaktor(
            "struktur_frisch",
            min(punkte, MAX_PUNKTE["struktur_frisch"]),
            MAX_PUNKTE["struktur_frisch"],
            detail,
        )
    )

    # 3 — Wie weit koennte es laufen? Weg zum naechsten Ziel, gemessen in ATR.
    ziel = ziel_art = None
    weg = None
    punkte, detail = 0.0, "kein Ziel gefunden"
    if richtung is not None:
        ziel, ziel_art, weg = _naechstes_ziel(per_tf, kurs, richtung)
        if ziel is not None and weg is not None and atr > 0:
            in_atr = weg / atr
            # Unter 1 ATR lohnt der Weg nicht, ab 6 ATR ist die Skala ausgereizt.
            punkte = MAX_PUNKTE["bewegungsraum"] * min(1.0, max(0.0, (in_atr - 1.0) / 5.0))
            detail = f"{weg / kurs * 100:.1f} % bis {ziel:,.2f} ({ziel_art}), {in_atr:.1f} ATR"
            detail = detail.replace(",", " ")
    f.append(ChartFaktor("bewegungsraum", punkte, MAX_PUNKTE["bewegungsraum"], detail))

    # 4 — Liegt eine offene Zone in Wettrichtung nah genug, um einzusteigen?
    punkte, detail = 0.0, "keine offene Zone in der Naehe"
    if richtung is not None and atr > 0:
        nah: list[tuple[float, str]] = []
        for tf in (Timeframe.H4, Timeframe.H1, Timeframe.M15):
            tfc = per_tf.get(tf)
            for zone, art in (
                (getattr(tfc, "fvgs", ()) or (), "FVG"),
                (getattr(tfc, "order_blocks", ()) or (), "OB"),
            ):
                for z in zone:
                    if getattr(getattr(z, "state", None), "value", "") not in (
                        "unmitigated",
                        "partial",
                    ):
                        continue
                    if not _passt(z.direction, richtung):
                        continue
                    mitte = (z.zone_low + z.zone_high) / 2
                    d_atr = abs(mitte - kurs) / atr
                    if d_atr <= 3.0:
                        nah.append((d_atr, f"{art} {tf.value} bei {mitte:,.2f}".replace(",", " ")))
        if nah:
            nah.sort()
            punkte = MAX_PUNKTE["zonen"] * min(1.0, len(nah) / 3.0)
            detail = f"{len(nah)} offene Zone(n), naechste {nah[0][1]}"
    f.append(ChartFaktor("zonen", punkte, MAX_PUNKTE["zonen"], detail))

    # 5 — Hat die Bewegung Kraft? Displacement ist der messbare Teil davon.
    punkte, detail = 0.0, "kein Displacement"
    if richtung is not None:
        stark = 0.0
        for tf in (Timeframe.H4, Timeframe.H1):
            for dsp in getattr(per_tf.get(tf), "displacements", ()) or ():
                if _passt(dsp.direction, richtung):
                    stark = max(stark, abs(float(getattr(dsp, "net_move_atr", 0.0))))
        if stark > 0:
            punkte = MAX_PUNKTE["momentum"] * min(1.0, stark / 3.0)
            detail = f"Displacement {stark:.1f} ATR in Richtung"
    f.append(ChartFaktor("momentum", punkte, MAX_PUNKTE["momentum"], detail))

    # 6 — Kauft man guenstig oder teuer? Long im Discount, Short im Premium.
    punkte, detail = 0.0, "Premium/Discount unbekannt"
    for tf in (Timeframe.H4, Timeframe.D1):
        pd = getattr(per_tf.get(tf), "premium_discount", None)
        pos = getattr(pd, "pd_position", None) if pd else None
        if pos is None:
            continue
        guenstig = (1.0 - pos) if richtung is Direction.LONG else pos
        punkte = MAX_PUNKTE["lage"] * max(0.0, min(1.0, guenstig))
        zone = getattr(getattr(pd, "zone", None), "value", "?")
        detail = f"{tf.value} Position {pos:.2f} ({zone})"
        break
    f.append(ChartFaktor("lage", punkte, MAX_PUNKTE["lage"], detail))

    score = sum(x.punkte for x in f)
    inval = _invalidierung(per_tf, kurs, richtung, atr) if richtung else None
    bew_pct = (abs(ziel - kurs) / kurs * 100) if ziel else None
    bew_atr = (abs(ziel - kurs) / atr) if ziel and atr > 0 else None
    rr = None
    if ziel is not None and inval is not None and abs(kurs - inval) > 0:
        rr = abs(ziel - kurs) / abs(kurs - inval)

    # Urteil. Score allein reicht nicht: ein gut ausgerichteter Chart, dessen naechstes
    # Ziel naeher liegt als die Invalidierung, ist trotzdem kein Trade. Beide Bedingungen
    # muessen zusammenkommen — das ist der Unterschied zu "sieht gut aus".
    if richtung is None or rr is None:
        urteil = "NO_TRADE"
    elif score >= 70 and rr >= 2.0:
        urteil = "A_PLUS"
    elif score >= 60 and rr >= 1.5:
        urteil = "A"
    elif score >= 40:
        urteil = "WATCH"
    else:
        urteil = "NO_TRADE"

    if richtung is None:
        kopf = "kein klarer Trend"
    elif score >= 70:
        kopf = f"{'LONG' if richtung is Direction.LONG else 'SHORT'} — stark ausgerichtet"
    elif score >= 50:
        kopf = f"{'LONG' if richtung is Direction.LONG else 'SHORT'} — brauchbar"
    else:
        kopf = f"{'LONG' if richtung is Direction.LONG else 'SHORT'} — schwach"

    return ChartChance(
        instrument=instrument,
        richtung=richtung,
        score=score,
        faktoren=tuple(f),
        kurs=kurs,
        ziel=ziel,
        ziel_art=ziel_art,
        invalidierung=inval,
        bewegung_pct=bew_pct,
        bewegung_atr=bew_atr,
        rr=rr,
        headline=kopf,
        urteil=urteil,
    )


__all__ = ["MAX_PUNKTE", "TF_GEWICHT", "ChartChance", "ChartFaktor", "bewerte_chart"]
