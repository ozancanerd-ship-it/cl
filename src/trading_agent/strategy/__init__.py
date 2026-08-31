"""Strategy Engine — brokerunabhängige Entscheidungslogik (``docs/strategy/``, ``0.1.1``).

Öffentliche Einstiegspunkte:

* ``evaluate(MarketContext, ...) -> EvaluationResult`` / ``decide(...) -> Decision`` —
  der Orchestrator (``strategy.evaluate``).
* ``Decision`` — die einzige Ausgabe der Engine (``BUY``/``SELL``/``WAIT``/``NO_TRADE``).
* ``SignalTracker`` — der lebende Signal-Lifecycle (``strategy.signal``).
* ``PositionManager`` — Exit / Paper-Position-Management (``strategy.position``).
* ``AlertEngine`` — selbst-aktualisierende Alerts (``strategy.alerts``).
* ``ContinuousEvaluator`` — Re-Evaluation je ``MarketContext`` (``strategy.engine``).
* ``PaperLiveRunner`` — der verdrahtete Paper-Live-Datenfluss (``strategy.paper_live``).

Die Re-Exports sind **lazy** (PEP 562): ``strategy.evaluate`` zieht ``analysis.mtf`` nach, und
``analysis.regime`` importiert wiederum ``strategy.primitives`` — ein eager Re-Export hier würde
diesen Zyklus beim Paket-Import auslösen. Submodule direkt zu importieren bleibt unberührt.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from trading_agent.strategy.alerts import AlertEngine, AlertParams, AlertType
    from trading_agent.strategy.decision import Decision
    from trading_agent.strategy.engine import ContinuousEvaluator, EngineParams, EngineTick
    from trading_agent.strategy.evaluate import (
        EvaluateParams,
        EvaluationResult,
        decide,
        evaluate,
        evaluate_from_mtf,
    )
    from trading_agent.strategy.m1_feed import InlineM1Source, M1Source, NullM1Source
    from trading_agent.strategy.paper_live import PaperLiveRunner, PaperLiveStep
    from trading_agent.strategy.position import (
        PaperPosition,
        PositionManager,
        PositionParams,
        PositionState,
    )
    from trading_agent.strategy.signal import (
        DynamicSignal,
        SignalChangeKind,
        SignalParams,
        SignalRevision,
        SignalState,
        SignalTracker,
        SignalUpdate,
    )

_LAZY = {
    "Decision": "trading_agent.strategy.decision",
    "EvaluateParams": "trading_agent.strategy.evaluate",
    "EvaluationResult": "trading_agent.strategy.evaluate",
    "decide": "trading_agent.strategy.evaluate",
    "evaluate": "trading_agent.strategy.evaluate",
    "evaluate_from_mtf": "trading_agent.strategy.evaluate",
    "DynamicSignal": "trading_agent.strategy.signal",
    "SignalChangeKind": "trading_agent.strategy.signal",
    "SignalParams": "trading_agent.strategy.signal",
    "SignalRevision": "trading_agent.strategy.signal",
    "SignalState": "trading_agent.strategy.signal",
    "SignalTracker": "trading_agent.strategy.signal",
    "SignalUpdate": "trading_agent.strategy.signal",
    "PaperPosition": "trading_agent.strategy.position",
    "PositionManager": "trading_agent.strategy.position",
    "PositionParams": "trading_agent.strategy.position",
    "PositionState": "trading_agent.strategy.position",
    "AlertEngine": "trading_agent.strategy.alerts",
    "AlertParams": "trading_agent.strategy.alerts",
    "AlertType": "trading_agent.strategy.alerts",
    "ContinuousEvaluator": "trading_agent.strategy.engine",
    "EngineParams": "trading_agent.strategy.engine",
    "EngineTick": "trading_agent.strategy.engine",
    "M1Source": "trading_agent.strategy.m1_feed",
    "InlineM1Source": "trading_agent.strategy.m1_feed",
    "NullM1Source": "trading_agent.strategy.m1_feed",
    "PaperLiveRunner": "trading_agent.strategy.paper_live",
    "PaperLiveStep": "trading_agent.strategy.paper_live",
}

__all__ = [
    "AlertEngine",
    "AlertParams",
    "AlertType",
    "ContinuousEvaluator",
    "Decision",
    "DynamicSignal",
    "EngineParams",
    "EngineTick",
    "EvaluateParams",
    "EvaluationResult",
    "InlineM1Source",
    "M1Source",
    "NullM1Source",
    "PaperLiveRunner",
    "PaperLiveStep",
    "PaperPosition",
    "PositionManager",
    "PositionParams",
    "PositionState",
    "SignalChangeKind",
    "SignalParams",
    "SignalRevision",
    "SignalState",
    "SignalTracker",
    "SignalUpdate",
    "decide",
    "evaluate",
    "evaluate_from_mtf",
]


def __getattr__(name: str) -> Any:
    module_path = _LAZY.get(name)
    if module_path is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib

    return getattr(importlib.import_module(module_path), name)
