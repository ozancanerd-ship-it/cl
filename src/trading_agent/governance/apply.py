"""``apply_live_gate`` — hängt den ``LiveGateReport`` an ein ``EvaluationResult``.

Getrennt von ``strategy.evaluate`` gehalten: die Strategie kennt keine Governance, die
Governance kennt die Strategie-Ausgabe nur lesend. Aufrufer (Live-Daemon, Paper-Runner,
Forward-Test) rufen dies **nach** ``evaluate`` auf.
"""

from __future__ import annotations

import copy
import dataclasses
from collections.abc import Sequence

from trading_agent.governance.edge_health import BaselineMetrics, assess_edge_health
from trading_agent.governance.live_gate import LiveGateReport, evaluate_live_gate
from trading_agent.governance.validation import ValidationRegistry
from trading_agent.journal.ledger import TradeRecord
from trading_agent.strategy.evaluate import EvaluationResult


def apply_live_gate(
    result: EvaluationResult,
    *,
    registry: ValidationRegistry,
    recent_trades: Sequence[TradeRecord] = (),
    baseline: BaselineMetrics | None = None,
    forward_trades_seen: int | None = None,
) -> EvaluationResult:
    """Neues ``EvaluationResult`` mit gesetztem ``live_gate``.

    ``recent_trades`` + ``baseline`` (optional): löst die Edge-Health-Prüfung aus. ``baseline``
    fällt sonst auf die in der Registry hinterlegte Setup-Baseline zurück.
    """
    d = result.decision
    sv = registry.get(d.setup_id, d.strategy_version)
    base = baseline or sv.baseline
    edge = (
        assess_edge_health(base, list(recent_trades))
        if base is not None and recent_trades
        else None
    )
    seen = forward_trades_seen if forward_trades_seen is not None else len(recent_trades)
    report: LiveGateReport = evaluate_live_gate(
        d.setup_id,
        d.strategy_version,
        registry=registry,
        edge_health=edge,
        forward_trades_seen=seen,
    )
    if dataclasses.is_dataclass(result) and not isinstance(result, type):
        return dataclasses.replace(result, live_gate=report)
    clone = copy.copy(result)  # Research-/Test-Kontext: kein EvaluationResult-Dataclass
    object.__setattr__(clone, "live_gate", report)
    return clone


__all__ = ["apply_live_gate"]
