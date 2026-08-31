"""Broker-Rohdaten → ``AccountPortfolio`` (Masterplan §33/§34).

Reine Transformation. Die **read-only** Account-Adapter (Kraken/Bybit/Binance) liefern Balances +
Positionen; hier werden sie in das broker-agnostische ``AccountPortfolio``-Modell übersetzt, das
``PortfolioHub`` konsolidiert.

* **Spot-Guthaben** (z. B. Kraken-``Balance``, Binance-``/api/v3/account``): jedes Nicht-Quote-
  Asset = ein LONG-``Holding``. Einstandspreis unbekannt → ``avg_entry_price = mark_price``
  (PnL 0), bis ein Trade-Journal echte Einstände liefert. Mark-Preis kommt vom Aufrufer
  (``prices``); fehlt er, wird das Asset **ausgelassen** (nichts erfunden).
* **Perp-Positionen** (Bybit ``/v5/position/list``, Kraken ``OpenPositions``): tragen Größe,
  Richtung und echten Einstand → vollständiges ``Holding``.

Keine Order, kein Schreiben, kein Netzwerk hier.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from trading_agent.core.enums import AssetClass, Direction
from trading_agent.portfolio_intel.models import AccountPortfolio, Holding

# Kraken nutzt X-/Z-Präfixe + Alt-Codes; hier die geläufigsten auf kanonische Symbole.
_KRAKEN_ASSET: dict[str, str] = {
    "XXBT": "BTC",
    "XBT": "BTC",
    "XETH": "ETH",
    "XXRP": "XRP",
    "XLTC": "LTC",
    "XXDG": "DOGE",
    "XDG": "DOGE",
    "SOL": "SOL",
    "ZEUR": "EUR",
    "ZUSD": "USD",
    "USDT": "USDT",
    "USDC": "USDC",
    "PAXG": "PAXG",
}
_QUOTE = frozenset({"EUR", "USD", "USDT", "USDC", "ZUSD", "ZEUR", "BUSD"})

_ASSET_CLASS: dict[str, AssetClass] = {
    "PAXG": AssetClass.GOLD,
    "XAUT": AssetClass.GOLD,
    "XAUUSDT": AssetClass.GOLD,
}


def _canon(asset: str) -> str:
    return _KRAKEN_ASSET.get(asset, asset).upper()


def _cls(instrument: str) -> AssetClass:
    base = instrument.replace("USDT", "").replace("USD", "").replace("EUR", "")
    if instrument in _ASSET_CLASS or base in ("PAXG", "XAUT", "XAU"):
        return AssetClass.GOLD
    return AssetClass.CRYPTO


def _spot_holdings(
    balances: dict[str, float], *, account: str, prices: dict[str, float], quote_ccy: str
) -> tuple[list[Holding], float]:
    holdings: list[Holding] = []
    cash = 0.0
    for raw_asset, amount in balances.items():
        asset = _canon(raw_asset)
        if amount <= 0:
            continue
        if asset in _QUOTE:
            if asset in (quote_ccy, f"Z{quote_ccy}"):
                cash += amount
            continue
        pair = f"{asset}{quote_ccy}"
        mark = prices.get(pair) or prices.get(f"{asset}USDT") or prices.get(asset)
        if mark is None or mark <= 0:
            continue  # kein Mark-Preis → nichts erfinden
        holdings.append(
            Holding(
                instrument=pair,
                asset_class=_cls(pair),
                account=account,
                direction=Direction.LONG,
                quantity=amount,
                avg_entry_price=mark,  # Einstand unbekannt → PnL 0
                mark_price=mark,
            )
        )
    return holdings, cash


def _perp_holding(p: dict[str, Any], *, account: str) -> Holding | None:
    """Bybit-/Kraken-Perp-Position-Dict → ``Holding``. Robuste getattr-artige Feldzugriffe."""
    inst = str(p.get("symbol") or p.get("pair") or p.get("instrument") or "").upper()
    size = abs(float(p.get("size") or p.get("vol") or p.get("qty") or 0.0))
    if not inst or size <= 0:
        return None
    side = str(p.get("side") or p.get("type") or "").lower()
    direction = Direction.SHORT if side in ("sell", "short") else Direction.LONG
    entry = float(p.get("avgPrice") or p.get("entryPrice") or p.get("cost") or 0.0)
    mark = float(p.get("markPrice") or p.get("value") or entry or 0.0)
    if entry <= 0 or mark <= 0:
        return None
    opened_raw = p.get("createdTime") or p.get("opentm")
    opened: datetime | None = None
    if opened_raw:
        with_ms = float(opened_raw)
        opened = datetime.fromtimestamp(with_ms / 1000.0 if with_ms > 1e11 else with_ms, tz=None)
    sl = p.get("stopLoss")
    return Holding(
        instrument=inst,
        asset_class=_cls(inst),
        account=account,
        direction=direction,
        quantity=size,
        avg_entry_price=entry,
        mark_price=mark,
        opened_at=opened,
        stop_ref=float(sl) if sl not in (None, "", "0") else None,
    )


def map_spot_account(
    *,
    account: str,
    as_of: datetime,
    balances: dict[str, float],
    prices: dict[str, float],
    quote_ccy: str = "USDT",
    read_only_verified: bool = False,
) -> AccountPortfolio:
    """Kraken-``Balance`` / Binance-``nonzero_balances`` → ``AccountPortfolio`` (nur Spot)."""
    holdings, cash = _spot_holdings(balances, account=account, prices=prices, quote_ccy=quote_ccy)
    return AccountPortfolio(
        account=account,
        as_of=as_of,
        cash=round(cash, 8),
        holdings=tuple(holdings),
        currency=quote_ccy,
        read_only_verified=read_only_verified,
    )


def map_derivatives_account(
    *,
    account: str,
    as_of: datetime,
    equity: float | None,
    positions: list[dict[str, Any]],
    quote_ccy: str = "USDT",
    read_only_verified: bool = False,
) -> AccountPortfolio:
    """Bybit-Wallet + ``/v5/position/list`` (oder Kraken ``OpenPositions``) → ``AccountPortfolio``."""
    holdings = [h for p in positions if (h := _perp_holding(p, account=account)) is not None]
    used = sum(h.cost_basis for h in holdings)
    cash = max(0.0, (equity or 0.0) - used) if equity is not None else 0.0
    return AccountPortfolio(
        account=account,
        as_of=as_of,
        cash=round(cash, 8),
        holdings=tuple(holdings),
        currency=quote_ccy,
        read_only_verified=read_only_verified,
    )


__all__ = ["map_derivatives_account", "map_spot_account"]
