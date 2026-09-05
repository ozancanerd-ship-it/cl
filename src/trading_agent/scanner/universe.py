"""Das handelbare Universum — dynamisch aus der Boerse, nicht aus einer Liste im Code.

Ozans Vorgabe woertlich: „Wenn heute Coin X die beste Opportunity besitzt, muss Coin X
gefunden werden koennen, auch wenn er gestern noch nicht relevant war."

Eine feste Liste kann das nicht. Also wird sie bei jedem Lauf neu gebildet: alle
handelbaren Paare der Boerse, dann durch Qualitaetsfilter, dann nach Liquiditaet
sortiert. Wer heute genug Umsatz hat, ist drin; wer austrocknet, faellt raus.

**Warum ueberhaupt gefiltert wird.** Ein Altcoin darf nicht deshalb attraktiv aussehen,
weil er extrem volatil ist — er muss auch handelbar sein. Ohne Umsatz bekommt man den
Einstieg nicht zum angezeigten Kurs und den Ausstieg gar nicht. Deshalb:

* **Umsatz** — unter der Schwelle ist der eigene Trade der Markt.
* **Stablecoins** raus — USDCUSDT hat keine Chartstruktur, nur Rauschen um 1,00.
* **Hebel-Token** raus (UP/DOWN/BULL/BEAR, 3L/3S) — die bilden nicht den Coin ab,
  sondern ein taeglich zurueckgesetztes Derivat mit eingebautem Zerfall.
* **Verpackte Doppel** raus (WBTC, WBETH …) — dieselbe Wette wie das Original, sie
  wuerde das Ranking nur mit Zwillingen fuellen.
* **Kurs-Untergrenze** — bei 0,00000012 USDT ist die kleinste Preisstufe schon ein
  Prozent Spread; Stops sind dort nicht sinnvoll setzbar.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

#: Basiswerte, die keine eigene Wette sind.
STABLECOINS = frozenset(
    {
        "USDC",
        "FDUSD",
        "TUSD",
        "BUSD",
        "DAI",
        "USDP",
        "USDD",
        "PYUSD",
        "USD1",
        "EUR",
        "EURI",
        "GBP",
        "AEUR",
        "TRY",
        "BRL",
        "ARS",
        "JPY",
        "RON",
        "ZAR",
        "PLN",
        "MXN",
        "COP",
        "CZK",
        "UAH",
        "NGN",
        "IDRT",
        "BIDR",
        "VAI",
        "USTC",
        "SUSD",
        "LUSD",
        "CRVUSD",
        "GUSD",
        "FRAX",
    }
)

#: Verpackte oder gestakte Doppel eines Basiswerts — dieselbe Wette, anderer Name.
DOPPEL = frozenset(
    {
        "WBTC",
        "WBETH",
        "BETH",
        "WETH",
        "STETH",
        "WSTETH",
        "CBBTC",
        "TBTC",
        "SOLVBTC",
        "BTCB",
        "MBTC",
        "LBTC",
        "RETH",
        "EZETH",
        "WEETH",
        "METH",
    }
)

#: Hebel-Token: taeglich zurueckgesetzt, mit Zerfall. Nie ein Swing-Instrument.
_HEBEL = re.compile(r"(?:UP|DOWN|BULL|BEAR)$|^\d+[LS]$|\d+[LS]$")

#: Gold laeuft als eigene Anlageklasse, nicht als Altcoin im Krypto-Ranking.
GOLD_BASIS = frozenset({"PAXG", "XAUT"})

#: Echte Kryptowerte, deren Kuerzel zufaellig auf B endet. Sie sind die Ausnahme von der
#: Regel darunter — die Liste ist kurz und aendert sich selten.
KRYPTO_AUF_B = frozenset({"ARB", "BB", "BEB", "BNB", "CKB", "DGB", "SHIB", "TRB", "YB"})


@dataclass(frozen=True, slots=True)
class UniversumEintrag:
    """Ein Paar, das die Filter ueberstanden hat."""

    instrument: str
    basis: str
    kurs: float
    umsatz_24h: float
    bewegung_24h_pct: float
    spanne_24h_pct: float  # (Hoch − Tief) / Tief, in Prozent — grober Bewegungsraum
    trades_24h: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "instrument": self.instrument,
            "basis": self.basis,
            "kurs": self.kurs,
            "umsatz_24h": round(self.umsatz_24h),
            "bewegung_24h_pct": round(self.bewegung_24h_pct, 2),
            "spanne_24h_pct": round(self.spanne_24h_pct, 2),
            "trades_24h": self.trades_24h,
        }


@dataclass(frozen=True, slots=True)
class UniversumFilter:
    """Die Schwellen. Bewusst als Daten, damit man sie sehen und begruenden kann."""

    quote: str = "USDT"
    #: Mindestumsatz in 24 h (Quote-Waehrung). 3 Mio USDT gemessen an Ozans Groessen-
    #: ordnung: eine Position von 50–200 EUR ist dort ein Tropfen, der Kurs merkt sie
    #: nicht. Hoehere Schwellen (10 Mio) halbieren das Universum, ohne dass es fuer
    #: dieses Kapital einen Unterschied macht — dann fehlen genau die Altcoins, die
    #: sich schnell bewegen.
    min_umsatz: float = 3_000_000.0
    #: Mindestkurs — darunter ist die Preisstufe selbst schon ein spuerbarer Spread.
    min_kurs: float = 0.0005
    #: Mindestzahl an Abschluessen. Viel Umsatz aus wenigen Grossorders ist keine Tiefe.
    min_trades: int = 3_000
    #: Wie viele Paare hoechstens ins Ranking gehen. Der Deckel ist eine Rechenzeit-,
    #: keine Qualitaetsgrenze — sortiert wird nach Umsatz, gekappt wird unten.
    max_symbole: int = 150
    #: Zusaetzlich immer dabei, auch wenn sie den Umsatzfilter reissen wuerden.
    immer_dabei: tuple[str, ...] = ("BTCUSDT", "ETHUSDT")
    ausschluss_basis: frozenset[str] = field(
        default_factory=lambda: STABLECOINS | DOPPEL | GOLD_BASIS
    )
    #: Tokenisierte Aktien und ETFs aussortieren (siehe :func:`ist_tokenisierte_aktie`).
    ohne_tokenisierte_aktien: bool = True


@dataclass(frozen=True, slots=True)
class UniversumBericht:
    """Was die Filter gemacht haben — damit ein leeres Universum erklaerbar ist."""

    gesamt: int
    nach_quote: int
    nach_ausschluss: int
    nach_liquiditaet: int
    genommen: int
    verworfen: dict[str, int]
    #: Welche Basiswerte als tokenisierte Aktie/ETF ausgeschlossen wurden. Steht im
    #: Bericht, damit ein faelschlich aussortierter Coin auffaellt statt zu verschwinden.
    tokenisiert: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "gesamt": self.gesamt,
            "nach_quote": self.nach_quote,
            "nach_ausschluss": self.nach_ausschluss,
            "nach_liquiditaet": self.nach_liquiditaet,
            "genommen": self.genommen,
            "verworfen": dict(self.verworfen),
            "tokenisiert": list(self.tokenisiert),
        }


def ist_hebel_token(basis: str) -> bool:
    return bool(_HEBEL.search(basis.upper()))


def ist_tokenisierte_aktie(basis: str) -> bool:
    """Tokenisierte Aktien und ETFs, die die Boerse als USDT-Paare fuehrt.

    Binance haengt daran ein ``B``: NVDAB, TSLAB, AAPLB, MSFTB, QQQB, SPYB, SOXLB,
    TQQQB, SQQQB … Aktuell sind das rund siebzig Paare, und sie sehen im Universum aus
    wie Altcoins. Drei Gruende, warum sie hier nicht hingehoeren:

    * Sie doppeln den Aktienscan, nur mit weniger Umsatz und Nachbildungsfehlern.
    * QQQB, SPYB, SOXLB, TQQQB, SQQQB sind **ETFs** — ausdruecklich ausgeschlossen.
    * TQQQB/SQQQB/SOXLB sind zusaetzlich gehebelt und werden taeglich zurueckgesetzt.

    Erkannt wird es an der Endung, mit einer kurzen Ausnahmeliste fuer echte Coins
    (:data:`KRYPTO_AUF_B`). Der Test schlaegt also im Zweifel zum Ausschluss aus: ein
    neues Aktien-Token faellt automatisch raus, ein neuer Coin auf B muesste einmal
    eingetragen werden. Diese Richtung ist die richtige — ein faelschlich gehandeltes
    ETF-Token waere teurer als ein uebersehener Coin, und die Ausgeschlossenen stehen
    mit Namen im Bericht.
    """
    b = basis.upper()
    return len(b) >= 3 and b.endswith("B") and b not in KRYPTO_AUF_B


def _basis_ok(basis: str, f: UniversumFilter) -> bool:
    b = basis.upper()
    if not b or b in f.ausschluss_basis or ist_hebel_token(b):
        return False
    return not (f.ohne_tokenisierte_aktien and ist_tokenisierte_aktie(b))


def bilde_universum(
    symbole: Iterable[dict[str, Any]],
    ticker: Iterable[dict[str, Any]],
    f: UniversumFilter | None = None,
) -> tuple[list[UniversumEintrag], UniversumBericht]:
    """Reine Funktion: Symbolliste + 24h-Ticker rein, gefiltertes Universum raus.

    Kein Netz, keine Boersenkenntnis — dadurch pruefbar ohne Live-Verbindung.
    """
    f = f or UniversumFilter()
    syms = list(symbole)
    kurse = {str(t.get("instrument", "")).upper(): t for t in ticker}

    verworfen = {
        "quote": 0,
        "stablecoin_oder_doppel": 0,
        "hebel_token": 0,
        "tokenisierte_aktie": 0,
        "umsatz": 0,
        "kurs": 0,
        "trades": 0,
        "kein_kurs": 0,
    }
    tokenisiert: list[str] = []
    gesamt = len(syms)

    nach_quote = [s for s in syms if str(s.get("quote", "")).upper() == f.quote.upper()]
    verworfen["quote"] = gesamt - len(nach_quote)

    erlaubt = []
    for s in nach_quote:
        b = str(s.get("basis", "")).upper()
        if not b or b in f.ausschluss_basis:
            verworfen["stablecoin_oder_doppel"] += 1
            continue
        if ist_hebel_token(b):
            verworfen["hebel_token"] += 1
            continue
        if f.ohne_tokenisierte_aktien and ist_tokenisierte_aktie(b):
            verworfen["tokenisierte_aktie"] += 1
            tokenisiert.append(b)
            continue
        erlaubt.append(s)

    eintraege: list[UniversumEintrag] = []
    for s in erlaubt:
        name = str(s["instrument"]).upper()
        t = kurse.get(name)
        if t is None:
            verworfen["kein_kurs"] += 1
            continue
        kurs = float(t.get("last") or 0.0)
        umsatz = float(t.get("quote_volume") or 0.0)
        trades = int(t.get("trades") or 0)
        pflicht = name in f.immer_dabei
        if not pflicht:
            if kurs < f.min_kurs:
                verworfen["kurs"] += 1
                continue
            if umsatz < f.min_umsatz:
                verworfen["umsatz"] += 1
                continue
            if trades < f.min_trades:
                verworfen["trades"] += 1
                continue
        hoch, tief = float(t.get("high") or 0.0), float(t.get("low") or 0.0)
        spanne = ((hoch - tief) / tief * 100.0) if tief > 0 else 0.0
        eintraege.append(
            UniversumEintrag(
                instrument=name,
                basis=str(s.get("basis", "")).upper(),
                kurs=kurs,
                umsatz_24h=umsatz,
                bewegung_24h_pct=float(t.get("price_change_pct") or 0.0),
                spanne_24h_pct=spanne,
                trades_24h=trades,
            )
        )

    nach_liquiditaet = len(eintraege)
    eintraege.sort(key=lambda e: (e.instrument not in f.immer_dabei, -e.umsatz_24h))
    genommen = eintraege[: f.max_symbole]

    bericht = UniversumBericht(
        gesamt=gesamt,
        nach_quote=len(nach_quote),
        nach_ausschluss=len(erlaubt),
        nach_liquiditaet=nach_liquiditaet,
        genommen=len(genommen),
        verworfen=verworfen,
        tokenisiert=tuple(sorted(tokenisiert)),
    )
    return genommen, bericht


async def hole_universum(
    provider: Any, f: UniversumFilter | None = None
) -> tuple[list[UniversumEintrag], UniversumBericht]:
    """Zwei Aufrufe an die Boerse, dann :func:`bilde_universum`.

    Bewusst zwei Sammelaufrufe statt 400 Einzelabfragen — sonst kostet allein die
    Universumsbildung mehr Zeit als der ganze Scan.
    """
    symbole = await provider.list_symbol_info(quote=(f or UniversumFilter()).quote)
    ticker = await provider.fetch_ticker_24h_all()
    return bilde_universum(symbole, ticker, f)


def nur_namen(eintraege: Sequence[UniversumEintrag]) -> list[str]:
    return [e.instrument for e in eintraege]


__all__ = [
    "DOPPEL",
    "GOLD_BASIS",
    "KRYPTO_AUF_B",
    "STABLECOINS",
    "UniversumBericht",
    "UniversumEintrag",
    "UniversumFilter",
    "bilde_universum",
    "hole_universum",
    "ist_hebel_token",
    "ist_tokenisierte_aktie",
    "nur_namen",
]
