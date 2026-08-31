"""Portfolio-Health, -Ranking und -Rotation (Masterplan §39/§40/§42).

* ``PortfolioHealth`` — ein 0–100-Wert aus Diversifikation, Konzentration, Korrelations-Hitze,
  mittlerer Positions-Qualität, Cash-Puffer, Allokations-Drift und offenem Verlust.
* ``PortfolioRanking`` — die Holdings nach ``PositionRating``-Score sortiert.
* ``RotationEngine`` — vergleicht das schwächste Holding mit der besten freien Opportunity und
  schlägt (nur als Text!) eine Rotation vor. **Kein Auto-Verkauf.**
"""

from __future__ import annotations

from dataclasses import dataclass

from trading_agent.core.enums import AssetClass
from trading_agent.portfolio_intel.correlation import CorrelationMatrix
from trading_agent.portfolio_intel.models import ConsolidatedPortfolio, PositionVerdict
from trading_agent.portfolio_intel.position_intel import PositionRating

_EQUITY_CLASSES = frozenset({AssetClass.EQUITY})
_CRYPTO_GOLD_CLASSES = frozenset({AssetClass.CRYPTO, AssetClass.ALTCOIN, AssetClass.GOLD})


def _clip01(x: float) -> float:
    return 0.0 if x < 0.0 else 1.0 if x > 1.0 else x


# ---------------------------------------------------------------------------- Health


@dataclass(frozen=True, slots=True)
class PortfolioHealthReport:
    score: float  # 0..100
    grade: str  # GREEN / YELLOW / RED
    components: dict[str, float]  # jeweils 0..1
    flags: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "score": self.score,
            "grade": self.grade,
            "components": {k: round(v, 3) for k, v in self.components.items()},
            "flags": list(self.flags),
        }


class PortfolioHealth:
    def __init__(
        self,
        *,
        target_equity_bucket: tuple[float, float] = (0.35, 0.65),
        cash_sweet_spot: tuple[float, float] = (0.05, 0.35),
        max_single_weight: float = 0.25,
    ) -> None:
        self.eq_lo, self.eq_hi = target_equity_bucket
        self.cash_lo, self.cash_hi = cash_sweet_spot
        self.max_single = max_single_weight

    def evaluate(
        self,
        cp: ConsolidatedPortfolio,
        *,
        ratings: list[PositionRating] | tuple[PositionRating, ...] = (),
        correlation: CorrelationMatrix | None = None,
    ) -> PortfolioHealthReport:
        flags: list[str] = []
        holdings = cp.net_holdings
        eq = cp.equity
        weights = [h.market_value / eq for h in holdings] if eq > 0 else []

        # Konzentration
        max_w = max(weights, default=0.0)
        concentration = _clip01(1.0 - max(0.0, max_w - self.max_single) / (1.0 - self.max_single))
        if max_w > self.max_single:
            flags.append(f"Konzentration: größte Position {max_w:.0%} (> {self.max_single:.0%})")

        # Diversifikation — effektive Anzahl Positionen (Inverse HHI), auf 6+ normiert
        hhi = sum(w * w for w in weights)
        eff_n = (1.0 / hhi) if hhi > 0 else 0.0
        diversification = _clip01(eff_n / 6.0)
        if 0 < len(holdings) < 3:
            flags.append(f"nur {len(holdings)} Position(en) — geringe Streuung")

        # Korrelations-Hitze
        if correlation is not None and len(holdings) >= 2:
            names = [h.instrument for h in holdings]
            pair = [
                abs(correlation.correlation(a, b))
                for i, a in enumerate(names)
                for b in names[i + 1 :]
            ]
            avg_corr = sum(pair) / len(pair) if pair else 0.0
            corr_heat = _clip01(1.0 - max(0.0, avg_corr - 0.3) / 0.6)
            if avg_corr >= 0.6:
                flags.append(f"hohe mittlere Korrelation der Holdings (ρ̄={avg_corr:.2f})")
        else:
            corr_heat = 0.7

        # Positions-Qualität
        if ratings:
            quality = _clip01(sum(r.score for r in ratings) / (100.0 * len(ratings)))
            n_exit = sum(
                1 for r in ratings if r.verdict in (PositionVerdict.EXIT, PositionVerdict.REDUCE)
            )
            if n_exit:
                flags.append(f"{n_exit} Position(en) mit Verdikt REDUCE/EXIT")
        else:
            quality = 0.5

        # Cash-Puffer
        cash_pct = cp.cash_pct
        if cash_pct < self.cash_lo:
            cash_buffer = _clip01(cash_pct / self.cash_lo)
            flags.append(f"wenig Cash-Puffer ({cash_pct:.0%})")
        elif cash_pct > self.cash_hi:
            cash_buffer = _clip01(1.0 - (cash_pct - self.cash_hi) / (1.0 - self.cash_hi))
            flags.append(f"viel unallokiertes Cash ({cash_pct:.0%})")
        else:
            cash_buffer = 1.0

        # Allokations-Drift (Aktien-Bucket)
        alloc = cp.allocation()
        eq_bucket = sum(v for k, v in alloc.items() if k in _EQUITY_CLASSES)
        cg_bucket = sum(v for k, v in alloc.items() if k in _CRYPTO_GOLD_CLASSES)
        invested = eq_bucket + cg_bucket
        if invested > 0:
            eq_share = eq_bucket / invested
            if eq_share < self.eq_lo:
                drift = _clip01(1.0 - (self.eq_lo - eq_share) / self.eq_lo)
                flags.append(
                    f"Aktien-Anteil {eq_share:.0%} unter Zielband {self.eq_lo:.0%}–{self.eq_hi:.0%}"
                )
            elif eq_share > self.eq_hi:
                drift = _clip01(1.0 - (eq_share - self.eq_hi) / (1.0 - self.eq_hi))
                flags.append(f"Aktien-Anteil {eq_share:.0%} über Zielband")
            else:
                drift = 1.0
        else:
            drift = 1.0

        # offener Verlust
        dd = cp.unrealized_pnl / eq if eq > 0 else 0.0
        drawdown = _clip01(1.0 + dd * 4.0) if dd < 0 else 1.0
        if dd <= -0.05:
            flags.append(f"offener Verlust {dd:.1%} des Equity")

        components = {
            "diversification": diversification,
            "concentration": concentration,
            "correlation_heat": corr_heat,
            "position_quality": quality,
            "cash_buffer": cash_buffer,
            "allocation_drift": drift,
            "drawdown": drawdown,
        }
        w = {
            "diversification": 0.16,
            "concentration": 0.18,
            "correlation_heat": 0.14,
            "position_quality": 0.22,
            "cash_buffer": 0.10,
            "allocation_drift": 0.10,
            "drawdown": 0.10,
        }
        score = round(100.0 * sum(components[k] * w[k] for k in components), 1)
        grade = "GREEN" if score >= 70 else "YELLOW" if score >= 45 else "RED"
        return PortfolioHealthReport(
            score=score, grade=grade, components=components, flags=tuple(flags)
        )


# ---------------------------------------------------------------------------- Ranking


@dataclass(frozen=True, slots=True)
class RankedHolding:
    rank: int
    instrument: str
    account: str
    score: float
    verdict: PositionVerdict
    weight_pct: float


class PortfolioRanking:
    @staticmethod
    def rank(
        cp: ConsolidatedPortfolio, ratings: list[PositionRating] | tuple[PositionRating, ...]
    ) -> tuple[RankedHolding, ...]:
        eq = cp.equity
        by_inst = {r.instrument: r for r in ratings}
        rows: list[RankedHolding] = []
        ordered = sorted(ratings, key=lambda r: r.score, reverse=True)
        for i, r in enumerate(ordered, start=1):
            h = cp.holding(r.instrument)
            w = (h.market_value / eq * 100.0) if (h is not None and eq > 0) else 0.0
            rows.append(
                RankedHolding(
                    rank=i,
                    instrument=r.instrument,
                    account=r.account,
                    score=r.score,
                    verdict=r.verdict,
                    weight_pct=round(w, 2),
                )
            )
        _ = by_inst
        return tuple(rows)

    @staticmethod
    def weakest(
        ratings: list[PositionRating] | tuple[PositionRating, ...],
    ) -> PositionRating | None:
        return min(ratings, key=lambda r: r.score) if ratings else None


# ---------------------------------------------------------------------------- Rotation


@dataclass(frozen=True, slots=True)
class RotationSuggestion:
    sell_instrument: str
    sell_account: str
    sell_score: float
    sell_verdict: PositionVerdict
    buy_instrument: str
    buy_opportunity_score: float
    edge: float  # buy_score - sell_score (Score-Punkte)
    rationale: tuple[str, ...]
    note: str = "Vorschlag — kein Auto-Verkauf. Erst nach manueller Prüfung + neuem Signal."

    def as_dict(self) -> dict[str, object]:
        return {
            "sell": {
                "instrument": self.sell_instrument,
                "account": self.sell_account,
                "score": self.sell_score,
                "verdict": self.sell_verdict.value,
            },
            "buy": {
                "instrument": self.buy_instrument,
                "opportunity_score": self.buy_opportunity_score,
            },
            "edge": round(self.edge, 1),
            "rationale": list(self.rationale),
            "note": self.note,
        }


class RotationEngine:
    def __init__(self, *, min_edge: float = 20.0) -> None:
        self.min_edge = min_edge

    def suggest(
        self,
        ratings: list[PositionRating] | tuple[PositionRating, ...],
        opportunities: list[object] | tuple[object, ...],
    ) -> RotationSuggestion | None:
        weak = PortfolioRanking.weakest(ratings)
        if weak is None or weak.verdict in (PositionVerdict.STRONG_HOLD, PositionVerdict.HOLD):
            return None
        held = {r.instrument for r in ratings}
        candidates = [
            o
            for o in opportunities
            if str(getattr(o, "instrument", "")).upper() not in held
            and bool(getattr(o, "is_actionable", False))
        ]
        if not candidates:
            return None
        best = max(candidates, key=lambda o: float(getattr(o, "score", 0.0)))
        buy_score = float(getattr(best, "score", 0.0))
        edge = buy_score - weak.score
        if edge < self.min_edge:
            return None
        rationale = (
            f"Schwächstes Holding {weak.instrument}: Score {weak.score:.0f} / {weak.verdict.value}",
            f"Freie Opportunity {getattr(best, 'instrument', '?')}: Score {buy_score:.0f}, actionable",
            f"Score-Vorsprung {edge:.0f} ≥ Schwelle {self.min_edge:.0f}",
            *(weak.reasons[:2]),
        )
        return RotationSuggestion(
            sell_instrument=weak.instrument,
            sell_account=weak.account,
            sell_score=weak.score,
            sell_verdict=weak.verdict,
            buy_instrument=str(getattr(best, "instrument", "?")),
            buy_opportunity_score=buy_score,
            edge=edge,
            rationale=rationale,
        )


__all__ = [
    "PortfolioHealth",
    "PortfolioHealthReport",
    "PortfolioRanking",
    "RankedHolding",
    "RotationEngine",
    "RotationSuggestion",
]
