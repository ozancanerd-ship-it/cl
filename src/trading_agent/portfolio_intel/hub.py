"""``PortfolioHub`` — Konsolidierung mehrerer read-only Accounts (Masterplan §33/§34).

Nimmt fertige ``AccountPortfolio``-Snapshots (die Adapter-Anbindung liegt außerhalb — die
read-only Adapter für Kraken/Bybit/Binance existieren bereits) und erzeugt die konsolidierte
Sicht: Netto-Holdings je Instrument, Equity je Account, Wert je Asset-Klasse.

**Keine Order, kein Schreiben.** Wenn zwei Accounts dasselbe Instrument in *gegenläufiger*
Richtung halten, werden beide als getrennte Netto-Holdings geführt (Hedge sichtbar lassen,
nicht wegrechnen).
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from trading_agent.core.enums import AssetClass, Direction
from trading_agent.portfolio_intel.models import (
    AccountPortfolio,
    ConsolidatedPortfolio,
    Holding,
)


class PortfolioHub:
    """Reiner Konsolidierer. Zustandslos zwischen ``consolidate``-Aufrufen."""

    def consolidate(
        self, accounts: list[AccountPortfolio] | tuple[AccountPortfolio, ...], *, as_of: datetime
    ) -> ConsolidatedPortfolio:
        accounts = tuple(accounts)
        per_account_equity = {a.account: a.equity for a in accounts}

        # (instrument, direction) -> Liste der Einzel-Holdings
        buckets: dict[tuple[str, Direction], list[Holding]] = defaultdict(list)
        for acc in accounts:
            for h in acc.holdings:
                buckets[(h.instrument.upper(), h.direction)].append(h)

        net: list[Holding] = []
        for (instrument, direction), hs in sorted(buckets.items(), key=lambda kv: kv[0][0]):
            qty = sum(h.quantity for h in hs)
            if qty <= 0:
                continue
            cost = sum(h.cost_basis for h in hs)
            mark = sum(h.market_value for h in hs) / qty
            avg_entry = cost / qty
            opened = [h.opened_at for h in hs if h.opened_at is not None]
            stops = [h.stop_ref for h in hs if h.stop_ref is not None]
            accs = sorted({h.account for h in hs})
            net.append(
                Holding(
                    instrument=instrument,
                    asset_class=hs[0].asset_class,
                    account="+".join(accs),
                    direction=direction,
                    quantity=qty,
                    avg_entry_price=avg_entry,
                    mark_price=mark,
                    opened_at=min(opened) if opened else None,
                    setup_id=next((h.setup_id for h in hs if h.setup_id), None),
                    stop_ref=(sum(stops) / len(stops)) if stops else None,
                )
            )

        ac_value: dict[AssetClass, float] = defaultdict(float)
        for h in net:
            ac_value[h.asset_class] += h.market_value

        return ConsolidatedPortfolio(
            as_of=as_of,
            accounts=accounts,
            net_holdings=tuple(net),
            per_account_equity=per_account_equity,
            asset_class_value=dict(ac_value),
        )

    @staticmethod
    def to_portfolio_context_positions(
        cp: ConsolidatedPortfolio,
    ) -> tuple[tuple[str, Direction, float], ...]:
        """(instrument, direction, gewicht%) — für die V9-/Duplikat-Vetos der Strategy-Engine."""
        eq = cp.equity
        out: list[tuple[str, Direction, float]] = []
        for h in cp.net_holdings:
            w = (h.market_value / eq * 100.0) if eq > 0 else 0.0
            out.append((h.instrument, h.direction, round(w, 4)))
        return tuple(out)


__all__ = ["PortfolioHub"]
