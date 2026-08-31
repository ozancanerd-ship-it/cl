"""``strategy.evaluate`` — der zentrale Orchestrator (``SPEC-ADDENDUM-0.1.1.md`` §1.2).

Verkettet die (bereits einzeln getesteten) Bausteine zu **einer** nachvollziehbaren Entscheidung:

```
No-Trade → Regime → MTF → Setup-FSM → Veto → Setup-State (WAIT?) → Location → RR
        → Confirmation → Confluence → Contradictions → Confidence → Score → Portfolio → Decision
```

Ergebnis: ``Decision`` ∈ {``BUY``, ``SELL``, ``WAIT``, ``NO_TRADE``} plus ein ``EvaluationResult``,
der **alle** Zwischen-Reports hält — damit die spätere UI *„warum BUY / SELL / WAIT / NO_TRADE?"*
vollständig aufklappen kann.

**Reine Delegation** — keine neue Analyse-Logik. Point-in-time / look-ahead-frei / deterministisch:
alles hängt am ``MarketContext.information_cutoff``. Long/Short-symmetrisch (die Bausteine sind es).

**MVP-Entry-Modus** = ``limit_at_proximal_edge`` (``SMC-SWEEP-REV-01`` §9). Der
``confirmation_market``-Modus (mit *„weiter WAIT bis Confirmation"*, ``SPEC-ADDENDUM`` §2.5) braucht
nativen M1-Feed **und** eine Lockerung der ``Decision``-Invariante (WAIT nach ARMED) — Backlog.

**Kein Broker-Code.** Der Orchestrator kennt weder Kraken/Bybit/MT5 noch TradingView.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Sequence
from datetime import datetime

from trading_agent.analysis.mtf import MtfContext, MtfParams, build_mtf_context
from trading_agent.analysis.sessions import active_session_names
from trading_agent.core.enums import (
    AssetClass,
    Direction,
    EntryMode,
    NoTradeReason,
    RiskTier,
    SessionName,
    SetupState,
    Timeframe,
)
from trading_agent.core.types import MarketContext, PortfolioContext
from trading_agent.core.version import STRATEGY_VERSION
from trading_agent.refdata.models import SessionSpec
from trading_agent.strategy.confidence import (
    ConfidenceParams,
    ConfidenceReport,
    assess_confidence,
)
from trading_agent.strategy.confluence import (
    ConfluenceParams,
    ConfluenceReport,
    assess_confluence,
)
from trading_agent.strategy.contradictions import (
    ContradictionParams,
    ContradictionReport,
    assess_contradictions,
)
from trading_agent.strategy.decision import Decision
from trading_agent.strategy.gates import (
    GateOutcome,
    GateParams,
    GateReport,
    evaluate_gates,
)
from trading_agent.strategy.no_trade import (
    AccountRisk,
    InstrumentHistory,
    NoTradeGroup,
    NoTradeParams,
    NoTradeReport,
    SystemState,
    assess_no_trade,
)
from trading_agent.strategy.price_action import (
    ConfirmationParams,
    ConfirmationScan,
    confirmation_for_candidate,
)
from trading_agent.strategy.scoring import ScoreParams, ScoreReport, score_setup
from trading_agent.strategy.setup_detection import (
    SetupCandidate,
    SetupParams,
    SetupScan,
    detect_setups,
)
from trading_agent.strategy.setups.breakout_retest import (
    SETUP_BREAKOUT_RETEST,
    BreakoutRetestParams,
    BreakoutRetestReport,
    detect_breakout_retest,
)
from trading_agent.strategy.veto import VetoParams, VetoReport, assess_vetoes

_HIGHER = (Timeframe.M15, Timeframe.H4, Timeframe.D1)


# --------------------------------------------------------------------------------- Parameter


@dataclasses.dataclass(frozen=True, slots=True)
class EvaluateParams:
    asset_class: AssetClass = AssetClass.CRYPTO
    entry_mode: EntryMode = EntryMode.LIMIT_AT_PROXIMAL_EDGE
    mtf: MtfParams = dataclasses.field(default_factory=MtfParams)
    setup: SetupParams = dataclasses.field(default_factory=SetupParams)
    no_trade: NoTradeParams = dataclasses.field(default_factory=NoTradeParams)
    veto: VetoParams = dataclasses.field(default_factory=VetoParams)
    gates: GateParams = dataclasses.field(default_factory=GateParams)
    confirmation: ConfirmationParams = dataclasses.field(default_factory=ConfirmationParams)
    confluence: ConfluenceParams = dataclasses.field(default_factory=ConfluenceParams)
    contradictions: ContradictionParams = dataclasses.field(default_factory=ContradictionParams)
    confidence: ConfidenceParams = dataclasses.field(default_factory=ConfidenceParams)
    scoring: ScoreParams = dataclasses.field(default_factory=ScoreParams)
    # 2. Setup-Typ (SETUP-BREAKOUT-RETEST-01). Läuft parallel zur SMC-Kette; greift nur, wenn
    # SMC NICHT actionable ist. Standardmäßig AN — die Governance (ValidationRegistry) hält es
    # als SHADOW, solange nicht validiert.
    breakout_enabled: bool = True
    breakout: BreakoutRetestParams = dataclasses.field(default_factory=BreakoutRetestParams)

    def __post_init__(self) -> None:
        # 24/7-Märkte (Krypto + Altcoins): kein Wochenend-Block / Session-Kalender im No-Trade-Gate.
        if (
            self.asset_class in (AssetClass.CRYPTO, AssetClass.ALTCOIN)
            and not self.no_trade.market_is_24_7
        ):
            object.__setattr__(
                self,
                "no_trade",
                dataclasses.replace(self.no_trade, market_is_24_7=True),
            )


# --------------------------------------------------------------------------------- Ausgabe


@dataclasses.dataclass(frozen=True, slots=True)
class EvaluationResult:
    """Die ``Decision`` + **alle** Zwischen-Reports (Explainability-Container).

    ``live_gate`` (optional): die **Freigabe**-Entscheidung, getrennt von der Strategie-
    Entscheidung — gesetzt von ``governance.apply_live_gate``. Ohne sie ist das Ergebnis eine
    reine Analyse (Backtest/Research); ein *actionable Live-Signal* verlangt ``live_gate.is_live``.
    """

    decision: Decision
    mtf: MtfContext
    scan: SetupScan
    no_trade: NoTradeReport
    candidate: SetupCandidate | None = None
    veto: VetoReport | None = None
    gates: GateReport | None = None
    confirmation: ConfirmationScan | None = None
    confluence: ConfluenceReport | None = None
    contradictions: ContradictionReport | None = None
    confidence: ConfidenceReport | None = None
    score: ScoreReport | None = None
    breakout: BreakoutRetestReport | None = None  # 2. Setup-Typ — Report (auch wenn nicht ARMED)
    live_gate: object = (
        None  # governance.LiveGateReport | None (spät gesetzt, Import-Zyklus vermeiden)
    )
    strategy_version: str = STRATEGY_VERSION

    @property
    def is_actionable(self) -> bool:
        """Strategie-Verdikt: der Live-Markt zeigt JETZT ein gültiges ARMED-Setup."""
        return self.decision.is_actionable

    @property
    def is_actionable_live(self) -> bool:
        """Freigabe-Verdikt: actionable **und** das Setup ist für Live-Signale validiert."""
        return self.decision.is_actionable and bool(getattr(self.live_gate, "is_live", False))


# --------------------------------------------------------------------------------- öffentlich


def evaluate(
    market_context: MarketContext,
    *,
    portfolio_context: PortfolioContext | None = None,
    m1_bars: Sequence[object] = (),
    session_specs: Sequence[SessionSpec] = (),
    system: SystemState | None = None,
    instrument_history: InstrumentHistory | None = None,
    account_risk: AccountRisk | None = None,
    params: EvaluateParams | None = None,
    mtf_cache: dict[tuple[object, ...], object] | None = None,
) -> EvaluationResult:
    """Vollständige Entscheidungs-Pipeline für **einen** ``MarketContext``. Deterministisch,
    look-ahead-frei (alles ≤ ``information_cutoff``).

    ``mtf_cache`` (optional, vom Aufrufer über mehrere Ticks gehalten): memoisiert die höheren
    TF-Analysen im MTF-Bau. Reine Beschleunigung, kein Verhaltensunterschied."""
    p = params or EvaluateParams()
    mtf = _build_mtf(market_context, p, mtf_cache)
    return evaluate_from_mtf(
        mtf,
        spread=market_context.spread,
        portfolio_context=portfolio_context,
        m1_bars=m1_bars,
        session_specs=session_specs,
        system=system,
        instrument_history=instrument_history,
        account_risk=account_risk,
        params=p,
    )


def evaluate_from_mtf(
    mtf: MtfContext,
    *,
    spread: float | None = None,
    portfolio_context: PortfolioContext | None = None,
    m1_bars: Sequence[object] = (),
    session_specs: Sequence[SessionSpec] = (),
    system: SystemState | None = None,
    instrument_history: InstrumentHistory | None = None,
    account_risk: AccountRisk | None = None,
    params: EvaluateParams | None = None,
) -> EvaluationResult:
    """Zentrale Pipeline auf einem bereits gebauten ``MtfContext``.

    Fährt die **SMC-SWEEP-REV-01-Kette** (``_evaluate_smc``) und — wenn diese *nicht* actionable
    ist und die globale No-Trade-Checkliste frei ist — parallel den **2. Setup-Typ**
    ``SETUP-BREAKOUT-RETEST-01``. Ein ARMED Breakout-Retest mit gültiger Geometrie + RR ersetzt
    dann die SMC-Entscheidung. Die SMC-Kette selbst bleibt unverändert.
    """
    p = params or EvaluateParams()
    smc = _evaluate_smc(
        mtf,
        spread=spread,
        portfolio_context=portfolio_context,
        m1_bars=m1_bars,
        session_specs=session_specs,
        system=system,
        instrument_history=instrument_history,
        account_risk=account_risk,
        params=p,
    )
    if not p.breakout_enabled or smc.decision.is_actionable or smc.no_trade.blocked:
        return smc
    bo = detect_breakout_retest(smc.mtf, params=p.breakout)
    if not bo.is_armed:
        return dataclasses.replace(smc, breakout=bo)
    return _breakout_decision(smc, bo, p)


def _breakout_decision(
    smc: EvaluationResult, bo: BreakoutRetestReport, p: EvaluateParams
) -> EvaluationResult:
    """Baut aus einem ARMED ``BreakoutRetestReport`` eine ``Decision.trade`` — leichter Pfad
    ohne die SMC-eigenen Location/Confluence-Gates (Breakout hat eigene Geometrie + RR-Prüfung)."""
    assert bo.direction is not None and bo.entry is not None
    from trading_agent.core.enums import RiskTier

    dec = Decision.trade(
        bo.instrument,
        bo.information_cutoff,
        bo.direction,
        entry=float(bo.entry),
        sl=float(bo.sl),  # type: ignore[arg-type]
        tp1=float(bo.tp1),  # type: ignore[arg-type]
        tp2=float(bo.tp2),  # type: ignore[arg-type]
        tier=RiskTier.B,  # Breakout-Retest gibt (noch) kein A/A+-Tier — konservativ B
        tp3_ref=bo.tp3_ref,
        rr_to_tp2=bo.rr_to_tp2,
        blended_rr=bo.blended_rr,
        score=None,
        confidence=bo.confidence,
        chain_progress=bo.chain_progress,
        setup_id=SETUP_BREAKOUT_RETEST,
        context_ref={
            "setup_type": SETUP_BREAKOUT_RETEST,
            "d1_trend": bo.d1_trend.value,
            "broken_level": bo.broken_level,
            "breakout_bar": bo.breakout_bar.isoformat() if bo.breakout_bar else None,
            "retest_bar": bo.retest_bar.isoformat() if bo.retest_bar else None,
        },
    )
    return dataclasses.replace(smc, decision=dec, breakout=bo)


def _evaluate_smc(
    mtf: MtfContext,
    *,
    spread: float | None = None,
    portfolio_context: PortfolioContext | None = None,
    m1_bars: Sequence[object] = (),
    session_specs: Sequence[SessionSpec] = (),
    system: SystemState | None = None,
    instrument_history: InstrumentHistory | None = None,
    account_risk: AccountRisk | None = None,
    params: EvaluateParams | None = None,
) -> EvaluationResult:
    """Die SMC-SWEEP-REV-01-Kette (unverändert). ``spread`` ergänzt/überschreibt
    ``mtf.market_context.spread`` für die Ausführungs-Gates."""
    p = params or EvaluateParams()
    inst = mtf.instrument
    cutoff = mtf.information_cutoff
    if spread is not None and mtf.market_context.spread is None:
        mtf = dataclasses.replace(
            mtf,
            market_context=dataclasses.replace(mtf.market_context, spread=spread),
        )
    sessions = _active_sessions(session_specs, cutoff)

    # --- Schritt 0: globale No-Trade-Checkliste (ohne Kandidat) -----------------------
    nt_global = assess_no_trade(
        mtf,
        confidence=None,
        system=system,
        portfolio=portfolio_context,
        account_risk=account_risk,
        session_specs=session_specs,
        now=cutoff,
        params=p.no_trade,
    )

    # --- Schritt 1–3: Setup-FSM ------------------------------------------------------
    scan = detect_setups(mtf, params=p.setup)

    if nt_global.blocked:
        return _wrap(
            _no_trade(inst, cutoff, nt_global.reasons, SetupState.SCANNING, chain="No-Trade-Gate"),
            mtf,
            scan,
            nt_global,
        )

    cand = scan.primary
    if cand is None:
        if scan.no_trade_reason is not None:
            return _wrap(
                _no_trade(inst, cutoff, (scan.no_trade_reason,), SetupState.SCANNING),
                mtf,
                scan,
                nt_global,
            )
        return _wrap(
            Decision.wait(
                inst,
                cutoff,
                SetupState.BIAS_SET,
                chain_progress="Bias steht, keine qualifizierende Liquidität",
            ),
            mtf,
            scan,
            nt_global,
        )

    d = cand.direction

    # --- kandidatenspezifische No-Trade (STRATEGY-STATE: Duplikat / Cooldown) -------
    nt_cand = assess_no_trade(
        mtf,
        candidate=cand,
        portfolio=portfolio_context,
        instrument_history=instrument_history,
        session_specs=session_specs,
        now=cutoff,
        params=p.no_trade,
    )
    ss_reasons = tuple(r.reason for r in nt_cand.by_group(NoTradeGroup.STRATEGY_STATE))
    if ss_reasons:
        return _wrap(
            _no_trade(inst, cutoff, ss_reasons, cand.state, d, cand.chain_progress),
            mtf,
            scan,
            nt_global,
            candidate=cand,
        )

    # --- Schritt 4: Vetos ----------------------------------------------------------
    gates = _run_gates(mtf, cand, p) if cand.is_armed else None
    veto = assess_vetoes(mtf, cand, gates=gates, portfolio_context=portfolio_context, params=p.veto)
    if veto.blocking:
        return _wrap(
            Decision.no_trade(
                inst,
                cutoff,
                (),
                setup_state=cand.state,
                direction=d,
                vetoes=veto.veto_ids,
                chain_progress=cand.chain_progress,
                context_ref={"veto_records": len(veto.records)},
            ),
            mtf,
            scan,
            nt_global,
            candidate=cand,
            veto=veto,
            gates=gates,
        )

    # --- Schritt 5: abgebrochene Kette / invalidierter Kandidat -------------------
    if cand.abort_reason is not None:
        return _wrap(
            _no_trade(
                inst, cutoff, (cand.abort_reason,), SetupState.SCANNING, d, cand.chain_progress
            ),
            mtf,
            scan,
            nt_global,
            candidate=cand,
            veto=veto,
        )
    if cand.invalidation is not None:
        return _wrap(
            _no_trade(
                inst, cutoff, (cand.invalidation,), SetupState.SCANNING, d, cand.chain_progress
            ),
            mtf,
            scan,
            nt_global,
            candidate=cand,
            veto=veto,
        )

    # --- Schritt 6: Kette lebt, aber < ARMED  ⇒  WAIT ----------------------------
    if cand.state.is_forming:
        return _wrap(
            Decision.wait(
                inst,
                cutoff,
                cand.state,
                direction=d,
                chain_progress=cand.chain_progress,
                context_ref={"htf_directional": mtf.htf_directional.value},
            ),
            mtf,
            scan,
            nt_global,
            candidate=cand,
            veto=veto,
        )

    # --- Schritt 7: ARMED — Ketten-Gates ---------------------------------------
    if gates is None:  # defensiv (is_armed war True)
        gates = _run_gates(mtf, cand, p)
    if gates.outcome is GateOutcome.WAIT:
        # ARMED ⇒ kein WAIT-Output (SPEC-ADDENDUM §1.4) — Geometrie (noch) nicht bestimmbar
        return _wrap(
            _no_trade(
                inst,
                cutoff,
                gates.reasons or (NoTradeReason.SL_TOO_WIDE,),
                cand.state,
                d,
                cand.chain_progress + " | Gates WAIT",
            ),
            mtf,
            scan,
            nt_global,
            candidate=cand,
            veto=veto,
            gates=gates,
        )
    if gates.outcome is GateOutcome.BLOCK:
        return _wrap(
            Decision.no_trade(
                inst,
                cutoff,
                gates.reasons,
                setup_state=cand.state,
                direction=d,
                vetoes=gates.vetoes,
                chain_progress=cand.chain_progress,
            ),
            mtf,
            scan,
            nt_global,
            candidate=cand,
            veto=veto,
            gates=gates,
        )
    geom = gates.geometry
    assert geom is not None

    # --- Confirmation --------------------------------------------------------
    # MVP-Modus limit_at_proximal_edge: Confirmation ist **Kontext** (fließt in Confluence /
    # Confidence), blockt aber nicht. Der confirmation_market-Modus (§2.5: „weiter WAIT bis
    # Confirmation") braucht nativen M1-Feed + eine Lockerung der Decision-Invariante (WAIT nach
    # ARMED) — Backlog (docs/CALIBRATION_BACKLOG.md).
    confirmation: ConfirmationScan | None = None
    if m1_bars:
        confirmation = confirmation_for_candidate(mtf, cand, m1_bars, params=p.confirmation)  # type: ignore[arg-type]

    # --- Confluence -----------------------------------------------------------
    confluence = assess_confluence(
        mtf,
        cand,
        gates=gates,
        confirmation=confirmation,
        session_names=sessions,
        params=p.confluence,
    )

    # --- Contradiction-Matrix (harte matrix-eigene Ausgänge) -----------------
    contra = assess_contradictions(
        mtf,
        cand,
        confluence=confluence,
        gates=gates,
        veto=veto,
        scan=scan,
        params=p.contradictions,
    )
    if contra.blocked:
        return _wrap(
            _no_trade(inst, cutoff, contra.hard_reasons, cand.state, d, cand.chain_progress),
            mtf,
            scan,
            nt_global,
            candidate=cand,
            veto=veto,
            gates=gates,
            confirmation=confirmation,
            confluence=confluence,
            contradictions=contra,
        )

    # --- Confidence ---------------------------------------------------------
    confidence = assess_confidence(mtf, cand, confirmation=confirmation, params=p.confidence)
    conf_reasons: list[NoTradeReason] = []
    if confidence.blocks_data:
        conf_reasons.append(NoTradeReason.DATA_CONFIDENCE_FLOOR)
    if confidence.blocks_setup or confidence.unconfirmed_swing:
        conf_reasons.append(NoTradeReason.CONFIDENCE_BELOW_MIN)
    if conf_reasons:
        return _wrap(
            _no_trade(inst, cutoff, tuple(conf_reasons), cand.state, d, cand.chain_progress),
            mtf,
            scan,
            nt_global,
            candidate=cand,
            veto=veto,
            gates=gates,
            confirmation=confirmation,
            confluence=confluence,
            contradictions=contra,
            confidence=confidence,
        )

    # --- Score + Tier ----------------------------------------------------
    score = score_setup(
        mtf,
        cand,
        confluence=confluence,
        confidence=confidence,
        gates=gates,
        vetoed=False,
        params=p.scoring,
    )
    if score.tier is RiskTier.NO_TRADE:
        return _wrap(
            _no_trade(
                inst, cutoff, (NoTradeReason.SCORE_BELOW_B,), cand.state, d, cand.chain_progress
            ),
            mtf,
            scan,
            nt_global,
            candidate=cand,
            veto=veto,
            gates=gates,
            confirmation=confirmation,
            confluence=confluence,
            contradictions=contra,
            confidence=confidence,
            score=score,
        )

    # --- Portfolio-Constraints (C9) — schon von No-Trade / Veto / Contradictions
    #     abgedeckt; hier nur der Vollständigkeit halber der Slot.

    # --- BUY / SELL -----------------------------------------------------
    dec = Decision.trade(
        inst,
        cutoff,
        d,
        entry=geom.entry,
        sl=geom.sl,
        tp1=geom.tp1,
        tp2=geom.tp2,
        tier=score.tier,
        tp3_ref=geom.tp3_ref,
        rr_to_tp2=geom.rr_to_tp2,
        blended_rr=geom.blended_rr,
        score=score.final_score,
        confidence=confidence.setup_confidence,
        chain_progress=cand.chain_progress,
        score_detail=_score_detail(score),
        confidence_detail=_confidence_detail(confidence),
        context_ref=_context_ref(mtf, cand, confluence, contra),
    )
    return _wrap(
        dec,
        mtf,
        scan,
        nt_global,
        candidate=cand,
        veto=veto,
        gates=gates,
        confirmation=confirmation,
        confluence=confluence,
        contradictions=contra,
        confidence=confidence,
        score=score,
    )


def decide(market_context: MarketContext, **kw: object) -> Decision:
    """Dünne API: nur die ``Decision`` (der volle Kontext steht in ``evaluate(...)``)."""
    return evaluate(market_context, **kw).decision  # type: ignore[arg-type]


# --------------------------------------------------------------------------------- intern


def _build_mtf(
    mc: MarketContext,
    p: EvaluateParams,
    mtf_cache: dict[tuple[object, ...], object] | None = None,
) -> MtfContext:
    m5 = list(mc.series.get(Timeframe.M5, ()))
    native = {tf: list(mc.series[tf]) for tf in _HIGHER if mc.series.get(tf)}
    return build_mtf_context(
        m5,
        instrument=mc.instrument,
        asset_class=p.asset_class,
        now=mc.information_cutoff,
        native_higher=native or None,
        spread=mc.spread,
        account_equity=mc.account_equity,
        derivatives=mc.derivatives,
        cross_asset=mc.cross_asset,
        news=mc.news,
        params=p.mtf,
        analysis_cache=mtf_cache,  # type: ignore[arg-type]
    )


def _run_gates(mtf: MtfContext, cand: SetupCandidate, p: EvaluateParams) -> GateReport:
    gp = (
        p.gates
        if p.gates.entry_mode is p.entry_mode
        else dataclasses.replace(p.gates, entry_mode=p.entry_mode)
    )
    return evaluate_gates(mtf, cand, spread=mtf.market_context.spread, params=gp)


def _active_sessions(specs: Sequence[SessionSpec], now: datetime) -> set[SessionName] | None:
    if not specs:
        return None
    return active_session_names(list(specs), now)


def _no_trade(
    inst: str,
    cutoff: datetime,
    reasons: Sequence[NoTradeReason],
    state: SetupState,
    direction: Direction | None = None,
    chain: str = "",
) -> Decision:
    return Decision.no_trade(
        inst, cutoff, reasons, setup_state=state, direction=direction, chain_progress=chain
    )


def _wrap(
    decision: Decision, mtf: MtfContext, scan: SetupScan, nt: NoTradeReport, **kw: object
) -> EvaluationResult:
    cand = kw.get("candidate")
    if isinstance(cand, SetupCandidate) and decision.setup_id == "SMC-SWEEP-REV-01":
        decision = dataclasses.replace(decision, setup_id=cand.setup_id)
    return EvaluationResult(decision=decision, mtf=mtf, scan=scan, no_trade=nt, **kw)  # type: ignore[arg-type]


def _score_detail(s: ScoreReport) -> dict[str, object]:
    return {
        "final_score": s.final_score,
        "score_0_100": s.score_0_100,
        "raw": s.raw,
        "weight_sum": s.weight_sum,
        "penalties_total": s.penalties_total,
        "tier": s.tier.value,
        "tier_reason": s.tier_reason,
        "factors": [
            {"name": f.name, "value": f.value, "weight": f.weight, "available": f.available}
            for f in s.factors
        ],
        "correlated_factor_groups": {k: list(v) for k, v in s.correlated_factor_groups.items()},
    }


def _confidence_detail(c: ConfidenceReport) -> dict[str, object]:
    return {
        "data_confidence": c.data.value,
        "analysis_confidence": c.analysis.value,
        "setup_confidence": c.setup_confidence,
        "floor_penalty_applied": c.floor_penalty_applied,
        "limiting_factor": c.limiting_factor,
        "data_terms": dict(c.data.terms),
        "analysis_terms": dict(c.analysis.terms),
        "unconfirmed_swing": c.unconfirmed_swing,
    }


def _context_ref(
    mtf: MtfContext,
    cand: SetupCandidate,
    confluence: ConfluenceReport,
    contra: ContradictionReport,
) -> dict[str, object]:
    return {
        "setup_id": cand.setup_id,
        "revision": cand.revision,
        "htf_directional": mtf.htf_directional.value,
        "htf_bias": mtf.htf_bias.value,
        "regime_ok": mtf.regime_ok,
        "data_confidence": round(mtf.data_confidence, 4),
        "chain_progress": cand.chain_progress,
        "confluence_net": confluence.net_confluence,
        "confluence_support": confluence.support_score,
        "confluence_agreement": confluence.agreement,
        "contradiction_flags": list(confluence.contradiction_flags),
        "contradiction_penalties": contra.negative_penalties,
        "veto_echoes": [v.value for v in contra.veto_echoes],
        "mtf_issues": list(mtf.issues[:8]),
        "sweep_bar": cand.sweep.penetration_bar.isoformat() if cand.sweep else None,
        "displacement_atr": round(cand.displacement.net_move_atr, 3) if cand.displacement else None,
        "structure_break": cand.structure_break.kind.value if cand.structure_break else None,
    }


__all__ = [
    "EvaluateParams",
    "EvaluationResult",
    "decide",
    "evaluate",
]
