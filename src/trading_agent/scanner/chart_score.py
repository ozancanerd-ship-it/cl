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

from dataclasses import dataclass, field
from typing import Any

from trading_agent.core.enums import Direction, Timeframe
from trading_agent.scanner.grading import (
    HANDELBAR,
    NOTE_KURZ,
    Profil,
    benote,
    confidence,
)


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
    ziel: float | None  # TP1 — erstes sinnvolles Liquiditaetsziel
    ziel_art: str | None
    tp2: float | None
    tp3: float | None
    invalidierung: float | None  # dahinter waere die Idee falsch
    bewegung_pct: float | None  # Weg bis zum Ziel
    bewegung_atr: float | None
    rr: float | None  # Ziel gegen Invalidierung
    headline: str
    urteil: str  # A_PLUS | A | A_MINUS | B_PLUS | B | WATCH | NO_TRADE
    #: Wie weit es bis TP2 laufen koennte, in Prozent. Die Groesse, an der ein
    #: aggressiver Swing-Trade haengt — nicht der Score allein.
    erwartete_bewegung_pct: float | None = None
    #: 0..1 — wie einheitlich das Bild ist. NICHT die Trefferwahrscheinlichkeit.
    confidence: float = 0.0
    profil: str = "aggressiv"
    begruendung: str = ""
    #: Was die Note nach oben begrenzt hat.
    bremse: str | None = None
    #: Dinge, die gegen den Trade sprechen, ohne ihn auszuschliessen.
    warnungen: tuple[str, ...] = ()
    #: Kennzahlen von aussen (Umsatz, 24h-Bewegung) — Kontext, kein Score-Bestandteil.
    zusatz: dict[str, Any] = field(default_factory=dict)

    @property
    def note_kurz(self) -> str:
        return NOTE_KURZ.get(self.urteil, "—")

    @property
    def handelbar(self) -> bool:
        return self.urteil in HANDELBAR

    def as_dict(self) -> dict[str, Any]:
        return {
            "instrument": self.instrument,
            "richtung": self.richtung.value if self.richtung else None,
            "score": round(self.score, 1),
            "kurs": self.kurs,
            "ziel": self.ziel,
            "ziel_art": self.ziel_art,
            "tp2": self.tp2,
            "tp3": self.tp3,
            "invalidierung": self.invalidierung,
            "bewegung_pct": round(self.bewegung_pct, 2) if self.bewegung_pct else None,
            "bewegung_atr": round(self.bewegung_atr, 2) if self.bewegung_atr else None,
            "rr": round(self.rr, 2) if self.rr else None,
            "headline": self.headline,
            "urteil": self.urteil,
            "note": self.note_kurz,
            "handelbar": self.handelbar,
            "erwartete_bewegung_pct": (
                round(self.erwartete_bewegung_pct, 2)
                if self.erwartete_bewegung_pct is not None
                else None
            ),
            "confidence": self.confidence,
            "profil": self.profil,
            "begruendung": self.begruendung,
            "bremse": self.bremse,
            "warnungen": list(self.warnungen),
            "zusatz": dict(self.zusatz),
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


def _ziele(
    per_tf: dict[Timeframe, Any], kurs: float, richtung: Direction, atr: float
) -> list[tuple[float, str]]:
    """Bis zu drei Liquiditaetsziele in Wettrichtung — TP1, TP2, TP3.

    Nur D1/H4: auf M15 liegt in jeder Richtung binnen 0.2 % irgendein Swing-Punkt, das
    waere kein Ziel, sondern Rauschen.

    Wichtig ist die Mindestentfernung. Beim ersten Lauf wurde jeweils der NAECHSTE Level
    genommen — bei LTC lag der 0.1 % entfernt, waehrend die Invalidierung strukturbedingt
    mehrere Prozent weg war. Ergebnis: R:R unter 1 bei ausnahmslos allen 28 Instrumenten.
    Das war kein Marktbefund, sondern eine falsch gestellte Frage. Ein Ziel, das naeher
    liegt als eine halbe Tagesschwankung, ist keines.
    """
    mindest = max(0.5 * atr, kurs * 0.002)
    kand: list[tuple[float, float, str]] = []
    gesehen: set[float] = set()
    for tf in (Timeframe.D1, Timeframe.H4):
        tfc = per_tf.get(tf)
        for lv in getattr(tfc, "liquidity", ()) or ():
            if getattr(getattr(lv, "state", None), "value", "") == "swept":
                continue
            if lv.strength < 0.10:
                continue
            p = float(lv.price)
            passt = p > kurs if richtung is Direction.LONG else p < kurs
            if not passt or abs(p - kurs) < mindest:
                continue
            # Level, die dicht beieinanderliegen, sind dasselbe Ziel.
            if any(abs(p - g) < mindest for g in gesehen):
                continue
            gesehen.add(p)
            kand.append((abs(p - kurs), p, getattr(lv.type, "value", "level")))
    kand.sort()
    return [(p, art) for _, p, art in kand[:3]]


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


def bewerte_chart(
    instrument: str,
    mtf: Any,
    kurs: float,
    *,
    zusatz: dict[str, Any] | None = None,
    profil: Profil | str = Profil.AGGRESSIV,
) -> ChartChance:
    """Sechs Faktoren, 100 Punkte, dann eine Note aus Score, CRV und erwarteter Bewegung.

    ``zusatz`` sind Kennzahlen von aussen (24h-Umsatz, 24h-Bewegung). Sie gehen NICHT in
    den Score ein — sie erzeugen Warnungen und stehen als Kontext in der Ausgabe. Wer
    Liquiditaet in den Score rechnet, bekommt eine Rangliste der grossen Coins statt
    eine der guten Charts.
    """
    zusatz = dict(zusatz or {})
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
    tp2 = tp3 = None
    weg = None
    punkte, detail = 0.0, "kein Ziel gefunden"
    if richtung is not None:
        gefunden = _ziele(per_tf, kurs, richtung, atr)
        if gefunden:
            ziel, ziel_art = gefunden[0]
            tp2 = gefunden[1][0] if len(gefunden) > 1 else None
            tp3 = gefunden[2][0] if len(gefunden) > 2 else None
            weg = abs(ziel - kurs)
        if ziel is not None and weg is not None and atr > 0:
            # Bewertet wird der Weg bis TP2 — dorthin laeuft der Trade, wenn er laeuft.
            fern = tp2 if tp2 is not None else ziel
            in_atr = abs(fern - kurs) / atr
            # Unter 1 ATR lohnt der Weg nicht, ab 6 ATR ist die Skala ausgereizt.
            punkte = MAX_PUNKTE["bewegungsraum"] * min(1.0, max(0.0, (in_atr - 1.0) / 5.0))
            detail = (
                f"TP1 {ziel:,.2f} ({weg / kurs * 100:.1f} %)"
                + (f" · TP2 {tp2:,.2f}" if tp2 else "")
                + (f" · TP3 {tp3:,.2f}" if tp3 else "")
                + f" — bis TP2 {in_atr:.1f} ATR"
            ).replace(",", " ")
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
    rr_ziel = tp2 if tp2 is not None else ziel
    if rr_ziel is not None and inval is not None and abs(kurs - inval) > 0:
        rr = abs(rr_ziel - kurs) / abs(kurs - inval)

    # Die erwartete Bewegung bis TP2 — die Groesse, an der ein Swing-Trade haengt.
    swing_ziel = tp2 if tp2 is not None else ziel
    erwartet = (abs(swing_ziel - kurs) / kurs * 100.0) if swing_ziel else None

    warnungen = _warnungen(zusatz, per_tf, richtung, score)

    urteilung = benote(
        score=score,
        rr=rr,
        move_pct=erwartet,
        hat_invalidierung=inval is not None and richtung is not None,
        profil=profil,
    )

    daten_ok = all(
        not getattr(per_tf.get(tf), "blocks_trading", False)
        for tf in (Timeframe.D1, Timeframe.H4)
        if per_tf.get(tf) is not None
    )
    erfuellt = sum(1 for x in f if x.anteil >= 0.5) / max(1, len(f))
    conf = confidence(
        einigkeit=einigkeit,
        score=score,
        faktoren_erfuellt=erfuellt,
        daten_ok=daten_ok,
        warnungen=len(warnungen),
    )

    if richtung is None:
        kopf = "kein klarer Trend"
    else:
        seite = "LONG" if richtung is Direction.LONG else "SHORT"
        note_text = NOTE_KURZ.get(urteilung.note, "—")
        if urteilung.note in HANDELBAR:
            luft = f", {erwartet:.1f} % bis TP2" if erwartet else ""
            kopf = (
                f"{seite} {note_text} — Score {score:.0f}, CRV 1:{rr:.1f}{luft}"
                if rr
                else (f"{seite} {note_text} — Score {score:.0f}")
            )
        elif urteilung.note == "WATCH":
            kopf = f"{seite} beobachten — {urteilung.bremse or 'noch nicht handelbar'}"
        else:
            kopf = f"{seite} — kein Trade: {urteilung.bremse or 'nichts passt zusammen'}"

    return ChartChance(
        instrument=instrument,
        richtung=richtung,
        score=score,
        faktoren=tuple(f),
        kurs=kurs,
        ziel=ziel,
        ziel_art=ziel_art,
        tp2=tp2,
        tp3=tp3,
        invalidierung=inval,
        bewegung_pct=bew_pct,
        bewegung_atr=bew_atr,
        rr=rr,
        headline=kopf,
        urteil=urteilung.note,
        erwartete_bewegung_pct=erwartet,
        confidence=conf,
        profil=str(urteilung.profil.value),
        begruendung=urteilung.begruendung,
        bremse=urteilung.bremse,
        warnungen=tuple(warnungen),
        zusatz=zusatz,
    )


def _warnungen(
    zusatz: dict[str, Any], per_tf: dict[Timeframe, Any], richtung: Direction | None, score: float
) -> list[str]:
    """Was gegen den Trade spricht, ohne ihn auszuschliessen.

    Bewusst getrennt vom Score. Eine Warnung soll sichtbar sein, nicht heimlich Punkte
    abziehen — sonst weiss niemand mehr, warum ein Chart schlechter bewertet wurde.
    """
    w: list[str] = []
    bewegung = zusatz.get("bewegung_24h_pct")
    spanne = zusatz.get("spanne_24h_pct")
    umsatz = zusatz.get("umsatz_24h")

    if bewegung is not None and abs(float(bewegung)) >= 25.0:
        w.append(
            f"in 24 h bereits {float(bewegung):+.0f} % gelaufen — ein Einstieg hier kauft "
            "die Bewegung, nicht den Aufbau"
        )
    if spanne is not None and float(spanne) >= 40.0:
        w.append(
            f"Tagesspanne {float(spanne):.0f} % — Stops brauchen entsprechend Abstand, "
            "die Position wird dadurch klein"
        )
    if umsatz is not None and float(umsatz) < 5_000_000:
        w.append(f"nur {float(umsatz) / 1e6:.1f} Mio USDT Umsatz — duenn fuer schnelle Ausstiege")

    for tf in (Timeframe.D1, Timeframe.H4):
        tfc = per_tf.get(tf)
        if tfc is not None and getattr(tfc, "blocks_trading", False):
            w.append(
                f"Reihe auf {tf.value} nicht frisch — bei Aktien ausserhalb der "
                "Boersenzeit normal, bei Krypto ein echter Ausfall"
            )
            break

    if richtung is not None:
        soll = "trend_up" if richtung is Direction.LONG else "trend_down"
        d1 = per_tf.get(Timeframe.D1)
        d1_richtung = getattr(getattr(d1, "regime", None), "directional", None)
        if d1 is not None and _v_enum(d1_richtung) not in (soll, "range", "unclear", ""):
            w.append("Tagestrend zeigt in die Gegenrichtung — das ist ein Trade gegen D1")

    return w


def _v_enum(x: Any) -> str:
    return str(getattr(x, "value", x) if x is not None else "")


__all__ = ["MAX_PUNKTE", "TF_GEWICHT", "ChartChance", "ChartFaktor", "bewerte_chart"]
