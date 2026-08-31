"""Portfolio Intelligence (Masterplan §33–§43).

Konsolidierung mehrerer **read-only** Accounts (`PortfolioHub`), echte rollierende
Korrelation aus OHLCV (`CorrelationEngine`), Positions-Bewertung 0–100 + Verdikt
(`PositionIntelligence`), Exit-/Re-Entry-Logik, Portfolio-Health, Ranking & Rotation.

Kein Auto-Verkauf. Keine Order. Alles ist Analyse + Empfehlung — die Entscheidung bleibt
beim Menschen (Masterplan: „kein Blind-AI, kein Echtgeld ohne Freigabe").
"""

from trading_agent.portfolio_intel.correlation import CorrelationEngine, CorrelationMatrix
from trading_agent.portfolio_intel.exit_intel import ExitIntelligence, ExitPlan
from trading_agent.portfolio_intel.health import (
    PortfolioHealth,
    PortfolioHealthReport,
    PortfolioRanking,
    RotationEngine,
    RotationSuggestion,
)
from trading_agent.portfolio_intel.hub import PortfolioHub
from trading_agent.portfolio_intel.models import (
    AccountPortfolio,
    ConsolidatedPortfolio,
    Holding,
    PositionVerdict,
)
from trading_agent.portfolio_intel.position_intel import PositionIntelligence, PositionRating
from trading_agent.portfolio_intel.reentry import ReEntryEngine, ReEntryWatch
from trading_agent.portfolio_intel.report import (
    PortfolioIntelligenceEngine,
    PortfolioIntelligenceReport,
)

__all__ = [
    "AccountPortfolio",
    "ConsolidatedPortfolio",
    "CorrelationEngine",
    "CorrelationMatrix",
    "ExitIntelligence",
    "ExitPlan",
    "Holding",
    "PortfolioHealth",
    "PortfolioHealthReport",
    "PortfolioHub",
    "PortfolioIntelligenceEngine",
    "PortfolioIntelligenceReport",
    "PortfolioRanking",
    "PositionIntelligence",
    "PositionRating",
    "PositionVerdict",
    "ReEntryEngine",
    "ReEntryWatch",
    "RotationEngine",
    "RotationSuggestion",
]
