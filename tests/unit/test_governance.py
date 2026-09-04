"""governance — Data-Roles-Prinzip: Historical=Validierung, Live=Entscheidung, Recent=Edge-Check.

Prüft ``ValidationRegistry`` (Freigabe-Autorität), ``assess_edge_health`` (Recent-Check) und
``evaluate_live_gate`` / ``apply_live_gate`` (SHADOW vs LIVE vs BLOCKED).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from trading_agent.governance import (
    BaselineMetrics,
    EdgeHealth,
    LiveEligibility,
    ValidationRegistry,
    ValidationStatus,
    assess_edge_health,
    evaluate_live_gate,
)
from trading_agent.governance.validation import SetupValidation
from trading_agent.journal.ledger import TradeRecord

_T0 = datetime(2026, 6, 1, tzinfo=UTC)


def _trades(rs: list[float]) -> list[TradeRecord]:
    out: list[TradeRecord] = []
    for i, r in enumerate(rs):
        t = _T0 + timedelta(days=i)
        out.append(
            TradeRecord(
                trade_id=f"t{i}",
                instrument="XAUUSDT",
                direction="buy",  # type: ignore[arg-type]
                setup_id="S",
                signal_ts=t,
                information_cutoff=t,
                entry_ts=t,
                entry_price=100.0,
                qty=1.0,
                exit_ts=t + timedelta(hours=4),
                exit_price=100.0 + r,
                exit_reason="target" if r > 0 else "stop",
                gross_r=r,
                realized_r=r,
                pnl_ccy=r,
                win_loss="WIN" if r > 0 else "LOSS",
            )
        )
    return out


# --------------------------------------------------------------------------- registry


def test_unknown_setup_defaults_to_unvalidated_and_not_live() -> None:
    reg = ValidationRegistry.default()
    # unbekanntes Setup → konservativ UNVALIDATED, nie live
    assert reg.status_of("BREAKOUT-RETEST-01", "0.1.1") is ValidationStatus.UNVALIDATED
    assert not reg.live_allowed("BREAKOUT-RETEST-01", "0.1.1")


def test_refuted_setups_are_refuted_in_the_builtin_defaults() -> None:
    """Die Defaults greifen, wenn die Config fehlt — sie duerfen nicht milder sein.

    Beide SMC-Setups sind seit 2026-09-04 widerlegt (958 OOS-Trades, t = -3.71).
    Ein Default, der sie als UNVALIDATED fuehrt, laesst sie harmloser aussehen als sie sind.
    """
    reg = ValidationRegistry.default()
    for setup_id in ("SMC-SWEEP-REV-01", "SETUP-BREAKOUT-RETEST-01"):
        assert reg.status_of(setup_id, "0.1.1") is ValidationStatus.REFUTED
        assert not reg.live_allowed(setup_id, "0.1.1")


def test_tsmom_is_in_validation_in_the_builtin_defaults() -> None:
    reg = ValidationRegistry.default()
    assert reg.status_of("SETUP-TSMOM-ENSEMBLE-01", "0.1.1") is ValidationStatus.IN_VALIDATION
    assert not reg.live_allowed("SETUP-TSMOM-ENSEMBLE-01", "0.1.1")


def test_registry_from_file_roundtrip(tmp_path) -> None:
    p = tmp_path / "v.json"
    p.write_text(
        json.dumps(
            {
                "setups": [
                    {
                        "setup_id": "S2",
                        "strategy_version": "0.1.1",
                        "status": "validated",
                        "baseline": {
                            "expectancy_r": 0.3,
                            "profit_factor": 1.5,
                            "win_rate": 0.45,
                            "max_drawdown_r": 8.0,
                            "n_trades": 200,
                        },
                        "validated_window": ["2023-01-01", "2025-06-01"],
                        "forward_trades_required": 80,
                    }
                ]
            }
        )
    )
    reg = ValidationRegistry.from_file(p)
    sv = reg.get("S2", "0.1.1")
    assert sv.status is ValidationStatus.VALIDATED and sv.live_allowed
    assert sv.baseline is not None and sv.baseline.expectancy_r == 0.3
    assert sv.forward_trades_required == 80
    # builtin bleibt erhalten
    assert reg.status_of("SMC-SWEEP-REV-01", "0.1.1") is ValidationStatus.REFUTED


# --------------------------------------------------------------------------- edge health


def test_edge_health_intact_when_recent_matches_baseline() -> None:
    base = BaselineMetrics(
        expectancy_r=0.25, profit_factor=1.5, win_rate=0.45, max_drawdown_r=8.0, n_trades=200
    )
    rep = assess_edge_health(base, _trades([2.0, -1, -1, 2.0, -1, 2.0, -1, 2.0, -1, -1] * 3))
    assert rep.health in (EdgeHealth.INTACT, EdgeHealth.WEAKENING)
    assert rep.recent_n == 30


def test_edge_health_broken_when_recent_negative() -> None:
    base = BaselineMetrics(
        expectancy_r=0.25, profit_factor=1.5, win_rate=0.45, max_drawdown_r=8.0, n_trades=200
    )
    rep = assess_edge_health(base, _trades([-1] * 25 + [2.0, 2.0]))
    assert rep.health is EdgeHealth.BROKEN
    assert rep.blocks_live


def test_edge_health_insufficient_data() -> None:
    base = BaselineMetrics(
        expectancy_r=0.25, profit_factor=1.5, win_rate=0.45, max_drawdown_r=8.0, n_trades=200
    )
    rep = assess_edge_health(base, _trades([2.0, -1, 2.0]))
    assert rep.health is EdgeHealth.INSUFFICIENT_DATA


# --------------------------------------------------------------------------- live gate


def test_live_gate_unvalidated_is_shadow() -> None:
    reg = ValidationRegistry.default()
    g = evaluate_live_gate("EIN-UNBEKANNTES-SETUP", "0.1.1", registry=reg)
    assert g.eligibility is LiveEligibility.SHADOW and not g.is_live


def test_live_gate_refuted_is_blocked_not_shadow() -> None:
    """SHADOW heisst 'beobachten'. Ein widerlegtes Setup soll gar nicht erst mitlaufen."""
    reg = ValidationRegistry.default()
    g = evaluate_live_gate("SMC-SWEEP-REV-01", "0.1.1", registry=reg)
    assert g.eligibility is LiveEligibility.BLOCKED and not g.is_live
    assert any("refuted" in r for r in g.reasons)


def test_live_gate_validated_is_live() -> None:
    reg = ValidationRegistry.default().with_entry(
        SetupValidation("S2", "0.1.1", ValidationStatus.VALIDATED)
    )
    g = evaluate_live_gate("S2", "0.1.1", registry=reg)
    assert g.eligibility is LiveEligibility.LIVE and g.is_live


def test_live_gate_validated_but_broken_edge_is_blocked() -> None:
    reg = ValidationRegistry.default().with_entry(
        SetupValidation("S2", "0.1.1", ValidationStatus.VALIDATED)
    )
    base = BaselineMetrics(
        expectancy_r=0.25, profit_factor=1.5, win_rate=0.45, max_drawdown_r=8.0, n_trades=200
    )
    eh = assess_edge_health(base, _trades([-1] * 26))
    g = evaluate_live_gate("S2", "0.1.1", registry=reg, edge_health=eh)
    assert g.eligibility is LiveEligibility.BLOCKED


def test_live_gate_degraded_is_blocked() -> None:
    reg = ValidationRegistry.default().degrade("SMC-SWEEP-REV-01", "0.1.1", reason="test")
    g = evaluate_live_gate("SMC-SWEEP-REV-01", "0.1.1", registry=reg)
    assert g.eligibility is LiveEligibility.BLOCKED
    assert g.validation_status is ValidationStatus.EDGE_DEGRADED


# --------------------------------------------------------------------------- apply to result


def test_apply_live_gate_flags_signal_report_as_shadow() -> None:
    from trading_agent.core.enums import DecisionType, Direction
    from trading_agent.governance import apply_live_gate
    from trading_agent.strategy.signal_report import build_signal_report

    class _E:
        def __init__(self, **kw: object) -> None:
            self.__dict__.update(kw)

    d = _E(
        decision=DecisionType.BUY,
        instrument="XAUUSDT",
        information_cutoff=_T0,
        setup_state=_E(value="armed"),
        setup_id="SETUP-TSMOM-ENSEMBLE-01",  # in_validation -> SHADOW
        strategy_version="0.1.1",
        direction=Direction.LONG,
        chain_progress="armed",
        reason_codes=(),
        entry=4480.0,
        sl=4460.0,
        tp1=4520.0,
        tp2=4560.0,
        tp3_ref="Runner",
        rr_to_tp2=4.0,
        blended_rr=3.1,
        tier=_E(value="A+"),
        confidence=0.88,
    )
    result = _E(
        decision=d,
        mtf=_E(per_tf={}, htf_directional=_E(value="trend_up")),
        confluence=None,
        contradictions=None,
        live_gate=None,
    )
    gated = apply_live_gate(result, registry=ValidationRegistry.default())  # type: ignore[arg-type]
    rep = build_signal_report(gated)
    assert rep is not None
    assert rep.live_eligibility == "shadow" and not rep.is_live
    txt = rep.as_text()
    assert "SHADOW-SIGNAL" in txt
    assert "🔥 A+ BUY · XAUUSDT · LONG" in txt  # Tier-Zeile bleibt als Kontext erhalten
