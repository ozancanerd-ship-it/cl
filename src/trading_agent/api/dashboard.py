"""Dashboard-State-Assembler (Masterplan §63–§70) — **framework-frei**.

Verdichtet den Output der bestehenden Engines zu **einem** JSON-serialisierbaren Objekt mit
den zehn Tabs des Ziel-UIs. Kein HTTP hier — ein späterer FastAPI-Layer (oder ein statisches
HTML-Dashboard) serialisiert nur noch ``DashboardState.as_dict()``.

Jeder Tab ist unabhängig: fehlt ein Baustein (kein Portfolio, kein News-Feed), zeigt der Tab
einen ``available: false``-Marker statt erfundener Zahlen (NO BLIND AI).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

TABS = (
    "overview",
    "market_scanner",
    "top_opportunities",
    "chart_analysis",
    "signals",
    "my_portfolios",
    "paper_trading",
    "performance",
    "news_macro",
    "system_health",
)


def _section(available: bool, **data: Any) -> dict[str, Any]:
    return {"available": available, **data}


def _merge(available: bool, extra: dict[str, Any] | None, **data: Any) -> dict[str, Any]:
    """``_section`` + ein Fremd-Dict, dessen ``available``-Key nicht kollidieren darf."""
    merged = {k: v for k, v in (extra or {}).items() if k != "available"}
    merged.update(data)
    return {"available": available, **merged}


@dataclass(frozen=True, slots=True)
class DashboardInputs:
    as_of: datetime
    strategy_version: str = "0.1.1"
    top_opportunities: list[dict[str, Any]] = field(default_factory=list)
    scanner_evaluations: int = 0
    signals: list[dict[str, Any]] = field(default_factory=list)
    chart_annotations: dict[str, Any] | None = None
    portfolio: dict[str, Any] | None = None
    paper_positions: list[dict[str, Any]] = field(default_factory=list)
    paper_performance: dict[str, Any] | None = None
    breadth: dict[str, Any] | None = None
    macro: dict[str, Any] | None = None
    news: dict[str, Any] | None = None
    system_health: dict[str, Any] | None = None
    blockers: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class DashboardState:
    as_of: str
    tabs: dict[str, dict[str, Any]]

    def as_dict(self) -> dict[str, Any]:
        return {"as_of": self.as_of, "tabs": self.tabs}

    def tab(self, name: str) -> dict[str, Any]:
        return self.tabs[name]


def build_dashboard_state(inp: DashboardInputs) -> DashboardState:
    top = inp.top_opportunities
    signals = inp.signals
    health = inp.system_health or {}
    grade = str(health.get("grade", "UNKNOWN")) if health else "UNKNOWN"

    actionable = [
        o
        for o in top
        if str(o.get("tier") or "") in ("A+", "A", "B") and o.get("setup_state") == "armed"
    ]

    tabs: dict[str, dict[str, Any]] = {
        "overview": _section(
            True,
            headline=(
                f"{len(signals)} Signal(e) · {len(top)} Instrument(e) gerankt · System {grade}"
            ),
            best_opportunity=top[0] if top else None,
            open_signals=len(signals),
            actionable_setups=len(actionable),
            portfolio_health=(inp.portfolio or {}).get("health") if inp.portfolio else None,
            breadth_regime=(inp.breadth or {}).get("regime") if inp.breadth else None,
            blockers=inp.blockers,
        ),
        "market_scanner": _section(
            bool(top),
            evaluated=inp.scanner_evaluations,
            instruments=top,
        ),
        "top_opportunities": _section(
            bool(top),
            ranking=top,
            actionable=actionable,
            note="Ranking nach Opportunity-Score, dann Setup-Reife (Masterplan §5/§6).",
        ),
        "chart_analysis": _section(
            inp.chart_annotations is not None,
            annotations=inp.chart_annotations,
        ),
        "signals": _section(
            True,
            emitted=signals,
            note=(
                "NO-TRADE-Zeitraum — lieber kein Trade als ein schlechter."
                if not signals
                else f"{len(signals)} strukturierte(s) BUY/SELL-Signal(e)."
            ),
        ),
        "my_portfolios": _merge(inp.portfolio is not None, inp.portfolio),
        "paper_trading": _section(
            True,
            open_positions=inp.paper_positions,
            performance=inp.paper_performance,
            validated=bool(inp.paper_performance and inp.paper_performance.get("trades", 0) >= 100),
        ),
        "performance": _merge(inp.paper_performance is not None, inp.paper_performance),
        "news_macro": _section(
            inp.macro is not None or inp.news is not None or inp.breadth is not None,
            macro=inp.macro,
            news=inp.news,
            breadth=inp.breadth,
        ),
        "system_health": _merge(bool(health), health, strategy_version=inp.strategy_version),
    }
    return DashboardState(as_of=inp.as_of.isoformat(), tabs=tabs)


__all__ = ["TABS", "DashboardInputs", "DashboardState", "build_dashboard_state"]
