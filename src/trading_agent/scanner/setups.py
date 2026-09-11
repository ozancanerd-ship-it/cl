"""Setup-Erkennung — wie ein Trader den Chart benennt, nicht wie ein Score ihn misst.

WARUM ES DAS BRAUCHT

``chart_score`` beantwortet die Frage „wie viele Dinge sind hier ausgerichtet?" mit
einer Zahl. Ein Trader stellt eine andere Frage: „was ist das hier fuer ein Setup?"
Das ist kein Wortspiel. Ein Score von 68 kann ein sauberer Ruecksetzer im Aufwaertstrend
sein — oder ein Kurs, der seit vier Wochen senkrecht laeuft und dessen Zahlen deshalb
gut aussehen. Das erste ist ein Trade, das zweite ist ein Nachlaufen.

Ozan hat es so gesagt: „weniger Mathe, mehr Trading". Genau das ist der Unterschied.

WAS HIER GEPRUEFT WIRD

Vier Setups, wie sie in der Praxis benannt werden. Jedes hat harte Bedingungen, die
erfuellt sein muessen, und weiche, die die Qualitaet bestimmen:

* **Ruecksetzer im Trend** — das Brot-und-Butter-Setup. Trend steht, Kurs ist
  zurueckgekommen, laeuft nicht ueberdehnt. Man kauft die Pause, nicht die Bewegung.
* **Ausbruch aus der Basis** — enge Spanne, Volumen versiegt, dann Bruch mit Volumen
  und starkem Schluss. Ohne Volumen ist es kein Ausbruch, sondern ein Ausrutscher.
* **Rueckeroberung nach Liquiditaetsgriff** — ein Tief wird abgeraeumt, der Kurs kommt
  sofort zurueck. Das ist die Stelle, an der die Stops der anderen liegen.
* **Abpraller an der Unterstuetzung** — nur im intakten uebergeordneten Trend, nur mit
  Beruhigung. Ein fallendes Messer ist kein Abpraller.

Findet sich keines davon, ist die ehrliche Antwort: **kein Setup**. Kein Trade daraus.
Genau das hat den ersten Alarmen gefehlt — sie hatten Zahlen, aber keinen Namen.

QUELLEN DER REGELN

Die Bedingungen sind aus der Praxisliteratur uebernommen, nicht aus einer Optimierung:
Volumen-Trockenheit vor dem Ausbruch und Volumenschub beim Bruch, Schlusskurs im oberen
Drittel statt langem oberem Docht, Basisqualitaet ueber Spannen-Kontraktion, „nicht
ueberdehnt" ueber den Abstand zum kurzen Durchschnitt, Trendfilter der hoeheren
Zeitebene. Nichts davon ist an unsere Historie angepasst — das waere Overfitting.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from trading_agent.core.enums import Direction, Timeframe

#: Spanne der letzten Kerzen gegen die davor. Darunter gilt die Basis als eng.
KONTRAKTION_GRENZE = 0.80
#: Volumen der letzten Kerzen gegen den Schnitt. Darunter gilt es als versiegt.
TROCKEN_GRENZE = 0.85
#: Volumen am Ausbruchstag gegen den Schnitt. Darueber gilt es als Schub.
SCHUB_GRENZE = 1.30
#: Schluss im oberen/unteren Anteil der Kerze. Darunter ist es ein Docht, kein Schluss.
SCHLUSSKRAFT_GRENZE = 0.60
#: Abstand zum EMA20 in ATR. Darueber ist die Bewegung ueberdehnt — ein Einstieg hier
#: kauft die Bewegung selbst, und der Stop muss zwangslaeufig weit weg.
UEBERDEHNT_ATR = 2.5
#: So tief muss ein Ruecksetzer mindestens gehen, in ATR, damit er einer ist.
PULLBACK_MIN_ATR = 0.7
#: Wie viele Kerzen zurueck ein Liquiditaetsgriff noch frisch ist.
GRIFF_FENSTER = 8

ARTEN = (
    "TREND_PULLBACK",
    "AUSBRUCH",
    "RUECKEROBERUNG",
    "ABPRALLER",
)

NAME: dict[str, str] = {
    "TREND_PULLBACK": "Ruecksetzer im Trend",
    "AUSBRUCH": "Ausbruch aus der Basis",
    "RUECKEROBERUNG": "Rueckeroberung nach Liquiditaetsgriff",
    "ABPRALLER": "Abpraller an der Unterstuetzung",
}


@dataclass(frozen=True, slots=True)
class Setup:
    """Ein benanntes Setup mit dem, was dafuer und dagegen spricht."""

    art: str
    name: str
    #: Bedingungen, die erfuellt sind — in Traderworten, nicht als Zahlen.
    erfuellt: tuple[str, ...]
    #: Bedingungen, die fehlen. Sie machen das Setup nicht ungueltig, nur schwaecher.
    fehlt: tuple[str, ...]
    #: Was passieren muss, bevor eingestiegen wird. Der Unterschied zwischen einem
    #: Kursniveau und einem Ausloeser.
    trigger: str
    #: sauber | brauchbar | unsauber
    qualitaet: str
    #: Ein Satz, der die Handelsidee erklaert.
    these: str

    @property
    def punkte(self) -> float:
        """0..1 — wie vollstaendig die Checkliste ist."""
        gesamt = len(self.erfuellt) + len(self.fehlt)
        return len(self.erfuellt) / gesamt if gesamt else 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "art": self.art,
            "name": self.name,
            "erfuellt": list(self.erfuellt),
            "fehlt": list(self.fehlt),
            "trigger": self.trigger,
            "qualitaet": self.qualitaet,
            "these": self.these,
            "punkte": round(self.punkte, 2),
        }


# ---------------------------------------------------------------- Kerzen-Helfer


def _spanne(bar: Any) -> float:
    return float(bar.high) - float(bar.low)


def _kontraktion(bars: list[Any], n: int = 8) -> float | None:
    """Spanne der letzten n Kerzen gegen die n davor. Unter 1 = die Basis wird enger.

    Das ist die messbare Seite dessen, was Trader „tight base" nennen. Eine Basis, in
    der die Spannen gleich gross bleiben, ist keine Basis, sondern Seitwaerts-Chaos.
    """
    if len(bars) < 2 * n:
        return None
    jung = sum(_spanne(b) for b in bars[-n:]) / n
    alt = sum(_spanne(b) for b in bars[-2 * n : -n]) / n
    return jung / alt if alt > 0 else None


def _volumen_quote(bars: list[Any], letzte: int = 5, fenster: int = 20) -> float | None:
    """Volumen der letzten Kerzen gegen den laengeren Schnitt."""
    if len(bars) < fenster + letzte:
        return None
    vols = [float(getattr(b, "volume", 0.0) or 0.0) for b in bars]
    if not any(vols):
        return None
    jung = sum(vols[-letzte:]) / letzte
    basis = sum(vols[-(fenster + letzte) : -letzte]) / fenster
    return jung / basis if basis > 0 else None


def _letzte_volumenquote(bars: list[Any], fenster: int = 20) -> float | None:
    """Nur die letzte Kerze gegen den Schnitt — der Volumenschub beim Bruch."""
    if len(bars) < fenster + 1:
        return None
    vols = [float(getattr(b, "volume", 0.0) or 0.0) for b in bars]
    if not any(vols):
        return None
    basis = sum(vols[-(fenster + 1) : -1]) / fenster
    return vols[-1] / basis if basis > 0 else None


def _schlusskraft(bar: Any, lang: bool) -> float | None:
    """Wo im Kerzenkoerper liegt der Schluss? 1.0 = am Hoch (bei Long).

    Ein Ausbruch, der mit langem oberem Docht schliesst, ist genau der Fehlausbruch,
    vor dem jede Praxisquelle warnt: intraday darueber, zum Schluss zurueck in die Range.
    """
    hi, lo, cl = float(bar.high), float(bar.low), float(bar.close)
    if hi <= lo:
        return None
    lage = (cl - lo) / (hi - lo)
    return lage if lang else 1.0 - lage


def _ema(werte: list[float], periode: int) -> float | None:
    if len(werte) < periode:
        return None
    k = 2.0 / (periode + 1.0)
    schnitt = sum(werte[:periode]) / periode
    for v in werte[periode:]:
        schnitt = v * k + schnitt * (1 - k)
    return schnitt


def _ueberdehnung(bars: list[Any], atr: float) -> float | None:
    """Abstand des Kurses zum EMA20 in ATR. Das Mass fuer „schon gelaufen"."""
    if atr <= 0 or len(bars) < 20:
        return None
    e = _ema([float(b.close) for b in bars], 20)
    if e is None:
        return None
    return (float(bars[-1].close) - e) / atr


def _bars(per_tf: dict[Timeframe, Any], tf: Timeframe) -> list[Any]:
    tfc = per_tf.get(tf)
    return list(getattr(tfc, "bars", ()) or []) if tfc is not None else []


def _passt(objekt_richtung: Any, wette: Direction) -> bool:
    v = getattr(objekt_richtung, "value", objekt_richtung)
    if wette is Direction.LONG:
        return v in ("long", "bullish")
    return v in ("short", "bearish")


def _regime(per_tf: dict[Timeframe, Any], tf: Timeframe) -> str:
    return str(
        getattr(getattr(getattr(per_tf.get(tf), "regime", None), "directional", None), "value", "")
    )


def _trend_steht(per_tf: dict[Timeframe, Any], richtung: Direction) -> bool:
    """Steht die uebergeordnete Richtung? D1 oder H4 muss mitziehen, keiner dagegen."""
    soll = "trend_up" if richtung is Direction.LONG else "trend_down"
    gegen = "trend_down" if richtung is Direction.LONG else "trend_up"
    d1, h4 = _regime(per_tf, Timeframe.D1), _regime(per_tf, Timeframe.H4)
    if d1 == gegen:
        return False
    return soll in (d1, h4)


# ---------------------------------------------------------------- Die vier Setups


def _pruefe_pullback(
    per_tf: dict[Timeframe, Any], kurs: float, richtung: Direction, atr: float
) -> Setup | None:
    """Trend steht, Kurs ist zurueckgekommen, Bewegung ist nicht ueberdehnt."""
    if not _trend_steht(per_tf, richtung):
        return None
    h4 = _bars(per_tf, Timeframe.H4)
    if len(h4) < 30 or atr <= 0:
        return None
    lang = richtung is Direction.LONG

    erfuellt: list[str] = []
    fehlt: list[str] = []

    d1r, h4r = _regime(per_tf, Timeframe.D1), _regime(per_tf, Timeframe.H4)
    soll = "trend_up" if lang else "trend_down"
    if d1r == soll and h4r == soll:
        erfuellt.append("Tages- und 4-Stunden-Trend zeigen in dieselbe Richtung")
    elif soll in (d1r, h4r):
        erfuellt.append(
            f"{'Tages' if d1r == soll else '4-Stunden'}-Trend traegt, die andere Ebene ist neutral"
        )

    # Wie tief ist der Ruecksetzer? Gemessen vom Extrem der letzten 20 Kerzen.
    fenster = h4[-20:]
    extrem = max(float(b.high) for b in fenster) if lang else min(float(b.low) for b in fenster)
    tiefe = abs(extrem - kurs) / atr
    if tiefe >= PULLBACK_MIN_ATR:
        erfuellt.append(f"Kurs ist {tiefe:.1f} ATR vom letzten Extrem zurueckgekommen")
    else:
        fehlt.append("noch kein echter Ruecksetzer — der Kurs steht praktisch am Extrem")

    ueber = _ueberdehnung(h4, atr)
    if ueber is not None:
        gemessen = ueber if lang else -ueber
        if gemessen <= UEBERDEHNT_ATR:
            erfuellt.append("nicht ueberdehnt — der Kurs steht nah an seinem Durchschnitt")
        else:
            fehlt.append(
                f"ueberdehnt: {gemessen:.1f} ATR ueber dem 20er-Schnitt, ein Einstieg hier "
                "kauft die Bewegung"
            )

    # Ein frischer Bruch in Richtung sagt: der Trend hat zuletzt geliefert.
    for tf in (Timeframe.H4, Timeframe.D1):
        brueche = list(getattr(per_tf.get(tf), "structure_breaks", ()) or ())
        if brueche and _passt(brueche[-1].direction, richtung):
            erfuellt.append(f"letzter Strukturbruch auf {tf.value} lief in Handelsrichtung")
            break
    else:
        fehlt.append("kein frischer Strukturbruch in Handelsrichtung")

    # Kommt er in eine Zone zurueck, in der Angebot/Nachfrage liegt?
    zone_da = False
    for tf in (Timeframe.H4, Timeframe.H1):
        tfc = per_tf.get(tf)
        for gruppe in (getattr(tfc, "fvgs", ()) or (), getattr(tfc, "order_blocks", ()) or ()):
            for z in gruppe:
                if getattr(getattr(z, "state", None), "value", "") not in (
                    "unmitigated",
                    "partial",
                ):
                    continue
                if not _passt(z.direction, richtung):
                    continue
                mitte = (float(z.zone_low) + float(z.zone_high)) / 2
                if abs(mitte - kurs) / atr <= 2.0:
                    zone_da = True
                    break
    if zone_da:
        erfuellt.append("der Ruecklauf trifft eine offene Zone, kein leeres Kursniveau")
    else:
        fehlt.append("keine offene Zone im Ruecklaufbereich")

    if len(erfuellt) < 3:
        return None

    guete = "sauber" if len(fehlt) == 0 else ("brauchbar" if len(fehlt) <= 1 else "unsauber")
    seite = "Aufwaerts" if lang else "Abwaerts"
    return Setup(
        art="TREND_PULLBACK",
        name=NAME["TREND_PULLBACK"],
        erfuellt=tuple(erfuellt),
        fehlt=tuple(fehlt),
        trigger=(
            "Einstieg erst, wenn der Kurs in der Zone dreht — eine 4-Stunden-Kerze, "
            f"die {'ueber' if lang else 'unter'} ihrer Vorgaengerin schliesst."
        ),
        qualitaet=guete,
        these=(
            f"{seite}trend steht, der Kurs macht Pause. Gehandelt wird die Pause, "
            "nicht der Ausbruch — deshalb liegt der Stop hinter dem letzten Strukturpunkt "
            "und nicht knapp unter dem Kurs."
        ),
    )


def _pruefe_ausbruch(
    per_tf: dict[Timeframe, Any], kurs: float, richtung: Direction, atr: float
) -> Setup | None:
    """Enge Basis, versiegtes Volumen, dann Bruch mit Schub und starkem Schluss."""
    h4 = _bars(per_tf, Timeframe.H4)
    if len(h4) < 40 or atr <= 0:
        return None
    lang = richtung is Direction.LONG
    erfuellt: list[str] = []
    fehlt: list[str] = []

    # Basis und Volumen werden OHNE die letzte Kerze gemessen. Die Ausbruchskerze ist
    # per Definition breit und volumenstark — rechnete man sie mit, wuerde sie genau
    # die Kontraktion und die Volumen-Trockenheit zudecken, die sie erst bedeutsam
    # machen. Gemessen wird die Basis, ausgeloest wird durch das, was danach kommt.
    basis = h4[:-1]
    kontr = _kontraktion(basis)
    if kontr is None:
        return None
    if kontr <= KONTRAKTION_GRENZE:
        erfuellt.append(f"die Spannen sind enger geworden ({kontr:.2f}) — eine Basis bildet sich")
    else:
        fehlt.append("keine Kontraktion — die Spannen bleiben breit, das ist keine Basis")

    trocken = _volumen_quote(basis)
    if trocken is not None:
        if trocken <= TROCKEN_GRENZE:
            erfuellt.append("Volumen ist in der Basis versiegt — das Angebot ist abgearbeitet")
        else:
            fehlt.append("Volumen bleibt hoch — die Basis ist noch umkaempft")

    schub = _letzte_volumenquote(h4)
    if schub is not None:
        if schub >= SCHUB_GRENZE:
            erfuellt.append(f"Volumenschub beim Bruch ({schub:.1f}× Schnitt)")
        else:
            fehlt.append("Ausbruch ohne Volumen — genau so entstehen Fehlausbrueche")

    kraft = _schlusskraft(h4[-1], lang)
    if kraft is not None:
        if kraft >= SCHLUSSKRAFT_GRENZE:
            erfuellt.append("Schluss im starken Drittel der Kerze, kein langer Gegen-Docht")
        else:
            fehlt.append(
                "die Kerze schliesst im schwachen Teil — intraday drueber, am Ende drunter"
            )

    # Steht der Kurs ueberhaupt an der Kante der Basis?
    fenster = h4[-25:-1]
    kante = max(float(b.high) for b in fenster) if lang else min(float(b.low) for b in fenster)
    nah = (kurs >= kante - 0.6 * atr) if lang else (kurs <= kante + 0.6 * atr)
    if nah:
        erfuellt.append(f"Kurs steht an der Kante der Basis ({kante:,.4f})".replace(",", " "))
    else:
        return None

    if not _trend_steht(per_tf, richtung):
        fehlt.append("die uebergeordnete Richtung traegt den Ausbruch nicht")
    else:
        erfuellt.append("der Ausbruch laeuft mit der uebergeordneten Richtung")

    if len(erfuellt) < 3:
        return None
    guete = "sauber" if len(fehlt) == 0 else ("brauchbar" if len(fehlt) <= 1 else "unsauber")
    return Setup(
        art="AUSBRUCH",
        name=NAME["AUSBRUCH"],
        erfuellt=tuple(erfuellt),
        fehlt=tuple(fehlt),
        trigger=(
            f"Einstieg erst bei einem 4-Stunden-Schluss {'ueber' if lang else 'unter'} "
            f"{kante:,.4f} — nicht beim Antippen.".replace(",", " ")
        ),
        qualitaet=guete,
        these=(
            "Der Kurs hat sich in einer engen Spanne beruhigt und bricht heraus. Die Idee "
            "lebt davon, dass der Bruch haelt — schliesst er zurueck in die Spanne, war es "
            "ein Fehlausbruch und der Trade ist erledigt."
        ),
    )


def _pruefe_rueckeroberung(
    per_tf: dict[Timeframe, Any], kurs: float, richtung: Direction, atr: float
) -> Setup | None:
    """Ein Extrem wurde abgeraeumt und sofort zurueckerobert."""
    h4 = _bars(per_tf, Timeframe.H4)
    if len(h4) < 25 or atr <= 0:
        return None
    lang = richtung is Direction.LONG
    fenster = h4[-GRIFF_FENSTER:]
    davor = h4[-30:-GRIFF_FENSTER]
    if len(davor) < 8:
        return None

    marke = min(float(b.low) for b in davor) if lang else max(float(b.high) for b in davor)
    gegriffen = (
        any(float(b.low) < marke for b in fenster)
        if lang
        else any(float(b.high) > marke for b in fenster)
    )
    zurueck = kurs > marke if lang else kurs < marke
    if not (gegriffen and zurueck):
        return None

    erfuellt = [
        f"das {'Tief' if lang else 'Hoch'} bei {marke:,.4f} wurde abgeraeumt".replace(",", " "),
        "der Kurs steht wieder auf der richtigen Seite — die Bewegung wurde nicht bestaetigt",
    ]
    fehlt: list[str] = []

    schub = _letzte_volumenquote(h4)
    if schub is not None:
        if schub >= 1.15:
            erfuellt.append("Volumen beim Zurueckkommen ueber dem Schnitt")
        else:
            fehlt.append("die Rueckeroberung laeuft ohne Volumen")

    kraft = _schlusskraft(h4[-1], lang)
    if kraft is not None and kraft >= SCHLUSSKRAFT_GRENZE:
        erfuellt.append("die letzte Kerze schliesst stark")
    elif kraft is not None:
        fehlt.append("der Schluss ist schwach — die Rueckeroberung ist noch nicht ueberzeugend")

    if _trend_steht(per_tf, richtung):
        erfuellt.append("die uebergeordnete Richtung traegt")
    else:
        fehlt.append(
            "gegen die uebergeordnete Richtung — dann ist es hoechstens eine Gegenbewegung"
        )

    guete = "sauber" if len(fehlt) == 0 else ("brauchbar" if len(fehlt) <= 1 else "unsauber")
    return Setup(
        art="RUECKEROBERUNG",
        name=NAME["RUECKEROBERUNG"],
        erfuellt=tuple(erfuellt),
        fehlt=tuple(fehlt),
        trigger=(
            f"Der Kurs muss {'ueber' if lang else 'unter'} {marke:,.4f} bleiben. "
            "Faellt er wieder darueber hinaus, ist die Idee erledigt.".replace(",", " ")
        ),
        qualitaet=guete,
        these=(
            "Dort lagen die Stops. Sie wurden geholt, und der Kurs kam sofort zurueck — "
            "das ist die Stelle, an der eine Bewegung oft beginnt, weil die "
            "Gegenseite gerade ausgestoppt wurde."
        ),
    )


def _pruefe_abpraller(
    per_tf: dict[Timeframe, Any], kurs: float, richtung: Direction, atr: float
) -> Setup | None:
    """Tief im Trend, RSI unten, Beruhigung sichtbar. Nur mit intaktem Obertrend."""
    from trading_agent.analysis.indicators import berechne

    if richtung is not Direction.LONG:
        return None  # Abpraller nach unten waeren short-Gegenbewegungen; bewusst ausgelassen
    d1 = _bars(per_tf, Timeframe.D1)
    h4 = _bars(per_tf, Timeframe.H4)
    if len(h4) < 60 or atr <= 0:
        return None
    if _regime(per_tf, Timeframe.D1) == "trend_down":
        return None

    ind4 = berechne(h4)
    if ind4.rsi is None or ind4.rsi > 38:
        return None

    erfuellt = [f"4-Stunden-RSI bei {ind4.rsi:.0f} — der Ruecksetzer ist ausgereizt"]
    fehlt: list[str] = []

    if len(d1) >= 200:
        ind1 = berechne(d1)
        if ind1.ueber_ema_lang:
            erfuellt.append("der Tageskurs steht ueber dem 200er-Schnitt — der Obertrend haelt")
        else:
            fehlt.append("unter dem 200er-Schnitt — hier faengt man ein fallendes Messer")

    kraft = _schlusskraft(h4[-1], True)
    if kraft is not None and kraft >= 0.55:
        erfuellt.append("die letzte Kerze schliesst im oberen Bereich — erste Beruhigung")
    else:
        fehlt.append("noch keine Beruhigung — der Kurs schliesst weiter schwach")

    if ind4.rsi_divergenz == "bullisch":
        erfuellt.append("bullishe RSI-Divergenz: neues Tief im Kurs, hoeheres Tief im RSI")

    if len(erfuellt) < 2:
        return None
    guete = "sauber" if len(fehlt) == 0 else ("brauchbar" if len(fehlt) <= 1 else "unsauber")
    return Setup(
        art="ABPRALLER",
        name=NAME["ABPRALLER"],
        erfuellt=tuple(erfuellt),
        fehlt=tuple(fehlt),
        trigger=(
            "Einstieg erst, wenn eine 4-Stunden-Kerze ueber dem Hoch der Vorkerze schliesst. "
            "Ohne diese Bestaetigung faengt man das fallende Messer."
        ),
        qualitaet=guete,
        these=(
            "Der uebergeordnete Trend ist intakt, der Ruecksetzer ist ausgereizt. Gehandelt "
            "wird die Rueckkehr zum Trend — nicht die Trendwende."
        ),
    )


# ---------------------------------------------------------------- Einstiegspunkt


#: Reihenfolge der Pruefung. Das erste passende Setup gewinnt — sie sind so sortiert,
#: dass das spezifischere vor dem allgemeineren steht.
_PRUEFER = (
    _pruefe_rueckeroberung,
    _pruefe_ausbruch,
    _pruefe_pullback,
    _pruefe_abpraller,
)


def erkenne_setup(
    per_tf: dict[Timeframe, Any], kurs: float, richtung: Direction | None, atr: float
) -> Setup | None:
    """Das erste Setup, dessen harte Bedingungen erfuellt sind — oder None.

    ``None`` heisst nicht „schlechter Chart". Es heisst: hier ist gerade nichts, was
    einen Namen haette. Ein Trade ohne Namen ist eine Vermutung.
    """
    if richtung is None or kurs <= 0:
        return None
    for pruefer in _PRUEFER:
        try:
            s = pruefer(per_tf, kurs, richtung, atr)
        except (AttributeError, TypeError, ValueError, IndexError, ZeroDivisionError):
            continue
        if s is not None:
            return s
    return None


def alle_setups(
    per_tf: dict[Timeframe, Any], kurs: float, richtung: Direction | None, atr: float
) -> tuple[Setup, ...]:
    """Alle passenden Setups — mehrere gleichzeitig sind ein Confluence-Argument."""
    if richtung is None or kurs <= 0:
        return ()
    aus: list[Setup] = []
    for pruefer in _PRUEFER:
        try:
            s = pruefer(per_tf, kurs, richtung, atr)
        except (AttributeError, TypeError, ValueError, IndexError, ZeroDivisionError):
            continue
        if s is not None:
            aus.append(s)
    return tuple(aus)


__all__ = [
    "ARTEN",
    "NAME",
    "Setup",
    "alle_setups",
    "erkenne_setup",
]
