"""portfolio_intel.account_mapping — Broker-Rohdaten → AccountPortfolio (Masterplan §33/§34)."""

from __future__ import annotations

from datetime import UTC, datetime

from trading_agent.core.enums import AssetClass, Direction
from trading_agent.portfolio_intel import PortfolioHub
from trading_agent.portfolio_intel.account_mapping import (
    map_derivatives_account,
    map_spot_account,
)

_NOW = datetime(2026, 8, 31, tzinfo=UTC)


def test_spot_account_kraken_style() -> None:
    ap = map_spot_account(
        account="kraken",
        as_of=_NOW,
        balances={"XXBT": 0.5, "XETH": 4.0, "ZEUR": 1200.0, "PAXG": 2.0, "DUST": 0.00001},
        prices={"BTCEUR": 60000.0, "ETHEUR": 3000.0, "PAXGEUR": 4000.0},
        quote_ccy="EUR",
    )
    assert ap.cash == 1200.0
    by = {h.instrument: h for h in ap.holdings}
    assert by["BTCEUR"].quantity == 0.5 and by["BTCEUR"].market_value == 30000.0
    assert by["BTCEUR"].unrealized_pnl == 0.0  # Einstand = Mark
    assert by["PAXGEUR"].asset_class is AssetClass.GOLD
    assert "DUSTEUR" not in by  # kein Preis → ausgelassen


def test_spot_account_binance_style_usdt() -> None:
    ap = map_spot_account(
        account="binance",
        as_of=_NOW,
        balances={"BTC": 0.1, "USDT": 500.0, "SOL": 10.0},
        prices={"BTCUSDT": 62000.0, "SOLUSDT": 150.0},
    )
    assert ap.cash == 500.0
    assert ap.positions_value == 0.1 * 62000.0 + 10.0 * 150.0
    assert all(h.direction is Direction.LONG for h in ap.holdings)


def test_derivatives_account_bybit_positions() -> None:
    positions = [
        {
            "symbol": "BTCUSDT",
            "side": "Buy",
            "size": "0.2",
            "avgPrice": "60000",
            "markPrice": "63000",
            "stopLoss": "58000",
            "createdTime": "1735689600000",
        },
        {"symbol": "ETHUSDT", "side": "Sell", "size": "3", "avgPrice": "3200", "markPrice": "3100"},
        {"symbol": "XRPUSDT", "side": "Buy", "size": "0"},  # geschlossen → raus
    ]
    ap = map_derivatives_account(account="bybit", as_of=_NOW, equity=20000.0, positions=positions)
    assert len(ap.holdings) == 2
    btc = next(h for h in ap.holdings if h.instrument == "BTCUSDT")
    assert btc.direction is Direction.LONG and btc.quantity == 0.2
    assert btc.unrealized_pnl == 0.2 * (63000.0 - 60000.0)
    assert btc.unrealized_r == (63000.0 - 60000.0) / (60000.0 - 58000.0)
    eth = next(h for h in ap.holdings if h.instrument == "ETHUSDT")
    assert eth.direction is Direction.SHORT
    assert eth.unrealized_pnl == 3 * (3200.0 - 3100.0)  # short im Plus


def test_hub_consolidates_mapped_accounts() -> None:
    spot = map_spot_account(
        account="binance",
        as_of=_NOW,
        balances={"BTC": 0.1, "USDT": 1000.0},
        prices={"BTCUSDT": 60000.0},
    )
    deriv = map_derivatives_account(
        account="bybit",
        as_of=_NOW,
        equity=5000.0,
        positions=[
            {
                "symbol": "BTCUSDT",
                "side": "Buy",
                "size": "0.05",
                "avgPrice": "59000",
                "markPrice": "60000",
            }
        ],
    )
    cp = PortfolioHub().consolidate([spot, deriv], as_of=_NOW)
    btc = cp.holding("BTCUSDT")
    assert btc is not None and abs(btc.quantity - 0.15) < 1e-9  # 0.1 spot + 0.05 perp, beide long
    assert abs(cp.equity - (spot.equity + deriv.equity)) < 1e-6
