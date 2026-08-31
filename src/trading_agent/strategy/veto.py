"""Veto-Engine — harte Handelsbarrieren V1–V10 (``contradictions.md`` §4/§6, ``SMC-SWEEP-REV-01`` §23).

**Ein Veto ist eine harte Barriere.** Ein hoher Confluence-/Score-Wert darf ein Veto **nicht**
überstimmen. Veto ≠ negativer Score — ein Veto bedeutet: *der Trade darf in diesem Zustand nicht
ausgelöst werden.* Die Score-Berechnung findet erst statt, wenn **keine** Vetos vorliegen
(``contradictions.md`` §6, Schritt 4 vor Schritt 6).

| ID | Veto | Auslöser |
|----|------|----------|
| **V1** | HTF directional conflict | D1/H4 gegensätzliche Trends bzw. Regime ``CONFLICTING`` |
| **V2** | Location blocked | Zonen-Mitte nicht im Discount/Premium des ``swept_leg`` (Location-Gate) |
| **V3** | Regime / volatility / compression | ``EXTREME`` **oder** ``LOW`` Vol · ``UNCLEAR`` · coiled ``COMPRESSION`` |
| **V4** | News / macro risk | blockierendes ``HIGH``-Event / ``risk_off`` **oder** Feed-Ausfall (fail-safe) |
| **V5** | Sweep-breakout conflict | ``confirmed close`` auf dem Sweep-TF erneut jenseits des Sweep-Extrems |
| **V6** | Data confidence floor | ``data_confidence < min_data_confidence`` (``0.50``) |
| **V7** | Spread / slippage / data age | Spread über Limit **oder** M5-Serie stale (Datenalter > Limit) |
| **V8** | RR blocked | eine der drei §16-Bedingungen verletzt (RR-Gate) |
| **V9** | Portfolio correlation / exposure | korrelierte Exposure / Konzentration / Portfolio-Heat |
| **V10** | No valid SL | Struktur erlaubt keinen regelkonformen SL (RR-Gate §10 Floor/Cap) |

**Determinismus / Point-in-time / Look-ahead:** alle Eingaben stammen aus ``MtfContext``
(≤ ``information_cutoff``), den (reinen) Gate-/Confluence-Ergebnissen und einem optionalen
``portfolio_context``-Snapshot. Rein funktional ⇒ deterministisch replaybar. **Asset-/Timeframe-aware**
(ATR-relativer Spread, TF-relatives Datenalter, Instrument-Korrelation). **Long/Short-symmetrisch**
(V5 gegen ``sweep.side``; alle übrigen Bedingungen richtungs-agnostisch oder von den Gates gespiegelt).

**Fehlende Daten** erzeugen **kein** Veto, wenn die Quelle für diesen Trade nicht zwingend nötig ist
(``derivatives`` / ``cross_asset`` / Spread in Paper/Demo → ``not_available``, nicht blockierend).
**Ausnahmen:** ``data_confidence`` unter der Schwelle ⇒ V6; fehlender News-Feed ⇒ V4 (Spec-Fail-safe,
``require_news_feed`` konfigurierbar). ``portfolio_context = None`` ⇒ V9 sauberer Pass-through.

**Kein Fake.** Es werden keine Werte erfunden — fehlt eine Eingabe, wird die zugehörige Prüfung als
``not_available`` protokolliert.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping, Sequence
from datetime import datetime
from enum import StrEnum

from trading_agent.analysis.mtf import MtfContext
from trading_agent.core.enums import (
    MarketSide,
    NoTradeReason,
    RegimeDirectional,
    RegimePhase,
    RegimeVolatility,
    Timeframe,
    VetoId,
)
from trading_agent.core.types import PortfolioContext
from trading_agent.core.version import STRATEGY_VERSION
from trading_agent.strategy.confluence import ConfluenceReport
from trading_agent.strategy.gates import GateReport
from trading_agent.strategy.setup_detection import SetupCandidate

_Evidence = Mapping[str, str | float | int | bool | None]


class VetoSeverity(StrEnum):
    CRITICAL = "critical"  # Daten / Safety / News — "so gar nicht erwägen"
    HARD = "hard"  # struktureller Veto dieses konkreten Setups
    PORTFOLIO = "portfolio"  # Setup ok, aber Portfolio kann es nicht tragen


class VetoSource(StrEnum):
    REGIME = "regime"
    LOCATION_GATE = "location_gate"
    RR_GATE = "rr_gate"
    MARKET_CONTEXT = "market_context"
    DATA_QUALITY = "data_quality"
    EXECUTION = "execution"
    SWEEP = "sweep"
    PORTFOLIO = "portfolio"
    CONFLUENCE_FLAG = "confluence_flag"


_SEVERITY: dict[VetoId, VetoSeverity] = {
    VetoId.V1: VetoSeverity.HARD,
    VetoId.V2: VetoSeverity.HARD,
    VetoId.V3: VetoSeverity.HARD,
    VetoId.V4: VetoSeverity.CRITICAL,
    VetoId.V5: VetoSeverity.HARD,
    VetoId.V6: VetoSeverity.CRITICAL,
    VetoId.V7: VetoSeverity.CRITICAL,
    VetoId.V8: VetoSeverity.HARD,
    VetoId.V9: VetoSeverity.PORTFOLIO,
    VetoId.V10: VetoSeverity.HARD,
}
# Priorität für die UI-Sortierung (kleiner = wichtiger zuerst)
_PRIORITY: dict[VetoId, int] = {
    VetoId.V6: 0,
    VetoId.V7: 1,
    VetoId.V4: 2,
    VetoId.V1: 3,
    VetoId.V3: 4,
    VetoId.V2: 5,
    VetoId.V5: 6,
    VetoId.V10: 7,
    VetoId.V8: 8,
    VetoId.V9: 9,
}


# --------------------------------------------------------------------------------- Parameter


@dataclasses.dataclass(frozen=True, slots=True)
class VetoParams:
    min_data_confidence: float = 0.50  # V6
    max_spread_atr: float = 0.10  # V7 — Spread ≤ x·ATR(entry_tf)
    max_spread_pct: float = 0.0005  # V7 — Spread/Preis ≤ 0.05 %
    max_data_age_periods: float = 3.0  # V7 — (cutoff − letzte M5-Bar) in M5-Perioden
    require_news_feed: bool = True  # V4 — fehlender Feed ⇒ Veto (Spec-Fail-safe, C10)
    portfolio_heat_cap_pct: float = 3.0  # V9 — Fallback, wenn portfolio keinen eigenen Cap liefert
    regime_timeframes: tuple[Timeframe, ...] = (Timeframe.D1, Timeframe.H4, Timeframe.M15)
    htf_timeframes: tuple[Timeframe, ...] = (Timeframe.D1, Timeframe.H4)
    sweep_timeframe: Timeframe = Timeframe.M15
    entry_timeframe: Timeframe = Timeframe.M5


# --------------------------------------------------------------------------------- Ausgabe


@dataclasses.dataclass(frozen=True, slots=True)
class VetoRecord:
    veto_id: VetoId
    reason: str
    severity: VetoSeverity
    timestamp: datetime  # wann die blockierende Bedingung beobachtet wurde
    information_cutoff: datetime
    evidence: _Evidence
    source: VetoSource
    blocking: bool = True
    correlated_with: tuple[VetoId, ...] = ()

    @property
    def priority(self) -> int:
        return _PRIORITY.get(self.veto_id, 99)


@dataclasses.dataclass(frozen=True, slots=True)
class VetoReport:
    instrument: str
    information_cutoff: datetime
    records: tuple[VetoRecord, ...]
    not_available: tuple[str, ...]  # Prüfungen ohne Daten (nicht blockierend)
    strategy_version: str = STRATEGY_VERSION

    @property
    def blocking(self) -> bool:
        return any(r.blocking for r in self.records)

    @property
    def veto_ids(self) -> tuple[VetoId, ...]:
        return tuple(dict.fromkeys(r.veto_id for r in self.records if r.blocking))

    @property
    def worst_severity(self) -> VetoSeverity | None:
        order = {VetoSeverity.PORTFOLIO: 0, VetoSeverity.HARD: 1, VetoSeverity.CRITICAL: 2}
        sev = [r.severity for r in self.records if r.blocking]
        return max(sev, key=lambda s: order[s]) if sev else None

    def by_id(self, veto_id: VetoId) -> VetoRecord | None:
        return next((r for r in self.records if r.veto_id is veto_id), None)


# --------------------------------------------------------------------------------- öffentlich


def assess_vetoes(
    mtf: MtfContext,
    candidate: SetupCandidate,
    *,
    confluence: ConfluenceReport | None = None,
    gates: GateReport | None = None,
    portfolio_context: PortfolioContext | None = None,
    params: VetoParams | None = None,
) -> VetoReport:
    """Vollständiger Veto-Lauf. ``gates`` / ``confluence`` / ``portfolio_context`` sind optional —
    fehlen sie, werden die betroffenen Prüfungen als ``not_available`` protokolliert (nicht
    blockierend), außer die Prüfung ist Spec-pflichtig (V4 Feed, V6)."""
    p = params or VetoParams()
    cutoff = mtf.information_cutoff
    flags = set(confluence.contradiction_flags) if confluence is not None else set()

    records: list[VetoRecord] = []
    na: list[str] = []

    for build in (
        _v1_htf_conflict,
        _v3_regime,
        _v4_news,
        _v5_sweep_breakout,
        _v6_data_confidence,
        _v7_execution,
    ):
        rec = build(mtf, candidate, cutoff, flags, p, na)
        if rec is not None:
            records.append(rec)

    records += _gate_vetoes(gates, cutoff, na)  # V2, V8, V10
    records += _v9_portfolio(candidate, portfolio_context, cutoff, p, na)

    records = _link_correlated(records)
    records.sort(key=lambda r: (r.priority, r.veto_id.value))

    return VetoReport(
        instrument=mtf.instrument,
        information_cutoff=cutoff,
        records=tuple(records),
        not_available=tuple(dict.fromkeys(na)),
    )


def collect_vetoes(
    mtf: MtfContext,
    candidate: SetupCandidate,
    *,
    confluence: ConfluenceReport | None = None,
    gates: GateReport | None = None,
    portfolio_context: PortfolioContext | None = None,
    params: VetoParams | None = None,
) -> tuple[VetoId, ...]:
    """Kompakte API: die blockierenden Veto-IDs (nach Priorität sortiert, dedupliziert)."""
    return assess_vetoes(
        mtf,
        candidate,
        confluence=confluence,
        gates=gates,
        portfolio_context=portfolio_context,
        params=params,
    ).veto_ids


# --------------------------------------------------------------------------------- V1 / V3


def _v1_htf_conflict(
    mtf: MtfContext,
    candidate: SetupCandidate,
    cutoff: datetime,
    flags: set[str],
    p: VetoParams,
    na: list[str],
) -> VetoRecord | None:
    d1 = _tf_directional(mtf, Timeframe.D1)
    h4 = _tf_directional(mtf, Timeframe.H4)
    conflicting = mtf.htf_directional is RegimeDirectional.CONFLICTING
    opposed = _dir_num(d1) != 0 and _dir_num(h4) != 0 and _dir_num(d1) != _dir_num(h4)
    if not (conflicting or opposed or "htf_conflict:V1" in flags):
        return None
    return VetoRecord(
        veto_id=VetoId.V1,
        reason="HTF-Richtungskonflikt: D1 und H4 zeigen gegensätzliche Trends",
        severity=_SEVERITY[VetoId.V1],
        timestamp=_tf_time(mtf, Timeframe.D1, cutoff),
        information_cutoff=cutoff,
        evidence={
            "d1_directional": d1.value if d1 is not None else None,
            "h4_directional": h4.value if h4 is not None else None,
            "merged": mtf.htf_directional.value,
            "confluence_flag": "htf_conflict:V1" in flags,
        },
        source=VetoSource.REGIME,
    )


def _v3_regime(
    mtf: MtfContext,
    candidate: SetupCandidate,
    cutoff: datetime,
    flags: set[str],
    p: VetoParams,
    na: list[str],
) -> VetoRecord | None:
    worst = _worst_volatility(mtf, p.regime_timeframes)
    unclear = any(_tf_directional(mtf, tf) is RegimeDirectional.UNCLEAR for tf in p.htf_timeframes)
    coiled = _any_coiled_compression(mtf, p.regime_timeframes)
    reasons: list[str] = []
    if worst is RegimeVolatility.EXTREME:
        reasons.append("Volatilität EXTREME")
    elif worst is RegimeVolatility.LOW:
        reasons.append("Volatilität LOW (für SMC-SWEEP-REV-01 verboten)")
    if unclear:
        reasons.append("HTF-Richtung UNCLEAR")
    if coiled:
        reasons.append("Phase coiled COMPRESSION")
    if not reasons and not (
        {"regime_vol_extreme:V3", "regime_vol_low:V3", "regime_phase_compression:V3"} & flags
    ):
        return None
    if not reasons:
        reasons.append("Regime-Gate nicht bestanden (Confluence-Flag)")
    return VetoRecord(
        veto_id=VetoId.V3,
        reason="Regime untauglich: " + " / ".join(reasons),
        severity=_SEVERITY[VetoId.V3],
        timestamp=_tf_time(mtf, Timeframe.H4, cutoff),
        information_cutoff=cutoff,
        evidence={
            "worst_volatility": worst.value,
            "htf_unclear": unclear,
            "coiled_compression": coiled,
            "regime_gate_ok": mtf.htf_regime_gate.ok,
            "regime_gate_reason": (
                mtf.htf_regime_gate.reason.value if mtf.htf_regime_gate.reason is not None else None
            ),
        },
        source=VetoSource.REGIME,
    )


# --------------------------------------------------------------------------------- V4 News


def _v4_news(
    mtf: MtfContext,
    candidate: SetupCandidate,
    cutoff: datetime,
    flags: set[str],
    p: VetoParams,
    na: list[str],
) -> VetoRecord | None:
    news = mtf.market_context.news
    if not news.feed_available:
        if not p.require_news_feed:
            na.append("v4_news_feed")
            return None
        return VetoRecord(
            veto_id=VetoId.V4,
            reason="News-Feed nicht verfügbar — fail-safe (news-rules.md, C10)",
            severity=_SEVERITY[VetoId.V4],
            timestamp=cutoff,
            information_cutoff=cutoff,
            evidence={"feed_available": False, "risk_off": news.risk_off},
            source=VetoSource.MARKET_CONTEXT,
        )
    blocking = news.risk_off or news.blocking_event_id is not None
    if not (blocking or "news_blocking:V4" in flags):
        return None
    return VetoRecord(
        veto_id=VetoId.V4,
        reason=(
            "Blockierendes HIGH-Impact-Event / risk_off im Fenster"
            if news.blocking_event_id is not None
            else "News-risk_off aktiv"
        ),
        severity=_SEVERITY[VetoId.V4],
        timestamp=news.feed_as_of or cutoff,
        information_cutoff=cutoff,
        evidence={
            "blocking_event_id": news.blocking_event_id,
            "risk_off": news.risk_off,
            "minutes_to_next_high_impact": news.minutes_to_next_high_impact,
            "feed_as_of": news.feed_as_of.isoformat() if news.feed_as_of is not None else None,
        },
        source=VetoSource.MARKET_CONTEXT,
    )


# --------------------------------------------------------------------------------- V5 Sweep


def _v5_sweep_breakout(
    mtf: MtfContext,
    candidate: SetupCandidate,
    cutoff: datetime,
    flags: set[str],
    p: VetoParams,
    na: list[str],
) -> VetoRecord | None:
    sweep = candidate.sweep
    if sweep is None:
        # Kein Sweep ist vor SWEPT normal (Kette noch am Aufbauen) — dann kein V5.
        if candidate.is_armed:
            return VetoRecord(
                veto_id=VetoId.V5,
                reason="Kein gültiger Sweep am ARMED-Kandidaten",
                severity=_SEVERITY[VetoId.V5],
                timestamp=cutoff,
                information_cutoff=cutoff,
                evidence={"sweep": None, "state": candidate.state.value},
                source=VetoSource.SWEEP,
            )
        return None
    if candidate.abort_reason is NoTradeReason.SWEEP_BECAME_BREAKOUT or (
        candidate.invalidation is NoTradeReason.CANDIDATE_INVALIDATED
    ):
        return VetoRecord(
            veto_id=VetoId.V5,
            reason="Sweep ungültig: Kette abgebrochen / Kandidat invalidiert (Re-Sweep)",
            severity=_SEVERITY[VetoId.V5],
            timestamp=cutoff,
            information_cutoff=cutoff,
            evidence={
                "abort_reason": candidate.abort_reason.value
                if candidate.abort_reason is not None
                else None,
                "invalidation": candidate.invalidation.value
                if candidate.invalidation is not None
                else None,
            },
            source=VetoSource.SWEEP,
        )
    # objektive Re-Sweep-Prüfung auf dem Sweep-TF: confirmed close jenseits des Extrems
    tfc = mtf.tf(p.sweep_timeframe)
    if tfc is None:
        na.append("v5_sweep_tf_bars")
        return None
    buy_side = sweep.side is MarketSide.BUY_SIDE
    ext = sweep.penetration_extreme
    for b in tfc.bars:
        if b.open_time <= sweep.reclaim_bar or b.close_time > cutoff:
            continue
        if (buy_side and b.close > ext) or (not buy_side and b.close < ext):
            return VetoRecord(
                veto_id=VetoId.V5,
                reason="Re-Sweep: confirmed close erneut jenseits des Sweep-Extrems",
                severity=_SEVERITY[VetoId.V5],
                timestamp=b.close_time,
                information_cutoff=cutoff,
                evidence={
                    "penetration_extreme": ext,
                    "re_sweep_close": b.close,
                    "re_sweep_bar": b.open_time.isoformat(),
                    "sweep_side": sweep.side.value,
                },
                source=VetoSource.SWEEP,
            )
    return None


# --------------------------------------------------------------------------------- V6 / V7


def _v6_data_confidence(
    mtf: MtfContext,
    candidate: SetupCandidate,
    cutoff: datetime,
    flags: set[str],
    p: VetoParams,
    na: list[str],
) -> VetoRecord | None:
    dc = mtf.data_confidence
    if dc >= p.min_data_confidence and "data_confidence_floor:V6" not in flags:
        return None
    return VetoRecord(
        veto_id=VetoId.V6,
        reason=f"data_confidence {dc:.2f} < Floor {p.min_data_confidence:.2f}",
        severity=_SEVERITY[VetoId.V6],
        timestamp=cutoff,
        information_cutoff=cutoff,
        evidence={
            "data_confidence": round(dc, 4),
            "floor": p.min_data_confidence,
            "issues": "; ".join(mtf.issues[:5]) if mtf.issues else None,
        },
        source=VetoSource.DATA_QUALITY,
    )


def _v7_execution(
    mtf: MtfContext,
    candidate: SetupCandidate,
    cutoff: datetime,
    flags: set[str],
    p: VetoParams,
    na: list[str],
) -> VetoRecord | None:
    reasons: list[str] = []
    ev: dict[str, str | float | int | bool | None] = {}

    spread = mtf.market_context.spread
    tfc = mtf.tf(p.entry_timeframe)
    atr_e = tfc.atr if tfc is not None else 0.0
    price = tfc.last_close if tfc is not None else None
    if spread is None:
        na.append("v7_spread")
        ev["spread"] = None
    else:
        ev["spread"] = spread
        if atr_e > 0.0 and spread > p.max_spread_atr * atr_e:
            reasons.append(f"Spread {spread:.5f} > {p.max_spread_atr}·ATR({atr_e:.5f})")
        if price is not None and price > 0.0 and spread / price > p.max_spread_pct:
            reasons.append(f"Spread/Preis {spread / price:.5%} > {p.max_spread_pct:.3%}")
    # Slippage / Order-Book-Tiefe: noch keine Datenquelle
    na.append("v7_slippage_estimate")
    na.append("v7_orderbook_depth")

    if tfc is not None and tfc.bars:
        age_periods = (cutoff - tfc.bars[-1].close_time).total_seconds() / p.entry_timeframe.seconds
        ev["data_age_periods"] = round(age_periods, 3)
        if age_periods > p.max_data_age_periods:
            reasons.append(f"Datenalter {age_periods:.1f} M5-Perioden > {p.max_data_age_periods}")
    else:
        na.append("v7_data_age")

    if not reasons:
        return None
    return VetoRecord(
        veto_id=VetoId.V7,
        reason="Ausführung untauglich: " + " / ".join(reasons),
        severity=_SEVERITY[VetoId.V7],
        timestamp=cutoff,
        information_cutoff=cutoff,
        evidence=ev,
        source=VetoSource.EXECUTION,
    )


# --------------------------------------------------------------------------------- V2 / V8 / V10


def _gate_vetoes(gates: GateReport | None, cutoff: datetime, na: list[str]) -> list[VetoRecord]:
    if gates is None:
        na.extend(["v2_location_gate", "v8_rr_gate", "v10_sl_geometry"])
        return []
    out: list[VetoRecord] = []
    loc = gates.location
    if loc.veto is VetoId.V2:
        out.append(
            VetoRecord(
                veto_id=VetoId.V2,
                reason=f"Location-Gate BLOCK: {loc.note}",
                severity=_SEVERITY[VetoId.V2],
                timestamp=cutoff,
                information_cutoff=cutoff,
                evidence={
                    "pd_position": loc.pd_position,
                    "swept_leg_low": loc.swept_leg[0] if loc.swept_leg is not None else None,
                    "swept_leg_high": loc.swept_leg[1] if loc.swept_leg is not None else None,
                    "note": loc.note,
                },
                source=VetoSource.LOCATION_GATE,
            )
        )
    rr = gates.rr
    if rr is None:
        na.extend(["v8_rr_gate", "v10_sl_geometry"])  # Location blockte vor der RR-Auswertung
    if rr is not None:
        g = rr.geometry
        base_ev: dict[str, str | float | int | bool | None] = {
            "rr_to_tp2": g.rr_to_tp2 if g is not None else None,
            "blended_rr": g.blended_rr if g is not None else None,
            "target_room_r": (
                None
                if g is None
                else ("inf" if g.target_room_r == float("inf") else g.target_room_r)
            ),
            "r_distance": g.r_distance if g is not None else None,
            "reasons": "; ".join(x.value for x in rr.reasons) or None,
            "note": rr.note,
        }
        if VetoId.V8 in rr.vetoes:
            out.append(
                VetoRecord(
                    veto_id=VetoId.V8,
                    reason=f"RR-Gate BLOCK: {rr.note}",
                    severity=_SEVERITY[VetoId.V8],
                    timestamp=cutoff,
                    information_cutoff=cutoff,
                    evidence=dict(base_ev),
                    source=VetoSource.RR_GATE,
                )
            )
        if VetoId.V10 in rr.vetoes:
            out.append(
                VetoRecord(
                    veto_id=VetoId.V10,
                    reason=f"Kein regelkonformer SL definierbar: {rr.note}",
                    severity=_SEVERITY[VetoId.V10],
                    timestamp=cutoff,
                    information_cutoff=cutoff,
                    evidence=dict(base_ev),
                    source=VetoSource.RR_GATE,
                )
            )
    return out


# --------------------------------------------------------------------------------- V9 Portfolio


def _v9_portfolio(
    candidate: SetupCandidate,
    portfolio: PortfolioContext | None,
    cutoff: datetime,
    p: VetoParams,
    na: list[str],
) -> list[VetoRecord]:
    if portfolio is None:
        na.append("v9_portfolio_context")
        return []

    inst = candidate.instrument
    d = candidate.direction
    threshold = portfolio.correlation_threshold

    correlated_same_dir = [
        (pos.instrument, round(portfolio.correlation(inst, pos.instrument), 4))
        for pos in portfolio.open_positions
        if pos.direction is d and portfolio.correlation(inst, pos.instrument) >= threshold
    ]
    duplicate_open = portfolio.open_direction(inst) is d
    duplicate_armed = portfolio.armed_setups.get(inst) is d

    reasons: list[str] = []
    if duplicate_open:
        reasons.append("bereits offene Position gleiche Richtung, gleiches Instrument")
    if duplicate_armed:
        reasons.append("bereits ARMED-Setup gleiche Richtung, gleiches Instrument")
    if correlated_same_dir and portfolio.cluster_open_risk_pct >= portfolio.cluster_cap_pct:
        reasons.append(
            f"Cluster-Budget erschöpft ({portfolio.cluster_open_risk_pct:.2f}% ≥ "
            f"{portfolio.cluster_cap_pct:.2f}%) bei korrelierter Exposure"
        )
    if portfolio.total_open_risk_pct >= p.portfolio_heat_cap_pct and correlated_same_dir:
        reasons.append(
            f"Portfolio-Heat {portfolio.total_open_risk_pct:.2f}% ≥ {p.portfolio_heat_cap_pct:.2f}% "
            "bei korrelierter Exposure"
        )

    if not reasons:
        return []
    return [
        VetoRecord(
            veto_id=VetoId.V9,
            reason="Portfolio/Exposure: " + " / ".join(reasons),
            severity=_SEVERITY[VetoId.V9],
            timestamp=cutoff,
            information_cutoff=cutoff,
            evidence={
                "correlated_same_direction": "; ".join(f"{i}={c}" for i, c in correlated_same_dir)
                or None,
                "correlation_threshold": threshold,
                "duplicate_open": duplicate_open,
                "duplicate_armed": duplicate_armed,
                "cluster_open_risk_pct": portfolio.cluster_open_risk_pct,
                "cluster_cap_pct": portfolio.cluster_cap_pct,
                "total_open_risk_pct": portfolio.total_open_risk_pct,
                "portfolio_heat_cap_pct": p.portfolio_heat_cap_pct,
            },
            source=VetoSource.PORTFOLIO,
        )
    ]


# --------------------------------------------------------------------------------- Korrelation


def _link_correlated(records: list[VetoRecord]) -> list[VetoRecord]:
    """Verknüpft Vetos mit derselben Wurzel (V6↔V7 Datenlage · V8↔V10 RR-Gate) für die App."""
    ids = {r.veto_id for r in records}
    pairs = {
        VetoId.V6: VetoId.V7,
        VetoId.V7: VetoId.V6,
        VetoId.V8: VetoId.V10,
        VetoId.V10: VetoId.V8,
    }
    out: list[VetoRecord] = []
    for r in records:
        partner = pairs.get(r.veto_id)
        if partner is not None and partner in ids:
            out.append(dataclasses.replace(r, correlated_with=(partner,)))
        else:
            out.append(r)
    return out


# --------------------------------------------------------------------------------- intern


def _dir_num(directional: RegimeDirectional | None) -> int:
    if directional is RegimeDirectional.TREND_UP:
        return 1
    if directional is RegimeDirectional.TREND_DOWN:
        return -1
    return 0


def _tf_directional(mtf: MtfContext, tf: Timeframe) -> RegimeDirectional | None:
    c = mtf.tf(tf)
    return c.regime.directional if c is not None else None


def _tf_time(mtf: MtfContext, tf: Timeframe, fallback: datetime) -> datetime:
    c = mtf.tf(tf)
    if c is not None and c.bars:
        return c.bars[-1].close_time
    return fallback


def _worst_volatility(mtf: MtfContext, timeframes: Sequence[Timeframe]) -> RegimeVolatility:
    order = {
        RegimeVolatility.LOW: 0,
        RegimeVolatility.NORMAL: 1,
        RegimeVolatility.HIGH: 2,
        RegimeVolatility.EXTREME: 3,
    }
    vols = [c.regime.volatility for tf in timeframes if (c := mtf.tf(tf)) is not None]
    if not vols:
        return RegimeVolatility.NORMAL
    if RegimeVolatility.EXTREME in vols:
        return RegimeVolatility.EXTREME
    if RegimeVolatility.LOW in vols:
        return RegimeVolatility.LOW
    return max(vols, key=lambda v: order[v])


def _any_coiled_compression(mtf: MtfContext, timeframes: Sequence[Timeframe]) -> bool:
    for tf in timeframes:
        c = mtf.tf(tf)
        if c is not None and c.regime.phase is RegimePhase.COMPRESSION and c.regime.coiled:
            return True
    return False


__all__ = [
    "VetoParams",
    "VetoRecord",
    "VetoReport",
    "VetoSeverity",
    "VetoSource",
    "assess_vetoes",
    "collect_vetoes",
]
