"""``PortfolioIntelligenceEngine`` — eine Fassade über §33–§43.

Ein Aufruf → ein vollständiger Bericht: Konsolidierung, je-Position-Rating + Exit-Plan,
Re-Entry-Watches, Portfolio-Health, Holding-Ranking, ein Rotations-Vorschlag.

Eingaben sind **fertige** Bausteine (Account-Snapshots, je-Instrument-``EvaluationResult``,
Korrelations-Serien, Opportunities aus dem Scanner) — die Engine rechnet, entscheidet aber
nichts scharf: **kein Auto-Verkauf, keine Order.**
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from trading_agent.portfolio_intel.correlation import CorrelationEngine, CorrelationMatrix
from trading_agent.portfolio_intel.exit_intel import ExitIntelligence, ExitPlan
from trading_agent.portfolio_intel.health import (
    PortfolioHealth,
    PortfolioHealthReport,
    PortfolioRanking,
    RankedHolding,
    RotationEngine,
    RotationSuggestion,
)
from trading_agent.portfolio_intel.hub import PortfolioHub
from trading_agent.portfolio_intel.models import AccountPortfolio, ConsolidatedPortfolio
from trading_agent.portfolio_intel.position_intel import PositionIntelligence, PositionRating
from trading_agent.portfolio_intel.reentry import ReEntryAssessment, ReEntryEngine


@dataclass(frozen=True, slots=True)
class PortfolioIntelligenceReport:
    as_of: datetime
    consolidated: ConsolidatedPortfolio
    ratings: tuple[PositionRating, ...]
    exit_plans: tuple[ExitPlan, ...]
    reentry: tuple[ReEntryAssessment, ...]
    health: PortfolioHealthReport
    ranking: tuple[RankedHolding, ...]
    rotation: RotationSuggestion | None
    correlation: CorrelationMatrix | None

    def as_dict(self) -> dict[str, object]:
        return {
            "as_of": self.as_of.isoformat(),
            "equity": round(self.consolidated.equity, 2),
            "cash_pct": round(self.consolidated.cash_pct, 4),
            "allocation": {k.value: round(v, 4) for k, v in self.consolidated.allocation().items()},
            "health": self.health.as_dict(),
            "ranking": [
                {
                    "rank": r.rank,
                    "instrument": r.instrument,
                    "score": r.score,
                    "verdict": r.verdict.value,
                    "weight_pct": r.weight_pct,
                }
                for r in self.ranking
            ],
            "ratings": [r.as_dict() for r in self.ratings],
            "exit_plans": [p.as_dict() for p in self.exit_plans if p.kind.value != "none"],
            "reentry": [a.as_dict() for a in self.reentry],
            "rotation": None if self.rotation is None else self.rotation.as_dict(),
        }


class PortfolioIntelligenceEngine:
    def __init__(
        self,
        *,
        hub: PortfolioHub | None = None,
        position_intel: PositionIntelligence | None = None,
        exit_intel: ExitIntelligence | None = None,
        health: PortfolioHealth | None = None,
        rotation: RotationEngine | None = None,
        reentry: ReEntryEngine | None = None,
        correlation: CorrelationEngine | None = None,
    ) -> None:
        self.hub = hub or PortfolioHub()
        self.position_intel = position_intel or PositionIntelligence()
        self.exit_intel = exit_intel or ExitIntelligence()
        self.health = health or PortfolioHealth()
        self.rotation = rotation or RotationEngine()
        self.reentry = reentry or ReEntryEngine()
        self.correlation = correlation or CorrelationEngine()

    def assess(
        self,
        accounts: list[AccountPortfolio] | tuple[AccountPortfolio, ...],
        *,
        as_of: datetime,
        evaluations: dict[str, object] | None = None,
        price_series: dict[str, object] | None = None,
        opportunities: list[object] | tuple[object, ...] = (),
    ) -> PortfolioIntelligenceReport:
        evaluations = {k.upper(): v for k, v in (evaluations or {}).items()}
        cp = self.hub.consolidate(accounts, as_of=as_of)

        corr: CorrelationMatrix | None = None
        if price_series and len(price_series) >= 2:
            corr = self.correlation.compute(
                {k.upper(): v for k, v in price_series.items()}, as_of=as_of
            )

        cohort = tuple(h.instrument for h in cp.net_holdings)
        ratings: list[PositionRating] = []
        exit_plans: list[ExitPlan] = []
        for h in cp.net_holdings:
            rating = self.position_intel.rate(
                h,
                evaluation=evaluations.get(h.instrument),
                portfolio_weight=cp.weight_of(h.instrument),
                correlation=corr,
                cohort=cohort,
            )
            ratings.append(rating)
            exit_plans.append(self.exit_intel.plan(h, rating))

        reentry: list[ReEntryAssessment] = []
        for w in self.reentry.watches:
            ev = evaluations.get(w.instrument)
            price = self._price_of(w.instrument, cp, price_series)
            if ev is not None and price is not None:
                a = self.reentry.assess(w.instrument, evaluation=ev, price=price)
                if a is not None:
                    reentry.append(a)

        health = self.health.evaluate(cp, ratings=ratings, correlation=corr)
        ranking = PortfolioRanking.rank(cp, ratings)
        rotation = self.rotation.suggest(ratings, opportunities)

        return PortfolioIntelligenceReport(
            as_of=as_of,
            consolidated=cp,
            ratings=tuple(ratings),
            exit_plans=tuple(exit_plans),
            reentry=tuple(reentry),
            health=health,
            ranking=ranking,
            rotation=rotation,
            correlation=corr,
        )

    @staticmethod
    def _price_of(
        instrument: str,
        cp: ConsolidatedPortfolio,
        price_series: dict[str, object] | None,
    ) -> float | None:
        h = cp.holding(instrument)
        if h is not None:
            return h.mark_price
        if price_series:
            bars = price_series.get(instrument) or price_series.get(instrument.upper())
            rows: list[object] = list(bars) if isinstance(bars, (list, tuple)) else []
            if rows:
                last = rows[-1]
                if isinstance(last, tuple):
                    return float(last[1])
                return float(getattr(last, "close", 0.0))
        return None


__all__ = ["PortfolioIntelligenceEngine", "PortfolioIntelligenceReport"]
