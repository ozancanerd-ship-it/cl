"""24/7 Market Scanner + Top-Opportunities-Ranking (`Masterplan §4/§5`).

Der Scanner läuft **über die vorhandene zentrale Pipeline**: er abonniert die `DecisionMade`-
Events (die bereits das volle `EvaluationResult` je Instrument tragen), verdichtet jedes zu
einem `OpportunityScore` und hält eine **asset-übergreifende Rangliste**, die sich bei jeder
Marktveränderung neu ordnet.

* Kein neuer Analyse-Pfad — die Bewertung nutzt ausschließlich `scanner.opportunity`.
* `MarketScanner` verwaltet die Score-Map, `TopOpportunities` die geordnete Sicht + „warum #1".
* Ergebnis-Events: `OpportunityScored` (je Bewertung), `RankingUpdated` (wenn #1 / Reihenfolge
  wechselt).
* Universum: eine **konfigurierbare Liste** (nicht nur die Watchlist) — der Aufrufer entscheidet,
  welche Instrumente die Pipeline füttert; der Scanner rankt, was reinkommt.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from trading_agent.runtime.bus import EventBus
from trading_agent.runtime.events import DecisionMade, OpportunityScored, RankingUpdated
from trading_agent.scanner.opportunity import OpportunityScore, score_opportunity
from trading_agent.utils.logging import get_logger

_log = get_logger("market_scanner")


@dataclass(slots=True)
class ScannerConfig:
    #: kanonisch → asset_class (für assetklassen-spezifische Score-Gewichtung + Anzeige)
    asset_class: dict[str, str] = field(default_factory=dict)
    #: kanonisch → "swing" | "day"
    horizon: dict[str, str] = field(default_factory=dict)
    default_asset_class: str = "crypto"
    default_horizon: str = "swing"
    #: nur Instrumente mit Score ≥ … erscheinen in `top()` (Rauschfilter)
    min_score_for_top: float = 0.0
    #: Bewertungen älter als … Sekunden gelten als veraltet und fallen aus dem Ranking
    stale_after_s: float = 3600.0


class MarketScanner:
    """Hält je Instrument die letzte `OpportunityScore`. `feed()` / Bus-Anbindung."""

    def __init__(self, cfg: ScannerConfig | None = None, *, clock: object = None) -> None:
        self.cfg = cfg or ScannerConfig()
        self._clock = clock
        self._scores: dict[str, OpportunityScore] = {}
        self._seen_at: dict[str, datetime] = {}
        self.evaluations = 0

    def _now(self) -> datetime:
        n = getattr(self._clock, "now", None)
        return n() if callable(n) else datetime.now(UTC)

    def attach(self, bus: EventBus) -> TopOpportunities:
        """Abonniert `DecisionMade` und gibt eine an denselben Bus gekoppelte Rangliste zurück."""
        top = TopOpportunities(self, bus=bus)

        async def _on_decision(ev: DecisionMade) -> None:
            self.feed(ev.instrument, ev.result)
            opp = self._scores.get(ev.instrument.upper())
            if opp is not None:
                await bus.publish(
                    OpportunityScored(
                        ts=opp.information_cutoff,
                        instrument=opp.instrument,
                        score=opp.score,
                        setup_state=opp.setup_state,
                        tier=opp.tier,
                        opportunity=opp,
                    )
                )
                await top.refresh()

        bus.subscribe(DecisionMade, _on_decision)
        return top

    def feed(
        self, instrument: str, result: object, *, spread_atr_ratio: float | None = None
    ) -> OpportunityScore:
        key = instrument.upper()
        opp = score_opportunity(
            result,
            spread_atr_ratio=spread_atr_ratio,
            asset_class=self.cfg.asset_class.get(key, self.cfg.default_asset_class),
            trading_horizon=self.cfg.horizon.get(key, self.cfg.default_horizon),
        )
        self._scores[key] = opp
        self._seen_at[key] = self._now()
        self.evaluations += 1
        return opp

    def score_for(self, instrument: str) -> OpportunityScore | None:
        return self._scores.get(instrument.upper())

    def all_scores(self) -> dict[str, OpportunityScore]:
        return dict(self._scores)

    def fresh_scores(self) -> list[OpportunityScore]:
        now = self._now()
        out: list[OpportunityScore] = []
        for key, opp in self._scores.items():
            seen = self._seen_at.get(key)
            if seen is None or (now - seen).total_seconds() <= self.cfg.stale_after_s:
                out.append(opp)
        return out


@dataclass(slots=True)
class RankedOpportunity:
    rank: int
    instrument: str
    score: float
    direction: str | None
    tier: str | None
    setup_state: str
    asset_class: str
    horizon: str
    headline: str


class TopOpportunities:
    """Geordnete, dynamische Sicht auf die Scanner-Score-Map. `Masterplan §5`."""

    def __init__(self, scanner: MarketScanner, *, bus: EventBus | None = None) -> None:
        self._scanner = scanner
        self._bus = bus
        self._last_top: str | None = None
        self._last_order: tuple[str, ...] = ()

    def ranking(self) -> list[RankedOpportunity]:
        rows = sorted(
            self._scanner.fresh_scores(),
            key=lambda o: (o.score, o.setup_readiness),
            reverse=True,
        )
        out: list[RankedOpportunity] = []
        for i, o in enumerate(rows, 1):
            if o.score < self._scanner.cfg.min_score_for_top:
                continue
            out.append(
                RankedOpportunity(
                    rank=i,
                    instrument=o.instrument,
                    score=o.score,
                    direction=o.direction.value if o.direction else None,
                    tier=o.tier,
                    setup_state=o.setup_state,
                    asset_class=o.asset_class,
                    horizon=o.trading_horizon,
                    headline=o.headline,
                )
            )
        return out

    def top(self, n: int = 5) -> list[RankedOpportunity]:
        return self.ranking()[:n]

    def rank_of(self, instrument: str) -> int | None:
        for r in self.ranking():
            if r.instrument == instrument.upper():
                return r.rank
        return None

    def explain(self, instrument: str) -> dict[str, object]:
        """„Warum ist dieses Asset auf Platz N?" — Faktor-Bilanz + Vergleich zu #2."""
        opp = self._scanner.score_for(instrument)
        if opp is None:
            return {"error": f"kein Score für {instrument}"}
        ranked = self.ranking()
        rank = next((r.rank for r in ranked if r.instrument == opp.instrument), None)
        contributions = sorted(
            (
                {
                    "factor": f.name,
                    "value": round(f.value, 3),
                    "weight": f.weight,
                    "contribution": round(f.value * f.weight, 2),
                    "detail": f.detail,
                }
                for f in opp.factors
                if f.available
            ),
            key=lambda x: x["contribution"],  # type: ignore[arg-type,return-value]
            reverse=True,
        )
        runner_up = ranked[1] if len(ranked) > 1 else None
        return {
            "instrument": opp.instrument,
            "rank": rank,
            "score": opp.score,
            "setup_state": opp.setup_state,
            "tier": opp.tier,
            "direction": opp.direction.value if opp.direction else None,
            "strategy_score": opp.strategy_score,
            "headline": opp.headline,
            "top_factors": contributions[:5],
            "weak_or_missing": [f.name for f in opp.factors if not f.available],
            "not_yet_evaluated": list(opp.unavailable),
            "vs_runner_up": (
                {
                    "instrument": runner_up.instrument,
                    "score": runner_up.score,
                    "gap": round(opp.score - runner_up.score, 1),
                }
                if runner_up
                else None
            ),
        }

    async def refresh(self) -> None:
        """Nach einer Neubewertung: wenn sich #1 oder die Reihenfolge geändert hat → Event."""
        ranked = self.ranking()
        order = tuple(r.instrument for r in ranked)
        new_top = order[0] if order else None
        if order != self._last_order:
            prev = self._last_top
            self._last_top, self._last_order = new_top, order
            if self._bus is not None and new_top is not None:
                await self._bus.publish(
                    RankingUpdated(
                        ts=datetime.now(UTC),
                        top_instrument=new_top,
                        previous_top=prev,
                        ranking=tuple((r.instrument, r.score) for r in ranked[:10]),
                    )
                )


__all__ = [
    "MarketScanner",
    "RankedOpportunity",
    "ScannerConfig",
    "TopOpportunities",
]
