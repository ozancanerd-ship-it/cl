"""Trade-Republic-Depot von Hand — der einzige zulaessige Weg an diese Positionen.

Warum getestet: die Datei wird von Hand gepflegt, also wird sie irgendwann Tippfehler
enthalten. Eine falsch gelesene Stueckzahl verfaelscht das gesamte Portfoliobild —
und damit die Frage, ob noch Platz fuer eine neue Position ist. Der Adapter muss
Unsinn ablehnen statt ihn durchzureichen.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from trading_agent.core.enums import AssetClass, Direction
from trading_agent.data.providers.trade_republic_manual import (
    ManualPosition,
    load_depot,
    missing_prices,
    to_account,
)


def _write(tmp_path: Path, data: dict) -> Path:
    p = tmp_path / "tr.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


GUT = {
    "as_of": "2026-09-04",
    "cash_eur": 120.5,
    "positions": [
        {"symbol": "nvda", "quantity": 0.5, "avg_price_eur": 210.0, "isin": "US67066G1040"},
        {"symbol": "MSFT", "quantity": 0.2, "avg_price_eur": 400.0},
    ],
}


def test_missing_file_is_not_an_error(tmp_path: Path) -> None:
    """Wer kein Depot hat, soll keinen Fehler sehen."""
    assert load_depot(tmp_path / "gibtsnicht.json") is None


def test_reads_and_normalises(tmp_path: Path) -> None:
    d = load_depot(_write(tmp_path, GUT))
    assert d is not None
    assert d.cash_eur == 120.5
    assert [q.symbol for q in d.positions] == ["NVDA", "MSFT"]  # klein geschrieben -> gross
    assert d.positions[1].isin is None


@pytest.mark.parametrize(
    ("kaputt", "erwartet"),
    [
        ({"symbol": "NVDA", "quantity": 0, "avg_price_eur": 210.0}, "quantity"),
        ({"symbol": "NVDA", "quantity": -1, "avg_price_eur": 210.0}, "quantity"),
        ({"symbol": "NVDA", "quantity": 1, "avg_price_eur": 0}, "avg_price_eur"),
        ({"symbol": "", "quantity": 1, "avg_price_eur": 1}, "symbol"),
        ({"symbol": "NVDA", "quantity": 1, "avg_price_eur": 1, "isin": "ZUKURZ"}, "ISIN"),
    ],
)
def test_rejects_nonsense(tmp_path: Path, kaputt: dict, erwartet: str) -> None:
    with pytest.raises(ValueError, match=erwartet):
        load_depot(_write(tmp_path, {**GUT, "positions": [kaputt]}))


def test_rejects_broken_date(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="kein Datum"):
        load_depot(_write(tmp_path, {**GUT, "as_of": "gestern"}))


def test_account_uses_live_prices_not_the_file(tmp_path: Path) -> None:
    """Der Depotwert darf nicht so alt sein wie der letzte Tippfehler."""
    d = load_depot(_write(tmp_path, GUT))
    assert d is not None
    acc = to_account(d, {"NVDA": 240.0, "MSFT": 500.0})
    assert acc.currency == "EUR"
    assert acc.cash == 120.5
    nvda = next(h for h in acc.holdings if h.instrument == "NVDA")
    assert nvda.mark_price == 240.0
    assert nvda.asset_class is AssetClass.EQUITY
    assert nvda.direction is Direction.LONG
    assert nvda.unrealized_pnl == pytest.approx(0.5 * (240.0 - 210.0))
    assert acc.equity == pytest.approx(120.5 + 0.5 * 240.0 + 0.2 * 500.0)


def test_missing_price_falls_back_to_cost_and_is_reported(tmp_path: Path) -> None:
    """Lieber 0 % Ergebnis als ein erfundener Kurs — aber es muss auffallen."""
    d = load_depot(_write(tmp_path, GUT))
    assert d is not None
    prices = {"NVDA": 240.0}
    acc = to_account(d, prices)
    msft = next(h for h in acc.holdings if h.instrument == "MSFT")
    assert msft.mark_price == msft.avg_entry_price
    assert msft.unrealized_pnl == 0.0
    assert missing_prices(d, prices) == ["MSFT"]


def test_stale_days_counts_from_as_of(tmp_path: Path) -> None:
    d = load_depot(_write(tmp_path, {**GUT, "as_of": "2026-08-25"}))
    assert d is not None
    assert d.stale_days == (datetime.now(UTC) - datetime(2026, 8, 25, tzinfo=UTC)).days


def test_position_validate_collects_every_problem() -> None:
    errs = ManualPosition(symbol="", quantity=-1, avg_price_eur=0, isin="X").validate()
    assert len(errs) == 4  # alle vier auf einmal, nicht nur der erste
