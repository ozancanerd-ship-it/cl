"""Portfolio-Ledger — der Zustand, den die Risk Engine und ``strategy.evaluate`` konsumieren.

Verfolgt Equity, offene Positionen (mit 1R-Risiko), realisierte Tages-/Wochen-P&L und Drawdown.
Produziert die ``PortfolioContext`` (für die C9-Vetos in ``evaluate``) und ``AccountState`` (für
die Risk Engine). **Kein Broker** — der Ledger wird von der Paper-/Backtest-Schleife bzw. später
von der Broker-Reconciliation gefüttert.

Alle Prozentwerte in **% der Start-Equity des Tages/der Woche** (peak-basierter Drawdown).
"""

from __future__ import annotations

import dataclasses
from datetime import date, datetime

from trading_agent.core.enums import Direction
from trading_agent.core.time import ensure_utc
from trading_agent.core.types import OpenPositionInfo, PortfolioContext
from trading_agent.risk.risk_engine import AccountState


@dataclasses.dataclass(frozen=True, slots=True)
class LedgerPosition:
    instrument: str
    direction: Direction
    open_risk_amount: float  # 1R in Account-Währung
    entry_ts: datetime
    cluster_id: str | None = None

    def risk_pct(self, equity: float) -> float:
        return (self.open_risk_amount / equity * 100.0) if equity > 0 else 0.0


@dataclasses.dataclass(frozen=True, slots=True)
class ClusterMap:
    """Instrument → Cluster-Id (BTC/ETH = 'crypto_majors', XAU = 'metals', …)."""

    by_instrument: dict[str, str] = dataclasses.field(default_factory=dict)
    static_correlations: dict[tuple[str, str], float] = dataclasses.field(default_factory=dict)

    def cluster(self, instrument: str) -> str | None:
        return self.by_instrument.get(instrument.upper())


class PortfolioLedger:
    """Zustandsbehaftet. Eine Instanz je Trading-Prozess."""

    def __init__(
        self,
        *,
        starting_equity: float,
        clusters: ClusterMap | None = None,
        cluster_cap_pct: float = 2.0,
        correlation_threshold: float = 0.70,
    ) -> None:
        self._equity = starting_equity
        self._day_start_equity = starting_equity
        self._week_start_equity = starting_equity
        self._peak_equity = starting_equity
        self._clusters = clusters or ClusterMap()
        self._cluster_cap_pct = cluster_cap_pct
        self._corr_threshold = correlation_threshold
        self._open: dict[str, LedgerPosition] = {}
        self._armed: dict[str, Direction] = {}
        self._trades_today = 0
        self._consecutive_losses = 0
        self._cur_day: date | None = None
        self._cur_week: tuple[int, int] | None = None

    # ---- Zeit-Rollover -------------------------------------------------------
    def roll_time(self, now: datetime) -> None:
        now = ensure_utc(now)
        d = now.date()
        w = now.isocalendar()[:2]
        if self._cur_day != d:
            self._cur_day = d
            self._day_start_equity = self._equity
            self._trades_today = 0
        if self._cur_week != w:
            self._cur_week = w
            self._week_start_equity = self._equity

    # ---- Fortschreibung ---------------------------------------------------
    def on_open(
        self,
        instrument: str,
        direction: Direction,
        *,
        risk_amount: float,
        entry_ts: datetime,
    ) -> None:
        inst = instrument.upper()
        self._open[inst] = LedgerPosition(
            inst, direction, risk_amount, ensure_utc(entry_ts), self._clusters.cluster(inst)
        )
        self._armed.pop(inst, None)
        self._trades_today += 1

    def on_close(self, instrument: str, *, realized_pnl: float, is_scratch: bool = False) -> None:
        inst = instrument.upper()
        self._open.pop(inst, None)
        self._equity += realized_pnl
        self._peak_equity = max(self._peak_equity, self._equity)
        if not is_scratch:
            self._consecutive_losses = self._consecutive_losses + 1 if realized_pnl < 0 else 0

    def set_armed(self, instrument: str, direction: Direction) -> None:
        self._armed[instrument.upper()] = direction

    def mark_price_pnl(self, unrealized_pnl: float) -> None:
        """Optional: offene P&L in die Drawdown-Spitze einrechnen (peak/valley on equity+uPnL)."""
        eq = self._equity + unrealized_pnl
        self._peak_equity = max(self._peak_equity, eq)

    # ---- Zugriff ----------------------------------------------------------
    @property
    def equity(self) -> float:
        return self._equity

    @property
    def total_open_risk_pct(self) -> float:
        return sum(p.risk_pct(self._equity) for p in self._open.values())

    def cluster_open_risk_pct(self, cluster_id: str | None) -> float:
        if cluster_id is None:
            return 0.0
        return sum(
            p.risk_pct(self._equity) for p in self._open.values() if p.cluster_id == cluster_id
        )

    def _pct_loss(self, ref: float) -> float:
        return max(0.0, (ref - self._equity) / ref * 100.0) if ref > 0 else 0.0

    def to_account_state(self, *, available_margin: float | None = None) -> AccountState:
        dd = (
            max(0.0, (self._peak_equity - self._equity) / self._peak_equity * 100.0)
            if self._peak_equity > 0
            else 0.0
        )
        return AccountState(
            equity=self._equity,
            daily_loss_pct=self._pct_loss(self._day_start_equity),
            weekly_loss_pct=self._pct_loss(self._week_start_equity),
            drawdown_pct=dd,
            trades_today=self._trades_today,
            consecutive_losses=self._consecutive_losses,
            available_margin=available_margin,
        )

    def to_portfolio_context(self, *, next_instrument: str | None = None) -> PortfolioContext:
        cluster_risk = 0.0
        if next_instrument is not None:
            cluster_risk = self.cluster_open_risk_pct(self._clusters.cluster(next_instrument))
        return PortfolioContext(
            open_positions=tuple(
                OpenPositionInfo(
                    p.instrument, p.direction, round(p.risk_pct(self._equity), 6), p.cluster_id
                )
                for p in self._open.values()
            ),
            armed_setups=dict(self._armed),
            total_open_risk_pct=round(self.total_open_risk_pct, 6),
            cluster_open_risk_pct=round(cluster_risk, 6),
            cluster_cap_pct=self._cluster_cap_pct,
            correlation_threshold=self._corr_threshold,
            static_correlations=dict(self._clusters.static_correlations),
        )


__all__ = ["ClusterMap", "LedgerPosition", "PortfolioLedger"]
