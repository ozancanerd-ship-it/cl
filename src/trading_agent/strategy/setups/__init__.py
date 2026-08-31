"""Zusätzliche Setup-Typen neben ``SMC-SWEEP-REV-01`` (das in ``strategy/setup_detection.py`` bleibt).

Jeder Typ ist **eigenständig**: eigene Erkennung, eigene Geometrie, eigene ``NoTradeReason``-Gründe.
``strategy.evaluate`` ruft die Zusatz-Detektoren **parallel** zur SMC-Kette auf und nimmt den
besten Kandidaten. Die SMC-Kette wird dabei nicht angefasst.
"""

from trading_agent.strategy.setups.breakout_retest import (
    SETUP_BREAKOUT_RETEST,
    BreakoutRetestParams,
    BreakoutRetestReport,
    BreakoutState,
    detect_breakout_retest,
)

__all__ = [
    "SETUP_BREAKOUT_RETEST",
    "BreakoutRetestParams",
    "BreakoutRetestReport",
    "BreakoutState",
    "detect_breakout_retest",
]
