"""Trade-Republic-Depot von Hand einlesen — es gibt keinen anderen zulaessigen Weg.

Trade Republic bietet **keine offizielle Schnittstelle fuer Privatkunden**
(``docs/TRADE-REPUBLIC-ANBINDUNG.md``). Die kursierenden Bibliotheken sprechen die
private App-API an: das verstoesst gegen die Nutzungsbedingungen und riskiert im
schlimmsten Fall die Sperrung eines Depots mit echtem Geld. Deshalb dieser Adapter.

Er liest eine JSON-Datei, die der Nutzer pflegt, und liefert dieselbe Struktur wie die
Boersen-Adapter. Wenn Trade Republic irgendwann eine offizielle API anbietet, wird nur
dieser Adapter getauscht — der Rest des Systems merkt nichts davon.

Format von ``config/holdings_trade_republic.json``:

    {
      "as_of": "2026-09-04",
      "cash_eur": 120.50,
      "positions": [
        {"symbol": "NVDA", "quantity": 0.5, "avg_price_eur": 210.0, "isin": "US67066G1040"}
      ]
    }

``mark_price`` kommt nicht aus der Datei, sondern vom Kursanbieter — sonst waere der
Depotwert nur so aktuell wie der letzte Tippfehler. Fehlt ein Kurs, gilt der
Einstandskurs, und die Position wird als solche markiert.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from trading_agent.core.enums import AssetClass, Direction
from trading_agent.portfolio_intel.models import AccountPortfolio, Holding

DEFAULT_PATH = "config/holdings_trade_republic.json"


@dataclass(frozen=True, slots=True)
class ManualPosition:
    symbol: str
    quantity: float
    avg_price_eur: float
    isin: str | None = None

    def validate(self) -> list[str]:
        errs: list[str] = []
        if not self.symbol.strip():
            errs.append("symbol fehlt")
        if self.quantity <= 0:
            errs.append(f"{self.symbol}: quantity muss > 0 sein (ist {self.quantity})")
        if self.avg_price_eur <= 0:
            errs.append(f"{self.symbol}: avg_price_eur muss > 0 sein (ist {self.avg_price_eur})")
        if self.isin is not None and len(self.isin) != 12:
            errs.append(f"{self.symbol}: ISIN hat {len(self.isin)} statt 12 Zeichen")
        return errs


@dataclass(frozen=True, slots=True)
class ManualDepot:
    as_of: datetime
    cash_eur: float
    positions: tuple[ManualPosition, ...]

    @property
    def stale_days(self) -> int:
        return (datetime.now(UTC) - self.as_of).days


def load_depot(path: str | Path = DEFAULT_PATH) -> ManualDepot | None:
    """Datei lesen. ``None``, wenn sie nicht existiert — das ist kein Fehler."""
    p = Path(path)
    if not p.exists():
        return None
    raw = json.loads(p.read_text(encoding="utf-8"))

    as_of_s = str(raw.get("as_of", ""))
    try:
        as_of = datetime.fromisoformat(as_of_s).replace(tzinfo=UTC)
    except ValueError as exc:
        raise ValueError(f"as_of ist kein Datum: {as_of_s!r} (erwartet JJJJ-MM-TT)") from exc

    positions = tuple(
        ManualPosition(
            symbol=str(q.get("symbol", "")).strip().upper(),
            quantity=float(q.get("quantity", 0)),
            avg_price_eur=float(q.get("avg_price_eur", 0)),
            isin=(str(q["isin"]).strip().upper() if q.get("isin") else None),
        )
        for q in raw.get("positions", [])
    )
    errs = [e for q in positions for e in q.validate()]
    if errs:
        raise ValueError("Depotdatei fehlerhaft:\n  " + "\n  ".join(errs))

    return ManualDepot(as_of=as_of, cash_eur=float(raw.get("cash_eur", 0.0)), positions=positions)


def to_account(
    depot: ManualDepot,
    prices_eur: dict[str, float] | None = None,
    *,
    account: str = "trade_republic",
) -> AccountPortfolio:
    """In die gemeinsame Portfolio-Struktur uebersetzen.

    ``prices_eur`` sind aktuelle Kurse je Symbol. Fehlt einer, wird der Einstandskurs
    benutzt — die Position erscheint dann mit 0 % Ergebnis, was ehrlicher ist als ein
    erfundener Kurs, aber beim Lesen auffallen muss.
    """
    px = prices_eur or {}
    holdings = tuple(
        Holding(
            instrument=q.symbol,
            asset_class=AssetClass.EQUITY,
            account=account,
            direction=Direction.LONG,
            quantity=q.quantity,
            avg_entry_price=q.avg_price_eur,
            mark_price=float(px.get(q.symbol, q.avg_price_eur)),
        )
        for q in depot.positions
    )
    return AccountPortfolio(
        account=account,
        as_of=depot.as_of,
        cash=depot.cash_eur,
        holdings=holdings,
        currency="EUR",
        # Von Hand gepflegt: es gibt keinen Schluessel, der etwas ausloesen koennte.
        read_only_verified=True,
    )


def missing_prices(depot: ManualDepot, prices_eur: dict[str, float]) -> list[str]:
    """Symbole ohne aktuellen Kurs — der Aufrufer soll das sichtbar machen."""
    return [q.symbol for q in depot.positions if q.symbol not in prices_eur]


__all__ = [
    "DEFAULT_PATH",
    "ManualDepot",
    "ManualPosition",
    "load_depot",
    "missing_prices",
    "to_account",
]
