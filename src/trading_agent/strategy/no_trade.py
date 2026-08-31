"""Globale No-Trade-Checkliste (``no-trade.md``) — **erster** Schritt der Entscheidungs-Pipeline.

Eine einzige, testbare Liste aller Bedingungen, unter denen **kein** neuer Trade eröffnet wird —
**unabhängig vom Setup und vom Score**. Läuft **vor** Regime-Feinprüfung, Ketten-Gates, Vetos und
Scoring (``no-trade.md`` §1). **No-Trade-Regeln sind harte Gates** — keine Score-/Confidence-Erhöhung
hebt einen Treffer auf.

8 Gruppen: SYSTEM · DATA · REGIME · TIME/SESSION · NEWS · RISK/PORTFOLIO · STRATEGY-STATE · EXECUTION.

**Phase-3-Abdeckung:** Es werden nur die **jetzt objektiv prüfbaren** Bedingungen scharf geschaltet
(aus ``MtfContext`` / ``MarketContext`` / optionalem ``SystemState`` / ``PortfolioContext`` /
``InstrumentHistory`` / ``AccountRisk`` / Session-Specs). Gruppen, die Konto-/Broker-/Margin-State
brauchen und heute keine Eingabe haben, werden in ``not_checked`` protokolliert und blockieren
**nicht** — volle Umsetzung mit Phase 4 (``risk/``) / Phase 9+ (Broker). **Kein Fake, kein stiller
Pass.**

Alle Schwellen ``PROPOSED DEFAULT`` (``no-trade.md`` §5). Point-in-time / look-ahead-frei /
deterministisch. Long/Short-symmetrisch (alle Bedingungen richtungs-agnostisch bzw. gegen die
Kandidaten-Richtung geprüft).
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from enum import StrEnum

from trading_agent.analysis.mtf import MtfContext
from trading_agent.analysis.sessions import SessionFilterParams, session_filter
from trading_agent.core.enums import DataQualityCode, Direction, NoTradeReason, Timeframe
from trading_agent.core.time import ensure_utc
from trading_agent.core.types import PortfolioContext
from trading_agent.core.version import STRATEGY_VERSION
from trading_agent.refdata.models import SessionSpec
from trading_agent.strategy.confidence import ConfidenceReport
from trading_agent.strategy.setup_detection import SetupCandidate

_Evidence = Mapping[str, str | float | int | bool | None]
_REQUIRED_TF = (Timeframe.M5, Timeframe.M15, Timeframe.H4, Timeframe.D1)
_ALERT_REASONS = frozenset(
    {
        NoTradeReason.LOSS_STREAK_REVIEW,
        NoTradeReason.MAX_DRAWDOWN,
        NoTradeReason.RECONCILIATION_PENDING,
        NoTradeReason.UNHANDLED_ERROR_STATE,
    }
)


class NoTradeGroup(StrEnum):
    SYSTEM = "system"
    DATA = "data"
    REGIME = "regime"
    TIME = "time"
    NEWS = "news"
    RISK = "risk"
    STRATEGY_STATE = "strategy_state"
    EXECUTION = "execution"


_Add = Callable[[NoTradeReason, NoTradeGroup, str, _Evidence], None]


# --------------------------------------------------------------------------------- Eingabe-State


@dataclasses.dataclass(frozen=True, slots=True)
class SystemState:
    """[1] System/Safety — Kill-Switches & Prozess-Zustand (Phase 9+ liefert das live)."""

    kill_switch_global: bool = False
    kill_switch_broker: bool = False
    kill_switch_asset: bool = False
    kill_switch_strategy: bool = False
    kill_switch_data: bool = False
    starting_up: bool = False
    reconciliation_pending: bool = False
    unhandled_error: bool = False


@dataclasses.dataclass(frozen=True, slots=True)
class InstrumentHistory:
    """[6]/[7] — instrumentbezogene Historie für Cooldowns / Verlustserie."""

    last_stop_out: datetime | None = None
    last_sweep_fail: datetime | None = None  # letzter SWEEP_BECAME_BREAKOUT
    consecutive_losses: int = 0


@dataclasses.dataclass(frozen=True, slots=True)
class AccountRisk:
    """[6] Risk — Konto-Kennzahlen (``None`` = unbekannt ⇒ nicht geprüft). Phase 4 füllt das."""

    daily_loss_pct: float | None = None
    weekly_loss_pct: float | None = None
    drawdown_pct: float | None = None
    trades_today: int | None = None


# --------------------------------------------------------------------------------- Parameter


@dataclasses.dataclass(frozen=True, slots=True)
class NoTradeParams:
    expected_strategy_version: str = STRATEGY_VERSION
    require_all_required_timeframes: bool = True

    min_completeness: float = 0.98
    min_freshness: float = 0.50
    min_data_confidence: float = 0.50

    session: SessionFilterParams = dataclasses.field(default_factory=SessionFilterParams)
    avoid_weekend: bool = True
    market_is_24_7: bool = False  # True für Krypto — kein Wochenend-Block, kein Session-Kalender

    require_news_feed: bool = True
    pre_positioning_ban_min: float = 120.0

    max_open_positions: int = 3
    max_portfolio_heat_pct: float = 3.0
    max_daily_loss_pct: float = 3.0
    max_weekly_loss_pct: float = 6.0
    max_drawdown_pct: float = 10.0
    max_trades_today: int = 6
    loss_streak_review: int = 4

    cooldown_after_stop_bars: int = 12
    cooldown_after_sweep_fail_bars: int = 6
    cooldown_timeframe: Timeframe = Timeframe.M15

    max_spread_atr: float = 0.10
    max_spread_pct: float = 0.0005
    max_data_age_periods: float = 3.0
    entry_timeframe: Timeframe = Timeframe.M5


# --------------------------------------------------------------------------------- Ausgabe


@dataclasses.dataclass(frozen=True, slots=True)
class NoTradeRecord:
    reason: NoTradeReason
    group: NoTradeGroup
    detail: str
    evidence: _Evidence
    timestamp: datetime
    information_cutoff: datetime
    requires_alert: bool = False


@dataclasses.dataclass(frozen=True, slots=True)
class NoTradeReport:
    instrument: str
    information_cutoff: datetime
    records: tuple[NoTradeRecord, ...]
    not_checked: tuple[str, ...]  # Prüfungen ohne die nötigen Eingaben (nicht blockierend)
    strategy_version: str = STRATEGY_VERSION

    @property
    def blocked(self) -> bool:
        return bool(self.records)

    @property
    def reasons(self) -> tuple[NoTradeReason, ...]:
        return tuple(dict.fromkeys(r.reason for r in self.records))

    @property
    def requires_alert(self) -> bool:
        return any(r.requires_alert for r in self.records)

    def by_group(self, group: NoTradeGroup) -> tuple[NoTradeRecord, ...]:
        return tuple(r for r in self.records if r.group is group)


# --------------------------------------------------------------------------------- öffentlich


def assess_no_trade(
    mtf: MtfContext,
    *,
    candidate: SetupCandidate | None = None,
    confidence: ConfidenceReport | None = None,
    system: SystemState | None = None,
    portfolio: PortfolioContext | None = None,
    instrument_history: InstrumentHistory | None = None,
    account_risk: AccountRisk | None = None,
    session_specs: Sequence[SessionSpec] = (),
    now: datetime | None = None,
    params: NoTradeParams | None = None,
) -> NoTradeReport:
    """Vollständige No-Trade-Prüfung. Alle zutreffenden Gründe werden protokolliert
    (``no-trade.md`` §3.2). ``blocked = True`` ⇒ die Pipeline stoppt **vor** dem Score."""
    p = params or NoTradeParams()
    cutoff = mtf.information_cutoff
    at = ensure_utc(now) if now is not None else cutoff
    recs: list[NoTradeRecord] = []
    nc: list[str] = []

    def add(reason: NoTradeReason, group: NoTradeGroup, detail: str, ev: _Evidence) -> None:
        recs.append(
            NoTradeRecord(
                reason=reason,
                group=group,
                detail=detail,
                evidence=ev,
                timestamp=at,
                information_cutoff=cutoff,
                requires_alert=reason in _ALERT_REASONS,
            )
        )

    _check_system(add, nc, system, candidate, p)
    _check_data(add, mtf, confidence, p)
    _check_regime(add, mtf)
    _check_time(add, nc, session_specs, p, at)
    _check_news(add, mtf, p)
    _check_risk(add, nc, portfolio, account_risk, instrument_history, p)
    _check_strategy_state(add, nc, candidate, portfolio, instrument_history, p, at)
    _check_execution(add, nc, mtf, p, cutoff)

    return NoTradeReport(
        instrument=mtf.instrument,
        information_cutoff=cutoff,
        records=tuple(recs),
        not_checked=tuple(dict.fromkeys(nc)),
    )


def check_no_trade(
    mtf: MtfContext,
    *,
    candidate: SetupCandidate | None = None,
    confidence: ConfidenceReport | None = None,
    system: SystemState | None = None,
    portfolio: PortfolioContext | None = None,
    instrument_history: InstrumentHistory | None = None,
    account_risk: AccountRisk | None = None,
    session_specs: Sequence[SessionSpec] = (),
    now: datetime | None = None,
    params: NoTradeParams | None = None,
) -> tuple[NoTradeReason, ...]:
    """Kompakte API: alle zutreffenden ``NoTradeReason`` (dedupliziert)."""
    return assess_no_trade(
        mtf,
        candidate=candidate,
        confidence=confidence,
        system=system,
        portfolio=portfolio,
        instrument_history=instrument_history,
        account_risk=account_risk,
        session_specs=session_specs,
        now=now,
        params=params,
    ).reasons


# --------------------------------------------------------------------------------- Gruppen


def _check_system(
    add: _Add,
    nc: list[str],
    system: SystemState | None,
    candidate: SetupCandidate | None,
    p: NoTradeParams,
) -> None:
    if candidate is not None and candidate.strategy_version != p.expected_strategy_version:
        add(
            NoTradeReason.SETUP_VERSION_MISMATCH,
            NoTradeGroup.STRATEGY_STATE,
            "laufende Strategy-Version weicht von der freigegebenen ab",
            {"running": candidate.strategy_version, "expected": p.expected_strategy_version},
        )
    if system is None:
        nc.append("system.kill_switch")
        return
    mapping = {
        NoTradeReason.KILL_SWITCH_GLOBAL: system.kill_switch_global,
        NoTradeReason.KILL_SWITCH_BROKER: system.kill_switch_broker,
        NoTradeReason.KILL_SWITCH_ASSET: system.kill_switch_asset,
        NoTradeReason.KILL_SWITCH_STRATEGY: system.kill_switch_strategy,
        NoTradeReason.KILL_SWITCH_DATA: system.kill_switch_data,
        NoTradeReason.SYSTEM_STARTING_UP: system.starting_up,
        NoTradeReason.RECONCILIATION_PENDING: system.reconciliation_pending,
        NoTradeReason.UNHANDLED_ERROR_STATE: system.unhandled_error,
    }
    for reason, active in mapping.items():
        if active:
            add(reason, NoTradeGroup.SYSTEM, f"{reason.value} aktiv", {"active": True})


def _check_data(
    add: _Add, mtf: MtfContext, confidence: ConfidenceReport | None, p: NoTradeParams
) -> None:
    if p.require_all_required_timeframes:
        missing = [tf.value for tf in _REQUIRED_TF if tf not in mtf.per_tf]
        if missing:
            add(
                NoTradeReason.DATA_INCOMPLETE,
                NoTradeGroup.DATA,
                "benötigte Timeframes fehlen",
                {"missing": ", ".join(missing)},
            )

    code_map: dict[DataQualityCode, NoTradeReason] = {
        DataQualityCode.EMPTY_SERIES: NoTradeReason.DATA_INCOMPLETE,
        DataQualityCode.MISSING_BAR: NoTradeReason.DATA_INCOMPLETE,
        DataQualityCode.GAP: NoTradeReason.DATA_GAP_RECENT,
        DataQualityCode.DUPLICATE_BAR: NoTradeReason.DATA_DUPLICATE,
        DataQualityCode.OUT_OF_ORDER: NoTradeReason.DATA_TIMESTAMP_INVALID,
        DataQualityCode.TIMESTAMP_NOT_UTC: NoTradeReason.DATA_TIMESTAMP_INVALID,
        DataQualityCode.TIMESTAMP_MISALIGNED: NoTradeReason.DATA_TIMESTAMP_INVALID,
        DataQualityCode.TIMESTAMP_IN_FUTURE: NoTradeReason.DATA_TIMESTAMP_INVALID,
        DataQualityCode.INVALID_OHLC: NoTradeReason.DATA_PRICE_ANOMALY,
        DataQualityCode.INVALID_VOLUME: NoTradeReason.DATA_PRICE_ANOMALY,
        DataQualityCode.STALE_DATA: NoTradeReason.DATA_STALE,
        DataQualityCode.FEED_UNHEALTHY: NoTradeReason.DATA_SOURCE_UNHEALTHY,
    }
    for tf, c in mtf.per_tf.items():
        for issue in c.quality.issues:
            reason = code_map.get(issue.code)
            if reason is not None:
                add(
                    reason,
                    NoTradeGroup.DATA,
                    f"{tf.value}: {issue.message}",
                    {
                        "timeframe": tf.value,
                        "code": issue.code.value,
                        "severity": issue.severity.value,
                    },
                )
        if c.data_terms.completeness < p.min_completeness:
            add(
                NoTradeReason.DATA_INCOMPLETE,
                NoTradeGroup.DATA,
                f"{tf.value}: completeness {c.data_terms.completeness:.2f} < {p.min_completeness}",
                {"timeframe": tf.value, "completeness": round(c.data_terms.completeness, 4)},
            )
        if c.data_terms.freshness < p.min_freshness:
            add(
                NoTradeReason.DATA_STALE,
                NoTradeGroup.DATA,
                f"{tf.value}: freshness {c.data_terms.freshness:.2f} < {p.min_freshness}",
                {"timeframe": tf.value, "freshness": round(c.data_terms.freshness, 4)},
            )

    dc = confidence.data_confidence if confidence is not None else mtf.data_confidence
    if dc < p.min_data_confidence:
        add(
            NoTradeReason.DATA_CONFIDENCE_FLOOR,
            NoTradeGroup.DATA,
            f"data_confidence {dc:.2f} < {p.min_data_confidence}",
            {"data_confidence": round(dc, 4)},
        )


def _check_regime(add: _Add, mtf: MtfContext) -> None:
    gate = mtf.htf_regime_gate
    if not gate.ok and gate.reason is not None:
        add(
            gate.reason,
            NoTradeGroup.REGIME,
            f"Regime-Gate nicht bestanden: {gate.reason.value}",
            {
                "merged_directional": gate.merged_directional.value,
                "disagreement": round(gate.disagreement, 4),
            },
        )


def _check_time(
    add: _Add,
    nc: list[str],
    session_specs: Sequence[SessionSpec],
    p: NoTradeParams,
    at: datetime,
) -> None:
    if p.market_is_24_7:
        nc.append("time.session_calendar (24/7-Markt)")
        return
    if session_specs:
        reason = session_filter(at, list(session_specs), params=p.session)
        if reason is not None:
            add(
                reason,
                NoTradeGroup.TIME,
                f"Session-Filter: {reason.value}",
                {"now": at.isoformat()},
            )
        return
    if p.avoid_weekend and at.weekday() >= 5:  # 5 = Sa, 6 = So (UTC)
        add(
            NoTradeReason.WEEKEND,
            NoTradeGroup.TIME,
            "Wochenende (UTC)",
            {"weekday": at.weekday()},
        )
    else:
        nc.append("time.session_calendar")


def _check_news(add: _Add, mtf: MtfContext, p: NoTradeParams) -> None:
    news = mtf.market_context.news
    if not news.feed_available:
        if p.require_news_feed:
            add(
                NoTradeReason.NEWS_FEED_UNAVAILABLE,
                NoTradeGroup.NEWS,
                "News-Feed nicht verfügbar (fail-safe, C10)",
                {"feed_available": False},
            )
        return
    if news.blocking_event_id is not None:
        add(
            NoTradeReason.NEWS_BLACKOUT_HIGH,
            NoTradeGroup.NEWS,
            "blockierendes HIGH-Impact-Event",
            {"blocking_event_id": news.blocking_event_id},
        )
    if news.risk_off:
        add(
            NoTradeReason.NEWS_RISK_OFF_FLAG,
            NoTradeGroup.NEWS,
            "News-risk_off aktiv",
            {"risk_off": True},
        )
    m = news.minutes_to_next_high_impact
    if m is not None and m < p.pre_positioning_ban_min:
        add(
            NoTradeReason.NEWS_PRE_POSITIONING_BAN,
            NoTradeGroup.NEWS,
            f"{m:.0f} min bis HIGH-Impact-Event < {p.pre_positioning_ban_min:.0f}",
            {"minutes_to_next_high_impact": m},
        )


def _check_risk(
    add: _Add,
    nc: list[str],
    portfolio: PortfolioContext | None,
    account: AccountRisk | None,
    history: InstrumentHistory | None,
    p: NoTradeParams,
) -> None:
    if portfolio is not None:
        if len(portfolio.open_positions) >= p.max_open_positions:
            add(
                NoTradeReason.MAX_OPEN_POSITIONS,
                NoTradeGroup.RISK,
                f"{len(portfolio.open_positions)} offene Positionen ≥ {p.max_open_positions}",
                {"open_positions": len(portfolio.open_positions)},
            )
        if portfolio.total_open_risk_pct >= p.max_portfolio_heat_pct:
            add(
                NoTradeReason.PORTFOLIO_HEAT,
                NoTradeGroup.RISK,
                f"Portfolio-Heat {portfolio.total_open_risk_pct:.2f}% ≥ {p.max_portfolio_heat_pct}%",
                {"total_open_risk_pct": portfolio.total_open_risk_pct},
            )
        if portfolio.cluster_open_risk_pct >= portfolio.cluster_cap_pct:
            add(
                NoTradeReason.MAX_CORRELATED_EXPOSURE,
                NoTradeGroup.RISK,
                f"Cluster-Risiko {portfolio.cluster_open_risk_pct:.2f}% ≥ "
                f"Cap {portfolio.cluster_cap_pct:.2f}%",
                {"cluster_open_risk_pct": portfolio.cluster_open_risk_pct},
            )
    if account is not None:
        acc_map = (
            (
                account.daily_loss_pct,
                p.max_daily_loss_pct,
                NoTradeReason.DAILY_LOSS_LIMIT,
                "Tagesverlust",
            ),
            (
                account.weekly_loss_pct,
                p.max_weekly_loss_pct,
                NoTradeReason.WEEKLY_LOSS_LIMIT,
                "Wochenverlust",
            ),
            (account.drawdown_pct, p.max_drawdown_pct, NoTradeReason.MAX_DRAWDOWN, "Drawdown"),
        )
        for val, limit, reason, label in acc_map:
            if val is not None and val >= limit:
                add(
                    reason,
                    NoTradeGroup.RISK,
                    f"{label} {val:.2f}% ≥ {limit}%",
                    {"value": val, "limit": limit},
                )
        if account.trades_today is not None and account.trades_today >= p.max_trades_today:
            add(
                NoTradeReason.MAX_TRADES_TODAY,
                NoTradeGroup.RISK,
                f"{account.trades_today} Trades heute ≥ {p.max_trades_today}",
                {"trades_today": account.trades_today},
            )
    if portfolio is None and account is None:
        nc.append("risk.account_state")

    losses = history.consecutive_losses if history is not None else 0
    if losses >= p.loss_streak_review:
        add(
            NoTradeReason.LOSS_STREAK_REVIEW,
            NoTradeGroup.RISK,
            f"{losses} Verluste in Folge ≥ {p.loss_streak_review} — manuelle Freigabe nötig",
            {"consecutive_losses": losses},
        )
    if history is None:
        nc.append("risk.loss_streak")


def _check_strategy_state(
    add: _Add,
    nc: list[str],
    candidate: SetupCandidate | None,
    portfolio: PortfolioContext | None,
    history: InstrumentHistory | None,
    p: NoTradeParams,
    at: datetime,
) -> None:
    if candidate is None:
        nc.append("strategy_state.candidate")
        return
    d = candidate.direction
    if portfolio is not None:
        open_dir = portfolio.open_direction(candidate.instrument)
        if open_dir is d:
            add(
                NoTradeReason.DUPLICATE_POSITION,
                NoTradeGroup.STRATEGY_STATE,
                "offene Position gleiche Richtung, gleiches Instrument",
                {"open_direction": d.value},
            )
        elif open_dir is _opposite(d):
            add(
                NoTradeReason.OPPOSITE_POSITION_OPEN,
                NoTradeGroup.STRATEGY_STATE,
                "offene Position gegen die neue Richtung (kein Hedging im MVP)",
                {"open_direction": _opposite(d).value},
            )
        if portfolio.armed_setups.get(candidate.instrument) is d:
            add(
                NoTradeReason.DUPLICATE_ARMED_SETUP,
                NoTradeGroup.STRATEGY_STATE,
                "bereits ARMED-Setup gleiche Richtung, gleiches Instrument",
                {"direction": d.value},
            )
    else:
        nc.append("strategy_state.portfolio")

    if history is not None:
        delta = p.cooldown_timeframe.seconds
        if history.last_stop_out is not None:
            bars = (at - ensure_utc(history.last_stop_out)).total_seconds() / delta
            if bars < p.cooldown_after_stop_bars:
                add(
                    NoTradeReason.COOLDOWN_AFTER_STOP,
                    NoTradeGroup.STRATEGY_STATE,
                    f"letzter Stop-Out {bars:.1f} M15-Bars her < {p.cooldown_after_stop_bars}",
                    {"bars_since_stop": round(bars, 2)},
                )
        if history.last_sweep_fail is not None:
            bars = (at - ensure_utc(history.last_sweep_fail)).total_seconds() / delta
            if bars < p.cooldown_after_sweep_fail_bars:
                add(
                    NoTradeReason.COOLDOWN_AFTER_SWEEP_FAIL,
                    NoTradeGroup.STRATEGY_STATE,
                    f"letzter Sweep-Fehlversuch {bars:.1f} Bars her < "
                    f"{p.cooldown_after_sweep_fail_bars}",
                    {"bars_since_sweep_fail": round(bars, 2)},
                )
    else:
        nc.append("strategy_state.cooldowns")


def _check_execution(
    add: _Add, nc: list[str], mtf: MtfContext, p: NoTradeParams, cutoff: datetime
) -> None:
    tfc = mtf.tf(p.entry_timeframe)
    spread = mtf.market_context.spread
    if spread is None:
        nc.append("execution.spread")
    elif tfc is not None:
        atr_e = tfc.atr
        price = tfc.last_close
        if atr_e > 0.0 and spread > p.max_spread_atr * atr_e:
            add(
                NoTradeReason.SPREAD_TOO_WIDE,
                NoTradeGroup.EXECUTION,
                f"Spread {spread:.5f} > {p.max_spread_atr}·ATR",
                {"spread": spread, "atr": atr_e},
            )
        elif price is not None and price > 0.0 and spread / price > p.max_spread_pct:
            add(
                NoTradeReason.SPREAD_TOO_WIDE,
                NoTradeGroup.EXECUTION,
                f"Spread/Preis {spread / price:.5%} > {p.max_spread_pct:.3%}",
                {"spread_pct": spread / price},
            )
    nc.append("execution.slippage")
    nc.append("execution.orderbook_depth")

    if tfc is not None and tfc.bars:
        age = (cutoff - tfc.bars[-1].close_time).total_seconds() / p.entry_timeframe.seconds
        if age > p.max_data_age_periods:
            add(
                NoTradeReason.DATA_AGE_EXECUTION,
                NoTradeGroup.EXECUTION,
                f"Datenalter {age:.1f} M5-Perioden > {p.max_data_age_periods}",
                {"data_age_periods": round(age, 3)},
            )


# --------------------------------------------------------------------------------- intern


def _opposite(d: Direction) -> Direction:
    return Direction.SHORT if d is Direction.LONG else Direction.LONG


__all__ = [
    "AccountRisk",
    "InstrumentHistory",
    "NoTradeGroup",
    "NoTradeParams",
    "NoTradeRecord",
    "NoTradeReport",
    "SystemState",
    "assess_no_trade",
    "check_no_trade",
]
