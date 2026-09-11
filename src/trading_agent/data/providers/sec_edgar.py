"""SEC EDGAR XBRL — Fundamentaldaten für US-Einzelaktien, direkt von der Quelle.

WARUM AUSGERECHNET DIE SEC

``analysis/fundamentals.py`` und ``investment/stock_analysis.py`` stehen seit Wochen
fertig da und bekommen nichts zu essen: es fehlt ein **Provider**. Die naheliegende
Quelle wäre Yahoos ``quoteSummary`` gewesen — die antwortet inzwischen mit 401 und ist
ausdrücklich gegen automatisierten Zugriff geschützt. Daran vorbeizubauen wäre
technisch möglich und trotzdem falsch, also wird es nicht gemacht.

Die SEC stellt dieselben Zahlen selbst bereit, kostenlos, ohne Schlüssel, ausdrücklich
zur maschinellen Nutzung. Es sind die **Originalzahlen aus den Einreichungen**, nicht
die aufbereiteten Kennzahlen eines Datenhändlers. Das ist ein Vorteil und ein Nachteil
zugleich: nichts ist geschönt, aber man muss die Kennzahlen selbst ausrechnen und mit
der Tatsache leben, dass verschiedene Unternehmen für dieselbe Sache verschiedene
XBRL-Etiketten benutzen. Deshalb steht hinter jeder Größe unten eine *Liste* möglicher
Etiketten, in der Reihenfolge, in der sie versucht werden.

PUNKT-IN-DER-ZEIT, UND ZWAR ERNST GEMEINT

Jede Tatsache in EDGAR trägt ein ``filed``-Datum — den Tag, an dem sie öffentlich
wurde. Der Bericht für das erste Quartal existiert nicht am 31. März, sondern erst
Anfang Mai. Wer die Zahl vorher benutzt, hat einen Backtest gebaut, der in der Zukunft
liest, und wird es nie merken, weil das Ergebnis besser aussieht statt schlechter.

Deshalb filtert ``_neueste`` **immer** auf ``filed <= as_of``, und es gibt keinen
Schalter, der das abstellt. Fehlt eine Kennzahl zum Stichtag, bleibt sie ``None`` —
``FundamentalContext`` kommt damit zurecht und zieht sie einfach nicht heran.

WAS DIESER PROVIDER NICHT KANN

* **Nur US-Filer.** Wer nicht bei der SEC einreicht, taucht nicht auf. Für ein
  deutsches oder asiatisches Papier gibt es hier schlicht nichts — und dann sagt der
  Provider das, statt etwas zu erfinden.
* **Keine Kurse.** Kurs-Gewinn-Verhältnis und Kurs-Umsatz-Verhältnis brauchen einen
  Kurs; der kommt von der Marktdatenseite und wird beim Aufruf hereingereicht.
* **Keine Schätzungen, keine Analystenerwartungen.** EDGAR enthält Vergangenheit.

RATE LIMIT UND HÖFLICHKEIT

Die SEC verlangt einen ``User-Agent`` mit echter Kontaktmöglichkeit und höchstens zehn
Anfragen je Sekunde. Beides wird hier eingehalten — der Standard liegt bei fünf, weil
wir es nicht eilig haben und eine Sperre teurer wäre als die gesparte Minute.

``companyfacts`` liefert **alle** Kennzahlen eines Unternehmens in *einer* Antwort.
Darum wird genau dieser Endpunkt benutzt und nicht ``frames``: für vierzig Werte sind
das vierzig Abrufe statt mehrerer hundert, und die Antwort lässt sich einen Tag lang
aufheben — Fundamentaldaten ändern sich quartalsweise, nicht halbstündlich.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from trading_agent.analysis.fundamentals import StockFundamentals
from trading_agent.net.client import HttpClient, NetError

#: Ohne Kontakt im User-Agent sperrt die SEC den Zugriff — das steht so in ihren
#: Zugangsregeln. Der Wert ist überschreibbar; der Standard nennt das Projekt.
STANDARD_AGENT = "AI-Trading-Agent/1.0 (Kontakt ueber GitHub: ozancanerd-ship-it/cl)"

_TICKER_URL = "https://www.sec.gov/files/company_tickers.json"
_FACTS_BASIS = "https://data.sec.gov"

#: So lange gilt eine zwischengespeicherte Antwort als frisch.
TICKER_HALTBAR = timedelta(days=7)
FACTS_HALTBAR = timedelta(hours=20)

#: Nur diese Formulare zählen. Pressemitteilungen (8-K) enthalten oft dieselben Zahlen,
#: aber ungeprüft und in wechselnder Abgrenzung — die Vergleichbarkeit über Quartale
#: hinweg ist wichtiger als ein paar Tage Vorsprung.
ERLAUBTE_FORMULARE = frozenset({"10-K", "10-Q", "20-F", "40-F", "10-K/A", "10-Q/A"})


class SecNichtVerfuegbar(RuntimeError):
    """Die Quelle konnte nicht gelesen werden. **Kein Rückfall auf erfundene Zahlen.**"""


# --------------------------------------------------------------------- Etiketten

#: Größen, die über einen Zeitraum laufen (Umsatz, Gewinn, Cashflow).
#: Mehrere Etiketten je Größe, weil Unternehmen unterschiedlich bilanzieren: Apple
#: meldet Umsatz als ``RevenueFromContractWithCustomerExcludingAssessedTax``, ältere
#: Einreichungen benutzen schlicht ``Revenues``. Reihenfolge = Vorrang.
FLUSS_ETIKETTEN: dict[str, tuple[str, ...]] = {
    "umsatz": (
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "Revenues",
        "RevenueFromContractWithCustomerIncludingAssessedTax",
        "SalesRevenueNet",
    ),
    "rohertrag": ("GrossProfit",),
    "betriebsergebnis": ("OperatingIncomeLoss",),
    "nettogewinn": ("NetIncomeLoss", "ProfitLoss"),
    "operativer_cashflow": ("NetCashProvidedByUsedInOperatingActivities",),
    "investitionen": (
        "PaymentsToAcquirePropertyPlantAndEquipment",
        "PaymentsToAcquireProductiveAssets",
    ),
    "abschreibungen": (
        "DepreciationDepletionAndAmortization",
        "DepreciationAmortizationAndAccretionNet",
        "DepreciationAndAmortization",
    ),
    "zinsaufwand": ("InterestExpense", "InterestExpenseDebt", "InterestIncomeExpenseNet"),
}

#: Größen, die zu einem Stichtag gelten (Bilanzposten).
BESTAND_ETIKETTEN: dict[str, tuple[str, ...]] = {
    "eigenkapital": (
        "StockholdersEquity",
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
    ),
    "umlaufvermoegen": ("AssetsCurrent",),
    "kurzfristige_schulden": ("LiabilitiesCurrent",),
    "zahlungsmittel": (
        "CashAndCashEquivalentsAtCarryingValue",
        "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
    ),
    "langfristige_schulden": (
        "LongTermDebtNoncurrent",
        "LongTermDebt",
    ),
}

#: Ergebnis je Aktie steht in einer eigenen Einheit und braucht deshalb einen eigenen Weg.
EPS_ETIKETTEN: tuple[str, ...] = ("EarningsPerShareDiluted", "EarningsPerShareBasicAndDiluted")


@dataclass(frozen=True, slots=True)
class Tatsache:
    """Eine Zahl aus einer Einreichung, mit allem, was für Punkt-in-der-Zeit nötig ist."""

    wert: float
    start: date | None
    ende: date
    eingereicht: date
    formular: str
    #: Länge des Zeitraums in Tagen. ``None`` bei Bilanzposten.
    tage: int | None = None

    @property
    def quartal(self) -> bool:
        return self.tage is not None and 60 <= self.tage <= 115

    @property
    def jahr(self) -> bool:
        return self.tage is not None and 330 <= self.tage <= 400


def _als_datum(x: Any) -> date | None:
    if not x:
        return None
    try:
        return date.fromisoformat(str(x)[:10])
    except ValueError:
        return None


def _tatsachen(facts: dict[str, Any], taxonomie: str, etikett: str, einheit: str) -> list[Tatsache]:
    """Alle Einzelwerte zu einem Etikett, unsortiert."""
    zweig = ((facts.get("facts") or {}).get(taxonomie) or {}).get(etikett)
    if not zweig:
        return []
    reihen = (zweig.get("units") or {}).get(einheit) or []
    aus: list[Tatsache] = []
    for r in reihen:
        ende = _als_datum(r.get("end"))
        eingereicht = _als_datum(r.get("filed"))
        if ende is None or eingereicht is None:
            continue
        form = str(r.get("form") or "")
        if form not in ERLAUBTE_FORMULARE:
            continue
        try:
            wert = float(r.get("val"))
        except (TypeError, ValueError):
            continue
        start = _als_datum(r.get("start"))
        tage = (ende - start).days if start else None
        aus.append(Tatsache(wert, start, ende, eingereicht, form, tage))
    return aus


def _sichtbar(reihen: Iterable[Tatsache], as_of: date) -> list[Tatsache]:
    """Nur, was am Stichtag schon eingereicht war. Die Stelle, an der Look-ahead stirbt."""
    return [t for t in reihen if t.eingereicht <= as_of]


def _neueste(
    facts: dict[str, Any],
    etiketten: Sequence[str],
    einheit: str,
    as_of: date,
    *,
    art: str = "bestand",
) -> Tatsache | None:
    """Der jüngste sichtbare Wert für die erste Etikettenvariante, die etwas liefert.

    ``art`` steuert, was als brauchbar gilt: ``bestand`` nimmt jeden Stichtagswert,
    ``quartal`` nur Zeiträume von rund drei Monaten, ``jahr`` nur Geschäftsjahre. Ohne
    diese Unterscheidung mischt man Quartals- und Jahreswerte in derselben Kennzahl —
    eine Marge wäre dann richtig gerechnet und trotzdem falsch.
    """
    for etikett in etiketten:
        reihen = _sichtbar(_tatsachen(facts, "us-gaap", etikett, einheit), as_of)
        if art == "quartal":
            reihen = [t for t in reihen if t.quartal]
        elif art == "jahr":
            reihen = [t for t in reihen if t.jahr]
        if not reihen:
            continue
        # Sortierschlüssel: erst das Periodenende, dann das Einreichungsdatum. Bei
        # Korrekturen (10-Q/A) steht damit die spätere Fassung vorn.
        reihen.sort(key=lambda t: (t.ende, t.eingereicht))
        return reihen[-1]
    return None


def _letzte_vier_quartale(
    facts: dict[str, Any], etiketten: Sequence[str], einheit: str, as_of: date
) -> tuple[float, date] | None:
    """Summe der jüngsten vier Quartale plus deren Endedatum — die übliche TTM-Größe.

    Wenn vier saubere Quartale nicht zusammenkommen, wird **nicht** hochgerechnet.
    Ein aus zwei Quartalen verdoppelter Jahresumsatz ist eine Erfindung mit Nachkommastelle.
    """
    for etikett in etiketten:
        reihen = [
            t for t in _sichtbar(_tatsachen(facts, "us-gaap", etikett, einheit), as_of) if t.quartal
        ]
        if not reihen:
            continue
        # Je Periodenende nur den zuletzt eingereichten Wert behalten (Korrekturen).
        je_ende: dict[date, Tatsache] = {}
        for t in sorted(reihen, key=lambda x: x.eingereicht):
            je_ende[t.ende] = t
        sortiert = sorted(je_ende.values(), key=lambda t: t.ende)
        if len(sortiert) < 4:
            continue
        vier = sortiert[-4:]
        # Die vier müssen zusammenhängen, sonst summiert man über eine Lücke hinweg.
        spanne = (vier[-1].ende - vier[0].ende).days
        if not 240 <= spanne <= 400:
            continue
        return sum(t.wert for t in vier), vier[-1].ende
    return None


def _quartal_vorjahr(
    facts: dict[str, Any], etiketten: Sequence[str], einheit: str, as_of: date
) -> tuple[Tatsache, Tatsache] | None:
    """Das jüngste Quartal und dasselbe Quartal ein Jahr davor — für das Wachstum.

    Bewusst Quartal gegen Vorjahresquartal und nicht gegen das Vorquartal: fast jedes
    Geschäft ist saisonal, und ein Weihnachtsquartal gegen ein Frühjahrsquartal zu
    stellen misst die Jahreszeit, nicht das Unternehmen.
    """
    for etikett in etiketten:
        reihen = [
            t for t in _sichtbar(_tatsachen(facts, "us-gaap", etikett, einheit), as_of) if t.quartal
        ]
        if not reihen:
            continue
        je_ende: dict[date, Tatsache] = {}
        for t in sorted(reihen, key=lambda x: x.eingereicht):
            je_ende[t.ende] = t
        sortiert = sorted(je_ende.values(), key=lambda t: t.ende)
        if not sortiert:
            continue
        jung = sortiert[-1]
        ziel = jung.ende - timedelta(days=365)
        kandidaten = [t for t in sortiert[:-1] if abs((t.ende - ziel).days) <= 45]
        if not kandidaten:
            continue
        kandidaten.sort(key=lambda t: abs((t.ende - ziel).days))
        return jung, kandidaten[0]
    return None


def _teile(zaehler: float | None, nenner: float | None) -> float | None:
    if zaehler is None or nenner is None or nenner == 0:
        return None
    return zaehler / nenner


def kennzahlen_aus_facts(
    symbol: str,
    facts: dict[str, Any],
    as_of: datetime,
    *,
    kurs: float | None = None,
) -> StockFundamentals | None:
    """Aus einer ``companyfacts``-Antwort die Kennzahlen rechnen, die wir bewerten.

    ``kurs`` ist optional und kommt von der Marktdatenseite — EDGAR kennt keine Kurse.
    Ohne ihn bleiben Kurs-Gewinn- und Kurs-Umsatz-Verhältnis ``None``, der Rest steht
    trotzdem zur Verfügung.
    """
    stichtag = as_of.date()

    umsatz_ttm = _letzte_vier_quartale(facts, FLUSS_ETIKETTEN["umsatz"], "USD", stichtag)
    gewinn_ttm = _letzte_vier_quartale(facts, FLUSS_ETIKETTEN["nettogewinn"], "USD", stichtag)
    rohertrag_ttm = _letzte_vier_quartale(facts, FLUSS_ETIKETTEN["rohertrag"], "USD", stichtag)
    ebit_ttm = _letzte_vier_quartale(facts, FLUSS_ETIKETTEN["betriebsergebnis"], "USD", stichtag)
    ocf_ttm = _letzte_vier_quartale(facts, FLUSS_ETIKETTEN["operativer_cashflow"], "USD", stichtag)
    capex_ttm = _letzte_vier_quartale(facts, FLUSS_ETIKETTEN["investitionen"], "USD", stichtag)
    afa_ttm = _letzte_vier_quartale(facts, FLUSS_ETIKETTEN["abschreibungen"], "USD", stichtag)
    zins_ttm = _letzte_vier_quartale(facts, FLUSS_ETIKETTEN["zinsaufwand"], "USD", stichtag)
    eps_ttm = _letzte_vier_quartale(facts, EPS_ETIKETTEN, "USD/shares", stichtag)

    eigenkapital = _neueste(facts, BESTAND_ETIKETTEN["eigenkapital"], "USD", stichtag)
    umlauf = _neueste(facts, BESTAND_ETIKETTEN["umlaufvermoegen"], "USD", stichtag)
    kurzfristig = _neueste(facts, BESTAND_ETIKETTEN["kurzfristige_schulden"], "USD", stichtag)
    cash = _neueste(facts, BESTAND_ETIKETTEN["zahlungsmittel"], "USD", stichtag)
    schulden = _neueste(facts, BESTAND_ETIKETTEN["langfristige_schulden"], "USD", stichtag)

    # Ohne Umsatz und ohne Gewinn ist nichts zu bewerten — dann lieber gar nichts liefern
    # als einen Kontext, der aus zwei Bilanzposten besteht.
    if umsatz_ttm is None and gewinn_ttm is None:
        return None

    umsatz = umsatz_ttm[0] if umsatz_ttm else None
    gewinn = gewinn_ttm[0] if gewinn_ttm else None

    ebitda = None
    if ebit_ttm and afa_ttm:
        ebitda = ebit_ttm[0] + afa_ttm[0]

    netto_schulden = None
    if schulden is not None:
        netto_schulden = schulden.wert - (cash.wert if cash is not None else 0.0)

    fcf = None
    if ocf_ttm and capex_ttm:
        fcf = ocf_ttm[0] - abs(capex_ttm[0])

    wachstum_umsatz = None
    paar = _quartal_vorjahr(facts, FLUSS_ETIKETTEN["umsatz"], "USD", stichtag)
    if paar and paar[1].wert:
        wachstum_umsatz = paar[0].wert / paar[1].wert - 1.0

    wachstum_eps = None
    paar_eps = _quartal_vorjahr(facts, EPS_ETIKETTEN, "USD/shares", stichtag)
    # Nur bei positivem Vorjahreswert. Bei negativem ist eine prozentuale Veränderung
    # nicht deutbar: von -1 auf +1 sind es weder +200 % noch -200 %, sondern eine Wende,
    # für die es keine sinnvolle Prozentzahl gibt. Dann lieber keine Zahl.
    if paar_eps and paar_eps[1].wert > 0:
        wachstum_eps = paar_eps[0].wert / paar_eps[1].wert - 1.0

    # Das jüngste verwertete Periodenende ist der ehrliche Stand der Daten — nicht heute.
    enden = [x[1] for x in (umsatz_ttm, gewinn_ttm, eps_ttm) if x]
    enden += [t.ende for t in (eigenkapital, umlauf) if t is not None]
    stand = max(enden) if enden else stichtag

    return StockFundamentals(
        symbol=symbol,
        as_of_report=datetime.combine(stand, datetime.min.time(), tzinfo=UTC),
        pe=_teile(kurs, eps_ttm[0]) if (kurs and eps_ttm and eps_ttm[0] > 0) else None,
        price_to_sales=None,  # braucht die Aktienzahl; kommt, wenn die Marktkapitalisierung da ist
        ev_ebitda=None,
        peg=None,
        forward_pe=None,
        revenue_growth_yoy=wachstum_umsatz,
        eps_growth_yoy=wachstum_eps,
        gross_margin=_teile(rohertrag_ttm[0] if rohertrag_ttm else None, umsatz),
        operating_margin=_teile(ebit_ttm[0] if ebit_ttm else None, umsatz),
        roe=_teile(gewinn, eigenkapital.wert if eigenkapital else None),
        fcf_margin=_teile(fcf, umsatz),
        net_debt_to_ebitda=_teile(netto_schulden, ebitda),
        current_ratio=_teile(
            umlauf.wert if umlauf else None, kurzfristig.wert if kurzfristig else None
        ),
        interest_coverage=_teile(
            ebit_ttm[0] if ebit_ttm else None,
            abs(zins_ttm[0]) if (zins_ttm and zins_ttm[0]) else None,
        ),
    )


# --------------------------------------------------------------------- Abruf


def _symbol_kern(symbol: str) -> str:
    """``NVDA-YFD`` → ``NVDA``. Unsere Instrumentenschlüssel tragen die Quelle im Namen."""
    return re.split(r"[-.]", symbol.upper(), maxsplit=1)[0]


@dataclass(slots=True)
class SecEdgar:
    """Fundamentaldaten von EDGAR, mit Zwischenspeicher auf der Platte.

    Der Zwischenspeicher ist kein Luxus, sondern der Grund, warum das überhaupt in einem
    halbstündlichen Lauf vertretbar ist: eine ``companyfacts``-Antwort ist mehrere
    hundert Kilobyte groß und ändert sich an rund vier Tagen im Jahr.
    """

    agent: str = STANDARD_AGENT
    cache_dir: Path = field(default_factory=lambda: Path("data/cache/sec_edgar"))
    rate_per_sec: float = 5.0
    transport: Any = None
    #: Wird für Tests gesetzt; sonst „jetzt".
    jetzt: Any = None

    def _now(self) -> datetime:
        return self.jetzt() if callable(self.jetzt) else datetime.now(UTC)

    def _kopf(self) -> dict[str, str]:
        return {"User-Agent": self.agent, "Accept-Encoding": "gzip, deflate"}

    def _aus_cache(self, name: str, haltbar: timedelta) -> Any | None:
        p = self.cache_dir / name
        if not p.is_file():
            return None
        alter = self._now() - datetime.fromtimestamp(p.stat().st_mtime, tz=UTC)
        if alter > haltbar:
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def _in_cache(self, name: str, inhalt: Any) -> None:
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            (self.cache_dir / name).write_text(
                json.dumps(inhalt, separators=(",", ":")), encoding="utf-8"
            )
        except OSError:
            # Ein fehlgeschlagener Zwischenspeicher darf den Abruf nicht kippen —
            # er ist eine Beschleunigung, keine Voraussetzung.
            pass

    async def ticker_verzeichnis(self) -> dict[str, int]:
        """Kürzel → CIK. Eine Datei, rund 10.000 Einträge, eine Woche haltbar."""
        zwischen = self._aus_cache("tickers.json", TICKER_HALTBAR)
        if zwischen is None:
            async with HttpClient(
                base_url="https://www.sec.gov",
                name="sec_tickers",
                rate_per_sec=self.rate_per_sec,
                headers=self._kopf(),
                transport=self.transport,
            ) as c:
                try:
                    zwischen = await c.get_json(_TICKER_URL.replace("https://www.sec.gov", ""))
                except NetError as exc:
                    raise SecNichtVerfuegbar(f"Tickerverzeichnis nicht ladbar: {exc}") from exc
            self._in_cache("tickers.json", zwischen)
        aus: dict[str, int] = {}
        # Die Datei kommt als Objekt mit laufenden Nummern als Schlüsseln, nicht als Liste.
        werte = zwischen.values() if isinstance(zwischen, dict) else zwischen
        for eintrag in werte:
            if not isinstance(eintrag, dict):
                continue
            t, cik = eintrag.get("ticker"), eintrag.get("cik_str")
            if t and cik is not None:
                aus[str(t).upper()] = int(cik)
        return aus

    async def company_facts(self, cik: int) -> dict[str, Any]:
        name = f"facts_{cik:010d}.json"
        zwischen = self._aus_cache(name, FACTS_HALTBAR)
        if zwischen is not None:
            return dict(zwischen)
        async with HttpClient(
            base_url=_FACTS_BASIS,
            name="sec_facts",
            rate_per_sec=self.rate_per_sec,
            headers=self._kopf(),
            transport=self.transport,
        ) as c:
            try:
                daten = await c.get_json(f"/api/xbrl/companyfacts/CIK{cik:010d}.json")
            except NetError as exc:
                raise SecNichtVerfuegbar(f"companyfacts CIK{cik:010d} nicht ladbar: {exc}") from exc
        self._in_cache(name, daten)
        return dict(daten)

    async def kennzahlen(
        self,
        symbole: Sequence[str],
        *,
        as_of: datetime | None = None,
        kurse: dict[str, float] | None = None,
    ) -> dict[str, StockFundamentals]:
        """Für jedes Kürzel die Kennzahlen — oder das Kürzel fehlt im Ergebnis.

        Ein Wert, für den EDGAR nichts hergibt (kein US-Filer, frisch an der Börse,
        ungewöhnliche Bilanzierung), taucht schlicht **nicht auf**. Das ist der ehrliche
        Zustand; ein leerer ``StockFundamentals`` würde so aussehen, als hätte man
        nachgesehen und nichts gefunden, statt gar nicht nachsehen zu können.
        """
        stichtag = as_of or self._now()
        kurse = kurse or {}
        verzeichnis = await self.ticker_verzeichnis()
        aus: dict[str, StockFundamentals] = {}
        for symbol in symbole:
            kern = _symbol_kern(symbol)
            cik = verzeichnis.get(kern)
            if cik is None:
                continue
            try:
                facts = await self.company_facts(cik)
            except SecNichtVerfuegbar:
                continue
            k = kennzahlen_aus_facts(
                symbol, facts, stichtag, kurs=kurse.get(symbol) or kurse.get(kern)
            )
            if k is not None:
                aus[symbol] = k
        return aus


__all__ = [
    "BESTAND_ETIKETTEN",
    "EPS_ETIKETTEN",
    "ERLAUBTE_FORMULARE",
    "FLUSS_ETIKETTEN",
    "STANDARD_AGENT",
    "SecEdgar",
    "SecNichtVerfuegbar",
    "Tatsache",
    "kennzahlen_aus_facts",
]
