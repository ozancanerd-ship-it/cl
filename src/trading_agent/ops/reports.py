"""Daily / Weekly Report (Masterplan §60–§62).

Bündelt den Output der bestehenden Engines zu **einem** menschenlesbaren Bericht:
Top-Opportunities, ausgegebene Signale, Portfolio-Intelligence (Health/Verdikte/Rotation),
Market Breadth, System-Health, Paper-Performance.

Reine Aufbereitung — die Bausteine werden fertig übergeben, hier wird nichts neu berechnet.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


class ReportPeriod(StrEnum):
    DAILY = "daily"
    WEEKLY = "weekly"


@dataclass(frozen=True, slots=True)
class ReportInputs:
    period: ReportPeriod
    generated_at: datetime
    window_start: datetime
    window_end: datetime
    top_opportunities: list[dict[str, object]] = field(default_factory=list)
    signals_emitted: list[dict[str, object]] = field(default_factory=list)
    portfolio: dict[str, object] | None = None  # PortfolioIntelligenceReport.as_dict()
    breadth: dict[str, object] | None = None  # MarketBreadth.as_dict()
    system_health: dict[str, object] | None = None
    paper_performance: dict[str, object] | None = None
    notes: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class Report:
    period: ReportPeriod
    generated_at: datetime
    headline: str
    sections: dict[str, str]

    def as_text(self) -> str:
        lines = [self.headline, "=" * len(self.headline), ""]
        for title, body in self.sections.items():
            lines.append(f"## {title}")
            lines.append(body.rstrip())
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"

    def as_dict(self) -> dict[str, object]:
        return {
            "period": self.period.value,
            "generated_at": self.generated_at.isoformat(),
            "headline": self.headline,
            "sections": self.sections,
        }


def _fmt_opportunities(rows: list[dict[str, object]], limit: int = 5) -> str:
    if not rows:
        return "keine bewerteten Instrumente im Zeitraum."
    out = []
    for r in rows[:limit]:
        out.append(
            f"  #{r.get('rank', '?')}  {r.get('instrument', '?'):<12} "
            f"Score {r.get('score', 0)}  {r.get('tier') or '—'}  "
            f"{r.get('setup_state', '?')}  {r.get('direction') or ''}".rstrip()
        )
    return "\n".join(out)


def _fmt_signals(rows: list[dict[str, object]]) -> str:
    if not rows:
        return "keine BUY/SELL-Signale — NO-TRADE-Zeitraum (lieber kein Trade als ein schlechter)."
    out = []
    for s in rows:
        out.append(
            f"  {s.get('action', '?')} {s.get('instrument', '?')} {s.get('direction', '')} · "
            f"Entry {s.get('entry')} / SL {s.get('stop_loss')} / TP2 {s.get('tp2')} · "
            f"RR {s.get('rr_to_tp2')} · Score {s.get('opportunity_score')}"
        )
    return "\n".join(out)


def _fmt_portfolio(p: dict[str, object] | None) -> str:
    if not p:
        return "kein Portfolio-Snapshot (keine Account-Daten übergeben)."
    raw_health = p.get("health")
    health: dict[str, object] = raw_health if isinstance(raw_health, dict) else {}
    raw_cash = p.get("cash_pct", 0)
    cash_pct = float(raw_cash) if isinstance(raw_cash, (int, float, str)) else 0.0
    lines = [
        f"  Equity {p.get('equity')}  ·  Cash {round(cash_pct * 100, 1)}%  ·  "
        f"Health {health.get('score', '?')}/{100} ({health.get('grade', '?')})"
    ]
    ranking = p.get("ranking", [])
    if isinstance(ranking, list):
        for r in ranking[:8]:
            if isinstance(r, dict):
                lines.append(
                    f"    #{r.get('rank')} {r.get('instrument'):<12} "
                    f"{r.get('score')}/100  {r.get('verdict')}  {r.get('weight_pct')}%"
                )
    flags = health.get("flags", []) if isinstance(health, dict) else []
    if isinstance(flags, list) and flags:
        lines.append("  Flags: " + "; ".join(str(f) for f in flags))
    rot = p.get("rotation")
    if isinstance(rot, dict):
        sell = rot.get("sell", {})
        buy = rot.get("buy", {})
        lines.append(
            f"  Rotation-Vorschlag: {sell.get('instrument') if isinstance(sell, dict) else '?'} "
            f"→ {buy.get('instrument') if isinstance(buy, dict) else '?'} "
            f"(Edge {rot.get('edge')}) — kein Auto-Verkauf"
        )
    return "\n".join(lines)


def _fmt_breadth(b: dict[str, object] | None) -> str:
    if not b:
        return "keine Breadth-Daten."
    return (
        f"  Regime {b.get('regime', '?')}  ·  Score {b.get('breadth_score')}  ·  "
        f"A/D {b.get('advancers')}/{b.get('decliners')}  ·  "
        f">SMA20 {b.get('pct_above_sma20')}  ·  NH/NL {b.get('new_highs')}/{b.get('new_lows')}"
    )


def _fmt_kv(d: dict[str, object] | None, empty: str) -> str:
    if not d:
        return empty
    return "\n".join(f"  {k}: {v}" for k, v in d.items())


def build_report(inp: ReportInputs) -> Report:
    span = f"{inp.window_start.date()} → {inp.window_end.date()}"
    n_sig = len(inp.signals_emitted)
    headline = (
        f"{inp.period.value.upper()} REPORT · {span} · "
        f"{n_sig} Signal(e) · {len(inp.top_opportunities)} Instrument(e) gescannt"
    )
    sections: dict[str, str] = {
        "Top Opportunities": _fmt_opportunities(inp.top_opportunities),
        "Signale": _fmt_signals(inp.signals_emitted),
        "Portfolio Intelligence": _fmt_portfolio(inp.portfolio),
        "Market Breadth": _fmt_breadth(inp.breadth),
        "Paper Performance": _fmt_kv(inp.paper_performance, "keine Paper-Trades im Zeitraum."),
        "System Health": _fmt_kv(inp.system_health, "kein Health-Snapshot."),
    }
    if inp.notes:
        sections["Notizen"] = "\n".join(f"  - {n}" for n in inp.notes)
    return Report(
        period=inp.period,
        generated_at=inp.generated_at,
        headline=headline,
        sections=sections,
    )


__all__ = ["Report", "ReportInputs", "ReportPeriod", "build_report"]
