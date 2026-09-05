"""Die Makrolage fuer den Scanner — Kontext und Risiko, nicht Signal.

Nicht zu verwechseln mit :mod:`trading_agent.analysis.macro`. Das dort ist die
strenge Point-in-Time-Schicht ueber FRED-Vintages fuer den Backtest: jede Zahl mit
ihrem Bekanntwerdungszeitpunkt, damit ein Test nicht mit Wissen rechnet, das es
damals noch nicht gab. Dieses Modul hier ist das Gegenstueck fuer den **laufenden
Betrieb**: es fragt, wie die Lage JETZT ist, und braucht dafuer keine Vintages.


Ozans Vorgabe aus dem Masterplan, woertlich: „News sollen nicht blind als
Trading-Signal verwendet werden. Sie sollen den technischen Kontext und das Risiko
beeinflussen."

Genau so ist es gebaut. Nichts hier geht in den Chart-Score ein. Die Makrolage
erzeugt **Warnungen** und **Kontextsaetze**, und ein bevorstehender wichtiger Termin
kann ein Setup ausbremsen — aber sie erzeugt nie ein Kauf- oder Verkaufssignal.

WARUM DIESE VIER GROESSEN

* **VIX** — der Preis der Absicherung am US-Aktienmarkt. Steigt er schnell, wird alles
  Riskante gleichzeitig verkauft, und Einzelanalysen zaehlen weniger als die Richtung
  des Gesamtmarkts.
* **Dollar-Index (DXY)** — ein starker Dollar zieht Gold und Krypto Liquiditaet ab.
* **10-jaehrige US-Rendite** — steigende Renditen sind Gegenwind fuer Wachstumswerte
  (deren Gewinn liegt in der Zukunft) und fuer Gold (das keine Zinsen zahlt).
* **S&P 500** — die Referenz, gegen die relative Staerke gemessen wird.

Das sind **Zusammenhaenge, keine Gesetze.** Sie halten oft und brechen manchmal. Die
Bewertung darunter ist gesetzt, nicht an historischen Ergebnissen optimiert — das
waere genau das Overfitting, das wir vermeiden.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

#: Yahoo-Symbole der Kennzahlen, mit Klartextnamen.
KENNZAHLEN: dict[str, tuple[str, str]] = {
    "vix": ("^VIX", "Volatilitaetsindex VIX"),
    "dxy": ("DX-Y.NYB", "US-Dollar-Index"),
    "us10y": ("^TNX", "10-jaehrige US-Rendite"),
    "spx": ("^GSPC", "S&P 500"),
}

#: Ab hier gilt der Markt als nervoes bzw. als ruhig.
VIX_RUHIG = 16.0
VIX_NERVOES = 24.0
VIX_PANIK = 32.0


@dataclass(frozen=True, slots=True)
class MacroWert:
    schluessel: str
    name: str
    symbol: str
    wert: float
    aenderung_5t_pct: float | None
    aenderung_20t_pct: float | None
    stand: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "schluessel": self.schluessel,
            "name": self.name,
            "symbol": self.symbol,
            "wert": round(self.wert, 4),
            "aenderung_5t_pct": (
                round(self.aenderung_5t_pct, 2) if self.aenderung_5t_pct is not None else None
            ),
            "aenderung_20t_pct": (
                round(self.aenderung_20t_pct, 2) if self.aenderung_20t_pct is not None else None
            ),
            "stand": self.stand,
        }


@dataclass(frozen=True, slots=True)
class Termin:
    """Ein Termin aus dem Wirtschaftskalender."""

    titel: str
    land: str
    zeitpunkt: datetime
    wirkung: str  # High | Medium | Low
    prognose: str = ""
    vorher: str = ""

    @property
    def hoch(self) -> bool:
        return self.wirkung.lower() == "high"

    def as_dict(self) -> dict[str, Any]:
        return {
            "titel": self.titel,
            "land": self.land,
            "zeitpunkt": self.zeitpunkt.isoformat(),
            "wirkung": self.wirkung,
            "prognose": self.prognose,
            "vorher": self.vorher,
        }


@dataclass(frozen=True, slots=True)
class MacroLage:
    werte: tuple[MacroWert, ...]
    regime: str  # risk_on | neutral | risk_off
    punkte: float  # -1 .. +1
    begruendung: tuple[str, ...]
    wirkung: dict[str, str] = field(default_factory=dict)  # Anlageklasse -> Satz
    termine: tuple[Termin, ...] = ()
    erzeugt: str = ""

    def wert(self, schluessel: str) -> MacroWert | None:
        return next((w for w in self.werte if w.schluessel == schluessel), None)

    def naechste_termine(self, *, stunden: int = 36, nur_hoch: bool = True) -> list[Termin]:
        jetzt = datetime.now(UTC)
        grenze = jetzt + timedelta(hours=stunden)
        return [
            t for t in self.termine if jetzt <= t.zeitpunkt <= grenze and (t.hoch or not nur_hoch)
        ]

    def as_dict(self) -> dict[str, Any]:
        return {
            "erzeugt": self.erzeugt or datetime.now(UTC).isoformat(),
            "regime": self.regime,
            "punkte": round(self.punkte, 3),
            "begruendung": list(self.begruendung),
            "wirkung": dict(self.wirkung),
            "werte": [w.as_dict() for w in self.werte],
            "termine": [t.as_dict() for t in self.termine],
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> MacroLage | None:
        if not d:
            return None
        werte = tuple(
            MacroWert(
                schluessel=str(w.get("schluessel", "")),
                name=str(w.get("name", "")),
                symbol=str(w.get("symbol", "")),
                wert=float(w.get("wert") or 0.0),
                aenderung_5t_pct=w.get("aenderung_5t_pct"),
                aenderung_20t_pct=w.get("aenderung_20t_pct"),
                stand=w.get("stand"),
            )
            for w in (d.get("werte") or [])
        )
        termine: list[Termin] = []
        for t in d.get("termine") or []:
            try:
                termine.append(
                    Termin(
                        titel=str(t.get("titel", "")),
                        land=str(t.get("land", "")),
                        zeitpunkt=datetime.fromisoformat(str(t.get("zeitpunkt"))),
                        wirkung=str(t.get("wirkung", "")),
                        prognose=str(t.get("prognose", "")),
                        vorher=str(t.get("vorher", "")),
                    )
                )
            except (ValueError, TypeError):
                continue
        return cls(
            werte=werte,
            regime=str(d.get("regime", "neutral")),
            punkte=float(d.get("punkte") or 0.0),
            begruendung=tuple(str(x) for x in (d.get("begruendung") or [])),
            wirkung=dict(d.get("wirkung") or {}),
            termine=tuple(termine),
            erzeugt=str(d.get("erzeugt", "")),
        )


def _aenderung(reihe: Sequence[float], tage: int) -> float | None:
    if len(reihe) <= tage:
        return None
    davor = reihe[-1 - tage]
    if davor == 0:
        return None
    return (reihe[-1] / davor - 1.0) * 100.0


def baue_werte(
    reihen: dict[str, list[float]], stand: dict[str, str] | None = None
) -> list[MacroWert]:
    """Aus Schlusskursreihen die Kennzahlen mit ihren Veraenderungen."""
    aus: list[MacroWert] = []
    for schluessel, (symbol, name) in KENNZAHLEN.items():
        r = reihen.get(schluessel) or []
        if not r:
            continue
        aus.append(
            MacroWert(
                schluessel=schluessel,
                name=name,
                symbol=symbol,
                wert=float(r[-1]),
                aenderung_5t_pct=_aenderung(r, 5),
                aenderung_20t_pct=_aenderung(r, 20),
                stand=(stand or {}).get(schluessel),
            )
        )
    return aus


def bewerte(werte: Sequence[MacroWert], termine: Sequence[Termin] = ()) -> MacroLage:
    """Risk-on / risk-off aus den vier Kennzahlen. Gesetzte Regeln, keine Anpassung."""
    nach = {w.schluessel: w for w in werte}
    punkte = 0.0
    gruende: list[str] = []

    vix = nach.get("vix")
    if vix is not None:
        if vix.wert >= VIX_PANIK:
            punkte -= 1.0
            gruende.append(f"VIX bei {vix.wert:.1f} — Panikbereich, alles Riskante wird verkauft")
        elif vix.wert >= VIX_NERVOES:
            punkte -= 0.5
            gruende.append(f"VIX bei {vix.wert:.1f} — der Markt sichert sich ab")
        elif vix.wert <= VIX_RUHIG:
            punkte += 0.4
            gruende.append(f"VIX bei {vix.wert:.1f} — ruhig, Risiko wird gerade bezahlt")
        if vix.aenderung_5t_pct is not None and vix.aenderung_5t_pct >= 30.0:
            punkte -= 0.4
            gruende.append(
                f"VIX {vix.aenderung_5t_pct:+.0f} % in fuenf Tagen — die Nervositaet nimmt schnell zu"
            )

    spx = nach.get("spx")
    if spx is not None and spx.aenderung_20t_pct is not None:
        if spx.aenderung_20t_pct >= 2.0:
            punkte += 0.3
            gruende.append(f"S&P 500 {spx.aenderung_20t_pct:+.1f} % in vier Wochen")
        elif spx.aenderung_20t_pct <= -4.0:
            punkte -= 0.4
            gruende.append(
                f"S&P 500 {spx.aenderung_20t_pct:+.1f} % in vier Wochen — der Markt gibt nach"
            )

    dxy = nach.get("dxy")
    wirkung: dict[str, str] = {}
    if dxy is not None and dxy.aenderung_20t_pct is not None:
        if dxy.aenderung_20t_pct >= 2.0:
            punkte -= 0.3
            gruende.append(f"Dollar {dxy.aenderung_20t_pct:+.1f} % staerker — zieht Liquiditaet ab")
            wirkung["krypto"] = "starker Dollar ist Gegenwind"
            wirkung["gold"] = "starker Dollar ist Gegenwind"
        elif dxy.aenderung_20t_pct <= -2.0:
            punkte += 0.3
            gruende.append(
                f"Dollar {dxy.aenderung_20t_pct:+.1f} % schwaecher — Rueckenwind fuer Sachwerte"
            )
            wirkung["krypto"] = "schwacher Dollar ist Rueckenwind"
            wirkung["gold"] = "schwacher Dollar ist Rueckenwind"

    y10 = nach.get("us10y")
    if y10 is not None and y10.aenderung_20t_pct is not None:
        if y10.aenderung_20t_pct >= 6.0:
            punkte -= 0.2
            gruende.append(
                f"10-Jahres-Rendite {y10.aenderung_20t_pct:+.1f} % — Gegenwind fuer Wachstum und Gold"
            )
            wirkung["aktien"] = "steigende Renditen belasten Wachstumswerte"
            wirkung.setdefault("gold", "steigende Renditen belasten Gold")
        elif y10.aenderung_20t_pct <= -6.0:
            punkte += 0.2
            gruende.append(f"10-Jahres-Rendite {y10.aenderung_20t_pct:+.1f} % — Entlastung")

    punkte = max(-1.0, min(1.0, punkte))
    regime = "risk_on" if punkte >= 0.35 else "risk_off" if punkte <= -0.35 else "neutral"
    if not gruende:
        gruende.append("keine der Kennzahlen zeigt etwas Auffaelliges")

    return MacroLage(
        werte=tuple(werte),
        regime=regime,
        punkte=punkte,
        begruendung=tuple(gruende),
        wirkung=wirkung,
        termine=tuple(termine),
        erzeugt=datetime.now(UTC).isoformat(),
    )


#: Welche Termine welche Anlageklasse betreffen. Grob, aber ehrlich grob.
KLASSE_JE_LAND: dict[str, tuple[str, ...]] = {
    "USD": ("aktien", "krypto", "gold"),
    "EUR": ("aktien", "gold"),
    "GBP": ("aktien",),
    "ALL": ("aktien", "krypto", "gold"),
}


def warnungen_fuer(lage: MacroLage | None, klasse: str, richtung: str | None) -> list[str]:
    """Was die Makrolage fuer ein einzelnes Setup bedeutet.

    Bewusst als Warnung, nicht als Punktabzug. Eine Warnung kann man lesen und
    ueberstimmen; ein heimlicher Abzug am Score waere nicht nachvollziehbar.
    """
    if lage is None:
        return []
    aus: list[str] = []
    if lage.regime == "risk_off" and richtung == "long":
        aus.append(f"Makro risk-off ({lage.begruendung[0]}) — Long laeuft gegen den Wind")
    elif lage.regime == "risk_on" and richtung == "short":
        aus.append(f"Makro risk-on ({lage.begruendung[0]}) — Short laeuft gegen den Wind")
    if satz := lage.wirkung.get(klasse):
        aus.append(f"Makro: {satz}")

    naechste = [
        t
        for t in lage.naechste_termine(stunden=36)
        if klasse in KLASSE_JE_LAND.get(t.land.upper(), ("aktien", "krypto", "gold"))
    ]
    if naechste:
        t = naechste[0]
        stunden = (t.zeitpunkt - datetime.now(UTC)).total_seconds() / 3600
        aus.append(
            f"{t.titel} ({t.land}) in {stunden:.0f} h — vor wichtigen Terminen bewegt sich "
            "der Kurs an der Analyse vorbei"
        )
    return aus


__all__ = [
    "KENNZAHLEN",
    "KLASSE_JE_LAND",
    "VIX_NERVOES",
    "VIX_PANIK",
    "VIX_RUHIG",
    "MacroLage",
    "MacroWert",
    "Termin",
    "baue_werte",
    "bewerte",
    "warnungen_fuer",
]
